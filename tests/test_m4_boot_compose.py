# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""boot.bin composer: splice only kernel and devicetree, refuse everything unknown.

Kernel bytes move only for an exact pair listed in ``KERNEL_TRANSITIONS``.
Both hashes must be in ``KNOWN_KERNELS`` and must share one release. Sharing
that release is not, by itself, a transition. A devicetree-only update does
not consult that set. Applying a pair replaces the boot kernel and the video
manifest's ``boot_kernel_sha256`` only; owner boot arguments, initramfs, the
devicetree, the stage2 notch preference, the audio manifest, and every other
video-manifest key and value stay. That contract is semantic: unrelated JSON
keys and values are preserved, and serialization whitespace is not. A no-op
on an already settled manifest still leaves those bytes unchanged. Rollback
restores the previous kernel and that same video pin.

Private recovery store, planted by these tests and not a CLI flag:

* ``var/lib/omarchy-m4/boot-compose/journal`` — JSON object with ``operation``
  (``apply`` or ``rollback``), ``from_sha256``, ``to_sha256``, and
  ``video_boot_kernel_sha256``.
* ``var/lib/omarchy-m4/boot-compose/snapshots/<sha256>`` — exact file bytes,
  and the file name is that sha256. A valid pending journal has both the
  ``from_sha256`` snapshot and the ``to_sha256`` snapshot.

Inspection of a valid pending journal must not replay either operation. A
journal whose operation is the opposite of the command refuses before
``boot.bin`` or the video manifest changes. A corrupt journal, or a pending
journal whose snapshot is missing or does not match its hash, is not a valid
pending state: inspection refuses it and does not apply it.

A finished rollback writes ``var/lib/omarchy-m4/boot-compose/hold``. Ordinary
compose, including ``--apply`` and a pacman ``--hook --apply``, reports that
hold and writes nothing while the packaged Image stays new. Inspection does
not clear it. A hold whose video pin does not match ``boot.bin`` is not that
report: inspection, ``--apply``, and ``--hook --apply`` name
``sudo m4-boot-compose --rollback --apply`` and write nothing.
``--resume-updates --apply`` releases the hold and composes the
installed packages, keeping this machine's loader, boot arguments, and
initramfs bytes. ``--resume-updates`` without ``--apply``, with ``--rollback``,
or from the package hook, is refused. A release that fails before any write
leaves every byte in place. A release that fails after the video pin keeps
the hold and the apply journal; ordinary compose does not continue it.
Inspection of that held apply journal, and a rollback asked while it is
pending, name ``sudo m4-boot-compose --resume-updates --apply``. Ordinary
``--apply`` does not clear the hold. With no hold, a pending apply still
names ordinary ``--apply`` and a pending rollback still names
``--rollback --apply``.

A hold path that is missing is absent. A regular hold file is present. A
directory or FIFO there is refused without being opened, and a symlink hold
keeps its own refusal. Copy-back whose video pin is a list or object refuses
before any boot, manifest, journal, or hold write. Read-only inspection of a
pending apply or rollback whose boot matches neither snapshot refuses the
same way apply does, and writes nothing.

A same-kernel rollback (devicetree only, or the backlight tree with its
handoff loader) stores both boot objects before it renames anything. A failure
after either rename still retries to the original object. The hold is created
only after eligibility and space checks, so a refusal creates none and does
not delete a hold that was already there. Pending recovery checks that
eligibility and the space the commit will use before a new hold as well. Once
a journal is gone, a later error names ``m4-boot-compose`` and the command to
run, including ``--resume-updates --apply`` when that release was interrupted
before ``boot.bin`` landed. It does not send the owner to an operation
recorded in a missing journal.

