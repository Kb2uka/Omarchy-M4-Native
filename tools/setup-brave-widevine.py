#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 studioxradio aka kb2uka
"""Explicit, owner-side installation of the pinned Linux ARM64 Widevine component."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import stat
import struct
import sys
import tempfile
import time
import urllib.request
import zipfile

VERSION = '4.10.3057.0'
URL = ('https://dl.google.com/release2/chrome_component/'
       'bar3g6bc54wcbgowdtlox2l3sa_4.10.3057.0/'
       'oimompecagnajdejgnnjijobebaeigek_4.10.3057.0_linux_arm64_h5xzghmkhfkkto65d2lfjxatgm.crx3')
ARCHIVE_SIZE = 19826058
ARCHIVE_SHA512 = ('2a5ad41d5037a1e9cbfe30a94653f49247c628278f922d5aab20566fac874685'
                  'ef8312f482e5bd23bdd88ecce98dc0c468c979637ebdb1174cc2801f8ffde8c6')
FILES = {
    'LICENSE': '20de375707692099b3132084695377ce5fec0aec05813dedcce094b8eda44386',
    'manifest.json': '128538e1eaa9dade9bdb033318ba4285f1fc54fea53efc62b4f417a9d89522ab',
    '_platform_specific/linux_arm64/libwidevinecdm.so':
        'e43843b7d42c486fc146b3009a87548ece55667e4e117fa1b2c5cdeb2ca35783',
    '_metadata/verified_contents.json':
        '5fc62fd2e3a85312023dbce2bf9b647c54c637766339f573c463b8c5a63b4f7b',
}
DIRECTORIES = {'_platform_specific/', '_platform_specific/linux_arm64/', '_metadata/'}
LIBRARY = '_platform_specific/linux_arm64/libwidevinecdm.so'


def require(value, message):
    if not value:
        raise ValueError(message)


def owned(path, home, directory=False, missing=False):
    require(path.is_absolute() and '..' not in path.parts and path.is_relative_to(home),
            'Path must be inside the owner home: ' + str(path))
    for item in (home, *reversed(path.relative_to(home).parents)):
        # Check each existing component below home separately; never follow links.
        if item == home:
            current = home
        else:
            current = home / item
        if current.exists() or current.is_symlink():
            info = current.lstat()
            require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
                    and not info.st_mode & 0o022, 'Unsafe owner directory: ' + str(current))
    try:
        info = path.lstat()
    except FileNotFoundError:
        require(missing, 'Required path is missing: ' + str(path))
        return
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    require(kind(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o022,
            'Unsafe owner path: ' + str(path))


def read_file(path, home, limit=16 * 1024 * 1024):
    owned(path, home)
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
    require(len(data) <= limit, 'File exceeds size limit: ' + str(path))
    return data


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'Duplicate JSON key')
        result[key] = value
    return result


def json_value(data):
    return json.loads(data, object_pairs_hook=unique_object)


def browser_closed(profile, proc=Path('/proc')):
    lock = profile / 'SingletonLock'
    require(not lock.exists() and not lock.is_symlink(),
            'Close all Brave windows first; the selected profile still has SingletonLock.')
    for process in proc.iterdir():
        if not process.name.isdecimal():
            continue
        try:
            uid = next(line for line in (process / 'status').read_text().splitlines()
                       if line.startswith('Uid:')).split()[1:]
            if str(os.getuid()) not in uid:
                continue
            executable = Path(str((process / 'exe').readlink()).removesuffix(' (deleted)'))
            require(executable.name not in ('brave', 'brave-browser', 'brave-browser-stable'),
                    'Close all Brave processes first; this tool never stops browsers.')
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError as error:
            raise ValueError('Cannot verify owner processes are closed: ' + process.name) from error


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Widevine download redirect refused')


def download():
    opener = urllib.request.build_opener(NoRedirect())
    deadline = time.monotonic() + 180
    data = bytearray()
    with opener.open(URL, timeout=30) as response:
        require(response.status == 200 and response.geturl() == URL, 'Unexpected download response')
        while True:
            require(time.monotonic() < deadline, 'Widevine download exceeded time limit')
            chunk = response.read(min(65536, ARCHIVE_SIZE + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
            require(len(data) <= ARCHIVE_SIZE, 'Widevine download exceeds pinned size')
    require(len(data) == ARCHIVE_SIZE, 'Widevine download size mismatch')
    require(hashlib.sha512(data).hexdigest() == ARCHIVE_SHA512, 'Widevine download SHA-512 mismatch')
    return bytes(data)


def unpack(data):
    require(len(data) == ARCHIVE_SIZE and hashlib.sha512(data).hexdigest() == ARCHIVE_SHA512,
            'Widevine archive identity mismatch')
    require(data[:8] == b'Cr24\x03\x00\x00\x00', 'Expected CRX3 archive')
    files = {}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = [entry.filename for entry in archive.infolist()]
        require(len(names) == len(set(names)) and set(names) == set(FILES) | DIRECTORIES,
                'Unexpected or duplicate Widevine archive member')
        for entry in archive.infolist():
            kind = stat.S_IFMT(entry.external_attr >> 16)
            if entry.filename in DIRECTORIES:
                require(entry.is_dir() and entry.file_size == 0 and kind in (0, stat.S_IFDIR),
                        'Invalid Widevine directory entry')
                continue
            require(not entry.is_dir() and kind in (0, stat.S_IFREG) and not entry.flag_bits & 1,
                    'Widevine archive links/special/encrypted entries are refused')
            require(entry.file_size <= 32 * 1024 * 1024, 'Widevine member exceeds size limit')
            content = archive.read(entry)
            require(hashlib.sha256(content).hexdigest() == FILES[entry.filename],
                    'Widevine member hash mismatch: ' + entry.filename)
            files[entry.filename] = content
    manifest = json_value(files['manifest.json'])
    require(manifest['version'] == VERSION and 'arm64' in manifest['accept_arch'],
            'Widevine manifest version/architecture mismatch')
    elf = files[LIBRARY]
    require(elf[:7] == b'\x7fELF\x02\x01\x01' and len(elf) >= 20
            and struct.unpack_from('<HH', elf, 16) == (3, 183),
            'Widevine library is not a Linux ARM64 shared object')
    return files


def installed(component, home):
    owned(component, home, directory=True)
    paths = list(component.rglob('*'))
    for path in paths:
        owned(path, home, directory=stat.S_ISDIR(path.lstat().st_mode))
    actual = {p.relative_to(component).as_posix() for p in paths}
    require(actual == set(FILES) | {p.rstrip('/') for p in DIRECTORIES},
            'Existing Widevine component has unexpected files')
    for name, expected in FILES.items():
        data = read_file(component / name, home, 32 * 1024 * 1024)
        require(hashlib.sha256(data).hexdigest() == expected,
                'Existing Widevine component differs; leaving it untouched: ' + name)


def directories(path, home):
    owned(path, home, directory=True, missing=True)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    owned(path, home, directory=True)


def atomic(path, data, home, previous=None, mode=0o600):
    owned(path, home, missing=previous is None)
    with tempfile.NamedTemporaryFile(prefix='.' + path.name + '.', dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            if previous is None:
                require(not path.exists() and not path.is_symlink(), 'Destination appeared; refusing overwrite')
            else:
                require(read_file(path, home) == previous, 'Preferences changed; close Brave and retry')
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def setup(profile_name):
    require(os.getuid() != 0 and os.geteuid() == os.getuid(), 'Run as the desktop owner, without sudo')
    require(platform.system() == 'Linux' and platform.machine() in ('aarch64', 'arm64'),
            'This component requires Linux ARM64')
    home = Path.home()
    config = Path(os.environ.get('XDG_CONFIG_HOME') or home / '.config')
    # The dedicated hardware launcher uses this fixed directory even with XDG_CONFIG_HOME set.
    if profile_name == 'Brave-Browser-M4':
        config = home / '.config'
    profile = config / 'BraveSoftware' / profile_name
    owned(profile, home, directory=True)
    browser_closed(profile)
    state_path = profile / 'Local State'
    original = read_file(state_path, home)
    state = json_value(original)
    require(isinstance(state, dict) and isinstance(state.get('brave', {}), dict), 'Unexpected Local State schema')
    value = state.get('brave', {}).get('widevine_opted_in', False)
    require(isinstance(value, bool), 'Unexpected Widevine preference value')
    store = Path(os.environ.get('XDG_DATA_HOME') or home / '.local/share') / 'omarchy/widevine'
    component = store / VERSION
    marker = profile / 'WidevineCdm/latest-component-updated-widevine-cdm'
    owned(marker, home, missing=True)
    if marker.exists():
        require(json_value(read_file(marker, home)) == {'Path': str(component)},
                'Another Widevine registration exists; leaving it untouched')
    owned(component, home, directory=True, missing=True)
    if component.exists():
        installed(component, home)
    else:
        files = unpack(download())
        browser_closed(profile)
        require(read_file(state_path, home) == original, 'Preferences changed during download; retry after closing Brave')
        directories(store, home)
        with tempfile.TemporaryDirectory(prefix='.' + VERSION + '.', dir=store) as temporary:
            staging = Path(temporary) / VERSION
            staging.mkdir(mode=0o700)
            for name, content in files.items():
                dest = staging / name
                dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                dest.write_bytes(content)
                dest.chmod(0o755 if name == LIBRARY else 0o644)
            require(not component.exists() and not component.is_symlink(), 'Component appeared; refusing overwrite')
            staging.rename(component)
        installed(component, home)
    browser_closed(profile)
    require(read_file(state_path, home) == original, 'Preferences changed; close Brave and retry')
    if not marker.exists():
        directories(marker.parent, home)
        atomic(marker, (json.dumps({'Path': str(component)}) + '\n').encode(), home)
    if value is not True:
        browser_closed(profile)
        state.setdefault('brave', {})['widevine_opted_in'] = True
        with tempfile.NamedTemporaryFile(prefix='Local State.before-widevine-', dir=profile, delete=False) as backup:
            backup.write(original)
            backup.flush()
            os.fsync(backup.fileno())
        atomic(state_path, (json.dumps(state, ensure_ascii=False, indent=2) + '\n').encode(), home,
               previous=original, mode=stat.S_IMODE(state_path.stat().st_mode))
    print('Widevine ' + VERSION + ' registered for ' + profile_name + '. Start Brave normally.')


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', required=True, choices=('Brave-Browser', 'Brave-Browser-M4'))
    args = parser.parse_args(arguments)
    previous_mask = os.umask(0o077)
    try:
        setup(args.profile)
    except (OSError, ValueError, KeyError, StopIteration, zipfile.BadZipFile) as error:
        parser.exit(1, 'Widevine setup refused: ' + str(error) + '\n')
    finally:
        os.umask(previous_mask)


if __name__ == '__main__':
    main()
