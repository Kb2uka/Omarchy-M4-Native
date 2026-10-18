#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Derive the M4 Mac mini (J773g) tester kit from the J713 installer update 4 kit.

Runs on a Linux build host with debugfs and e2fsck (docs/j773g-test-kit.md). The
J713 kit is checked against its pins. The output kit changes only:

- the boot devicetree (the J773g one from tools/build-j773g-dtb.py);
- the installer definition: OS name, OS package, devicetree, and the 26.6.2
  firmware of the mini's installer profile;
- Install.command, START-HERE.md, KNOWN-LIMITATIONS.md and UPDATES.md (packaging/j773g-test);
- in the root image, the two SDDM files that tie the desktop to the J713 GPU,
  and the start links of J713-only services that refuse on a mini anyway;
- KIT.json, IMAGE.json and SHA256SUMS, regenerated for those changes.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FILES = ROOT / 'packaging/j773g-test'

# Installer update 4 (v0.1.0-alpha.1-update.4), the J713 kit this kit derives from.
KIT_SHA256 = '9f216fd08695049b3c2affdabe459477795c4a0a765526d7d2dcfe706a2ff345'
SUMS_SHA256 = '454569f6092e8413e15b438ded17c99e9a4e7b20f87787ebb8193badd95b6428'
OLD_PACKAGE = 'omarchy-j713-0.1.0-alpha.1.zip'
OLD_ROOT_SHA256 = '995b5c2a3a331007056a529e51bf8e805c6c3827abf624efb8e6b8f16788665a'
OLD_DTB = ('boot/portable.dtb', 'e54c77a760a5b696486f9d80c7c83ca4295d641113bb9e54d13e259863bf9b07')
KEPT_BOOT = {
    'boot/initramfs.cpio.gz': '0b4e8f8ac58ec7afda6de5810b1f96599f582297e246cbe7ac4dfdfad8e0af28',
    'boot/Image.gz': 'c3704cfd2afb7760e73f0b9939a68864a30bd916fcd478bd960e538373123dc9',
    'boot/loader.bin': '3a3f7a64c65fc366d5ac9aece3e1bee64119be012aa1ec5b15d2743c6f91fce4',
}
OLD_ZIP_NAMES = ['root.ext4', OLD_DTB[0], *KEPT_BOOT]

# The J773g devicetree (docs/j773g-boot-dtb.md).
DTB = ('boot/j773g.dtb', 'c2a4f0cc0e28841bd5c3a1beb1342a2b6cfebfa670cf53db10b6bfec2c92332b')
LABEL = 'j773g-test.1'
PACKAGE = 'omarchy-j773g-0.1.0-alpha.1-test.1.zip'
OS_NAME = 'Omarchy M4 Native 0.1.0-alpha.1 (J773g tester)'
FIRMWARE = '26.6.2'  # the installer's j773gap profile

# Root files the mini build replaces: path -> (current SHA-256, replacement under packaging/j773g-test).
ROOT_FILES = {
    '/etc/sddm.conf.d/99-m4.conf':
        ('69a1b2d7c60f057936ddaff8df3fa6fe32d084048e2fdf80e7215d07dbcab58a', 'sddm-99-m4.conf'),
    '/etc/systemd/system/sddm.service.d/90-m4-staging.conf':
        ('535bb8c48bd850be07616560215d1d36912878b982b92ca597311a0ccb6ebb81', 'sddm-90-m4-staging.conf'),
}
# Start links of services whose board gates refuse a J773g, and the update timer that
# would retry a refused channel setup forever.
ROOT_LINKS = (
    '/etc/systemd/system/multi-user.target.wants/m4-j713-audio-register.service',
    '/etc/systemd/system/multi-user.target.wants/speakersafetyd-j713.service',
    '/etc/systemd/system/multi-user.target.wants/m4-video.service',
    '/etc/systemd/system/multi-user.target.wants/j713-owner-assets.service',
    '/etc/systemd/system/timers.target.wants/m4-channel-connect.timer',
)
REPLACED_DOCS = {'Install.command': 0o755, 'START-HERE.md': 0o644, 'KNOWN-LIMITATIONS.md': 0o644, 'UPDATES.md': 0o644}
REWRITTEN = {'KIT.json', 'IMAGE.json', 'SHA256SUMS', 'installer_data.json', 'os/' + OLD_PACKAGE, *REPLACED_DOCS}
QUALIFICATION = ('J773g tester build derived from the J713 installer update 4 kit. Nobody has booted it on '
                 'a Mac mini; it is not qualified for any Mac.')


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
        names.append(name)
    on_disk = {p.relative_to(kit).as_posix() for p in kit.rglob('*') if p.is_file()}
    require(on_disk == set(names) | {'SHA256SUMS'}, 'Kit has unlisted or missing files')
    return names


