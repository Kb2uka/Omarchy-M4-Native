#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Compose the installed J713 boot.bin from the packaged kernel and devicetree.

boot.bin is per machine: its boot arguments and initramfs carry this owner's
root partition and Apple firmware. Kernel/devicetree updates preserve both.
The native backlight additionally replaces only a known stage2, preserving the
owner's notch preference, because its per-boot display reservations are required
by the enabled backlight tree. Failed checks leave boot.bin untouched.
Inspection is the default; --apply keeps the replaced file as boot.bin.previous.
"""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import sys
import zlib

# Kernels and devicetrees an installed alpha.1 machine may boot, by SHA-256.
# A kernel maps to its module release. Adding one requires the protected audio,
# video and GPU stacks to be requalified for it (docs/package-channel.md).
# The second hash is the retained-bounds kernel. It shares the release; modules
# stay with the base kernel package. Kernel bytes move only for KERNEL_TRANSITIONS.
OLD_KERNEL = 'c3704cfd2afb7760e73f0b9939a68864a30bd916fcd478bd960e538373123dc9'
NEW_KERNEL = 'a7a160b6686652d7ae93218593d015febf9cc7ab0ef838148e551116befeb05e'
RELEASE = '7.1.9-m4-gpu-backport-20261008'
KNOWN_KERNELS = {
    OLD_KERNEL: RELEASE,
    NEW_KERNEL: RELEASE,
}
KERNEL_TRANSITIONS = {(OLD_KERNEL, NEW_KERNEL), (NEW_KERNEL, OLD_KERNEL)}
# Devicetrees in lineage order. A machine whose boot.bin carries a later one than
# the package keeps it: an owner-applied devicetree update is never reverted.
KNOWN_DTBS = {
    '67dc61458b03c0fad1156994ad85a85f9f8612cac2996eb684609b0455ebf547': 'v0.1.0-alpha.1',
    'e54c77a760a5b696486f9d80c7c83ca4295d641113bb9e54d13e259863bf9b07': 'v0.1.0-alpha.1-update.1',
    '033025bbdda3ce72b5eefe39d1d6d68209256fd43e4fd587c96ae1cf38978555': 'j713-cpufreq',
    '08e8e719381f88a1e50d821c145dad5b2d97fd772609e13d7778e19795e9cad1': 'j713-cpu-temperature',
    'fdedc09f2fe8ed681a61208589024e01fb5efa77beebbd0eb4ad267c08e77f7c': 'j713-ecluster-thermal',
    'c4f036b819ce757ce790b01779a3f7532eacd5cb1c1097fa20286d1a9c9ec6ca': 'j713-pboost',
    '352bfa74351ad7d12d993f752fe9c09d682511bca0d4a60177cda0d716d1d614': 'j713-usb0',
    'afb025a8f1c946d164fe962f20b2e9d5cd987cfc668985eee62e41767722b2aa': 'j713-backlight',
}
BOARD = b'apple,j713'
BOOT = 'boot/efi/omarchy/boot.bin'
# Packaging stages this beside the unchanged base Image.gz. A devicetree-only
# update still reads whatever kernel image is installed at this path.
KERNEL = 'usr/lib/omarchy-m4/retained-bounds/Image.gz'
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
BACKLIGHT_DTB = 'afb025a8f1c946d164fe962f20b2e9d5cd987cfc668985eee62e41767722b2aa'
KNOWN_LOADERS = {
    '3a3f7a64c65fc366d5ac9aece3e1bee64119be012aa1ec5b15d2743c6f91fce4': 'hidden',
    '7bc78b9138ae449eeab08d0a713c96f25e0d7477b7a48f2804da2c17509a9609': 'hidden',
    'f2417a74d13838004847a6fbdf946b524005301b88b5a403b1e79cb36d0ec7ca': 'shown',
    'b86aee6840fe670b592271ec2816d42c5515cddc20a14e08bdc5bcbce547e6fd': 'hidden',
    '692f656cd8f6dfb2530172da518f9e2e18e6c5032df3062cd603eb4539e4c405': 'shown',
}
BACKLIGHT_LOADERS = {
    'hidden': ('usr/lib/omarchy-m4/backlight/stage2-hidden.bin',
               'b86aee6840fe670b592271ec2816d42c5515cddc20a14e08bdc5bcbce547e6fd'),
    'shown': ('usr/lib/omarchy-m4/backlight/stage2-shown.bin',
              '692f656cd8f6dfb2530172da518f9e2e18e6c5032df3062cd603eb4539e4c405'),
}
PACMAN_LOCK = 'var/lib/pacman/db.lck'
JOURNAL_DIR = 'var/lib/omarchy-m4/boot-compose'
JOURNAL = JOURNAL_DIR + '/journal'
SNAPSHOTS = JOURNAL_DIR + '/snapshots'
SHA256 = re.compile(r'[0-9a-f]{64}')


class Refused(Exception):
    def __init__(self, message, pending=False):
        super().__init__(message)
        self.pending = pending


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


def transition_kind(old, new):
    """How a kernel-hash change is allowed.

    ``listed`` is an exact KERNEL_TRANSITIONS pair that shares one release.
    ``historical`` is a same-release pair whose hashes are not in that table
    (fixture kernels, or a devicetree update that also carries an already
    qualified kernel). ``refused`` is any other hash change. ``same`` is not a
    kernel change; devicetree-only updates do not consult the table.
    """
    if old == new:
        return 'same'
    if (old, new) in KERNEL_TRANSITIONS:
        if old in KNOWN_KERNELS and new in KNOWN_KERNELS and KNOWN_KERNELS[old] == KNOWN_KERNELS[new]:
            return 'listed'
        return 'refused'
    governed = {item for pair in KERNEL_TRANSITIONS for item in pair}
    if not KERNEL_TRANSITIONS or old in governed or new in governed:
        return 'refused'
    if old in KNOWN_KERNELS and new in KNOWN_KERNELS and KNOWN_KERNELS[old] == KNOWN_KERNELS[new]:
        return 'historical'
    return 'refused'


def check_listed(root, old, new):
    """Release, audio, video pin, and modules for an exact kernel pair. No writes."""
    require(transition_kind(old, new) == 'listed',
            'The installed video stack is not qualified for kernel ' + new)
    release = KNOWN_KERNELS[new]
    video, audio = manifest(root, VIDEO_MANIFEST), manifest(root, AUDIO_MANIFEST)
    require(video.get('kernel_release') == release and video.get('boot_kernel_sha256') in (old, new),
            'The installed video stack is not qualified for kernel ' + new)
    require(audio.get('kernel_release') == release,
            'The installed audio stack is not qualified for kernel release ' + release)
    modules = root / 'usr/lib/modules' / release
    require(modules.is_dir() and not modules.is_symlink(), 'Kernel modules are missing: ' + str(modules))
    return video


def owned(root, relative):
    """A path under root with no symlink component and no parent segments."""
    current = Path(root)
    require(not current.is_symlink(), 'Boot compose root is a symlink')
    for part in Path(relative).parts:
        require(part not in ('', '.', '..'), 'Unsafe boot-compose path')
        current = current / part
        require(not current.is_symlink(), 'Refusing a symlink in boot-compose state: ' + str(current))
    return current


def read_nofollow(path, what):
    require(path.is_file() and not path.is_symlink(), what + ' is missing or not a regular file')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        chunks = []
        while True:
            block = os.read(fd, 1 << 20)
            if not block:
                break
            chunks.append(block)
    finally:
        os.close(fd)
    return b''.join(chunks)


def private_dir(path):
    require(not path.is_symlink(), 'Boot compose directory is a symlink: ' + str(path))
    path.mkdir(mode=0o700, exist_ok=True)
    require(path.is_dir() and not path.is_symlink(), 'Boot compose directory is not a directory')
    os.chmod(path, 0o700)


def ensure_store(root):
    parent = owned(root, 'var/lib/omarchy-m4')
    parent.mkdir(parents=True, exist_ok=True)
    base = owned(root, JOURNAL_DIR)
    private_dir(base)
    private_dir(owned(root, SNAPSHOTS))
    return base


def snapshot_path(root, digest):
    require(isinstance(digest, str) and SHA256.fullmatch(digest) is not None, 'Boot snapshot hash is invalid')
    return owned(root, SNAPSHOTS + '/' + digest)


def read_snapshot(root, digest):
    path = snapshot_path(root, digest)
    data = read_nofollow(path, 'Boot snapshot')
    require(sha(data) == digest, 'Boot snapshot does not match its hash')
    return data


def same_owner(source, dest):
    """Both boot images keep this machine's arguments, initramfs, and notch choice."""
    require(source['bootargs'] == dest['bootargs'] and source['initramfs'] == dest['initramfs'],
            'Boot objects do not belong to the same owner')
    require(sha(source['dtb']) in KNOWN_DTBS and sha(dest['dtb']) in KNOWN_DTBS,
            'Boot object carries an unrecognized devicetree')
    if source['loader'] != dest['loader']:
        current, restored = sha(source['loader']), sha(dest['loader'])
        require(current in KNOWN_LOADERS and restored in KNOWN_LOADERS and
                KNOWN_LOADERS[current] == KNOWN_LOADERS[restored],
                'Boot objects change notch preference or an unknown stage2')
    old, new = sha(source['kernel']), sha(dest['kernel'])
    require(old in KNOWN_KERNELS and new in KNOWN_KERNELS, 'Boot object has an unrecognized kernel')
    require(KNOWN_KERNELS[old] == KNOWN_KERNELS[new],
            'Boot objects are not the same kernel release')
    if old != new:
        require((old, new) in KERNEL_TRANSITIONS,
                'The installed video stack is not qualified for kernel ' + new)
    return old, new


