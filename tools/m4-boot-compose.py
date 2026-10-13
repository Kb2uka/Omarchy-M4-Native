#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Compose the installed J713 boot.bin from the packaged kernel and devicetree.

boot.bin is per machine: the m1n1 loader, the boot arguments and the initramfs
(which carries this owner's Apple firmware) are written once by the installer.
Only the kernel and devicetree segments are replaced from package files; every
other byte is kept. Any failed check leaves boot.bin untouched. Inspection is
the default; --apply writes, keeping the replaced file as boot.bin.previous.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import zlib

# Kernels and devicetrees an installed alpha.1 machine may boot, by SHA-256.
# A kernel maps to its module release. Adding one requires the protected audio,
# video and GPU stacks to be requalified for it (docs/package-channel.md).
KNOWN_KERNELS = {
    'c3704cfd2afb7760e73f0b9939a68864a30bd916fcd478bd960e538373123dc9': '7.1.9-m4-gpu-backport-20261008',
}
# Devicetrees in lineage order. A machine whose boot.bin carries a later one than
# the package keeps it: an owner-applied devicetree update is never reverted.
KNOWN_DTBS = {
    '67dc61458b03c0fad1156994ad85a85f9f8612cac2996eb684609b0455ebf547': 'v0.1.0-alpha.1',
    'e54c77a760a5b696486f9d80c7c83ca4295d641113bb9e54d13e259863bf9b07': 'v0.1.0-alpha.1-update.1',
    '033025bbdda3ce72b5eefe39d1d6d68209256fd43e4fd587c96ae1cf38978555': 'j713-cpufreq',
}
BOARD = b'apple,j713'
BOOT = 'boot/efi/omarchy/boot.bin'
KERNEL = 'usr/lib/omarchy-m4/Image.gz'
DTB = 'usr/lib/omarchy-m4/j713.dtb'
VIDEO_MANIFEST = 'etc/omarchy/j713-video-manifest.json'
AUDIO_MANIFEST = 'etc/omarchy/j713-audio-manifest.json'
COMPATIBLE = 'sys/firmware/devicetree/base/compatible'
PREVIOUS = '.previous'
BOOTARGS = b'chosen.bootargs='
GZIP = b'\x1f\x8b\x08'
FDT_MAGIC = b'\xd0\x0d\xfe\xed'
INITRAMFS = b'm1n1_initramfs'
MARGIN = 1 << 20


class Refused(Exception):
    pass


def require(condition, message):
    if not condition:
        raise Refused(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def gzip_end(data, start):
    """Return the end offset of the one checked gzip member starting at start, or None."""
    stream = zlib.decompressobj(31)
    try:
        stream.decompress(memoryview(data)[start:])
    except zlib.error:
        return None
    if not stream.eof:
        return None
    return len(data) - len(stream.unused_data)


def fdt_size(data, offset):
    """Return the devicetree size at offset, or None when no valid header is there."""
    if data[offset:offset + 4] != FDT_MAGIC or offset + 40 > len(data):
        return None
    total, structure, strings, _, version, last = struct.unpack_from('>6I', data, offset + 4)
    if not (40 <= total <= len(data) - offset and version >= 16 and last <= 16 and
            structure < total and strings < total):
        return None
    return total


def parse_at(data, start):
    """Split boot.bin when its boot arguments start at start, else return None."""
    end = data.find(b'\n', start)
    if start == 0 or end < 0:
        return None
    line = data[start:end + 1]
    if not all(32 <= byte < 127 for byte in line[:-1]):
        return None
    kernel = end + 1
    if data[kernel:kernel + 3] != GZIP:
        return None
    dtb = gzip_end(data, kernel)
    if dtb is None:
        return None
    size = fdt_size(data, dtb)
    if size is None:
        return None
    tag = dtb + size
    if data[tag:tag + len(INITRAMFS)] != INITRAMFS or tag + len(INITRAMFS) + 4 > len(data):
        return None
    length, = struct.unpack_from('<I', data, tag + len(INITRAMFS))
    if tag + len(INITRAMFS) + 4 + length != len(data):
        return None
    return {'loader': data[:start], 'bootargs': line, 'kernel': data[kernel:dtb],
            'dtb': data[dtb:tag], 'initramfs': data[tag:]}


def parse(data):
    """Return the five boot.bin segments; exactly one layout must consume the file."""
    found, position = [], data.find(BOOTARGS)
    while position >= 0:
        layout = parse_at(data, position)
        if layout is not None:
            found.append(layout)
        position = data.find(BOOTARGS, position + 1)
    require(len(found) == 1, 'boot.bin does not have the expected loader, boot arguments, '
            'kernel, devicetree and initramfs layout')
    return found[0]


def join(layout):
    return b''.join(layout[name] for name in ('loader', 'bootargs', 'kernel', 'dtb', 'initramfs'))


def read_regular(path, what):
    require(path.is_file() and not path.is_symlink(), what + ' is missing: ' + str(path))
    return path.read_bytes()


def manifest(root, name):
    try:
        value = json.loads(read_regular(root / name, 'Protected stack manifest'))
    except ValueError:
        raise Refused('Protected stack manifest is not valid JSON: ' + str(root / name))
    require(isinstance(value, dict), 'Protected stack manifest is not an object: ' + str(root / name))
    return value


def check_protected(root, kernel_sha):
    """The audio and video stacks are pinned to one kernel; never boot past them."""
    release = KNOWN_KERNELS[kernel_sha]
    video, audio = manifest(root, VIDEO_MANIFEST), manifest(root, AUDIO_MANIFEST)
    require(video.get('boot_kernel_sha256') == kernel_sha and video.get('kernel_release') == release,
            'The installed video stack is not qualified for kernel ' + kernel_sha)
    require(audio.get('kernel_release') == release,
            'The installed audio stack is not qualified for kernel release ' + release)
    modules = root / 'usr/lib/modules' / release
    require(modules.is_dir() and not modules.is_symlink(), 'Kernel modules are missing: ' + str(modules))


def plan(root):
    """Return (current bytes, desired bytes, receipt) for the installed machine."""
    compatible = read_regular(root / COMPATIBLE, 'Devicetree compatible list')
    require(BOARD in compatible.split(b'\0'), 'This is not a J713 MacBook Air')
    boot_path = root / BOOT
    current = read_regular(boot_path, 'boot.bin (is the EFI partition mounted at /boot/efi?)')
    kernel = read_regular(root / KERNEL, 'Packaged kernel')
    dtb = read_regular(root / DTB, 'Packaged devicetree')
    layout = parse(current)
    now = {'kernel': sha(layout['kernel']), 'dtb': sha(layout['dtb'])}
    require(now['kernel'] in KNOWN_KERNELS, 'boot.bin boots an unrecognized kernel ' + now['kernel'])
    require(now['dtb'] in KNOWN_DTBS, 'boot.bin carries an unrecognized devicetree ' + now['dtb'])
    new = {'kernel': sha(kernel), 'dtb': sha(dtb)}
    require(new['kernel'] in KNOWN_KERNELS, 'Packaged kernel is not a known J713 kernel ' + new['kernel'])
    require(new['dtb'] in KNOWN_DTBS, 'Packaged devicetree is not a known J713 devicetree ' + new['dtb'])
    require(gzip_end(kernel, 0) == len(kernel), 'Packaged kernel is not one complete gzip stream')
    require(fdt_size(dtb, 0) == len(dtb), 'Packaged devicetree has an invalid header')
    lineage = list(KNOWN_DTBS)
    if lineage.index(now['dtb']) > lineage.index(new['dtb']):
        dtb, new['dtb'] = layout['dtb'], now['dtb']
    check_protected(root, new['kernel'])
    desired = join({**layout, 'kernel': kernel, 'dtb': dtb})
    result = parse(desired)
    require(all(result[name] == layout[name] for name in ('loader', 'bootargs', 'initramfs')) and
            result['kernel'] == kernel and result['dtb'] == dtb, 'Composed boot.bin verification failed')
    receipt = {'boot': str(boot_path), 'boot_sha256': sha(current), 'kernel_sha256': new['kernel'],
               'kernel_release': KNOWN_KERNELS[new['kernel']], 'devicetree_sha256': new['dtb'],
               'devicetree_release': KNOWN_DTBS[new['dtb']], 'applied': False}
    if desired == current:
        receipt['status'] = 'current'
    else:
        receipt.update(status='update available', new_boot_sha256=sha(desired),
                       changes=sorted(name for name in now if now[name] != new[name]))
    return current, desired, receipt


def write(path, data):
    """Write a verified temporary copy, then rename it over path atomically."""
    temp = path.with_name(path.name + '.new')
    if temp.exists() or temp.is_symlink():
        temp.unlink()
    with temp.open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    require(temp.read_bytes() == data, 'Temporary copy does not match: ' + str(temp))
    os.replace(temp, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def replace(boot_path, current, desired):
    """Keep current as boot.bin.previous, then replace boot.bin; both verified."""
    previous = boot_path.with_name(boot_path.name + PREVIOUS)
    need = len(desired) + MARGIN
    if not (previous.is_file() and previous.read_bytes() == current):
        need += len(current)
    stat = os.statvfs(boot_path.parent)
    require(stat.f_bavail * stat.f_frsize >= need, 'Not enough free space on the EFI partition')
    if not (previous.is_file() and previous.read_bytes() == current):
        write(previous, current)
    write(boot_path, desired)
    return previous


def compose(root, apply=False):
    current, desired, receipt = plan(root)
    boot_path = root / BOOT
    previous = boot_path.with_name(boot_path.name + PREVIOUS)
    if receipt['status'] == 'current' or not apply:
        return receipt
    replace(boot_path, current, desired)
    receipt.update(applied=True, status='updated; takes effect at next boot', previous=str(previous),
                   rollback='sudo m4-boot-compose --rollback --apply')
    return receipt


def rollback(root, apply=False):
    """Swap boot.bin.previous back in; it must belong to this machine."""
    compatible = read_regular(root / COMPATIBLE, 'Devicetree compatible list')
    require(BOARD in compatible.split(b'\0'), 'This is not a J713 MacBook Air')
    boot_path = root / BOOT
    previous = boot_path.with_name(boot_path.name + PREVIOUS)
    current = read_regular(boot_path, 'boot.bin (is the EFI partition mounted at /boot/efi?)')
    older = read_regular(previous, 'Rollback copy')
    now, then = parse(current), parse(older)
    require(all(now[name] == then[name] for name in ('loader', 'bootargs', 'initramfs')),
            'The rollback copy was not made from this boot.bin')
    receipt = {'boot': str(boot_path), 'boot_sha256': sha(current), 'restore_sha256': sha(older),
               'applied': False, 'status': 'rollback available' if older != current else 'current'}
    if older == current or not apply:
        return receipt
    replace(boot_path, current, older)
    receipt.update(applied=True, status='rolled back; takes effect at next boot',
                   hold='Installed packages are unchanged. The next M4 package update composes '
                        'boot.bin again; downgrade or hold the package to stay on this version.')
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--apply', action='store_true', help='write boot.bin (default: inspect only)')
    parser.add_argument('--rollback', action='store_true', help='restore boot.bin.previous')
    parser.add_argument('--root', type=Path, default=Path('/'), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        receipt = (rollback if args.rollback else compose)(args.root, args.apply)
    except (Refused, OSError) as error:
        print('m4-boot-compose: refused: ' + str(error) + '. boot.bin was not changed.', file=sys.stderr)
        return 1
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
