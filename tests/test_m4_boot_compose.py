# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""boot.bin composer: splice only kernel and devicetree, refuse everything unknown.

Kernel bytes move only for a pair listed in ``KERNEL_TRANSITIONS`` whose two
hashes share one entry in ``KNOWN_KERNELS``. A devicetree-only update does not
consult that set. Applying a pair replaces the boot kernel and the video
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
NEW_KERNEL = 'a7a160b6686652d7ae93218593d015febf9cc7ab0ef838148e551116befeb05e'
TRANSITION_RELEASE = '7.1.9-m4-gpu-backport-20261008'
BOOT_JOURNAL = 'var/lib/omarchy-m4/boot-compose/journal'
BOOT_SNAPSHOTS = 'var/lib/omarchy-m4/boot-compose/snapshots'


def scenario_newer_devicetree_kept(test, module):
    machine = Machine(test, module, boot_bytes=boot(dtb=D4), package_kernel=K2)
    patcher = mock.patch.dict(module.KNOWN_DTBS, {sha(D4): 'cpufreq'})
    patcher.start()
    test.addCleanup(patcher.stop)
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

    def handoff_machine(self, shown=False):
        native_tree = fdt(b'native backlight tree')
        old_loader = LOADER + (b' shown preference' if shown else b'')
        loaders = {mode: (mode.encode() + b' handoff stage2').ljust(0x110000, b'\0')
                   for mode in ('hidden', 'shown')}
        paths = {mode: f'usr/lib/omarchy-m4/backlight/stage2-{mode}.bin' for mode in loaders}
        for patcher in (
                mock.patch.object(MODULE, 'BACKLIGHT_DTB', sha(native_tree)),
                mock.patch.dict(MODULE.KNOWN_LOADERS,
                                {sha(old_loader): 'shown' if shown else 'hidden',
                                 **{sha(data): mode for mode, data in loaders.items()}}, clear=True),
                mock.patch.dict(MODULE.BACKLIGHT_LOADERS,
                                {mode: (paths[mode], sha(data)) for mode, data in loaders.items()}, clear=True)):
            patcher.start()
            self.addCleanup(patcher.stop)
        machine = Machine(self, boot_bytes=boot(loader=old_loader), package_dtb=native_tree)
        patcher = mock.patch.dict(MODULE.KNOWN_DTBS, {sha(native_tree): 'native backlight'})
        patcher.start()
        self.addCleanup(patcher.stop)
        for mode, data in loaders.items():
            machine.put(paths[mode], data)
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

    def test_rollback_of_an_unlisted_kernel_pair_refuses_before_writes(self):
        """Previous may change the kernel only for an allowlisted pair.

        Owner segments match and both hashes are known on one release. The
        pair is not in ``KERNEL_TRANSITIONS``.
        """
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
        code, _, err = machine.run('--rollback', '--apply')
        self.assertEqual(code, 1, err)
        self.assertIn('not qualified', err)
        self.assertEqual(machine.boot, current)
        self.assertEqual(machine.previous, older)
        self.assertEqual((machine.root / MODULE.VIDEO_MANIFEST).read_bytes(), raw)
        self.assertEqual((machine.root / MODULE.AUDIO_MANIFEST).read_bytes(), audio)
        self.assertFalse((machine.root / BOOT_JOURNAL).exists())


def video_with_pin(raw, pin):
    """Parsed manifest with only ``boot_kernel_sha256`` replaced."""
    document = json.loads(raw)
    document['boot_kernel_sha256'] = pin
    return document


if __name__ == '__main__':
    unittest.main()
