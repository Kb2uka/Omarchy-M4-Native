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
NOTCH_UNIT = 'usr/lib/systemd/user/omarchy-m4-notch.service'
SPEAKER_DROP_IN = 'usr/lib/systemd/system/m4-j713-audio-register.service.d/20-m4-speaker-module.conf'
GPU_DROP_IN = 'usr/lib/systemd/system/m4-gpu.service.d/20-m4-gpu-module.conf'
HEADPHONES_DROP_IN = 'usr/lib/systemd/system/m4-j713-audio-register.service.d/30-m4-headphones.conf'
CPU_IDLE_UNIT = 'usr/lib/systemd/system/m4-cpu-idle.service'
# Installed paths of the headphone UCM and WirePlumber files, relative to their
# source tree audio/j713/headphones/policy/.
HEADPHONES_POLICY = ('usr/share/alsa/ucm2/conf.d/J713Headphones/J713Headphones.conf',
                     'usr/share/alsa/ucm2/conf.d/J713Headphones/HiFi.conf',
                     'usr/share/wireplumber/wireplumber.conf.d/83-j713-headphones.conf',
                     'usr/share/wireplumber/scripts/default-nodes/j713-headphones.lua')
SOURCE_JOINS = ROOT / 'packaging/channel-source-joins.json'
CORRESPONDENCE = ('rebuilt-identical', 'unverified')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def git_tree_id(tree, revision, path):
    """The git tree object of path at revision, or None when it does not exist there."""
    result = subprocess.run(['git', '-C', str(tree), 'rev-parse', '--verify', '--quiet', revision + ':' + path],
                            capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def source_joins(path=None):
    """Load the channel binary-to-source joins and reject malformed entries."""
    target = path or SOURCE_JOINS
    require(target.is_file() and not target.is_symlink(), 'Missing channel source joins: ' + str(target))
    joins = json.loads(target.read_bytes())
    require(joins.get('schema') == 1 and isinstance(joins.get('binaries'), dict),
            'Unknown channel source joins schema')
    for name, entry in joins['binaries'].items():
        require(re.fullmatch(r'[0-9a-f]{64}', entry.get('sha256', '')) and
                entry.get('correspondence') in CORRESPONDENCE and entry.get('sources'),
                'Incomplete channel source join: ' + name)
        for source in entry['sources']:
            require(set(source) in ({'tree', 'commit'}, {'archive', 'sha256'}),
                    'Unknown source reference for ' + name)
            require('commit' not in source or re.fullmatch(r'[0-9a-f]{40}', source['commit']),
                    'A joined tree commit is a full commit ID: ' + name)
            require('sha256' not in source or re.fullmatch(r'[0-9a-f]{64}', source['sha256']),
                    'A joined archive sha256 is a 64-char hex string: ' + name)
            require('tree' in source or (source['archive'].startswith(('source/', 'package-inputs/')) and
                                         '/' not in source['archive'].split('/', 1)[1]),
                    'Source archives live directly in source/ or package-inputs/: ' + name)
    return joins


def package_inputs(name):
    """Return {filename: sha256} for all package-inputs binaries downloaded by this package."""
    recipe = ROOT / 'packaging/omarchy-m4' / name / 'PKGBUILD'
    require(recipe.is_file(), f'Missing PKGBUILD for package: {name}')
    text = recipe.read_text()
    require(not re.search(r'^\s*source_[a-zA-Z0-9_]+\s*=', text, re.M),
            f'{name}: arch-suffixed source_* array is not allowed')
    require(not re.search(r'^\s*sha256sums_[a-zA-Z0-9_]+\s*=', text, re.M),
            f'{name}: arch-suffixed sha256sums_* array is not allowed')
    vars = {}
    for line in text.splitlines():
        m = re.match(r'^([a-zA-Z_0-9]+)=(.*)$', line.strip())
        if m:
            vars[m.group(1)] = m.group(2).strip('\'"')
    for _ in range(10):
        changed = False
        for k, v in list(vars.items()):
            for sub_k, sub_v in vars.items():
                if sub_k == k:
                    continue
                if f'${{{sub_k}}}' in v:
                    vars[k] = v.replace(f'${{{sub_k}}}', sub_v)
                    changed = True
                elif f'${sub_k}' in v:
                    vars[k] = v.replace(f'${sub_k}', sub_v)
                    changed = True
        if not changed:
            break
    sources_m = re.search(r'^\s*source=\((.*?)\)', text, re.M | re.S)
    sums_m = re.search(r'^\s*sha256sums=\((.*?)\)', text, re.M | re.S)
    if not sources_m or not sums_m:
        require('package-inputs/' not in text,
                f'{name}: PKGBUILD mentions package-inputs/ but no inputs were parsed')
        return {}
    entries = re.findall(r"""['"]([^'"]+)['"]""", sources_m.group(1))
    sums = re.findall(r"'([0-9a-f]{64})'", sums_m.group(1))
    require(len(entries) == len(sums), f'{name}: source and sha256sums counts differ')
    inputs = {}
    for entry, digest in zip(entries, sums):
        for _ in range(10):
            for k, v in vars.items():
                entry = entry.replace(f'${{{k}}}', v).replace(f'${k}', v)
        if 'package-inputs/' in entry:
            filename = entry.split('package-inputs/')[-1].split('::')[-1].strip()
            inputs[filename] = digest
    if 'package-inputs/' in text:
        require(bool(inputs), f'{name}: PKGBUILD mentions package-inputs/ but no inputs were parsed')
    return inputs


def check_channel_joins(name, joins):
    """Every binary downloaded from package-inputs must have a rebuilt-identical source join."""
    inputs = package_inputs(name)
    for filename, digest in sorted(inputs.items()):
        require(filename in joins['binaries'],
                f'{name} downloads binary without a source join: {filename}')
        entry = joins['binaries'][filename]
        require(entry['sha256'] == digest,
                f'Source join names a different build of {filename}')
        require(entry['correspondence'] == 'rebuilt-identical',
                f'Source correspondence for {filename} is not verified by an identical rebuild: {entry.get("correspondence")}')
        for source in entry['sources']:
            if 'tree' in source:
                require((ROOT / source['tree']).exists(),
                        f'Joined source tree missing for {filename}: {source["tree"]}')
                if 'commit' in source:
                    pinned = git_tree_id(ROOT, source['commit'], source['tree'])
                    require(pinned is not None,
                            f'Joined commit {source["commit"][:12]} does not exist or does not contain {source["tree"]} for {filename}')
                    head_tree = git_tree_id(ROOT, 'HEAD', source['tree'])
                    require(pinned == head_tree,
                            f'Joined source tree {source["tree"]} differs from {source["commit"][:12]} for {filename}')


def check_stale_joins(channel, joins):
    """Refuse joins for binaries the channel no longer ships."""
    all_downloaded = set()
    for pkg in channel['packages']:
        all_downloaded.update(package_inputs(pkg))
    stale = set(joins['binaries']) - all_downloaded
    require(not stale,
            'Source joins for binaries the channel no longer ships: ' + ', '.join(sorted(stale)))


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


def check_package(channel, package, info, files, root, joins=None):
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
    if joins is not None:
        check_channel_joins(name, joins)
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
        # An older installed setup script does not list omarchy-m4-gpu; this dependency
        # makes its first omarchy-m4-update run install it with the new boot package.
        require('omarchy-m4-gpu' in names, 'Boot package must require the GPU module package')
        require('omarchy-m4-headphones' in names, 'Boot package must require the headphone jack package')
        require('omarchy-m4-cpu' in names, 'Boot package must require the CPU idle package')
        require('omarchy-m4-evdi' in names, 'Boot package must require the USB display package')
        # The opt-in notch stage 2 (#89): its pin, the tool's own pin, and the login unit.
        for path, digest in channel['notch_loader'].items():
            require(files.get(path) == (0o644, digest), 'Staged notch stage 2 differs from the pin: ' + path)
        tool = (ROOT / 'tools/m4-notch.py').read_bytes()
        require(files.get('usr/bin/m4-notch') == (0o755, sha(tool)), 'Packaged notch tool differs')
        unit = (ROOT / 'packaging/omarchy-m4/omarchy-m4-boot/omarchy-m4-notch.service').read_bytes()
        require(files.get(NOTCH_UNIT) == (0o644, sha(unit)), 'Notch login unit differs')
        notch = runpy.run_path(str(root / 'usr/bin/m4-notch'))
        require(channel['notch_loader'].get(notch['STAGED']) == notch['NOTCH']['sha256'],
                'Notch tool pins a different stage 2 than the channel')
    elif name == 'omarchy-m4-speaker':
        for path, digest in channel['speaker_module'].items():
            require(files.get(path, (0, ''))[1] == digest, 'Staged speaker module differs from the pin: ' + path)
        tool = (ROOT / 'tools/m4-speaker-module.py').read_bytes()
        require(files.get('usr/bin/m4-speaker-module') == (0o755, sha(tool)), 'Packaged speaker module tool differs')
        drop_in = (ROOT / 'packaging/omarchy-m4/omarchy-m4-speaker/20-m4-speaker-module.conf').read_bytes()
        require(files.get(SPEAKER_DROP_IN) == (0o644, sha(drop_in)), 'Speaker module drop-in differs')
    elif name == 'omarchy-m4-gpu':
        for path, digest in channel['gpu_module'].items():
            require(files.get(path, (0, ''))[1] == digest, 'Staged GPU module differs from the pin: ' + path)
        tool = (ROOT / 'tools/m4-gpu-module.py').read_bytes()
        require(files.get('usr/bin/m4-gpu-module') == (0o755, sha(tool)), 'Packaged GPU module tool differs')
        drop_in = (ROOT / 'packaging/omarchy-m4/omarchy-m4-gpu/20-m4-gpu-module.conf').read_bytes()
        require(files.get(GPU_DROP_IN) == (0o644, sha(drop_in)), 'GPU module drop-in differs')
    elif name == 'omarchy-m4-headphones':
        for path, digest in channel['headphone_modules'].items():
            require(files.get(path, (0, ''))[1] == digest, 'Staged headphone module differs from the pin: ' + path)
        tool = (ROOT / 'tools/m4-headphones.py').read_bytes()
        require(files.get('usr/bin/m4-headphones') == (0o755, sha(tool)), 'Packaged headphone tool differs')
        drop_in = (ROOT / 'packaging/omarchy-m4/omarchy-m4-headphones/30-m4-headphones.conf').read_bytes()
        require(files.get(HEADPHONES_DROP_IN) == (0o644, sha(drop_in)), 'Headphone drop-in differs')
        for path in HEADPHONES_POLICY:
            source = (ROOT / 'audio/j713/headphones/policy' / path).read_bytes()
            require(files.get(path) == (0o644, sha(source)), 'Packaged headphone policy differs: ' + path)
    elif name == 'omarchy-m4-cpu':
        for path, digest in channel['cpu_idle_module'].items():
            require(files.get(path, (0, ''))[1] == digest, 'Staged CPU idle module differs from the pin: ' + path)
        for path, digest in channel['cpu_guard_module'].items():
            require(files.get(path, (0, ''))[1] == digest, 'Staged boost guard differs from the pin: ' + path)
        tool = (ROOT / 'tools/m4-cpu-idle.py').read_bytes()
        require(files.get('usr/bin/m4-cpu-idle') == (0o755, sha(tool)), 'Packaged CPU idle tool differs')
        unit = (ROOT / 'packaging/omarchy-m4/omarchy-m4-cpu/m4-cpu-idle.service').read_bytes()
        require(files.get(CPU_IDLE_UNIT) == (0o644, sha(unit)), 'CPU idle unit differs')
        # The helper's own pin must be the channel's, or every boot would refuse the staged module.
        helper = runpy.run_path(str(root / 'usr/bin/m4-cpu-idle'))
        require(helper['PIN']['sha256'] == channel['cpu_idle_module'][helper['STAGED']] and
                helper['GUARD_PIN']['sha256'] == channel['cpu_guard_module'][helper['GUARD_STAGED']] and
                helper['RELEASE'] == kernel['release'], 'CPU idle tool pins a different module or kernel')
    elif name == 'omarchy-m4-evdi':
        for pins, label in ((channel['evdi_module'], 'evdi module'), (channel['evdi_library'], 'evdi library')):
            for path, digest in pins.items():
                require(files.get(path, (0, ''))[1] == digest, f'Staged {label} differs from the pin: ' + path)
        # The module is built for exactly the channel kernel; its directory names that release.
        require(all(p.startswith(f"usr/lib/modules/{kernel['release']}/") for p in channel['evdi_module']),
                'evdi module is not installed for the channel kernel')
        # Packages built for Arch's evdi (the displaylink AUR package) must install on this one.
        require(info.get('provides') == ['evdi=' + info['pkgver'][0].split('-')[0]] and
                sorted(info.get('conflict', [])) == ['evdi', 'evdi-dkms', 'evdi-git'],
                'evdi package relations changed')
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
    joins = source_joins()
    check_stale_joins(channel, joins)
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
            shipped = False
            if compare_published:
                previous = published(channel, package.name, scratch)
                if previous is not None:
                    other = scratch / ('published-root-' + package.name)
                    other.mkdir()
                    require(release_identity(*extract(previous, other)) == release_identity(info, files),
                            package.name + ' is already published with different contents, install script '
                            'or metadata; bump pkgrel or pkgver')
                    shipped = True
            # A version already published byte for byte is on Airs and is not uploaded again,
            # so the rebuilt-identical rule binds every version that has not shipped yet.
            name = check_package(channel, package, info, files, root, joins=None if shipped else joins)
            require(name not in seen, 'Duplicate package ' + name)
            seen.add(name)
            print(f'{package.name}: {len(files)} files, policy passed' + (' (already published)' if shipped else ''))
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
