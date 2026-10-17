#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Assemble the alpha release from the retained build-2 files on a Linux build host."""
import argparse
import copy
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
import tarfile
import uuid
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from m4_audio_package import image_path

VERSION = '0.1.0-alpha.1'
BASE_SUMS = '95c8125fdd054ebcabbfc490e34ced0ee1b2e5293a3980aba383c54623fe121d'
BASE_ROOT = 'b2beef5d2d1c1370e6d6911a3ff6f1d4ddc1e7c992a444f90283491dd8440836'
BASE_AUDIO = 'ffa147b80173ede85be61b8b1a5431edcd8d605a740962da2dfa65ba189dcc6a'
BASE_GPU_UNIT = 'f4e62aa8a1a5782e1c27928e06f72e64ecfb630fee621a585db80837fb1c0671'
BASE_AUDIO_REGISTER = 'aa3f9f8b4c6c08ce3e088442168d5e80d9a59aa1b0e6d1449f6943829ae1a65d'
BASE_AUDIO_UNIT = 'bfa2e740cc4ff8aa03246f97bcbf85f92528b11fea22100ad8a3c312c7cb0ddf'
BASE_VIDEO = '8e1607e29174987e90f0f1cd0361786f894b239ad54fba08067ce1da54ec2331'
BASE_VIDEO_START = 'c613d8a3b48021204538fc0dd766a523b2ddb4c0ed4d2a26bb21259ba285c85d'
BASE_KERNEL = '2ddab32174f5a0d1153b177bb9fabc645e8d9f72d09d2fa523e78758f05290b9'
KERNEL_IDENTITY = {
    'kernel': {'sha256': 'c3704cfd2afb7760e73f0b9939a68864a30bd916fcd478bd960e538373123dc9',
               'bytes': 18901266},
    'build': {
        'kernel_release': '7.1.9-m4-gpu-backport-20261008',
        'config_sha256': '14b4437b2b195e912a2ab614d28761acd3dc79f886d4487964aa0912616a3a6e',
        'dockchannel_hid_sha256': 'e4eebbee612eee32bd51ce370d412afc8cf62a9a7e087a3797c2c29306cedfb4',
        'macsmc_sha256': '0cbe1eaf11e473336bfc24d5036a4a15586f21c868e8fedd21aeb1ce8bcaa9d6',
        'module_symvers_sha256': '5e3c2624c6a991bae503336137ce15071e51ef043edcd20ba1ad19df4e23d67c',
        'vmlinux_symvers_sha256': '5e3c2624c6a991bae503336137ce15071e51ef043edcd20ba1ad19df4e23d67c',
        'export_rows': 16412, 'exports_unchanged': True,
    },
}
KERNEL_PATCHES = {
    'patches-kernel/j713-input/dockchannel-start-retry.patch':
        'f3b1cacd0f1704e60edbadba714ac052959356446e5e059ad345fddc1d501967',
    'patches-kernel/j713-input/macsmc-key-info-lock.patch':
        '47a09e2b804b156c8ee5e26a025980caca7a10c4f0c0ef0bcdd8baf8b3b912f4',
    'patches-kernel/j713-input/dockchannel-afe-action.patch':
        '8f9ab49462a5076b7345aa465cef99696f29cbf4d23ab5bfa629acfebee8ed89',
}
NATIVE_KERNEL_CHECKS = {'boot', 'touch_mt_ready', 'keyboard_registered', 'trackpad_registered',
                        'gpu_service_integrity', 'video_service_integrity',
                        'audio_service_integrity', 'repeat_boot'}
POWEROFF_DTB = 'e54c77a760a5b696486f9d80c7c83ca4295d641113bb9e54d13e259863bf9b07'
# m1n1 stage 1, the installer's boot/m1n1.bin: alpha.1's, and the #82 fix that leaves an M4's
# ANS stopped without a PMGR reset (docs/j713-m1n1-issue82-ans-reset.md). Both are 0x120000 bytes.
STAGE1_MEMBER = 'boot/m1n1.bin'
STAGE1_ALPHA1 = '18cc4cd1ba07928078d2cb52a2c1c5f40a4661fd3db7ee6af0e6923fd237be10'
STAGE1_ANS1 = '8c5a15736c19d0a3428e64f01292791fb54dd68e09f2f5304a7210d19c42adce'
STAGE1_BYTES = 0x120000
STAGE1_PATCH = 'patches-m1n1/stage1-ans-no-reset.patch'
# The Air GPU module: alpha.1's, and the one omarchy-m4-gpu 1-3 ships (#57's per-chip power limit
# and #118's eight-core GPU), built from gpu/j713. The image carries the fixed module, its pin in
# runtime.sha256, and alpha.1's files where m4-gpu-module keeps them, so a new install is in the
# state an Air reaches through the channel and --rollback still restores alpha.1.
GPU_MODULE = 'opt/m4-gpu/asahi_m4.ko'
GPU_SUMS = 'opt/m4-gpu/runtime.sha256'
GPU_BACKUP = 'var/lib/omarchy-m4/gpu-module'
GPU_ALPHA1 = '80cf3669575edd09aed226a93e47aaaf132e70f1070154629c413c0b40d34578'
GPU_FIXED = 'de0337a773930c7862a99706112dd71eaaecf2021707da248fd3d07feefdc824'
GPU_FIXED_BYTES = 390816
GPU_SOURCE = 'gpu/j713'
FIRS = ('j713-static-minimal-lane0-48000.wav', 'j713-static-minimal-lane1-48000.wav',
        'j713-static-minimal-lane2-48000.wav', 'j713-static-minimal-lane3-48000.wav')