def load_journal(root):
    """Return a validated pending journal, None when absent, or refuse corrupt state."""
    path = owned(root, JOURNAL)
    if not path.exists():
        return None
    try:
        value = json.loads(read_nofollow(path, 'Boot journal'))
    except ValueError:
        raise Refused('Boot journal is corrupt') from None
    require(isinstance(value, dict), 'Boot journal is corrupt')
    operation = value.get('operation')
    from_sha, to_sha = value.get('from_sha256'), value.get('to_sha256')
    video_pin = value.get('video_boot_kernel_sha256')
    require(operation in ('apply', 'rollback'), 'Boot journal operation is invalid')
    require(all(isinstance(item, str) and SHA256.fullmatch(item) for item in (from_sha, to_sha, video_pin)),
            'Boot journal hashes are invalid')
    from_bytes, to_bytes = read_snapshot(root, from_sha), read_snapshot(root, to_sha)
    try:
        source, dest = parse(from_bytes), parse(to_bytes)
    except Refused:
        raise Refused('Boot journal snapshots are not boot objects') from None
    old, new = same_owner(source, dest)
    require(video_pin == new, 'Boot journal video pin does not match the target kernel')
    return {'operation': operation, 'from_sha256': from_sha, 'to_sha256': to_sha,
            'video_boot_kernel_sha256': video_pin, 'from_bytes': from_bytes, 'to_bytes': to_bytes,
            'from_kernel': old, 'to_kernel': new}


