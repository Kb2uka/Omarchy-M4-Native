#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Apply one fixed J713 audio module on an installed alpha.1 Air.

The audio modules are pinned in /etc/omarchy/j713-audio-manifest.json, and the
register and readiness services refuse any module or manifest that disagrees.
The default component is the speaker module (snd-soc-j713-group.ko). MCA
(snd-soc-apple-mca.ko) is the same swap with its own paths, pins, backup, hold,
and rollback marker. Each run changes that component's module and exactly its
six manifest pins. Unrelated keys and the other component stay as they are.

The swap runs at boot, before the audio register service loads modules. Known
states for the selected component: stock, fixed, forward interruption (new
module bytes, old pins), and reverse interruption (old module bytes, new pins).
--rollback from fixed or forward-interrupted state requires both stock backup
halves to already be regular files; an absent or corrupt backup refuses and
writes no directory or marker. A reverse state is finished only when a
rollback marker is already present with a valid stock backup; a new request
does not create that marker. A boot that finds stock bytes, stock pins, a
valid stock backup, and that marker clears the marker and stays held.
Inspection only reads. A known state with an existing regular rollback marker
and a valid stock backup reports that rollback as requested and names the
component in the restart guidance, ahead of hold. Held stock, or a held
forward interruption, without that pending rollback reports that nothing
applies. A markerless reverse state reports the refusal and recovery from the
verified stock backup. A marker with an invalid backup or state does not
promise restoration. Inspection is the default.
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
MCA_NAME = 'snd_soc_apple_mca'
MCA_MODULE = f'usr/lib/omarchy/j713-audio/{RELEASE}/modules/snd-soc-apple-mca.ko'
MCA_STAGED = 'usr/lib/omarchy-m4/j713-audio/snd-soc-apple-mca.ko'
MCA_STATE = 'var/lib/omarchy-m4/mca-module'
MCA_STOCK = {'sha256': 'd3194660a28d30ba8ea296b6592836ad4ca9426d9471d05ba8ce3cad284baa1e', 'size': 79152,
             'note': '040000001400000003000000474e550057b999a68c50407955e8b2ed92f242b0cfe426e9'}
MCA_FIXED = {'sha256': 'fb941df4d55c4f2093b4595e0392aaf97ffe3a250228518b18a4f70568bf7454', 'size': 79152,
             'note': '040000001400000003000000474e55000a48e413fcbb318d62c1161c59fb2bef1de7df81'}


class Refused(Exception):
    pass


