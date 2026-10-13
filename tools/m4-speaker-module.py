#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Apply the fixed J713 speaker protection module (#61) on an installed alpha.1 Air.

The audio modules are pinned in /etc/omarchy/j713-audio-manifest.json, and the
register and readiness services refuse any module or manifest that disagrees.
This tool replaces snd-soc-j713-group.ko and exactly the six manifest values
that pin it, and nothing else. It only acts at boot, from the drop-in that runs
it before m4-j713-audio-register.service loads any module, because a swap in a
running session would fail the audio readiness gate until the next boot.

Known states: stock alpha.1 (module and manifest both 465a88aa), fixed (both
aaad802e) and the one state a power cut between the two writes can leave
(module fixed, manifest stock). Anything else is refused and left untouched.
--rollback asks the next boot to restore the backed-up alpha.1 files and to
stop applying the fix. Inspection is the default.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

RELEASE = '7.1.9-m4-gpu-backport-20261008'
NAME = 'snd_soc_j713_group'
MODULE = f'usr/lib/omarchy/j713-audio/{RELEASE}/modules/snd-soc-j713-group.ko'
MANIFEST = 'etc/omarchy/j713-audio-manifest.json'
STAGED = 'usr/lib/omarchy-m4/j713-audio/snd-soc-j713-group.ko'
STATE = 'var/lib/omarchy-m4/speaker-module'
STOCK = {'sha256': '465a88aa9f70ceac8682499ad6bd9ecd34a33b35a3b5c556b42f7bd1524272d0', 'size': 52088,
         'note': '040000001400000003000000474e5500f2078435f633f3e320dc95119734cfd263d6edd9'}
FIXED = {'sha256': 'aaad802eb136ff6cb915feaa123001b3eb848bc2f038d0a5b71c8e2ff35d2581', 'size': 52000,
         'note': '040000001400000003000000474e55009e792aa072fe3a8c75354bcc631fae233c7c014b'}


class Refused(Exception):
    pass