def pending_receipt(journal):
    return {'applied': False, 'operation': journal['operation'],
            'from_sha256': journal['from_sha256'], 'to_sha256': journal['to_sha256'],
            'status': 'transition pending (' + journal['operation'] +
                      '); resume with the same operation, nothing was replayed'}


def require_space(root, from_bytes, to_bytes):
    """Root filesystem and EFI must hold the snapshots, the previous file, and the new boot."""
    boot_path = root / BOOT
    efi = os.statvfs(boot_path.parent)
    require(efi.f_bavail * efi.f_frsize >= len(from_bytes) + len(to_bytes) + MARGIN,
            'Not enough free space on the EFI partition')
    anchor = root if root.is_dir() else boot_path.parent
    rootfs = os.statvfs(anchor)
    require(rootfs.f_bavail * rootfs.f_frsize >= len(from_bytes) + len(to_bytes) + MARGIN,
            'Not enough free space on the root filesystem')


def write_private(path, data):
    write(path, data)
    os.chmod(path, 0o600)


def save_journal(root, operation, from_bytes, to_bytes, video_pin):
    """Persist both boot objects and the journal before any boot or video write."""
    require_space(root, from_bytes, to_bytes)
    source, dest = parse(from_bytes), parse(to_bytes)
    old, new = same_owner(source, dest)
    require(video_pin == new, 'Boot journal video pin does not match the target kernel')
    ensure_store(root)
    from_sha, to_sha = sha(from_bytes), sha(to_bytes)
    write_private(snapshot_path(root, from_sha), from_bytes)
    write_private(snapshot_path(root, to_sha), to_bytes)
    require(read_snapshot(root, from_sha) == from_bytes and read_snapshot(root, to_sha) == to_bytes,
            'Saved boot snapshot does not match')
    payload = {'operation': operation, 'from_sha256': from_sha, 'to_sha256': to_sha,
               'video_boot_kernel_sha256': video_pin}
    write_private(owned(root, JOURNAL), (json.dumps(payload, indent=2, sort_keys=True) + '\n').encode())
    return load_journal(root)


