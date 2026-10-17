#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Rebuild a binary Apple Device Tree from macOS's IODeviceTree text dump.

Usage: ioreg-adt.py <ioreg-text> <out.adt>
The input is `ioreg -p IODeviceTree -l -w0`, which needs no recoveryOS and no
m1n1. The output has the format tools/adt.py reads, so tools/adt-audit.py and
m1n1's proxyclient tools can run on a Mac before anything is installed on it.

It is a reconstruction, not a capture. Only properties ioreg prints as data
are kept (<hex> or <"a","b">); values IOKit adds at runtime (numbers, arrays,
dictionaries) are dropped, and so are nodes ioreg shows without a "name": IOKit
driver objects, and /options, whose properties IODTNVRAM replaces with NVRAM
variables.
A string rendering <"a","b"> becomes b"a\\0b\\0", so a binary value that
happened to look like strings comes back with a different layout. macOS zeroes
some nodes (memory-map); a zeroed name is replaced with the ioreg node name.
The ADT m1n1 captures at boot stays the reference.
"""
import re
import struct
import sys

NODE = re.compile(r'^([ |]*)\+-o (\S+)')
PROP = re.compile(r'^[ |]*"([^"]+)" = (.*)$')
HEX = re.compile(r'<([0-9a-f]*)>')
STRINGS = re.compile(r'<("[^"]*"(?:,"[^"]*")*)>')


def parse_text(lines):
    """The ioreg tree: each node is {'name', 'props': {key: raw text}, 'children'}."""
    root = {'name': '', 'props': {}, 'children': [], 'depth': -1}
    stack = [root]
    for line in lines:
        line = line.rstrip('\n')
        match = NODE.match(line)
        if match:
            depth = len(match.group(1))
            while stack[-1]['depth'] >= depth:
                stack.pop()
            node = {'name': match.group(2), 'props': {}, 'children': [], 'depth': depth}
            stack[-1]['children'].append(node)
            stack.append(node)
            continue
        match = PROP.match(line)
        if match and len(stack) > 1:
            stack[-1]['props'].setdefault(match.group(1), match.group(2))
    return root


def value(text):
    """The bytes of a property ioreg printed as data, or None for IOKit values."""
    match = HEX.fullmatch(text)
    if match:
        return bytes.fromhex(match.group(1))
    match = STRINGS.fullmatch(text)
    if match:
        return b''.join(part.encode('latin-1') + b'\0' for part in re.findall(r'"([^"]*)"', match.group(1)))
    return None


def is_adt_node(node):
    return 'name' in node['props']


def encode(node):
    props = []
    for key, text in node['props'].items():
        data = value(text)
        # The name field is 32 bytes ending in NUL; measure the encoded name, not its characters.
        if data is None or len(key.encode()) >= 32:
            continue
        if key == 'name' and not data.strip(b'\0'):
            data = node['name'].split('@')[0].encode() + b'\0'
        props.append((key, data))
    children = [child for child in node['children'] if is_adt_node(child)]
    out = bytearray(struct.pack('<II', len(props), len(children)))
    for key, data in props:
        out += key.encode().ljust(32, b'\0') + struct.pack('<I', len(data)) + data + b'\0' * (-len(data) % 4)
    for child in children:
        out += encode(child)
    return bytes(out)


def device_tree(root):
    stack = [root]
    while stack:
        node = stack.pop()
        for child in node['children']:
            if child['name'] == 'device-tree':
                return child
            stack.append(child)
    raise ValueError('no device-tree node: is this an IODeviceTree-plane dump?')


def main(argv):
    if len(argv) != 3:
        print(__doc__.split('\n\n')[1], file=sys.stderr)
        return 2
    with open(argv[1], errors='replace') as source:
        tree = device_tree(parse_text(source))
    with open(argv[2], 'wb') as out:
        out.write(encode(tree))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