def require(condition, message):
    if not condition:
        raise Refused(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def serialize(manifest):
    # The alpha.1 image tools write the manifest exactly this way.
    return (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode()


def pins(manifest):
    """The six manifest values that pin the module, as one comparable record."""
    artifact = manifest['module_artifacts'][NAME]
    metadata = manifest['file_metadata'][MODULE]
    return {'files': manifest['files']['/' + MODULE], 'metadata_sha256': metadata['sha256'],
            'metadata_size': metadata['size'], 'artifact_sha256': artifact['sha256'],
            'artifact_size': artifact['size'], 'artifact_note': artifact['note'], 'modules': manifest['modules'][NAME]}


def expected(identity):
    return {'files': identity['sha256'], 'metadata_sha256': identity['sha256'], 'metadata_size': identity['size'],
            'artifact_sha256': identity['sha256'], 'artifact_size': identity['size'],
            'artifact_note': identity['note'], 'modules': identity['note']}


def with_fixed_pins(manifest):
    updated = json.loads(json.dumps(manifest))
    updated['files']['/' + MODULE] = FIXED['sha256']
    updated['file_metadata'][MODULE].update(sha256=FIXED['sha256'], size=FIXED['size'])
    updated['module_artifacts'][NAME].update(sha256=FIXED['sha256'], size=FIXED['size'], note=FIXED['note'])
    updated['modules'][NAME] = FIXED['note']
    return updated


def write(path, data, mode):
    """Replace path atomically and durably, keeping mode; the caller fixed the order."""
    temp = path.with_name(path.name + '.m4-speaker-new')
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


def inspect(root):
    module = root / MODULE
    manifest_path = root / MANIFEST
    raw = regular(manifest_path)
    manifest = json.loads(raw)
    require(manifest.get('kernel_release') == RELEASE, 'Audio manifest is for another kernel release')
    module_sha = sha(regular(module))
    record = pins(manifest)
    stage = root / STATE
    report = {'module': module_sha, 'held': (stage / 'hold').exists(),
              'rollback_requested': (stage / 'rollback').exists(),
              'loaded': (root / 'sys/module' / NAME).exists()}
    if module_sha == FIXED['sha256'] and record == expected(FIXED):
        report['state'] = 'fixed'
    elif module_sha == STOCK['sha256'] and record == expected(STOCK):
        report['state'] = 'stock'
    elif module_sha == FIXED['sha256'] and record == expected(STOCK):
        report['state'] = 'interrupted'  # a power cut between the module and manifest writes
    else:
        report['state'] = 'unknown'
    return report, manifest, raw


def apply(root, report, manifest, raw):
    require(not report['loaded'], 'The speaker module is loaded; this applies at the next boot')
    staged = regular(root / STAGED)
    require(sha(staged) == FIXED['sha256'] and len(staged) == FIXED['size'], 'Staged module differs from the pin')
    require(serialize(manifest) == raw, 'Audio manifest is not in the alpha.1 format; nothing was changed')
    stage = root / STATE
    stage.mkdir(parents=True, exist_ok=True)
    module, manifest_path = root / MODULE, root / MANIFEST
    if report['state'] == 'stock':
        for name, path in (('module.ko', module), ('manifest.json', manifest_path)):
            backup = stage / ('alpha1-' + name)
            if not backup.exists():
                write(backup, path.read_bytes(), 0o600)
        # Module first, manifest last: an interruption leaves the 'interrupted'
        # state, which the next boot completes before any module loads.
        write(module, staged, module.stat().st_mode & 0o777)
    updated = serialize(with_fixed_pins(manifest))
    write(manifest_path, updated, manifest_path.stat().st_mode & 0o777)
    return 'applied'


def rollback(root, report):
    require(not report['loaded'], 'The speaker module is loaded; request the rollback without --boot')
    stage = root / STATE
    module_backup, manifest_backup = stage / 'alpha1-module.ko', stage / 'alpha1-manifest.json'
    require(module_backup.is_file() and manifest_backup.is_file(), 'No alpha.1 backup to restore')
    old_module, old_manifest = module_backup.read_bytes(), manifest_backup.read_bytes()
    require(sha(old_module) == STOCK['sha256'], 'Backed-up module is not the alpha.1 module')
    require(pins(json.loads(old_manifest)) == expected(STOCK), 'Backed-up manifest is not the alpha.1 manifest')
    (stage / 'hold').touch()  # never apply the fix again until the hold is removed
    module, manifest_path = root / MODULE, root / MANIFEST
    write(manifest_path, old_manifest, manifest_path.stat().st_mode & 0o777)
    write(module, old_module, module.stat().st_mode & 0o777)
    (stage / 'rollback').unlink(missing_ok=True)
    return 'rolled back to the alpha.1 speaker module'


def run(root, mode):
    report, manifest, raw = inspect(root)
    stage = root / STATE
    if mode == 'request-rollback':
        require(report['state'] in ('fixed', 'interrupted'), 'Nothing to roll back: state is ' + report['state'])
        stage.mkdir(parents=True, exist_ok=True)
        (stage / 'rollback').touch()
        report['status'] = 'rollback requested; restart to restore the alpha.1 speaker module'
    elif mode == 'boot':
        if report['rollback_requested']:
            report['status'] = rollback(root, report)
        elif report['held']:
            report['status'] = 'held after a rollback; nothing applied'
        elif report['state'] in ('stock', 'interrupted'):
            report['status'] = apply(root, report, manifest, raw) + '; the fixed module loads this boot'
        elif report['state'] == 'fixed':
            report['status'] = 'current'
        else:
            raise Refused('Unknown speaker module state; nothing was changed')
    else:
        report['status'] = {'fixed': 'current', 'stock': 'staged; applies at the next boot',
                            'interrupted': 'interrupted; completes at the next boot',
                            'unknown': 'unknown state; the fix will not be applied'}[report['state']]
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--boot', action='store_true',
                        help='apply or roll back now; only m4-j713-audio-register.service runs this')
    action.add_argument('--rollback', action='store_true', help='restore the alpha.1 module at the next boot')
    parser.add_argument('--root', type=Path, default=Path('/'), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.root == Path('/') and (args.boot or args.rollback) and os.geteuid() != 0:
        print('m4-speaker-module: run with sudo', file=sys.stderr)
        return 1
    mode = 'boot' if args.boot else 'request-rollback' if args.rollback else 'inspect'
    try:
        report = run(args.root, mode)
    except (Refused, OSError, ValueError, KeyError) as error:
        print('m4-speaker-module: refused: ' + str(error), file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
