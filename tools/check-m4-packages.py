#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Check built M4 channel packages against the channel policy before signing.

Every file must be on its package's allowlist and off the protected list (the
video, Widevine, audio, speaker and trackpad work installed outside pacman),
except the exact files listed with a reason under protected_exceptions.
The kernel and devicetree must be the pinned, qualified ones, and the boot
composer must accept exactly them. A package already published under the same
file name must have identical contents, so a change cannot ship without a
version bump.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path
import runpy
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CHANNEL = ROOT / 'packaging/omarchy-m4/channel.json'
REALTIME = 'usr/lib/systemd/system/speakersafetyd-j713.service.d/50-realtime.conf'
SPEAKER_DROP_IN = 'usr/lib/systemd/system/m4-j713-audio-register.service.d/20-m4-speaker-module.conf'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def pkginfo(text):
    info = {}
    for line in text.splitlines():
        if line and not line.startswith('#'):
            key, _, value = line.partition(' = ')
            info.setdefault(key, []).append(value)
    return info


def extract(package, destination):
    """Return (pkginfo, {path: (mode, sha256)}) read from the archive's own headers.

    Regular files are also written below destination for content checks.
    """
    archive = destination.with_name(destination.name + '.tar')
    with archive.open('xb') as stream:
        subprocess.run(['zstd', '-dcq', str(package)], stdout=stream, check=True)
    try:
        return read_archive(archive, destination, package.name)
    finally:
        archive.unlink()


def read_archive(archive, destination, label):
    """Read an uncompressed package tar; see extract()."""
    info, files, scripts = None, {}, None
    with tarfile.open(archive) as tar:
        for member in tar:
            name = member.name.removeprefix('./')
            parts = Path(name).parts
            require(name and not name.startswith('/') and '..' not in parts, 'Unsafe member path: ' + name)
            if member.isdir():
                continue
            require(member.isfile(), 'Only regular files and directories are allowed: ' + name)
            data = tar.extractfile(member).read()
            if name == '.PKGINFO':
                info = pkginfo(data.decode())
                continue
            if name == '.INSTALL':
                scripts = sha(data)
                continue
            if len(parts) == 1 and name.startswith('.'):
                continue
            require(name not in files, 'Duplicate member: ' + name)
            files[name] = (member.mode & 0o7777, sha(data))
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    require(info is not None, 'Package has no .PKGINFO: ' + label)
    info['.INSTALL'] = [scripts]
    return info, files


def release_identity(info, files):
    """What a published file name promises: contents, install script and metadata."""
    return {key: value for key, value in info.items() if key not in ('builddate', 'packager')}, files


def allowed(name, prefixes):
    return any(name == p or (p.endswith('/') and name.startswith(p)) for p in prefixes)