def write_video_pin(root, pin, allowed):
    path = root / VIDEO_MANIFEST
    raw = read_regular(path, 'Protected stack manifest')
    try:
        document = json.loads(raw)
    except ValueError:
        raise Refused('Protected stack manifest is not valid JSON: ' + str(path)) from None
    require(isinstance(document, dict), 'Protected stack manifest is not an object: ' + str(path))
    current = document.get('boot_kernel_sha256')
    require(current in allowed, 'The installed video stack is not qualified for kernel ' + pin)
    if current == pin:
        return
    document['boot_kernel_sha256'] = pin
    write(path, (json.dumps(document, indent=2) + '\n').encode())


def clear_journal(root):
    path = owned(root, JOURNAL)
    require(path.is_file() and not path.is_symlink(), 'Boot journal disappeared before it could be cleared')
    path.unlink()
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    require(not path.exists(), 'Boot journal is still present')


def commit_transition(root, journal):
    """Previous from the snapshot, then the video pin, then boot. Clear the journal last."""
    require_space(root, journal['from_bytes'], journal['to_bytes'])
    boot_path = root / BOOT
    current = read_regular(boot_path, 'boot.bin (is the EFI partition mounted at /boot/efi?)')
    require(current in (journal['from_bytes'], journal['to_bytes']),
            'boot.bin is neither side of the pending transition')
    allowed = (journal['from_kernel'], journal['to_kernel'])
    video = manifest(root, VIDEO_MANIFEST)
    require(video.get('boot_kernel_sha256') in allowed,
            'The installed video stack is not qualified for kernel ' + journal['video_boot_kernel_sha256'])
    # Previous is the boot we are leaving, taken from the snapshot, not from a
    # partially overwritten boot.bin.previous and not from the live boot.bin.
    write(boot_path.with_name(boot_path.name + PREVIOUS), journal['from_bytes'])
    write_video_pin(root, journal['video_boot_kernel_sha256'], allowed)
    write(boot_path, journal['to_bytes'])
    require(read_regular(boot_path, 'boot.bin') == journal['to_bytes'], 'boot.bin does not match the snapshot')
    require(manifest(root, VIDEO_MANIFEST).get('boot_kernel_sha256') == journal['video_boot_kernel_sha256'],
            'Video manifest pin does not match the target kernel')
    clear_journal(root)


