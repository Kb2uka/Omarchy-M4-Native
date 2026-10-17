#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Install the built channel packages into a sandboxed alpha.1 root with real pacman.

Runs on a hosted runner as an ordinary user with sudo. A throwaway key signs a
local copy of the repository. The sandbox carries an alpha.1-style
linux-m4-omarchy -1, a synthetic boot.bin around the real packaged kernel and
devicetree, and stand-ins for the protected video, Widevine, audio and speaker
files. The setup script must connect it twice without changing those files or
boot.bin, and pacman must refuse a database signed by an untrusted key.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[2]
CHANNEL = json.loads((ROOT / 'packaging/omarchy-m4/channel.json').read_text())
RELEASE = CHANNEL['kernel']['release']
# Packages an Air's 1-5 setup script does not list; omarchy-m4-boot's dependencies bring them.
BROUGHT_BY_BOOT = ('omarchy-m4-gpu', 'omarchy-m4-headphones', 'omarchy-m4-cpu', 'omarchy-m4-evdi')
STUB = '''pkgname=e2e-system
pkgver=1
pkgrel=1
arch=(any)
provides=(python coreutils kmod pacman gstreamer=1.28.7 gst-plugins-base-libs=1.28.7 libpulse)
package() { mkdir -p "$pkgdir/usr/share/e2e-system"; }
'''
OLD_KERNEL = '''pkgname=linux-m4-omarchy
pkgver=7.1.9.m4.20261008
pkgrel=1
arch=(aarch64)
depends=(coreutils kmod)
provides=(linux=7.1.9 linux-aarch64=7.1.9)
conflicts=(linux-aarch64)
package() {
  install -Dm644 /dev/null "$pkgdir/usr/lib/modules/%s/modules.order"
  printf 'alpha.1 package kernel' | install -Dm644 /dev/stdin "$pkgdir/usr/lib/omarchy-m4/Image.gz"
}
''' % RELEASE
PROTECTED = {
    'opt/m4-video/20261011/mesa/lib/libgallium-26.3.0-devel.so': b'decoder mesa',
    'opt/m4-gpu/asahi_m4.ko': b'gpu module',
    'opt/m4-mesa/lib/libEGL.so.1.0.0': b'gpu mesa',
    'usr/local/bin/brave': b'#!/bin/sh\n# Widevine-aware browser wrapper\n',
    'etc/skel/.config/omarchy/pending/widevine-Brave-Browser.json': b'{}\n',
    f'usr/lib/omarchy/j713-audio/{RELEASE}/modules/snd-soc-tas2764.ko': b'speaker amp',
    'usr/share/asahi-audio/j713/graph.json': b'{}\n',
    'usr/share/speakersafetyd/apple/j713.conf': b'[speakers]\n',
}