def check_package(channel, package, info, files, root):
    name = info['pkgname'][0]
    require(name in channel['packages'], 'Unexpected package ' + name)
    expected = f"{name}-{info['pkgver'][0]}-{info['arch'][0]}.pkg.tar.zst"
    require(package.name == expected, 'Package file name does not match its metadata: ' + package.name)
    require(files, 'Empty package ' + name)
    for path in files:
        require(allowed(path, channel['allowed_paths'][name]), f'{name} installs an unlisted path: {path}')
        require(not allowed(path, channel['protected_paths']) or path in channel.get('protected_exceptions', {}),
                f'{name} installs a protected path: {path}')
        require(files[path][0] & 0o7022 == 0, f'{name} installs a writable or setuid file: {path}')
    kernel = channel['kernel']
    if name == 'linux-m4-omarchy':
        image = files.get('usr/lib/omarchy-m4/Image.gz')
        require(image and image[1] == kernel['image_sha256'], 'Kernel package does not carry the pinned kernel')
        size = (root / 'usr/lib/omarchy-m4/Image.gz').stat().st_size
        require(size == kernel['image_bytes'], 'Kernel image size differs from the pin')
        require(any(p.endswith('.ko') for p in files), 'Kernel package has no modules')
        require(info.get('conflict') == ['linux-aarch64'] and
                sorted(info.get('provides', [])) == ['linux-aarch64=7.1.9', 'linux=7.1.9'] and
                sorted(info.get('depend', [])) == ['coreutils', 'kmod', 'omarchy-m4-boot'],
                'Kernel package relations changed')
    elif name == 'omarchy-m4-boot':
        require(files.get('usr/lib/omarchy-m4/j713.dtb', (0, ''))[1] == channel['devicetree']['sha256'],
                'Boot package does not carry the pinned devicetree')
        composer = (ROOT / 'tools/m4-boot-compose.py').read_bytes()
        hook = (ROOT / 'packaging/omarchy-m4/omarchy-m4-boot/90-omarchy-m4-boot.hook').read_bytes()
        require(files['usr/bin/m4-boot-compose'] == (0o755, sha(composer)), 'Packaged composer differs')
        require(files['usr/share/libalpm/hooks/90-omarchy-m4-boot.hook'][1] == sha(hook), 'Packaged hook differs')
        policy = runpy.run_path(str(root / 'usr/bin/m4-boot-compose'))
        require(policy['KNOWN_KERNELS'].get(kernel['image_sha256']) == kernel['release'],
                'Composer does not accept the pinned kernel release')
        require(channel['devicetree']['sha256'] in policy['KNOWN_DTBS'], 'Composer does not accept the pinned devicetree')
        realtime = (ROOT / 'packaging/omarchy-m4/omarchy-m4-boot/speakersafetyd-j713-realtime.conf').read_bytes()
        require(files.get(REALTIME) == (0o644, sha(realtime)), 'Boot package lacks the real-time speaker setting')
        # No dependency on the sinks: a later gst-plugins-good must be able to replace them.
        for path, source in (('usr/bin/omarchy-m4-update', 'tools/omarchy-m4-update'),
                             ('usr/lib/omarchy-m4/m4-channel-setup.py', 'tools/m4-channel-setup.py')):
            require(files.get(path, (0, ''))[1] == sha((ROOT / source).read_bytes()), 'Boot package differs: ' + path)
        names = [re.split('[<>=]', d, maxsplit=1)[0] for d in info.get('depend', [])]
        require('omarchy-m4-gst-audio' not in names, 'Boot package must not require the GStreamer sinks')
    elif name == 'omarchy-m4-speaker':
        for path, digest in channel['speaker_module'].items():
            require(files.get(path, (0, ''))[1] == digest, 'Staged speaker module differs from the pin: ' + path)
        tool = (ROOT / 'tools/m4-speaker-module.py').read_bytes()
        require(files.get('usr/bin/m4-speaker-module') == (0o755, sha(tool)), 'Packaged speaker module tool differs')
        drop_in = (ROOT / 'packaging/omarchy-m4/omarchy-m4-speaker/20-m4-speaker-module.conf').read_bytes()
        require(files.get(SPEAKER_DROP_IN) == (0o644, sha(drop_in)), 'Speaker module drop-in differs')
    elif name == 'omarchy-m4-gst-audio':
        for path, digest in channel['gst_audio'].items():
            require(files.get(path, (0, ''))[1] == digest, 'GStreamer sink differs from the pin: ' + path)
        require(info.get('conflict') == ['gst-plugins-good'], 'GStreamer sink package must conflict with gst-plugins-good')
        require(sorted(info.get('depend', [])) == ['gst-plugins-base-libs>=1.28.7', 'gstreamer>=1.28.7', 'libpulse'],
                'GStreamer sink package dependencies changed')
    elif name == 'omarchy-m4-keyring':
        listing = subprocess.run(['gpg', '--show-keys', '--with-colons',
                                  str(root / 'usr/share/pacman/keyrings/omarchy-m4.gpg')],
                                 check=True, capture_output=True, text=True).stdout
        fingerprints = [line.split(':')[9] for line in listing.splitlines() if line.startswith('fpr:')]
        require(fingerprints[:1] == [channel['signing_fingerprint']], 'Keyring does not hold the channel signing key')
        trusted = (root / 'usr/share/pacman/keyrings/omarchy-m4-trusted').read_text()
        require(trusted == channel['signing_fingerprint'] + ':4:\n', 'Keyring trusts an unexpected key')
    return name


def published(channel, filename, scratch):
    """Return the published package of that name, or None when there is none."""
    url = channel['server'] + '/' + filename
    try:
        with urllib.request.urlopen(url, timeout=120) as response:
            data = response.read()
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise
    path = scratch / ('published-' + filename)
    path.write_bytes(data)
    return path


def check(directory, compare_published=True):
    channel = json.loads(CHANNEL.read_text())
    packages = sorted(directory.glob('*.pkg.tar.zst'))
    require(sorted(p.name for p in directory.iterdir()) == [p.name for p in packages],
            'Only package archives may be in the build output')
    seen = set()
    with tempfile.TemporaryDirectory(prefix='m4-check-') as scratch:
        scratch = Path(scratch)
        for package in packages:
            root = scratch / package.name
            root.mkdir()
            info, files = extract(package, root)
            name = check_package(channel, package, info, files, root)
            require(name not in seen, 'Duplicate package ' + name)
            seen.add(name)
            if compare_published:
                previous = published(channel, package.name, scratch)
                if previous is not None:
                    other = scratch / ('published-root-' + package.name)
                    other.mkdir()
                    require(release_identity(*extract(previous, other)) == release_identity(info, files),
                            package.name + ' is already published with different contents, install script '
                            'or metadata; bump pkgrel or pkgver')
            print(f'{package.name}: {len(files)} files, policy passed')
    require(seen == set(channel['packages']), 'Missing packages: ' + ', '.join(set(channel['packages']) - seen))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--offline', action='store_true', help='skip the published-package comparison')
    args = parser.parse_args()
    try:
        check(args.directory, not args.offline)
    except ValueError as error:
        raise SystemExit('check-m4-packages: ' + str(error))


if __name__ == '__main__':
    main()