PROFILES = ('Brave-Browser', 'Brave-Browser-M4', 'Chromium')
# Every binary the root carries outside pacman must name the source that built it (issue 77).
SOURCE_JOINS = 'packaging/release-source-joins.json'
CORRESPONDENCE = ('rebuilt-identical', 'unverified')
KERNEL_MODULES = ('.ko', '.ko.gz', '.ko.xz', '.ko.zst')
VIRTUAL_TREES = ('dev', 'proc', 'run', 'sys', 'tmp')
# source/ files the assembler writes itself rather than copying in.
GENERATED_SOURCES = ('source/integration-inputs.tar.gz', 'source/installer-source.tar.gz',
                     'source/release-integration.tar.gz', 'source/README.md', 'source/j713-poweroff.md',
                     'source/j713-kernel-receipt.json')


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def source_joins(tree):
    """Load the binary-to-source joins and reject malformed entries."""
    joins = json.loads((tree / SOURCE_JOINS).read_bytes())
    require(joins.get('schema') == 1 and isinstance(joins.get('binaries'), dict), 'Unknown source joins')
    for name, entry in joins['binaries'].items():
        require(re.fullmatch(r'[0-9a-f]{64}', entry.get('sha256', '')) and
                entry.get('correspondence') in CORRESPONDENCE and entry.get('sources'),
                'Incomplete source join: ' + name)
        for source in entry['sources']:
            require(set(source) in ({'tree'}, {'tree', 'commit'}, {'archive'}, {'archive', 'sha256'}),
                    'Unknown source reference for ' + name)
            require('commit' not in source or re.fullmatch(r'[0-9a-f]{40}', source['commit']),
                    'A joined tree commit is a full commit ID: ' + name)
            require('tree' in source or (source['archive'].startswith('source/') and
                                         '/' not in source['archive'][7:]),
                    'Source archives live directly in source/: ' + name)
    return joins


def packaged_files(root):
    """Paths the image's pacman database says a package owns."""
    owned = set()
    for files in sorted((root / 'var/lib/pacman/local').glob('*/files')):
        listing = False
        for line in files.read_text(errors='replace').splitlines():
            if line.startswith('%'):
                listing = line == '%FILES%'
            elif listing and line and not line.endswith('/'):
                owned.add(line)
    return owned


def unpackaged_binaries(root):
    """Every ELF object or kernel module in the image that no package owns, by SHA-256."""
    owned = packaged_files(root)
    found = {}
    for directory, children, files in os.walk(root):
        if Path(directory) == root:
            children[:] = [child for child in children if child not in VIRTUAL_TREES]
        for name in files:
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if relative in owned or path.is_symlink() or not path.is_file():
                continue
            with path.open('rb') as stream:
                elf = stream.read(4) == b'\x7fELF'
            if elf or name.endswith(KERNEL_MODULES):
                found[relative] = sha(path)
    return found


def check_binary_joins(found, joins, allow_unverified=False):
    """Each unpackaged binary must match its join, and every join must still ship."""
    listed = joins['binaries']
    require(not set(found) - set(listed),
            'Binaries without a source join: ' + ', '.join(sorted(set(found) - set(listed))))
    require(not set(listed) - set(found),
            'Source joins for binaries the image no longer carries: ' + ', '.join(sorted(set(listed) - set(found))))
    for name, digest in sorted(found.items()):
        require(listed[name]['sha256'] == digest, 'Source join names a different build of ' + name)
    unverified = sorted(name for name, entry in listed.items() if entry['correspondence'] != 'rebuilt-identical')
    require(allow_unverified or not unverified,
            'Source correspondence not verified by an identical rebuild: ' + ', '.join(unverified))
    return unverified


