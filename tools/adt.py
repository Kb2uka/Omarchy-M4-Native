#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Standalone Apple Device Tree (ADT) binary parser. No m1n1 code.

Format: node = u32 nprops, u32 nchildren, then nprops x (char name[32],
u32 len (bit31 = placeholder), data padded to 4), then children.
"""
import struct, sys

def parse(buf, off=0):
    nprops, nchild = struct.unpack_from('<II', buf, off); off += 8
    props = {}
    for _ in range(nprops):
        name = buf[off:off+32].split(b'\0',1)[0].decode('ascii','replace'); off += 32
        ln, = struct.unpack_from('<I', buf, off); off += 4
        ln &= 0x7fffffff
        props[name] = bytes(buf[off:off+ln]); off += (ln + 3) & ~3
    kids = []
    for _ in range(nchild):
        k, off = parse(buf, off); kids.append(k)
    return {'props': props, 'children': kids}, off

def name(n): return n['props'].get('name', b'?').split(b'\0')[0].decode()

def walk(n, path=''):
    p = path + '/' + name(n) if path or name(n) != 'device-tree' else ''
    yield p or '/', n
    for k in n['children']:
        yield from walk(k, p)

def fmt(v):
    s = v.rstrip(b'\0')
    if s and all(32 <= c < 127 for c in s) and b'\0' not in s:
        return '"' + s.decode() + '"'
    if len(v) % 4 == 0 and 0 < len(v) <= 64:
        return '<' + ' '.join('0x%x' % x for x in struct.unpack('<%dI' % (len(v)//4), v)) + '>'
    return '[%d bytes] %s' % (len(v), v[:32].hex())

def regs(v, acells=2, scells=2):
    n = (acells+scells)*4; out=[]
    for i in range(0, len(v) - len(v) % n, n):
        w = struct.unpack('<%dI' % (n//4), v[i:i+n])
        a = sum(w[j] << (32*j) for j in range(acells)); s = sum(w[acells+j] << (32*j) for j in range(scells))
        out.append((a, s))
    return out

def u32s(v):
    """Decode an ADT byte property as little-endian 32-bit cells."""
    if len(v) % 4:
        raise ValueError("property length is not a multiple of four")
    return list(struct.unpack('<%dI' % (len(v) // 4), v))

def strings(v):
    """Decode an ADT NUL-separated string list."""
    return [s.decode('ascii', 'replace') for s in v.rstrip(b'\0').split(b'\0') if s]

def find_path(root, wanted):
    """Return the node at an exact ADT path, or None."""
    for path, node in walk(root):
        if path == wanted:
            return node
    return None

def phandles(root):
    """Return an AAPL,phandle integer to (path, node) mapping."""
    out = {}
    for path, node in walk(root):
        raw = node['props'].get('AAPL,phandle')
        if raw is not None and len(raw) == 4:
            out[u32s(raw)[0]] = (path, node)
    return out

if __name__ == '__main__':
    buf = open(sys.argv[1],'rb').read()
    root, _ = parse(buf)
    mode = sys.argv[2] if len(sys.argv) > 2 else 'dump'
    if mode == 'dump':
        for p, n in walk(root):
            print('==', p)
            for k, v in n['props'].items():
                if k == 'name': continue
                if k == 'reg' and len(v) % 16 == 0 and v:
                    print('   reg =', ', '.join('0x%x/0x%x' % r for r in regs(v)))
                elif k == 'interrupts' and len(v) % 4 == 0:
                    print('   interrupts =', list(struct.unpack('<%dI' % (len(v)//4), v)))
                else:
                    print('   %s = %s' % (k, fmt(v)))
    else:
        for p, n in walk(root):
            if mode in p:
                print('==', p)
                for k, v in n['props'].items():
                    if k == 'name': continue
                    if k == 'reg' and len(v) % 16 == 0 and v:
                        print('   reg =', ', '.join('0x%x/0x%x' % r for r in regs(v)))
                    elif k == 'interrupts' and len(v) % 4 == 0:
                        print('   interrupts =', list(struct.unpack('<%dI' % (len(v)//4), v)))
                    else:
                        print('   %s = %s' % (k, fmt(v)))
