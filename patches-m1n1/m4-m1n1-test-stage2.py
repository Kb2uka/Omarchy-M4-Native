#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Swap the m1n1 stage 2 inside an installed J713 boot.bin for a #82 test build, and back.

  sudo python3 m4-m1n1-test-stage2.py install FILE SHA256
  sudo python3 m4-m1n1-test-stage2.py rollback
  sudo python3 m4-m1n1-test-stage2.py status

Stage 2 is the first 0x110000 bytes of boot.bin; the payload after it is never
changed. Beside boot.bin, install keeps the replaced stage 2 in
stage2.pre-issue82.bin and a receipt naming both loaders in
stage2.issue82.json. Rollback writes only the replaced bytes back, so a
devicetree update made in between survives, and only while stage 2 is still the
test build. It also finishes an interrupted install or rollback. Changes take
effect at the next boot. M4_M1N1_TEST_* variables are for tests only.
"""
import hashlib
import json
import os
import re
from pathlib import Path
import sys

STAGE2 = 0x110000
# Stage 2 loaders this helper replaces: alpha.1's, and the #82 reporter's
# 8-byte build that disables the dockchannel console.
KNOWN = {
    '3a3f7a64c65fc366d5ac9aece3e1bee64119be012aa1ec5b15d2743c6f91fce4': 'alpha.1',
    '97550b7c505879ad8c2b8e79618b4f0581e10d2e3eb6043182016882d0a0407e': '#82 reporter dockchannel-off build',
}
# Published test builds. The first helper wrote no receipt, so rollback
# recognises these by hash when the receipt is missing.
PUBLISHED = {'7bc78b9138ae449eeab08d0a713c96f25e0d7477b7a48f2804da2c17509a9609'}
BOOT = Path(os.environ.get('M4_M1N1_TEST_BOOT', '/boot/efi/omarchy/boot.bin'))
if 'M4_M1N1_TEST_BOOT' in os.environ and 'M4_M1N1_TEST_KNOWN' in os.environ:
    # Tests cannot carry real loaders, so a test boot.bin may name its own.
    KNOWN = dict.fromkeys(os.environ['M4_M1N1_TEST_KNOWN'].split(','), 'test fixture')
    PUBLISHED = set(filter(None, os.environ.get('M4_M1N1_TEST_PUBLISHED', '').split(',')))
BACKUP = BOOT.with_name('stage2.pre-issue82.bin')
RECEIPT = BOOT.with_name('stage2.issue82.json')
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
    # Only this helper writes this name; a leftover is from a run that stopped.
    temp.unlink(missing_ok=True)
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


def read_receipt():
    if not RECEIPT.exists():
        return None
    try:
        receipt = json.loads(RECEIPT.read_text())
        installed = receipt['installed']
        valid = (set(receipt) == {'installed', 'replaced'} and receipt['replaced'] in KNOWN and
                 isinstance(installed, str) and re.fullmatch('[0-9a-f]{64}', installed) is not None and
                 installed not in KNOWN)
    except (ValueError, TypeError, AttributeError):
        valid = False
    require(valid, f'{RECEIPT} is not a receipt this helper wrote; nothing changed')
    return receipt


def replace_stage2(boot, stage2):
    result = stage2 + boot[STAGE2:]
    # Narrow the window against another tool rewriting boot.bin meanwhile.
    require(BOOT.read_bytes() == boot, 'boot.bin changed while working; nothing changed, run it again')
    write(BOOT, result)
    require(BOOT.read_bytes() == result, 'Written boot.bin does not match')
    return result


def status():
    head = sha(read_boot()[:STAGE2])
    receipt = {'boot': str(BOOT), 'stage2_sha256': head, 'stage2': KNOWN.get(head, 'test build or unknown'),
               'backup': str(BACKUP) if BACKUP.exists() else None, 'receipt': read_receipt()}
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
    require(not BACKUP.exists() and not RECEIPT.exists(), 'a test build was installed before; run rollback first')
    boot = read_boot()
    head = sha(boot[:STAGE2])
    require(head in KNOWN, f'stage 2 in boot.bin is not a known loader ({head}); nothing changed')
    # Receipt, then backup, then boot.bin: rollback recovers from a stop after any step.
    write(RECEIPT, json.dumps({'installed': want, 'replaced': head}).encode() + b'\n')
    write(BACKUP, boot[:STAGE2])
    require(BACKUP.read_bytes() == boot[:STAGE2], 'Backup does not match')
    result = replace_stage2(boot, loader)
    return {'status': 'installed; takes effect at next boot', 'replaced': KNOWN[head], 'backup': str(BACKUP),
            'boot_sha256': sha(result), 'rollback': 'sudo python3 m4-m1n1-test-stage2.py rollback'}


def rollback():
    receipt = read_receipt()
    require(receipt is not None or BACKUP.exists(), 'no test build was installed; nothing to roll back')
    boot = read_boot()
    head = sha(boot[:STAGE2])
    replaced = receipt['replaced'] if receipt else None
    if head == replaced or (BACKUP.exists() and boot[:STAGE2] == BACKUP.read_bytes()):
        restored = 'already in place'
    else:
        # Only undo the test build this helper installed, never a loader that replaced it since.
        installed = receipt['installed'] if receipt else None
        require(head == installed or (receipt is None and head in PUBLISHED),
                f'stage 2 in boot.bin ({head}) is not the installed test build; nothing changed. '
                f'If a newer loader has replaced it, delete {BACKUP} and {RECEIPT}')
        require(BACKUP.is_file(), f'no backup at {BACKUP}; nothing changed')
        saved = BACKUP.read_bytes()
        require(len(saved) == STAGE2 and sha(saved) in KNOWN and (replaced is None or sha(saved) == replaced),
                'backup is not the replaced stage 2; nothing changed')
        replace_stage2(boot, saved)
        restored = KNOWN[sha(saved)]
    # Backup first, then receipt: a stop in between is finished by the next rollback.
    BACKUP.unlink(missing_ok=True)
    RECEIPT.unlink(missing_ok=True)
    return {'status': 'restored; takes effect at next boot', 'restored': restored}


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
