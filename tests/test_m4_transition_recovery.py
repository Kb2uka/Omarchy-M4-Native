# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Recovery around a pending boot-compose journal.

A valid pending journal must keep m4-notch from replacing the loader. Show and
hide refuse before they write the backup, boot.bin, the video manifest, or the
journal. Resuming the same composer operation afterwards still commits.

Root space for the two snapshots is required once, when they are saved. After
those files exist, the root filesystem only has to keep the margin; the EFI
partition is a different statvfs path and stays ample here.

A write that lands and then raises is retried with the same command. The
journal clears and owner boot segments stay with the unrelated video fields.
A rollback that dies after the boot write still finishes on the retry, and
the durable boot-compose hold remains so the packaged Image is not composed
back. Show and hide name that pending journal when they refuse.

Root space for the two snapshots is the root filesystem only when that is
where the store lives. The nearest existing parent of the snapshot directory
is the filesystem that must hold them. Ample space on the root filesystem
does not pay for a short snapshot store, and the refusal comes before any
snapshot, boot, or video write. Once the snapshot files exist, resume still
needs only the margin.
"""
import contextlib
import io
import json
from pathlib import Path
import struct
import types
import unittest
from unittest import mock

import test_m4_boot_compose as boot_tests
import test_m4_notch as notch_tests


NOTCH = notch_tests.NOTCH
COMPOSE = notch_tests.COMPOSE
MODULE = boot_tests.MODULE
BOOT_JOURNAL = boot_tests.BOOT_JOURNAL
BOOT_SNAPSHOTS = boot_tests.BOOT_SNAPSHOTS


def run_module(module, root, *args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = module.main(['--root', str(root), *args])
    return code, out.getvalue(), err.getvalue()


def listed_machine(test, boot_kernel, package_kernel):
    """Kernel pair the composer will journal. Owner loader, args, and initramfs match."""
    loader = boot_tests.LOADER + b' notch'
    current = boot_tests.boot(kernel_bytes=boot_kernel, dtb=boot_tests.D1, loader=loader)
    machine = boot_tests.Machine(
        test, boot_bytes=current, package_kernel=package_kernel, package_dtb=boot_tests.D1)
    releases = {boot_tests.sha(boot_kernel): boot_tests.RELEASE,
                boot_tests.sha(package_kernel): boot_tests.RELEASE}
    pairs = {(boot_tests.sha(boot_kernel), boot_tests.sha(package_kernel)),
             (boot_tests.sha(package_kernel), boot_tests.sha(boot_kernel))}
    for patcher in (mock.patch.dict(machine.module.KNOWN_KERNELS, releases, clear=True),
                    mock.patch.object(machine.module, 'KERNEL_TRANSITIONS', pairs)):
        patcher.start()
        test.addCleanup(patcher.stop)
    video = {'boot_kernel_sha256': boot_tests.sha(boot_kernel), 'kernel_release': boot_tests.RELEASE,
             'owner_panel': 'kept', 'notch': 'owner'}
    machine.put(machine.module.VIDEO_MANIFEST, (json.dumps(video, indent=4) + '\n').encode())
    machine.video_before = (machine.root / machine.module.VIDEO_MANIFEST).read_bytes()
    machine.audio_before = (machine.root / machine.module.AUDIO_MANIFEST).read_bytes()
    machine.boot_before = current
    return machine


def plant(machine, operation, from_bytes, to_bytes, video_pin):
    module = machine.module
    from_sha, to_sha = module.sha(from_bytes), module.sha(to_bytes)
    machine.put(module.JOURNAL, (json.dumps({
        'operation': operation, 'from_sha256': from_sha, 'to_sha256': to_sha,
        'video_boot_kernel_sha256': video_pin}, indent=2, sort_keys=True) + '\n').encode())
    machine.put(module.SNAPSHOTS + '/' + from_sha, from_bytes)
    machine.put(module.SNAPSHOTS + '/' + to_sha, to_bytes)


def space_by_snapshots(machine, current, desired):
    """EFI answers are ample. Root drops to the margin once either snapshot file exists."""
    module = machine.module
    efi = machine.root / 'boot' / 'efi'
    snaps = machine.root / module.SNAPSHOTS
    pair = len(current) + len(desired) + module.MARGIN

    def fake(path):
        probed = Path(path)
        if probed == efi or efi in probed.parents:
            return types.SimpleNamespace(f_bavail=1 << 40, f_frsize=1)
        have = snaps.is_dir() and any(item.is_file() for item in snaps.iterdir())
        return types.SimpleNamespace(f_bavail=module.MARGIN if have else pair, f_frsize=1)

    return fake


def fail_after(module, name):
    """Call the real write, then raise once when the final path name matches."""
    real = module.write
    fired = {'n': 0}

    def wrapped(path, data):
        real(path, data)
        if fired['n'] == 0 and Path(path).name == name:
            fired['n'] = 1
            raise OSError('injected write failure')

    return wrapped, fired


class NotchPendingJournalTests(unittest.TestCase):
    def pins(self):
        compose = COMPOSE
        k1, k2, d1 = notch_tests.K1, notch_tests.K2, notch_tests.D1
        release = '7.1.9-recovery'
        patchers = [
            mock.patch.dict(compose.KNOWN_KERNELS, {notch_tests.sha(k1): release, notch_tests.sha(k2): release},
                            clear=True),
            mock.patch.dict(compose.KNOWN_DTBS, {notch_tests.sha(d1): 'alpha.1'}, clear=True),
            mock.patch.object(compose, 'KERNEL_TRANSITIONS', {
                (notch_tests.sha(k1), notch_tests.sha(k2)), (notch_tests.sha(k2), notch_tests.sha(k1))}),
            mock.patch.dict(compose.KNOWN_LOADERS, {
                notch_tests.sha(notch_tests.STOCK_LOADER): 'hidden',
                notch_tests.sha(notch_tests.NOTCH_LOADER): 'shown'}, clear=True),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        return compose, release

    def air(self, loader):
        compose, release = self.pins()
        air = notch_tests.Air(self, loader=loader)
        k1 = notch_tests.sha(notch_tests.K1)
        air.put(compose.VIDEO_MANIFEST, (json.dumps({
            'boot_kernel_sha256': k1, 'kernel_release': release, 'owner_panel': 'kept'}) + '\n').encode())
        air.put(compose.AUDIO_MANIFEST, json.dumps({'kernel_release': release}).encode())
        (air.root / 'usr/lib/modules' / release).mkdir(parents=True)
        return air, compose

    def snapshot(self, air, compose):
        paths = [NOTCH.BOOT, NOTCH.BACKUP, compose.VIDEO_MANIFEST, compose.AUDIO_MANIFEST,
                 compose.JOURNAL, compose.BOOT + compose.PREVIOUS, NOTCH.PACMAN_LOCK]
        snap_dir = air.root / compose.SNAPSHOTS
        if snap_dir.is_dir():
            paths.extend(compose.SNAPSHOTS + '/' + item.name for item in snap_dir.iterdir())
        found = {}
        for relative in paths:
            path = air.root / relative
            found[relative] = path.read_bytes() if path.is_file() else None
        return found

    def refuse(self, air, compose, action):
        before = self.snapshot(air, compose)
        with self.assertRaises((NOTCH.Refused, compose.Refused)):
            getattr(NOTCH, action)(air.root)
        self.assertEqual(self.snapshot(air, compose), before)
        self.assertIsNone(before[NOTCH.BACKUP])
        self.assertFalse((air.root / NOTCH.PACMAN_LOCK).exists())

    def test_show_and_hide_refuse_pending_apply_and_rollback_then_resume(self):
        # Already-shown / already-hidden return without writing; those are not this check.
        for operation in ('apply', 'rollback'):
            for action, loader in (('show', notch_tests.STOCK_LOADER), ('hide', notch_tests.NOTCH_LOADER)):
                with self.subTest(operation=operation, action=action):
                    air, compose = self.air(loader)
                    live = notch_tests.boot(loader=loader, kernel=notch_tests.K1)
                    other = notch_tests.boot(loader=loader, kernel=notch_tests.K2)
                    if operation == 'apply':
                        from_bytes, to_bytes = live, other
                        pin = notch_tests.sha(notch_tests.K2)
                        args = ('--apply',)
                    else:
                        from_bytes, to_bytes = other, live
                        pin = notch_tests.sha(notch_tests.K1)
                        air.put(compose.VIDEO_MANIFEST, (json.dumps({
                            'boot_kernel_sha256': notch_tests.sha(notch_tests.K2),
                            'kernel_release': '7.1.9-recovery', 'owner_panel': 'kept'}) + '\n').encode())
                        args = ('--rollback', '--apply')
                    air.put(NOTCH.BOOT, from_bytes)
                    plant(types.SimpleNamespace(module=compose, put=air.put), operation, from_bytes, to_bytes, pin)
                    self.refuse(air, compose, action)
                    code, _, err = run_module(compose, air.root, *args)
                    self.assertEqual((code, err), (0, ''), err)
                    self.assertEqual((air.root / compose.BOOT).read_bytes(), to_bytes)
                    self.assertEqual((air.root / (compose.BOOT + compose.PREVIOUS)).read_bytes(), from_bytes)
                    self.assertFalse((air.root / compose.JOURNAL).exists())
                    video = json.loads((air.root / compose.VIDEO_MANIFEST).read_bytes())
                    self.assertEqual(video['boot_kernel_sha256'], pin)
                    self.assertEqual(video['owner_panel'], 'kept')
                    self.assertEqual(json.loads((air.root / compose.AUDIO_MANIFEST).read_bytes())['kernel_release'],
                                     '7.1.9-recovery')

    def test_pending_refusal_names_the_journal(self):
        """The notch tools name the pending journal and the command that resumes it."""
        for operation in ('apply', 'rollback'):
            for action, loader in (('show', notch_tests.STOCK_LOADER), ('hide', notch_tests.NOTCH_LOADER)):
                with self.subTest(operation=operation, action=action):
                    air, compose = self.air(loader)
                    live = notch_tests.boot(loader=loader, kernel=notch_tests.K1)
                    other = notch_tests.boot(loader=loader, kernel=notch_tests.K2)
                    if operation == 'apply':
                        from_bytes, to_bytes = live, other
                        pin = notch_tests.sha(notch_tests.K2)
                        command = 'sudo m4-boot-compose --apply'
                    else:
                        from_bytes, to_bytes = other, live
                        pin = notch_tests.sha(notch_tests.K1)
                        air.put(compose.VIDEO_MANIFEST, (json.dumps({
                            'boot_kernel_sha256': notch_tests.sha(notch_tests.K2),
                            'kernel_release': '7.1.9-recovery', 'owner_panel': 'kept'}) + '\n').encode())
                        command = 'sudo m4-boot-compose --rollback --apply'
                    air.put(NOTCH.BOOT, from_bytes)
                    plant(types.SimpleNamespace(module=compose, put=air.put), operation, from_bytes, to_bytes, pin)
                    before = self.snapshot(air, compose)
                    with self.assertRaises((NOTCH.Refused, compose.Refused)) as caught:
                        getattr(NOTCH, action)(air.root)
                    message = str(caught.exception)
                    self.assertIn('pending', message)
                    self.assertIn('journal', message)
                    self.assertIn(command, message)
                    self.assertEqual(self.snapshot(air, compose), before)
                    self.assertIsNone(before[NOTCH.BACKUP])
                    self.assertFalse((air.root / NOTCH.PACMAN_LOCK).exists())

    def test_corrupt_and_wrong_journal_refuse_before_notch_writes(self):
        def corrupt(air, compose, live):
            air.put(compose.JOURNAL, b'{not a journal')

        def wrong_snapshot(air, compose, live):
            other = notch_tests.boot(loader=notch_tests.STOCK_LOADER if live.startswith(notch_tests.STOCK_LOADER)
                                     else notch_tests.NOTCH_LOADER, kernel=notch_tests.K2)
            plant(types.SimpleNamespace(module=compose, put=air.put), 'apply', live, other,
                  notch_tests.sha(notch_tests.K2))
            air.put(compose.SNAPSHOTS + '/' + compose.sha(live), b'truncated snapshot')

        for label, setup in (('corrupt', corrupt), ('wrong-snapshot', wrong_snapshot)):
            for action, loader in (('show', notch_tests.STOCK_LOADER), ('hide', notch_tests.NOTCH_LOADER)):
                with self.subTest(label=label, action=action):
                    air, compose = self.air(loader)
                    setup(air, compose, air.boot_bytes())
                    self.refuse(air, compose, action)

    def test_journal_refuses_wrong_video_pin_and_foreign_owner_before_writes(self):
        """A planted journal whose snapshot hashes match must still be refused.

        The video pin and the owner check run after the snapshot hash check.
        ``save_journal`` cannot build these fixtures: it rejects them itself.
        Inspection, apply, and notch show/hide all stop before any write.
        """
        def foreign_bootargs(live):
            loader = notch_tests.STOCK_LOADER if live.startswith(notch_tests.STOCK_LOADER) else notch_tests.NOTCH_LOADER
            other = (loader + b'chosen.bootargs=root=UUID=other-machine\n' + notch_tests.K2 +
                     notch_tests.D1 + notch_tests.INITRAMFS)
            return live, other, notch_tests.sha(notch_tests.K2), 'same owner'

        def foreign_initramfs(live):
            loader = notch_tests.STOCK_LOADER if live.startswith(notch_tests.STOCK_LOADER) else notch_tests.NOTCH_LOADER
            firmware = b'070701 other machine firmware'
            initramfs = b'm1n1_initramfs' + struct.pack('<I', len(firmware)) + firmware
            other = loader + notch_tests.BOOTARGS + notch_tests.K2 + notch_tests.D1 + initramfs
            return live, other, notch_tests.sha(notch_tests.K2), 'same owner'

        def wrong_pin(live):
            other = notch_tests.boot(
                loader=notch_tests.STOCK_LOADER if live.startswith(notch_tests.STOCK_LOADER)
                else notch_tests.NOTCH_LOADER, kernel=notch_tests.K2)
            # Valid hash of the source kernel, not the target. The snapshot files
            # are stored under the digest of these exact bytes.
            return live, other, notch_tests.sha(notch_tests.K1), 'video pin'

        for label, build in (('wrong-video-pin', wrong_pin), ('foreign-bootargs', foreign_bootargs),
                             ('foreign-initramfs', foreign_initramfs)):
            for action, loader in (('show', notch_tests.STOCK_LOADER), ('hide', notch_tests.NOTCH_LOADER)):
                with self.subTest(label=label, action=action):
                    air, compose = self.air(loader)
                    live = air.boot_bytes()
                    from_bytes, to_bytes, pin, reason = build(live)
                    planted = types.SimpleNamespace(module=compose, put=air.put)
                    plant(planted, 'apply', from_bytes, to_bytes, pin)
                    self.assertEqual((air.root / (compose.SNAPSHOTS + '/' + compose.sha(from_bytes))).read_bytes(),
                                     from_bytes)
                    self.assertEqual((air.root / (compose.SNAPSHOTS + '/' + compose.sha(to_bytes))).read_bytes(),
                                     to_bytes)
                    with self.assertRaises(compose.Refused) as refused:
                        compose.load_journal(air.root)
                    self.assertIn(reason, str(refused.exception))
                    before = self.snapshot(air, compose)
                    code, out, err = run_module(compose, air.root)
                    self.assertEqual(code, 1, out + err)
                    self.assertIn(reason, err)
                    self.assertEqual(self.snapshot(air, compose), before)
                    code, out, err = run_module(compose, air.root, '--apply')
                    self.assertEqual(code, 1, out + err)
                    self.assertIn(reason, err)
                    self.assertEqual(self.snapshot(air, compose), before)
                    self.assertFalse((air.root / compose.PACMAN_LOCK).exists())
                    self.refuse(air, compose, action)


class RootSpaceTests(unittest.TestCase):
    def test_apply_counts_snapshot_bytes_once(self):
        packaged = boot_tests.kernel(12)
        machine = listed_machine(self, boot_tests.K1, packaged)
        current, desired, receipt = MODULE.plan(machine.root)
        self.assertEqual(receipt['changes'], ['kernel'])
        with mock.patch.object(MODULE.os, 'statvfs', side_effect=space_by_snapshots(machine, current, desired)):
            code, out, err = machine.run('--apply')
        self.assertEqual((code, err), (0, ''), err)
        self.assertEqual(json.loads(out)['status'], 'updated; takes effect at next boot')
        self.assertEqual(machine.boot, desired)
        self.assertEqual(machine.previous, current)
        self.assertFalse((machine.root / MODULE.JOURNAL).exists())
        video = json.loads((machine.root / MODULE.VIDEO_MANIFEST).read_bytes())
        self.assertEqual(video['boot_kernel_sha256'], boot_tests.sha(packaged))
        self.assertEqual((video['owner_panel'], video['notch']), ('kept', 'owner'))
        self.assertEqual((machine.root / MODULE.AUDIO_MANIFEST).read_bytes(), machine.audio_before)
        layout = MODULE.parse(machine.boot)
        owner = MODULE.parse(current)
        self.assertEqual([layout[name] for name in ('loader', 'bootargs', 'dtb', 'initramfs')],
                         [owner[name] for name in ('loader', 'bootargs', 'dtb', 'initramfs')])

    def test_resume_with_only_the_root_margin(self):
        packaged = boot_tests.kernel(13)
        machine = listed_machine(self, boot_tests.K1, packaged)
        current, desired, _ = MODULE.plan(machine.root)
        plant(machine, 'apply', current, desired, boot_tests.sha(packaged))
        with mock.patch.object(MODULE.os, 'statvfs', side_effect=space_by_snapshots(machine, current, desired)):
            code, _, err = machine.run('--apply')
        self.assertEqual((code, err), (0, ''), err)
        self.assertEqual(machine.boot, desired)
        self.assertEqual(machine.previous, current)
        self.assertFalse((machine.root / BOOT_JOURNAL).exists())
        self.assertTrue((machine.root / BOOT_SNAPSHOTS / MODULE.sha(current)).is_file())

    def test_separate_snapshot_store_filesystem_refuses_before_writes(self):
        """Ample root does not pay for the snapshot store's own filesystem.

        The check is the nearest existing parent of
        ``var/lib/omarchy-m4/boot-compose/snapshots``. Nothing under that store,
        and neither boot.bin nor the video manifest, may be written. Resume
        after the snapshots already exist is still the margin-only case above.
        """
        packaged = boot_tests.kernel(17)
        machine = listed_machine(self, boot_tests.K1, packaged)
        video = (machine.root / MODULE.VIDEO_MANIFEST).read_bytes()
        audio = (machine.root / MODULE.AUDIO_MANIFEST).read_bytes()
        boot_before = machine.boot
        snaps = machine.root / MODULE.SNAPSHOTS
        efi = machine.root / 'boot' / 'efi'
        ample = types.SimpleNamespace(f_bavail=1 << 40, f_frsize=1)
        short = types.SimpleNamespace(f_bavail=0, f_frsize=1)

        def fake(path):
            probed = Path(path)
            if probed == efi or efi in probed.parents or probed == machine.root:
                return ample
            if probed == snaps or snaps in probed.parents or probed in snaps.parents:
                return short
            return ample

        with mock.patch.object(MODULE.os, 'statvfs', side_effect=fake):
            code, _, err = machine.run('--apply')
        self.assertEqual(code, 1, err)
        self.assertIn('Not enough free space', err)
        self.assertTrue('snapshot' in err.lower() or MODULE.JOURNAL_DIR in err, err)
        self.assertEqual(machine.boot, boot_before)
        self.assertIsNone(machine.previous)
        self.assertEqual((machine.root / MODULE.VIDEO_MANIFEST).read_bytes(), video)
        self.assertEqual((machine.root / MODULE.AUDIO_MANIFEST).read_bytes(), audio)
        self.assertFalse((machine.root / MODULE.JOURNAL).exists())
        self.assertFalse(snaps.exists() and any(path.is_file() for path in snaps.rglob('*')))
        self.assertFalse((machine.root / MODULE.PACMAN_LOCK).exists())


class InterruptedCommitTests(unittest.TestCase):
    def retry(self, operation, fail_name):
        packaged = boot_tests.kernel(14 if operation == 'apply' else 15)
        if operation == 'apply':
            machine = listed_machine(self, boot_tests.K1, packaged)
            args = ('--apply',)
            target_pin = boot_tests.sha(packaged)
            to_bytes = boot_tests.boot(kernel_bytes=packaged, dtb=boot_tests.D1, loader=boot_tests.LOADER + b' notch')
            from_bytes = machine.boot_before
        else:
            machine = listed_machine(self, packaged, boot_tests.K1)
            from_bytes = machine.boot_before
            to_bytes = boot_tests.boot(kernel_bytes=boot_tests.K1, dtb=boot_tests.D1,
                                       loader=boot_tests.LOADER + b' notch')
            machine.put(MODULE.BOOT + MODULE.PREVIOUS, to_bytes)
            args = ('--rollback', '--apply')
            target_pin = boot_tests.sha(boot_tests.K1)
        wrapped, fired = fail_after(MODULE, fail_name)
        with mock.patch.object(MODULE, 'write', side_effect=wrapped):
            code, _, err = machine.run(*args)
        self.assertEqual(code, 1, err)
        self.assertEqual(fired['n'], 1)
        self.assertIn('injected write failure', err)
        self.assertTrue((machine.root / MODULE.JOURNAL).is_file())
        self.assertEqual(machine.previous, from_bytes)
        video = json.loads((machine.root / MODULE.VIDEO_MANIFEST).read_bytes())
        self.assertEqual((video['owner_panel'], video['notch'], video['boot_kernel_sha256']),
                         ('kept', 'owner', target_pin))
        if fail_name == 'boot.bin':
            self.assertEqual(machine.boot, to_bytes)
        else:
            self.assertEqual(machine.boot, from_bytes)
        code, _, err = machine.run(*args)
        self.assertEqual((code, err), (0, ''), err)
        self.assertEqual(machine.boot, to_bytes)
        self.assertEqual(machine.previous, from_bytes)
        self.assertFalse((machine.root / MODULE.JOURNAL).exists())
        video = json.loads((machine.root / MODULE.VIDEO_MANIFEST).read_bytes())
        self.assertEqual(video['boot_kernel_sha256'], target_pin)
        self.assertEqual((video['owner_panel'], video['notch']), ('kept', 'owner'))
        self.assertEqual((machine.root / MODULE.AUDIO_MANIFEST).read_bytes(), machine.audio_before)
        done, owner = MODULE.parse(machine.boot), MODULE.parse(from_bytes)
        self.assertEqual([done[name] for name in ('loader', 'bootargs', 'dtb', 'initramfs')],
                         [owner[name] for name in ('loader', 'bootargs', 'dtb', 'initramfs')])

    def test_retry_after_video_and_boot_replacement(self):
        for operation in ('apply', 'rollback'):
            for fail_name in ('j713-video-manifest.json', 'boot.bin'):
                with self.subTest(operation=operation, fail_name=fail_name):
                    self.retry(operation, fail_name)

    def test_interrupted_rollback_after_boot_write_keeps_the_hold(self):
        """Retry finishes the rollback, and the packaged Image stays uncomposed.

        The first attempt dies after ``boot.bin`` has been replaced. The retry
        clears the journal and leaves the boot-compose hold. A later ordinary
        compose reports the hold and writes nothing.
        """
        packaged = boot_tests.kernel(16)
        new = boot_tests.sha(packaged)
        old = boot_tests.sha(boot_tests.K1)
        machine = listed_machine(self, packaged, packaged)
        for patcher in (
                mock.patch.dict(MODULE.KNOWN_KERNELS, {old: boot_tests.RELEASE, new: boot_tests.RELEASE},
                                clear=True),
                mock.patch.object(MODULE, 'KERNEL_TRANSITIONS', {(old, new), (new, old)})):
            patcher.start()
            self.addCleanup(patcher.stop)
        loader = boot_tests.LOADER + b' notch'
        updated = boot_tests.boot(kernel_bytes=packaged, dtb=boot_tests.D1, loader=loader)
        restored = boot_tests.boot(kernel_bytes=boot_tests.K1, dtb=boot_tests.D1, loader=loader)
        machine.put(MODULE.BOOT, updated)
        machine.put(MODULE.BOOT + MODULE.PREVIOUS, restored)
        video = {'boot_kernel_sha256': new, 'kernel_release': boot_tests.RELEASE,
                 'owner_panel': 'kept', 'notch': 'owner'}
        machine.put(MODULE.VIDEO_MANIFEST, (json.dumps(video, indent=4) + '\n').encode())
        audio = (machine.root / MODULE.AUDIO_MANIFEST).read_bytes()
        wrapped, fired = fail_after(MODULE, 'boot.bin')
        with mock.patch.object(MODULE, 'write', side_effect=wrapped):
            code, _, err = machine.run('--rollback', '--apply')
        self.assertEqual(code, 1, err)
        self.assertEqual(fired['n'], 1)
        self.assertIn('injected write failure', err)
        self.assertEqual(machine.boot, restored)
        self.assertTrue((machine.root / MODULE.JOURNAL).is_file())
        code, _, err = machine.run('--rollback', '--apply')
        self.assertEqual((code, err), (0, ''), err)
        self.assertEqual(machine.boot, restored)
        self.assertFalse((machine.root / MODULE.JOURNAL).exists())
        self.assertEqual((machine.root / MODULE.KERNEL).read_bytes(), packaged)
        done, owner = MODULE.parse(machine.boot), MODULE.parse(restored)
        self.assertEqual([done[name] for name in ('loader', 'bootargs', 'dtb', 'initramfs')],
                         [owner[name] for name in ('loader', 'bootargs', 'dtb', 'initramfs')])
        parsed = json.loads((machine.root / MODULE.VIDEO_MANIFEST).read_bytes())
        self.assertEqual(parsed['boot_kernel_sha256'], old)
        self.assertEqual((parsed['owner_panel'], parsed['notch']), ('kept', 'owner'))
        self.assertEqual((machine.root / MODULE.AUDIO_MANIFEST).read_bytes(), audio)
        hold = machine.root / boot_tests.BOOT_HOLD
        self.assertTrue(hold.is_file() and not hold.is_symlink())
        held = hold.read_bytes()
        code, out, err = machine.run('--apply')
        self.assertEqual((code, err), (0, ''), err)
        receipt = json.loads(out)
        self.assertFalse(receipt['applied'])
        self.assertIn('held', receipt['status'].lower())
        self.assertEqual(machine.boot, restored)
        self.assertEqual(hold.read_bytes(), held)
        self.assertEqual((machine.root / MODULE.KERNEL).read_bytes(), packaged)


if __name__ == '__main__':
    unittest.main()
