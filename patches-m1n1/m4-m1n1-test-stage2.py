#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Swap the m1n1 stage 2 inside an installed J713 boot.bin for a #82 test build, and back.

  sudo python3 m4-m1n1-test-stage2.py install FILE SHA256
  sudo python3 m4-m1n1-test-stage2.py rollback
  sudo python3 m4-m1n1-test-stage2.py status

Stage 2 is the first 0x110000 bytes of boot.bin; the payload after it is never
changed. The replaced stage 2 is kept beside boot.bin, and rollback writes only
those bytes back, so a devicetree update made in between survives. Changes take
effect at the next boot. M4_M1N1_TEST_* variables are for tests only.
"""
import hashlib
import json
import os
from pathlib import Path
import sys

STAGE2 = 0x110000
# Stage 2 loaders this helper replaces: alpha.1's, and the #82 reporter's
# 8-byte build that disables the dockchannel console.
KNOWN = {
    '3a3f7a64c65fc366d5ac9aece3e1bee64119be012aa1ec5b15d2743c6f91fce4': 'alpha.1',
    '97550b7c505879ad8c2b8e79618b4f0581e10d2e3eb6043182016882d0a0407e': '#82 reporter dockchannel-off build',
}
BOOT = Path(os.environ.get('M4_M1N1_TEST_BOOT', '/boot/efi/omarchy/boot.bin'))
if 'M4_M1N1_TEST_BOOT' in os.environ and 'M4_M1N1_TEST_KNOWN' in os.environ:
    # Tests cannot carry real loaders, so a test boot.bin may name its own.
    KNOWN = dict.fromkeys(os.environ['M4_M1N1_TEST_KNOWN'].split(','), 'test fixture')
BACKUP = BOOT.with_name('stage2.pre-issue82.bin')
CHOSEN = Path('/proc/device-tree/chosen')


class Refused(Exception):
    pass


def require(condition, message):
    if not condition:
        raise Refused(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


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


def read_boot():
    require(BOOT.is_file() and not BOOT.is_symlink(), 'boot.bin must be a regular file: ' + str(BOOT))
    boot = BOOT.read_bytes()
    require(len(boot) > STAGE2, 'boot.bin is too small to hold stage 2')
    return boot


def replace_stage2(boot, stage2):
    result = stage2 + boot[STAGE2:]
    write(BOOT, result)
    require(BOOT.read_bytes() == result, 'Written boot.bin does not match')
    return result


def status():
    head = sha(read_boot()[:STAGE2])
    receipt = {'boot': str(BOOT), 'stage2_sha256': head, 'stage2': KNOWN.get(head, 'test build or unknown'),
               'backup': str(BACKUP) if BACKUP.exists() else None}
    for stage in ('stage1', 'stage2'):
        tag = CHOSEN / f'asahi,m1n1-{stage}-version'
        if tag.exists():
            receipt[f'running_{stage}'] = tag.read_bytes().rstrip(b'\0').decode(errors='replace')
    return receipt


def install(file, want):
    loader = Path(file).read_bytes()
    require(len(loader) == STAGE2, f'{file} is {len(loader)} bytes, not a stage 2 loader')
    require(sha(loader) == want, f'{file} does not match {want}; download it again')
    require(want not in KNOWN, 'that is not a test build; use rollback')
    boot = read_boot()
    head = sha(boot[:STAGE2])
    require(head in KNOWN, f'stage 2 in boot.bin is not a known loader ({head}); nothing changed')
    require(not BACKUP.exists(), f'{BACKUP} already exists; roll back first')
    write(BACKUP, boot[:STAGE2])
    require(BACKUP.read_bytes() == boot[:STAGE2], 'Backup does not match')
    result = replace_stage2(boot, loader)
    return {'status': 'installed; takes effect at next boot', 'replaced': KNOWN[head], 'backup': str(BACKUP),
            'boot_sha256': sha(result), 'rollback': 'sudo python3 m4-m1n1-test-stage2.py rollback'}


def rollback():
    require(BACKUP.is_file(), f'no backup at {BACKUP}')
    saved = BACKUP.read_bytes()
    require(len(saved) == STAGE2 and sha(saved) in KNOWN, 'backup is not a known stage 2; nothing changed')
    boot = read_boot()
    if boot[:STAGE2] != saved:
        replace_stage2(boot, saved)
    BACKUP.unlink()
    return {'status': 'restored; takes effect at next boot', 'restored': KNOWN[sha(saved)]}


def main(argv):
    commands = {'install': (install, 2), 'rollback': (rollback, 0), 'status': (status, 0)}
    if len(argv) < 1 or argv[0] not in commands or len(argv) - 1 != commands[argv[0]][1]:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    function, _ = commands[argv[0]]
    try:
        print(json.dumps(function(*argv[1:]), indent=2))
    except (Refused, OSError) as error:
        print('m4-m1n1-test-stage2: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
