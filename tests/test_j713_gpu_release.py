# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""The release image carries the fixed Air GPU module in the state m4-gpu-module calls fixed."""
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release = load('gpu_release', 'tools/prepare-j713-release.py')
tool = load('gpu_release_tool', 'tools/m4-gpu-module.py')

OLD, NEW = b'alpha.1 gpu module', b'fixed gpu module'
MESA = 'f9f3247a5b0b425c15f29cf0773092e95c691e347594cd0b32278f303dc2ab05  /opt/m4-mesa/lib/libEGL.so.1.0.0\n'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def sums(pin):
    # The retained runtime.sha256: the module line first, then the Mesa files.
    return f'{pin}  /opt/m4-gpu/asahi_m4.ko\n{MESA}'.encode()


class GpuImageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / 'root'
        self.module, self.sums = self.root / release.GPU_MODULE, self.root / release.GPU_SUMS
        self.put(release.GPU_MODULE, OLD, 0o644)
        self.put(release.GPU_SUMS, sums(digest(OLD)), 0o664)
        for target, value in (('GPU_ALPHA1', digest(OLD)), ('GPU_FIXED', digest(NEW))):
            patcher = mock.patch.object(release, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def put(self, name, data, mode):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(mode)

    def state(self):
        files = sorted(p for p in self.root.rglob('*') if p.is_file() or p.is_symlink())
        return {p.relative_to(self.root).as_posix(): (p.read_bytes(), p.lstat().st_mode & 0o777) for p in files}

    def test_swaps_the_module_and_only_its_pin_and_keeps_alpha1_for_rollback(self):
        release.prepare_gpu(self.root, NEW)
        self.assertEqual(self.state(), {
            release.GPU_MODULE: (NEW, 0o644),
            release.GPU_SUMS: (sums(digest(NEW)), 0o664),
            release.GPU_BACKUP + '/alpha1-module.ko': (OLD, 0o600),
            release.GPU_BACKUP + '/alpha1-runtime.sha256': (sums(digest(OLD)), 0o600),
        })
        self.assertEqual((self.root / release.GPU_BACKUP).stat().st_mode & 0o777, 0o755)

    def test_keeps_the_retained_modes_and_sets_the_state_directory_mode_under_any_umask(self):
        self.module.chmod(0o640)
        self.sums.chmod(0o660)
        previous = os.umask(0o077)
        try:
            release.prepare_gpu(self.root, NEW)
        finally:
            os.umask(previous)
        self.assertEqual(self.module.stat().st_mode & 0o777, 0o640)
        self.assertEqual(self.sums.stat().st_mode & 0o777, 0o660)
        self.assertEqual((self.root / release.GPU_BACKUP).stat().st_mode & 0o777, 0o755)

    def test_the_image_is_what_m4_gpu_module_calls_fixed_and_rollback_restores_alpha1(self):
        release.prepare_gpu(self.root, NEW)
        with mock.patch.dict(tool.STOCK, sha256=digest(OLD), size=len(OLD)), \
             mock.patch.dict(tool.FIXED, sha256=digest(NEW), size=len(NEW)), \
             mock.patch.object(tool, 'PREVIOUS', ()), mock.patch('sys.stdout', io.StringIO()):
            self.assertEqual(tool.run(self.root, 'inspect')['state'], 'fixed')
            self.assertEqual(tool.run(self.root, 'boot')['status'], 'current')
            tool.run(self.root, 'request-rollback')
            self.assertEqual(tool.run(self.root, 'boot')['status'], 'rolled back to the alpha.1 GPU module')
        self.assertEqual(self.module.read_bytes(), OLD)
        self.assertEqual(self.sums.read_bytes(), sums(digest(OLD)))

    def test_the_tool_and_the_assembler_name_the_same_files(self):
        self.assertEqual((release.GPU_MODULE, release.GPU_SUMS, release.GPU_BACKUP),
                         (tool.MODULE, tool.SUMS, tool.STATE))
        self.assertEqual((release.GPU_ALPHA1, release.GPU_FIXED), (digest(OLD), digest(NEW)))
        unpatched = load('gpu_release_pins', 'tools/prepare-j713-release.py')
        self.assertEqual((unpatched.GPU_ALPHA1, unpatched.GPU_FIXED), (tool.STOCK['sha256'], tool.FIXED['sha256']))
        self.assertEqual(unpatched.GPU_FIXED_BYTES, tool.FIXED['size'])

    def test_unknown_retained_files_refuse_before_any_write(self):
        cases = {
            'not alpha.1': (lambda: self.put(release.GPU_MODULE, b'hand-patched module', 0o644),
                            'Unknown retained GPU module'),
            'already fixed': (lambda: self.put(release.GPU_MODULE, NEW, 0o644), 'Unknown retained GPU module'),
            'pin differs': (lambda: self.put(release.GPU_SUMS, sums(digest(NEW)), 0o664),
                            'Retained GPU pins disagree'),
            'pinned twice': (lambda: self.put(release.GPU_SUMS, sums(digest(OLD)) * 2, 0o664),
                             'Retained GPU pins disagree'),
            'not pinned': (lambda: self.put(release.GPU_SUMS, MESA.encode(), 0o664), 'Retained GPU pins disagree'),
            'pins missing': (lambda: self.sums.unlink(), 'Missing retained GPU pins'),
            'state present': (lambda: self.put(release.GPU_BACKUP + '/hold', b'', 0o644),
                              'GPU module state already present'),
        }
        for label, (change, message) in cases.items():
            with self.subTest(label):
                self.setUp()
                change()
                before = self.state()
                with self.assertRaisesRegex(RuntimeError, message):
                    release.prepare_gpu(self.root, NEW)
                self.assertEqual(self.state(), before)

    def test_a_wrong_size_input_refuses_without_being_read(self):
        path = self.root / 'other.ko'
        path.write_bytes(NEW + b'\x00')
        with mock.patch.object(release, 'GPU_FIXED_BYTES', len(NEW)), \
             mock.patch.object(Path, 'read_bytes', side_effect=AssertionError('read before the size check')):
            with self.assertRaisesRegex(RuntimeError, 'Unknown GPU module input'):
                release.gpu_module_input(path)
        self.put('fixed.ko', NEW, 0o644)
        with mock.patch.object(release, 'GPU_FIXED_BYTES', len(NEW)):
            self.assertEqual(release.gpu_module_input(self.root / 'fixed.ko'), NEW)

    def test_a_linked_module_refuses(self):
        real = self.root / 'elsewhere.ko'
        real.write_bytes(OLD)
        self.module.unlink()
        self.module.symlink_to(real)
        with self.assertRaisesRegex(ValueError, 'Symlink in image'):
            release.prepare_gpu(self.root, NEW)
        self.assertEqual(real.read_bytes(), OLD)


if __name__ == '__main__':
    unittest.main()
