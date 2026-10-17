#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Replace the devicetree inside an installed J713 boot.bin, in place.

The installer bakes per-machine boot arguments into boot.bin, so the file
differs per machine. Only the embedded devicetree payload is swapped; m1n1
locates the next payload from the DTB header's own size. Inspection is the
default; --apply keeps a backup and replaces the file atomically.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct

ALPHA1_DTB = '67dc61458b03c0fad1156994ad85a85f9f8612cac2996eb684609b0455ebf547'
POWEROFF_DTB = 'e54c77a760a5b696486f9d80c7c83ca4295d641113bb9e54d13e259863bf9b07'
CPUFREQ_DTB = '033025bbdda3ce72b5eefe39d1d6d68209256fd43e4fd587c96ae1cf38978555'
CPUTEMP_DTB = '08e8e719381f88a1e50d821c145dad5b2d97fd772609e13d7778e19795e9cad1'
ECLUSTER_DTB = 'fdedc09f2fe8ed681a61208589024e01fb5efa77beebbd0eb4ad267c08e77f7c'
PBOOST_DTB = 'c4f036b819ce757ce790b01779a3f7532eacd5cb1c1097fa20286d1a9c9ec6ca'
USB0_DTB = '352bfa74351ad7d12d993f752fe9c09d682511bca0d4a60177cda0d716d1d614'
# Replacement devicetree -> (devicetrees it may replace, backup suffix).
UPDATES = {
    POWEROFF_DTB: ((ALPHA1_DTB,), '.pre-shutdown-flag'),
    CPUFREQ_DTB: ((ALPHA1_DTB, POWEROFF_DTB), '.pre-cpufreq'),
    # Adds only temperature channels, so it replaces only the cpufreq devicetree:
    # machines without the cpufreq prerequisites cannot reach cpufreq through it.
    CPUTEMP_DTB: ((CPUFREQ_DTB,), '.pre-cputemp'),
    # Adds E-cluster states and the CPU die thermal zone, which reads the CPU
    # temperature channels, so it replaces only the CPU temperature devicetree.
    ECLUSTER_DTB: ((CPUTEMP_DTB,), '.pre-ecluster'),
    # Adds P and E states, with P held at 1800 MHz by a boost gate on the CPU die
    # thermal zone, so it replaces only the E-cluster devicetree.
    PBOOST_DTB: ((ECLUSTER_DTB,), '.pre-pboost'),
    # Turns on the front USB-C port the way the hinge-side one is set up, so it
    # replaces only the boost-state devicetree.
    USB0_DTB: ((PBOOST_DTB,), '.pre-usb0'),
}
FDT_MAGIC = b'\xd0\x0d\xfe\xed'
NEXT_PAYLOAD = b'm1n1_initramfs'
DEFAULT_BOOT = Path('/boot/efi/omarchy/boot.bin')


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def devicetrees(data):
    """Return (offset, size, sha256) for every well-formed FDT header in data."""
    found, pos = [], data.find(FDT_MAGIC)
    while pos >= 0:
        if pos + 8 <= len(data):
            size, = struct.unpack_from('>I', data, pos + 4)
            if 40 <= size <= len(data) - pos:
                found.append((pos, size, sha(data[pos:pos + size])))
        pos = data.find(FDT_MAGIC, pos + 1)
    return found


def plan(boot, dtb):
    """Return the replacement boot.bin bytes, or None when already updated."""
    target = sha(dtb)
    require(target in UPDATES, 'Unknown replacement devicetree')
    replaces = UPDATES[target][0]
    trees = devicetrees(boot)
    old = [t for t in trees if t[2] in replaces]
    new = [t for t in trees if t[2] == target]
    if not old and len(new) == 1:
        return None
    require(len(old) == 1 and not new, 'boot.bin does not hold exactly one devicetree this update replaces')
    offset, size, _ = old[0]
    require(boot[offset + size:offset + size + len(NEXT_PAYLOAD)] == NEXT_PAYLOAD,
            'Unexpected payload after the devicetree')
    result = boot[:offset] + dtb + boot[offset + size:]
    check = devicetrees(result)
    require([t[2] for t in check if t[2] in (*replaces, target)] == [target] and
            result[:offset] == boot[:offset] and
            result[offset + len(dtb):] == boot[offset + size:], 'Replacement verification failed')
    return result


def write(path, data):
    temp = path.with_name(path.name + '.new')
    require(not temp.exists(), 'Stale temporary file: ' + str(temp))
    with temp.open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def update(boot_path, dtb_path, apply=False):
    require(boot_path.is_file() and not boot_path.is_symlink(), 'boot.bin must be a regular file')
    require(dtb_path.is_file() and not dtb_path.is_symlink(), 'Devicetree must be a regular file')
    boot = boot_path.read_bytes()
    dtb = dtb_path.read_bytes()
    result = plan(boot, dtb)
    backup = boot_path.with_name(boot_path.name + UPDATES[sha(dtb)][1])
    receipt = {'boot': str(boot_path), 'boot_sha256': sha(boot), 'devicetree_sha256': sha(dtb),
               'backup': str(backup), 'applied': False}
    if result is None:
        receipt['status'] = 'already updated'
        return receipt
    receipt.update(status='update available', new_boot_sha256=sha(result),
                   rollback=f'sudo cp {backup} {boot_path} && sync')
    if not apply:
        return receipt
    if backup.exists():
        require(backup.read_bytes() == boot, 'A different backup already exists: ' + str(backup))
    else:
        write(backup, boot)
    write(boot_path, result)
    require(boot_path.read_bytes() == result, 'Written boot.bin does not match')
    receipt.update(applied=True, status='updated; takes effect at next boot')
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dtb', required=True, type=Path, help='replacement devicetree')
    parser.add_argument('--boot', type=Path, default=DEFAULT_BOOT)
    parser.add_argument('--apply', action='store_true', help='write the update (default: inspect only)')
    args = parser.parse_args()
    print(json.dumps(update(args.boot, args.dtb, args.apply), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
