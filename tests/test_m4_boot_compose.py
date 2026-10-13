# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""boot.bin composer: splice only kernel and devicetree, refuse everything unknown."""
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


def load(text=None):
    if text is None:
        spec = importlib.util.spec_from_file_location('m4_boot_compose', SOURCE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    module = types.ModuleType('m4_boot_compose_mutant')
    exec(compile(text, str(SOURCE), 'exec'), module.__dict__)
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

    def test_shipped_lineage(self):
        channel = json.loads((ROOT / 'packaging/omarchy-m4/channel.json').read_text())
        updater = load_tool('tools/m4-j713-update-boot-dtb.py')
        self.assertEqual(MODULE.KNOWN_KERNELS, {channel['kernel']['image_sha256']: channel['kernel']['release']})
        self.assertEqual(list(MODULE.KNOWN_DTBS),
                         [updater.ALPHA1_DTB, updater.POWEROFF_DTB, updater.CPUFREQ_DTB])
        self.assertEqual(channel['devicetree']['sha256'], updater.POWEROFF_DTB)


def load_tool(name):
    spec = importlib.util.spec_from_file_location(Path(name).stem.replace('-', '_'), ROOT / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Each mutant removes one guard; at least one scenario must catch it.
MUTANTS = (
    ('protected-stack', '    check_protected(root, new[\'kernel\'])\n', ''),
    # A composer that drops the owner's firmware must be stopped by the verification.
    ('drop-firmware', "desired = join({**layout, 'kernel': kernel,",
     "desired = join({**layout, 'initramfs': layout['initramfs'][:18], 'kernel': kernel,"),
    ('known-current-kernel', "require(now['kernel'] in KNOWN_KERNELS", 'require(True'),
    ('known-current-dtb', "require(now['dtb'] in KNOWN_DTBS", 'require(True'),
    ('known-package-dtb', "require(new['dtb'] in KNOWN_DTBS", 'require(True'),
    ('free-space', "require(stat.f_bavail * stat.f_frsize >= need", 'require(True'),
    ('board', "    require(BOARD in compatible.split(b'\\0'), 'This is not a J713 MacBook Air')\n    boot_path = root / BOOT\n    current = read_regular(boot_path, 'boot.bin (is the EFI partition mounted at /boot/efi?)')\n    kernel",
     "    boot_path = root / BOOT\n    current = read_regular(boot_path, 'boot.bin (is the EFI partition mounted at /boot/efi?)')\n    kernel"),
    ('backup', '        write(previous, current)\n', '        pass\n'),
    ('splice-dtb', "'dtb': dtb})", "'dtb': layout['dtb']})"),
    ('revert-newer-dtb', "if lineage.index(now['dtb']) > lineage.index(new['dtb']):", 'if False:'),
    ('rollback-owner', "require(all(now[name] == then[name] for name in ('loader', 'bootargs', 'initramfs')),",
     'require(True,'),
)


class MutationTests(unittest.TestCase):
    def test_every_guard_is_covered(self):
        text = SOURCE.read_text()
        for name, before, after in MUTANTS:
            with self.subTest(name):
                self.assertEqual(text.count(before), 1, 'mutation anchor changed: ' + name)
                mutant = load(text.replace(before, after))
                caught = []
                for scenario in SCENARIOS:
                    try:
                        scenario(self, mutant)
                    except Exception:  # any failure means the scenario noticed
                        caught.append(scenario.__name__)
                self.assertTrue(caught, 'mutant survived: ' + name)


if __name__ == '__main__':
    unittest.main()