def git_tree_id(tree, revision, path):
    """The git tree object of path at revision, or None when it does not exist there."""
    result = subprocess.run(['git', '-C', str(tree), 'rev-parse', '--verify', '--quiet', revision + ':' + path],
                            capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def check_planned_sources(tree, joins, planned):
    """Before any output exists: every joined archive will be in the kit at its pin, and every
    commit-pinned tree is unchanged in the revision whose sources the kit carries."""
    for name, entry in sorted(joins['binaries'].items()):
        for source in entry['sources']:
            if 'commit' in source:
                pinned = git_tree_id(tree, source['commit'], source['tree'])
                require(pinned is not None and pinned == git_tree_id(tree, 'HEAD', source['tree']),
                        'Joined source tree ' + source['tree'] + ' differs from ' + source['commit'][:12] +
                        ' for ' + name + '; assemble from that commit or join the source that built it')
            elif 'archive' in source:
                require(source['archive'] in planned,
                        'Joined source archive will not be in the kit for ' + name + ': ' + source['archive'])
                path = planned[source['archive']]
                # The assembler writes generated archives afresh each time, so they can't carry a pin.
                require('sha256' not in source or path is not None,
                        'Joined source archive is generated by the assembler and cannot be pinned for ' +
                        name + ': ' + source['archive'])
                require('sha256' not in source or sha(path) == source['sha256'],
                        'Joined source archive changed for ' + name + ': ' + source['archive'])


def check_join_sources(output, tracked, joins):
    """Every source a join names is in the kit, at its pinned identity."""
    for name, entry in sorted(joins['binaries'].items()):
        for source in entry['sources']:
            if 'tree' in source:
                prefix = source['tree'].rstrip('/') + '/'
                require(any(path == source['tree'] or path.startswith(prefix) for path in tracked),
                        'Joined source tree is not in the release integration: ' + source['tree'])
                continue
            path = output / source['archive']
            require(path.is_file() and not path.is_symlink(),
                    'Joined source archive missing for ' + name + ': ' + source['archive'])
            require('sha256' not in source or sha(path) == source['sha256'],
                    'Joined source archive changed for ' + name + ': ' + source['archive'])


def document(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def command(*args):
    subprocess.run(list(map(str, args)), check=True)


def source_revision(tree):
    status = subprocess.check_output(
        ['git', '-C', str(tree), 'status', '--porcelain', '--untracked-files=no'], text=True)
    require(not status, 'Release source has tracked changes; commit before assembly')
    return subprocess.check_output(
        ['git', '-C', str(tree), 'rev-parse', '--verify', 'HEAD'], text=True).strip()


def unique_receipt_object(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, 'Duplicate kernel receipt key')
        value[key] = item
    return value


def kernel_input(kernel, receipt_path, tree, revision):
    """Accept the exact build only with recorded native startup evidence."""
    require((kernel is None) == (receipt_path is None),
            '--kernel and --kernel-receipt must be supplied together')
    if kernel is None:
        return None
    require(kernel.is_file() and not kernel.is_symlink() and
            kernel.stat().st_size == KERNEL_IDENTITY['kernel']['bytes'], 'Unknown kernel input')
    require(receipt_path.is_file() and not receipt_path.is_symlink() and
            0 < receipt_path.stat().st_size <= 65536, 'Invalid kernel receipt input')
    raw = receipt_path.read_bytes()
    receipt = json.loads(raw, object_pairs_hook=unique_receipt_object)
    fields = {'schema', 'kind', 'board', 'soc', 'source_revision', 'kernel', 'build', 'patches',
              'native_startup_qualified', 'native_checks', 'evidence_sha256',
              'fresh_install_qualified', 'physical_acceptance'}
    require(type(receipt) is dict and set(receipt) == fields, 'Unknown kernel receipt schema')
    require(type(receipt['schema']) is int and receipt['schema'] == 1 and
            receipt['kind'] == 'j713-afe-action-kernel' and receipt['board'] == 'apple,j713' and
            receipt['soc'] == 'apple,t8132', 'Unsupported kernel receipt board/schema')
    require(receipt['source_revision'] == revision, 'Kernel receipt source revision mismatch')
    require(type(receipt['kernel']) is dict and type(receipt['kernel'].get('bytes')) is int and
            type(receipt['build']) is dict and type(receipt['build'].get('export_rows')) is int and
            receipt['kernel'] == KERNEL_IDENTITY['kernel'] and
            receipt['build'] == KERNEL_IDENTITY['build'] and
            receipt['build']['exports_unchanged'] is True, 'Unrecognized kernel build identity')
    checks = receipt['native_checks']
    require(receipt['native_startup_qualified'] is True and
            type(checks) is dict and set(checks) == NATIVE_KERNEL_CHECKS and
            all(value is True for value in checks.values()), 'Kernel native startup evidence is incomplete')
    require(receipt['fresh_install_qualified'] is False and
            receipt['physical_acceptance'] == 'not_performed', 'Kernel receipt exceeds startup scope')
    require(isinstance(receipt['evidence_sha256'], str) and
            re.fullmatch('[0-9a-f]{64}', receipt['evidence_sha256']) is not None,
            'Kernel evidence hash is missing')
    require(receipt['patches'] == KERNEL_PATCHES, 'Kernel source patch identities differ')
    for name in (*KERNEL_PATCHES, 'docs/j713-afe-native-action.md', 'docs/release-source.md'):
        path = image_path(tree, name)
        require(path.is_file(), 'Missing kernel source: ' + name)
        data = path.read_bytes()
        if name in KERNEL_PATCHES:
            require(hashlib.sha256(data).hexdigest() == KERNEL_PATCHES[name],
                    'Kernel source patch changed: ' + name)
        committed = subprocess.check_output(['git', '-C', str(tree), 'show', revision + ':' + name])
        require(data == committed, 'Kernel source is not frozen in the release revision: ' + name)
    data = kernel.read_bytes()
    require(len(data) == receipt['kernel']['bytes'] and
            hashlib.sha256(data).hexdigest() == receipt['kernel']['sha256'], 'Kernel input hash mismatch')
    return {'data': data, 'receipt': receipt, 'receipt_bytes': raw}


def stage1_input(path, tree, revision):
    """Accept only the #82 stage 1 build, with its patch frozen in the release revision."""
    if path is None:
        return None
    require(path.is_file() and not path.is_symlink() and path.stat().st_size == STAGE1_BYTES,
            'Unknown stage 1 input')
    data = path.read_bytes()
    require(hashlib.sha256(data).hexdigest() == STAGE1_ANS1, 'Unknown stage 1 input')
    patch = image_path(tree, STAGE1_PATCH)
    require(patch.is_file(), 'Missing stage 1 source: ' + STAGE1_PATCH)
    committed = subprocess.check_output(['git', '-C', str(tree), 'show', revision + ':' + STAGE1_PATCH])
    require(patch.read_bytes() == committed, 'Stage 1 source is not frozen in the release revision')
    return data


def gpu_module_input(path):
    """Accept only the fixed Air GPU module; its source join pins gpu/j713 (check_planned_sources)."""
    require(path.is_file() and not path.is_symlink() and path.stat().st_size == GPU_FIXED_BYTES,
            'Unknown GPU module input')
    data = path.read_bytes()
    require(hashlib.sha256(data).hexdigest() == GPU_FIXED, 'Unknown GPU module input')
    return data


def module_pins(sums):
    """The digests runtime.sha256 pins for the GPU module."""
    return [fields[0] for fields in (line.split() for line in sums.decode().splitlines())
            if len(fields) == 2 and fields[1] == '/' + GPU_MODULE]


def prepare_gpu(root, module):
    """Swap alpha.1's GPU module and its one pin for the fixed module, keeping alpha.1's files."""
    path, sums_path = image_path(root, GPU_MODULE), image_path(root, GPU_SUMS)
    require(path.is_file() and not path.is_symlink() and sha(path) == GPU_ALPHA1,
            'Unknown retained GPU module')
    require(sums_path.is_file() and not sums_path.is_symlink(), 'Missing retained GPU pins')
    sums = sums_path.read_bytes()
    require(module_pins(sums) == [GPU_ALPHA1], 'Retained GPU pins disagree with the module')
    backup = root / GPU_BACKUP
    require(not backup.exists() and not backup.is_symlink(), 'GPU module state already present')
    old = path.read_bytes()
    backup.mkdir(mode=0o755, parents=True)
    backup.chmod(0o755)
    for name, data in (('alpha1-module.ko', old), ('alpha1-runtime.sha256', sums)):
        (backup / name).write_bytes(data)
        (backup / name).chmod(0o600)
    fixed = ''.join(f'{GPU_FIXED}  /{GPU_MODULE}\n' if line.split()[1:] == ['/' + GPU_MODULE] else line
                    for line in sums.decode().splitlines(keepends=True)).encode()
    # Rewritten in place, so both keep their retained modes.
    path.write_bytes(module)
    sums_path.write_bytes(fixed)
    require(sha(path) == GPU_FIXED and module_pins(sums_path.read_bytes()) == [GPU_FIXED],
            'GPU module swap did not take')


def release_boot(original, kernel):
    boot = copy.deepcopy(original)
    boot['dtb']['sha256'] = POWEROFF_DTB
    if kernel is not None:
        require(boot['kernel']['sha256'] == BASE_KERNEL, 'Unknown retained kernel prerequisite')
        args = boot['bootargs'].split()
        require(args.count('dockchannel_hid.m4_power_v2=1') == 1 and
                sum(arg.startswith('dockchannel_hid.m4_power_v2=') for arg in args) == 1 and
                not any(arg.startswith(('dockchannel_hid.m4_afe_action=',
                                        'dockchannel_hid.m4_afe_power=')) for arg in args),
                'Unexpected retained input boot arguments')
        boot['kernel']['sha256'] = kernel['receipt']['kernel']['sha256']
        boot['bootargs'] += ' dockchannel_hid.m4_afe_action=1'
    return boot


def patch_main(data):
    text = data.decode('utf-8')
    changes = {
        'import system, osenum, stub, diskutil, osinstall, gravity_firmware, m1n1\n':
        'import system, osenum, stub, diskutil, osinstall, gravity_firmware, m1n1\nimport j713_owner_assets\n',
        '    def __init__(self, version):\n':
        '    def __init__(self, version):\n        self.owner_assets = j713_owner_assets.prepare_for_installer()\n',
        '        self.ins.collect_firmware(fw_pkg)\n':
        '        self.ins.collect_firmware(fw_pkg)\n        j713_owner_assets.add_to_firmware(fw_pkg, self.owner_assets)\n',
        '            self.ins.collect_firmware(pkg)\n':
        '            self.ins.collect_firmware(pkg)\n            j713_owner_assets.add_to_firmware(pkg, self.owner_assets)\n',
    }
    for old, new in changes.items():
        require(text.count(old) == 1, 'Installer source seam changed: ' + old.strip())
        text = text.replace(old, new)
    return text.encode('utf-8')


def repack_installer(source, destination, module, source_archive=False, stage1=None):
    entry = 'src/main.py' if source_archive else 'main.py'
    addition = 'src/j713_owner_assets.py' if source_archive else 'j713_owner_assets.py'
    require(stage1 is None or not source_archive, 'Stage 1 belongs to the macOS installer only')
    replaced = loaders = 0
    names = set()
    with tarfile.open(source, 'r:gz') as old, tarfile.open(destination, 'x:gz') as new:
        for item in old:
            name = item.name.removeprefix('./')
            require(name not in names, 'Duplicate installer member')
            names.add(name)
            require(name != addition, 'Installer module already exists')
            if name == entry:
                require(item.isfile(), 'Installer entry is not regular')
                data = patch_main(old.extractfile(item).read())
                item = copy.copy(item)
                item.size = len(data)
                new.addfile(item, io.BytesIO(data))
                replaced += 1
            elif name == STAGE1_MEMBER and not source_archive:
                require(item.isfile(), 'Installer stage 1 is not regular')
                data = old.extractfile(item).read()
                require(hashlib.sha256(data).hexdigest() == STAGE1_ALPHA1, 'Unknown installer stage 1')
                loaders += 1
                if stage1 is not None:
                    item = copy.copy(item)
                    item.size = len(stage1)
                    data = stage1
                new.addfile(item, io.BytesIO(data))
            else:
                new.addfile(item, old.extractfile(item) if item.isfile() else None)
        require(replaced == 1, 'Missing installer entry point')
        require(source_archive or loaders == 1, 'Missing installer stage 1')
        item = tarfile.TarInfo(addition)
        item.mode, item.size = 0o644, len(module)
        new.addfile(item, io.BytesIO(module))


def clean_integration(source, destination):
    excluded = {'kit/packages/audio/root/usr/share/asahi-audio/j713/' + name for name in FIRS}
    excluded.update('kit/packages/video/root/etc/skel/.config/omarchy/pending/widevine-' + p + '.json'
                    for p in PROFILES)
    removed = set()
    with tarfile.open(source, 'r:gz') as old, tarfile.open(destination, 'x:gz') as new:
        for item in old:
            name = item.name.removeprefix('./')
            if name in excluded:
                require(item.isfile(), 'Unexpected excluded member type')
                removed.add(name)
            else:
                new.addfile(item, old.extractfile(item) if item.isfile() else None)
    require(removed == excluded, 'Private integration input set changed')


def fill_free_space(root):
    # An actual zero write replaces freed blocks. Sparse allocation would not.
    path = root / '.release-free-space'
    require(not path.exists(), 'Free-space filler already exists')
    try:
        with path.open('xb', buffering=0) as stream:
            block = bytes(4 * 1024 * 1024)
            try:
                while True:
                    stream.write(block)
            except OSError as error:
                require(error.errno == errno.ENOSPC, 'Unexpected free-space write error: ' + str(error))
            try:
                os.fsync(stream.fileno())
            except OSError as error:
                require(error.errno == errno.ENOSPC, 'Unexpected free-space flush error: ' + str(error))
    finally:
        path.unlink(missing_ok=True)
    os.sync()


def owner_audio_manifest(raw, previous_pin, profile):
    """Bind the static FIR identities and record their provenance."""
    require(hashlib.sha256(raw).hexdigest() == previous_pin,
            'Unexpected retained audio manifest')
    require(set(profile['outputs']) == set(FIRS) == set(profile['reference_outputs']),
            'Unexpected owner filter set')
    manifest = json.loads(raw)
    require(manifest['schema'] == 1 and manifest['board'] == 'apple,j713',
            'Unexpected retained audio manifest board/schema')
    for name in FIRS:
        previous, native = profile['reference_outputs'][name], profile['outputs'][name]
        key = 'usr/share/asahi-audio/j713/' + name
        require(manifest['files']['/' + key] == previous['sha256'] and
                manifest['file_metadata'][key] == {**previous, 'mode': 0o644},
                'Unexpected reference filter: ' + name)
        require(set(native) == {'size', 'sha256'} and
                type(native['size']) is int and native['size'] == previous['size'] and
                isinstance(native['sha256'], str) and
                re.fullmatch('[0-9a-f]{64}', native['sha256']),
                'Invalid native filter identity: ' + name)
        manifest['files']['/' + key] = native['sha256']
        manifest['file_metadata'][key]['sha256'] = native['sha256']
    manifest.pop('distribution_hold')
    manifest.pop('redistributable')
    manifest['filter_provenance'] = (
        'Derived from Apple J713 tuning through the retained pipeline; see audio/j713/filters/README.md.'
    )
    return (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode()


def prepare_startup(root, tree, raw):
    """Carry startup repairs into the image and both runtime integrity maps."""
    gpu = image_path(root, 'etc/systemd/system/m4-gpu.service')
    require(gpu.is_file() and sha(gpu) == BASE_GPU_UNIT, 'Unknown retained graphics unit')
    manifest = json.loads(raw)
    replacements = {}
    for name, pin, source in (
            ('usr/lib/omarchy/m4-j713-audio-register.py', BASE_AUDIO_REGISTER,
             'tools/m4-j713-audio-register.py'),
            ('usr/lib/systemd/system/m4-j713-audio-register.service', BASE_AUDIO_UNIT,
             'packaging/j713-audio/m4-j713-audio-register.service')):
        path = image_path(root, name)
        require(path.is_file() and sha(path) == pin, 'Unknown retained audio registration file: ' + name)
        previous = dict(sha256=pin, size=path.stat().st_size, mode=0o644)
        require(manifest['files']['/' + name] == pin and
                manifest['file_metadata'][name] == previous and
                path.stat().st_mode & 0o777 == 0o644,
                'Retained audio registration identity maps disagree: ' + name)
        replacements[name] = image_path(tree, source).read_bytes()
    gpu_replacement = image_path(tree, 'packaging/j713-startup/m4-gpu.service').read_bytes()
    configure = runpy.run_path(str(tree / 'tools/configure-j713-first-boot.py'))['apply']
    configure(root, tree)
    owner_setup = runpy.run_path(str(tree / 'tools/configure-j713-owner-setup.py'))['configure']
    owner_setup(root, apply=True)
    gpu.write_bytes(gpu_replacement)
    gpu.chmod(0o644)
    for name, replacement in replacements.items():
        path = image_path(root, name)
        path.write_bytes(replacement)
        path.chmod(0o644)
        pin = hashlib.sha256(replacement).hexdigest()
        manifest['files']['/' + name] = pin
        manifest['file_metadata'][name] = dict(sha256=pin, size=len(replacement), mode=0o644)
    return (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode()


def prepare_decoder(root, tree, kernel=None):
    """Replace the retained decoder wait and carry its exact identity forward."""
    name = 'usr/local/bin/m4-video-start'
    path = image_path(root, name)
    metadata = image_path(root, 'etc/omarchy/j713-video-manifest.json')
    require(metadata.is_file() and sha(metadata) == BASE_VIDEO, 'Unknown retained video manifest')
    require(path.is_file() and sha(path) == BASE_VIDEO_START and
            path.stat().st_mode & 0o777 == 0o755, 'Unknown retained decoder startup helper')
    manifest = json.loads(metadata.read_bytes())
    require(manifest['files'][name] == dict(sha256=BASE_VIDEO_START, size=path.stat().st_size, mode=0o755),
            'Retained decoder identity map disagrees')
    if kernel is not None:
        require(manifest['board'] == 'apple,j713' and
                manifest['boot_kernel_sha256'] == BASE_KERNEL and
                manifest['kernel_release'] == kernel['receipt']['build']['kernel_release'],
                'Retained decoder kernel identity mismatch')
        manifest['boot_kernel_sha256'] = kernel['receipt']['kernel']['sha256']
    replacement = image_path(tree, 'packaging/j713-startup/m4-video-start').read_bytes()
    path.write_bytes(replacement)
    path.chmod(0o755)
    manifest['files'][name] = dict(sha256=hashlib.sha256(replacement).hexdigest(),
                                 size=len(replacement), mode=0o755)
    document(metadata, manifest)
    return sha(metadata)


def static_filters(tree):
    """Verify the complete approved platform profile before changing an image."""
    profile = json.loads((tree / 'audio/j713/owner-fir/manifest.json').read_bytes())
    require(set(profile['outputs']) == set(FIRS), 'Unexpected static FIR set')
    payload = {}
    for name, record in profile['outputs'].items():
        path = tree / 'audio/j713/filters' / name
        require(path.is_file() and not path.is_symlink(), 'Missing static FIR: ' + name)
        data = path.read_bytes()
        require(len(data) == record['size'] and hashlib.sha256(data).hexdigest() == record['sha256'],
                'Static FIR identity mismatch: ' + name)
        payload[name] = data
    return profile, payload


def prepare_root(source, image, root, tree, identity, kernel=None, allow_unverified=False, gpu_module=None):
    manifest, filters = static_filters(tree)
    require(sha(source) == BASE_ROOT, 'Retained root image changed')
    command('cp', '--reflink=auto', '--sparse=always', source, image)
    root.mkdir()
    mounted = False
    try:
        command('mount', '-o', 'loop,nodev,nosuid', image, root)
        mounted = True
        completion = root / 'etc/m4-install-complete'
        require(not completion.exists() and not completion.is_symlink(),
                'Retained root must not have completed first boot')
        audio_manifest = root / 'etc/omarchy/j713-audio-manifest.json'
        native_audio = owner_audio_manifest(audio_manifest.read_bytes(), BASE_AUDIO, manifest)
        native_audio = prepare_startup(root, tree, native_audio)
        if gpu_module is not None:
            prepare_gpu(root, gpu_module)
        video_pin = (prepare_decoder(root, tree) if kernel is None else
                     prepare_decoder(root, tree, kernel=kernel))
        for name in FIRS:
            path = root / 'usr/share/asahi-audio/j713' / name
            require(path.is_file() and not path.is_symlink() and
                    sha(path) == manifest['reference_outputs'][name]['sha256'], 'Unexpected original FIR')
            path.write_bytes(filters[name])
            path.chmod(0o644)
        audio_manifest.write_bytes(native_audio)
        for profile in PROFILES:
            path = root / ('etc/skel/.config/omarchy/pending/widevine-' + profile + '.json')
            require(json.loads(path.read_bytes()) == {'schema': 1, 'profile': profile,
                                                     'consent': 'owner-requested'}, 'Unexpected preset choice')
            with path.open('r+b') as stream:
                stream.write(bytes(path.stat().st_size)); stream.flush(); os.fsync(stream.fileno())
            path.unlink()
        additions = {
            'usr/lib/omarchy/m4-j713-owner-assets.py': 'tools/m4-j713-owner-assets.py',
            'usr/lib/systemd/system/j713-owner-assets.service': 'packaging/j713-owner-assets.service',
            'usr/lib/systemd/system/systemd-user-sessions.service.d/10-owner-assets.conf':
                'packaging/j713-owner-assets-session.conf',
            'usr/lib/systemd/system/omarchy-provision-owner.service.d/10-owner-assets.conf':
                'packaging/j713-owner-assets-provision.conf',
            # Connect to the signed M4 update channel once the owner is online.
            'usr/lib/omarchy/m4-channel-connect.py': 'tools/m4-channel-setup.py',  # not the package's copy path
            'usr/lib/systemd/system/m4-channel-connect.service': 'packaging/j713-first-boot/m4-channel-connect.service',
            'usr/lib/systemd/system/m4-channel-connect.timer': 'packaging/j713-first-boot/m4-channel-connect.timer',
        }
        for target, source_name in additions.items():
            path = root / target
            require(not path.exists() and not path.is_symlink(), 'Root addition already present')
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(tree / source_name, path)
            path.chmod(0o644)
        link = root / 'etc/systemd/system/multi-user.target.wants/j713-owner-assets.service'
        require(not link.exists() and not link.is_symlink(), 'Owner service already enabled')
        link.symlink_to('/usr/lib/systemd/system/j713-owner-assets.service')
        timer = root / 'etc/systemd/system/timers.target.wants/m4-channel-connect.timer'
        require(not timer.exists() and not timer.is_symlink(), 'Channel connect timer already enabled')
        timer.parent.mkdir(parents=True, exist_ok=True)
        timer.symlink_to('/usr/lib/systemd/system/m4-channel-connect.timer')
        (root / 'etc/m4-root-id').write_text(identity + '\n')
        unverified = check_binary_joins(unpackaged_binaries(root), source_joins(tree), allow_unverified)
        fill_free_space(root)
        command('umount', root)
        mounted = False
    finally:
        if mounted:
            command('umount', root)
    command('tune2fs', '-U', identity, image)
    # The installer requires a clean ext4 image checked after its last write mount.
    command('e2fsck', '-f', '-p', image)
    return {'audio': hashlib.sha256(native_audio).hexdigest(), 'video': video_pin, 'unverified': unverified}


def assemble(args):
    require(sys.version_info >= (3, 12), 'Build requires Python 3.12 or newer')
    require(os.geteuid() == 0, 'Build on a Linux host through its scoped root job')
    tree, baseline = args.source_tree.resolve(), args.baseline.resolve()
    revision = source_revision(tree)
    tracked = [raw.decode() for raw in subprocess.check_output(
        ['git', '-C', str(tree), 'ls-files', '-z']).split(b'\0') if raw]
    require(all('audio/j713/filters/' + name in tracked for name in FIRS),
            'Static FIR assets must be tracked in the release source')
    kernel = kernel_input(getattr(args, 'kernel', None), getattr(args, 'kernel_receipt', None),
                          tree, revision)
    stage1 = stage1_input(getattr(args, 'stage1', None), tree, revision)
    gpu_module = gpu_module_input(args.gpu_module)
    require(args.output.is_absolute() and not args.output.exists() and
            not args.output.is_symlink(), 'A new absolute build output is required')
    require(shutil.disk_usage(args.output.parent).free > 40 * 1024**3,
            'Need 40 GiB free on the build host')
    require(sha(baseline / 'SHA256SUMS') == BASE_SUMS, 'Unknown retained delivery')
    for line in (baseline / 'SHA256SUMS').read_text().splitlines():
        pin, name = line.split('  ', 1)
        require(sha(baseline / name) == pin, 'Retained delivery mismatch: ' + name)
    require(sha(args.cargo_source) == 'faf50f21db4b622900bd275441bc321d1a01933afb555e84d1446d38d6027a34',
            'Unknown locked dependency sources')
    require(sha(args.sound_exports) == '9f3e2cf8c83045e3a89b94ef7c8526c4dfb10b386d872669819cd8f4f35d4362',
            'Unknown supplemental sound exports')
    require(sha(args.reconstruction) == '47c144a606c13f218b1bd84d8a1b86d6d9ac281a58a04d0c191e0508221b1e7c',
            'Unknown source reconstruction guidance')
    require(args.poweroff_dtb.is_file() and not args.poweroff_dtb.is_symlink() and
            sha(args.poweroff_dtb) == POWEROFF_DTB, 'Unknown SMC poweroff device tree')
    static_filters(tree)
    config = json.loads((baseline / 'installer_data.json').read_bytes())
    old_image = json.loads((baseline / 'IMAGE.json').read_bytes())
    require(old_image['audio_manifest_sha256'] == BASE_AUDIO, 'Baseline audio identity changed')
    require(old_image['video_manifest_sha256'] == BASE_VIDEO, 'Baseline video identity changed')
    require(config['os_list'][0]['m4_boot'] == old_image['boot'], 'Baseline boot pins disagree')
    boot = release_boot(old_image['boot'], kernel)
    joins = source_joins(tree)
    archives = {source['archive']: source.get('sha256') for entry in joins['binaries'].values()
                for source in entry['sources'] if 'archive' in source}
    join_sources = getattr(args, 'join_source', None) or []
    for path in join_sources:
        name = 'source/' + path.name
        require(path.is_file() and not path.is_symlink() and archives.get(name) and sha(path) == archives[name],
                'Join source is not a pinned archive any join names: ' + str(path))
    # Where each source/ file of the kit comes from (None: written by this assembler).
    planned = {'source/' + path.name: path for path in sorted((baseline / 'source').glob('*'))
               if path.is_file()}
    planned.update({'source/j713-audio-cargo-sources.tar.gz': args.cargo_source,
                    'source/reference-sound-exports.symvers': args.sound_exports,
                    'source/j713-audio-reconstruction.tar': args.reconstruction})
    planned.update({'source/' + path.name: path for path in join_sources})
    planned.update(dict.fromkeys(GENERATED_SOURCES))
    check_planned_sources(tree, joins, planned)
    allow_unverified = getattr(args, 'allow_unverified_source', False)
    args.output.mkdir(mode=0o700)
    output = args.output / 'delivery'
    output.mkdir()
    image = args.output / 'root.ext4'
    identity = str(uuid.uuid4())
    root_args = (args.root_image.resolve(), image, args.output / 'mounted-root', tree, identity)
    root_options = {'kernel': kernel} if kernel is not None else {}
    root_options['gpu_module'] = gpu_module
    if allow_unverified:
        root_options['allow_unverified'] = True
    manifest_pins = prepare_root(*root_args, **root_options)
    old_package = config['os_list'][0]['package']
    new_package = 'omarchy-j713-' + VERSION + '.zip'
    skipped = {'SHA256SUMS', 'KIT.json', 'IMAGE.json', 'installer_data.json', 'Install.command',
               'START-HERE.md', 'KNOWN-LIMITATIONS.md', 'fir-origin.json', 'installer-macos.tar.gz',
               'source/integration-inputs.tar.gz', 'source/installer-source.tar.gz',
               'source/README.md', 'os/' + old_package}
    for source in sorted(baseline.rglob('*')):
        if source.is_dir():
            continue
        name = source.relative_to(baseline).as_posix()
        require(not source.is_symlink(), 'Unexpected delivery link')
        if name not in skipped:
            target = output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            target.chmod(0o644)
    (output / 'os').mkdir(exist_ok=True)
    with zipfile.ZipFile(baseline / 'os' / old_package) as old, zipfile.ZipFile(
            output / 'os' / new_package, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as new:
        require(set(old.namelist()) == {'root.ext4', *(p['file'] for k, p in old_image['boot'].items()
                                                     if k != 'bootargs')}, 'Unexpected boot archive')
        new.write(image, 'root.ext4')
        for kind, entry in old_image['boot'].items():
            if kind == 'bootargs':
                continue
            data = old.read(entry['file'])
            require(hashlib.sha256(data).hexdigest() == entry['sha256'], 'Boot payload changed')
            if kind == 'dtb':
                data = args.poweroff_dtb.read_bytes()
            elif kind == 'kernel' and kernel is not None:
                data = kernel['data']
            require(hashlib.sha256(data).hexdigest() == boot[kind]['sha256'],
                    'Release boot payload pin mismatch')
            new.writestr(entry['file'], data)
    module = (tree / 'tools/m4-j713-owner-assets.py').read_bytes()
    repack_installer(baseline / 'installer-macos.tar.gz', output / 'installer-macos.tar.gz', module,
                     stage1=stage1)
    repack_installer(baseline / 'source/installer-source.tar.gz',
                     output / 'source/installer-source.tar.gz', module, True)
    clean_integration(baseline / 'source/integration-inputs.tar.gz', output / 'source/integration-inputs.tar.gz')
    shutil.copyfile(args.cargo_source, output / 'source/j713-audio-cargo-sources.tar.gz')
    shutil.copyfile(args.sound_exports, output / 'source/reference-sound-exports.symvers')
    shutil.copyfile(args.reconstruction, output / 'source/j713-audio-reconstruction.tar')
    for path in join_sources:
        target = output / 'source' / path.name
        require(not target.exists(), 'Join source would replace a retained archive: ' + path.name)
        shutil.copyfile(path, target)
    for source_name, destination in (
            ('packaging/Install.command', 'Install.command'),
            ('docs/install.md', 'START-HERE.md'),
            ('docs/release-limitations.md', 'KNOWN-LIMITATIONS.md'),
            ('docs/updates.md', 'UPDATES.md'),
            ('docs/j713-poweroff.md', 'source/j713-poweroff.md'),
            ('docs/release-source.md', 'source/README.md')):
        shutil.copyfile(tree / source_name, output / destination)
    # The current repository sources are a separate exact archive; private build
    # logs, machine credentials and workflow instructions never enter this archive.
    integration = [name for name in tracked if Path(name).name not in ('AGENTS.md', 'CLAUDE.md')]
    with tarfile.open(output / 'source/release-integration.tar.gz', 'x:gz') as archive:
        for name in integration:
            archive.add(tree / name, arcname=name, recursive=False)
    check_join_sources(output, integration, joins)
    if kernel is not None:
        (output / 'source/j713-kernel-receipt.json').write_bytes(kernel['receipt_bytes'])
    config['os_list'][0]['package'] = new_package
    config['os_list'][0]['name'] = 'Omarchy M4 Native ' + VERSION + ' (experimental J713)'
    config['os_list'][0]['m4_boot'] = boot
    roots = [p for p in config['os_list'][0]['partitions'] if 'rootfs_uuid' in p]
    require(len(roots) == 1, 'Unexpected root layout')
    roots[0].update(rootfs_uuid=identity, size=str(image.stat().st_size) + 'B')
    document(output / 'installer_data.json', config)
    require(source_revision(tree) == revision, 'Release source revision changed during assembly')
    metadata = {
        'release_version': VERSION, 'private_only': False, 'source_revision': revision,
        'credit': 'studioxradio aka kb2uka', 'base_omarchy_version': '4.0.4',
        'boot': boot,
        'root': {'bytes': image.stat().st_size, 'sha256': sha(image), 'filesystem_uuid': identity},
        'audio_manifest_sha256': manifest_pins['audio'],
        'video_manifest_sha256': manifest_pins['video'],
        'fresh_install_qualified': False,
        'qualification': 'The preceding owner-input candidate failed fresh startup. This image includes startup and recovery corrections; a successful owner installation is still required before publication.',
        'owner_inputs': 'The root image contains the exact approved J713 FIR profile; Widevine requires each owner choice.',
        'baseline_root_sha256': BASE_ROOT,
        'source_joins_sha256': sha(tree / SOURCE_JOINS),
        'source_correspondence_unverified': manifest_pins['unverified'],
        'poweroff': {'change': 'Enable existing SMC handler with its Abbey shutdown-state PMU cells',
                    'hardware_verified': False, 'issue': 24},
        'stage1': ({'file': 'installer-macos.tar.gz:' + STAGE1_MEMBER, 'sha256': STAGE1_ALPHA1}
                   if stage1 is None else
                   {'file': 'installer-macos.tar.gz:' + STAGE1_MEMBER, 'sha256': STAGE1_ANS1,
                    'replaces_sha256': STAGE1_ALPHA1, 'source': STAGE1_PATCH, 'issue': 82}),
        'gpu_module': {'file': GPU_MODULE, 'sha256': GPU_FIXED, 'replaces_sha256': GPU_ALPHA1,
                       'source': GPU_SOURCE, 'issues': [57, 118],
                       'rollback': GPU_BACKUP + '/alpha1-module.ko'},
    }
    if kernel is not None:
        metadata.update(kernel_receipt_sha256=hashlib.sha256(kernel['receipt_bytes']).hexdigest(),
                        kernel_native_startup_qualified=True,
                        qualification='Candidate kernel passed the objective native startup checks recorded '
                        'in source/j713-kernel-receipt.json. Physical acceptance and an attended fresh '
                        'installation remain unverified; this is not publication qualification.')
    document(output / 'IMAGE.json', metadata)
    files = {}
    for path in sorted(output.rglob('*')):
        if path.is_file():
            require(not path.is_symlink(), 'Unexpected final delivery link')
            path.chmod(0o755 if path.name == 'Install.command' else 0o644)
            files[path.relative_to(output).as_posix()] = {'bytes': path.stat().st_size, 'sha256': sha(path)}
    kit = {key: metadata[key] for key in ('release_version', 'private_only', 'source_revision',
                                        'base_omarchy_version', 'credit', 'fresh_install_qualified', 'qualification')}
    kit['files'] = files
    document(output / 'KIT.json', kit)
    pins = {name: record['sha256'] for name, record in files.items()}
    pins['KIT.json'] = sha(output / 'KIT.json')
    (output / 'SHA256SUMS').write_text(''.join(pin + '  ' + name + '\n' for name, pin in sorted(pins.items())))
    document(args.output / 'assembly.json', {'kit_sha256': sha(output / 'KIT.json'),
                                           'checksums_sha256': sha(output / 'SHA256SUMS'),
                                           'source_revision': revision, 'root_uuid': identity})
    print('Release delivery assembled at ' + str(output), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source-tree', 'baseline', 'root-image', 'cargo-source',
                 'sound-exports', 'reconstruction', 'poweroff-dtb', 'gpu-module', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--kernel', type=Path, help='Exact startup-qualified J713 AFE action Image.gz')
    parser.add_argument('--kernel-receipt', type=Path, help='Matching build and native startup receipt')
    parser.add_argument('--stage1', type=Path,
                        help='The #82 m1n1 stage 1 build, in place of alpha.1\'s in the macOS installer')
    parser.add_argument('--join-source', type=Path, action='append', default=[],
                        help='A source archive that ' + SOURCE_JOINS + ' names and pins; copied into source/')
    parser.add_argument('--allow-unverified-source', action='store_true',
                        help='Reassemble alpha.1 with joins not yet proven by an identical rebuild; '
                             'IMAGE.json lists them')
    assemble(parser.parse_args())


if __name__ == '__main__':
    main()
