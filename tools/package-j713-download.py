#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 studioxradio aka kb2uka
"""Split an explicitly cleared installer delivery without changing its files."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import tarfile

ASSET_LIMIT = 2**31 - 1
PART_BYTES = 1_900_000_000
MANIFEST = 'download-manifest.tsv'
DOWNLOADER = 'download-j713.command'
DOWNLOAD_SUMS = 'DOWNLOAD-SHA256SUMS'
RELEASE_URL = 'https://github.com/Kb2uka/Omarchy-M4-Native/releases/download/'
RELEASE_DEFAULTS = "release_base=''\nrelease_pin=''\nrelease_tag=''\n"
SAFE_PATH = re.compile(r'[A-Za-z0-9_-][A-Za-z0-9._-]*(?:/[A-Za-z0-9_-][A-Za-z0-9._-]*)*\Z')
SHA = re.compile(r'[0-9a-f]{64}\Z')
TAG = re.compile(r'v[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?\Z')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def safe_path(name):
    require(isinstance(name, str) and len(name) <= 240 and SAFE_PATH.fullmatch(name),
            'Unsafe delivery path: ' + str(name))
    return name


def no_links(path):
    require(path.is_absolute(), 'Use absolute input and output paths')
    require(not any(p.is_symlink() for p in (path, *path.parents)), 'Symlink path refused')


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'Duplicate JSON key')
        result[key] = value
    return result


def document(path):
    require(path.stat().st_size <= 1024 * 1024, 'Oversized metadata')
    return json.loads(path.read_bytes(), object_pairs_hook=unique_pairs)


def identity(path):
    s = path.stat(follow_symlinks=False)
    require(stat.S_ISREG(s.st_mode) and s.st_nlink == 1, 'Regular single-link input required')
    require(stat.S_IMODE(s.st_mode) in (0o600, 0o644, 0o700, 0o755), 'Unexpected input permissions')
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, stat.S_IMODE(s.st_mode))


def inventory(delivery, kit_pin, sums_pin, tag):
    no_links(delivery)
    require(delivery.is_dir(), 'Missing delivery directory')
    require(SHA.fullmatch(kit_pin) and SHA.fullmatch(sums_pin), 'Invalid metadata pin')
    require(TAG.fullmatch(tag), 'Invalid release tag')
    files = {}
    for path in sorted(delivery.rglob('*'), key=lambda p: p.relative_to(delivery).as_posix()):
        name = safe_path(path.relative_to(delivery).as_posix())
        require(not path.is_symlink(), 'Symlink delivery member refused')
        if path.is_dir():
            continue
        before = identity(path)
        pin = digest(path)
        require(identity(path) == before, 'Input changed while hashing: ' + name)
        files[name] = {'bytes': before[2], 'sha256': pin, 'mode': before[5], 'identity': before}
    require(0 < len(files) <= 512, 'Unexpected delivery member count')
    required = {'KIT.json', 'SHA256SUMS', 'IMAGE.json', 'installer_data.json', 'Install.command'}
    require(required <= files.keys(), 'Incomplete installer delivery')
    require(files['KIT.json']['sha256'] == kit_pin and files['SHA256SUMS']['sha256'] == sums_pin,
            'Delivery metadata pin mismatch')
    kit, image = document(delivery / 'KIT.json'), document(delivery / 'IMAGE.json')
    # Transport does not grant distribution rights or override historical private evidence.
    require(kit.get('private_only') is False and image.get('private_only') is False,
            'Private or unclassified delivery cannot be packaged for download')
    require(kit.get('release_version') == tag[1:] and image.get('release_version') == tag[1:],
            'Release tag does not match the frozen delivery')
    require(isinstance(kit.get('files'), dict) and set(kit['files']) == set(files) - {'KIT.json', 'SHA256SUMS'},
            'KIT.json does not close the delivery inventory')
    for name, entry in kit['files'].items():
        require(isinstance(entry, dict) and entry.get('sha256') == files[name]['sha256'] and
                type(entry.get('bytes')) is int and entry['bytes'] == files[name]['bytes'],
                'KIT.json file mismatch: ' + name)
    expected = ''.join(files[name]['sha256'] + '  ' + name + '\n'
                       for name in sorted(set(files) - {'SHA256SUMS'})).encode('ascii')
    require((delivery / 'SHA256SUMS').read_bytes() == expected, 'Noncanonical or incomplete SHA256SUMS')
    definition = document(delivery / 'installer_data.json')
    entries = definition.get('os_list')
    require(isinstance(entries, list) and len(entries) == 1 and isinstance(entries[0], dict),
            'Exactly one installer OS package is supported')
    package = entries[0].get('package')
    require(isinstance(package, str) and '/' not in package and package.endswith('.zip'),
            'Unsafe installer package name')
    archive = safe_path('os/' + package)
    require(archive in files and files[archive]['bytes'] > 0, 'Missing OS ZIP')
    require({name for name in files if name.startswith('os/')} == {archive}, 'Unexpected OS assets')
    return files, archive


def package(delivery, kit_pin, sums_pin, tag, output, part_bytes=PART_BYTES):
    require(type(part_bytes) is int and 0 < part_bytes <= ASSET_LIMIT, 'Invalid part size')
    no_links(output)
    require(not output.exists(), 'Output must not exist')
    require(output.parent.is_dir() and not output.is_relative_to(delivery), 'Invalid output parent')
    files, archive = inventory(delivery, kit_pin, sums_pin, tag)
    count = (files[archive]['bytes'] + part_bytes - 1) // part_bytes
    require(1 <= count <= 99, 'Too many OS parts')
    companion_bytes = sum(entry['bytes'] for name, entry in files.items() if name != archive)
    require(sum(entry['bytes'] for entry in files.values()) <= 200_000_000_000,
            'Delivery exceeds transport size bound')
    require(companion_bytes + len(files) * 2048 + 10240 <= ASSET_LIMIT, 'Companion exceeds asset limit')
    required_space = sum(entry['bytes'] for entry in files.values()) + 1024 * 1024
    require(shutil.disk_usage(output.parent).free >= required_space, 'Insufficient transport output space')
    output.mkdir(mode=0o700)
    try:
        prefix = 'omarchy-j713-' + tag
        parts = []
        total_hash = hashlib.sha256()
        require(identity(delivery / archive) == files[archive]['identity'], 'OS ZIP input changed')
        with (delivery / archive).open('rb') as source:
            for index in range(1, count + 1):
                name = prefix + '-os.part' + str(index).zfill(3)
                remaining = min(part_bytes, files[archive]['bytes'] - (index - 1) * part_bytes)
                expected_size = remaining
                h = hashlib.sha256()
                with (output / name).open('xb') as target:
                    while remaining:
                        data = source.read(min(1024 * 1024, remaining))
                        require(data, 'OS ZIP became short')
                        target.write(data); h.update(data); total_hash.update(data)
                        remaining -= len(data)
                parts.append((index, name, expected_size, h.hexdigest()))
            require(not source.read(1) and total_hash.hexdigest() == files[archive]['sha256'],
                    'OS ZIP changed while splitting')
        require(identity(delivery / archive) == files[archive]['identity'], 'OS ZIP input changed')
        companion = prefix + '-companion.tar'
        with tarfile.open(output / companion, 'x', format=tarfile.USTAR_FORMAT) as tar:
            for name, entry in files.items():
                if name == archive:
                    continue
                path = delivery / name
                require(identity(path) == entry['identity'], 'Companion input changed: ' + name)
                info = tarfile.TarInfo(name)
                info.size, info.mode, info.mtime = entry['bytes'], entry['mode'], 0
                with path.open('rb') as stream:
                    tar.addfile(info, stream)
                require(identity(path) == entry['identity'] and digest(path) == entry['sha256'],
                        'Companion input changed while copying: ' + name)
        companion_size = (output / companion).stat().st_size
        require(companion_size <= ASSET_LIMIT, 'Companion exceeds asset limit')
        companion_pin = digest(output / companion)
        rows = [('format', 'j713-download-v1'), ('release', tag),
                ('archive', archive, files[archive]['bytes'], files[archive]['sha256']),
                ('companion', companion, companion_size, companion_pin)]
        rows += [('part', *part) for part in parts]
        rows += [('file', name, format(entry['mode'], '03o'), entry['bytes'], entry['sha256'])
                 for name, entry in files.items()]
        manifest = ''.join('\t'.join(map(str, row)) + '\n' for row in rows).encode('ascii')
        require(len(manifest) <= 128 * 1024, 'Oversized transport manifest')
        (output / MANIFEST).write_bytes(manifest)
        manifest_pin = hashlib.sha256(manifest).hexdigest()
        template = Path(__file__).with_name(DOWNLOADER).read_text(encoding='ascii')
        require(template.count(RELEASE_DEFAULTS) == 1, 'Unexpected downloader template')
        release_url = RELEASE_URL + tag
        defaults = (f"release_base='{release_url}'\n"
                    f"release_pin='{manifest_pin}'\n"
                    f"release_tag='{tag}'\n")
        downloader = template.replace(RELEASE_DEFAULTS, defaults).encode('ascii')
        (output / DOWNLOADER).write_bytes(downloader)
        # These transport assets remain outside the frozen kit and its manifest.
        pins = {name: pin for _, name, _, pin in parts}
        pins.update({companion: companion_pin, MANIFEST: manifest_pin,
                     DOWNLOADER: hashlib.sha256(downloader).hexdigest()})
        sums = ''.join(pins[name] + '  ' + name + '\n' for name in sorted(pins)).encode('ascii')
        (output / DOWNLOAD_SUMS).write_bytes(sums)
        pins[DOWNLOAD_SUMS] = hashlib.sha256(sums).hexdigest()
        for path in output.iterdir():
            path.chmod(0o755 if path.name == DOWNLOADER else 0o644)
        artifacts = {name: {'bytes': (output / name).stat().st_size, 'sha256': pin,
                            'mode': stat.S_IMODE((output / name).stat().st_mode)}
                     for name, pin in sorted(pins.items())}
        return {'manifest': MANIFEST, 'manifest_sha256': manifest_pin,
                'downloader': DOWNLOADER, 'downloader_sha256': pins[DOWNLOADER],
                'download_checksums': DOWNLOAD_SUMS, 'download_checksums_sha256': pins[DOWNLOAD_SUMS],
                'release_url': release_url, 'artifacts': artifacts,
                'release_tag': tag, 'part_count': len(parts), 'kit_sha256': kit_pin,
                'checksums_sha256': sums_pin, 'published': False}
    except BaseException:
        shutil.rmtree(output)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('delivery', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    for name in ('kit-sha256', 'checksums-sha256', 'release-tag'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--part-bytes', type=int, default=PART_BYTES)
    args = parser.parse_args()
    print(json.dumps(package(args.delivery, args.kit_sha256, args.checksums_sha256,
                             args.release_tag, args.output, args.part_bytes), indent=2))


if __name__ == '__main__':
    main()
