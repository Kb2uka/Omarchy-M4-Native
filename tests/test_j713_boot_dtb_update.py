"""In-place boot.bin devicetree update guards, on synthetic boot images."""
import hashlib
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'tools/m4-j713-update-boot-dtb.py'
SPEC = importlib.util.spec_from_file_location('boot_dtb_update', SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def fdt(body):
    return struct.pack('>2I', 0xd00dfeed, 8 + 32 + len(body)) + bytes(32) + body


OLD = fdt(b'old poweroff tree')
NEW = fdt(b'new poweroff tree with cells')
CPUFREQ = fdt(b'poweroff tree with cpufreq clusters')
INITRAMFS = b'm1n1_initramfs' + struct.pack('<I', 6) + b'070701'


def image(*trees, tail=INITRAMFS):
    return b'm1n1 stage2\0chosen.bootargs=x\n\x1f\x8bkernel' + b''.join(trees) + tail


class UpdateBootDtb(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.boot = self.root / 'boot.bin'
        self.dtb = self.root / 'new.dtb'
        self.dtb.write_bytes(NEW)
        old, new, cpufreq = (hashlib.sha256(d).hexdigest() for d in (OLD, NEW, CPUFREQ))
        patcher = patch.dict(MODULE.UPDATES, {new: ((old,), '.pre-shutdown-flag'),
                                              cpufreq: ((old, new), '.pre-cpufreq')}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_update(self, boot, apply=True):
        self.boot.write_bytes(boot)
        return MODULE.update(self.boot, self.dtb, apply)

    def test_inspection_changes_nothing(self):
        receipt = self.run_update(image(OLD), apply=False)
        self.assertEqual(receipt['status'], 'update available')
        self.assertEqual(self.boot.read_bytes(), image(OLD))
        self.assertFalse(Path(receipt['backup']).exists())

    def test_apply_swaps_only_the_devicetree_and_keeps_backup(self):
        receipt = self.run_update(image(OLD))
        self.assertTrue(receipt['applied'])
        self.assertEqual(self.boot.read_bytes(), image(NEW))
        self.assertEqual(Path(receipt['backup']).read_bytes(), image(OLD))
        self.assertFalse((self.root / 'boot.bin.new').exists())

    def test_second_run_is_a_no_op(self):
        self.run_update(image(OLD))
        receipt = MODULE.update(self.boot, self.dtb, True)
        self.assertEqual(receipt['status'], 'already updated')
        self.assertEqual(self.boot.read_bytes(), image(NEW))

    def test_unknown_devicetree_refused(self):
        with self.assertRaisesRegex(RuntimeError, 'exactly one'):
            self.run_update(image(fdt(b'someone else tree')))
        self.assertEqual(self.boot.read_bytes(), image(fdt(b'someone else tree')))

    def test_duplicate_devicetree_refused(self):
        with self.assertRaisesRegex(RuntimeError, 'exactly one'):
            self.run_update(image(OLD, OLD))

    def test_unexpected_layout_refused(self):
        with self.assertRaisesRegex(RuntimeError, 'Unexpected payload'):
            self.run_update(image(OLD, tail=b'\0\0\0\0'))
        self.assertEqual(self.boot.read_bytes(), image(OLD, tail=b'\0\0\0\0'))

    def test_wrong_replacement_refused(self):
        self.dtb.write_bytes(fdt(b'unreviewed tree'))
        with self.assertRaisesRegex(RuntimeError, 'Unknown replacement'):
            self.run_update(image(OLD))
        self.assertEqual(self.boot.read_bytes(), image(OLD))

    def test_conflicting_backup_refused(self):
        (self.root / 'boot.bin.pre-shutdown-flag').write_bytes(b'other machine')
        with self.assertRaisesRegex(RuntimeError, 'different backup'):
            self.run_update(image(OLD))
        self.assertEqual(self.boot.read_bytes(), image(OLD))


    def test_cpufreq_update_follows_update_1_with_its_own_backup(self):
        self.run_update(image(OLD))
        self.dtb.write_bytes(CPUFREQ)
        receipt = MODULE.update(self.boot, self.dtb, True)
        self.assertEqual(receipt['status'], 'updated; takes effect at next boot')
        self.assertEqual(self.boot.read_bytes(), image(CPUFREQ))
        self.assertEqual((self.root / 'boot.bin.pre-cpufreq').read_bytes(), image(NEW))
        self.assertEqual((self.root / 'boot.bin.pre-shutdown-flag').read_bytes(), image(OLD))
        self.assertIn('boot.bin.pre-cpufreq', receipt['rollback'])

    def test_cpufreq_update_applies_directly_to_alpha_1(self):
        self.dtb.write_bytes(CPUFREQ)
        receipt = self.run_update(image(OLD))
        self.assertTrue(receipt['applied'])
        self.assertEqual(self.boot.read_bytes(), image(CPUFREQ))
        self.assertEqual((self.root / 'boot.bin.pre-cpufreq').read_bytes(), image(OLD))

    def test_cpufreq_update_is_idempotent(self):
        self.dtb.write_bytes(CPUFREQ)
        self.run_update(image(NEW))
        self.assertEqual(MODULE.update(self.boot, self.dtb, True)['status'], 'already updated')

    def test_update_1_does_not_downgrade_cpufreq(self):
        with self.assertRaisesRegex(RuntimeError, 'exactly one'):
            self.run_update(image(CPUFREQ))
        self.assertEqual(self.boot.read_bytes(), image(CPUFREQ))
        self.assertFalse((self.root / 'boot.bin.pre-shutdown-flag').exists())

    def test_shipped_updates_chain_from_alpha_1(self):
        updates = SOURCE.read_text()
        self.assertIn("CPUFREQ_DTB: ((ALPHA1_DTB, POWEROFF_DTB), '.pre-cpufreq')", updates)
        self.assertIn("POWEROFF_DTB: ((ALPHA1_DTB,), '.pre-shutdown-flag')", updates)


if __name__ == '__main__':
    unittest.main()