def debugfs_read(debugfs, image, path):
    result = subprocess.run([debugfs, '-R', 'cat ' + path, str(image)], capture_output=True, check=True)
    return result.stdout


def debugfs_exists(debugfs, image, path):
    result = subprocess.run([debugfs, '-R', 'stat ' + path, str(image)], capture_output=True, text=True)
    return 'Inode:' in result.stdout


def patch_root(image, debugfs='debugfs', e2fsck='e2fsck'):
    """Replace the mini's root files and remove the J713-only start links, then check the filesystem."""
    for path, (pin, _) in ROOT_FILES.items():
        require(sha_bytes(debugfs_read(debugfs, image, path)) == pin, 'Unexpected root file: ' + path)
    for link in ROOT_LINKS:
        require(debugfs_exists(debugfs, image, link), 'Missing root start link: ' + link)
    commands = []
    for path, (_, name) in ROOT_FILES.items():
        directory, base = path.rsplit('/', 1)
        commands += [f'cd {directory}', f'rm {base}', f'write {FILES / name} {base}',
                     f'sif {base} uid 0', f'sif {base} gid 0', f'sif {base} mode 0100644']
    commands += [f'rm {link}' for link in ROOT_LINKS]
    with tempfile.NamedTemporaryFile('w', suffix='.debugfs') as script:
        script.write('\n'.join(commands) + '\n')
        script.flush()
        subprocess.run([debugfs, '-w', '-f', script.name, str(image)], capture_output=True, check=True)
    for path, (_, name) in ROOT_FILES.items():
        require(debugfs_read(debugfs, image, path) == (FILES / name).read_bytes(), 'Root file not replaced: ' + path)
        stat = subprocess.run([debugfs, '-R', 'stat ' + path, str(image)], capture_output=True, text=True).stdout
        require(re.search(r'Mode:\s+0644', stat) and re.search(r'User:\s+0\s+Group:\s+0\b', stat),
                'Root file ownership or mode: ' + path)
    for link in ROOT_LINKS:
        require(not debugfs_exists(debugfs, image, link), 'Root start link not removed: ' + link)
    check = subprocess.run([e2fsck, '-fn', str(image)], capture_output=True, text=True)
    require(check.returncode == 0, 'Root filesystem check failed: ' + check.stdout[-400:])


def installer_data(old):
    """The mini's installer definition: only name, package, devicetree and firmware change."""
    entries = old.get('os_list')
    require(isinstance(entries, list) and len(entries) == 1, 'Unexpected installer definition')
    entry = entries[0]
    require(entry.get('package') == OLD_PACKAGE and entry.get('supported_fw') == ['26.5.1'] and
            entry['m4_boot']['dtb'] == {'file': OLD_DTB[0], 'sha256': OLD_DTB[1]} and
            all(entry['m4_boot'][part]['sha256'] == KEPT_BOOT[entry['m4_boot'][part]['file']]
                for part in ('initramfs', 'kernel', 'loader')), 'Unexpected installer definition')
    new = copy.deepcopy(old)
    target = new['os_list'][0]
    target.update(name=OS_NAME, package=PACKAGE, supported_fw=[FIRMWARE])
    target['m4_boot']['dtb'] = {'file': DTB[0], 'sha256': DTB[1]}
    return new


def build_zip(old_zip, root_image, dtb, output):
    with zipfile.ZipFile(old_zip) as old:
        require([info.filename for info in old.infolist()] == OLD_ZIP_NAMES, 'Unexpected OS ZIP layout')
        require(sha_bytes(old.read(OLD_DTB[0])) == OLD_DTB[1], 'Unexpected J713 devicetree')
        with zipfile.ZipFile(output, 'x', zipfile.ZIP_DEFLATED, allowZip64=True) as new:
            new.write(root_image, 'root.ext4')
            new.writestr(DTB[0], dtb)
            for name, pin in KEPT_BOOT.items():
                data = old.read(name)
                require(sha_bytes(data) == pin, 'Unexpected boot part: ' + name)
                new.writestr(name, data)


