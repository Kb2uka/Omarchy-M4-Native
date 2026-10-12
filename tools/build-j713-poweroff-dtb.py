#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Add the SMC poweroff child to the exact retained portable J713 DTB."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile

BASE_SHA256 = '3ba58a528c3c1f07bbde6704231689a8a9eba1b4a3cc9a73f62654544b11e5e3'
SMC = '/soc/smc@38c600000'
CHILD = SMC + '/reboot'


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def tree(data):
    """Read nodes, raw properties and reservations without normalizing values."""
    require(len(data) >= 40, 'Truncated DTB header')
    magic, total, start, strings, reserved, version, last, cpu, strsize, size = struct.unpack_from('>10I', data)
    require(magic == 0xd00dfeed and total == len(data) and version == 17 and last == 16,
            'Unexpected DTB header')
    require(start + size <= total and strings + strsize <= total, 'Invalid DTB section bounds')
    reservations = []
    pos = reserved
    while True:
        require(pos + 16 <= total, 'Truncated reservation table')
        entry = struct.unpack_from('>QQ', data, pos)
        pos += 16
        reservations.append(entry)
        if entry == (0, 0):
            break
    nodes, stack, pos = {}, [], start
    while pos < start + size:
        token, = struct.unpack_from('>I', data, pos)
        pos += 4
        if token == 1:
            end = data.index(0, pos, start + size)
            stack.append(data[pos:end].decode('ascii'))
            pos = (end + 4) & ~3
            path = '/'.join(stack) or '/'
            require(path not in nodes, 'Duplicate node')
            nodes[path] = {}
        elif token == 2:
            require(bool(stack), 'Unbalanced tree')
            stack.pop()
        elif token == 3:
            length, offset = struct.unpack_from('>II', data, pos)
            pos += 8
            require(stack and pos + length <= start + size and offset < strsize,
                    'Invalid property bounds')
            end = data.index(0, strings + offset, strings + strsize)
            name = data[strings + offset:end].decode('ascii')
            properties = nodes['/'.join(stack) or '/']
            require(name not in properties, 'Duplicate property')
            properties[name] = data[pos:pos + length]
            pos = (pos + length + 3) & ~3
        elif token == 4:
            continue
        elif token == 9:
            require(not stack, 'Unclosed node')
            return nodes, reservations, cpu
        else:
            raise ValueError('Unknown DTB token')
    raise ValueError('Missing DTB end')


def build(base, output, overlay, dtc='dtc', fdtoverlay='fdtoverlay'):
    require(base.is_file() and not base.is_symlink(), 'Base must be a regular file')
    original = base.read_bytes()
    require(sha(original) == BASE_SHA256, 'Unqualified portable base DTB')
    require(overlay.is_file() and not overlay.is_symlink(), 'Overlay must be a regular file')
    require(not output.exists() and not output.is_symlink(), 'Output must not exist')
    require(output.parent.is_dir(), 'Output directory must exist')
    old, reservations, cpu = tree(original)
    require(b'apple,j713' in old['/']['compatible'].split(b'\0') and
            b'apple,t8132' in old['/']['compatible'].split(b'\0'), 'Wrong board')
    require(old[SMC].get('status', b'okay\0') in (b'okay\0', b'ok\0'), 'SMC disabled')
    require(CHILD not in old, 'Reboot child already exists')
    with tempfile.TemporaryDirectory(prefix='j713-poweroff-', dir=output.parent) as scratch:
        compiled = Path(scratch) / 'poweroff.dtbo'
        candidate = Path(scratch) / 'candidate.dtb'
        subprocess.run([dtc, '-I', 'dts', '-O', 'dtb', '-o', str(compiled), str(overlay)], check=True)
        subprocess.run([fdtoverlay, '-i', str(base), '-o', str(candidate), str(compiled)], check=True)
        data = candidate.read_bytes()
        new, new_reservations, new_cpu = tree(data)
        expected = dict(old)
        expected[CHILD] = {'compatible': b'apple,smc-reboot\0'}
        require(new == expected and new_reservations == reservations and new_cpu == cpu,
                'Candidate changed data beyond the single SMC reboot child')
        require(base.read_bytes() == original, 'Base changed during build')
        with output.open('xb') as stream:
            stream.write(data)
    return {'base_sha256': sha(original), 'output_sha256': sha(data), 'output_bytes': len(data),
            'overlay_sha256': sha(overlay.read_bytes()), 'added_node': CHILD,
            'other_properties_reservations_and_boot_cpu_unchanged': True,
            'kernel_changed': False, 'hardware_poweroff_verified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--overlay', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'patches/j713-smc-poweroff.dtso')
    args = parser.parse_args()
    print(json.dumps(build(args.base, args.output, args.overlay), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
