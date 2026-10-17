#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Apply the fixed Air GPU module (#57, #118) on an installed alpha.1 Air.

m4-gpu-start checks /opt/m4-gpu/runtime.sha256 before it loads
/opt/m4-gpu/asahi_m4.ko, and runs only once per boot. This tool replaces the
module and exactly its one line in runtime.sha256, and nothing else. It only
acts at boot, from the drop-in that runs it before m4-gpu-start, because the
module stays loaded until the next boot.

Known states: stock alpha.1 (module and pin both 80cf3669), fixed (both
de0337a7, the #57 power limit plus the eight-core GPU of #118), previous (both
3cf47fa7, the #57 module of omarchy-m4-gpu 1-1 and 1-2 and its test build),
and the states a power cut between the two writes can leave (module newer than
its pin). Stock and previous move to fixed. Anything else, such as a
hand-patched module, is refused and left untouched. --rollback asks the next
boot to restore the alpha.1 files and to stop applying the fix. Inspection is
the default.

Every report also names the GPU values the module's Air gate checks (core mask,
power limit and firmware build), so a refused GPU can be read from the journal.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

NAME = 'asahi_m4'
MODULE = 'opt/m4-gpu/asahi_m4.ko'
SUMS = 'opt/m4-gpu/runtime.sha256'
STAGED = 'usr/lib/omarchy-m4/gpu/asahi_m4.ko'
STATE = 'var/lib/omarchy-m4/gpu-module'
# Where gpu/j713/m4-gpu-test-module keeps alpha.1 on an Air that ran the test build.
TEST_BACKUP = 'opt/m4-gpu/alpha1-backup'
GPU_NODE = 'sys/firmware/devicetree/base/soc/gpu@302600000'
STOCK = {'sha256': '80cf3669575edd09aed226a93e47aaaf132e70f1070154629c413c0b40d34578', 'size': 388216}
FIXED = {'sha256': 'de0337a773930c7862a99706112dd71eaaecf2021707da248fd3d07feefdc824', 'size': 390816}
# Earlier fixes this one replaces, oldest first: #57's module (1-1, 1-2).
PREVIOUS = ({'sha256': '3cf47fa706630ddc5fc55a3bce00ab31e8a4c5ee48e1ba563e26a0406da196f8', 'size': 390752},)


class Refused(Exception):
    pass


def require(condition, message):
    if not condition:
        raise Refused(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def pinned(sums):
    """The digest runtime.sha256 pins for the module, or None unless exactly one line does."""
    lines = [line.split() for line in sums.decode().splitlines()]
    digests = [fields[0] for fields in lines if len(fields) == 2 and fields[1] == '/' + MODULE]
    return digests[0] if len(digests) == 1 else None


def with_pin(sums, digest):
    out = []
    for line in sums.decode().splitlines(keepends=True):
        fields = line.split()
        out.append(f'{digest}  /{MODULE}\n' if len(fields) == 2 and fields[1] == '/' + MODULE else line)
    return ''.join(out).encode()


def write(path, data, mode):
    """Replace path atomically and durably, keeping mode; the caller fixed the order."""
    temp = path.with_name(path.name + '.m4-gpu-new')
    temp.unlink(missing_ok=True)
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temp, mode)
    os.replace(temp, path)
    directory = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def regular(path):
    require(path.is_file() and not path.is_symlink(), 'Missing or not a regular file: /' + str(path))
    return path.read_bytes()


def gpu_values(root):
    """The devicetree values gpu/j713/asahi/g16.rs gates on; None where one is missing."""
    node = root / GPU_NODE

    def read(name):
        try:
            return (node / name).read_bytes()
        except OSError:
            return None

    def cell(name):
        raw = read(name)
        return int.from_bytes(raw, 'big') if raw is not None and len(raw) == 4 else None

    mask, build = cell('apple,m4-core-mask'), read('apple,firmware-build')
    try:
        build = build.rstrip(b'\0').decode('ascii') if build else None
    except UnicodeDecodeError:
        build = None
    return {'core_mask': None if mask is None else f'{mask:#x}',
            'max_power_mw': cell('apple,m4-max-power-mw'), 'firmware_build': build or None}


def inspect(root):
    sums = regular(root / SUMS)
    module_sha, pin = sha(regular(root / MODULE)), pinned(sums)
    stage = root / STATE
    report = {'module': module_sha, 'pinned': pin, 'held': (stage / 'hold').exists(),
              'rollback_requested': (stage / 'rollback').exists(),
              'loaded': (root / 'sys/module' / NAME).exists(), 'gpu': gpu_values(root)}
    # Each swap writes the module first and its pin last, so a power cut can
    # leave the module one step ahead of its pin, never behind.
    order = [STOCK['sha256']] + [old['sha256'] for old in PREVIOUS] + [FIXED['sha256']]
    if module_sha == pin == FIXED['sha256']:
        report['state'] = 'fixed'
    elif module_sha == pin == STOCK['sha256']:
        report['state'] = 'stock'
    elif module_sha == pin and module_sha in order:
        report['state'] = 'previous'
    elif module_sha in order and pin in order and order.index(pin) < order.index(module_sha):
        report['state'] = 'interrupted'
    else:
        report['state'] = 'unknown'
    return report, sums


