#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Add the live J713 TrustCache reservation around an existing watchdog loader."""

import argparse
import hashlib
import json
from pathlib import Path
import runpy
import struct
import sys

PAGE = 0x4000
MAX_DTB = 2 * 1024 * 1024
D_LOWER = (3, 0, 11, 0, 2)
D_UPPER = (3, 0, 11, 0, 3)


def checked_range(value, name):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"invalid {name} range")
    address, size = value
    if any(not isinstance(x, int) or isinstance(x, bool) for x in value) or address < 0 or size <= 0 or address + size > 1 << 64:
        raise ValueError(f"invalid {name} extent")
    return address, size


def live_range(proxy, utils):
    if proxy.get_chipid() != 0x8132 or utils.adt['/product'].unique_model != 'J713AP':
        raise ValueError('only the validated J713/T8132 target is supported')
    if proxy.read32(0x3882B001C) != 0:
        raise ValueError('watchdog must still be disabled before DT preparation')
    memory = utils.adt['/chosen/memory-map']._properties
    adt, adt_size = checked_range(memory['DeviceTree'], 'ADT')
    start, size = checked_range(memory['TrustCache'], 'TrustCache')
    image, image_size = checked_range(memory['BootKC-rw'], 'boot image')
    physical, total = checked_range((utils.ba.phys_base, utils.ba.mem_size), 'RAM')
    lower, upper = utils.mrs(D_LOWER), utils.mrs(D_UPPER)
    if not (adt + adt_size == start and start + size == image):
        raise ValueError('TrustCache is not between ADT and the boot image')
    if not (physical <= adt < image + image_size <= physical + total):
        raise ValueError('boot allocations are outside reported RAM')
    if any(x % PAGE for x in (adt, adt_size, start, size, image, image_size)) or size > 16 * 1024 * 1024:
        raise ValueError('unexpected allocation alignment or TrustCache size')
    # The observed D upper bound is the start of the last protected 4 KiB page.
    if lower != adt or upper != start + size - 0x1000:
        raise ValueError('CTRR_D bounds do not match the complete ADT/TrustCache allocation')
    return start, size


def dtb_layout(data):
    if not 40 <= len(data) <= MAX_DTB:
        raise ValueError('invalid DTB length')
    fields = list(struct.unpack_from('>10I', data))
    magic, total, tree, strings, reservations, version, compatible, _, strings_size, tree_size = fields
    if magic != 0xD00DFEED or total != len(data) or version != 17 or compatible != 16:
        raise ValueError('expected a complete version-17 DTB')
    if reservations % 8 or tree % 4 or tree_size % 4:
        raise ValueError('unaligned DTB blocks')
    if not (40 <= reservations and reservations + 16 <= tree and tree + tree_size <= strings
            and strings + strings_size <= total and tree_size >= 16):
        raise ValueError('overlapping or out-of-range DTB blocks')
    if struct.unpack_from('>I', data, tree + tree_size - 4)[0] != 9:
        raise ValueError('structure has no final END token')
    return fields


def root_compatible(data, fields):
    tree, strings, strings_size, tree_size = fields[2], fields[3], fields[8], fields[9]
    end = tree + tree_size
    position = tree
    while position + 4 <= end and struct.unpack_from('>I', data, position)[0] == 4:
        position += 4
    if position + 8 > end or data[position:position + 8] != b'\0\0\0\1\0\0\0\0':
        raise ValueError('invalid root node')
    position += 8
    values = None
    while position + 4 <= end:
        token = struct.unpack_from('>I', data, position)[0]
        position += 4
        if token in (1, 2):
            break
        if token == 4:
            continue
        if token != 3 or position + 8 > end:
            raise ValueError('invalid root property token')
        length, name = struct.unpack_from('>II', data, position)
        position += 8
        padded = (length + 3) & ~3
        if position + padded > end or name >= strings_size:
            raise ValueError('out-of-range root property')
        terminator = data.find(b'\0', strings + name, strings + strings_size)
        if terminator < 0:
            raise ValueError('unterminated property name')
        if data[strings + name:terminator] == b'compatible':
            if values is not None or not length or data[position + length - 1] != 0:
                raise ValueError('invalid or duplicate root compatible property')
            values = data[position:position + length - 1].split(b'\0')
        position += padded
    if not values or b'apple,j713' not in values or b'apple,t8132' not in values:
        raise ValueError('DTB does not identify J713/T8132')