def require(condition, message):
    if not condition:
        raise SystemExit('m4-channel-e2e: ' + message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def run(*command, check=True, **options):
    print('+ ' + ' '.join(map(str, command)), flush=True)
    return subprocess.run([str(part) for part in command], check=check, **options)


def sudo(*command, **options):
    return run('sudo', '--preserve-env=GNUPGHOME', *command, **options)


def member(package, name):
    data = subprocess.run(['bsdtar', '-xOf', str(package), name], check=True, capture_output=True).stdout
    require(data, f'{name} is missing from {package.name}')
    return data


def makepkg(directory, recipe, output):
    directory.mkdir()
    (directory / 'PKGBUILD').write_text(recipe)
    config = directory / 'makepkg.conf'
    config.write_text(runpy.run_path(str(ROOT / 'tools/build-m4-packages.py'))['MAKEPKG_CONF'])
    run('makepkg', '--config', config, '--nodeps', '--noconfirm', cwd=directory,
        env=dict(os.environ, PKGDEST=str(output)))


def new_key(home, name):
    home.mkdir(mode=0o700)
    env = dict(os.environ, GNUPGHOME=str(home))
    run('gpg', '--batch', '--pinentry-mode', 'loopback', '--passphrase', '', '--quick-generate-key',
        name, 'ed25519', 'sign', 'never', env=env)
    listing = run('gpg', '--with-colons', '--list-secret-keys', env=env, capture_output=True, text=True).stdout
    fingerprint = next(line.split(':')[9] for line in listing.splitlines() if line.startswith('fpr:'))
    armored = run('gpg', '--armor', '--export', fingerprint, env=env, capture_output=True, text=True).stdout
    return env, fingerprint, armored


def signed_repository(directory, packages, env, fingerprint):
    directory.mkdir()
    for package in packages:
        shutil.copyfile(package, directory / package.name)
        run('gpg', '--batch', '--yes', '--local-user', fingerprint, '--detach-sign', '--no-armor',
            directory / package.name, env=env)
    run('repo-add', '--sign', '--key', fingerprint, directory / 'omarchy-m4.db.tar.gz',
        *sorted(directory.glob('*.pkg.tar.zst')), env=env)


def boot_bin(kernel, dtb):
    loader = b'm1n1 sandbox loader\0' + bytes(44) + b'STACKBOT'
    args = b'chosen.bootargs=dockchannel_hid.m4_power_v2=1 dockchannel_hid.m4_afe_action=1\n'
    initramfs = b'\x1f\x8bsandbox base cpio' + b'070701 sandbox owner firmware'
    return loader + args + kernel + dtb + b'm1n1_initramfs' + struct.pack('<I', len(initramfs)) + initramfs


def sandbox(root, old, stub):
    for name in ('var/lib/pacman', 'var/cache/pacman/pkg', 'var/log', 'etc/pacman.d/hooks',
                 'srv/core', 'boot/efi/omarchy', 'sys/firmware/devicetree/base'):
        (root / name).mkdir(parents=True, exist_ok=True)
    with tarfile.open(root / 'srv/core/core.db', 'w:gz'):
        pass
    (root / 'etc/pacman.d/mirrorlist').write_text(f'Server = file://{root}/srv/$repo\n')
    (root / 'etc/pacman.conf').write_text(
        '[options]\nHoldPkg = pacman glibc\nArchitecture = aarch64\n'
        'SigLevel = Required DatabaseOptional\nLocalFileSigLevel = Optional\n\n'
        f'[core]\nInclude = {root}/etc/pacman.d/mirrorlist\n')
    (root / 'sys/firmware/devicetree/base/compatible').write_bytes(b'apple,j713\0apple,t8132\0apple,arm-platform\0')
    for name, data in PROTECTED.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(data)
    kernel_sha = CHANNEL['kernel']['image_sha256']
    for name, data in (('etc/omarchy/j713-video-manifest.json',
                        {'boot_kernel_sha256': kernel_sha, 'kernel_release': RELEASE}),
                       ('etc/omarchy/j713-audio-manifest.json', {'kernel_release': RELEASE})):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(json.dumps(data) + '\n')
    sudo('chown', '-R', 'root:root', root)
    pacman = ['pacman', '--root', root, '--dbpath', root / 'var/lib/pacman', '--config', root / 'etc/pacman.conf',
              '--cachedir', root / 'var/cache/pacman/pkg', '--gpgdir', root / 'etc/pacman.d/gnupg',
              '--hookdir', root / 'etc/pacman.d/hooks', '--logfile', root / 'var/log/pacman.log']
    sudo(*pacman, '-Sy')
    sudo(*pacman, '-U', '--noconfirm', '--noscriptlet', stub, old)
    sudo('pacman-key', '--gpgdir', root / 'etc/pacman.d/gnupg', '--init')
    return pacman


def snapshot(root):
    names = [*PROTECTED, 'boot/efi/omarchy/boot.bin', 'etc/omarchy/j713-video-manifest.json',
             'etc/omarchy/j713-audio-manifest.json']
    return {name: sha(Path(root / name).read_bytes()) for name in names}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('packages', type=Path, help='built channel packages')
    built = parser.parse_args().packages.resolve()
    packages = sorted(built.glob('*.pkg.tar.zst'))
    by_name = {p.name.rsplit('-', 3)[0]: p for p in packages}
    require(set(by_name) == set(CHANNEL['packages']), 'Unexpected package set')
    with tempfile.TemporaryDirectory(prefix='m4-e2e-') as scratch:
        scratch = Path(scratch)
        try:
            run('chmod', '755', scratch)
            local = scratch / 'local'
            local.mkdir()
            makepkg(scratch / 'stub', STUB, local)
            makepkg(scratch / 'old-kernel', OLD_KERNEL, local)
            env, fingerprint, armored = new_key(scratch / 'gnupg', 'M4 channel e2e <e2e@invalid>')
            signed_repository(scratch / 'repo', packages, env, fingerprint)
            root = scratch / 'root'
            root.mkdir()
            kernel = member(by_name['linux-m4-omarchy'], 'usr/lib/omarchy-m4/Image.gz')
            dtb = member(by_name['omarchy-m4-boot'], 'usr/lib/omarchy-m4/j713.dtb')
            (root / 'boot/efi/omarchy').mkdir(parents=True)
            (root / 'boot/efi/omarchy/boot.bin').write_bytes(boot_bin(kernel, dtb))
            old, = local.glob('linux-m4-omarchy-*.pkg.tar.zst')
            stub, = local.glob('e2e-system-*.pkg.tar.zst')
            pacman = sandbox(root, old, stub)
            before = snapshot(root)
            key = scratch / 'key.asc'
            key.write_text(armored)
            setup = ['python3', ROOT / 'tools/m4-channel-setup.py', '--root', root,
                     '--server', f'file://{scratch}/repo', '--key-file', key, '--fingerprint', fingerprint]
            plan = json.loads(sudo(*setup, capture_output=True, text=True).stdout)
            require(plan['applied'] is False and plan['pacman_conf_changes'], 'Inspection did not plan the change')
            require(snapshot(root) == before, 'Inspection changed files')
            # The first run is the setup script an Air already has from omarchy-m4-boot 1-5,
            # which lists none of omarchy-m4-gpu, omarchy-m4-headphones, omarchy-m4-cpu and
            # omarchy-m4-evdi; the dependencies of boot 1-6 (GPU), 1-7 (headphone jack), 1-10 (CPU
            # idle and boost guard) and 1-13 (USB displays) must bring them.
            source = (ROOT / 'tools/m4-channel-setup.py').read_text()
            listed = source[source.index('PACKAGES = ('):source.index(')', source.index('PACKAGES = (')) + 1]
            older = scratch / 'm4-channel-setup-1-5.py'
            older.write_text(source.replace(listed, 'PACKAGES = (' + ', '.join(
                repr(p) for p in CHANNEL['packages'] if p not in BROUGHT_BY_BOOT) + ')'))
            require(not any(p in older.read_text().split('PACKAGES = (')[1].split(')')[0] for p in BROUGHT_BY_BOOT),
                    'The 1-5 setup script stand-in still lists a package the boot package brings')
            results = []
            for attempt in range(2):
                command = [setup[0], older, *setup[2:]] if attempt == 0 else setup
                result = sudo(*command, '--apply', capture_output=True, text=True, check=False)
                print(result.stdout + result.stderr)
                require(result.returncode == 0, f'Setup attempt {attempt + 1} failed')
                results.append(json.loads(result.stdout))
                if attempt == 0:
                    for brought in BROUGHT_BY_BOOT:
                        require(brought not in results[0]['install'] and
                                run(*pacman, '-Q', brought, check=False).returncode == 0,
                                'The first run from the 1-5 setup script did not install ' + brought)
                conf = (root / 'etc/pacman.conf').read_text()
                require(conf.count('Include = ' + str(root / 'etc/pacman.d/omarchy-m4.conf')) == 1,
                        'The channel include is missing or duplicated')
            require(all(r['status'].startswith('connected') and r['boot'] == 'current' and
                        r['restart_needed'] is False for r in results), 'Setup did not report a current boot.bin')
            repos = run('pacman-conf', '--config', root / 'etc/pacman.conf', '--repo-list',
                        capture_output=True, text=True).stdout.split()
            require(repos == ['omarchy-m4', 'core'], 'The channel is not searched first: ' + ' '.join(repos))
            installed = run(*pacman, '-Q', capture_output=True, text=True).stdout
            for package in packages:
                name, version, rel, _ = package.name.removesuffix('.pkg.tar.zst').rsplit('-', 3)
                require(f'{name} {version}-{rel}' in installed, f'{name} {version}-{rel} is not installed')
            require(sha((root / 'usr/lib/omarchy-m4/Image.gz').read_bytes()) == CHANNEL['kernel']['image_sha256'],
                    'The installed kernel image is not the pinned kernel')
            require(len(list((root / 'usr/lib/modules' / RELEASE).rglob('*.ko'))) > 1000, 'Modules are missing')
            require(snapshot(root) == before, 'A protected file or boot.bin changed')
            for path, digest in CHANNEL['gst_audio'].items():
                require(sha((root / path).read_bytes()) == digest, 'GStreamer sink missing or wrong: ' + path)
            require((root / 'usr/lib/systemd/system/speakersafetyd-j713.service.d/50-realtime.conf').is_file(),
                    'The real-time speaker setting is missing')
            for path, digest in CHANNEL['speaker_module'].items():
                require(sha((root / path).read_bytes()) == digest, 'Staged speaker module missing or wrong: ' + path)
            require((root / 'usr/lib/systemd/system/m4-j713-audio-register.service.d/20-m4-speaker-module.conf').is_file()
                    and os.access(root / 'usr/bin/m4-speaker-module', os.X_OK), 'The speaker module step is missing')
            for path, digest in CHANNEL['gpu_module'].items():
                require(sha((root / path).read_bytes()) == digest, 'Staged GPU module missing or wrong: ' + path)
            require((root / 'usr/lib/systemd/system/m4-gpu.service.d/20-m4-gpu-module.conf').is_file()
                    and os.access(root / 'usr/bin/m4-gpu-module', os.X_OK), 'The GPU module step is missing')
            for path, digest in CHANNEL['headphone_modules'].items():
                require(sha((root / path).read_bytes()) == digest, 'Staged headphone module missing or wrong: ' + path)
            require((root / 'usr/lib/systemd/system/m4-j713-audio-register.service.d/30-m4-headphones.conf').is_file()
                    and os.access(root / 'usr/bin/m4-headphones', os.X_OK), 'The headphone step is missing')
            for path, digest in {**CHANNEL['cpu_idle_module'], **CHANNEL['cpu_guard_module']}.items():
                require(sha((root / path).read_bytes()) == digest, 'Staged CPU module missing or wrong: ' + path)
            require((root / 'usr/lib/systemd/system/m4-cpu-idle.service').is_file()
                    and os.access(root / 'usr/bin/m4-cpu-idle', os.X_OK), 'The CPU idle step is missing')
            for path, digest in {**CHANNEL['evdi_module'], **CHANNEL['evdi_library']}.items():
                require(sha((root / path).read_bytes()) == digest, 'USB display (evdi) file missing or wrong: ' + path)
            require(run(*pacman, '-T', 'evdi<1.16', check=False).returncode == 0,
                    'The evdi package does not satisfy the displaylink package\'s dependency')
            for path, digest in CHANNEL['notch_loader'].items():
                require(sha((root / path).read_bytes()) == digest, 'Staged notch stage 2 missing or wrong: ' + path)
            require((root / 'usr/lib/systemd/user/omarchy-m4-notch.service').is_file()
                    and os.access(root / 'usr/bin/m4-notch', os.X_OK), 'The notch switch is missing')
            require(os.access(root / 'usr/bin/omarchy-m4-update', os.X_OK) and
                    (root / 'usr/lib/omarchy-m4/m4-channel-setup.py').is_file(), 'omarchy-m4-update is missing')
            require((root / 'usr/lib/systemd/user/omarchy-m4-update-check.timer').is_file() and
                    os.access(root / 'usr/bin/omarchy-m4-update-check', os.X_OK), 'The update notifier is missing')
            require((root / 'etc/pacman.conf.pre-omarchy-m4').is_file(), 'No pacman.conf backup')
            # A database signed by a key the machine does not trust must be refused.
            env2, other, _ = new_key(scratch / 'gnupg-untrusted', 'Untrusted <untrusted@invalid>')
            signed_repository(scratch / 'forged', packages, env2, other)
            forged = scratch / 'forged.conf'
            forged.write_text('[options]\nArchitecture = aarch64\nSigLevel = Required DatabaseRequired\n'
                              f'[omarchy-m4]\nServer = file://{scratch}/forged\n')
            (scratch / 'forged-db').mkdir()
            refused = sudo('pacman', '--root', root, '--dbpath', scratch / 'forged-db', '--noconfirm',
                           '--gpgdir', root / 'etc/pacman.d/gnupg', '--config', forged, '-Sy', check=False)
            require(refused.returncode != 0, 'pacman accepted a database signed by an untrusted key')
            print('m4-channel-e2e: passed')
        finally:
            # pacman ran as root in these two trees; remove them before the temporary directory goes.
            subprocess.run(['sudo', 'rm', '-rf', str(scratch / 'root'), str(scratch / 'forged-db')], check=False)


if __name__ == '__main__':
    sys.exit(main())