def resume(root, journal):
    check_listed(root, journal['from_kernel'], journal['to_kernel'])
    commit_transition(root, journal)
    return journal


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
    kind = transition_kind(now['kernel'], new['kernel'])
    if kind == 'listed':
        check_listed(root, now['kernel'], new['kernel'])
    elif kind == 'refused':
        check_protected(root, new['kernel'])
        raise Refused('The installed video stack is not qualified for kernel ' + new['kernel'])
    else:
        check_protected(root, new['kernel'])
    loader = layout['loader']
    if BACKLIGHT_DTB in lineage and lineage.index(new['dtb']) >= lineage.index(BACKLIGHT_DTB):
        current_loader = sha(loader)
        require(current_loader in KNOWN_LOADERS,
                'Display handoff cannot replace an unknown stage2 ' + current_loader)
        path, digest = BACKLIGHT_LOADERS[KNOWN_LOADERS[current_loader]]
        loader = read_regular(root / path, 'Packaged display handoff stage2')
        require(len(loader) == 0x110000 and sha(loader) == digest,
                'Packaged display handoff stage2 differs from its pin')
        now['loader'], new['loader'] = current_loader, digest
    desired = join({**layout, 'kernel': kernel, 'dtb': dtb, 'loader': loader})
    result = parse(desired)
    require(all(result[name] == layout[name] for name in ('bootargs', 'initramfs')) and
            result['loader'] == loader and result['kernel'] == kernel and result['dtb'] == dtb,
            'Composed boot.bin verification failed')
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
    journal = load_journal(root)
    if journal is not None:
        if not apply:
            return pending_receipt(journal)
        if journal['operation'] != 'apply':
            raise Refused('A rollback transition is pending. Resume with sudo m4-boot-compose --rollback --apply',
                          pending=True)
        resume(root, journal)
        boot_path = root / BOOT
        previous = boot_path.with_name(boot_path.name + PREVIOUS)
        return {'boot': str(boot_path), 'boot_sha256': sha(journal['to_bytes']),
                'kernel_sha256': journal['to_kernel'], 'kernel_release': KNOWN_KERNELS[journal['to_kernel']],
                'applied': True, 'status': 'updated; takes effect at next boot', 'previous': str(previous),
                'changes': ['kernel'], 'rollback': 'sudo m4-boot-compose --rollback --apply'}
    current, desired, receipt = plan(root)
    boot_path = root / BOOT
    previous = boot_path.with_name(boot_path.name + PREVIOUS)
    if receipt['status'] == 'current' or not apply:
        return receipt
    kind = transition_kind(sha(parse(current)['kernel']), sha(parse(desired)['kernel']))
    if kind == 'listed':
        journal = save_journal(root, 'apply', current, desired, sha(parse(desired)['kernel']))
        resume(root, journal)
        receipt.update(applied=True, status='updated; takes effect at next boot', previous=str(previous),
                       rollback='sudo m4-boot-compose --rollback --apply')
        receipt['boot_sha256'] = sha(desired)
        return receipt
    replace(boot_path, current, desired)
    receipt.update(applied=True, status='updated; takes effect at next boot', previous=str(previous),
                   rollback='sudo m4-boot-compose --rollback --apply')
    return receipt


