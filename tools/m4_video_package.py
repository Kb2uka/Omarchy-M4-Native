#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Deterministic packaging and offline image configuration for the qualified J713 stack."""
import argparse
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import tarfile
from m4_audio_package import digest, dtb_compatible, image_path, require, verified

HERE = Path(__file__).resolve().parent
MANIFEST = 'etc/omarchy/j713-video-manifest.json'
STAGED = '.m4-video-new'


def read_json(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'Duplicate JSON key: ' + key)
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=unique)


def spec():
    return read_json((HERE / 'qualified.json').read_bytes())


def link_path(root, name):
    return image_path(root, str(PurePosixPath(name).parent)) / PurePosixPath(name).name


def public_parents(root, name, create=False):
    image_path(root, str(PurePosixPath(name).parent))
    current = root
    for part in PurePosixPath(name).parts[:-1]:
        current /= part
        if current.exists():
            info = current.stat()
            require(current.is_dir() and info.st_uid == os.geteuid() and
                    stat.S_IMODE(info.st_mode) & 0o555 == 0o555 and
                    not stat.S_IMODE(info.st_mode) & 0o022, 'Unsafe public parent: ' + str(current))
        elif create:
            current.mkdir(mode=0o755)
            current.chmod(0o755)


def checked_inputs(inputs, definition):
    payload = {}
    for name, entry in definition['files'].items():
        payload[name] = verified(inputs, name, entry)
    for name, target in definition['links'].items():
        path = link_path(inputs, name)
        require(path.is_symlink() and os.readlink(path) == target, 'Input symlink mismatch: ' + name)
    return payload


