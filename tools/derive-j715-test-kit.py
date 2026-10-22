#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
# Copyright (c) 2026 Kazuhide Takahashi
"""Derive the 15-inch M4 Air (J715) tester kit from the J713 installer update 4 kit.

Runs on a Linux build host with debugfs, e2fsck and patch (docs/j715-test-kit.md).
The J713 kit is checked against its pins. The output kit changes only:

- the boot devicetree (the J715 one from tools/build-j715-dtb.py) and the kernel
  (build 18 with patches-kernel/j715-input, so the built-in keyboard and trackpad work);
- the macOS installer: packaging/j715-test/installer-j715.patch adds the j715ap
  profile and the kos radio firmware to four of its files;
- the installer definition: OS name, OS package, devicetree and kernel;
- Install.command, START-HERE.md, KNOWN-LIMITATIONS.md and UPDATES.md (packaging/j715-test);
- in the root image: the GPU module that admits the J715 and its pin, m4-gpu-start's
  board check, the session placement patched to admit the J715 (the live gates keep
  refusing a testing board), and the start links and
  drop-ins of the J713-only services that refuse a J715 anyway;
- the J715 sources beside the kit's source bundle, and KIT.json, IMAGE.json and
  SHA256SUMS, regenerated for those changes.
"""
import argparse
import copy
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FILES = ROOT / 'packaging/j715-test'

# Installer update 4 (v0.1.0-alpha.1-update.4), the J713 kit this kit derives from.
KIT_SHA256 = '9f216fd08695049b3c2affdabe459477795c4a0a765526d7d2dcfe706a2ff345'
SUMS_SHA256 = '454569f6092e8413e15b438ded17c99e9a4e7b20f87787ebb8193badd95b6428'
OLD_PACKAGE = 'omarchy-j713-0.1.0-alpha.1.zip'
OLD_ROOT_SHA256 = '995b5c2a3a331007056a529e51bf8e805c6c3827abf624efb8e6b8f16788665a'
OLD_DTB = ('boot/portable.dtb', 'e54c77a760a5b696486f9d80c7c83ca4295d641113bb9e54d13e259863bf9b07')
OLD_KERNEL = ('boot/Image.gz', 'c3704cfd2afb7760e73f0b9939a68864a30bd916fcd478bd960e538373123dc9')
KEPT_BOOT = {
    'boot/initramfs.cpio.gz': '0b4e8f8ac58ec7afda6de5810b1f96599f582297e246cbe7ac4dfdfad8e0af28',
    'boot/loader.bin': '3a3f7a64c65fc366d5ac9aece3e1bee64119be012aa1ec5b15d2743c6f91fce4',
}
OLD_ZIP_NAMES = ['root.ext4', OLD_DTB[0], 'boot/initramfs.cpio.gz', OLD_KERNEL[0], 'boot/loader.bin']

# The J715 devicetree (docs/j715-boot-dtb.md) and kernel (tools/build-j715-kernel.sh rebuilds it).
DTB = ('boot/j715.dtb', '84a19aba63365c5b07a6f53780c0b48f0d054e9bf0c787ef285e7c9cf401f066')
KERNEL = ('boot/Image.gz', '291c4b95d3f1fbbffc0e11af3421b3125c3155050c90e97867cad6e3d244c05f')
LABEL = 'j715-test.1'
PACKAGE = 'omarchy-j715-0.1.0-alpha.1-test.1.zip'
OS_NAME = 'Omarchy M4 Native 0.1.0-alpha.1 (J715 tester)'
FIRMWARE = '26.5.1'  # the installer's j713ap and j715ap profiles

