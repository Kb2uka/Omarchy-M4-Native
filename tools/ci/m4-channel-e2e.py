#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Install the built channel packages into a sandboxed alpha.1 root with real pacman.

Runs on a hosted runner as an ordinary user with sudo. A throwaway key signs a
local copy of the repository. The sandbox carries an alpha.1-style
linux-m4-omarchy -1, a synthetic boot.bin around the real base kernel and
devicetree, and stand-ins for the protected video, Widevine, audio and speaker
files. The first signed update moves that boot object to the retained-bounds
kernel and changes only the video boot_kernel_sha256 plus boot.bin, so the
first run reports restart_needed. A second run leaves that state in place.
The live MCA file is arbitrary stand-in bytes, an unknown state, so that
second run reports restart_needed false. This sandbox does not spoof a
production hash to make the stand-in known; native evidence covers the real
stock-to-fixed MCA migration. pacman must refuse a database signed by an
untrusted key.

The sandbox installs the legacy stubs with --noscriptlet and then runs
m4-boot-compose itself. That is not proof the ALPM hook fired.

A smaller signed sandbox then installs a newer boot and speaker than its
repository, plus the synthetic older kernel. The explicit
pacman -S --needed transaction downgrades those newer packages. The corrected
setup leaves them installed and still updates the kernel. Those extra
packages are markers, not qualified modules, and a composer stub only lets
that setup call finish.
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
BROUGHT_BY_BOOT = ('omarchy-m4-gpu', 'omarchy-m4-headphones', 'omarchy-m4-cpu', 'omarchy-m4-evdi',
                   'omarchy-m4-backlight')
STUB = '''pkgname=e2e-system
pkgver=1
pkgrel=1
arch=(any)
provides=(python coreutils kmod pacman brightnessctl gstreamer=1.28.7 gst-plugins-base-libs=1.28.7 libpulse)
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
OLD_SPEAKER = '''pkgname=omarchy-m4-speaker
pkgver=1
pkgrel=1
arch=(aarch64)
depends=(python)
package() { install -Dm644 /dev/null "$pkgdir/usr/bin/m4-speaker-module"; }
'''
# Arbitrary live bytes, not a stock or fixed identity. Left in place. The
# updater must classify them as unknown and must not ask for a restart when
# boot did not change. Native evidence covers the real MCA migration.
MCA_LIVE = f'usr/lib/omarchy/j713-audio/{RELEASE}/modules/snd-soc-apple-mca.ko'
MCA_STAND_IN = b'legacy mca stand-in\n'
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


def boot_bin(kernel, dtb, loader):
    args = b'chosen.bootargs=dockchannel_hid.m4_power_v2=1 dockchannel_hid.m4_afe_action=1\n'
    initramfs = b'\x1f\x8bsandbox base cpio' + b'070701 sandbox owner firmware'
    return loader + args + kernel + dtb + b'm1n1_initramfs' + struct.pack('<I', len(initramfs)) + initramfs


def sandbox(root, old, stub, old_speaker):
    for name in ('var/lib/pacman', 'var/cache/pacman/pkg', 'var/log', 'etc/pacman.d/hooks',
                 'etc/pacman.d/gnupg', 'srv/core', 'boot/efi/omarchy', 'sys/firmware/devicetree/base'):
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
                        {'boot_kernel_sha256': kernel_sha, 'kernel_release': RELEASE,
                         'owner_panel': 'custom-kept'}),
                       ('etc/omarchy/j713-audio-manifest.json', {'kernel_release': RELEASE})):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(json.dumps(data) + '\n')
    live = root / MCA_LIVE
    live.parent.mkdir(parents=True, exist_ok=True)
    live.write_bytes(MCA_STAND_IN)
    sudo('chown', '-R', 'root:root', root)
    pacman = ['pacman', '--root', root, '--dbpath', root / 'var/lib/pacman', '--config', root / 'etc/pacman.conf',
              '--cachedir', root / 'var/cache/pacman/pkg', '--gpgdir', root / 'etc/pacman.d/gnupg',
              '--hookdir', root / 'etc/pacman.d/hooks', '--logfile', root / 'var/log/pacman.log']
    sudo(*pacman, '-Sy')
    sudo(*pacman, '-U', '--noconfirm', '--noscriptlet', stub, old, old_speaker)
    sudo('pacman-key', '--gpgdir', root / 'etc/pacman.d/gnupg', '--init')
    return pacman


def snapshot(root):
    names = [*PROTECTED, 'boot/efi/omarchy/boot.bin', 'etc/omarchy/j713-video-manifest.json',
             'etc/omarchy/j713-audio-manifest.json', MCA_LIVE]
    return {name: sha((root / name).read_bytes()) for name in names}


def base_payload(package, scratch):
    """Extract the unchanged base kernel package and return its file table."""
    checker = runpy.run_path(str(ROOT / 'tools/check-m4-packages.py'))
    dest = scratch / 'base-kernel'
    dest.mkdir()
    _info, files = checker['extract'](package, dest)
    return files


def package_version(path):
    """pacman -Q text for a built package filename. pkgver contains no hyphen."""
    name, version, release, _arch = path.name.removesuffix('.pkg.tar.zst').rsplit('-', 3)
    return f'{name} {version}-{release}'


def marker_recipe(name, version, release, depends=()):
    """Empty signed package. The marker file is not a qualified module."""
    dependency = 'depends=(' + ' '.join(depends) + ')\n' if depends else ''
    marker = f'{name} {version}-{release}'
    return f'''pkgname={name}