def assemble(inputs, output, archive, local_only=False):
    definition = spec()
    require(local_only, 'Source/provenance closure pending; explicit --local-only required')
    require(not output.exists() and not output.is_symlink(), 'Package output must be new')
    require(not archive.exists() and not archive.is_symlink(), 'Archive output must be new')
    payload = checked_inputs(inputs, definition)
    # The closed input map never includes firmware, browser profiles or CDM files.
    manifest = {**definition, 'redistributable': False}
    raw = (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode()
    output.mkdir(parents=True)
    for name, data in sorted(payload.items()):
        path = output / 'root' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(definition['files'][name]['mode'])
    for name, target in sorted(definition['links'].items()):
        path = output / 'root' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(target)
    (output / 'manifest.json').write_bytes(raw)
    (output / 'manifest.json').chmod(0o644)
    # Explicit entries, fixed metadata and no gzip timestamp: repeated inputs yield identical bytes.
    with tarfile.open(archive, 'w', format=tarfile.USTAR_FORMAT) as tar:
        for name in sorted(['manifest.json', *['root/' + p for p in payload],
                            *['root/' + p for p in definition['links']]]):
            item = tarfile.TarInfo(name)
            item.uid = item.gid = item.mtime = 0
            item.uname = item.gname = ''
            if name == 'manifest.json':
                data, item.mode = raw, 0o644
            elif name[5:] in definition['links']:
                item.type = tarfile.SYMTYPE
                item.linkname = definition['links'][name[5:]]
                item.mode = 0o777
                tar.addfile(item)
                continue
            else:
                data = payload[name[5:]]
                item.mode = definition['files'][name[5:]]['mode']
            item.size = len(data)
            tar.addfile(item, io.BytesIO(data))
    return {'manifest_sha256': digest(raw), 'archive_sha256': digest(archive.read_bytes()),
            'files': len(payload), 'links': len(definition['links']), 'redistributable': False}


def load_package(package, pin):
    raw = image_path(package, 'manifest.json').read_bytes()
    require(digest(raw) == pin, 'Package manifest pin mismatch')
    manifest = read_json(raw)
    require(manifest == {**spec(), 'redistributable': False}, 'Unexpected package contract')
    payload = checked_inputs(package / 'root', manifest)
    expected = {'manifest.json', *['root/' + p for p in manifest['files']],
                *['root/' + p for p in manifest['links']]}
    actual = {str(p.relative_to(package)) for p in package.rglob('*') if not p.is_dir() or p.is_symlink()}
    require(actual == expected, 'Unexpected package members')
    return raw, manifest, payload


def configure(root, package, pin, boot_dtb, boot_kernel, firmware, apply=False):
    require(root.is_absolute() and not any(p.is_symlink() for p in (root, *root.parents)), 'Unsafe image root')
    root = root.resolve(strict=True)
    require(root != Path('/'), 'Offline image required; refusing live root')
    require(root.is_dir(), 'Image root is not a directory')
    raw, definition, planned = load_package(package, pin)
    for marker in ('etc/m4-root-id', 'var/lib/omarchy/provisioning/pending'):
        require(image_path(root, marker).is_file(), 'Not a pending owner image: ' + marker)
    dtb = boot_dtb.read_bytes()
    require({b'apple,j713', b'apple,t8132'} <= set(dtb_compatible(dtb)), 'Unsupported boot DTB board')
    require(digest(dtb) == definition['boot_dtb_sha256'], 'Unqualified boot DTB')
    require(digest(boot_kernel.read_bytes()) == definition['boot_kernel_sha256'], 'Exact portable kernel required')
    require(image_path(root, 'usr/lib/modules/' + definition['kernel_release']).is_dir(), 'Missing exact image kernel')
    # This package does not synthesize or replace GPU/root-verification setup.
    for name in definition['required_image_files']:
        require(image_path(root, name).is_file(), 'Missing image prerequisite: ' + name)
    external = firmware.read_bytes()
    require(digest(external) == definition['firmware_sha256'], 'External firmware pin mismatch')
    for name in definition['external_firmware_destinations']:
        planned[name] = external
    planned[MANIFEST] = raw
    modes = {name: entry['mode'] for name, entry in definition['files'].items()}
    modes.update({name: 0o644 for name in definition['external_firmware_destinations']})
    modes[MANIFEST] = 0o644
    links = {**definition['links'], **definition['enable_links']}
    for name in definition['conflicting_paths']:
        path = link_path(root, name)
        require(not path.exists() and not path.is_symlink(), 'Conflicting startup: ' + name)
    dropins = image_path(root, 'etc/systemd/system/m4-video.service.d')
    require(not dropins.exists() or dropins.is_dir() and not list(dropins.iterdir()), 'Unreviewed service overrides')
    changes = []
    for name, content in planned.items():
        path = image_path(root, name)
        public_parents(root, name)
        require(not path.with_name(path.name + STAGED).exists() and
                not path.with_name(path.name + STAGED).is_symlink(), 'Stale temporary file: ' + name)
        require(not path.exists() or path.is_file(), 'Non-file destination: ' + name)
        if path.exists():
            info = path.stat()
            require(info.st_uid == os.geteuid(), 'Foreign file owner: ' + name)
            current = path.read_bytes()
            require(current == content or digest(current) in definition['allowed_previous'].get(name, []),
                    'Conflicting existing video file: ' + name)
        if not path.exists() or path.read_bytes() != content or stat.S_IMODE(path.stat().st_mode) != modes[name]:
            changes.append(name)
    for name, target in links.items():
        path = link_path(root, name)
        public_parents(root, name)
        require(not path.exists() and not path.is_symlink() or
                path.is_symlink() and os.readlink(path) == target and path.lstat().st_uid == os.geteuid(),
                'Conflicting symlink: ' + name)
        if not path.is_symlink():
            changes.append(name)
    require(apply or not changes, 'Video image differs: ' + ', '.join(changes))
    if apply:
        # Offline image: all inputs/destinations checked first, enable link published last.
        order = sorted(changes, key=lambda n: (n in definition['enable_links'], n == MANIFEST,
                                              n == 'usr/local/bin/brave-m4-hardware', n))
        for name in order:
            public_parents(root, name, create=True)
            if name in links:
                link_path(root, name).symlink_to(links[name])
                continue
            path = image_path(root, name)
            temporary = path.with_name(path.name + STAGED)
            with temporary.open('xb') as stream:
                stream.write(planned[name])
                stream.flush()
                os.fsync(stream.fileno())
            temporary.chmod(modes[name])
            os.replace(temporary, path)
    return {'manifest_sha256': digest(raw), 'changed_files': changes,
            'scope': 'Offline pending image only; no boot image write or runtime action',
            'browser_default_changed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    pack = commands.add_parser('assemble')
    pack.add_argument('--inputs', type=Path, required=True)
    pack.add_argument('--output', type=Path, required=True)
    pack.add_argument('--archive', type=Path, required=True)
    pack.add_argument('--local-only', action='store_true')
    for action in ('apply', 'check'):
        cmd = commands.add_parser(action)
        for option in ('root', 'package', 'boot-dtb', 'boot-kernel', 'firmware'):
            cmd.add_argument('--' + option, type=Path, required=True)
        cmd.add_argument('--manifest-sha256', required=True)
    args = parser.parse_args()
    if args.command == 'assemble':
        result = assemble(args.inputs, args.output, args.archive, args.local_only)
    else:
        require(os.geteuid() == 0, 'Root required for mounted image apply/check')
        mount = subprocess.run(['findmnt', '--json', '--target', str(args.root), '--output', 'TARGET,FSTYPE'],
                               check=True, text=True, capture_output=True)
        entries = read_json(mount.stdout)['filesystems']
        require(len(entries) == 1 and entries[0]['fstype'] == 'ext4' and
                Path(entries[0]['target']).resolve() == args.root.resolve(), 'Explicit mounted ext4 image required')
        result = configure(args.root, args.package, args.manifest_sha256, args.boot_dtb,
                           args.boot_kernel, args.firmware, args.command == 'apply')
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == '__main__':
    main()