# macOS installer members the J715 profile patch changes: name -> (update 4, patched).
INSTALLER_FILES = {
    'main.py': ('6057bd16bfa7da725e9e531bdb59acfcb23d335fc3e2e73381017ee7d81918a2',
                'afdb70077a491c3fdd1a482aae2810306f58bf4c5b0a38e5abfaf0a065c951d2'),
    'gravity_firmware/bluetooth.py': ('ef5f079dc28d23f2bec7520a33bd139beb21b384e9f43e4f2c840e58d45840ee',
                                      '9289c70ea510ed3e830935add767a450e2d13ad980db656faf0b54571dd19022'),
    'gravity_firmware/open_firmware.py': ('244fba29dec42aee641592690278375ff31d93a91b4e893fbd532861af54ab7e',
                                          '8180355edc16fef9dcf46f5b452c67ca81d374b73ac2ffbcfc55d8d643083481'),
    'gravity_firmware/wifi.py': ('d268b07d2512baa5b3c440aaa292b6feb6b26bdcf1a93304cdc2714ccffb6377',
                                 'f79ebf956b9bd40be42bc54167777af0dff6facff1499dbac61243a34d392950'),
}
INSTALLER_PATCH = FILES / 'installer-j715.patch'
# The patch names main.py by its source path.
PATCH_PATHS = {'main.py': 'src/main.py'}

# The GPU module built from gpu/j713 with apple,j715 in the Air board check
# (packaging/channel-source-joins.json, kit_binaries asahi_m4-55831cf4.ko).
GPU_MODULE = '/opt/m4-gpu/asahi_m4.ko'
GPU_OLD = 'de0337a773930c7862a99706112dd71eaaecf2021707da248fd3d07feefdc824'
GPU_NEW = '55831cf4e8216ed423cd68fd39bcc989907f1958e8fbc07575351463906ab1f2'
GPU_SUMS = '/opt/m4-gpu/runtime.sha256'
GPU_SUMS_OLD = 'f4353ab283977d333ffba02759c4be26fbee0eaa167994e129dae6562fecc598'
GPU_START = '/usr/local/bin/m4-gpu-start'
GPU_START_OLD = '3caeb53871758d1aed220dd8c7f40e98890cb963a7be6768f12fc226d936c620'
GPU_START_LINE = (b'grep -zFxq apple,j713 /sys/firmware/devicetree/base/compatible\n',
                  b'grep -zFxq -e apple,j713 -e apple,j715 /sys/firmware/devicetree/base/compatible\n')
PLACEMENT = '/etc/skel/.local/lib/omarchy/m4-session-placement.py'
PLACEMENT_OLD = '21c8ebb8cd3239dc0bad07bd8aa57e054c0c59c6aae04c59fc7f6a356fb7e642'
# The live gates keep refusing a testing board, so the kit patches its own copies to
# admit the J715: the session placement in the root, and g16.rs (the source of the
# GPU module) beside the kit's sources. Each is (repository path, base, patch, result).
PLACEMENT_SOURCE = ('tools/m4-session-placement.py',
                    '4f535a3c4dc67f87cb4e17faad95cde022aac3521681aa13b71245bb7aedf54d',
                    FILES / 'm4-session-placement-j715.patch',
                    '38886367c2186c7eb22c4790347180323b57ddb6cb3c0a6e21d5afd8a73f29df')
G16_SOURCE = ('gpu/j713/asahi/g16.rs',
              'eb72d85b6d5ad2dbdbf7070d6381f3e309f06eff4f4d079fc4e0835e6e8e197c',
              FILES / 'g16-j715.patch',
              'aa88fa303e53fb287743d29b5ec20a00843a6a12fce0f715e6ea7960be3e1320')
