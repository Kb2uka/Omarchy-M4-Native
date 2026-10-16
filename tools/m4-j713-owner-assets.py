#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 studioxradio aka kb2uka
"""Carry the owner's protected-media choice into first boot."""
import argparse
import json
import os
from pathlib import Path
import stat
import tempfile

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


def prepare_for_installer():
    """Validate the owner's choice before the first partition operation."""
    widevine = os.environ.get('M4_OWNER_WIDEVINE', '')
    require(widevine in ('yes', 'no'),
            'Launch this installer through bash Install.command --install to prepare owner options')
    return widevine == 'yes'


def add_to_firmware(package, widevine):
    """Carry the explicit protected-media choice in the owner's firmware package."""
    from gravity_firmware.core import FWFile
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


def owner_options(raw):
    options = json.loads(raw)
    require(isinstance(options, dict) and set(options) == {'schema', 'widevine'}
            and type(options['schema']) is int and options['schema'] == 1
            and type(options['widevine']) is bool, 'Invalid owner options')
    return options['widevine']


def install_options(widevine, pending):
    """Preflight all browser requests before atomically installing any of them."""
    destinations = {}
    for profile in PROFILES:
        destination = pending / ('widevine-' + profile + '.json')
        if widevine:
            destinations[destination] = encoded({'schema': 1, 'profile': profile,
                                                  'consent': 'owner-requested'})
        else:
            require(not destination.exists() and not destination.is_symlink(),
                    'Protected-media request exists without owner consent')
    for path, data in destinations.items():
        existing(path, data)
    for path, data in destinations.items():
        install_file(path, data, 0o600)


def install():
    require(os.getuid() == 0 and os.geteuid() == 0, 'Run through the system service')
    tags = Path('/sys/firmware/devicetree/base/compatible').read_bytes().split(b'\0')
    require(b'apple,j713' in tags and b'apple,t8132' in tags, 'This service requires J713/T8132')
    raw = read_regular(Path('/usr/lib/firmware') / OPTIONS, 4096, True)
    install_options(owner_options(raw), Path('/etc/skel/.config/omarchy/pending'))
    print('J713 owner options ready.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['install'])
    parser.parse_args()
    install()


if __name__ == '__main__':
    main()