A corrupt journal, or a ``boot.bin`` that is neither snapshot of a pending
transition, is unresolved recovery. The refusal tells the operator to inspect
the journal under the compose root and does not claim that ``boot.bin`` was
left unchanged. ``/`` names ``/var/lib/omarchy-m4/boot-compose/journal``.
The journal and its snapshots stay. Other preflight refusals, where nothing
was pending, may still say that ``boot.bin`` was not changed.
"""
import contextlib
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import random
import stat
import struct
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'tools/m4-boot-compose.py'


def load():
    spec = importlib.util.spec_from_file_location('m4_boot_compose', SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def kernel(seed):
    return gzip.compress(random.Random(seed).randbytes(4096), mtime=0)


def fdt(body):
    return struct.pack('>10I', 0xd00dfeed, 40 + len(body), 40, 40, 40, 17, 16, 0, 0, len(body)) + body


K1, K2 = kernel(1), kernel(2)
D1, D2, D3 = fdt(b'alpha.1 tree'), fdt(b'update 1 tree with cells'), fdt(b'unknown tree')
D4 = fdt(b'update 1 tree with cpufreq clusters')
RELEASE = '7.1.9-test'
# A loader that, like m1n1, contains the boot-argument tag text itself.
LOADER = b'm1n1 stage2 chosen.bootargs= string table\0' + bytes(32) + b'STACKBOT'
BOOTARGS = b'chosen.bootargs=dockchannel_hid.m4_power_v2=1 dockchannel_hid.m4_afe_action=1\n'
FIRMWARE = gzip.compress(b'070701 base', mtime=0) + b'070701 owner firmware'
INITRAMFS = b'm1n1_initramfs' + struct.pack('<I', len(FIRMWARE)) + FIRMWARE


def boot(kernel_bytes=K1, dtb=D1, loader=LOADER, bootargs=BOOTARGS, initramfs=INITRAMFS):
    return loader + bootargs + kernel_bytes + dtb + initramfs


class Machine:
    """A temporary root that looks like an installed alpha.1 Air."""

    def __init__(self, test, module=MODULE, boot_bytes=None, package_kernel=K1, package_dtb=D2):
        temp = tempfile.TemporaryDirectory()
        test.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.module = module
        for patcher in (mock.patch.dict(module.KNOWN_KERNELS, {sha(K1): RELEASE, sha(K2): RELEASE}, clear=True),
                        mock.patch.dict(module.KNOWN_DTBS, {sha(D1): 'alpha.1', sha(D2): 'update 1'}, clear=True)):
            patcher.start()
            test.addCleanup(patcher.stop)
        self.put(module.COMPATIBLE, b'apple,j713\0apple,t8132\0apple,arm-platform\0')
        self.put(module.BOOT, boot() if boot_bytes is None else boot_bytes)
        self.put(module.KERNEL, package_kernel)
        self.put(module.DTB, package_dtb)
        self.manifests(sha(K1))
        (self.root / 'usr/lib/modules' / RELEASE).mkdir(parents=True)
        (self.root / 'var/lib/pacman').mkdir(parents=True)

    def put(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def manifests(self, kernel_sha, video_release=RELEASE, audio_release=RELEASE):
        self.put(self.module.VIDEO_MANIFEST, json.dumps(
            {'boot_kernel_sha256': kernel_sha, 'kernel_release': video_release}).encode())
        self.put(self.module.AUDIO_MANIFEST, json.dumps({'kernel_release': audio_release}).encode())

    @property
    def boot(self):
        return (self.root / self.module.BOOT).read_bytes()

    @property
    def previous(self):
        path = self.root / (self.module.BOOT + self.module.PREVIOUS)
        return path.read_bytes() if path.exists() else None

    def run(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = self.module.main(['--root', str(self.root), *args])
        return code, out.getvalue(), err.getvalue()


# Scenarios shared by the ordinary tests and the mutation check. Each raises
# AssertionError when the composer misbehaves.

def scenario_devicetree_update(test, module):
    machine = Machine(test, module)
    code, out, err = machine.run('--apply')
    test.assertEqual((code, err), (0, ''))
    receipt = json.loads(out)
    test.assertEqual(receipt['status'], 'updated; takes effect at next boot')
    test.assertEqual(receipt['changes'], ['dtb'])
    test.assertEqual(machine.boot, boot(dtb=D2))
    test.assertEqual(machine.previous, boot())


def scenario_unqualified_kernel(test, module):
    machine = Machine(test, module, package_kernel=K2)
    code, _, err = machine.run('--apply')
    test.assertEqual(code, 1)
    test.assertIn('video stack is not qualified', err)
    test.assertIn('boot.bin was not changed.', err)
    test.assertEqual((machine.boot, machine.previous), (boot(), None))


def scenario_audio_release(test, module):
    machine = Machine(test, module, package_kernel=K2)
    machine.manifests(sha(K2), audio_release='7.1.9-other')
    code, _, err = machine.run('--apply')
    test.assertEqual(code, 1)
    test.assertIn('audio stack is not qualified', err)
    test.assertEqual(machine.boot, boot())


def scenario_unknown_current(test, module):
    for current, message in ((boot(kernel_bytes=kernel(9)), 'unrecognized kernel'),
                             (boot(dtb=D3), 'unrecognized devicetree')):
        machine = Machine(test, module, boot_bytes=current)
        code, _, err = machine.run('--apply')
        test.assertEqual(code, 1)
        test.assertIn(message, err)
        test.assertEqual((machine.boot, machine.previous), (current, None))


def scenario_unknown_package(test, module):
    for name, data, message in (('DTB', D3, 'not a known J713 devicetree'),
                                ('KERNEL', kernel(9), 'not a known J713 kernel')):
        machine = Machine(test, module)
        machine.put(getattr(module, name), data)
        code, _, err = machine.run('--apply')
        test.assertEqual(code, 1)
        test.assertIn(message, err)
        test.assertEqual(machine.boot, boot())


def scenario_preserves_machine_parts(test, module):
    loader = LOADER + b' this machine'
    args = b'chosen.bootargs=root=PARTUUID=1234 dockchannel_hid.m4_power_v2=1\n'
    firmware = b'070701 another owner'
    initramfs = b'm1n1_initramfs' + struct.pack('<I', len(firmware)) + firmware
    machine = Machine(test, module, boot_bytes=boot(loader=loader, bootargs=args, initramfs=initramfs))
    test.assertEqual(machine.run('--apply')[0], 0)
    test.assertEqual(machine.boot, boot(dtb=D2, loader=loader, bootargs=args, initramfs=initramfs))


def scenario_free_space(test, module):
    machine = Machine(test, module)
    with mock.patch.object(module.os, 'statvfs', return_value=types.SimpleNamespace(f_bavail=1, f_frsize=4096)):
        code, _, err = machine.run('--apply')
    test.assertEqual(code, 1)
    test.assertIn('Not enough free space', err)
    test.assertEqual((machine.boot, machine.previous), (boot(), None))


def scenario_modules(test, module):
    machine = Machine(test, module)
    (machine.root / 'usr/lib/modules' / RELEASE).rmdir()
    code, _, err = machine.run('--apply')
    test.assertEqual(code, 1)
    test.assertIn('Kernel modules are missing', err)


def scenario_board(test, module):
    machine = Machine(test, module)
    machine.put(module.COMPATIBLE, b'apple,j613\0apple,t8112\0')
    code, _, err = machine.run('--apply')
    test.assertEqual(code, 1)
    test.assertIn('not a J713', err)
    test.assertEqual(machine.boot, boot())


def scenario_rollback(test, module):
    machine = Machine(test, module)
    test.assertEqual(machine.run('--apply')[0], 0)
    code, out, _ = machine.run('--rollback', '--apply')
    test.assertEqual(code, 0)
    test.assertEqual(json.loads(out)['status'], 'rolled back; takes effect at next boot')
    test.assertEqual((machine.boot, machine.previous), (boot(), boot(dtb=D2)))


def scenario_foreign_rollback(test, module):
    machine = Machine(test, module)
    other = b'm1n1_initramfs' + struct.pack('<I', 6) + b'070701'
    machine.put(module.BOOT + module.PREVIOUS, boot(initramfs=other))
    code, _, err = machine.run('--rollback', '--apply')
    test.assertEqual(code, 1)
    test.assertIn('not made from this boot.bin', err)
    test.assertEqual(machine.boot, boot())


OLD_KERNEL = 'c3704cfd2afb7760e73f0b9939a68864a30bd916fcd478bd960e538373123dc9'
NEW_KERNEL = 'ca1fff571cf1b9d8c0b07b2e97ba0a5402f93c1b7a2d54ea2c07e24a8b70f9d0'
TRANSITION_RELEASE = '7.1.9-m4-gpu-backport-20261008'
BOOT_JOURNAL = 'var/lib/omarchy-m4/boot-compose/journal'
BOOT_SNAPSHOTS = 'var/lib/omarchy-m4/boot-compose/snapshots'
# Durable marker created by a finished rollback. Ordinary compose leaves it alone.
BOOT_HOLD = MODULE.JOURNAL_DIR + '/hold'


def owner_image(tag):
    """Loader, boot arguments, and initramfs that belong to one synthetic owner."""
    loader = LOADER + tag
    bootargs = b'chosen.bootargs=root=PARTUUID=' + tag + b' dockchannel_hid.m4_power_v2=1\n'
    firmware = b'070701 ' + tag
    initramfs = b'm1n1_initramfs' + struct.pack('<I', len(firmware)) + firmware
    return loader, bootargs, initramfs


def plant_journal(machine, operation, from_bytes, to_bytes, video_pin):
    """Write a journal the real ``load_journal`` will accept, plus both snapshots."""
    module = machine.module
    payload = {'operation': operation, 'from_sha256': module.sha(from_bytes),
               'to_sha256': module.sha(to_bytes), 'video_boot_kernel_sha256': video_pin}
    machine.put(module.JOURNAL, (json.dumps(payload, indent=2, sort_keys=True) + '\n').encode())
    machine.put(module.SNAPSHOTS + '/' + module.sha(from_bytes), from_bytes)
    machine.put(module.SNAPSHOTS + '/' + module.sha(to_bytes), to_bytes)


def assert_unresolved_recovery(test, err):
    """Corrupt or neither-side recovery must not claim the boot object was untouched."""
    lowered = err.lower()
    test.assertNotIn('boot.bin was not changed', err)
    test.assertIn('unresolved', lowered)
    test.assertIn('inspect', lowered)
    test.assertIn(BOOT_JOURNAL, err)


@contextlib.contextmanager
def approve_synthetic_k1_k2(module, root):
    """Register the fixture K1→K2 pair for this block only. Other hashes stay unlisted."""
    pairs = set(module.KERNEL_TRANSITIONS)
    pairs.add((sha(K1), sha(K2)))
    pairs.add((sha(K2), sha(K1)))
    modules = root / 'usr/lib/modules' / module.KNOWN_KERNELS[sha(K2)]
    if not modules.is_dir():
        modules.mkdir(parents=True)
    with mock.patch.object(module, 'KERNEL_TRANSITIONS', pairs):
        yield


def scenario_newer_devicetree_kept(test, module):
    machine = Machine(test, module, boot_bytes=boot(dtb=D4), package_kernel=K2)
    with mock.patch.dict(module.KNOWN_DTBS, {sha(D4): 'cpufreq'}):
        with approve_synthetic_k1_k2(module, machine.root):
            machine.manifests(sha(K2))
            code, out, err = machine.run('--apply')
    test.assertEqual((code, err), (0, ''))
    receipt = json.loads(out)
    test.assertEqual((receipt['changes'], receipt['devicetree_sha256']), (['kernel'], sha(D4)))
    test.assertEqual(machine.boot, boot(kernel_bytes=K2, dtb=D4))


SCENARIOS = (scenario_devicetree_update, scenario_newer_devicetree_kept, scenario_unqualified_kernel, scenario_audio_release,
             scenario_unknown_current, scenario_unknown_package, scenario_preserves_machine_parts,
             scenario_free_space, scenario_modules, scenario_board, scenario_rollback,
             scenario_foreign_rollback)


class ComposeTests(unittest.TestCase):
    def test_scenarios(self):
        for scenario in SCENARIOS:
            with self.subTest(scenario.__name__):
                scenario(self, MODULE)

    def test_current_machine_is_left_alone(self):
        machine = Machine(self, package_dtb=D1)
        code, out, _ = machine.run('--apply')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['status'], 'current')
        self.assertEqual((machine.boot, machine.previous), (boot(), None))

    def test_inspection_writes_nothing(self):
        machine = Machine(self)
        code, out, _ = machine.run()
        receipt = json.loads(out)
        self.assertEqual((code, receipt['status'], receipt['applied']), (0, 'update available', False))
        self.assertEqual(receipt['new_boot_sha256'], sha(boot(dtb=D2)))
        self.assertEqual((machine.boot, machine.previous), (boot(), None))

    def test_second_apply_is_current(self):
        machine = Machine(self)
        self.assertEqual(machine.run('--apply')[0], 0)
        code, out, _ = machine.run('--apply')
        self.assertEqual(json.loads(out)['status'], 'current')
        self.assertEqual(machine.previous, boot())

    def test_qualified_kernel_change(self):
        machine = Machine(self, package_kernel=K2)
        with approve_synthetic_k1_k2(MODULE, machine.root):
            machine.manifests(sha(K2))
            code, out, _ = machine.run('--apply')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['changes'], ['dtb', 'kernel'])
        self.assertEqual(machine.boot, boot(kernel_bytes=K2, dtb=D2))

    def test_layout_must_consume_the_whole_file(self):
        for current in (boot() + b'\0', boot()[:-1], boot(initramfs=b'm1n1_initramfX' + INITRAMFS[14:]),
                        BOOTARGS + K1 + D1 + INITRAMFS, boot(kernel_bytes=K1[:-9])):
            with self.subTest(len(current)):
                machine = Machine(self, boot_bytes=current)
                code, _, err = machine.run('--apply')
                self.assertEqual(code, 1)
                self.assertIn('expected loader, boot arguments', err)
                self.assertEqual(machine.boot, current)

    def test_corrupt_package_kernel(self):
        machine = Machine(self)
        broken = K1[:-1] + bytes([K1[-1] ^ 1])
        with mock.patch.dict(MODULE.KNOWN_KERNELS, {sha(broken): RELEASE}):
            machine.put(MODULE.KERNEL, broken)
            machine.manifests(sha(broken))
            code, _, err = machine.run('--apply')
        self.assertEqual(code, 1)
        self.assertIn('not one complete gzip stream', err)

    def test_failed_replace_keeps_boot(self):
        machine = Machine(self)
        real = os.replace

        def fail_on_boot(source, target):
            if Path(target).name == 'boot.bin':
                raise OSError('simulated EFI write failure')
            return real(source, target)

        with mock.patch.object(MODULE.os, 'replace', side_effect=fail_on_boot):
            code, _, err = machine.run('--apply')
        self.assertEqual(code, 1)
        self.assertIn('simulated EFI write failure', err)
        self.assertEqual(machine.boot, boot())

    def test_stale_temporary_file_is_replaced(self):
        machine = Machine(self)
        machine.put(MODULE.BOOT + '.new', b'stale')
        self.assertEqual(machine.run('--apply')[0], 0)
        self.assertEqual(machine.boot, boot(dtb=D2))
        self.assertFalse((machine.root / (MODULE.BOOT + '.new')).exists())

    def test_rollback_without_copy(self):
        machine = Machine(self)
        code, _, err = machine.run('--rollback', '--apply')
        self.assertEqual(code, 1)
        self.assertIn('Rollback copy is missing', err)

    def handoff_machine(self, shown=False, *, hold_patches=True):
        native_tree = fdt(b'native backlight tree')
        old_loader = LOADER + (b' shown preference' if shown else b'')
        loaders = {mode: (mode.encode() + b' handoff stage2').ljust(0x110000, b'\0')
                   for mode in ('hidden', 'shown')}
        paths = {mode: f'usr/lib/omarchy-m4/backlight/stage2-{mode}.bin' for mode in loaders}
        # The devicetree pin starts after Machine(), which replaces KNOWN_DTBS.
        early = (
            mock.patch.object(MODULE, 'BACKLIGHT_DTB', sha(native_tree)),
            mock.patch.dict(MODULE.KNOWN_LOADERS,
                            {sha(old_loader): 'shown' if shown else 'hidden',
                             **{sha(data): mode for mode, data in loaders.items()}}, clear=True),
            mock.patch.dict(MODULE.BACKLIGHT_LOADERS,
                            {mode: (paths[mode], sha(data)) for mode, data in loaders.items()}, clear=True))
        started = []
        try:
            for patcher in early:
                patcher.start()
                started.append(patcher)
                if hold_patches:
                    self.addCleanup(patcher.stop)
            machine = Machine(self, boot_bytes=boot(loader=old_loader), package_dtb=native_tree)
            late = mock.patch.dict(MODULE.KNOWN_DTBS, {sha(native_tree): 'native backlight'})
            late.start()
            started.append(late)
            if hold_patches:
                self.addCleanup(late.stop)
            for mode, data in loaders.items():
                machine.put(paths[mode], data)
        except Exception:
            if not hold_patches:
                for patcher in reversed(started):
                    patcher.stop()
            raise
        machine.handoff_patchers = () if hold_patches else tuple(started)
        return machine, native_tree, old_loader, loaders, paths

    def test_backlight_update_preserves_hidden_notch_preference(self):
        machine, tree, _, loaders, _ = self.handoff_machine()
        self.assertEqual(machine.run('--apply')[0], 0)
        self.assertEqual(machine.boot, boot(loader=loaders['hidden'], dtb=tree))

    def test_backlight_update_preserves_shown_notch_preference(self):
        machine, tree, _, loaders, _ = self.handoff_machine(shown=True)
        self.assertEqual(machine.run('--apply')[0], 0)
        self.assertEqual(machine.boot, boot(loader=loaders['shown'], dtb=tree))

    def test_unknown_stage2_cannot_enable_the_backlight_tree(self):
        machine, _, _, _, _ = self.handoff_machine()
        current = boot(loader=LOADER + b' unrecognized')
        machine.put(MODULE.BOOT, current)
        self.assertEqual(machine.run('--apply')[0], 1)
        self.assertEqual((machine.boot, machine.previous), (current, None))

    def test_corrupt_handoff_loader_cannot_enable_the_backlight_tree(self):
        machine, _, old_loader, loaders, paths = self.handoff_machine()
        machine.put(paths['hidden'], loaders['shown'])
        self.assertEqual(machine.run('--apply')[0], 1)
        self.assertEqual((machine.boot, machine.previous), (boot(loader=old_loader), None))

    def test_rollback_restores_the_tree_and_handoff_loader_together(self):
        machine, _, old_loader, _, _ = self.handoff_machine()
        self.assertEqual(machine.run('--apply')[0], 0)
        self.assertEqual(machine.run('--rollback', '--apply')[0], 0)
        self.assertEqual(machine.boot, boot(loader=old_loader))

    def test_another_boot_writer_blocks_standalone_apply(self):
        machine = Machine(self)
        machine.put(MODULE.PACMAN_LOCK, b'another transaction')
        self.assertEqual(machine.run('--apply')[0], 1)
        self.assertEqual((machine.boot, machine.previous), (boot(), None))
        self.assertEqual((machine.root / MODULE.PACMAN_LOCK).read_bytes(), b'another transaction')

    def test_package_hook_keeps_the_existing_transaction_lock(self):
        machine = Machine(self)
        machine.put(MODULE.PACMAN_LOCK, b'pacman transaction')
        self.assertEqual(machine.run('--apply', '--hook')[0], 0)
        self.assertEqual(machine.boot, boot(dtb=D2))
        self.assertEqual((machine.root / MODULE.PACMAN_LOCK).read_bytes(), b'pacman transaction')

    def transition(self, *, boot_kernel=None, package_kernel=None, video_pin=None, allow=True,
                   releases=None):
        """Machine whose boot kernel and packaged kernel are a bounded pair.

        Fixture kernels are synthetic. ``KERNEL_TRANSITIONS`` and
        ``KNOWN_KERNELS`` are patched for this case; the real pair is pinned
        by ``test_kernel_transition_pair_is_pinned``.
        """
        boot_kernel = K1 if boot_kernel is None else boot_kernel
        package_kernel = kernel(12) if package_kernel is None else package_kernel
        current = boot(kernel_bytes=boot_kernel, loader=LOADER + b' notch', bootargs=BOOTARGS,
                       initramfs=INITRAMFS)
        machine = Machine(self, boot_bytes=current, package_kernel=package_kernel, package_dtb=D1)
        releases = releases or {sha(boot_kernel): RELEASE, sha(package_kernel): RELEASE}
        pairs = {(sha(boot_kernel), sha(package_kernel)), (sha(package_kernel), sha(boot_kernel))}
        for patcher in (
                mock.patch.dict(MODULE.KNOWN_KERNELS, releases, clear=True),
                mock.patch.object(MODULE, 'KERNEL_TRANSITIONS', pairs if allow else set(), create=True)):
            patcher.start()
            self.addCleanup(patcher.stop)
        pin = sha(boot_kernel) if video_pin is None else video_pin
        video = {'boot_kernel_sha256': pin, 'kernel_release': releases[sha(package_kernel)],
                 'owner_panel': 'kept', 'notch': 'owner'}
        machine.put(MODULE.VIDEO_MANIFEST, json.dumps(video, indent=4).encode() + b'\n')
        machine.video_before = (machine.root / MODULE.VIDEO_MANIFEST).read_bytes()
        machine.audio_before = (machine.root / MODULE.AUDIO_MANIFEST).read_bytes()
        machine.boot_before = current
        return machine

    def test_kernel_transition_pair_is_pinned(self):
        module = load()
        transitions = {tuple(pair) for pair in getattr(module, 'KERNEL_TRANSITIONS', ())}
        self.assertIn((OLD_KERNEL, NEW_KERNEL), transitions)
        self.assertIn((NEW_KERNEL, OLD_KERNEL), transitions)
        self.assertEqual(module.KNOWN_KERNELS.get(OLD_KERNEL), TRANSITION_RELEASE)
        self.assertEqual(module.KNOWN_KERNELS.get(NEW_KERNEL), TRANSITION_RELEASE)

    def test_devicetree_only_update_does_not_need_a_kernel_pair(self):
        machine = Machine(self)
        with mock.patch.object(MODULE, 'KERNEL_TRANSITIONS', set(), create=True):
            code, out, err = machine.run('--apply')
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(json.loads(out)['changes'], ['dtb'])
        self.assertEqual(machine.boot, boot(dtb=D2))

    def test_apply_transitions_kernel_and_only_the_video_pin(self):
        packaged = kernel(12)
        machine = self.transition(package_kernel=packaged)
        code, out, err = machine.run('--apply')
        self.assertEqual((code, err), (0, ''))
        receipt = json.loads(out)
        self.assertEqual(receipt['changes'], ['kernel'])
        self.assertEqual(machine.boot, boot(kernel_bytes=packaged, dtb=D1, loader=LOADER + b' notch'))
        self.assertEqual(machine.previous, machine.boot_before)
        video = json.loads((machine.root / MODULE.VIDEO_MANIFEST).read_bytes())
        self.assertEqual(video, video_with_pin(machine.video_before, sha(packaged)))
        self.assertEqual((machine.root / MODULE.AUDIO_MANIFEST).read_bytes(), machine.audio_before)
        settled = (machine.root / MODULE.VIDEO_MANIFEST).read_bytes()
        code, out, _ = machine.run('--apply')
        self.assertEqual(json.loads(out)['status'], 'current')
        self.assertEqual(machine.previous, machine.boot_before)
        self.assertEqual((machine.root / MODULE.VIDEO_MANIFEST).read_bytes(), settled)
        self.assertEqual(code, 0)

    def test_unknown_pair_release_or_pins_refuse_before_writes(self):
        packaged = kernel(12)
        # Video is already pinned to the packaged kernel, which is enough for
        # the composer today. The pair is absent from KERNEL_TRANSITIONS.
        machine = self.transition(package_kernel=packaged, allow=False, video_pin=sha(packaged))
        code, _, err = machine.run('--apply')
        self.assertEqual(code, 1, err)
        self.assertEqual((machine.boot, machine.previous), (machine.boot_before, None))
        self.assertEqual((machine.root / MODULE.VIDEO_MANIFEST).read_bytes(), machine.video_before)

        machine = self.transition(package_kernel=packaged, video_pin=sha(packaged),
                                  releases={sha(K1): RELEASE, sha(packaged): '7.1.9-other'})
        machine.put(MODULE.AUDIO_MANIFEST, json.dumps({'kernel_release': '7.1.9-other'}).encode())
        code, _, err = machine.run('--apply')
        self.assertEqual(code, 1, err)
        self.assertEqual((machine.boot, machine.previous), (machine.boot_before, None))
        self.assertEqual((machine.root / MODULE.VIDEO_MANIFEST).read_bytes(), machine.video_before)

        machine = self.transition(package_kernel=packaged, video_pin=sha(kernel(9)))
        code, _, err = machine.run('--apply')
        self.assertEqual(code, 1, err)
        self.assertIsNone(machine.previous)
        self.assertEqual(machine.boot, machine.boot_before)

    def test_rollback_restores_kernel_and_video_pin_only(self):
        packaged = kernel(12)
        current = boot(kernel_bytes=packaged, dtb=D1, loader=LOADER + b' notch')
        previous = boot(kernel_bytes=K1, dtb=D1, loader=LOADER + b' notch')
        machine = self.transition(boot_kernel=packaged, package_kernel=K1, video_pin=sha(packaged))
        machine.put(MODULE.BOOT, current)
        machine.put(MODULE.BOOT + MODULE.PREVIOUS, previous)
        video = {'boot_kernel_sha256': sha(packaged), 'kernel_release': RELEASE,
                 'owner_panel': 'kept'}
        raw = json.dumps(video, indent=4).encode() + b'\n'
        machine.put(MODULE.VIDEO_MANIFEST, raw)
        audio = (machine.root / MODULE.AUDIO_MANIFEST).read_bytes()
        code, out, err = machine.run('--rollback', '--apply')
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(json.loads(out)['status'], 'rolled back; takes effect at next boot')
        self.assertEqual(machine.boot, previous)
        restored = json.loads((machine.root / MODULE.VIDEO_MANIFEST).read_bytes())
        self.assertEqual(restored, video_with_pin(raw, sha(K1)))
        self.assertEqual((machine.root / MODULE.AUDIO_MANIFEST).read_bytes(), audio)

    def test_interrupted_transition_resumes_and_rejects_the_opposite_operation(self):
        packaged = kernel(12)
        machine = self.transition(package_kernel=packaged)
        real = os.replace

        def fail_previous(source, target):
            if Path(target).name == 'boot.bin.previous':
                raise OSError('power cut while replacing boot.bin.previous')
            return real(source, target)

        with mock.patch.object(MODULE.os, 'replace', side_effect=fail_previous):
            code, _, err = machine.run('--apply')
        self.assertEqual(code, 1, err)
        self.assertEqual(machine.boot, machine.boot_before)
        self.assertEqual((machine.root / MODULE.VIDEO_MANIFEST).read_bytes(), machine.video_before)
        code, out, err = machine.run()
        self.assertEqual(code, 0, err)
        self.assertFalse(json.loads(out)['applied'])
        self.assertEqual(machine.boot, machine.boot_before)
        self.assertEqual((machine.root / MODULE.VIDEO_MANIFEST).read_bytes(), machine.video_before)
        planted = boot(kernel_bytes=K1, dtb=D2, loader=LOADER + b' notch')
        machine.put(MODULE.BOOT + MODULE.PREVIOUS, planted)
        code, _, err = machine.run('--rollback', '--apply')
        self.assertEqual(code, 1, err)
        self.assertEqual(machine.boot, machine.boot_before)
        self.assertEqual((machine.root / MODULE.VIDEO_MANIFEST).read_bytes(), machine.video_before)
        code, _, err = machine.run('--apply')
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(machine.boot, boot(kernel_bytes=packaged, dtb=D1, loader=LOADER + b' notch'))
        self.assertEqual(json.loads((machine.root / MODULE.VIDEO_MANIFEST).read_bytes())['boot_kernel_sha256'],
                         sha(packaged))
        self.assertEqual(json.loads((machine.root / MODULE.VIDEO_MANIFEST).read_bytes())['owner_panel'], 'kept')

    def test_pending_journal_blocks_the_opposite_operation_and_inspection_does_not_apply(self):
        packaged = kernel(12)
        machine = self.transition(package_kernel=packaged)
        desired = boot(kernel_bytes=packaged, dtb=D1, loader=LOADER + b' notch')
        machine.put(BOOT_JOURNAL, json.dumps({
            'operation': 'apply', 'from_sha256': sha(machine.boot_before), 'to_sha256': sha(desired),
            'video_boot_kernel_sha256': sha(packaged)}).encode())
        machine.put(f'{BOOT_SNAPSHOTS}/{sha(machine.boot_before)}', machine.boot_before)
        machine.put(f'{BOOT_SNAPSHOTS}/{sha(desired)}', desired)
        code, out, err = machine.run()
        self.assertEqual(code, 0, err)
        self.assertFalse(json.loads(out)['applied'])
        self.assertEqual(machine.boot, machine.boot_before)
        self.assertEqual((machine.root / MODULE.VIDEO_MANIFEST).read_bytes(), machine.video_before)
        previous = boot(dtb=D2, loader=LOADER + b' notch')
        machine.put(MODULE.BOOT + MODULE.PREVIOUS, previous)
        code, _, err = machine.run('--rollback', '--apply')
        self.assertEqual(code, 1, err)
        self.assertEqual(machine.boot, machine.boot_before)
        self.assertEqual((machine.root / MODULE.VIDEO_MANIFEST).read_bytes(), machine.video_before)

    def test_corrupt_journal_or_snapshot_refuses_before_writes(self):
        machine = Machine(self)
        machine.put(BOOT_JOURNAL, b'{not a journal')
        code, _, err = machine.run('--apply')
        self.assertEqual(code, 1, err)
        self.assertEqual((machine.boot, machine.previous), (boot(), None))
        code, _, err = machine.run()
        self.assertEqual(code, 1, err)
        self.assertEqual(machine.boot, boot())

        machine = Machine(self)
        current = boot()
        machine.put(BOOT_JOURNAL, json.dumps({
            'operation': 'apply', 'from_sha256': sha(current), 'to_sha256': sha(boot(dtb=D2)),
            'video_boot_kernel_sha256': sha(K1)}).encode())
        machine.put(f'{BOOT_SNAPSHOTS}/{sha(current)}', b'truncated snapshot')
        machine.put(f'{BOOT_SNAPSHOTS}/{sha(boot(dtb=D2))}', boot(dtb=D2))
        video = (machine.root / MODULE.VIDEO_MANIFEST).read_bytes()
        code, _, err = machine.run()
        self.assertEqual(code, 1, err)
        self.assertEqual((machine.boot, machine.previous), (current, None))
        self.assertEqual((machine.root / MODULE.VIDEO_MANIFEST).read_bytes(), video)
        code, _, err = machine.run('--apply')
        self.assertEqual(code, 1, err)
        self.assertEqual((machine.boot, machine.previous), (current, None))
        self.assertEqual((machine.root / MODULE.VIDEO_MANIFEST).read_bytes(), video)

        machine = Machine(self)
        current = boot()
        desired = boot(dtb=D2)
        machine.put(BOOT_JOURNAL, json.dumps({
            'operation': 'apply', 'from_sha256': sha(current), 'to_sha256': sha(desired),
            'video_boot_kernel_sha256': sha(K1)}).encode())
        machine.put(f'{BOOT_SNAPSHOTS}/{sha(current)}', current)
        video = (machine.root / MODULE.VIDEO_MANIFEST).read_bytes()
        code, _, err = machine.run()
        self.assertEqual(code, 1, err)
        self.assertEqual((machine.boot, machine.previous), (current, None))
        self.assertEqual((machine.root / MODULE.VIDEO_MANIFEST).read_bytes(), video)
        code, _, err = machine.run('--apply')
        self.assertEqual(code, 1, err)
        self.assertEqual((machine.boot, machine.previous), (current, None))
        self.assertEqual((machine.root / MODULE.VIDEO_MANIFEST).read_bytes(), video)

    def test_transition_keeps_the_free_space_and_package_lock_refusals(self):
        packaged = kernel(12)
        machine = self.transition(package_kernel=packaged)
        with mock.patch.object(MODULE.os, 'statvfs', return_value=types.SimpleNamespace(f_bavail=1, f_frsize=4096)):
            code, _, err = machine.run('--apply')
        self.assertEqual(code, 1)
        self.assertIn('Not enough free space', err)
        self.assertEqual(machine.boot, machine.boot_before)
        self.assertEqual((machine.root / MODULE.VIDEO_MANIFEST).read_bytes(), machine.video_before)
        self.assertIsNone(machine.previous)

        machine = self.transition(package_kernel=packaged)
        machine.put(MODULE.PACMAN_LOCK, b'another transaction')
        code, _, err = machine.run('--apply')
        self.assertEqual(code, 1)
        self.assertIn('boot writer', err)
        self.assertEqual(machine.boot, machine.boot_before)
        self.assertEqual((machine.root / MODULE.PACMAN_LOCK).read_bytes(), b'another transaction')

    def test_same_release_pair_outside_the_allowlist_refuses_before_writes(self):
        """A third and fourth known hash, same release, are not a kernel transition.

        The video manifest is already pinned to the packaged kernel. Owner boot
        arguments, initramfs, and stage2 match. The real ``KERNEL_TRANSITIONS``
        set is left as shipped. Nothing may be written.
        """
        third, fourth = kernel(3), kernel(4)
        loader = LOADER + b' owner-stage2'
        bootargs = b'chosen.bootargs=root=PARTUUID=abcd dockchannel_hid.m4_power_v2=1\n'
        firmware = b'070701 owner-kept'
        initramfs = b'm1n1_initramfs' + struct.pack('<I', len(firmware)) + firmware
        current = boot(kernel_bytes=third, dtb=D1, loader=loader, bootargs=bootargs, initramfs=initramfs)
        machine = Machine(self, boot_bytes=current, package_kernel=fourth, package_dtb=D1)
        releases = {sha(third): RELEASE, sha(fourth): RELEASE}
        patcher = mock.patch.dict(MODULE.KNOWN_KERNELS, releases, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.assertEqual(
            {tuple(pair) for pair in MODULE.KERNEL_TRANSITIONS},
            {(OLD_KERNEL, NEW_KERNEL), (NEW_KERNEL, OLD_KERNEL)})
        video = {'boot_kernel_sha256': sha(fourth), 'kernel_release': RELEASE, 'owner_panel': 'kept'}
        raw = json.dumps(video, indent=4).encode() + b'\n'
        machine.put(MODULE.VIDEO_MANIFEST, raw)
        audio = (machine.root / MODULE.AUDIO_MANIFEST).read_bytes()
        code, _, err = machine.run('--apply')
        self.assertEqual(code, 1, err)
        self.assertIn('not qualified', err)
        self.assertEqual(machine.boot, current)
        self.assertIsNone(machine.previous)
        self.assertEqual((machine.root / MODULE.VIDEO_MANIFEST).read_bytes(), raw)
        self.assertEqual((machine.root / MODULE.AUDIO_MANIFEST).read_bytes(), audio)
        self.assertFalse((machine.root / BOOT_JOURNAL).exists())

    def unlisted_kernel_pair(self):
        """Known same-release kernels that are not a ``KERNEL_TRANSITIONS`` pair."""
        third, fourth = kernel(3), kernel(4)
        loader = LOADER + b' owner-stage2'
        bootargs = b'chosen.bootargs=root=PARTUUID=abcd dockchannel_hid.m4_power_v2=1\n'
        firmware = b'070701 owner-kept'
        initramfs = b'm1n1_initramfs' + struct.pack('<I', len(firmware)) + firmware
        current = boot(kernel_bytes=fourth, dtb=D1, loader=loader, bootargs=bootargs, initramfs=initramfs)
        older = boot(kernel_bytes=third, dtb=D1, loader=loader, bootargs=bootargs, initramfs=initramfs)
        machine = Machine(self, boot_bytes=current, package_kernel=fourth, package_dtb=D1)
        patcher = mock.patch.dict(
            MODULE.KNOWN_KERNELS, {sha(third): RELEASE, sha(fourth): RELEASE}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        machine.put(MODULE.BOOT + MODULE.PREVIOUS, older)
        video = {'boot_kernel_sha256': sha(fourth), 'kernel_release': RELEASE, 'owner_panel': 'kept'}
        raw = json.dumps(video, indent=4).encode() + b'\n'
        machine.put(MODULE.VIDEO_MANIFEST, raw)
        audio = (machine.root / MODULE.AUDIO_MANIFEST).read_bytes()
        return machine, current, older, raw, audio

    def assert_unlisted_rollback_refused(self, machine, current, older, raw, audio, *extra):
        code, _, err = machine.run('--rollback', *extra)
        self.assertEqual(code, 1, err)
        self.assertIn('not qualified', err)
        self.assertIn('boot.bin was not changed.', err)
        self.assertEqual(machine.boot, current)
        self.assertEqual(machine.previous, older)
        self.assertEqual((machine.root / MODULE.VIDEO_MANIFEST).read_bytes(), raw)
        self.assertEqual((machine.root / MODULE.AUDIO_MANIFEST).read_bytes(), audio)
        self.assertFalse((machine.root / BOOT_JOURNAL).exists())
        self.assertFalse((machine.root / BOOT_HOLD).exists())

    def test_rollback_of_an_unlisted_kernel_pair_refuses_before_writes(self):
        """Previous may change the kernel only for an allowlisted pair.

        Owner segments match and both hashes are known on one release. The
        pair is not in ``KERNEL_TRANSITIONS``.
        """
        self.assert_unlisted_rollback_refused(*self.unlisted_kernel_pair(), '--apply')

    def test_rollback_inspection_of_an_unlisted_pair_refuses_like_apply(self):
        """Inspection of an unlisted rollback refuses before it reports a receipt."""
        self.assert_unlisted_rollback_refused(*self.unlisted_kernel_pair())

    def invoke(self, machine, *args):
        try:
            return machine.run(*args)
        except SystemExit as error:
            self.fail('m4-boot-compose rejected %s (%s)' % (args, error))

    def assert_compose_held(self, machine, args, boot_bytes, previous_bytes, video_bytes,
                            audio_bytes, hold_bytes):
        if '--hook' in args:
            machine.put(MODULE.PACMAN_LOCK, b'updater retry')
        code, out, err = self.invoke(machine, *args)
        self.assertEqual((code, err), (0, ''), err)
        receipt = json.loads(out)
        self.assertFalse(receipt['applied'])
        self.assertEqual(receipt['status'], MODULE.HELD_STATUS)
        self.assertEqual(receipt['boot_sha256'], sha(boot_bytes))
        self.assertEqual(receipt['kernel_sha256'], sha(MODULE.parse(boot_bytes)['kernel']))
        self.assertEqual(receipt['devicetree_sha256'], sha(MODULE.parse(boot_bytes)['dtb']))
        self.assertNotIn('takes effect at next boot', receipt['status'])
        self.assertEqual(machine.boot, boot_bytes)
        self.assertEqual(machine.previous, previous_bytes)
        self.assertEqual((machine.root / MODULE.VIDEO_MANIFEST).read_bytes(), video_bytes)
        self.assertEqual((machine.root / MODULE.AUDIO_MANIFEST).read_bytes(), audio_bytes)
        self.assertEqual((machine.root / BOOT_HOLD).read_bytes(), hold_bytes)
        self.assertFalse((machine.root / BOOT_JOURNAL).exists())
        if '--hook' in args:
            self.assertEqual((machine.root / MODULE.PACMAN_LOCK).read_bytes(), b'updater retry')
        else:
            self.assertFalse((machine.root / MODULE.PACMAN_LOCK).exists())

    def prepare_kernel_rollback_hold(self):
        """Roll the listed kernel back while the packaged Image stays the new one."""
        packaged = kernel(30)
        loader, bootargs, initramfs = owner_image(b'hold-owner')
        restored = boot(kernel_bytes=K1, dtb=D1, loader=loader, bootargs=bootargs, initramfs=initramfs)
        updated = boot(kernel_bytes=packaged, dtb=D1, loader=loader, bootargs=bootargs, initramfs=initramfs)
        machine = self.transition(boot_kernel=K1, package_kernel=packaged)
        machine.put(MODULE.BOOT, updated)
        machine.put(MODULE.BOOT + MODULE.PREVIOUS, restored)
        video = {'boot_kernel_sha256': sha(packaged), 'kernel_release': RELEASE,
                 'owner_panel': 'kept', 'notch': 'owner'}
        machine.put(MODULE.VIDEO_MANIFEST, json.dumps(video, indent=4).encode() + b'\n')
        audio = (machine.root / MODULE.AUDIO_MANIFEST).read_bytes()
        code, out, err = self.invoke(machine, '--rollback', '--apply')
        self.assertEqual((code, err), (0, ''), err)
        self.assertNotIn('next M4 package update composes', out)
        self.assertEqual(machine.boot, restored)
        self.assertEqual((machine.root / MODULE.KERNEL).read_bytes(), packaged)
        self.assertEqual((machine.root / MODULE.AUDIO_MANIFEST).read_bytes(), audio)
        hold = machine.root / BOOT_HOLD
        self.assertTrue(hold.is_file() and not hold.is_symlink())
        self.assertFalse((machine.root / BOOT_JOURNAL).exists())
        machine.audio_before = audio
        machine.resume_pin = sha(packaged)
        return machine, restored, updated, ['kernel']

    def prepare_devicetree_rollback_hold(self):
        """Roll a devicetree update back while the packaged tree stays the new one."""
        loader, bootargs, initramfs = owner_image(b'dtb-owner')
        restored = boot(kernel_bytes=K1, dtb=D1, loader=loader, bootargs=bootargs, initramfs=initramfs)
        updated = boot(kernel_bytes=K1, dtb=D2, loader=loader, bootargs=bootargs, initramfs=initramfs)
        machine = Machine(self, boot_bytes=updated, package_kernel=K1, package_dtb=D2)
        machine.put(MODULE.BOOT + MODULE.PREVIOUS, restored)
        video = {'boot_kernel_sha256': sha(K1), 'kernel_release': RELEASE,
                 'owner_panel': 'kept', 'notch': 'owner'}
        raw = json.dumps(video, indent=4).encode() + b'\n'
        machine.put(MODULE.VIDEO_MANIFEST, raw)
        audio = (machine.root / MODULE.AUDIO_MANIFEST).read_bytes()
        code, out, err = self.invoke(machine, '--rollback', '--apply')
        self.assertEqual((code, err), (0, ''), err)
        self.assertNotIn('next M4 package update composes', out)
        self.assertEqual(machine.boot, restored)
        self.assertEqual((machine.root / MODULE.DTB).read_bytes(), D2)
        self.assertEqual((machine.root / MODULE.VIDEO_MANIFEST).read_bytes(), raw)
        self.assertEqual((machine.root / MODULE.AUDIO_MANIFEST).read_bytes(), audio)
        hold = machine.root / BOOT_HOLD
        self.assertTrue(hold.is_file() and not hold.is_symlink())
        machine.audio_before = audio
        machine.resume_pin = None
        return machine, restored, updated, ['dtb']

    def test_rollback_hold_blocks_ordinary_compose_until_resume_updates(self):
        """The hold survives inspection, compose, and updater retries, then resume releases it."""
        for label, prepare in (
                ('kernel image stays packaged', self.prepare_kernel_rollback_hold),
                ('devicetree stays packaged', self.prepare_devicetree_rollback_hold)):
            with self.subTest(label):
                machine, restored, released, changes = prepare()
                hold_bytes = (machine.root / BOOT_HOLD).read_bytes()
                video_bytes = (machine.root / MODULE.VIDEO_MANIFEST).read_bytes()
                previous_bytes = machine.previous
                audio_bytes = machine.audio_before
                for args in ((), ('--apply',), ('--apply',)):
                    self.assert_compose_held(
                        machine, args, restored, previous_bytes, video_bytes, audio_bytes, hold_bytes)
                code, _, err = self.invoke(machine, '--rollback')
                self.assertEqual(code, 0, err)
                self.assertEqual(machine.boot, restored)
                self.assertEqual((machine.root / BOOT_HOLD).read_bytes(), hold_bytes)
                for args in (('--hook', '--apply'), ('--hook', '--apply')):
                    self.assert_compose_held(
                        machine, args, restored, previous_bytes, video_bytes, audio_bytes, hold_bytes)
                (machine.root / MODULE.PACMAN_LOCK).unlink()
                code, out, err = self.invoke(machine, '--resume-updates', '--apply')
                self.assertEqual((code, err), (0, ''), err)
                receipt = json.loads(out)
                self.assertTrue(receipt['applied'])
                self.assertEqual(receipt['status'], 'updated; takes effect at next boot')
                self.assertEqual(receipt['changes'], changes)
                self.assertEqual(receipt['boot_sha256'], sha(released))
                self.assertEqual(receipt['kernel_sha256'], sha(MODULE.parse(released)['kernel']))
                self.assertEqual(receipt['devicetree_sha256'], sha(MODULE.parse(released)['dtb']))
                self.assertEqual(machine.boot, released)
                self.assertFalse((machine.root / BOOT_HOLD).exists())
                done, owner = MODULE.parse(machine.boot), MODULE.parse(restored)
                self.assertEqual([done[name] for name in ('loader', 'bootargs', 'initramfs')],
                                 [owner[name] for name in ('loader', 'bootargs', 'initramfs')])
                if machine.resume_pin is None:
                    self.assertEqual((machine.root / MODULE.VIDEO_MANIFEST).read_bytes(), video_bytes)
                else:
                    parsed = json.loads((machine.root / MODULE.VIDEO_MANIFEST).read_bytes())
                    self.assertEqual(parsed['boot_kernel_sha256'], machine.resume_pin)
                    self.assertEqual((parsed['owner_panel'], parsed['notch']), ('kept', 'owner'))
                self.assertEqual((machine.root / MODULE.AUDIO_MANIFEST).read_bytes(), audio_bytes)
                code, out, err = self.invoke(machine, '--apply')
                self.assertEqual((code, err), (0, ''), err)
                self.assertEqual(json.loads(out)['status'], 'current')
                self.assertEqual(machine.boot, released)
                self.assertFalse((machine.root / BOOT_HOLD).exists())

    def test_resume_updates_refuses_without_apply_or_with_rollback(self):
        machine = Machine(self)
        before = machine.boot
        cases = (
            ('--resume-updates',),
            ('--resume-updates', '--rollback'),
            ('--resume-updates', '--rollback', '--apply'),
            ('--hook', '--resume-updates', '--apply'),
        )
        for args in cases:
            with self.subTest(args=args):
                code, out, err = self.invoke(machine, *args)
                self.assertEqual(code, 1, err)
                self.assertEqual(out, '')
                self.assertIn('resume-updates', err)
                self.assertIn('--apply', err)
                if '--rollback' in args:
                    self.assertIn('--rollback', err)
                self.assertEqual(machine.boot, before)
                self.assertIsNone(machine.previous)
                self.assertFalse((machine.root / BOOT_JOURNAL).exists())
                self.assertFalse((machine.root / BOOT_HOLD).exists())

    def test_failed_resume_updates_keeps_the_hold(self):
        """The video pin is written before boot.bin. The release stays recoverable.

        Ordinary compose must not finish that journal. A later
        ``--resume-updates --apply`` does. The preflight twin writes nothing.
        """
        machine, restored, updated, _ = self.prepare_kernel_rollback_hold()
        hold_bytes = (machine.root / BOOT_HOLD).read_bytes()
        real = os.replace

        def fail_boot(source, target):
            if Path(target).name == 'boot.bin':
                raise OSError('power cut while releasing the hold')
            return real(source, target)

        with mock.patch.object(MODULE.os, 'replace', side_effect=fail_boot):
            code, out, err = self.invoke(machine, '--resume-updates', '--apply')
        self.assertEqual(code, 1, err)
        self.assertEqual(out, '')
        self.assertIn('power cut while releasing the hold', err)
        self.assertNotIn('boot.bin was not changed', err)
        self.assertNotIn(MODULE.HELD_STATUS, out)
        self.assertEqual(machine.boot, restored)
        self.assertEqual((machine.root / BOOT_HOLD).read_bytes(), hold_bytes)
        journal = MODULE.load_journal(machine.root)
        self.assertEqual(journal['operation'], 'apply')
        self.assertEqual(journal['to_bytes'], updated)
        self.assertEqual((machine.root / (BOOT_SNAPSHOTS + '/' + journal['from_sha256'])).read_bytes(),
                         journal['from_bytes'])
        self.assertEqual((machine.root / (BOOT_SNAPSHOTS + '/' + journal['to_sha256'])).read_bytes(),
                         updated)
        video = json.loads((machine.root / MODULE.VIDEO_MANIFEST).read_bytes())
        self.assertEqual(video['boot_kernel_sha256'], sha(MODULE.parse(updated)['kernel']))
        self.assertNotEqual(video['boot_kernel_sha256'], sha(MODULE.parse(restored)['kernel']))
        code, out, err = self.invoke(machine, '--apply')
        self.assertEqual(code, 1, err)
        self.assertEqual(out, '')
        self.assertIn('resume-updates', err)
        self.assertNotIn('boot.bin was not changed', err)
        self.assertEqual(machine.boot, restored)
        self.assertEqual((machine.root / BOOT_HOLD).read_bytes(), hold_bytes)
        self.assertTrue((machine.root / BOOT_JOURNAL).is_file())
        code, out, err = self.invoke(machine, '--resume-updates', '--apply')
        self.assertEqual((code, err), (0, ''), err)
        receipt = json.loads(out)
        self.assertTrue(receipt['applied'])
        self.assertEqual(receipt['changes'], ['kernel'])
        self.assertEqual(receipt['boot_sha256'], sha(machine.boot))
        self.assertEqual(machine.boot, updated)
        self.assertFalse((machine.root / BOOT_HOLD).exists())
        self.assertFalse((machine.root / BOOT_JOURNAL).exists())

    def test_held_same_kernel_resume_interruption_names_resume_updates(self):
        """A same-kernel release that dies before boot.bin lands has no journal.

        Previous differs from the rolled-back boot, so replace writes that
        backup and then fails the boot rename before it lands. The refusal
        names ``--resume-updates --apply`` and does not point at a journal.
        That command then applies the packaged tree and clears the hold.
        """
        machine, restored, updated, changes = self.prepare_devicetree_rollback_hold()
        self.assertEqual(machine.boot, restored)
        self.assertNotEqual(machine.previous, machine.boot)
        self.assertEqual(sha(MODULE.parse(machine.boot)['dtb']), sha(D1))
        self.assertEqual((machine.root / MODULE.DTB).read_bytes(), D2)
        self.assertFalse((machine.root / BOOT_JOURNAL).exists())
        hold_bytes = (machine.root / BOOT_HOLD).read_bytes()
        video = (machine.root / MODULE.VIDEO_MANIFEST).read_bytes()
        audio = machine.audio_before
        owner = MODULE.parse(restored)
        real = os.replace

        def fail_boot_before_land(source, target):
            if Path(target).name == 'boot.bin':
                raise OSError('power cut before boot.bin landed')
            return real(source, target)

        with mock.patch.object(MODULE.os, 'replace', side_effect=fail_boot_before_land):
            code, out, err = self.invoke(machine, '--resume-updates', '--apply')
        self.assertEqual(code, 1, err)
        self.assertEqual(out, '')
        self.assertIn('power cut before boot.bin landed', err)
        self.assertIn('sudo m4-boot-compose --resume-updates --apply', err)
        self.assertNotIn('--rollback', err)
        self.assertNotIn('recorded there', err)
        self.assertNotIn('operation recorded', err)
        self.assertNotIn(BOOT_JOURNAL, err)
        self.assertNotIn('boot.bin was not changed', err)
        self.assertEqual(machine.boot, restored)
        self.assertEqual(machine.previous, restored)
        self.assertEqual((machine.root / BOOT_HOLD).read_bytes(), hold_bytes)
        self.assertFalse((machine.root / BOOT_JOURNAL).exists())
        self.assertEqual((machine.root / MODULE.VIDEO_MANIFEST).read_bytes(), video)
        self.assertEqual((machine.root / MODULE.AUDIO_MANIFEST).read_bytes(), audio)
        self.assertEqual((machine.root / MODULE.DTB).read_bytes(), D2)
        self.assertFalse((machine.root / MODULE.PACMAN_LOCK).exists())

        code, out, err = self.invoke(machine, '--resume-updates', '--apply')
        self.assertEqual((code, err), (0, ''), err)
        receipt = json.loads(out)
        self.assertTrue(receipt['applied'])
        self.assertEqual(receipt['changes'], changes)
        self.assertEqual(receipt['boot_sha256'], sha(updated))
        self.assertEqual(machine.boot, updated)
        self.assertEqual(machine.previous, restored)
        done = MODULE.parse(machine.boot)
        self.assertEqual([done[name] for name in ('loader', 'bootargs', 'initramfs', 'kernel')],
                         [owner[name] for name in ('loader', 'bootargs', 'initramfs', 'kernel')])
        self.assertEqual(done['dtb'], MODULE.parse(updated)['dtb'])
        self.assertFalse((machine.root / BOOT_HOLD).exists())
        self.assertFalse((machine.root / BOOT_JOURNAL).exists())
        self.assertEqual((machine.root / MODULE.VIDEO_MANIFEST).read_bytes(), video)
        self.assertEqual((machine.root / MODULE.AUDIO_MANIFEST).read_bytes(), audio)

    def test_resume_updates_preflight_failure_leaves_every_byte(self):
        machine, restored, _, _ = self.prepare_kernel_rollback_hold()
        hold_bytes = (machine.root / BOOT_HOLD).read_bytes()
        video = (machine.root / MODULE.VIDEO_MANIFEST).read_bytes()
        previous = machine.previous
        machine.put(MODULE.AUDIO_MANIFEST, json.dumps({'kernel_release': '7.1.9-other'}).encode())
        audio = (machine.root / MODULE.AUDIO_MANIFEST).read_bytes()
        code, out, err = self.invoke(machine, '--resume-updates', '--apply')
        self.assertEqual(code, 1, err)
        self.assertEqual(out, '')
        self.assertIn('audio stack is not qualified', err)
        self.assertIn('boot.bin was not changed.', err)
        self.assertEqual(machine.boot, restored)
        self.assertEqual(machine.previous, previous)
        self.assertEqual((machine.root / MODULE.VIDEO_MANIFEST).read_bytes(), video)
        self.assertEqual((machine.root / MODULE.AUDIO_MANIFEST).read_bytes(), audio)
        self.assertEqual((machine.root / BOOT_HOLD).read_bytes(), hold_bytes)
        self.assertFalse((machine.root / BOOT_JOURNAL).exists())

    def test_pending_opposite_journal_still_refuses_while_held(self):
        machine, restored, updated, _ = self.prepare_kernel_rollback_hold()
        hold_bytes = (machine.root / BOOT_HOLD).read_bytes()
        video = (machine.root / MODULE.VIDEO_MANIFEST).read_bytes()
        plant_journal(machine, 'rollback', updated, restored, sha(K1))
        journal = (machine.root / BOOT_JOURNAL).read_bytes()
        code, _, err = self.invoke(machine, '--apply')
        self.assertEqual(code, 1, err)
        self.assertIn('rollback transition is pending', err)
        self.assertIn('m4-boot-compose --rollback --apply', err)
        self.assertNotIn('boot.bin was not changed', err)
        self.assertEqual(machine.boot, restored)
        self.assertEqual((machine.root / BOOT_HOLD).read_bytes(), hold_bytes)
        self.assertEqual((machine.root / BOOT_JOURNAL).read_bytes(), journal)
        self.assertEqual((machine.root / MODULE.VIDEO_MANIFEST).read_bytes(), video)

    def test_resume_pending_apply_then_composes_the_newer_packaged_devicetree(self):
        """Commit the recorded apply, then plan the packages that are installed now.

        The packaged devicetree moved forward while the journal was open. The
        receipt names both changes and the final boot SHA. Owner loader, boot
        arguments, and initramfs stay the original bytes. The cross-kernel
        backup stays the original image, not the intermediate recorded boot,
        and a later rollback restores that original. The recorded snapshots
        remain after the journal is cleared.
        """
        packaged = kernel(18)
        loader, bootargs, initramfs = owner_image(b'pending-owner')
        original = boot(kernel_bytes=K1, dtb=D1, loader=loader, bootargs=bootargs, initramfs=initramfs)
        recorded = boot(kernel_bytes=packaged, dtb=D1, loader=loader, bootargs=bootargs, initramfs=initramfs)
        final = boot(kernel_bytes=packaged, dtb=D2, loader=loader, bootargs=bootargs, initramfs=initramfs)
        machine = self.transition(boot_kernel=K1, package_kernel=packaged)
        machine.put(MODULE.BOOT, original)
        video = {'boot_kernel_sha256': sha(K1), 'kernel_release': RELEASE,
                 'owner_panel': 'kept', 'notch': 'owner'}
        raw = json.dumps(video, indent=4).encode() + b'\n'
        machine.put(MODULE.VIDEO_MANIFEST, raw)
        audio = (machine.root / MODULE.AUDIO_MANIFEST).read_bytes()
        plant_journal(machine, 'apply', original, recorded, sha(packaged))
        machine.put(MODULE.DTB, D2)
        machine.put(MODULE.BOOT + MODULE.PREVIOUS, b'decoy previous')
        code, out, err = machine.run('--apply')
        self.assertEqual((code, err), (0, ''), err)
        receipt = json.loads(out)
        self.assertEqual(machine.boot, final)
        self.assertEqual(machine.previous, original)
        self.assertNotEqual(machine.previous, recorded)
        self.assertEqual(receipt['boot_sha256'], sha(final))
        self.assertEqual(receipt['kernel_sha256'], sha(packaged))
        self.assertEqual(receipt['devicetree_sha256'], sha(D2))
        self.assertEqual(sorted(receipt['changes']), ['dtb', 'kernel'])
        self.assertTrue(receipt['applied'])
        self.assertEqual(receipt['status'], 'updated; takes effect at next boot')
        self.assertFalse((machine.root / BOOT_JOURNAL).exists())
        self.assertEqual((machine.root / (BOOT_SNAPSHOTS + '/' + sha(original))).read_bytes(), original)
        self.assertEqual((machine.root / (BOOT_SNAPSHOTS + '/' + sha(recorded))).read_bytes(), recorded)
        done = MODULE.parse(machine.boot)
        self.assertEqual((done['loader'], done['bootargs'], done['initramfs']), (loader, bootargs, initramfs))
        self.assertEqual((done['kernel'], done['dtb']), (packaged, D2))
        parsed = json.loads((machine.root / MODULE.VIDEO_MANIFEST).read_bytes())
        self.assertEqual(parsed['boot_kernel_sha256'], sha(packaged))
        self.assertEqual((parsed['owner_panel'], parsed['notch']), ('kept', 'owner'))
        self.assertEqual((machine.root / MODULE.AUDIO_MANIFEST).read_bytes(), audio)
        code, out, err = machine.run('--rollback', '--apply')
        self.assertEqual((code, err), (0, ''), err)
        rolled = json.loads(out)
        self.assertEqual(machine.boot, original)
        self.assertEqual(machine.previous, final)
        self.assertEqual(rolled['boot_sha256'], sha(original))
        self.assertEqual(rolled['restore_sha256'], sha(original))
        self.assertEqual(rolled['status'], 'rolled back; takes effect at next boot')
        restored = MODULE.parse(machine.boot)
        self.assertEqual((restored['loader'], restored['bootargs'], restored['initramfs']),
                         (loader, bootargs, initramfs))
        self.assertEqual((restored['kernel'], restored['dtb']), (K1, D1))
        parsed = json.loads((machine.root / MODULE.VIDEO_MANIFEST).read_bytes())
        self.assertEqual(parsed['boot_kernel_sha256'], sha(K1))
        self.assertEqual((parsed['owner_panel'], parsed['notch']), ('kept', 'owner'))
        self.assertEqual((machine.root / MODULE.AUDIO_MANIFEST).read_bytes(), audio)
        self.assertEqual((machine.root / MODULE.KERNEL).read_bytes(), packaged)
        self.assertEqual((machine.root / MODULE.DTB).read_bytes(), D2)
        self.assertTrue((machine.root / BOOT_HOLD).is_file())

    def test_replayed_journal_receipt_names_each_changed_segment(self):
        """A resumed apply lists every segment the journal changed.

        Packages equal the recorded destination, so planning adds nothing and
        a hard-coded kernel list cannot stand in for a devicetree change. The
        backlight handoff also changes stage2; the receipt is the sorted union
        of those names, with no duplicates.
        """
        def assert_replay(machine, original, target, changes):
            self.assertEqual(machine.boot, original)
            self.assertEqual((machine.root / MODULE.KERNEL).read_bytes(), MODULE.parse(target)['kernel'])
            self.assertEqual((machine.root / MODULE.DTB).read_bytes(), MODULE.parse(target)['dtb'])
            self.assertFalse((machine.root / BOOT_HOLD).exists())
            machine.put(MODULE.BOOT + MODULE.PREVIOUS, original)
            plant_journal(machine, 'apply', original, target, sha(MODULE.parse(target)['kernel']))
            loaded = MODULE.load_journal(machine.root)
            self.assertEqual((loaded['operation'], loaded['from_bytes'], loaded['to_bytes']),
                             ('apply', original, target))
            self.assertEqual(machine.boot, loaded['from_bytes'])
            audio = (machine.root / MODULE.AUDIO_MANIFEST).read_bytes()
            code, out, err = machine.run('--apply')
            self.assertEqual((code, err), (0, ''), err)
            receipt = json.loads(out)
            self.assertEqual(receipt['changes'], changes)
            self.assertEqual(receipt['changes'], sorted(set(receipt['changes'])))
            self.assertEqual(receipt['boot_sha256'], sha(machine.boot))
            self.assertEqual(receipt['boot_sha256'], sha(target))
            self.assertTrue(receipt['applied'])
            self.assertEqual(receipt['status'], 'updated; takes effect at next boot')
            self.assertEqual(machine.boot, target)
            done, want = MODULE.parse(machine.boot), MODULE.parse(target)
            names = ('loader', 'bootargs', 'kernel', 'dtb', 'initramfs')
            self.assertEqual([done[name] for name in names], [want[name] for name in names])
            self.assertEqual(machine.previous, original)
            self.assertFalse((machine.root / BOOT_JOURNAL).exists())
            self.assertFalse((machine.root / BOOT_HOLD).exists())
            self.assertEqual((machine.root / MODULE.AUDIO_MANIFEST).read_bytes(), audio)
            parsed = json.loads((machine.root / MODULE.VIDEO_MANIFEST).read_bytes())
            self.assertEqual(parsed['boot_kernel_sha256'], sha(want['kernel']))

        packaged = kernel(33)
        loader, bootargs, initramfs = owner_image(b'receipt-owner')
        original = boot(kernel_bytes=K1, dtb=D1, loader=loader, bootargs=bootargs, initramfs=initramfs)
        target = boot(kernel_bytes=packaged, dtb=D2, loader=loader, bootargs=bootargs, initramfs=initramfs)
        self.assertEqual(MODULE.parse(original)['loader'], MODULE.parse(target)['loader'])
        self.assertNotEqual(sha(MODULE.parse(original)['kernel']), sha(MODULE.parse(target)['kernel']))
        self.assertNotEqual(sha(MODULE.parse(original)['dtb']), sha(MODULE.parse(target)['dtb']))
        machine = self.transition(boot_kernel=K1, package_kernel=packaged)
        machine.put(MODULE.BOOT, original)
        machine.put(MODULE.DTB, D2)
        with self.subTest(changes='dtb kernel'):
            assert_replay(machine, original, target, ['dtb', 'kernel'])

        machine, tree, old_loader, loaders, _ = self.handoff_machine()
        patcher = mock.patch.object(
            MODULE, 'KERNEL_TRANSITIONS', {(sha(K1), sha(K2)), (sha(K2), sha(K1))})
        patcher.start()
        self.addCleanup(patcher.stop)
        machine.put(MODULE.KERNEL, K2)
        original = boot(kernel_bytes=K1, dtb=D1, loader=old_loader)
        target = boot(kernel_bytes=K2, dtb=tree, loader=loaders['hidden'])
        source, dest = MODULE.parse(original), MODULE.parse(target)
        self.assertEqual(MODULE.KNOWN_LOADERS[sha(source['loader'])], MODULE.KNOWN_LOADERS[sha(dest['loader'])])
        self.assertNotEqual(source['loader'], dest['loader'])
        self.assertNotEqual(sha(source['kernel']), sha(dest['kernel']))
        self.assertNotEqual(sha(source['dtb']), sha(dest['dtb']))
        self.assertEqual(sha(dest['dtb']), MODULE.BACKLIGHT_DTB)
        self.assertEqual(sha(dest['loader']), MODULE.BACKLIGHT_LOADERS['hidden'][1])
        machine.put(MODULE.BOOT, original)
        with self.subTest(changes='dtb kernel loader'):
            assert_replay(machine, original, target, ['dtb', 'kernel', 'loader'])

    def test_corrupt_journal_or_neither_side_reports_unresolved_recovery(self):
        """Do not claim boot.bin was unchanged, and do not delete the journal."""
        def preserved(machine, paths):
            found = {}
            for relative in paths:
                path = machine.root / relative
                found[relative] = path.read_bytes() if path.is_file() else None
            return found

        machine = Machine(self)
        machine.put(BOOT_JOURNAL, b'{not a journal')
        paths = (MODULE.BOOT, MODULE.BOOT + MODULE.PREVIOUS, MODULE.VIDEO_MANIFEST,
                 MODULE.AUDIO_MANIFEST, BOOT_JOURNAL)
        before = preserved(machine, paths)
        for args in ((), ('--apply',), ('--rollback',), ('--rollback', '--apply')):
            with self.subTest(corrupt=args):
                code, out, err = machine.run(*args)
                self.assertEqual(code, 1, out + err)
                self.assertEqual(out, '')
                assert_unresolved_recovery(self, err)
                self.assertEqual(preserved(machine, paths), before)

        packaged = kernel(12)
        machine = self.transition(package_kernel=packaged)
        desired = boot(kernel_bytes=packaged, dtb=D1, loader=LOADER + b' notch')
        plant_journal(machine, 'apply', machine.boot_before, desired, sha(packaged))
        neither = boot(kernel_bytes=K1, dtb=D2, loader=LOADER + b' notch')
        self.assertNotIn(neither, (machine.boot_before, desired))
        machine.put(MODULE.BOOT, neither)
        machine.put(MODULE.BOOT + MODULE.PREVIOUS, b'decoy previous')
        paths = (MODULE.BOOT, MODULE.BOOT + MODULE.PREVIOUS, MODULE.VIDEO_MANIFEST,
                 MODULE.AUDIO_MANIFEST, BOOT_JOURNAL,
                 BOOT_SNAPSHOTS + '/' + sha(machine.boot_before),
                 BOOT_SNAPSHOTS + '/' + sha(desired))
        before = preserved(machine, paths)
        code, out, err = machine.run('--apply')
        self.assertEqual(code, 1, out + err)
        self.assertEqual(out, '')
        assert_unresolved_recovery(self, err)
        self.assertIn('neither side', err)
        self.assertEqual(preserved(machine, paths), before)

    def test_unresolved_recovery_names_the_journal_under_the_compose_root(self):
        """Corrupt and pending refusals name ``str(root / JOURNAL)``.

        A compose root of ``/`` is ``/var/lib/omarchy-m4/boot-compose/journal``.
        This sandbox names its own journal. The check does not depend on cwd.
        The journal is not deleted, and the refusal does not claim that
        boot.bin was left unchanged.
        """
        self.assertEqual(str(Path('/') / MODULE.JOURNAL), '/var/lib/omarchy-m4/boot-compose/journal')

        machine = Machine(self)
        self.assertNotEqual(machine.root, Path('/'))
        rooted = str(machine.root / MODULE.JOURNAL)
        self.assertNotEqual(rooted, MODULE.JOURNAL)
        self.assertTrue(rooted.startswith(str(machine.root) + '/'))
        machine.put(MODULE.JOURNAL, b'{not a journal')
        before = self.protected_bytes(machine)
        for args in ((), ('--apply',)):
            with self.subTest(kind='corrupt', args=args):
                code, out, err = machine.run(*args)
                self.assertEqual(code, 1, out + err)
                self.assertEqual(out, '')
                self.assertIn(rooted, err)
                self.assertNotIn('boot.bin was not changed', err)
                self.assertIn('unresolved', err.lower())
                self.assertIn('recorded there', err)
                self.assertIn('Do not assume boot.bin is unchanged.', err)
                self.assertEqual(self.protected_bytes(machine), before)
                self.assertEqual((machine.root / MODULE.JOURNAL).read_bytes(), b'{not a journal')

        packaged = kernel(12)
        pending = self.transition(package_kernel=packaged)
        desired = boot(kernel_bytes=packaged, dtb=D1, loader=LOADER + b' notch')
        plant_journal(pending, 'apply', pending.boot_before, desired, sha(packaged))
        self.assertEqual(pending.boot, MODULE.load_journal(pending.root)['from_bytes'])
        rooted = str(pending.root / MODULE.JOURNAL)
        before = self.protected_bytes(pending)
        code, out, err = pending.run('--rollback', '--apply')
        self.assertEqual(code, 1, out + err)
        self.assertEqual(out, '')
        self.assertIn('apply transition is pending', err)
        self.assertIn(rooted, err)
        self.assertNotIn('boot.bin was not changed', err)
        self.assertIn('recorded there', err)
        self.assertIn('Do not assume boot.bin is unchanged.', err)
        self.assertEqual(self.protected_bytes(pending), before)
        self.assertFalse((pending.root / MODULE.PACMAN_LOCK).exists())

    def test_commit_transition_refuses_boot_on_neither_side(self):
        packaged = kernel(12)
        machine = self.transition(package_kernel=packaged)
        desired = boot(kernel_bytes=packaged, dtb=D1, loader=LOADER + b' notch')
        plant_journal(machine, 'apply', machine.boot_before, desired, sha(packaged))
        neither = boot(kernel_bytes=K1, dtb=D2, loader=LOADER + b' notch')
        machine.put(MODULE.BOOT, neither)
        journal_bytes = (machine.root / BOOT_JOURNAL).read_bytes()
        video = (machine.root / MODULE.VIDEO_MANIFEST).read_bytes()
        audio = (machine.root / MODULE.AUDIO_MANIFEST).read_bytes()
        journal = MODULE.load_journal(machine.root)
        with self.assertRaises(MODULE.Refused) as caught:
            MODULE.commit_transition(machine.root, journal)
        self.assertIn('neither side of the pending transition', str(caught.exception))
        self.assertEqual(machine.boot, neither)
        self.assertIsNone(machine.previous)
        self.assertEqual((machine.root / BOOT_JOURNAL).read_bytes(), journal_bytes)
        self.assertEqual((machine.root / (BOOT_SNAPSHOTS + '/' + sha(machine.boot_before))).read_bytes(),
                         machine.boot_before)
        self.assertEqual((machine.root / (BOOT_SNAPSHOTS + '/' + sha(desired))).read_bytes(), desired)
        self.assertEqual((machine.root / MODULE.VIDEO_MANIFEST).read_bytes(), video)
        self.assertEqual((machine.root / MODULE.AUDIO_MANIFEST).read_bytes(), audio)

    def test_cross_release_exact_pair_refuses_before_writes(self):
        """An exact pair whose hashes do not share a release is not a transition."""
        third, fourth = kernel(21), kernel(22)
        source, target = '7.1.9-source-release', '7.1.9-other-release'
        loader, bootargs, initramfs = owner_image(b'cross-release')
        current = boot(kernel_bytes=third, dtb=D1, loader=loader, bootargs=bootargs, initramfs=initramfs)
        machine = Machine(self, boot_bytes=current, package_kernel=fourth, package_dtb=D1)
        (machine.root / 'usr/lib/modules' / target).mkdir(parents=True)
        for patcher in (
                mock.patch.dict(MODULE.KNOWN_KERNELS, {sha(third): source, sha(fourth): target}, clear=True),
                mock.patch.object(MODULE, 'KERNEL_TRANSITIONS', {(sha(third), sha(fourth)),
                                                                 (sha(fourth), sha(third))})):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.assertIn((sha(third), sha(fourth)), set(MODULE.KERNEL_TRANSITIONS))
        video = {'boot_kernel_sha256': sha(fourth), 'kernel_release': target, 'owner_panel': 'kept'}
        raw = json.dumps(video, indent=4).encode() + b'\n'
        machine.put(MODULE.VIDEO_MANIFEST, raw)
        machine.put(MODULE.AUDIO_MANIFEST, json.dumps({'kernel_release': target}).encode())
        audio = (machine.root / MODULE.AUDIO_MANIFEST).read_bytes()
        code, _, err = machine.run('--apply')
        self.assertEqual(MODULE.transition_kind(sha(third), sha(fourth)), 'refused')
        self.assertEqual(code, 1, err)
        self.assertIn('not qualified', err)
        self.assertIn('boot.bin was not changed.', err)
        self.assertEqual(machine.boot, current)
        self.assertIsNone(machine.previous)
        self.assertEqual((machine.root / MODULE.VIDEO_MANIFEST).read_bytes(), raw)
        self.assertEqual((machine.root / MODULE.AUDIO_MANIFEST).read_bytes(), audio)
        self.assertFalse((machine.root / BOOT_JOURNAL).exists())
        self.assertFalse((machine.root / BOOT_SNAPSHOTS).exists() and
                         any((machine.root / BOOT_SNAPSHOTS).iterdir()))

    def test_unrelated_video_pin_refuses_before_journal_creation(self):
        packaged = kernel(12)
        machine = self.transition(package_kernel=packaged, video_pin=sha(kernel(9)))
        code, _, err = machine.run('--apply')
        self.assertEqual(code, 1, err)
        self.assertIn('not qualified', err)
        self.assertIn('boot.bin was not changed.', err)
        self.assertEqual(machine.boot, machine.boot_before)
        self.assertIsNone(machine.previous)
        self.assertEqual((machine.root / MODULE.VIDEO_MANIFEST).read_bytes(), machine.video_before)
        self.assertEqual((machine.root / MODULE.AUDIO_MANIFEST).read_bytes(), machine.audio_before)
        self.assertFalse((machine.root / BOOT_JOURNAL).exists())
        snaps = machine.root / BOOT_SNAPSHOTS
        self.assertFalse(snaps.exists() and any(snaps.iterdir()))

    def test_root_snapshot_budget_and_efi_budget_refuse_separately(self):
        """Each filesystem is named. The other one is ample, and nothing is written."""
        packaged = kernel(12)

        def run(case):
            machine = self.transition(package_kernel=packaged)
            efi = machine.root / 'boot' / 'efi'
            ample = types.SimpleNamespace(f_bavail=1 << 40, f_frsize=1)
            # The margin alone cannot pay for both snapshots.
            tight = types.SimpleNamespace(f_bavail=MODULE.MARGIN, f_frsize=1)
            tiny = types.SimpleNamespace(f_bavail=1, f_frsize=1)

            def fake(path):
                probed = Path(path)
                on_efi = probed == efi or efi in probed.parents
                if case == 'efi':
                    return tiny if on_efi else ample
                return ample if on_efi else tight

            video = (machine.root / MODULE.VIDEO_MANIFEST).read_bytes()
            with mock.patch.object(MODULE.os, 'statvfs', side_effect=fake):
                code, _, err = machine.run('--apply')
            self.assertEqual(code, 1, err)
            if case == 'efi':
                self.assertIn('Not enough free space on the EFI partition', err)
                self.assertNotIn('root filesystem', err)
            else:
                self.assertIn('Not enough free space on the root filesystem', err)
                self.assertNotIn('EFI partition', err)
            self.assertIn('boot.bin was not changed.', err)
            self.assertEqual(machine.boot, machine.boot_before)
            self.assertIsNone(machine.previous)
            self.assertEqual((machine.root / MODULE.VIDEO_MANIFEST).read_bytes(), video)
            self.assertFalse((machine.root / BOOT_JOURNAL).exists())
            snaps = machine.root / BOOT_SNAPSHOTS
            self.assertFalse(snaps.exists() and any(path.is_file() for path in snaps.rglob('*')))

        for case in ('efi', 'root'):
            with self.subTest(case=case):
                run(case)

    def test_compose_refuses_a_pending_rollback_journal(self):
        packaged = kernel(12)
        machine = self.transition(package_kernel=packaged)
        newer = boot(kernel_bytes=packaged, dtb=D1, loader=LOADER + b' notch')
        plant_journal(machine, 'rollback', newer, machine.boot_before, sha(K1))
        journal = (machine.root / BOOT_JOURNAL).read_bytes()
        video = (machine.root / MODULE.VIDEO_MANIFEST).read_bytes()
        code, out, err = machine.run()
        self.assertEqual(code, 0, err)
        self.assertFalse(json.loads(out)['applied'])
        self.assertEqual(machine.boot, machine.boot_before)
        code, out, err = machine.run('--apply')
        self.assertEqual(code, 1, out + err)
        self.assertEqual(out, '')
        self.assertIn('rollback transition is pending', err)
        self.assertIn('m4-boot-compose --rollback --apply', err)
        self.assertNotIn('boot.bin was not changed', err)
        self.assertEqual(machine.boot, machine.boot_before)
        self.assertIsNone(machine.previous)
        self.assertEqual((machine.root / MODULE.VIDEO_MANIFEST).read_bytes(), video)
        self.assertEqual((machine.root / BOOT_JOURNAL).read_bytes(), journal)
        self.assertEqual((machine.root / (BOOT_SNAPSHOTS + '/' + sha(newer))).read_bytes(), newer)
        self.assertEqual((machine.root / (BOOT_SNAPSHOTS + '/' + sha(machine.boot_before))).read_bytes(),
                         machine.boot_before)

    def test_notch_preference_mismatch_names_the_toggle(self):
        """A preference mismatch tells the owner how to put the notch back."""
        hidden = LOADER + b' pref-hidden'
        shown = LOADER + b' pref-shown'
        patcher = mock.patch.dict(MODULE.KNOWN_LOADERS, {sha(hidden): 'hidden', sha(shown): 'shown'})
        patcher.start()
        self.addCleanup(patcher.stop)
        current = boot(loader=shown)
        older = boot(loader=hidden)
        machine = Machine(self, boot_bytes=current, package_dtb=D1)
        machine.put(MODULE.BOOT + MODULE.PREVIOUS, older)
        video = (machine.root / MODULE.VIDEO_MANIFEST).read_bytes()
        audio = (machine.root / MODULE.AUDIO_MANIFEST).read_bytes()
        code, out, err = machine.run('--rollback', '--apply')
        self.assertEqual(code, 1, err)
        self.assertEqual(out, '')
        self.assertIn('notch preference', err)
        self.assertIn('m4-notch --show', err)
        self.assertIn('m4-notch --hide', err)
        self.assertIn('boot.bin was not changed.', err)
        self.assertEqual(machine.boot, current)
        self.assertEqual(machine.previous, older)
        self.assertEqual((machine.root / MODULE.VIDEO_MANIFEST).read_bytes(), video)
        self.assertEqual((machine.root / MODULE.AUDIO_MANIFEST).read_bytes(), audio)
        self.assertFalse((machine.root / BOOT_JOURNAL).exists())
        self.assertFalse((machine.root / BOOT_HOLD).exists())

    def saved_bytes(self, machine):
        """Boot, backup, manifests, packages, and recovery files currently on disk."""
        paths = [MODULE.BOOT, MODULE.BOOT + MODULE.PREVIOUS, MODULE.VIDEO_MANIFEST,
                 MODULE.AUDIO_MANIFEST, MODULE.KERNEL, MODULE.DTB, BOOT_JOURNAL, BOOT_HOLD]
        backlight = machine.root / 'usr/lib/omarchy-m4/backlight'
        if backlight.is_dir():
            paths.extend(str(path.relative_to(machine.root))
                         for path in sorted(backlight.iterdir()) if path.is_file())
        snaps = machine.root / BOOT_SNAPSHOTS
        if snaps.is_dir():
            paths.extend(BOOT_SNAPSHOTS + '/' + path.name
                         for path in sorted(snaps.iterdir()) if path.is_file())
        found = {}
        for relative in paths:
            path = machine.root / relative
            found[relative] = path.read_bytes() if path.is_file() else None
        return found

    def assert_no_recovery_writes(self, machine, before, *, hold_bytes=None):
        after = self.saved_bytes(machine)
        self.assertIsNone(after.get(BOOT_JOURNAL))
        self.assertFalse(any(name.startswith(BOOT_SNAPSHOTS + '/') for name in after))
        if hold_bytes is None:
            self.assertIsNone(after.get(BOOT_HOLD))
        else:
            self.assertEqual(after.get(BOOT_HOLD), hold_bytes)
        for name, data in before.items():
            if name in (BOOT_HOLD, BOOT_JOURNAL) or name.startswith(BOOT_SNAPSHOTS + '/'):
                continue
            self.assertEqual(after.get(name), data, name)

    def same_kernel_rollback_retry(self, machine, current, original, boundary):
        """Fail one rollback rename, then a fresh rollback must finish on ``original``.

        The rename is allowed to complete before the error. ``.previous`` may
        then hold ``current``; the snapshot store is what still has ``original``.
        """
        current_layout, original_layout = MODULE.parse(current), MODULE.parse(original)
        self.assertEqual(current_layout['kernel'], original_layout['kernel'])
        self.assertEqual(current_layout['bootargs'], original_layout['bootargs'])
        self.assertEqual(current_layout['initramfs'], original_layout['initramfs'])
        self.assertNotEqual((current_layout['dtb'], current_layout['loader']),
                            (original_layout['dtb'], original_layout['loader']))
        video = (machine.root / MODULE.VIDEO_MANIFEST).read_bytes()
        audio = (machine.root / MODULE.AUDIO_MANIFEST).read_bytes()
        packages = ((machine.root / MODULE.KERNEL).read_bytes(), (machine.root / MODULE.DTB).read_bytes())
        self.assertFalse((machine.root / BOOT_HOLD).exists())
        self.assertFalse((machine.root / BOOT_JOURNAL).exists())
        real = os.replace
        fired = {'n': 0}

        def fail_after_rename(source, target):
            result = real(source, target)
            if fired['n'] == 0 and Path(target).name == boundary:
                fired['n'] = 1
                raise OSError('power cut after ' + boundary)
            return result

        with mock.patch.object(MODULE.os, 'replace', side_effect=fail_after_rename):
            code, out, err = machine.run('--rollback', '--apply')
        self.assertEqual(fired['n'], 1)
        self.assertEqual(code, 1, err)
        self.assertEqual(out, '')
        self.assertIn('power cut after ' + boundary, err)
        self.assertNotIn('boot.bin was not changed', err)
        self.assertIn('unresolved', err.lower())
        self.assertTrue((machine.root / BOOT_HOLD).is_file())
        self.assertEqual(machine.boot, current if boundary == 'boot.bin.previous' else original)
        journal_during = (machine.root / BOOT_JOURNAL).read_bytes() if (machine.root / BOOT_JOURNAL).is_file() else None
        during = self.saved_bytes(machine)
        for args in (('--rollback',), ()):
            code, _, inspect_err = machine.run(*args)
            self.assertEqual(code, 0, inspect_err)
            self.assertEqual(self.saved_bytes(machine), during)
        code, out, err = machine.run('--rollback', '--apply')
        self.assertEqual((code, err), (0, ''), err)
        receipt = json.loads(out)
        self.assertEqual(machine.boot, original)
        self.assertEqual(machine.previous, current)
        self.assertEqual(receipt['boot_sha256'], sha(original))
        self.assertEqual(receipt['restore_sha256'], sha(original))
        self.assertIsNotNone(journal_during)
        self.assertFalse((machine.root / BOOT_JOURNAL).exists())
        self.assertEqual((machine.root / BOOT_HOLD).read_bytes(), during[BOOT_HOLD])
        done = MODULE.parse(machine.boot)
        self.assertEqual([done[name] for name in ('loader', 'bootargs', 'kernel', 'dtb', 'initramfs')],
                         [original_layout[name] for name in ('loader', 'bootargs', 'kernel', 'dtb', 'initramfs')])
        self.assertEqual((machine.root / MODULE.VIDEO_MANIFEST).read_bytes(), video)
        self.assertEqual((machine.root / MODULE.AUDIO_MANIFEST).read_bytes(), audio)
        self.assertEqual(((machine.root / MODULE.KERNEL).read_bytes(), (machine.root / MODULE.DTB).read_bytes()),
                         packages)
        for name, data in during.items():
            if name.startswith('usr/lib/omarchy-m4/backlight/'):
                self.assertEqual((machine.root / name).read_bytes(), data, name)
        code, out, err = machine.run('--apply')
        self.assertEqual((code, err), (0, ''), err)
        held = json.loads(out)
        self.assertFalse(held['applied'])
        self.assertEqual(held['status'], MODULE.HELD_STATUS)
        self.assertEqual(machine.boot, original)
        self.assertEqual(machine.previous, current)
        self.assertEqual((machine.root / MODULE.VIDEO_MANIFEST).read_bytes(), video)
        self.assertEqual((machine.root / MODULE.AUDIO_MANIFEST).read_bytes(), audio)

    def test_same_kernel_rollback_retry_keeps_original_target(self):
        """A devicetree rollback that dies after either rename still restores the original tree."""
        for boundary in ('boot.bin.previous', 'boot.bin'):
            with self.subTest(boundary=boundary):
                machine = Machine(self)
                code, _, err = machine.run('--apply')
                self.assertEqual((code, err), (0, ''), err)
                current, original = boot(dtb=D2), boot()
                self.assertEqual((machine.boot, machine.previous), (current, original))
                self.same_kernel_rollback_retry(machine, current, original, boundary)

    def test_backlight_rollback_retry_keeps_original_tree_and_loader(self):
        """Shown and hidden handoff rollbacks retry to the original tree and loader.

        Each subTest drops its loader and backlight pins before the next one.
        """
        for shown in (False, True):
            for boundary in ('boot.bin.previous', 'boot.bin'):
                with self.subTest(shown=shown, boundary=boundary):
                    machine, tree, old_loader, loaders, _ = self.handoff_machine(
                        shown=shown, hold_patches=False)
                    try:
                        code, _, err = machine.run('--apply')
                        self.assertEqual((code, err), (0, ''), err)
                        mode = 'shown' if shown else 'hidden'
                        current = boot(loader=loaders[mode], dtb=tree)
                        original = boot(loader=old_loader)
                        self.assertEqual(machine.boot, current)
                        self.assertEqual(machine.previous, original)
                        self.same_kernel_rollback_retry(machine, current, original, boundary)
                    finally:
                        for patcher in reversed(machine.handoff_patchers):
                            patcher.stop()

    def rollback_fixtures(self, kind):
        """A valid rollback pair with no hold, journal, or snapshots yet."""
        if kind == 'cross':
            machine = self.transition(boot_kernel=K2, package_kernel=K1)
            current = machine.boot
            original = boot(kernel_bytes=K1, loader=LOADER + b' notch')
            machine.put(MODULE.BOOT + MODULE.PREVIOUS, original)
            self.assertNotEqual(MODULE.parse(current)['kernel'], MODULE.parse(original)['kernel'])
        else:
            machine = Machine(self, boot_bytes=boot(dtb=D2))
            current, original = boot(dtb=D2), boot()
            machine.put(MODULE.BOOT + MODULE.PREVIOUS, original)
            self.assertEqual(MODULE.parse(current)['kernel'], MODULE.parse(original)['kernel'])
        self.assertFalse((machine.root / BOOT_HOLD).exists())
        self.assertFalse((machine.root / BOOT_JOURNAL).exists())
        snaps = machine.root / BOOT_SNAPSHOTS
        self.assertFalse(snaps.exists() and any(path.is_file() for path in snaps.rglob('*')))
        return machine, self.saved_bytes(machine)

    def test_rollback_space_refusal_creates_no_hold(self):
        """EFI and snapshot shortages refuse before a hold, journal, or payload write.

        Same-kernel rollback journals its target, so the snapshot store is in
        that budget too. A hold that already exists keeps its bytes.
        """
        efi_cases = (('same', 'efi'), ('cross', 'efi'), ('same', 'snapshot'), ('cross', 'snapshot'))
        for kind, case in efi_cases:
            with self.subTest(kind=kind, case=case):
                machine, before = self.rollback_fixtures(kind)
                efi = machine.root / 'boot' / 'efi'
                ample = types.SimpleNamespace(f_bavail=1 << 40, f_frsize=1)
                tight = types.SimpleNamespace(f_bavail=MODULE.MARGIN, f_frsize=1)
                tiny = types.SimpleNamespace(f_bavail=1, f_frsize=1)

                def fake(path, case=case):
                    probed = Path(path)
                    on_efi = probed == efi or efi in probed.parents
                    if case == 'efi':
                        return tiny if on_efi else ample
                    return ample if on_efi else tight

                with mock.patch.object(MODULE.os, 'statvfs', side_effect=fake):
                    code, out, err = machine.run('--rollback', '--apply')
                self.assertEqual(code, 1, out + err)
                self.assertEqual(out, '')
                if case == 'efi':
                    self.assertIn('Not enough free space on the EFI partition', err)
                    self.assertNotIn('root filesystem', err)
                else:
                    self.assertIn('Not enough free space on the root filesystem', err)
                    self.assertNotIn('EFI partition', err)
                self.assert_no_recovery_writes(machine, before)
                self.assertIn('boot.bin was not changed.', err)

        machine, before = self.rollback_fixtures('cross')
        hold_bytes = b'already held\n'
        machine.put(BOOT_HOLD, hold_bytes)
        before[BOOT_HOLD] = hold_bytes
        efi = machine.root / 'boot' / 'efi'
        ample = types.SimpleNamespace(f_bavail=1 << 40, f_frsize=1)
        tiny = types.SimpleNamespace(f_bavail=1, f_frsize=1)

        def efi_only(path):
            probed = Path(path)
            return tiny if probed == efi or efi in probed.parents else ample

        with mock.patch.object(MODULE.os, 'statvfs', side_effect=efi_only):
            code, out, err = machine.run('--rollback', '--apply')
        self.assertEqual(code, 1, out + err)
        self.assertEqual(out, '')
        self.assertIn('Not enough free space on the EFI partition', err)
        self.assert_no_recovery_writes(machine, before, hold_bytes=hold_bytes)
        self.assertIn('boot.bin was not changed.', err)

    def test_pending_rollback_protected_refusal_creates_no_hold(self):
        """A pending rollback still checks the protected stacks before creating a hold."""
        def planted():
            packaged = kernel(12)
            machine = self.transition(package_kernel=packaged)
            original = machine.boot_before
            current = boot(kernel_bytes=packaged, dtb=D1, loader=LOADER + b' notch')
            machine.put(MODULE.BOOT, current)
            plant_journal(machine, 'rollback', current, original, sha(K1))
            loaded = MODULE.load_journal(machine.root)
            self.assertEqual(loaded['operation'], 'rollback')
            self.assertEqual((loaded['from_bytes'], loaded['to_bytes']), (current, original))
            self.assertFalse((machine.root / BOOT_HOLD).exists())
            return machine

        for kind in ('video', 'audio'):
            with self.subTest(kind=kind):
                machine = planted()
                if kind == 'video':
                    video = json.loads((machine.root / MODULE.VIDEO_MANIFEST).read_bytes())
                    video['boot_kernel_sha256'] = sha(kernel(9))
                    machine.put(MODULE.VIDEO_MANIFEST, (json.dumps(video, indent=4) + '\n').encode())
                    cause = 'video stack is not qualified'
                else:
                    machine.put(MODULE.AUDIO_MANIFEST, json.dumps({'kernel_release': '7.1.9-other'}).encode())
                    cause = 'audio stack is not qualified'
                before = self.saved_bytes(machine)
                code, out, err = machine.run('--rollback', '--apply')
                self.assertEqual(code, 1, err)
                self.assertEqual(out, '')
                self.assertIn(cause, err)
                self.assertFalse((machine.root / BOOT_HOLD).exists())
                self.assertEqual(self.saved_bytes(machine), before)
                assert_unresolved_recovery(self, err)

    def test_pending_rollback_efi_budget_refuses_before_hold(self):
        """A pending rollback pays the EFI commit budget before creating a hold.

        Snapshots already exist, so the store only has to keep the margin.
        The EFI partition is too small. ``--rollback --apply`` refuses that
        budget before any new hold. Journal, snapshots, boot, previous, video,
        and audio stay, and the refusal does not claim boot.bin was unchanged.
        """
        packaged = kernel(12)
        machine = self.transition(package_kernel=packaged)
        original = machine.boot_before
        current = boot(kernel_bytes=packaged, dtb=D1, loader=LOADER + b' notch')
        machine.put(MODULE.BOOT, current)
        machine.put(MODULE.BOOT + MODULE.PREVIOUS, original)
        plant_journal(machine, 'rollback', current, original, sha(K1))
        loaded = MODULE.load_journal(machine.root)
        self.assertEqual(loaded['operation'], 'rollback')
        self.assertEqual(loaded['from_bytes'], current)
        self.assertEqual(machine.boot, current)
        self.assertEqual(json.loads((machine.root / MODULE.VIDEO_MANIFEST).read_bytes())['boot_kernel_sha256'],
                         sha(K1))
        self.assertEqual(json.loads((machine.root / MODULE.AUDIO_MANIFEST).read_bytes())['kernel_release'],
                         RELEASE)
        self.assertFalse((machine.root / BOOT_HOLD).exists())
        before = self.saved_bytes(machine)
        efi = machine.root / 'boot' / 'efi'
        margin = types.SimpleNamespace(f_bavail=MODULE.MARGIN, f_frsize=1)
        tiny = types.SimpleNamespace(f_bavail=1, f_frsize=1)

        def fake(path):
            probed = Path(path)
            on_efi = probed == efi or efi in probed.parents
            return tiny if on_efi else margin

        with mock.patch.object(MODULE.os, 'statvfs', side_effect=fake):
            code, out, err = machine.run('--rollback', '--apply')
        self.assertEqual(code, 1, out + err)
        self.assertEqual(out, '')
        self.assertIn('Not enough free space on the EFI partition', err)
        self.assertNotIn('root filesystem', err)
        self.assertNotIn('boot.bin was not changed', err)
        self.assertFalse((machine.root / BOOT_HOLD).exists())
        self.assertEqual(machine.boot, current)
        self.assertEqual(machine.previous, original)
        self.assertEqual(self.saved_bytes(machine), before)
        assert_unresolved_recovery(self, err)

    def test_incoherent_same_kernel_rollback_target_refuses_before_hold(self):
        """A same-kernel rollback journal still requires the backlight handoff loader."""
        machine, tree, old_loader, loaders, _ = self.handoff_machine()
        current = boot(loader=loaders['hidden'], dtb=tree)
        target = boot(loader=old_loader, dtb=tree)
        self.assertEqual(MODULE.parse(current)['kernel'], MODULE.parse(target)['kernel'])
        self.assertNotEqual(MODULE.parse(current)['loader'], MODULE.parse(target)['loader'])
        machine.put(MODULE.BOOT, current)
        plant_journal(machine, 'rollback', current, target, sha(K1))
        self.assertEqual(MODULE.load_journal(machine.root)['operation'], 'rollback')
        before = self.saved_bytes(machine)
        code, out, err = machine.run('--rollback', '--apply')
        self.assertEqual(code, 1, err)
        self.assertEqual(out, '')
        self.assertIn('handoff', err)
        self.assertIn('stage2', err)
        self.assertFalse((machine.root / BOOT_HOLD).exists())
        self.assertEqual(self.saved_bytes(machine), before)
        assert_unresolved_recovery(self, err)

    def test_resumed_apply_late_refusal_does_not_name_missing_journal(self):
        """After the journal is cleared, a package refusal must not point back at it."""
        packaged = kernel(18)
        machine = self.transition(package_kernel=packaged)
        original = machine.boot_before
        committed = boot(kernel_bytes=packaged, dtb=D1, loader=LOADER + b' notch')
        plant_journal(machine, 'apply', original, committed, sha(packaged))
        machine.put(MODULE.DTB, b'corrupt packaged devicetree')
        audio = (machine.root / MODULE.AUDIO_MANIFEST).read_bytes()
        code, out, err = machine.run('--apply')
        self.assertEqual(code, 1, err)
        self.assertEqual(out, '')
        self.assertEqual(machine.boot, committed)
        self.assertEqual(machine.previous, original)
        parsed = json.loads((machine.root / MODULE.VIDEO_MANIFEST).read_bytes())
        self.assertEqual(parsed['boot_kernel_sha256'], sha(packaged))
        self.assertEqual((parsed['owner_panel'], parsed['notch']), ('kept', 'owner'))
        self.assertEqual((machine.root / MODULE.AUDIO_MANIFEST).read_bytes(), audio)
        self.assertFalse((machine.root / BOOT_JOURNAL).exists())
        self.assertIn('Packaged devicetree', err)
        self.assertNotIn('boot.bin was not changed', err)
        self.assertNotIn('recorded there', err)
        self.assertNotIn('operation recorded', err)
        self.assertIn('m4-boot-compose', err)
        self.assertTrue(any(word in err.lower() for word in ('inspect', 'fix', 'rerun')))
        self.assertTrue('--apply' in err or ('updater' in err.lower() and 'retry' in err.lower()))

    def test_manual_copy_back_manifest_failure_names_explicit_retry(self):
        """Copy-back that dies before the video pin is replaced names ``--rollback --apply``.

        The hold is present and the video pin still names the newer kernel.
        Ordinary inspection, ordinary ``--apply``, and ``--hook --apply`` refuse
        with that explicit rollback. They do not report a held success or tell
        the owner to release the hold. The explicit rollback then converges.
        """
        loader = LOADER + b' recognized handoff'
        patcher = mock.patch.dict(MODULE.KNOWN_LOADERS, {sha(loader): 'hidden'})
        patcher.start()
        self.addCleanup(patcher.stop)
        packaged = kernel(8)
        machine = self.transition(boot_kernel=K1, package_kernel=packaged, video_pin=sha(packaged))
        original = boot(kernel_bytes=K1, dtb=D1, loader=loader)
        machine.put(MODULE.BOOT, original)
        machine.put(MODULE.BOOT + MODULE.PREVIOUS, original)
        video = (machine.root / MODULE.VIDEO_MANIFEST).read_bytes()
        audio = (machine.root / MODULE.AUDIO_MANIFEST).read_bytes()
        self.assertFalse((machine.root / BOOT_JOURNAL).exists())
        self.assertNotEqual(json.loads(video)['boot_kernel_sha256'], sha(K1))
        real = os.replace

        def fail_manifest(source, target):
            if Path(target).name == 'j713-video-manifest.json':
                raise OSError('video manifest rename failed')
            return real(source, target)

        with mock.patch.object(MODULE.os, 'replace', side_effect=fail_manifest):
            code, out, err = machine.run('--rollback', '--apply')
        self.assertEqual(code, 1, err)
        self.assertEqual(out, '')
        self.assertIn('video manifest rename failed', err)
        self.assertEqual(machine.boot, original)
        self.assertEqual(machine.previous, original)
        self.assertEqual((machine.root / MODULE.VIDEO_MANIFEST).read_bytes(), video)
        self.assertEqual((machine.root / MODULE.AUDIO_MANIFEST).read_bytes(), audio)
        self.assertTrue((machine.root / BOOT_HOLD).is_file())
        self.assertFalse((machine.root / BOOT_JOURNAL).exists())
        held = self.protected_bytes(machine)
        self.assertIsNotNone(held[BOOT_HOLD])
        self.assertIsNone(held[BOOT_JOURNAL])
        self.assertNotEqual(
            json.loads(held[MODULE.VIDEO_MANIFEST])['boot_kernel_sha256'],
            sha(MODULE.parse(held[MODULE.BOOT])['kernel']))
        rollback_cmd = 'sudo m4-boot-compose --rollback --apply'
        for args in ((), ('--apply',)):
            with self.subTest(args=args):
                code, out, refusal = machine.run(*args)
                self.assertEqual(code, 1, out + refusal)
                self.assertEqual(out, '')
                self.assertIn(rollback_cmd, refusal)
                self.assertNotIn('resume-updates', refusal)
                self.assertNotIn('boot.bin was not changed', refusal)
                self.assertNotIn(MODULE.HELD_STATUS, out)
                self.assertEqual(self.protected_bytes(machine), held)
                self.assertFalse((machine.root / MODULE.PACMAN_LOCK).exists())
        machine.put(MODULE.PACMAN_LOCK, b'pacman transaction')
        code, out, refusal = machine.run('--hook', '--apply')
        self.assertEqual(code, 1, out + refusal)
        self.assertEqual(out, '')
        self.assertIn(rollback_cmd, refusal)
        self.assertNotIn('resume-updates', refusal)
        self.assertNotIn('boot.bin was not changed', refusal)
        self.assertNotIn(MODULE.HELD_STATUS, out)
        self.assertEqual(self.protected_bytes(machine), held)
        self.assertEqual((machine.root / MODULE.PACMAN_LOCK).read_bytes(), b'pacman transaction')
        (machine.root / MODULE.PACMAN_LOCK).unlink()
        code, out, retry_err = machine.run('--rollback', '--apply')
        self.assertEqual((code, retry_err), (0, ''), retry_err)
        receipt = json.loads(out)
        self.assertTrue(receipt['applied'])
        self.assertEqual(receipt['status'], MODULE.HELD_STATUS)
        self.assertEqual(machine.boot, original)
        self.assertEqual(machine.previous, original)
        parsed = json.loads((machine.root / MODULE.VIDEO_MANIFEST).read_bytes())
        self.assertEqual(parsed['boot_kernel_sha256'], sha(K1))
        self.assertEqual((parsed['owner_panel'], parsed['notch']), ('kept', 'owner'))
        self.assertEqual((machine.root / MODULE.AUDIO_MANIFEST).read_bytes(), audio)
        self.assertTrue((machine.root / BOOT_HOLD).is_file())
        self.assertFalse((machine.root / BOOT_JOURNAL).exists())
        self.assertNotIn('boot.bin was not changed', err)
        self.assertNotIn('recorded there', err)
        self.assertNotIn('operation recorded', err)
        self.assertIn('m4-boot-compose', err)
        self.assertIn('--rollback', err)
        self.assertIn('--apply', err)

    def protected_bytes(self, machine):
        """Boot, manifests, journal, snapshots, and hold bytes. No hold open."""
        paths = [MODULE.BOOT, MODULE.BOOT + MODULE.PREVIOUS, MODULE.VIDEO_MANIFEST,
                 MODULE.AUDIO_MANIFEST, MODULE.KERNEL, MODULE.DTB, BOOT_JOURNAL, BOOT_HOLD]
        snap_dir = machine.root / BOOT_SNAPSHOTS
        if snap_dir.is_dir():
            paths.extend(BOOT_SNAPSHOTS + '/' + item.name for item in snap_dir.iterdir())
        found = {}
        for relative in paths:
            path = machine.root / relative
            try:
                mode = os.lstat(path).st_mode
            except FileNotFoundError:
                found[relative] = None
                continue
            if stat.S_ISLNK(mode):
                found[relative] = ('symlink', os.readlink(path))
            elif stat.S_ISDIR(mode):
                found[relative] = ('dir',)
            elif stat.S_ISFIFO(mode):
                found[relative] = ('fifo',)
            elif stat.S_ISREG(mode):
                found[relative] = path.read_bytes()
            else:
                found[relative] = ('other', stat.S_IFMT(mode))
        return found

    def test_pending_guidance_names_the_command_that_actually_resumes(self):
        """A held apply journal must not be described as ordinary ``--apply``.

        The hold file and the apply journal are both real. Inspection and the
        opposite rollback name ``--resume-updates --apply`` and write nothing.
        Ordinary ``--apply`` keeps the hold. That resume command then commits.
        Without a hold, apply and rollback keep their ordinary commands.
        """
        resume = 'sudo m4-boot-compose --resume-updates --apply'
        ordinary = 'sudo m4-boot-compose --apply'
        packaged = kernel(31)
        machine = self.transition(package_kernel=packaged)
        restored = machine.boot_before
        updated = boot(kernel_bytes=packaged, dtb=D1, loader=LOADER + b' notch')
        self.assertNotEqual(restored, updated)
        plant_journal(machine, 'apply', restored, updated, sha(packaged))
        machine.put(BOOT_HOLD, b'held\n')
        before = self.protected_bytes(machine)
        self.assertEqual(before[BOOT_HOLD], b'held\n')
        self.assertIsNotNone(before[BOOT_JOURNAL])

        code, out, err = machine.run()
        self.assertEqual((code, err), (0, ''), err)
        receipt = json.loads(out)
        self.assertEqual(receipt['operation'], 'apply')
        self.assertFalse(receipt['applied'])
        self.assertEqual(receipt['resume'], resume)
        self.assertIn(resume, receipt['status'])
        self.assertNotIn('--rollback', receipt['status'])
        self.assertEqual(self.protected_bytes(machine), before)

        code, out, err = machine.run('--rollback')
        self.assertEqual((code, err), (0, ''), err)
        receipt = json.loads(out)
        self.assertEqual(receipt['resume'], resume)
        self.assertIn(resume, receipt['status'])
        self.assertNotIn(ordinary, receipt['status'].replace(resume, ''))
        self.assertEqual(self.protected_bytes(machine), before)

        code, out, err = machine.run('--rollback', '--apply')
        self.assertEqual(code, 1, out + err)
        self.assertEqual(out, '')
        self.assertNotIn('Traceback', err)
        self.assertIn('apply transition is pending', err)
        self.assertIn(resume, err)
        self.assertNotIn(ordinary, err.replace(resume, ''))
        self.assertEqual(self.protected_bytes(machine), before)

        code, out, err = machine.run('--apply')
        self.assertEqual(code, 1, out + err)
        self.assertEqual(out, '')
        self.assertNotIn('Traceback', err)
        self.assertIn(resume, err)
        self.assertEqual(self.protected_bytes(machine), before)
        self.assertEqual((machine.root / BOOT_HOLD).read_bytes(), b'held\n')
        self.assertTrue((machine.root / BOOT_JOURNAL).is_file())

        code, out, err = machine.run('--resume-updates', '--apply')
        self.assertEqual((code, err), (0, ''), err)
        self.assertNotIn('Traceback', err)
        self.assertEqual(machine.boot, updated)
        self.assertEqual(machine.previous, restored)
        self.assertFalse((machine.root / BOOT_HOLD).exists())
        self.assertFalse((machine.root / BOOT_JOURNAL).exists())
        parsed = json.loads((machine.root / MODULE.VIDEO_MANIFEST).read_bytes())
        self.assertEqual(parsed['boot_kernel_sha256'], sha(packaged))
        self.assertEqual((parsed['owner_panel'], parsed['notch']), ('kept', 'owner'))
        self.assertEqual((machine.root / MODULE.AUDIO_MANIFEST).read_bytes(), before[MODULE.AUDIO_MANIFEST])
        self.assertEqual((machine.root / MODULE.KERNEL).read_bytes(), before[MODULE.KERNEL])
        self.assertEqual((machine.root / MODULE.DTB).read_bytes(), before[MODULE.DTB])

        bare = self.transition(package_kernel=packaged)
        plant_journal(bare, 'apply', bare.boot_before, updated, sha(packaged))
        self.assertFalse((bare.root / BOOT_HOLD).exists())
        bare_before = self.protected_bytes(bare)
        code, out, err = bare.run()
        self.assertEqual((code, err), (0, ''), err)
        receipt = json.loads(out)
        self.assertEqual(receipt['resume'], ordinary)
        self.assertIn(ordinary, receipt['status'])
        self.assertNotIn('resume-updates', receipt['status'])
        self.assertEqual(self.protected_bytes(bare), bare_before)
        code, out, err = bare.run('--rollback', '--apply')
        self.assertEqual(code, 1, out + err)
        self.assertIn(ordinary, err)
        self.assertNotIn('resume-updates', err)
        self.assertEqual(self.protected_bytes(bare), bare_before)

    def test_nonregular_hold_is_refused_and_a_regular_hold_still_blocks(self):
        """A directory or FIFO hold is not absent, so ordinary apply cannot undo it.

        The positive case is a regular hold file: inspection and apply report
        the hold and leave the boot image. A symlink hold keeps the symlink
        refusal. Nothing opens a FIFO.
        """
        def fresh():
            machine = Machine(self)
            self.assertNotEqual(machine.boot, boot(dtb=D2))
            return machine

        regular = fresh()
        regular.put(BOOT_HOLD, b'held\n')
        before = self.protected_bytes(regular)
        for args in ((), ('--apply',)):
            with self.subTest(kind='regular', args=args):
                code, out, err = regular.run(*args)
                self.assertEqual((code, err), (0, ''), err)
                receipt = json.loads(out)
                self.assertFalse(receipt['applied'])
                self.assertEqual(receipt['status'], MODULE.HELD_STATUS)
                self.assertEqual(regular.boot, before[MODULE.BOOT])
                self.assertIsNone(regular.previous)
                self.assertEqual((regular.root / BOOT_HOLD).read_bytes(), b'held\n')
                self.assertEqual(self.protected_bytes(regular), before)

        for kind in ('directory', 'fifo', 'symlink'):
            machine = fresh()
            hold = machine.root / BOOT_HOLD
            hold.parent.mkdir(parents=True, exist_ok=True)
            if kind == 'directory':
                hold.mkdir()
            elif kind == 'fifo':
                os.mkfifo(hold)
            else:
                target = hold.parent / 'hold-target'
                target.write_bytes(b'held\n')
                hold.symlink_to(target.name)
            before = self.protected_bytes(machine)
            for args in ((), ('--apply',)):
                with self.subTest(kind=kind, args=args):
                    code, out, err = machine.run(*args)
                    self.assertEqual(code, 1, out + err)
                    self.assertEqual(out, '')
                    self.assertNotIn('Traceback', err)
                    if kind == 'symlink':
                        self.assertIn('symlink', err)
                    else:
                        self.assertIn('not a regular file', err)
                    self.assertNotIn('takes effect at next boot', err)
                    self.assertEqual(machine.boot, before[MODULE.BOOT])
                    self.assertIsNone(machine.previous)
                    self.assertEqual(self.protected_bytes(machine), before)
                    self.assertFalse((machine.root / BOOT_JOURNAL).exists())
                    self.assertFalse((machine.root / MODULE.PACMAN_LOCK).exists())

    def test_copy_back_refuses_a_non_string_video_pin_before_writes(self):
        """Copy-back must refuse a list or object video pin without a traceback.

        ``boot.bin`` already equals ``boot.bin.previous``. Inspection and apply
        both stop before the journal, the hold, and every protected file change.
        """
        image = boot(kernel_bytes=K1, dtb=D1)
        for pin in ([], {}):
            machine = Machine(self, boot_bytes=image, package_kernel=K1, package_dtb=D1)
            machine.put(MODULE.BOOT + MODULE.PREVIOUS, image)
            raw = (json.dumps({'boot_kernel_sha256': pin, 'kernel_release': RELEASE,
                               'owner_panel': 'kept'}) + '\n').encode()
            machine.put(MODULE.VIDEO_MANIFEST, raw)
            before = self.protected_bytes(machine)
            self.assertEqual(before[MODULE.BOOT], image)
            self.assertEqual(before[MODULE.BOOT + MODULE.PREVIOUS], image)
            self.assertIsNone(before[BOOT_JOURNAL])
            self.assertIsNone(before[BOOT_HOLD])
            for args in (('--rollback',), ('--rollback', '--apply')):
                with self.subTest(pin=type(pin).__name__, args=args):
                    code, out, err = machine.run(*args)
                    self.assertEqual(code, 1, out + err)
                    self.assertEqual(out, '')
                    self.assertNotIn('Traceback', err)
                    self.assertIn('not qualified', err)
                    self.assertEqual(self.protected_bytes(machine), before)
                    self.assertEqual((machine.root / MODULE.VIDEO_MANIFEST).read_bytes(), raw)
                    self.assertFalse((machine.root / BOOT_JOURNAL).exists())
                    self.assertFalse((machine.root / BOOT_HOLD).exists())

    def test_inspection_refuses_pending_boot_on_neither_snapshot(self):
        """Read-only inspection refuses a boot that is neither pending snapshot.

        Apply already rejects that boot when it commits. These calls do not
        apply: a pending apply is inspected with no arguments, and a pending
        rollback with ``--rollback`` only. Neither invocation may write.
        """
        packaged = kernel(12)
        desired = boot(kernel_bytes=packaged, dtb=D1, loader=LOADER + b' notch')
        neither = boot(kernel_bytes=K1, dtb=D2, loader=LOADER + b' notch')

        def planted(operation):
            machine = self.transition(package_kernel=packaged)
            self.assertNotIn(neither, (machine.boot_before, desired))
            if operation == 'apply':
                plant_journal(machine, 'apply', machine.boot_before, desired, sha(packaged))
            else:
                plant_journal(machine, 'rollback', desired, machine.boot_before, sha(K1))
            machine.put(MODULE.BOOT, neither)
            machine.put(MODULE.BOOT + MODULE.PREVIOUS, b'decoy previous')
            loaded = MODULE.load_journal(machine.root)
            self.assertEqual(loaded['operation'], operation)
            self.assertNotIn(machine.boot, (loaded['from_bytes'], loaded['to_bytes']))
            return machine

        for operation, args in (('apply', ()), ('rollback', ('--rollback',))):
            with self.subTest(operation=operation):
                machine = planted(operation)
                before = self.protected_bytes(machine)
                code, out, err = machine.run(*args)
                self.assertEqual(code, 1, out + err)
                self.assertEqual(out, '')
                self.assertNotIn('Traceback', err)
                self.assertIn('neither side', err)
                assert_unresolved_recovery(self, err)
                self.assertEqual(self.protected_bytes(machine), before)
                self.assertFalse((machine.root / MODULE.PACMAN_LOCK).exists())


def video_with_pin(raw, pin):
    """Parsed manifest with only ``boot_kernel_sha256`` replaced."""
    document = json.loads(raw)
    document['boot_kernel_sha256'] = pin
    return document


if __name__ == '__main__':
    unittest.main()