# Start links of services whose board gates refuse a J715, and the update timer that
# would retry a refused channel setup for ever (as in the J773g tester kit).
ROOT_LINKS = (
    '/etc/systemd/system/multi-user.target.wants/m4-j713-audio-register.service',
    '/etc/systemd/system/multi-user.target.wants/speakersafetyd-j713.service',
    '/etc/systemd/system/multi-user.target.wants/m4-video.service',
    '/etc/systemd/system/multi-user.target.wants/j713-owner-assets.service',
    '/etc/systemd/system/timers.target.wants/m4-channel-connect.timer',
)
# Drop-ins that pull the J713 owner options service in without its start link, with
# their sha256 (as in the J773g tester kit). The provisioning one Requires it, so its
# J713 board check would fail the first-boot user setup and leave SDDM with no account.
ROOT_DROPINS = {
    '/usr/lib/systemd/system/omarchy-provision-owner.service.d/10-owner-assets.conf':
        'acef154cb62c6e97ec3c7106774d1b4ce8cabdfd538424fb9dcc2f4a1480e9bc',
    '/usr/lib/systemd/system/systemd-user-sessions.service.d/10-owner-assets.conf':
        '80f7b0786414de133a9b2ee3c83374e8fb53b7ea997b2046cc0dafe479fbdd46',
}
# Sources the kit adds beside its source bundle: published path -> repository path.
SOURCES = {
    'source/j715/dockchannel-hid-accept-j715.patch': 'patches-kernel/j715-input/dockchannel-hid-accept-j715.patch',
    'source/j715/installer-j715.patch': 'packaging/j715-test/installer-j715.patch',
    'source/j715/installer-j715-tests.patch': 'packaging/j715-test/installer-j715-tests.patch',
    'source/j715/g16-j715.patch': 'packaging/j715-test/g16-j715.patch',
    'source/j715/m4-session-placement-j715.patch': 'packaging/j715-test/m4-session-placement-j715.patch',
    'source/j715/j715-board.dtso': 'patches/j715-board.dtso',
}
REPLACED_DOCS = {'Install.command': 0o755, 'START-HERE.md': 0o644, 'KNOWN-LIMITATIONS.md': 0o644, 'UPDATES.md': 0o644}
REWRITTEN = {'KIT.json', 'IMAGE.json', 'SHA256SUMS', 'installer_data.json', 'installer-macos.tar.gz',
             'os/' + OLD_PACKAGE, *REPLACED_DOCS}
QUALIFICATION = ('J715 tester build derived from the J713 installer update 4 kit. One J715 has '
                 'installed it; it is not qualified for any Mac.')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def check_kit(kit):
    """Check the J713 kit's pins and every file its SHA256SUMS lists; return the listed names."""
    require(sha(kit / 'KIT.json') == KIT_SHA256, 'Unexpected J713 kit (KIT.json)')
    require(sha(kit / 'SHA256SUMS') == SUMS_SHA256, 'Unexpected J713 kit (SHA256SUMS)')
    names = []
    for line in (kit / 'SHA256SUMS').read_text().splitlines():
        digest, name = line.split('  ', 1)
        require(not name.startswith('/') and '..' not in Path(name).parts, 'Unsafe kit path')
        require(sha(kit / name) == digest, 'Kit file changed: ' + name)
        require((kit / name).stat().st_mode & 0o7777 in (0o644, 0o755), 'Unexpected kit file mode: ' + name)
        names.append(name)
    on_disk = {p.relative_to(kit).as_posix() for p in kit.rglob('*') if p.is_file()}
    require(on_disk == set(names) | {'SHA256SUMS'}, 'Kit has unlisted or missing files')
    return names


