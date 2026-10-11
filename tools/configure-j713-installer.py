#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 studioxradio aka kb2uka
"""Apply the pinned experimental components only to an offline, owner-free loop image."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'tools'))
from m4_audio_package import image_path, require

PREPARED_PROVISION_SHA = '1761b562ab066d2a6eafb0fed2640c3388b146113289940d0e3f1c4cbfa520b4'
ORDINARY_BRAVE_DESKTOP_SHA = 'c07276b69c7304981525ecb022f92daf7ae125a4fb05ac3442157b50826e257a'


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, HERE / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify_kit(pin):
    path = HERE / 'manifest.json'
    require(sha(path) == pin, 'Kit manifest pin mismatch')
    manifest = json.loads(path.read_text())
    expected = {'manifest.json'} | set(manifest['files']) | set(manifest['links'])
    actual = {p.relative_to(HERE).as_posix() for p in HERE.rglob('*')
              if not p.is_dir() or p.is_symlink()}
    require(actual == expected, 'Unexpected kit members')
    for name, entry in manifest['files'].items():
        p = image_path(HERE, name)
        require(p.is_file() and stat.S_IMODE(p.stat().st_mode) == entry['mode'] and
                p.stat().st_size == entry['bytes'] and sha(p) == entry['sha256'],
                'Kit input changed: ' + name)
    for name, target in manifest['links'].items():
        p = HERE / name
        require(p.is_symlink() and os.readlink(p) == target, 'Kit link changed: ' + name)
    require(manifest['private_only'] and not manifest['fresh_install_qualified'], 'Unexpected release state')
    return manifest


def owner_free(root):
    require(root.is_absolute() and root == root.resolve() and root != Path('/'), 'Offline image required')
    for name in ('etc/m4-root-id', 'var/lib/omarchy/provisioning/pending'):
        require(image_path(root, name).is_file(), 'Missing pending-image marker: ' + name)
    for name in ('home', 'root', 'etc/NetworkManager/system-connections', 'etc/pacman.d/gnupg'):
        p = image_path(root, name)
        require(p.is_dir() and not list(p.iterdir()), 'Private state or missing sanitation directory: ' + name)
    require(image_path(root, 'etc/machine-id').read_bytes() == b'', 'Machine identity remains')
    require(not image_path(root, 'etc/m4-install-complete').exists(), 'Installed-owner marker remains')
    require(not list(image_path(root, 'etc/ssh').glob('ssh_host_*')), 'SSH host keys remain')
    for row in image_path(root, 'etc/passwd').read_text().splitlines():
        require(not 1000 <= int(row.split(':')[2]) < 65534, 'Owner account remains')
    for name in ('etc/shadow', 'etc/gshadow'):
        require(all(row.split(':')[1] == '!' for row in image_path(root, name).read_text().splitlines()),
                'Password state is not locked: ' + name)


def loop_image(root, backing):
    require(os.geteuid() == 0, 'Root required for mounted-image action')
    mount = json.loads(subprocess.check_output(['findmnt', '--json', '--target', str(root),
                                               '--output', 'TARGET,SOURCE,FSTYPE'], text=True))['filesystems']
    require(len(mount) == 1 and mount[0]['target'] == str(root) and mount[0]['fstype'] == 'ext4',
            'Explicit mounted ext4 image required')
    source = mount[0]['source']
    require(source.startswith('/dev/loop') and source[9:].isdigit(), 'A disposable loop image is required')
    devices = json.loads(subprocess.check_output(['losetup', '--json', '--output', 'NAME,BACK-FILE', source],
                                                 text=True))['loopdevices']
    require(len(devices) == 1 and Path(devices[0]['back-file']).resolve() == backing.resolve() and
            backing.is_file() and not backing.is_symlink(), 'Loop backing image mismatch')


def acceptable_missing(function, prefix):
    try:
        return function()
    except ValueError as error:
        require(str(error).startswith(prefix), str(error))
        return {'pending': str(error)}


def browser_plan(root):
    path = image_path(root, 'usr/bin/omarchy-provision-user')
    raw = path.read_bytes()
    old = b'env -u BROWSER xdg-settings set default-web-browser brave-m4-hardware.desktop\n'
    new = b'env -u BROWSER xdg-settings set default-web-browser brave-browser.desktop\n'
    require(raw.count(old) + raw.count(new) == 1, 'Unexpected browser finalization source')
    original = raw.replace(new, old)
    require(hashlib.sha256(original).hexdigest() == PREPARED_PROVISION_SHA,
            'Unknown owner finalization source')
    require(stat.S_IMODE(path.stat().st_mode) == 0o755, 'Unexpected finalizer mode')
    desktop = image_path(root, 'usr/share/applications/brave-browser.desktop')
    require(sha(desktop) == ORDINARY_BRAVE_DESKTOP_SHA and
            stat.S_IMODE(desktop.stat().st_mode) == 0o644,
            'Ordinary Brave desktop is unavailable to the Omarchy launcher')
    return path, original.replace(old, new)


def browser_configure(root, apply=False):
    path, expected = browser_plan(root)
    if apply and path.read_bytes() != expected:
        path.write_bytes(expected)
    require(path.read_bytes() == expected, 'Browser finalization differs')


def configure(root, firmware, apply=False):
    owner_free(root)
    desktop = load('desktop', 'tools/configure-m4-desktop.py')
    audio = load('audio', 'tools/configure-m4-audio.py')
    video = load('video', 'tools/m4_video_package.py')
    video.HERE = HERE / 'source/video-package'
    mpv = load('mpv_install', 'source/mpv/install.py')
    audio_package, video_package = HERE / 'packages/audio', HERE / 'packages/video'
    ap, vp = sha(audio_package / 'manifest.json'), sha(video_package / 'manifest.json')
    desktop.planned_files(root)
    browser_plan(root)
    require(image_path(root, 'usr/share/applications/chromium.desktop').read_bytes() ==
            (HERE / 'inputs/chromium.desktop').read_bytes(), 'Unknown shipped Chromium desktop')
    audio_args = dict(root=root, package=audio_package, board='apple,j713',
                      kernel_release='7.1.9-m4-gpu-backport-20261008',
                      base_dtb=HERE / 'inputs/audio-base.dtb', package_sha256=ap,
                      previous_package_sha256=json.loads((HERE / 'manifest.json').read_text())
                      .get('previous_audio_package_sha256'))
    video_args = dict(root=root, package=video_package, pin=vp, boot_dtb=HERE / 'inputs/video-base.dtb',
                      boot_kernel=HERE / 'inputs/Image.gz', firmware=firmware)
    # Run each helper's full validation without writes; only its exact missing-install
    # result is allowed. Every other exception refuses the whole offline operation.
    acceptable_missing(lambda: audio.configure(**audio_args), 'Audio image configuration differs:')
    acceptable_missing(lambda: video.configure(**video_args), 'Video image differs:')
    acceptable_missing(lambda: mpv.operate('check', root), 'Integration is not installed')
    if not apply:
        desktop.configure(root, False)
        audio.configure(**audio_args)
        video.configure(**video_args)
        mpv.operate('check', root)
        browser_configure(root)
        return {'status': 'checked'}
    desktop.configure(root, True)
    audio.configure(**audio_args, apply=True)
    video.configure(**video_args, apply=True)
    mpv.operate('apply', root)
    browser_configure(root, apply=True)
    return {'status': 'applied', 'scope': 'Offline image only; no device, service, browser, boot or disk-partition action'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['verify', 'apply', 'check'])
    p.add_argument('--manifest-sha256', required=True)
    p.add_argument('--root', type=Path)
    p.add_argument('--backing-image', type=Path)
    p.add_argument('--avd-firmware', type=Path)
    a = p.parse_args()
    verify_kit(a.manifest_sha256)
    if a.action == 'verify':
        print(json.dumps({'status': 'verified', 'private_only': True, 'fresh_install_qualified': False}))
        return
    require(a.root is not None and a.backing_image is not None and a.avd_firmware is not None,
            'Image root/backing and external AVD firmware are required')
    loop_image(a.root, a.backing_image)
    print(json.dumps(configure(a.root, a.avd_firmware, a.action == 'apply'), sort_keys=True))


if __name__ == '__main__':
    main()
