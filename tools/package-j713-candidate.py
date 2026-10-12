#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 studioxradio aka kb2uka
"""Package an explicitly qualified, checked offline image as a private build-2 delivery."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tarfile
import uuid
import zipfile

from m4_audio_package import digest, image_path, require
from m4_video_package import read_json

BASE_SUMS = 'b763190189a280b01cd5ecc67b52662fbe688b66034654e464abe8ffa4cccd1a'
VERSION = '0.1.0-alpha.1'


def raw_json(value):
    return (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()


def file_sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def pinned_json(path, pin):
    raw = path.read_bytes()
    require(not path.is_symlink() and digest(raw) == pin, 'Receipt pin mismatch')
    return read_json(raw)


def native_receipt(accepted, kit_pin, revision):
    require(set(accepted) == {'schema', 'kind', 'kit_sha256', 'source_revision',
                             'native_qualified', 'passed', 'browsers'} and
            accepted['schema'] == 1 and accepted['kind'] == 'j713-private-native-qualification' and
            accepted['kit_sha256'] == kit_pin and accepted['source_revision'] == revision and
            accepted['native_qualified'] is True and
            accepted['passed'] == {'kernel-reboot-trackpad': True, 'audio-clean-default': True},
            'Native qualification does not bind this kit')
    browsers = accepted['browsers']
    require(isinstance(browsers, dict) and set(browsers) == {'brave', 'chromium'},
            'Both native browser outcomes are required')
    for result in browsers.values():
        require(isinstance(result, dict) and
                set(result) == {'passed', 'decoder', 'hardware_accelerated', 'evidence_sha256'} and
                result['passed'] is True and type(result['hardware_accelerated']) is bool and
                isinstance(result['decoder'], str) and 0 < len(result['decoder']) <= 128 and
                result['decoder'].strip() == result['decoder'] and
                isinstance(result['evidence_sha256'], str) and
                re.fullmatch('[0-9a-f]{64}', result['evidence_sha256']) is not None,
                'Native browser qualification is incomplete')


def finalize(baseline, kit, kit_pin, image, approval, approval_pin, checks, checks_pin, output, source_tree):
    require(not output.exists() and not output.is_symlink(), 'Delivery output must be new')
    require(output.is_absolute() and not any(p.is_symlink() for p in (output.parent, *output.parent.parents)), 'Symlink or relative output ancestor')
    require(not any(output.is_relative_to(p.resolve()) for p in (baseline, kit)), 'Output cannot be inside an input tree')
    require(image.is_file() and not image.is_symlink(), 'Regular unmounted root image required')
    mount = subprocess.run(['findmnt', '--noheadings', '--source', str(image.resolve())],
                           capture_output=True, text=True)
    require(mount.returncode == 1, 'Image mount state could not be excluded')
    loops = subprocess.check_output(['losetup', '--json', '--list', '--associated', str(image.resolve())], text=True)
    require(not read_json(loops).get('loopdevices'), 'Image is still attached to a loop device')
    definition = pinned_json(kit / 'manifest.json', kit_pin)
    require(definition['release_version'] == VERSION and definition['private_build'] == 2 and
            definition['private_only'] and not definition['fresh_install_qualified'], 'Unexpected candidate state')
    # Execute only the reviewed kit verifier after its source bytes are bound by the source revision.
    verifier_path = image_path(kit, 'prepare-image.py')
    committed = subprocess.check_output(['git', '-C', str(source_tree), 'show', definition['source_revision'] + ':tools/configure-j713-installer.py'])
    require(verifier_path.read_bytes() == committed, 'Kit verifier differs from source revision')
    spec = importlib.util.spec_from_file_location('candidate_verifier', verifier_path)
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    verifier.verify_kit(kit_pin)
    accepted = pinned_json(approval, approval_pin)
    native_receipt(accepted, kit_pin, definition['source_revision'])
    checked = pinned_json(checks, checks_pin)
    required = {'offline-apply-check', 'owner-free', 'locale-utf8', 'sudo', 'base-packages', 'e2fsck'}
    require(set(checked) == {'schema', 'kit_sha256', 'root_sha256', 'root_uuid', 'passed'} and
            checked['schema'] == 1 and checked['kit_sha256'] == kit_pin and
            isinstance(checked['passed'], dict) and set(checked['passed']) == required and
            all(value is True for value in checked['passed'].values()),
            'Incomplete image validation receipt')
    root_sha = file_sha(image)
    require(root_sha == checked['root_sha256'], 'Image changed after validation')
    with image.open('rb') as stream:
        stream.seek(1024); sb = stream.read(1024)
    require(len(sb) == 1024 and sb[56:58] == b'\x53\xef', 'Invalid ext4 image')
    root_uuid = str(uuid.UUID(bytes=sb[104:120]))
    state, mounted, last_checked = struct.unpack_from('<H', sb, 58)[0], struct.unpack_from('<I', sb, 44)[0], struct.unpack_from('<I', sb, 64)[0]
    require(root_uuid == checked['root_uuid'] and state & 1 and not state & 2 and last_checked >= mounted,
            'Image identity or clean-check state differs')
    sums = image_path(baseline, 'SHA256SUMS').read_bytes()
    require(digest(sums) == BASE_SUMS, 'Unqualified baseline delivery')
    for line in sums.decode('ascii').splitlines():
        pin, name = line.split('  ', 1)
        require(file_sha(image_path(baseline, name)) == pin, 'Baseline delivery changed: ' + name)
    old = read_json(image_path(baseline, 'IMAGE.json').read_bytes())
    require(root_uuid != old['root']['filesystem_uuid'], 'A new candidate requires a fresh root identity')
    old_config = read_json(image_path(baseline, 'installer_data.json').read_bytes())
    old_kit = read_json(image_path(baseline, 'KIT.json').read_bytes())
    expected = {*old_kit['files'], 'KIT.json', 'SHA256SUMS'}
    actual = {p.relative_to(baseline).as_posix() for p in baseline.rglob('*') if not p.is_dir() or p.is_symlink()}
    require(actual == expected and all(not p.is_symlink() for p in baseline.rglob('*')), 'Unexpected baseline delivery members')
    boot_names = {'loader': ('loader.bin', 'loader.bin'), 'kernel': ('Image.gz', 'Image.gz'),
                  'dtb': ('video-base.dtb', 'portable.dtb'), 'initramfs': ('initramfs.cpio.gz', 'initramfs.cpio.gz')}
    boot = {name: {'file': 'boot/' + dest, 'sha256': file_sha(kit / 'inputs' / source)}
            for name, (source, dest) in boot_names.items()}
    boot['bootargs'] = old['boot']['bootargs']
    archive_name = 'omarchy-j713-private-' + VERSION + '-build2.zip'
    skipped = {'KIT.json', 'SHA256SUMS', 'IMAGE.json', 'installer_data.json', 'source/integration-inputs.tar.gz',
               'os/' + old_config['os_list'][0]['package']}
    output.mkdir(parents=True)
    for name in sorted(expected - skipped):
        source = image_path(baseline, name)
        target = image_path(output, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    (output / 'os').mkdir(exist_ok=True)
    archive = output / 'os' / archive_name
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as z:
        z.write(image, 'root.ext4')
        for source, dest in boot_names.values():
            z.write(kit / 'inputs' / source, 'boot/' + dest)
    with zipfile.ZipFile(archive) as z:
        require(z.testzip() is None, 'ZIP CRC failure')
        with z.open('root.ext4') as stream:
            require(hashlib.file_digest(stream, 'sha256').hexdigest() == root_sha, 'Archived root differs')
        for part in boot_names:
            require(digest(z.read(boot[part]['file'])) == boot[part]['sha256'], 'Archived boot part differs')
    config = old_config
    require(len(config['os_list']) == 1, 'Unexpected installer OS list')
    os_entry = config['os_list'][0]
    os_entry['package'] = archive_name
    os_entry['m4_boot'] = boot
    roots = [p for p in os_entry['partitions'] if 'rootfs_uuid' in p]
    require(len(roots) == 1, 'Unexpected installer root layout')
    roots[0].update(rootfs_uuid=root_uuid, size=str(image.stat().st_size) + 'B')
    (output / 'installer_data.json').write_bytes(raw_json(config))
    metadata = {**old, 'release_version': VERSION, 'private_build': 2,
                'source_revision': definition['source_revision'], 'assembly_kit_sha256': kit_pin,
                'audio_manifest_sha256': definition['audio_package_sha256'],
                'video_manifest_sha256': definition['video_package_sha256'],
                'root': {'sha256': root_sha, 'bytes': image.stat().st_size, 'filesystem_uuid': root_uuid},
                'boot': boot, 'native_qualification_receipt_sha256': approval_pin,
                'native_browser_results': accepted['browsers'],
                'image_validation_receipt_sha256': checks_pin, 'fresh_install_qualified': False}
    (output / 'IMAGE.json').write_bytes(raw_json(metadata))
    with tarfile.open(output / 'source/integration-inputs.tar.gz', 'w:gz') as t:
        t.add(kit, arcname='kit')
    additions = ('\n## Private build 2\n\nVersion ' + VERSION + ', build 2. Source revision `' +
                 definition['source_revision'] + '`. The prior installed image is preserved; this candidate\n'
                 'requires its own owner fresh-install verification before publication. Widevine binaries\n'
                 'are excluded; consented user-side acquisition waits for a closed browser and network.\n')
    with (output / 'START-HERE.md').open('a') as stream:
        stream.write(additions)
    files = {}
    for path in sorted(output.rglob('*')):
        if path.is_file():
            name = path.relative_to(output).as_posix()
            require(not path.is_symlink() and ':' not in name, 'Nonportable delivery path')
            files[name] = {'sha256': file_sha(path), 'bytes': path.stat().st_size}
    kit_metadata = {**old_kit, 'files': files, 'source_revision': definition['source_revision'],
                    'native_browser_results': accepted['browsers'],
                    'release_version': VERSION, 'private_build': 2, 'fresh_install_qualified': False}
    raw = raw_json(kit_metadata)
    (output / 'KIT.json').write_bytes(raw)
    pins = {name: entry['sha256'] for name, entry in files.items()}
    pins['KIT.json'] = digest(raw)
    (output / 'SHA256SUMS').write_text(''.join(pin + '  ' + name + '\n' for name, pin in sorted(pins.items())))
    return {'kit_sha256': digest(raw), 'checksums_sha256': file_sha(output / 'SHA256SUMS'),
            'source_revision': definition['source_revision'], 'private_build': 2,
            'private_only': True, 'fresh_install_qualified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('baseline-delivery', 'kit', 'root-image', 'native-qualification', 'image-checks', 'output', 'source-tree'):
        parser.add_argument('--' + name, type=Path, required=True)
    for name in ('kit', 'native-qualification', 'image-checks'):
        parser.add_argument('--' + name + '-sha256', required=True)
    a = parser.parse_args()
    print(json.dumps(finalize(a.baseline_delivery, a.kit, a.kit_sha256, a.root_image,
                             a.native_qualification, a.native_qualification_sha256,
                             a.image_checks, a.image_checks_sha256, a.output, a.source_tree), indent=2))


if __name__ == '__main__':
    main()
