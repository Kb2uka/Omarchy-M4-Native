# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""The boot-time speaker module swap: only known states, six pins, always consistent."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('m4_speaker_module', ROOT / 'tools/m4-speaker-module.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

OLD, NEW = b'alpha.1 speaker module', b'fixed speaker module'
STOCK = {'sha256': hashlib.sha256(OLD).hexdigest(), 'size': len(OLD), 'note': 'old-note'}
FIXED = {'sha256': hashlib.sha256(NEW).hexdigest(), 'size': len(NEW), 'note': 'new-note'}


def manifest(identity):
    path = MODULE.MODULE
    return {
        'kernel_release': MODULE.RELEASE,
        'files': {'/' + path: identity['sha256'], '/usr/lib/speakersafetyd-j713': 'daemon'},
        'file_metadata': {path: {'sha256': identity['sha256'], 'size': identity['size'], 'mode': 420},
                          'usr/lib/speakersafetyd-j713': {'sha256': 'daemon', 'size': 9, 'mode': 493}},
        'module_artifacts': {MODULE.NAME: {'module': MODULE.NAME, 'note': identity['note'], 'path': '/' + path,
                                           'sha256': identity['sha256'], 'size': identity['size']},
                             'snd_soc_tas2764': {'note': 'amp', 'sha256': 'amp'}},
        'modules': {MODULE.NAME: identity['note'], 'snd_soc_tas2764': 'amp'},
        'readiness_inputs': {'unit': 'kept as installed'},
    }


class SpeakerModule(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for target, values in ((MODULE.STOCK, STOCK), (MODULE.FIXED, FIXED)):
            patcher = mock.patch.dict(target, values)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.module, self.manifest = self.root / MODULE.MODULE, self.root / MODULE.MANIFEST
        self.install(OLD, manifest(STOCK))
        self.put(MODULE.STAGED, NEW)

    def put(self, name, data, mode=0o644):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(mode)

    def install(self, module, document):
        self.put(MODULE.MODULE, module)
        self.put(MODULE.MANIFEST, MODULE.serialize(document))

    def run_tool(self, *args):
        output = io.StringIO()
        with mock.patch('sys.stdout', output), mock.patch('sys.stderr', io.StringIO()):
            code = MODULE.main(['--root', str(self.root), *args])
        return code, json.loads(output.getvalue()) if code == 0 else None

    def state(self):
        return self.module.read_bytes(), json.loads(self.manifest.read_bytes())

    def test_inspection_changes_nothing(self):
        before = self.state()
        code, report = self.run_tool()
        self.assertEqual((code, report['status']), (0, 'staged; applies at the next boot'))
        self.assertEqual(self.state(), before)

    def test_boot_applies_module_and_exactly_six_pins(self):
        code, report = self.run_tool('--boot')
        self.assertEqual(code, 0)
        self.assertIn('applied', report['status'])
        module, document = self.state()
        self.assertEqual(module, NEW)
        self.assertEqual(document, MODULE.with_fixed_pins(manifest(STOCK)))
        self.assertEqual(document, manifest(FIXED))
        self.assertEqual(self.manifest.read_bytes(), MODULE.serialize(manifest(FIXED)))
        self.assertEqual(self.module.stat().st_mode & 0o777, 0o644)
        self.assertEqual(self.manifest.stat().st_mode & 0o777, 0o644)
        stage = self.root / MODULE.STATE
        self.assertEqual((stage / 'alpha1-module.ko').read_bytes(), OLD)
        self.assertEqual(json.loads((stage / 'alpha1-manifest.json').read_bytes()), manifest(STOCK))
        self.assertEqual(self.run_tool('--boot')[1]['status'], 'current')

    def test_fixed_air_is_left_alone_even_in_another_format(self):
        self.put(MODULE.MODULE, NEW)
        self.manifest.write_text(json.dumps(manifest(FIXED)))  # hand-written, not the image format
        before = self.manifest.read_bytes()
        code, report = self.run_tool('--boot')
        self.assertEqual((code, report['status']), (0, 'current'))
        self.assertEqual(self.manifest.read_bytes(), before)

    def test_interrupted_swap_is_completed_before_modules_load(self):
        real = MODULE.write

        def fail_on_manifest(path, data, mode):
            if path == self.manifest:
                raise OSError('power cut')
            real(path, data, mode)
        with mock.patch.object(MODULE, 'write', side_effect=fail_on_manifest):
            self.assertEqual(self.run_tool('--boot')[0], 1)
        self.assertEqual(self.run_tool()[1]['state'], 'interrupted')
        self.assertEqual(self.run_tool('--boot')[0], 0)
        self.assertEqual(self.state(), (NEW, manifest(FIXED)))

    def test_loaded_module_is_never_swapped(self):
        (self.root / 'sys/module' / MODULE.NAME).mkdir(parents=True)
        before = self.state()
        self.assertEqual(self.run_tool('--boot')[0], 1)
        self.assertEqual(self.state(), before)

    def test_unknown_or_unsafe_states_change_nothing(self):
        cases = {
            'other module': lambda: self.put(MODULE.MODULE, b'someone else'),
            'one pin edited': lambda: self.install(OLD, {**manifest(STOCK), 'modules': {MODULE.NAME: 'x',
                                                                                         'snd_soc_tas2764': 'amp'}}),
            'not the image format': lambda: self.manifest.write_text(json.dumps(manifest(STOCK))),
            'staged module differs': lambda: self.put(MODULE.STAGED, b'tampered'),
            'other kernel': lambda: self.install(OLD, {**manifest(STOCK), 'kernel_release': 'other'}),
        }
        for label, change in cases.items():
            with self.subTest(label):
                self.setUp()
                change()
                before = (self.module.read_bytes(), self.manifest.read_bytes())
                self.assertEqual(self.run_tool('--boot')[0], 1)
                self.assertEqual((self.module.read_bytes(), self.manifest.read_bytes()), before)

    def test_rollback_restores_alpha1_and_holds(self):
        self.run_tool('--boot')
        code, report = self.run_tool('--rollback')
        self.assertIn('restart', report['status'])
        self.assertEqual(self.state()[0], NEW, 'rollback waits for the next boot')
        self.assertEqual(self.run_tool('--boot')[0], 0)
        self.assertEqual(self.state(), (OLD, manifest(STOCK)))
        self.assertEqual(self.run_tool('--boot')[1]['status'], 'held after a rollback; nothing applied')
        self.assertEqual(self.state()[0], OLD)

    def test_rollback_without_backup_refuses(self):
        self.install(NEW, manifest(FIXED))
        self.run_tool('--rollback')
        self.assertEqual(self.run_tool('--boot')[0], 1)
        self.assertEqual(self.state(), (NEW, manifest(FIXED)))

    def test_pins_match_the_qualified_spec(self):
        spec = json.loads((ROOT / 'audio/j713/qualified.json').read_text())
        artifact = spec['module_artifacts'][MODULE.NAME]
        reloaded = importlib.util.module_from_spec(SPEC)  # unpatched pins
        SPEC.loader.exec_module(reloaded)
        self.assertEqual((artifact['sha256'], artifact['size'], artifact['note']),
                         (reloaded.FIXED['sha256'], reloaded.FIXED['size'], reloaded.FIXED['note']))
        self.assertEqual('/' + reloaded.MODULE, artifact['path'])
        self.assertEqual(spec['kernel_release'], reloaded.RELEASE)


if __name__ == '__main__':
    unittest.main()
