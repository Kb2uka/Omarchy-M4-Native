#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 studioxradio aka kb2uka
"""Carry locally generated J713 filters and the owner's choice into first boot."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

FIRS = {
    'j713-static-minimal-lane0-48000.wav': (16440, 'cddb9a1c2aa29a8271526ca954ddbcdc15dcd09e6f546b72c45b2643f7d3e484'),
    'j713-static-minimal-lane1-48000.wav': (16440, 'c398560bb45bc12932b4c9516ed5b9812aeeefb64f9bdb4f28814c0ea2f6a188'),
    'j713-static-minimal-lane2-48000.wav': (4152, 'c02bbed34a37a496103cb75ede1ccaa872ac6bb2dff0023b8601397cd582d5f6'),
    'j713-static-minimal-lane3-48000.wav': (4152, 'c02bbed34a37a496103cb75ede1ccaa872ac6bb2dff0023b8601397cd582d5f6'),
}
FIRMWARE = 'apple/j713-owner-audio'
OPTIONS = 'omarchy/j713-owner-options.json'
PROFILES = ('Brave-Browser', 'Brave-Browser-M4', 'Chromium')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def encoded(value):
    return (json.dumps(value, sort_keys=True) + '\n').encode('ascii')


def read_regular(path, limit, root_owned=False):
    require(path.is_absolute() and path.resolve() == path, 'Non-canonical input: ' + str(path))
    if root_owned:
        for parent in (path, *path.parents):
            info = parent.lstat()
            require(info.st_uid == 0 and not info.st_mode & 0o022,
                    'Writable or foreign input: ' + str(parent))
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_size <= limit,
                'Invalid input size or type: ' + str(path))
        result = stream.read(limit + 1)
    require(len(result) <= limit, 'Input grew beyond its limit')
    return result


def filters(directory, root_owned=False):
    result = {}
    for name, (size, pin) in FIRS.items():
        data = read_regular(directory / name, size, root_owned)
        require(len(data) == size and hashlib.sha256(data).hexdigest() == pin,
                'Unqualified speaker filter: ' + name)
        result[name] = data
    return result


def prepare_for_installer():
    """Read all owner inputs before the installer's first partition operation."""
    directory = os.environ.get('M4_OWNER_FIRS', '')
    widevine = os.environ.get('M4_OWNER_WIDEVINE', '')
    require(directory and widevine in ('yes', 'no'),
            'Launch this installer through bash Install.command --install to prepare owner inputs')
    payload = filters(Path(directory))
    return payload, widevine == 'yes'


def add_to_firmware(package, prepared):
    """Called by the Mac installer; no generated filter is in the download."""
    from gravity_firmware.core import FWFile
    payload, widevine = prepared
    for name, data in payload.items():
        destination = FIRMWARE + '/' + name
        package.add_file(destination, FWFile(destination, data))
    package.add_file(OPTIONS, FWFile(OPTIONS, encoded({'schema': 1, 'widevine': widevine})))


def trusted_directory(path):
    require(path.is_absolute(), 'Absolute destination required')
    for parent in reversed((path, *path.parents)):
        if not parent.exists() and not parent.is_symlink():
            parent.mkdir(mode=0o755)
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
                'Untrusted destination directory: ' + str(parent))


def existing(path, expected):
    if path.exists() or path.is_symlink():
        require(read_regular(path, len(expected), True) == expected,
                'Existing owner asset differs: ' + str(path))
        return True
    return False


def install_file(path, data, mode):
    trusted_directory(path.parent)
    if existing(path, data):
        return
    name = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.owner-', delete=False) as stream:
            name = stream.name
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        require(not path.exists() and not path.is_symlink(), 'Destination appeared: ' + str(path))
        os.replace(name, path)
        name = None
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if name is not None:
            os.unlink(name)


def install():
    require(os.getuid() == 0 and os.geteuid() == 0, 'Run through the system service')
    tags = Path('/sys/firmware/devicetree/base/compatible').read_bytes().split(b'\0')
    require(b'apple,j713' in tags and b'apple,t8132' in tags, 'This service requires J713/T8132')
    firmware = Path('/usr/lib/firmware')
    payload = filters(firmware / FIRMWARE, True)
    raw = read_regular(firmware / OPTIONS, 4096, True)
    options = json.loads(raw)
    require(isinstance(options, dict) and set(options) == {'schema', 'widevine'}
            and type(options['schema']) is int and options['schema'] == 1
            and type(options['widevine']) is bool, 'Invalid owner options')
    destinations = {Path('/usr/share/asahi-audio/j713') / name: (data, 0o644)
                    for name, data in payload.items()}
    pending = Path('/etc/skel/.config/omarchy/pending')
    for profile in PROFILES:
        destination = pending / ('widevine-' + profile + '.json')
        if options['widevine']:
            destinations[destination] = (encoded({'schema': 1, 'profile': profile,
                                                  'consent': 'owner-requested'}), 0o600)
        else:
            require(not destination.exists() and not destination.is_symlink(),
                    'Protected-media request exists without owner consent')
    # Check every pre-existing payload before writing any new one.
    for path, (data, _) in destinations.items():
        existing(path, data)
    for path, (data, mode) in destinations.items():
        install_file(path, data, mode)
    print('J713 owner assets ready; speaker filter identities preserved.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['install'])
    parser.parse_args()
    install()


if __name__ == '__main__':
    main()
