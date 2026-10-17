#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Make the #82 ANS reset experiment loaders from the test 1 stage 1.

  python3 issue82-variants.py m1n1-stage1-issue82-test1.bin OUT_DIR

Test 1 (54807eb5...) freezes on the #82 Air inside the first
pmgr_reset(die, "ANS") in nvme_shutdown(). Rebuilt from source the freeze goes
away, so each variant edits test 1 in place: a few instructions become NOPs and
the version tag changes from -dctx1 to -i82vN, both at the same length, so every
instruction stays at its address. Each site must hold the expected instruction
first, and each removed call must branch to the expected function.

  v1  nvme_shutdown() skips both pmgr_reset() calls. ANS is put to sleep and
      its CPU stopped as before, but m1n1 never resets it; Linux does.
  v2  pmgr_reset_device() keeps its console print but skips the four PMGR
      stores, so the print still lands right after ANS sleeps, with no reset.
  v3  pmgr_reset_device() resets ANS without printing first.
"""
import hashlib
from pathlib import Path
import struct
import sys

TEST1 = '54807eb5aa8a2dae7aa64eac735d12168e42bc275d2dd24d328dcadcf7e526dd'
NOP = 0xd503201f
TAG = b'20261008-dctx1'
TAG_COUNT = 2  # the banner and chosen.asahi,m1n1-stage1-version

# Offsets in test 1. The raw loader is linked at 0, so file offsets are addresses.
PMGR_RESET = 0x269c8
DEBUG_PRINTF = 0x30c28
STR_W8_X20 = 0xb9000288  # str w8, [x20]: one PMGR power-state register store

# offset: (expected instruction, BL target or None, what it does)
SITES = {
    'v1': {
        0x23e90: (0x94000ace, PMGR_RESET, 'nvme_shutdown: pmgr_reset(die, "ANS")'),
        0x23ea0: (0x94000aca, PMGR_RESET, 'nvme_shutdown: pmgr_reset(die, "ANS2")'),
    },
    'v2': {
        0x26964: (STR_W8_X20, None, 'pmgr_reset_device: set DEV_DISABLE'),
        0x26974: (STR_W8_X20, None, 'pmgr_reset_device: set RESET'),
        0x26988: (STR_W8_X20, None, 'pmgr_reset_device: clear RESET'),
        0x26998: (STR_W8_X20, None, 'pmgr_reset_device: clear DEV_DISABLE'),
    },
    'v3': {
        0x26954: (0x940028b5, DEBUG_PRINTF, 'pmgr_reset_device: printf("pmgr: resetting device ...")'),
    },
}


class Refused(Exception):
    pass


def bl_target(offset, word):
    if word >> 26 != 0b100101:
        return None
    imm = word & 0x3ffffff
    if imm & 0x2000000:
        imm -= 0x4000000
    return offset + imm * 4


def make_variant(data, name, expected_sha=TEST1):
    if expected_sha and hashlib.sha256(data).hexdigest() != expected_sha:
        raise Refused('input is not the test 1 stage 1 (%s)' % expected_sha[:8])
    if data.count(TAG) != TAG_COUNT:
        raise Refused('expected %d copies of the -dctx1 tag' % TAG_COUNT)
    out = bytearray(data.replace(TAG, TAG[:-5] + b'i82' + name.encode()))
    for offset, (word, target, what) in SITES[name].items():
        found = struct.unpack_from('<I', out, offset)[0]
        if found != word:
            raise Refused('%#x (%s): found %#010x, expected %#010x' % (offset, what, found, word))
        if target is not None and bl_target(offset, found) != target:
            raise Refused('%#x (%s): call does not reach %#x' % (offset, what, target))
        struct.pack_into('<I', out, offset, NOP)
    return bytes(out)


def main(argv):
    if len(argv) != 3:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    data = Path(argv[1]).read_bytes()
    try:
        made = {'m1n1-stage1-issue82-%s.bin' % name: make_variant(data, name, TEST1) for name in SITES}
    except Refused as error:
        print('refused: %s' % error, file=sys.stderr)
        return 1
    out = Path(argv[2])
    out.mkdir(parents=True, exist_ok=True)
    sums = []
    for filename, variant in made.items():
        (out / filename).write_bytes(variant)
        sums.append('%s  %s\n' % (hashlib.sha256(variant).hexdigest(), filename))
    (out / 'SHA256SUMS').write_text(''.join(sums))
    sys.stdout.write(''.join(sums))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