def patched_installer_files(old):
    """Apply installer-j715.patch to the four update 4 members; return name -> new bytes."""
    with tempfile.TemporaryDirectory(prefix='j715-installer-') as scratch:
        for name, data in old.items():
            path = Path(scratch) / PATCH_PATHS.get(name, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        result = subprocess.run(['patch', '-p1', '--batch', '--forward', '--no-backup-if-mismatch',
                                 '-i', str(INSTALLER_PATCH)],
                                cwd=scratch, capture_output=True, text=True)
        require(result.returncode == 0 and 'offset' not in result.stdout and 'fuzz' not in result.stdout,
                'Installer patch did not apply exactly: ' + result.stdout[-400:])
        return {name: (Path(scratch) / PATCH_PATHS.get(name, name)).read_bytes() for name in old}


def patched_source(path, base, patch, result):
    """Apply a kit patch exactly to a repository file at its pinned base; return the result."""
    data = (ROOT / path).read_bytes()
    require(sha_bytes(data) == base, 'Unexpected repository file: ' + path)
    with tempfile.TemporaryDirectory(prefix='j715-source-') as scratch:
        target = Path(scratch) / path
        target.parent.mkdir(parents=True)
        target.write_bytes(data)
        run = subprocess.run(['patch', '-p1', '--batch', '--forward', '--no-backup-if-mismatch', '-i', str(patch)],
                             cwd=scratch, capture_output=True, text=True)
        require(run.returncode == 0 and 'offset' not in run.stdout and 'fuzz' not in run.stdout,
                'Kit patch did not apply exactly: ' + patch.name)
        data = target.read_bytes()
    require(sha_bytes(data) == result, 'Unexpected patched file: ' + path)
    return data


def repack_installer(source, destination):
    """Copy the update 4 installer, replacing only the four profile files, deterministically."""
    with tarfile.open(source, 'r:gz') as old:
        members = old.getmembers()
        old_data = {}
        for item in members:
            name = item.name.removeprefix('./')
            if name in INSTALLER_FILES:
                require(item.isfile() and name not in old_data, 'Unexpected installer member: ' + name)
                old_data[name] = old.extractfile(item).read()
                require(sha_bytes(old_data[name]) == INSTALLER_FILES[name][0], 'Unexpected installer file: ' + name)
        require(set(old_data) == set(INSTALLER_FILES), 'Missing installer files')
        new_data = patched_installer_files(old_data)
        for name, data in new_data.items():
            require(sha_bytes(data) == INSTALLER_FILES[name][1], 'Unexpected patched installer file: ' + name)
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as raw:
            os.fchmod(raw.fileno(), 0o644)
            with gzip.GzipFile(fileobj=raw, mode='wb', mtime=0, filename='') as compressed, \
                    tarfile.open(fileobj=compressed, mode='w', format=tarfile.PAX_FORMAT) as new:
                for item in members:
                    name = item.name.removeprefix('./')
                    if name in new_data:
                        item = copy.copy(item)
                        item.size = len(new_data[name])
                        new.addfile(item, io.BytesIO(new_data[name]))
                    else:
                        new.addfile(item, old.extractfile(item) if item.isfile() else None)


def debugfs_read(debugfs, image, path):
    result = subprocess.run([debugfs, '-R', 'cat ' + path, str(image)], capture_output=True, check=True)
    return result.stdout


def debugfs_stat(debugfs, image, path):
    return subprocess.run([debugfs, '-R', 'stat ' + path, str(image)], capture_output=True, text=True).stdout


def root_changes(debugfs, image, gpu_module):
    """Check the update 4 root files and return path -> (new bytes, mode)."""
    gpu = gpu_module.read_bytes()
    require(sha_bytes(gpu) == GPU_NEW, 'Unexpected J715 GPU module')
    require(sha_bytes(debugfs_read(debugfs, image, GPU_MODULE)) == GPU_OLD, 'Unexpected root GPU module')
    sums = debugfs_read(debugfs, image, GPU_SUMS)
    require(sha_bytes(sums) == GPU_SUMS_OLD, 'Unexpected root GPU pins')
    old_line = f'{GPU_OLD}  {GPU_MODULE}\n'.encode()
    require(sums.count(old_line) == 1, 'Root GPU pins do not name the module once')
    start = debugfs_read(debugfs, image, GPU_START)
    require(sha_bytes(start) == GPU_START_OLD and start.count(GPU_START_LINE[0]) == 1, 'Unexpected m4-gpu-start')
    placement = debugfs_read(debugfs, image, PLACEMENT)
    require(sha_bytes(placement) == PLACEMENT_OLD, 'Unexpected root session placement')
    new_placement = patched_source(*PLACEMENT_SOURCE)
    return {
        GPU_MODULE: (gpu, 0o644),
        GPU_SUMS: (sums.replace(old_line, f'{GPU_NEW}  {GPU_MODULE}\n'.encode()), 0o664),
        GPU_START: (start.replace(*GPU_START_LINE), 0o755),
        PLACEMENT: (new_placement, 0o644),
    }


def patch_root(image, gpu_module, debugfs='debugfs', e2fsck='e2fsck'):
    """Replace the J715 root files and remove the J713-only start links, then check the filesystem."""
    changes = root_changes(debugfs, image, gpu_module)
    for link in ROOT_LINKS:
        require('Inode:' in debugfs_stat(debugfs, image, link), 'Missing root start link: ' + link)
    for path, pin in ROOT_DROPINS.items():
        require(sha_bytes(debugfs_read(debugfs, image, path)) == pin, 'Unexpected root drop-in: ' + path)
    with tempfile.TemporaryDirectory(prefix='j715-root-') as scratch:
        commands = []
        for index, (path, (data, mode)) in enumerate(sorted(changes.items())):
            staged = Path(scratch) / str(index)
            staged.write_bytes(data)
            directory, base = path.rsplit('/', 1)
            commands += [f'cd {directory}', f'rm {base}', f'write {staged} {base}',
                         f'sif {base} uid 0', f'sif {base} gid 0', f'sif {base} mode 0100{mode:o}']
        commands += [f'rm {path}' for path in (*ROOT_LINKS, *ROOT_DROPINS)]
        script = Path(scratch) / 'commands.debugfs'
        script.write_text('\n'.join(commands) + '\n')
        subprocess.run([debugfs, '-w', '-f', str(script), str(image)], capture_output=True, check=True)
    for path, (data, mode) in changes.items():
        require(debugfs_read(debugfs, image, path) == data, 'Root file not replaced: ' + path)
        stat = debugfs_stat(debugfs, image, path)
        require(re.search(r'Mode:\s+0' + f'{mode:o}', stat) and re.search(r'User:\s+0\s+Group:\s+0\b', stat),
                'Root file ownership or mode: ' + path)
    for path in (*ROOT_LINKS, *ROOT_DROPINS):
        require('Inode:' not in debugfs_stat(debugfs, image, path), 'Root file not removed: ' + path)
    check = subprocess.run([e2fsck, '-fn', str(image)], capture_output=True, text=True)
    require(check.returncode == 0, 'Root filesystem check failed: ' + check.stdout[-400:])
    return {path: sha_bytes(data) for path, (data, _) in changes.items()}


def installer_data(old):
    """The J715 installer definition: only name, package, devicetree and kernel change."""
    entries = old.get('os_list')
    require(isinstance(entries, list) and len(entries) == 1, 'Unexpected installer definition')
    entry = entries[0]
    boot = entry['m4_boot']
    require(entry.get('package') == OLD_PACKAGE and entry.get('supported_fw') == [FIRMWARE] and
            boot['dtb'] == {'file': OLD_DTB[0], 'sha256': OLD_DTB[1]} and
            boot['kernel'] == {'file': OLD_KERNEL[0], 'sha256': OLD_KERNEL[1]} and
            all(boot[part]['sha256'] == KEPT_BOOT[boot[part]['file']] for part in ('initramfs', 'loader')),
            'Unexpected installer definition')
    new = copy.deepcopy(old)
    target = new['os_list'][0]
    target.update(name=OS_NAME, package=PACKAGE)
    target['m4_boot']['dtb'] = {'file': DTB[0], 'sha256': DTB[1]}
    target['m4_boot']['kernel'] = {'file': KERNEL[0], 'sha256': KERNEL[1]}
    return new


def build_zip(old_zip, root_image, dtb, kernel, output):
    with zipfile.ZipFile(old_zip) as old:
        require([info.filename for info in old.infolist()] == OLD_ZIP_NAMES, 'Unexpected OS ZIP layout')
        require(sha_bytes(old.read(OLD_DTB[0])) == OLD_DTB[1], 'Unexpected J713 devicetree')
        require(sha_bytes(old.read(OLD_KERNEL[0])) == OLD_KERNEL[1], 'Unexpected J713 kernel')
        with zipfile.ZipFile(output, 'x', zipfile.ZIP_DEFLATED, allowZip64=True) as new:
            new.write(root_image, 'root.ext4')
            new.writestr(DTB[0], dtb)
            new.writestr('boot/initramfs.cpio.gz', old.read('boot/initramfs.cpio.gz'))
            new.writestr(KERNEL[0], kernel)
            new.writestr('boot/loader.bin', old.read('boot/loader.bin'))
            for name, pin in KEPT_BOOT.items():
                require(sha_bytes(old.read(name)) == pin, 'Unexpected boot part: ' + name)


def sparse_copy(source, target, block=1 << 20):
    """Copy the 16 GiB root image, leaving its zero blocks as holes."""
    zero = bytes(block)
    while chunk := source.read(block):
        if chunk == zero[:len(chunk)]:
            target.seek(len(chunk), os.SEEK_CUR)
        else:
            target.write(chunk)
    target.truncate()


def write(path, data, mode=0o644):
    """Create a new kit file; the kit is a public download, so its files are world-readable."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        os.fchmod(stream.fileno(), mode)
        stream.write(data)


def canonical(value):
    return (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()


def derive(kit, dtb_path, kernel_path, gpu_module, output, revision, debugfs='debugfs', e2fsck='e2fsck'):
    """Build the tester kit in a staging directory and move it to output only once it is complete."""
    require(re.fullmatch(r'[0-9a-f]{40}', revision), 'A full source revision is required')
    require(not output.exists() and not output.is_symlink(), 'Output must not exist')
    require(output.parent.is_dir(), 'Output directory must exist')
    with tempfile.TemporaryDirectory(prefix='.j715-kit-', dir=output.parent) as staging:
        result = assemble(kit, dtb_path, kernel_path, gpu_module, Path(staging) / 'kit', revision, debugfs, e2fsck)
        os.rename(Path(staging) / 'kit', output)
    return result


def assemble(kit, dtb_path, kernel_path, gpu_module, output, revision, debugfs, e2fsck):
    dtb, kernel = dtb_path.read_bytes(), kernel_path.read_bytes()
    require(sha_bytes(dtb) == DTB[1], 'Unexpected J715 devicetree')
    require(sha_bytes(kernel) == KERNEL[1], 'Unexpected J715 kernel')
    names = check_kit(kit)
    old_kit = json.loads((kit / 'KIT.json').read_bytes())
    old_image = json.loads((kit / 'IMAGE.json').read_bytes())
    require(old_kit.get('release_version') == '0.1.0-alpha.1' and old_kit.get('private_only') is False,
            'Unexpected J713 kit metadata')
    require(old_image.get('root', {}).get('sha256') == OLD_ROOT_SHA256 and
            old_image.get('gpu_module', {}).get('sha256') == GPU_OLD, 'Unexpected J713 image metadata')
    output.mkdir()
    (output / 'os').mkdir()
    with tempfile.TemporaryDirectory(prefix='root-', dir=output.parent) as scratch:
        root_image = Path(scratch) / 'root.ext4'
        with zipfile.ZipFile(kit / 'os' / OLD_PACKAGE) as old, old.open('root.ext4') as source, \
                open(root_image, 'wb') as target:
            sparse_copy(source, target)
        require(sha(root_image) == OLD_ROOT_SHA256, 'Unexpected J713 root image')
        root_files = patch_root(root_image, gpu_module, debugfs, e2fsck)
        root_sha = sha(root_image)
        build_zip(kit / 'os' / OLD_PACKAGE, root_image, dtb, kernel, output / 'os' / PACKAGE)
    for name in names:
        if name in REWRITTEN:
            continue
        (output / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(kit / name, output / name)
        shutil.copymode(kit / name, output / name)
    repack_installer(kit / 'installer-macos.tar.gz', output / 'installer-macos.tar.gz')
    for name, mode in REPLACED_DOCS.items():
        write(output / name, (FILES / name).read_bytes(), mode)
    for name, source in SOURCES.items():
        require(not (output / name).exists(), 'Source name already in the kit: ' + name)
        (output / name).parent.mkdir(parents=True, exist_ok=True)
        write(output / name, (ROOT / source).read_bytes())
    write(output / 'source/j715/g16.rs', patched_source(*G16_SOURCE))
    write(output / 'installer_data.json', canonical(installer_data(json.loads((kit / 'installer_data.json').read_bytes()))))
    tester = {'board': 'J715', 'model': 'Mac16,13', 'label': LABEL, 'derived_from_kit_sha256': KIT_SHA256,
              'derivation_revision': revision, 'firmware': FIRMWARE}
    image = copy.deepcopy(old_image)
    image['boot']['dtb'] = {'file': DTB[0], 'sha256': DTB[1]}
    image['boot']['kernel'] = {'file': KERNEL[0], 'sha256': KERNEL[1]}
    image['root'] = dict(image['root'], sha256=root_sha)
    image['qualification'] = QUALIFICATION
    # The kernel changed, so build 18's native startup receipt no longer covers it.
    image['kernel_native_startup_qualified'] = False
    image['gpu_module'] = dict(image['gpu_module'], sha256=GPU_NEW, replaces_sha256=GPU_OLD,
                               issues=image['gpu_module']['issues'] + [142])
    image['j715_test'] = dict(tester, root_from_sha256=OLD_ROOT_SHA256, root_files=root_files,
                              root_links_removed=list(ROOT_LINKS), root_dropins_removed=sorted(ROOT_DROPINS),
                              installer_files={name: pins[1] for name, pins in INSTALLER_FILES.items()},
                              sources=dict(SOURCES, **{'source/j715/g16.rs': G16_SOURCE[0] + ' with ' +
                                                       G16_SOURCE[2].relative_to(ROOT).as_posix()}))
    write(output / 'IMAGE.json', canonical(image))
    files = {}
    for path in sorted(output.rglob('*')):
        name = path.relative_to(output).as_posix()
        if path.is_file() and name not in ('KIT.json', 'SHA256SUMS'):
            files[name] = {'bytes': path.stat().st_size, 'sha256': sha(path)}
    kit_meta = dict(old_kit, files=files, qualification=QUALIFICATION, j715_test=tester)
    write(output / 'KIT.json', canonical(kit_meta))
    listed = {**files, 'KIT.json': {'sha256': sha(output / 'KIT.json')}}
    write(output / 'SHA256SUMS', ''.join(f"{listed[name]['sha256']}  {name}\n" for name in sorted(listed)).encode())
    return {'kit_sha256': sha(output / 'KIT.json'), 'checksums_sha256': sha(output / 'SHA256SUMS'),
            'root_sha256': root_sha, 'os_zip': 'os/' + PACKAGE, 'os_zip_sha256': files['os/' + PACKAGE]['sha256'],
            'installer_sha256': files['installer-macos.tar.gz']['sha256'],
            'dtb_sha256': DTB[1], 'kernel_sha256': KERNEL[1], 'label': LABEL}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--kit', required=True, type=Path, help='The J713 update 4 installer directory')
    parser.add_argument('--dtb', required=True, type=Path, help='The J715 devicetree')
    parser.add_argument('--kernel', required=True, type=Path, help='The J715 kernel Image.gz')
    parser.add_argument('--gpu-module', required=True, type=Path, help='asahi_m4-55831cf4.ko')
    parser.add_argument('--output', required=True, type=Path, help='New directory for the tester kit')
    parser.add_argument('--revision', required=True, help='Full commit of this repository')
    args = parser.parse_args()
    print(json.dumps(derive(args.kit, args.dtb, args.kernel, args.gpu_module, args.output, args.revision),
                     indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