def require(condition, message):
    if not condition:
        raise Refused(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def record(component='speaker'):
    """Paths and pins for one component. Reads the module globals on each call."""
    if component == 'speaker':
        return {'component': 'speaker', 'name': NAME, 'module': MODULE, 'staged': STAGED, 'state': STATE,
                'stock': STOCK, 'fixed': FIXED}
    if component == 'mca':
        return {'component': 'mca', 'name': MCA_NAME, 'module': MCA_MODULE, 'staged': MCA_STAGED,
                'state': MCA_STATE, 'stock': MCA_STOCK, 'fixed': MCA_FIXED}
    raise Refused('Unknown component: ' + str(component))


def serialize(manifest):
    # The alpha.1 image tools write the manifest exactly this way.
    return (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode()


def pins(manifest, component='speaker'):
    """The six manifest values that pin one component, as one comparable record."""
    selected = record(component)
    artifact = manifest['module_artifacts'][selected['name']]
    metadata = manifest['file_metadata'][selected['module']]
    return {'files': manifest['files']['/' + selected['module']], 'metadata_sha256': metadata['sha256'],
            'metadata_size': metadata['size'], 'artifact_sha256': artifact['sha256'],
            'artifact_size': artifact['size'], 'artifact_note': artifact['note'],
            'modules': manifest['modules'][selected['name']]}


def expected(identity):
    return {'files': identity['sha256'], 'metadata_sha256': identity['sha256'], 'metadata_size': identity['size'],
            'artifact_sha256': identity['sha256'], 'artifact_size': identity['size'],
            'artifact_note': identity['note'], 'modules': identity['note']}


def with_pins(manifest, identity, component='speaker'):
    """Copy the manifest and replace only this component's six pins."""
    selected = record(component)
    updated = json.loads(json.dumps(manifest))
    updated['files']['/' + selected['module']] = identity['sha256']
    updated['file_metadata'][selected['module']].update(sha256=identity['sha256'], size=identity['size'])
    updated['module_artifacts'][selected['name']].update(
        sha256=identity['sha256'], size=identity['size'], note=identity['note'])
    updated['modules'][selected['name']] = identity['note']
    return updated


def with_fixed_pins(manifest):
    return with_pins(manifest, FIXED, 'speaker')


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


def marker(path):
    """Create an empty owner-only marker and fsync its directory.

    The mode applies only when this call creates the file. An existing marker
    keeps its mode and contents.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    directory = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def backup_paths(root, selected):
    stage = root / selected['state']
    return stage, stage / 'alpha1-module.ko', stage / 'alpha1-manifest.json'


def valid_stock_backup(module_backup, manifest_backup, selected):
    """True when both backup files are the stock module and the stock pins.

    A missing half, a symlink, or a manifest that is not those pins is false.
    """
    if module_backup.is_symlink() or manifest_backup.is_symlink():
        return False
    if not (module_backup.is_file() and manifest_backup.is_file()):
        return False
    module_bytes = module_backup.read_bytes()
    identity = selected['stock']
    if sha(module_bytes) != identity['sha256'] or len(module_bytes) != identity['size']:
        return False
    try:
        saved = json.loads(manifest_backup.read_bytes())
        return pins(saved, selected['component']) == expected(identity)
    except (ValueError, KeyError, TypeError, AttributeError, UnicodeError):
        return False


def inspect(root, component='speaker'):
    selected = record(component)
    module = root / selected['module']
    manifest_path = root / MANIFEST
    raw = regular(manifest_path)
    manifest = json.loads(raw)
    require(manifest.get('kernel_release') == RELEASE, 'Audio manifest is for another kernel release')
    module_sha = sha(regular(module))
    found = pins(manifest, selected['component'])
    stage = root / selected['state']
    report = {'module': module_sha, 'held': (stage / 'hold').exists(),
              'rollback_requested': (stage / 'rollback').exists(),
              'loaded': (root / 'sys/module' / selected['name']).exists(),
              'component': selected['component']}
    if module_sha == selected['fixed']['sha256'] and found == expected(selected['fixed']):
        report['state'] = 'fixed'
    elif module_sha == selected['stock']['sha256'] and found == expected(selected['stock']):
        report['state'] = 'stock'
    elif module_sha == selected['fixed']['sha256'] and found == expected(selected['stock']):
        report['state'] = 'interrupted'  # a power cut between the module and manifest writes
    elif module_sha == selected['stock']['sha256'] and found == expected(selected['fixed']):
        report['state'] = 'reverse'  # old bytes with new pins; only a rollback may finish this
    else:
        report['state'] = 'unknown'
    return report, manifest, raw


def backup_half_present(path):
    return path.exists() or path.is_symlink()


def stock_module_half(path, selected):
    """True when this backup half is the regular stock module. A symlink is not."""
    if path.is_symlink() or not path.is_file():
        return False
    module_bytes = path.read_bytes()
    identity = selected['stock']
    return sha(module_bytes) == identity['sha256'] and len(module_bytes) == identity['size']


def stock_manifest_half(path, selected):
    """True when this backup half is a regular manifest with the stock pins."""
    if path.is_symlink() or not path.is_file():
        return False
    try:
        saved = json.loads(path.read_bytes())
        return pins(saved, selected['component']) == expected(selected['stock'])
    except (ValueError, KeyError, TypeError):
        return False


def prepare_backup(root, selected, manifest_bytes):
    """Create a stock backup from the current stock files, or finish one missing half.

    Called only for a live stock module with stock pins. Each existing half is
    judged on its own before any backup write. A wrong, corrupt, or symlink
    half refuses with neither half overwritten. A valid complete backup is left
    unchanged. The missing half is copied from the current stock files.
    """
    stage, module_backup, manifest_backup = backup_paths(root, selected)
    module_present = backup_half_present(module_backup)
    manifest_present = backup_half_present(manifest_backup)
    module_ok = stock_module_half(module_backup, selected) if module_present else False
    manifest_ok = stock_manifest_half(manifest_backup, selected) if manifest_present else False
    if (module_present and not module_ok) or (manifest_present and not manifest_ok):
        raise Refused('Existing backup does not match the stock ' + selected['component']
                      + ' module and pins')
    if module_present and manifest_present:
        return
    live = regular(root / selected['module'])
    identity = selected['stock']
    require(sha(live) == identity['sha256'] and len(live) == identity['size'],
            'Stock backup does not match the selected pins')
    try:
        live_manifest = json.loads(manifest_bytes)
    except ValueError:
        raise Refused('Audio manifest is not in the alpha.1 format; nothing was changed') from None
    require(pins(live_manifest, selected['component']) == expected(identity),
            'Stock backup does not match the selected pins')
    stage.mkdir(parents=True, exist_ok=True)
    if not module_present:
        write(module_backup, live, 0o600)
    if not manifest_present:
        write(manifest_backup, manifest_bytes, 0o600)
    require(valid_stock_backup(module_backup, manifest_backup, selected),
            'Stock backup does not match the selected pins')


def apply(root, report, manifest, raw, component='speaker'):
    selected = record(component)
    require(not report['loaded'], 'The ' + selected['name'] + ' module is loaded; this applies at the next boot')
    staged = regular(root / selected['staged'])
    identity = selected['fixed']
    require(sha(staged) == identity['sha256'] and len(staged) == identity['size'], 'Staged module differs from the pin')
    # Identity is the inspected pins, the staged module, and the stock backup.
    # A valid manifest may omit the final newline or use another indent or key order.
    module = root / selected['module']
    if report['state'] == 'stock':
        prepare_backup(root, selected, raw)
        # Module first, manifest last: an interruption leaves the forward state,
        # which the next boot completes before this module loads.
        write(module, staged, module.stat().st_mode & 0o777)
    elif report['state'] == 'interrupted':
        _, module_backup, manifest_backup = backup_paths(root, selected)
        require(valid_stock_backup(module_backup, manifest_backup, selected),
                'Interrupted swap has no valid stock backup')
    updated = serialize(with_pins(manifest, identity, selected['component']))
    write(root / MANIFEST, updated, (root / MANIFEST).stat().st_mode & 0o777)
    return 'applied'


def finish_restored_rollback(root, selected, stage):
    """Stock bytes, stock pins, and a stock backup: clear the marker and stay held.

    The module and the manifest are not written. The marker is removed only
    after the live component and both backup halves have been checked.
    """
    module = root / selected['module']
    manifest_path = root / MANIFEST
    module_bytes = regular(module)
    identity = selected['stock']
    require(sha(module_bytes) == identity['sha256'] and len(module_bytes) == identity['size'],
            'Rollback is incomplete; nothing was changed')
    current = json.loads(regular(manifest_path))
    require(pins(current, selected['component']) == expected(identity),
            'Rollback is incomplete; nothing was changed')
    _, module_backup, manifest_backup = backup_paths(root, selected)
    require(valid_stock_backup(module_backup, manifest_backup, selected),
            'Backed-up module is not the alpha.1 ' + selected['component'] + ' module and pins')
    intent = stage / 'rollback'
    require(intent.is_file() and not intent.is_symlink(),
            'Rollback request is missing; nothing was changed')
    marker(stage / 'hold')
    intent.unlink()
    directory = os.open(stage, os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return 'held after a rollback; nothing applied'


def rollback(root, report, component='speaker'):
    selected = record(component)
    require(not report['loaded'],
            'The ' + selected['name'] + ' module is loaded; request the rollback without --boot')
    stage, module_backup, manifest_backup = backup_paths(root, selected)
    if report['state'] == 'stock':
        return finish_restored_rollback(root, selected, stage)
    require(report['state'] in ('fixed', 'interrupted', 'reverse'),
            'Nothing to roll back: state is ' + report['state'])
    require(valid_stock_backup(module_backup, manifest_backup, selected),
            'Backed-up module is not the alpha.1 ' + selected['component'] + ' module and pins')
    # Hold and the rollback marker are durable before either payload write.
    marker(stage / 'hold')
    module = root / selected['module']
    manifest_path = root / MANIFEST
    write(module, module_backup.read_bytes(), module.stat().st_mode & 0o777)
    current = json.loads(regular(manifest_path))
    restored = serialize(with_pins(current, selected['stock'], selected['component']))
    write(manifest_path, restored, manifest_path.stat().st_mode & 0o777)
    (stage / 'rollback').unlink(missing_ok=True)
    directory = os.open(stage, os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return 'rolled back to the alpha.1 ' + selected['component'] + ' module'


def run(root, mode, component='speaker'):
    selected = record(component)
    report, manifest, raw = inspect(root, selected['component'])
    stage = root / selected['state']
    if mode == 'request-rollback':
        # Backup halves are judged before any directory or marker write.
        _, module_backup, manifest_backup = backup_paths(root, selected)
        if report['state'] == 'reverse':
            # Old bytes with new pins are not a new request. An existing marker
            # plus a valid stock backup stays as it is and creates nothing.
            intent = stage / 'rollback'
            require(intent.is_file() and not intent.is_symlink()
                    and valid_stock_backup(module_backup, manifest_backup, selected),
                    'Interrupted rollback without a rollback request; nothing was changed')
        else:
            require(report['state'] in ('fixed', 'interrupted'),
                    'Nothing to roll back: state is ' + report['state'])
            require(valid_stock_backup(module_backup, manifest_backup, selected),
                    'Backed-up module is not the alpha.1 ' + selected['component'] + ' module and pins')
            stage.mkdir(parents=True, exist_ok=True)
            marker(stage / 'rollback')
        report['status'] = ('rollback requested; restart to restore the alpha.1 '
                            + selected['component'] + ' module')
    elif mode == 'boot':
        if report['rollback_requested']:
            report['status'] = rollback(root, report, selected['component'])
        elif report['state'] == 'reverse':
            raise Refused('Interrupted rollback without a rollback request; nothing was changed')
        elif report['held']:
            report['status'] = 'held after a rollback; nothing applied'
        elif report['state'] in ('stock', 'interrupted'):
            report['status'] = apply(root, report, manifest, raw, selected['component']) + '; the fixed module loads this boot'
        elif report['state'] == 'fixed':
            report['status'] = 'current'
        else:
            raise Refused('Unknown ' + selected['component'] + ' module state; nothing was changed')
    else:
        # Read-only, in the same order as boot. No marker or backup is created.
        intent = stage / 'rollback'
        regular_marker = intent.is_file() and not intent.is_symlink()
        known = report['state'] in ('stock', 'fixed', 'interrupted', 'reverse')
        _, module_backup, manifest_backup = backup_paths(root, selected)
        backup_ok = (known and report['rollback_requested'] and
                     valid_stock_backup(module_backup, manifest_backup, selected))
        if known and regular_marker and backup_ok:
            report['status'] = ('rollback requested; restart to restore the alpha.1 '
                                + selected['component'] + ' module')
        elif report['state'] == 'reverse' and not report['rollback_requested']:
            report['status'] = ('refused; nothing was changed; recover from the verified stock backup')
        elif known and report['rollback_requested'] and (not regular_marker or not backup_ok):
            report['status'] = 'refused; nothing was changed; recovery required'
        elif report['held'] and report['state'] in ('stock', 'interrupted'):
            report['status'] = 'held after a rollback; nothing applied'
        else:
            report['status'] = {'fixed': 'current', 'stock': 'staged; applies at the next boot',
                                'interrupted': 'interrupted; completes at the next boot',
                                'reverse': 'interrupted rollback; waiting for a rollback request',
                                'unknown': 'unknown state; the fix will not be applied'}[report['state']]
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--boot', action='store_true',
                        help='apply or roll back now; only m4-j713-audio-register.service runs this')
    action.add_argument('--rollback', action='store_true', help='restore the alpha.1 module at the next boot')
    parser.add_argument('--component', choices=('speaker', 'mca'), default='speaker',
                        help='audio component to inspect or swap (default: speaker)')
    parser.add_argument('--root', type=Path, default=Path('/'), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.root == Path('/') and (args.boot or args.rollback) and os.geteuid() != 0:
        print('m4-speaker-module: run with sudo', file=sys.stderr)
        return 1
    mode = 'boot' if args.boot else 'request-rollback' if args.rollback else 'inspect'
    try:
        report = run(args.root, mode, args.component)
    except (Refused, OSError, ValueError, KeyError) as error:
        print('m4-speaker-module: refused: ' + str(error), file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