def apply(root, report, sums):
    require(not report['loaded'], 'The GPU module is loaded; this applies at the next boot')
    staged = regular(root / STAGED)
    require(sha(staged) == FIXED['sha256'] and len(staged) == FIXED['size'], 'Staged module differs from the pin')
    stage = root / STATE
    stage.mkdir(parents=True, exist_ok=True)
    module, sums_path = root / MODULE, root / SUMS
    if report['state'] == 'stock':
        for name, path in (('module.ko', module), ('runtime.sha256', sums_path)):
            backup = stage / ('alpha1-' + name)
            if not backup.exists():
                write(backup, path.read_bytes(), 0o600)
    if report['module'] != FIXED['sha256']:
        # Module first, pin last: an interruption leaves the 'interrupted'
        # state, which the next boot completes before m4-gpu-start runs.
        write(module, staged, module.stat().st_mode & 0o777)
    write(sums_path, with_pin(sums, FIXED['sha256']), sums_path.stat().st_mode & 0o777)
    return 'applied'


def backups(root):
    """The alpha.1 module and runtime.sha256 to restore: this tool's, else the test helper's."""
    for directory, module, sums in ((root / STATE, 'alpha1-module.ko', 'alpha1-runtime.sha256'),
                                    (root / TEST_BACKUP, 'asahi_m4.ko', 'runtime.sha256')):
        if (directory / module).is_file() and (directory / sums).is_file():
            return (directory / module).read_bytes(), (directory / sums).read_bytes()
    raise Refused('No alpha.1 backup to restore')


def rollback(root, report):
    require(not report['loaded'], 'The GPU module is loaded; request the rollback without --boot')
    old_module, old_sums = backups(root)
    require(sha(old_module) == STOCK['sha256'], 'Backed-up module is not the alpha.1 module')
    require(pinned(old_sums) == STOCK['sha256'], 'Backed-up runtime.sha256 does not pin the alpha.1 module')
    stage = root / STATE
    stage.mkdir(parents=True, exist_ok=True)
    (stage / 'hold').touch()  # never apply the fix again until the hold is removed
    module, sums_path = root / MODULE, root / SUMS
    write(sums_path, old_sums, sums_path.stat().st_mode & 0o777)
    write(module, old_module, module.stat().st_mode & 0o777)
    (stage / 'rollback').unlink(missing_ok=True)
    return 'rolled back to the alpha.1 GPU module'


def run(root, mode):
    report, sums = inspect(root)
    stage = root / STATE
    if mode == 'request-rollback':
        require(report['state'] in ('fixed', 'previous', 'interrupted'),
                'Nothing to roll back: state is ' + report['state'])
        stage.mkdir(parents=True, exist_ok=True)
        (stage / 'rollback').touch()
        report['status'] = 'rollback requested; restart to restore the alpha.1 GPU module'
    elif mode == 'boot':
        if report['rollback_requested']:
            report['status'] = rollback(root, report)
        elif report['held']:
            report['status'] = 'held after a rollback; nothing applied'
        elif report['state'] in ('stock', 'previous', 'interrupted'):
            report['status'] = apply(root, report, sums) + '; the fixed module loads this boot'
        elif report['state'] == 'fixed':
            report['status'] = 'current'
        else:
            raise Refused('Unknown GPU module state; nothing was changed')
    else:
        report['status'] = {'fixed': 'current', 'stock': 'staged; applies at the next boot',
                            'previous': 'newer fix staged; applies at the next boot',
                            'interrupted': 'interrupted; completes at the next boot',
                            'unknown': 'unknown state; the fix will not be applied'}[report['state']]
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--boot', action='store_true',
                        help='apply or roll back now; only m4-gpu.service runs this')
    action.add_argument('--rollback', action='store_true', help='restore the alpha.1 module at the next boot')
    parser.add_argument('--root', type=Path, default=Path('/'), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.root == Path('/') and (args.boot or args.rollback) and os.geteuid() != 0:
        print('m4-gpu-module: run with sudo', file=sys.stderr)
        return 1
    mode = 'boot' if args.boot else 'request-rollback' if args.rollback else 'inspect'
    try:
        report = run(args.root, mode)
    except (Refused, OSError, ValueError, UnicodeDecodeError) as error:
        print('m4-gpu-module: refused: ' + str(error), file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