def reserve(data, start, size):
    start, size = checked_range((start, size), 'reservation')
    if start % PAGE or size % PAGE:
        raise ValueError('reservation must use 16 KiB pages')
    fields = dtb_layout(data)
    root_compatible(data, fields)
    position, tree = fields[4], fields[2]
    entries = []
    while position + 16 <= tree:
        address, length = struct.unpack_from('>QQ', data, position)
        if (address, length) == (0, 0):
            break
        entries.append(checked_range((address, length), 'existing reservation'))
        position += 16
    else:
        raise ValueError('unterminated memory reservation block')
    previous_end = 0
    covered = False
    for address, length in sorted(entries):
        if address < previous_end:
            raise ValueError('existing reservations overlap')
        previous_end = address + length
        if address <= start and start + size <= previous_end:
            covered = True
        elif max(address, start) < min(previous_end, start + size):
            raise ValueError('TrustCache partially overlaps an existing reservation')
    if covered:
        return data
    result = bytearray(data[:position] + struct.pack('>QQ', start, size) + data[position:])
    fields[1] += 16
    fields[2] += 16
    fields[3] += 16
    struct.pack_into('>10I', result, 0, *fields)
    return bytes(result)


def prepare(proxy, utils, address, original, evidence):
    start, size = live_range(proxy, utils)
    header = utils.iface.readmem(address, 40)
    if len(header) != 40:
        raise ValueError('short DTB header read')
    total = struct.unpack_from('>I', header, 4)[0]
    if not 40 <= total <= MAX_DTB:
        raise ValueError('invalid target DTB size')
    source = utils.iface.readmem(address, total)
    output = reserve(source, start, size)
    # Exclusive creation prevents accidental replacement of evidence from another boot.
    with evidence.open('xb') as saved:
        saved.write(output)
    destination = utils.malloc(len(output))
    utils.iface.writemem(destination, output)
    if utils.iface.readmem(destination, len(output)) != output:
        raise RuntimeError('reserved DTB upload verification failed')
    print('TRUSTCACHE_RESERVATION ' + json.dumps({
        'address': hex(start), 'size': hex(size), 'evidence': str(evidence),
        'input_sha256': hashlib.sha256(source).hexdigest(),
        'output_sha256': hashlib.sha256(output).hexdigest(),
    }), flush=True)
    return original(proxy, destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--proxyclient', type=Path, required=True)
    parser.add_argument('--watchdog-runtime', type=Path, required=True)
    parser.add_argument('--output-dtb', type=Path, required=True)
    parser.add_argument('runtime_args', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    forwarded = args.runtime_args
    if forwarded[:1] == ['--']:
        forwarded = forwarded[1:]
    options = {argument.partition('=')[0] for argument in forwarded}
    if '--arm' not in forwarded or '--kernel' not in options or '--dtb' not in options:
        parser.error('forward a watchdog-protected Linux boot after --')
    if '--proxyclient' in options:
        parser.error('--proxyclient is provided by this wrapper')
    if not args.watchdog_runtime.is_file() or not (args.proxyclient / 'm1n1/proxy.py').is_file():
        parser.error('existing watchdog runtime and proxyclient are required')
    if args.output_dtb.exists() or not args.output_dtb.parent.is_dir():
        parser.error('output DTB must be a new file in an existing directory')
    sys.path.insert(0, str(args.proxyclient.resolve()))
    from m1n1.proxy import M1N1Proxy
    original = M1N1Proxy.kboot_prepare_dt
    previous_argv = sys.argv
    def guarded(proxy, address):
        setup = sys.modules['m1n1.setup']
        return prepare(proxy, setup.u, address, original, args.output_dtb)
    try:
        M1N1Proxy.kboot_prepare_dt = guarded
        sys.argv = [str(args.watchdog_runtime), '--proxyclient', str(args.proxyclient), *forwarded]
        runpy.run_path(sys.argv[0], run_name='__main__')
    finally:
        M1N1Proxy.kboot_prepare_dt = original
        sys.argv = previous_argv


if __name__ == '__main__':
    main()