pkgver={version}
pkgrel={release}
arch=(aarch64)
{dependency}options=('!strip' '!debug')
package() {{
  printf '%s\\n' '{marker}' | install -Dm644 /dev/stdin "$pkgdir/usr/share/omarchy-m4/{name}.marker"
}}
'''


def prove_newer_candidates_stay(scratch, stub, old_kernel, old_speaker, env, fingerprint, key):
    """Explicit --needed downgrades a newer boot and speaker; setup does not.

    Reuses the synthetic older kernel, makepkg, signed_repository and sandbox.
    The repository kernel depends on omarchy-m4-boot with no version, so the
    already-installed newer boot satisfies that dependency. The downgrade in
    the baseline comes from naming the package. Marker bytes are not qualified
    modules. The composer stub is not boot composition, and the setup install
    still passes --noscriptlet in this sandbox.
    """
    output = scratch / 'newer-pkgs'
    source = scratch / 'newer-src'
    output.mkdir()
    source.mkdir()
    built = {}

    def build(label, name, version, release, depends=()):
        makepkg(source / label, marker_recipe(name, version, release, depends), output)
        package = output / f'{name}-{version}-{release}-aarch64.pkg.tar.zst'
        require(package.is_file(), 'did not build ' + label)
        built[label] = package

    build('boot-public', 'omarchy-m4-boot', '1', '1')
    build('boot-newer', 'omarchy-m4-boot', '1', '99')
    build('speaker-newer', 'omarchy-m4-speaker', '1', '99')
    build('kernel-public', 'linux-m4-omarchy', '7.1.9.m4.20261008', '2', ('omarchy-m4-boot',))
    equals = [name for name in CHANNEL['packages']
              if name not in ('omarchy-m4-boot', 'omarchy-m4-speaker', 'linux-m4-omarchy')]
    for name in equals:
        build(name, name, '1', '1')
    repo = scratch / 'newer-repo'
    signed_repository(repo, [built['boot-public'], old_speaker, built['kernel-public'],
                             *[built[name] for name in equals]], env, fingerprint)
    run('chmod', '-R', 'a+rX', repo)
    hold = scratch / 'newer-hold'
    hold.mkdir()
    sandbox(hold, old_kernel, stub, old_speaker)

    def pacman_of(root, *args):
        command = ['pacman', '--root', root, '--dbpath', root / 'var/lib/pacman',
                   '--cachedir', root / 'var/cache/pacman/pkg', '--gpgdir', root / 'etc/pacman.d/gnupg',
                   '--hookdir', root / 'etc/pacman.d/hooks', '--logfile', root / 'var/log/pacman.log',
                   '--config', root / 'etc/pacman.conf', *args]
        result = sudo(*command, check=False, capture_output=True, text=True)
        print(result.stdout + result.stderr, end='')
        return result

    added = pacman_of(hold, '-U', '--noconfirm', '--noscriptlet', built['boot-newer'],
                      built['speaker-newer'], *[built[name] for name in equals])
    require(added.returncode == 0, 'could not install the newer candidate packages')
    composer = scratch / 'newer-composer.py'
    composer.write_text('import json\nprint(json.dumps({"status": "current"}))\n')
    sudo('mkdir', '-p', hold / 'usr/bin')
    sudo('cp', composer, hold / 'usr/bin/m4-boot-compose')
    sudo('chmod', '755', hold / 'usr/bin/m4-boot-compose')
    require(pacman_of(hold, '-Q', 'omarchy-m4-boot').stdout.strip() == package_version(built['boot-newer']),
            'the fixture did not install the newer boot')
    require(pacman_of(hold, '-Q', 'omarchy-m4-speaker').stdout.strip() == package_version(built['speaker-newer']),
            'the fixture did not install the newer speaker')
    require(pacman_of(hold, '-Q', 'linux-m4-omarchy').stdout.strip() == package_version(old_kernel),
            'the fixture did not keep the synthetic older kernel')
    baseline = scratch / 'newer-baseline'
    kept = scratch / 'newer-kept'
    sudo('cp', '-a', hold, baseline)
    sudo('cp', '-a', hold, kept)
    server = f'file://{repo}'
    include = 'Include = ' + str(baseline / 'etc/pacman.d/omarchy-m4.conf')
    snippet = f'[omarchy-m4]\nSigLevel = Required DatabaseRequired\nServer = {server}\n'
    blob = scratch / 'newer-snippet.conf'
    blob.write_text(snippet)
    sudo('cp', blob, baseline / 'etc/pacman.d/omarchy-m4.conf')
    current = (baseline / 'etc/pacman.conf').read_text()
    require(current.count('[core]\n') == 1, 'sandbox pacman.conf has no single [core]')
    updated = scratch / 'newer-pacman.conf'
    updated.write_text(current.replace('[core]\n', include + '\n\n[core]\n', 1))
    sudo('cp', updated, baseline / 'etc/pacman.conf')
    for args in (('--add', str(key)), ('--lsign-key', fingerprint)):
        trusted = sudo('pacman-key', '--gpgdir', baseline / 'etc/pacman.d/gnupg', *args, check=False,
                       capture_output=True, text=True)
        print(trusted.stdout + trusted.stderr, end='')
        require(trusted.returncode == 0, 'baseline could not trust the channel key')
    require(pacman_of(baseline, '-Sy').returncode == 0, 'baseline database refresh failed')
    # The pre-fix updater names every requested package. --needed skips an equal
    # version and still downgrades a newer one. No --noscriptlet: version
    # selection happens before scriptlets, and these markers have none.
    downgraded = pacman_of(baseline, '-S', '--needed', '--noconfirm', *CHANNEL['packages'])
    require(downgraded.returncode == 0, 'explicit --needed baseline failed')
    require(pacman_of(baseline, '-Q', 'omarchy-m4-boot').stdout.strip() == package_version(built['boot-public']),
            'explicit --needed did not downgrade the newer boot')
    require(pacman_of(baseline, '-Q', 'omarchy-m4-speaker').stdout.strip() == package_version(old_speaker),
            'explicit --needed did not downgrade the newer speaker')
    require(pacman_of(baseline, '-Q', 'linux-m4-omarchy').stdout.strip() == package_version(built['kernel-public']),
            'explicit --needed did not update the older kernel')
    setup = ['python3', ROOT / 'tools/m4-channel-setup.py', '--root', kept, '--server', server,
             '--key-file', key, '--fingerprint', fingerprint, '--apply']
    corrected = sudo(*setup, check=False, capture_output=True, text=True)
    print(corrected.stdout + corrected.stderr, end='')
    require(corrected.returncode == 0, 'corrected setup failed')

    def echoed(operation):
        matches = [line for line in corrected.stderr.splitlines()
                   if line.startswith('+ pacman ') and operation in line.split()]
        require(len(matches) == 1, operation + ' was not run once')
        return matches[0]

    refresh, installed_query, update_query, install = (echoed(name) for name in ('-Sy', '-Qq', '-Quq', '-S'))
    require(corrected.stderr.index(refresh) < corrected.stderr.index(installed_query) <
            corrected.stderr.index(update_query) < corrected.stderr.index(install),
            'package queries did not follow the channel refresh')
    full = '--config ' + str(kept / 'etc/pacman.conf')
    require(full in installed_query and full in update_query and full in install,
            'queries did not use the same config as the install')
    require(full not in refresh, 'the channel refresh used the full config')
    require('--needed' in install.split() and '--noconfirm' in install.split() and '--noscriptlet' in install.split(),
            'the install transaction did not keep --needed')
    require('-Syu' not in install.split() and '--sysupgrade' not in install.split(),
            'setup ran a system upgrade')
    require('linux-m4-omarchy' in install.split(), 'the older kernel was not installed')
    require('omarchy-m4-boot' not in install.split() and 'omarchy-m4-speaker' not in install.split(),
            'a newer candidate was named on the install')
    plan = json.loads(corrected.stdout)
    require(plan['install'] == ['linux-m4-omarchy'] and plan['applied'] is True and plan['boot'] == 'current'
            and plan['restart_needed'] is False and plan['status'].startswith('connected'),
            'corrected setup did not finish with only the kernel transaction')
    require(pacman_of(kept, '-Q', 'omarchy-m4-boot').stdout.strip() == package_version(built['boot-newer']),
            'corrected setup downgraded the newer boot')
    require(pacman_of(kept, '-Q', 'omarchy-m4-speaker').stdout.strip() == package_version(built['speaker-newer']),
            'corrected setup downgraded the newer speaker')
    require(pacman_of(kept, '-Q', 'linux-m4-omarchy').stdout.strip() == package_version(built['kernel-public']),
            'corrected setup did not update the older kernel')
    require(pacman_of(kept, '-Q', equals[0]).stdout.strip() == package_version(built[equals[0]]),
            'corrected setup changed an equal package')


def assert_base_kernel(root, files):
    """The installed base image and every module still match that package."""
    compared = 0
    for path, (_mode, digest) in files.items():
        if path == 'usr/lib/omarchy-m4/Image.gz' or path.startswith('usr/lib/modules/'):
            require(sha((root / path).read_bytes()) == digest, 'Base kernel file differs: ' + path)
            compared += 1
    require(compared > 1000, 'Base kernel comparison did not cover the module tree')


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
            makepkg(scratch / 'old-speaker', OLD_SPEAKER, local)
            env, fingerprint, armored = new_key(scratch / 'gnupg', 'M4 channel e2e <e2e@invalid>')
            signed_repository(scratch / 'repo', packages, env, fingerprint)
            root = scratch / 'root'
            root.mkdir()
            kernel = member(by_name['linux-m4-omarchy'], 'usr/lib/omarchy-m4/Image.gz')
            require(sha(kernel) == CHANNEL['kernel']['image_sha256'], 'Base package image is not the pinned kernel')
            dtb = member(by_name['omarchy-m4-boot'], 'usr/lib/omarchy-m4/j713.dtb')
            (root / 'boot/efi/omarchy').mkdir(parents=True)
            loader = member(by_name['omarchy-m4-boot'], 'usr/lib/omarchy-m4/backlight/stage2-hidden.bin')
            (root / 'boot/efi/omarchy/boot.bin').write_bytes(boot_bin(kernel, dtb, loader))
            old, = local.glob('linux-m4-omarchy-*.pkg.tar.zst')
            old_speaker, = local.glob('omarchy-m4-speaker-1-1-*.pkg.tar.zst')
            stub, = local.glob('e2e-system-*.pkg.tar.zst')
            # --noscriptlet: the legacy stubs do not run hooks. Later setup calls the composer explicitly.
            pacman = sandbox(root, old, stub, old_speaker)
            base_files = base_payload(by_name['linux-m4-omarchy'], scratch)
            before = snapshot(root)
            key = scratch / 'key.asc'
            key.write_text(armored)
            setup = ['python3', ROOT / 'tools/m4-channel-setup.py', '--root', root,
                     '--server', f'file://{scratch}/repo', '--key-file', key, '--fingerprint', fingerprint]
            plan = json.loads(sudo(*setup, capture_output=True, text=True).stdout)
            require(plan['applied'] is False and plan['pacman_conf_changes'], 'Inspection did not plan the change')
            require(snapshot(root) == before, 'Inspection changed files')
            # Synthetic selector for this sandbox, not a copy of a released setup script.
            # It omits the packages an older request did not name, including speaker.
            # omarchy-m4-boot's versioned dependency must still bring speaker 1-5.
            omitted = BROUGHT_BY_BOOT + ('omarchy-m4-speaker',)
            source = (ROOT / 'tools/m4-channel-setup.py').read_text()
            listed = source[source.index('PACKAGES = ('):source.index(')', source.index('PACKAGES = (')) + 1]
            older = scratch / 'm4-channel-setup-older.py'
            older.write_text(source.replace(listed, 'PACKAGES = (' + ', '.join(
                repr(p) for p in CHANNEL['packages'] if p not in omitted) + ')'))
            require(not any(p in older.read_text().split('PACKAGES = (')[1].split(')')[0] for p in omitted),
                    'The older request stand-in still lists a package the boot package brings')
            results = []
            for attempt in range(2):
                command = [setup[0], older, *setup[2:]] if attempt == 0 else setup
                result = sudo(*command, '--apply', capture_output=True, text=True, check=False)
                print(result.stdout + result.stderr)
                require(result.returncode == 0, f'Setup attempt {attempt + 1} failed')
                results.append(json.loads(result.stdout))
                if attempt == 0:
                    for brought in omitted:
                        require(brought not in results[0]['install'] and
                                run(*pacman, '-Q', brought, check=False).returncode == 0,
                                'The first run from the older request did not install ' + brought)
                    require('omarchy-m4-speaker 1-5' in run(*pacman, '-Q', 'omarchy-m4-speaker',
                                                           capture_output=True, text=True).stdout,
                            'The boot dependency did not upgrade the speaker stub to 1-5')
                    after_first = snapshot(root)
                    assert_base_kernel(root, base_files)
                conf = (root / 'etc/pacman.conf').read_text()
                require(conf.count('Include = ' + str(root / 'etc/pacman.d/omarchy-m4.conf')) == 1,
                        'The channel include is missing or duplicated')
            require(results[0]['status'].startswith('connected') and
                    results[0]['boot'] == 'updated; takes effect at next boot' and
                    results[0]['restart_needed'] is True, 'The first update did not move the old boot object')
            require(results[1]['status'].startswith('connected') and results[1]['boot'] == 'current' and
                    results[1]['restart_needed'] is False,
                    'The second run requested a restart for the unknown MCA stand-in')
            require(snapshot(root) == after_first, 'The second run changed boot, video, or a protected file')
            assert_base_kernel(root, base_files)
            compose = runpy.run_path(str(ROOT / 'tools/m4-boot-compose.py'))
            before_boot = compose['parse']((root / 'boot/efi/omarchy/boot.bin.previous').read_bytes())
            after_boot = compose['parse']((root / 'boot/efi/omarchy/boot.bin').read_bytes())
            require(sha(before_boot['kernel']) == CHANNEL['kernel']['image_sha256'] and
                    sha(after_boot['kernel']) == CHANNEL['boot_kernel']['image_sha256'],
                    'boot.bin did not move from the base kernel to the retained-bounds image')
            require(all(before_boot[name] == after_boot[name] for name in ('bootargs', 'dtb', 'initramfs', 'loader')),
                    'The boot update changed more than the kernel')
            video = json.loads((root / 'etc/omarchy/j713-video-manifest.json').read_text())
            require(video == {'boot_kernel_sha256': CHANNEL['boot_kernel']['image_sha256'],
                              'kernel_release': RELEASE, 'owner_panel': 'custom-kept'},
                    'The video manifest changed something other than boot_kernel_sha256')
            audio = json.loads((root / 'etc/omarchy/j713-audio-manifest.json').read_text())
            require(audio == {'kernel_release': RELEASE}, 'The audio manifest changed')
            protected_now = {name: sha((root / name).read_bytes()) for name in PROTECTED}
            protected_before = {name: before[name] for name in PROTECTED}
            require(protected_now == protected_before, 'A protected file changed')
            require((root / MCA_LIVE).read_bytes() == MCA_STAND_IN, 'The live MCA stand-in was replaced')
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
            require(snapshot(root) == after_first, 'Staging checks changed boot, video, or a protected file')
            for path, digest in CHANNEL['gst_audio'].items():
                require(sha((root / path).read_bytes()) == digest, 'GStreamer sink missing or wrong: ' + path)
            require((root / 'usr/lib/systemd/system/speakersafetyd-j713.service.d/50-realtime.conf').is_file(),
                    'The real-time speaker setting is missing')
            for path, digest in CHANNEL['speaker_module'].items():
                require(sha((root / path).read_bytes()) == digest, 'Staged speaker module missing or wrong: ' + path)
            require((root / 'usr/lib/systemd/system/m4-j713-audio-register.service.d/20-m4-speaker-module.conf').is_file()
                    and os.access(root / 'usr/bin/m4-speaker-module', os.X_OK), 'The speaker module step is missing')
            for path, digest in CHANNEL['mca_module'].items():
                require(sha((root / path).read_bytes()) == digest, 'Staged MCA module missing or wrong: ' + path)
            mca_drop = ROOT / 'packaging/omarchy-m4/omarchy-m4-speaker/25-m4-mca-module.conf'
            installed_drop = root / 'usr/lib/systemd/system/m4-j713-audio-register.service.d/25-m4-mca-module.conf'
            require(installed_drop.read_bytes() == mca_drop.read_bytes(), 'The MCA drop-in is not the packaged file')
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
            for path, digest in CHANNEL['backlight_loaders'].items():
                require(sha((root / path).read_bytes()) == digest, 'Display handoff stage2 missing or wrong: ' + path)
            require((root / 'usr/lib/systemd/user/omarchy-m4-notch.service').is_file()
                    and os.access(root / 'usr/bin/m4-notch', os.X_OK), 'The notch switch is missing')
            for path, digest in CHANNEL['backlight_module'].items():
                require(sha((root / path).read_bytes()) == digest, 'Native backlight module missing or wrong: ' + path)
            require((root / 'usr/lib/systemd/system/m4-backlight.service').is_file() and
                    (root / 'usr/lib/systemd/system/m4-backlight-display.service').is_file() and
                    os.access(root / 'usr/bin/m4-backlight', os.X_OK), 'Native backlight startup helper is missing')
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
            prove_newer_candidates_stay(scratch, stub, old, old_speaker, env, fingerprint, key)
            print('m4-channel-e2e: passed')
        finally:
            # pacman ran as root in these trees; remove them before the temporary directory goes.
            subprocess.run(['sudo', 'rm', '-rf', str(scratch / 'root'), str(scratch / 'forged-db'),
                            str(scratch / 'newer-hold'), str(scratch / 'newer-baseline'),
                            str(scratch / 'newer-kept')], check=False)


if __name__ == '__main__':
    sys.exit(main())