def write(path, data, mode=0o644):
    """Create a new kit file; the kit is a public download, so its files are world-readable."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        os.fchmod(stream.fileno(), mode)
        stream.write(data)


def canonical(value):
    return (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()


def derive(kit, dtb_path, output, revision, debugfs='debugfs', e2fsck='e2fsck'):
    """Build the tester kit in a staging directory and move it to output only once it is complete."""
    require(re.fullmatch(r'[0-9a-f]{40}', revision), 'A full source revision is required')
    require(not output.exists() and not output.is_symlink(), 'Output must not exist')
    require(output.parent.is_dir(), 'Output directory must exist')
    with tempfile.TemporaryDirectory(prefix='.j773g-kit-', dir=output.parent) as staging:
        result = assemble(kit, dtb_path, Path(staging) / 'kit', revision, debugfs, e2fsck)
        os.rename(Path(staging) / 'kit', output)
    return result


def assemble(kit, dtb_path, output, revision, debugfs, e2fsck):
    dtb = dtb_path.read_bytes()
    require(sha_bytes(dtb) == DTB[1], 'Unexpected J773g devicetree')
    names = check_kit(kit)
    old_kit = json.loads((kit / 'KIT.json').read_bytes())
    old_image = json.loads((kit / 'IMAGE.json').read_bytes())
    require(old_kit.get('release_version') == '0.1.0-alpha.1' and old_kit.get('private_only') is False,
            'Unexpected J713 kit metadata')
    require(old_image.get('root', {}).get('sha256') == OLD_ROOT_SHA256, 'Unexpected J713 image metadata')
    output.mkdir()
    (output / 'os').mkdir()
    with tempfile.TemporaryDirectory(prefix='root-', dir=output.parent) as scratch:
        root_image = Path(scratch) / 'root.ext4'
        with zipfile.ZipFile(kit / 'os' / OLD_PACKAGE) as old, old.open('root.ext4') as source, \
                open(root_image, 'wb') as target:
            shutil.copyfileobj(source, target, 16 << 20)
        require(sha(root_image) == OLD_ROOT_SHA256, 'Unexpected J713 root image')
        patch_root(root_image, debugfs, e2fsck)
        root_sha = sha(root_image)
        build_zip(kit / 'os' / OLD_PACKAGE, root_image, dtb, output / 'os' / PACKAGE)
    for name in names:
        if name in REWRITTEN:
            continue
        (output / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(kit / name, output / name)
        shutil.copymode(kit / name, output / name)
    for name, mode in REPLACED_DOCS.items():
        write(output / name, (FILES / name).read_bytes(), mode)
    write(output / 'installer_data.json', canonical(installer_data(json.loads((kit / 'installer_data.json').read_bytes()))))
    tester = {'board': 'J773g', 'model': 'Mac16,10', 'label': LABEL, 'derived_from_kit_sha256': KIT_SHA256,
              'derivation_revision': revision, 'firmware': FIRMWARE}
    image = copy.deepcopy(old_image)
    image['boot']['dtb'] = {'file': DTB[0], 'sha256': DTB[1]}
    image['root'] = dict(image['root'], sha256=root_sha)
    image['qualification'] = QUALIFICATION
    image['j773g_test'] = dict(tester, root_from_sha256=OLD_ROOT_SHA256,
                               root_files_replaced=sorted(ROOT_FILES), root_links_removed=list(ROOT_LINKS))
    write(output / 'IMAGE.json', canonical(image))
    files = {}
    for path in sorted(output.rglob('*')):
        name = path.relative_to(output).as_posix()
        if path.is_file() and name not in ('KIT.json', 'SHA256SUMS'):
            files[name] = {'bytes': path.stat().st_size, 'sha256': sha(path)}
    kit_meta = dict(old_kit, files=files, qualification=QUALIFICATION, j773g_test=tester)
    write(output / 'KIT.json', canonical(kit_meta))
    listed = {**files, 'KIT.json': {'sha256': sha(output / 'KIT.json')}}
    write(output / 'SHA256SUMS', ''.join(f"{listed[name]['sha256']}  {name}\n" for name in sorted(listed)).encode())
    return {'kit_sha256': sha(output / 'KIT.json'), 'checksums_sha256': sha(output / 'SHA256SUMS'),
            'root_sha256': root_sha, 'os_zip': 'os/' + PACKAGE, 'os_zip_sha256': files['os/' + PACKAGE]['sha256'],
            'dtb_sha256': DTB[1], 'label': LABEL}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--kit', required=True, type=Path, help='The J713 update 4 installer directory')
    parser.add_argument('--dtb', required=True, type=Path, help='The J773g devicetree')
    parser.add_argument('--output', required=True, type=Path, help='New directory for the tester kit')
    parser.add_argument('--revision', required=True, help='Full commit of this repository')
    args = parser.parse_args()
    print(json.dumps(derive(args.kit, args.dtb, args.output, args.revision), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