def rollback(root, apply=False):
    """Swap boot.bin.previous back in; it must belong to this machine."""
    journal = load_journal(root)
    if journal is not None:
        if not apply:
            return pending_receipt(journal)
        if journal['operation'] != 'rollback':
            raise Refused('An apply transition is pending. Resume with sudo m4-boot-compose --apply',
                          pending=True)
        resume(root, journal)
        boot_path = root / BOOT
        return {'boot': str(boot_path), 'boot_sha256': sha(journal['to_bytes']),
                'restore_sha256': sha(journal['to_bytes']), 'applied': True,
                'status': 'rolled back; takes effect at next boot',
                'hold': 'Installed packages are unchanged. The next M4 package update composes '
                        'boot.bin again; downgrade or hold the package to stay on this version.'}
    compatible = read_regular(root / COMPATIBLE, 'Devicetree compatible list')
    require(BOARD in compatible.split(b'\0'), 'This is not a J713 MacBook Air')
    boot_path = root / BOOT
    previous = boot_path.with_name(boot_path.name + PREVIOUS)
    current = read_regular(boot_path, 'boot.bin (is the EFI partition mounted at /boot/efi?)')
    older = read_regular(previous, 'Rollback copy')
    now, then = parse(current), parse(older)
    require(all(now[name] == then[name] for name in ('bootargs', 'initramfs')) and
            (now['loader'] == then['loader'] or
             (sha(now['loader']) in KNOWN_LOADERS and sha(then['loader']) in KNOWN_LOADERS and
              KNOWN_LOADERS[sha(now['loader'])] == KNOWN_LOADERS[sha(then['loader'])])),
            'The rollback copy was not made from this boot.bin or changes notch preference')
    if sha(then['dtb']) == BACKLIGHT_DTB:
        require(sha(then['loader']) in {entry[1] for entry in BACKLIGHT_LOADERS.values()},
                'Rollback backlight tree lacks its display handoff stage2')
    receipt = {'boot': str(boot_path), 'boot_sha256': sha(current), 'restore_sha256': sha(older),
               'applied': False, 'status': 'rollback available' if older != current else 'current'}
    if older == current or not apply:
        return receipt
    old_kernel, new_kernel = sha(now['kernel']), sha(then['kernel'])
    if transition_kind(old_kernel, new_kernel) == 'listed':
        check_listed(root, old_kernel, new_kernel)
        journal = save_journal(root, 'rollback', current, older, new_kernel)
        resume(root, journal)
        receipt.update(applied=True, status='rolled back; takes effect at next boot',
                       hold='Installed packages are unchanged. The next M4 package update composes '
                            'boot.bin again; downgrade or hold the package to stay on this version.')
        return receipt
    replace(boot_path, current, older)
    receipt.update(applied=True, status='rolled back; takes effect at next boot',
                   hold='Installed packages are unchanged. The next M4 package update composes '
                        'boot.bin again; downgrade or hold the package to stay on this version.')
    return receipt


@contextlib.contextmanager
def transaction_guard(root, package_hook=False):
    path = root / PACMAN_LOCK
    if package_hook:
        require(path.is_file() and not path.is_symlink(), 'Package hook lacks its pacman transaction lock')
        yield
        return
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o000)
    except FileExistsError:
        raise Refused('A package transaction or another boot writer is running') from None
    os.close(fd)
    try:
        yield
    finally:
        path.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--apply', action='store_true', help='write boot.bin (default: inspect only)')
    parser.add_argument('--rollback', action='store_true', help='restore boot.bin.previous')
    parser.add_argument('--root', type=Path, default=Path('/'), help=argparse.SUPPRESS)
    parser.add_argument('--hook', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.apply:
            with transaction_guard(args.root, args.hook):
                receipt = (rollback if args.rollback else compose)(args.root, True)
        else:
            receipt = (rollback if args.rollback else compose)(args.root, False)
    except (Refused, OSError) as error:
        pending = getattr(error, 'pending', False)
        if not pending:
            try:
                pending = load_journal(args.root) is not None
            except (Refused, OSError):
                pending = False
        if pending:
            suffix = ('. Resume the pending transition with the same operation; '
                      'do not assume boot.bin is unchanged.')
        else:
            suffix = '. boot.bin was not changed.'
        print('m4-boot-compose: refused: ' + str(error) + suffix, file=sys.stderr)
        return 1
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
