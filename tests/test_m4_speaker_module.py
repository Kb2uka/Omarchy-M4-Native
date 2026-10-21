# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""The boot-time speaker module swap: only known states, six pins, always consistent.

MCA is a second component of the same tool, selected with ``--component mca``.
The default remains the speaker. Fixture bytes below are synthetic and are
patched onto ``STOCK``/``FIXED`` and ``MCA_STOCK``/``MCA_FIXED``; the real
alpha.1 and fixed pins are asserted only against an unpatched import.

Per component the tool keeps its own installed path, staged path, and state
directory (speaker: ``var/lib/omarchy-m4/speaker-module``; MCA:
``var/lib/omarchy-m4/mca-module``). Each directory has its own ``hold`` and
``rollback`` markers and its own module backup. The shared audio manifest is
updated only in that component's six pins. Unrelated keys, the other
component's pins, and a hand-written manifest's other bytes stay as they were.
A forward power cut is new module bytes with the old pins, and the next boot
finishes it. The reverse (old module bytes, new pins) is resumed only when
this component's rollback marker and a valid backup are present.
"""
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
# Same length so a pin-only manifest edit can keep every unrelated byte.
MCA_OLD, MCA_NEW = b'mca-stock-module-bytes', b'mca-fixed-module-bytes'
MCA_OLD_NOTE, MCA_NEW_NOTE = 'mca-old-note!!', 'mca-new-note!!'
MCA_NAME = 'snd_soc_apple_mca'
MCA_MODULE = 'usr/lib/omarchy/j713-audio/7.1.9-m4-gpu-backport-20261008/modules/snd-soc-apple-mca.ko'
MCA_STAGED = 'usr/lib/omarchy-m4/j713-audio/snd-soc-apple-mca.ko'
MCA_STATE = 'var/lib/omarchy-m4/mca-module'
MCA_STOCK_PIN = {
    'sha256': 'd3194660a28d30ba8ea296b6592836ad4ca9426d9471d05ba8ce3cad284baa1e',
    'size': 79152,
    'note': '040000001400000003000000474e550057b999a68c50407955e8b2ed92f242b0cfe426e9',
}
MCA_FIXED_PIN = {
    'sha256': 'fb941df4d55c4f2093b4595e0392aaf97ffe3a250228518b18a4f70568bf7454',
    'size': 79152,
    'note': '040000001400000003000000474e55000a48e413fcbb318d62c1161c59fb2bef1de7df81',
}


def identity(blob, note):
    return {'sha256': hashlib.sha256(blob).hexdigest(), 'size': len(blob), 'note': note}


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


def with_mca(document, record):
    """Add the six MCA pins. ``record`` is a sha256/size/note identity."""
    updated = json.loads(json.dumps(document))
    updated['files']['/' + MCA_MODULE] = record['sha256']
    updated['file_metadata'][MCA_MODULE] = {'sha256': record['sha256'], 'size': record['size'], 'mode': 420}
    updated['module_artifacts'][MCA_NAME] = {
        'module': MCA_NAME, 'note': record['note'], 'path': '/' + MCA_MODULE,
        'sha256': record['sha256'], 'size': record['size']}
    updated['modules'][MCA_NAME] = record['note']
    return updated


def mask_pins(raw, *records):
    """Hide one component's pin strings so the other manifest bytes can be compared."""
    masked = raw
    for record in records:
        for value in (record['sha256'], record['note']):
            masked = masked.replace(value.encode(), b'X' * len(value))
    return masked


class SpeakerModule(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for target, values in ((MODULE.STOCK, STOCK), (MODULE.FIXED, FIXED)):
            patcher = mock.patch.dict(target, values)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name, values in (('MCA_STOCK', identity(MCA_OLD, MCA_OLD_NOTE)),
                             ('MCA_FIXED', identity(MCA_NEW, MCA_NEW_NOTE))):
            patcher = mock.patch.object(MODULE, name, values, create=True)
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
        output, error = io.StringIO(), io.StringIO()
        with mock.patch('sys.stdout', output), mock.patch('sys.stderr', error):
            try:
                code = MODULE.main(['--root', str(self.root), *args])
            except SystemExit as exited:
                code = exited.code if isinstance(exited.code, int) else 1
        self.last_error = error.getvalue()
        text = output.getvalue()
        return code, json.loads(text) if code == 0 and text.strip() else None

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
            'compact json with a wrong pin': lambda: self.manifest.write_text(json.dumps(
                {**manifest(STOCK), 'modules': {MODULE.NAME: 'x', 'snd_soc_tas2764': 'amp'}})),
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

    def plant(self, speaker_record, mca_record, speaker_bytes, mca_bytes, extra=None):
        document = with_mca(manifest(speaker_record), mca_record)
        if extra:
            document.update(extra)
        self.install(speaker_bytes, document)
        self.put(MCA_MODULE, mca_bytes)
        self.put(MCA_STAGED, MCA_NEW)
        return document

    def test_mca_updates_from_stock_or_fixed_speaker_without_touching_it(self):
        cases = {'stock speaker': (OLD, STOCK), 'fixed speaker': (NEW, FIXED)}
        for label, (speaker_bytes, speaker_record) in cases.items():
            with self.subTest(label):
                self.setUp()
                extra = {'owner_calibration': {'gain': 3}}
                self.plant(speaker_record, identity(MCA_OLD, MCA_OLD_NOTE), speaker_bytes, MCA_OLD, extra)
                before = self.manifest.read_bytes()
                code, report = self.run_tool('--boot', '--component', 'mca')
                self.assertEqual(code, 0, self.last_error)
                self.assertIn('applied', report['status'])
                self.assertEqual((self.root / MODULE.MODULE).read_bytes(), speaker_bytes)
                self.assertEqual((self.root / MCA_MODULE).read_bytes(), MCA_NEW)
                raw = self.manifest.read_bytes()
                restored = json.loads(raw)
                self.assertEqual(restored['modules'][MODULE.NAME], speaker_record['note'])
                self.assertEqual(restored['modules'][MCA_NAME], MCA_NEW_NOTE)
                self.assertEqual(restored['owner_calibration'], {'gain': 3})
                self.assertEqual(mask_pins(raw, identity(MCA_OLD, MCA_OLD_NOTE), identity(MCA_NEW, MCA_NEW_NOTE)),
                                 mask_pins(before, identity(MCA_OLD, MCA_OLD_NOTE), identity(MCA_NEW, MCA_NEW_NOTE)))
                self.assertFalse((self.root / MODULE.STATE / 'rollback').exists())
                self.assertFalse((self.root / MCA_STATE / 'hold').exists())
                again = self.manifest.read_bytes()
                self.assertEqual(self.run_tool('--boot', '--component', 'mca')[1]['status'], 'current')
                self.assertEqual(self.manifest.read_bytes(), again)

    def test_speaker_rollback_keeps_later_mca_pins_and_custom_values(self):
        # The speaker backup is taken at apply time. A later MCA edit and an
        # owner value written after that backup must survive speaker rollback.
        document = with_mca(manifest(STOCK), identity(MCA_OLD, MCA_OLD_NOTE))
        document['owner_calibration'] = {'gain': 3}
        self.install(OLD, document)
        self.put(MCA_MODULE, MCA_OLD)
        self.assertEqual(self.run_tool('--boot')[0], 0)
        live = json.loads(self.manifest.read_bytes())
        live['owner_calibration'] = {'gain': 9, 'label': 'bench'}
        live = with_mca(live, identity(MCA_NEW, MCA_NEW_NOTE))
        self.manifest.write_bytes(MODULE.serialize(live))
        self.put(MCA_MODULE, MCA_NEW)
        self.assertEqual(self.run_tool('--rollback')[0], 0)
        self.assertEqual(self.run_tool('--boot')[0], 0)
        module, restored = self.state()
        self.assertEqual(module, OLD)
        self.assertEqual(restored['modules'][MODULE.NAME], STOCK['note'])
        self.assertEqual(restored['files']['/' + MODULE.MODULE], STOCK['sha256'])
        self.assertEqual(restored['owner_calibration'], {'gain': 9, 'label': 'bench'})
        self.assertEqual(restored['modules'][MCA_NAME], MCA_NEW_NOTE)
        self.assertEqual(restored['module_artifacts'][MCA_NAME]['sha256'], hashlib.sha256(MCA_NEW).hexdigest())
        self.assertEqual((self.root / MCA_MODULE).read_bytes(), MCA_NEW)
        self.assertTrue((self.root / MODULE.STATE / 'hold').exists())
        self.assertFalse((self.root / MCA_STATE / 'hold').exists())

    def test_mca_rollback_keeps_the_speaker_update_and_its_own_hold(self):
        self.plant(STOCK, identity(MCA_OLD, MCA_OLD_NOTE), OLD, MCA_OLD, {'owner_calibration': {'gain': 4}})
        self.assertEqual(self.run_tool('--boot')[0], 0)
        self.assertEqual(self.run_tool('--boot', '--component', 'mca')[0], 0)
        self.assertEqual(self.run_tool('--rollback', '--component', 'mca')[0], 0)
        self.assertFalse((self.root / MODULE.STATE / 'rollback').exists())
        self.assertEqual(self.run_tool('--boot', '--component', 'mca')[0], 0)
        module, restored = self.state()
        self.assertEqual(module, NEW)
        self.assertEqual(restored['modules'][MODULE.NAME], FIXED['note'])
        self.assertEqual(restored['modules'][MCA_NAME], MCA_OLD_NOTE)
        self.assertEqual(restored['owner_calibration'], {'gain': 4})
        self.assertEqual((self.root / MCA_MODULE).read_bytes(), MCA_OLD)
        self.assertTrue((self.root / MCA_STATE / 'hold').exists())
        self.assertFalse((self.root / MODULE.STATE / 'hold').exists())
        self.assertEqual(self.run_tool('--boot')[1]['status'], 'current')

    def test_loaded_component_refuses_only_its_own_write(self):
        self.plant(STOCK, identity(MCA_OLD, MCA_OLD_NOTE), OLD, MCA_OLD)
        (self.root / 'sys/module' / MODULE.NAME).mkdir(parents=True)
        before = self.manifest.read_bytes()
        self.assertNotEqual(self.run_tool('--boot')[0], 0)
        self.assertEqual(self.manifest.read_bytes(), before)
        code, report = self.run_tool('--boot', '--component', 'mca')
        self.assertEqual(code, 0, self.last_error)
        self.assertEqual((self.root / MCA_MODULE).read_bytes(), MCA_NEW)
        self.assertEqual((self.root / MODULE.MODULE).read_bytes(), OLD)
        self.assertIn('applied', report['status'])

        self.setUp()
        self.plant(STOCK, identity(MCA_OLD, MCA_OLD_NOTE), OLD, MCA_OLD)
        (self.root / 'sys/module' / MCA_NAME).mkdir(parents=True)
        before = (self.manifest.read_bytes(), (self.root / MCA_MODULE).read_bytes())
        code, _ = self.run_tool('--boot', '--component', 'mca')
        self.assertEqual(code, 1)
        self.assertIn(MCA_NAME, self.last_error)
        self.assertNotIn('unrecognized arguments', self.last_error)
        self.assertEqual((self.manifest.read_bytes(), (self.root / MCA_MODULE).read_bytes()), before)
        self.assertEqual(self.run_tool('--boot')[0], 0)
        self.assertEqual((self.root / MODULE.MODULE).read_bytes(), NEW)

    def test_mca_refuses_one_wrong_pin_or_wrong_staged_or_backup_bytes(self):
        base = with_mca(manifest(STOCK), identity(MCA_OLD, MCA_OLD_NOTE))
        mutations = {
            'files': lambda document: document['files'].__setitem__('/' + MCA_MODULE, 'f' * 64),
            'metadata sha': lambda document: document['file_metadata'][MCA_MODULE].__setitem__('sha256', 'e' * 64),
            'metadata size': lambda document: document['file_metadata'][MCA_MODULE].__setitem__('size', 1),
            'artifact sha': lambda document: document['module_artifacts'][MCA_NAME].__setitem__('sha256', 'd' * 64),
            'artifact size': lambda document: document['module_artifacts'][MCA_NAME].__setitem__('size', 1),
            'artifact note': lambda document: document['module_artifacts'][MCA_NAME].__setitem__('note', 'other-note!!!'),
            'modules': lambda document: document['modules'].__setitem__(MCA_NAME, 'other-note!!!'),
        }
        for label, change in mutations.items():
            with self.subTest(label):
                self.setUp()
                document = json.loads(json.dumps(base))
                change(document)
                self.install(OLD, document)
                self.put(MCA_MODULE, MCA_OLD)
                self.put(MCA_STAGED, MCA_NEW)
                before = (self.manifest.read_bytes(), (self.root / MCA_MODULE).read_bytes(),
                          (self.root / MODULE.MODULE).read_bytes())
                code, _ = self.run_tool('--boot', '--component', 'mca')
                self.assertEqual(code, 1)
                self.assertNotIn('unrecognized arguments', self.last_error)
                self.assertEqual((self.manifest.read_bytes(), (self.root / MCA_MODULE).read_bytes(),
                                  (self.root / MODULE.MODULE).read_bytes()), before)

        self.setUp()
        self.plant(STOCK, identity(MCA_OLD, MCA_OLD_NOTE), OLD, MCA_OLD)
        self.put(MCA_STAGED, b'tampered-mca-module')
        before = (self.root / MCA_MODULE).read_bytes()
        self.assertEqual(self.run_tool('--boot', '--component', 'mca')[0], 1)
        self.assertNotIn('unrecognized arguments', self.last_error)
        self.assertEqual((self.root / MCA_MODULE).read_bytes(), before)

        self.setUp()
        self.plant(FIXED, identity(MCA_NEW, MCA_NEW_NOTE), NEW, MCA_NEW)
        stage = self.root / MCA_STATE
        stage.mkdir(parents=True)
        (stage / 'rollback').touch()
        self.put(MCA_STATE + '/alpha1-module.ko', b'not-the-stock-mca')
        self.put(MCA_STATE + '/alpha1-manifest.json', MODULE.serialize(
            with_mca(manifest(FIXED), identity(MCA_OLD, MCA_OLD_NOTE))))
        before = (self.root / MCA_MODULE).read_bytes()
        self.assertEqual(self.run_tool('--boot', '--component', 'mca')[0], 1)
        self.assertNotIn('unrecognized arguments', self.last_error)
        self.assertEqual((self.root / MCA_MODULE).read_bytes(), before)

    def test_mca_forward_interruption_completes_and_reverse_needs_rollback(self):
        self.plant(FIXED, identity(MCA_OLD, MCA_OLD_NOTE), NEW, MCA_OLD, {'owner_calibration': {'gain': 2}})
        real = MODULE.write

        def fail_on_manifest(path, data, mode):
            if path == self.manifest and (self.root / MCA_MODULE).read_bytes() == MCA_NEW:
                raise OSError('power cut')
            real(path, data, mode)

        with mock.patch.object(MODULE, 'write', side_effect=fail_on_manifest):
            self.assertEqual(self.run_tool('--boot', '--component', 'mca')[0], 1)
        self.assertEqual((self.root / MCA_MODULE).read_bytes(), MCA_NEW)
        self.assertEqual(json.loads(self.manifest.read_bytes())['modules'][MCA_NAME], MCA_OLD_NOTE)
        code, report = self.run_tool('--component', 'mca')
        self.assertEqual(code, 0, self.last_error)
        self.assertEqual(report['state'], 'interrupted')
        self.assertEqual(self.run_tool('--boot', '--component', 'mca')[0], 0)
        restored = json.loads(self.manifest.read_bytes())
        self.assertEqual(restored['modules'][MCA_NAME], MCA_NEW_NOTE)
        self.assertEqual(restored['modules'][MODULE.NAME], FIXED['note'])
        self.assertEqual(restored['owner_calibration'], {'gain': 2})

        self.setUp()
        document = with_mca(manifest(FIXED), identity(MCA_NEW, MCA_NEW_NOTE))
        document['owner_calibration'] = {'gain': 8}
        self.install(NEW, document)
        self.put(MCA_MODULE, MCA_OLD)  # old bytes, new pins: interrupted rollback
        backup = with_mca(manifest(FIXED), identity(MCA_OLD, MCA_OLD_NOTE))
        backup['owner_calibration'] = {'gain': 1}
        stage = self.root / MCA_STATE
        stage.mkdir(parents=True)
        self.put(MCA_STATE + '/alpha1-module.ko', MCA_OLD)
        self.put(MCA_STATE + '/alpha1-manifest.json', MODULE.serialize(backup))
        before = self.manifest.read_bytes()
        self.assertEqual(self.run_tool('--boot', '--component', 'mca')[0], 1)
        self.assertEqual(self.manifest.read_bytes(), before)
        self.assertEqual((self.root / MCA_MODULE).read_bytes(), MCA_OLD)
        (stage / 'rollback').touch()
        self.assertEqual(self.run_tool('--boot', '--component', 'mca')[0], 0)
        finished = json.loads(self.manifest.read_bytes())
        self.assertEqual(finished['modules'][MCA_NAME], MCA_OLD_NOTE)
        self.assertEqual(finished['owner_calibration'], {'gain': 8})
        self.assertEqual(finished['modules'][MODULE.NAME], FIXED['note'])
        self.assertTrue((stage / 'hold').exists())

    def test_fixed_mca_custom_manifest_is_not_rewritten(self):
        document = with_mca(manifest(FIXED), identity(MCA_NEW, MCA_NEW_NOTE))
        document['owner_calibration'] = {'gain': 5}
        self.put(MODULE.MODULE, NEW)
        self.put(MCA_MODULE, MCA_NEW)
        self.manifest.write_text(json.dumps(document, indent=4))
        before = self.manifest.read_bytes()
        code, report = self.run_tool('--boot', '--component', 'mca')
        self.assertEqual(code, 0, self.last_error)
        self.assertEqual(report['status'], 'current')
        self.assertEqual(self.manifest.read_bytes(), before)
        self.assertEqual(self.run_tool('--boot')[1]['status'], 'current')
        self.assertEqual(self.manifest.read_bytes(), before)

    def test_reverse_speaker_interruption_resumes_only_with_rollback_intent(self):
        live = manifest(FIXED)
        live['owner_calibration'] = {'gain': 8}
        self.install(OLD, live)
        backup = manifest(STOCK)
        backup['owner_calibration'] = {'gain': 1}
        stage = self.root / MODULE.STATE
        stage.mkdir(parents=True)
        self.put(MODULE.STATE + '/alpha1-module.ko', OLD)
        self.put(MODULE.STATE + '/alpha1-manifest.json', MODULE.serialize(backup))
        before = self.manifest.read_bytes()
        self.assertEqual(self.run_tool('--boot')[0], 1)
        self.assertEqual(self.manifest.read_bytes(), before)
        (stage / 'rollback').touch()
        self.assertEqual(self.run_tool('--boot')[0], 0)
        module, restored = self.state()
        self.assertEqual(module, OLD)
        self.assertEqual(restored['modules'][MODULE.NAME], STOCK['note'])
        self.assertEqual(restored['owner_calibration'], {'gain': 8})

    def component(self, name):
        """Paths and bytes for one component. The other component stays stock."""
        if name == 'speaker':
            return {'name': 'speaker', 'args': (), 'module': MODULE.MODULE, 'staged': MODULE.STAGED,
                    'state': MODULE.STATE, 'stock_bytes': OLD, 'fixed_bytes': NEW, 'stock': STOCK, 'fixed': FIXED,
                    'other_module': MCA_MODULE, 'other_bytes': MCA_OLD}
        return {'name': 'mca', 'args': ('--component', 'mca'), 'module': MCA_MODULE, 'staged': MCA_STAGED,
                'state': MCA_STATE, 'stock_bytes': MCA_OLD, 'fixed_bytes': MCA_NEW,
                'stock': identity(MCA_OLD, MCA_OLD_NOTE), 'fixed': identity(MCA_NEW, MCA_NEW_NOTE),
                'other_module': MODULE.MODULE, 'other_bytes': OLD}

    def install_both(self, speaker_bytes, speaker_record, mca_bytes, mca_record, owner):
        document = with_mca(manifest(speaker_record), mca_record)
        document['owner_calibration'] = owner
        self.install(speaker_bytes, document)
        self.put(MCA_MODULE, mca_bytes)
        self.put(MCA_STAGED, MCA_NEW)
        self.put(MODULE.STAGED, NEW)
        return document

    def test_rollback_power_cut_before_clearing_intent_finishes_on_retry(self):
        """Stock bytes and pins with hold and the rollback marker still present.

        A power cut after the restored manifest write and before the marker is
        removed must be finished by the next --boot. The marker goes away, the
        hold stays, unrelated manifest fields stay, and the fixed module is
        not applied. One injected unlink failure, then one retry, for each
        component.
        """
        for name in ('speaker', 'mca'):
            with self.subTest(name):
                self.setUp()
                selected = self.component(name)
                owner = {'gain': 11, 'label': name}
                self.install_both(OLD, STOCK, MCA_OLD, identity(MCA_OLD, MCA_OLD_NOTE), {'gain': 1})
                self.assertEqual(self.run_tool('--boot', *selected['args'])[0], 0)
                live = json.loads(self.manifest.read_bytes())
                live['owner_calibration'] = owner
                self.manifest.write_bytes(MODULE.serialize(live))
                self.assertEqual(self.run_tool('--rollback', *selected['args'])[0], 0)
                stage = self.root / selected['state']
                real_unlink = Path.unlink

                def fail_rollback_once(path, missing_ok=False):
                    if path.name == 'rollback' and not fail_rollback_once.done:
                        fail_rollback_once.done = True
                        raise OSError('power cut before unlinking rollback')
                    return real_unlink(path, missing_ok=missing_ok)

                fail_rollback_once.done = False
                with mock.patch.object(Path, 'unlink', fail_rollback_once):
                    self.assertEqual(self.run_tool('--boot', *selected['args'])[0], 1)
                self.assertEqual((self.root / selected['module']).read_bytes(), selected['stock_bytes'])
                self.assertTrue((stage / 'hold').exists())
                self.assertTrue((stage / 'rollback').exists())
                cut = json.loads(self.manifest.read_bytes())
                self.assertEqual(cut['owner_calibration'], owner)
                self.assertEqual(cut['modules'][MODULE.NAME if name == 'speaker' else MCA_NAME],
                                 selected['stock']['note'])
                self.assertEqual((self.root / selected['other_module']).read_bytes(), selected['other_bytes'])
                code, report = self.run_tool('--boot', *selected['args'])
                self.assertEqual(code, 0, self.last_error)
                self.assertEqual(report['status'], 'held after a rollback; nothing applied')
                self.assertFalse((stage / 'rollback').exists())
                self.assertTrue((stage / 'hold').exists())
                self.assertEqual((self.root / selected['module']).read_bytes(), selected['stock_bytes'])
                finished = json.loads(self.manifest.read_bytes())
                self.assertEqual(finished['owner_calibration'], owner)
                self.assertEqual(finished['modules'][MODULE.NAME if name == 'speaker' else MCA_NAME],
                                 selected['stock']['note'])
                self.assertEqual((self.root / selected['other_module']).read_bytes(), selected['other_bytes'])
                code, report = self.run_tool('--boot', *selected['args'])
                self.assertEqual(code, 0, self.last_error)
                self.assertEqual(report['status'], 'held after a rollback; nothing applied')
                self.assertEqual((self.root / selected['module']).read_bytes(), selected['stock_bytes'])
                self.assertEqual(json.loads(self.manifest.read_bytes())['owner_calibration'], owner)
                self.assertTrue((stage / 'hold').exists())
                self.assertFalse((stage / 'rollback').exists())

    def test_partial_stock_backup_completes_and_a_wrong_part_refuses(self):
        """One valid backup half is finished on the next boot, then the fix applies.

        A backup half that is not the stock module or stock pins refuses and
        leaves the live module and manifest unchanged. Both missing-half
        directions are covered.
        """
        for name in ('speaker', 'mca'):
            for direction in ('module', 'manifest'):
                with self.subTest(component=name, direction=direction, valid=True):
                    self.setUp()
                    selected = self.component(name)
                    owner = {'gain': 6, 'label': name + '-' + direction}
                    document = self.install_both(
                        OLD, STOCK, MCA_OLD, identity(MCA_OLD, MCA_OLD_NOTE), owner)
                    stage = self.root / selected['state']
                    stage.mkdir(parents=True)
                    if direction == 'module':
                        self.put(selected['state'] + '/alpha1-module.ko', selected['stock_bytes'])
                    else:
                        self.put(selected['state'] + '/alpha1-manifest.json', MODULE.serialize(document))
                    code, report = self.run_tool('--boot', *selected['args'])
                    self.assertEqual(code, 0, self.last_error)
                    self.assertIn('applied', report['status'])
                    self.assertEqual((self.root / selected['module']).read_bytes(), selected['fixed_bytes'])
                    applied = json.loads(self.manifest.read_bytes())
                    self.assertEqual(applied['owner_calibration'], owner)
                    self.assertEqual(applied['modules'][MODULE.NAME if name == 'speaker' else MCA_NAME],
                                     selected['fixed']['note'])
                    self.assertEqual((stage / 'alpha1-module.ko').read_bytes(), selected['stock_bytes'])
                    saved = json.loads((stage / 'alpha1-manifest.json').read_bytes())
                    self.assertEqual(saved['modules'][MODULE.NAME if name == 'speaker' else MCA_NAME],
                                     selected['stock']['note'])
                    self.assertEqual((self.root / selected['other_module']).read_bytes(), selected['other_bytes'])

            for direction in ('module', 'manifest'):
                with self.subTest(component=name, direction=direction, valid=False):
                    self.setUp()
                    selected = self.component(name)
                    owner = {'gain': 7, 'label': 'wrong-' + direction}
                    document = self.install_both(
                        OLD, STOCK, MCA_OLD, identity(MCA_OLD, MCA_OLD_NOTE), owner)
                    stage = self.root / selected['state']
                    stage.mkdir(parents=True)
                    if direction == 'module':
                        self.put(selected['state'] + '/alpha1-module.ko', b'not-the-stock-module')
                    else:
                        wrong = json.loads(json.dumps(document))
                        key = MODULE.NAME if name == 'speaker' else MCA_NAME
                        wrong['modules'][key] = 'not-stock-note'
                        self.put(selected['state'] + '/alpha1-manifest.json', MODULE.serialize(wrong))
                    before_module = (self.root / selected['module']).read_bytes()
                    before_manifest = self.manifest.read_bytes()
                    before_other = (self.root / selected['other_module']).read_bytes()
                    self.assertEqual(self.run_tool('--boot', *selected['args'])[0], 1)
                    self.assertEqual((self.root / selected['module']).read_bytes(), before_module)
                    self.assertEqual(self.manifest.read_bytes(), before_manifest)
                    self.assertEqual((self.root / selected['other_module']).read_bytes(), before_other)
                    self.assertFalse((stage / 'hold').exists())

    def test_request_rollback_does_not_authorize_reverse_corruption(self):
        """Old bytes with new pins and no rollback marker are not a request.

        --rollback must not create the marker. A later --boot still leaves the
        live module, the manifest, and the stock backup unchanged.
        """
        for name in ('speaker', 'mca'):
            with self.subTest(name):
                self.setUp()
                selected = self.component(name)
                owner = {'gain': 8, 'label': 'reverse-' + name}
                if name == 'speaker':
                    live_record, live_bytes = FIXED, OLD
                    mca_record, mca_bytes = identity(MCA_OLD, MCA_OLD_NOTE), MCA_OLD
                    backup_document = with_mca(manifest(STOCK), mca_record)
                else:
                    live_record, live_bytes = STOCK, OLD
                    mca_record, mca_bytes = identity(MCA_NEW, MCA_NEW_NOTE), MCA_OLD
                    backup_document = with_mca(manifest(STOCK), identity(MCA_OLD, MCA_OLD_NOTE))
                self.install_both(live_bytes, live_record, mca_bytes, mca_record, owner)
                backup_document['owner_calibration'] = {'gain': 1}
                stage = self.root / selected['state']
                stage.mkdir(parents=True)
                self.put(selected['state'] + '/alpha1-module.ko', selected['stock_bytes'])
                self.put(selected['state'] + '/alpha1-manifest.json', MODULE.serialize(backup_document))
                before = (self.manifest.read_bytes(),
                          (self.root / selected['module']).read_bytes(),
                          (stage / 'alpha1-module.ko').read_bytes(),
                          (stage / 'alpha1-manifest.json').read_bytes(),
                          (self.root / selected['other_module']).read_bytes())
                self.assertFalse((stage / 'rollback').exists())
                self.assertEqual(self.run_tool('--rollback', *selected['args'])[0], 1)
                self.assertFalse((stage / 'rollback').exists())
                self.assertFalse((stage / 'hold').exists())
                self.assertEqual(self.run_tool('--boot', *selected['args'])[0], 1)
                self.assertEqual((self.manifest.read_bytes(),
                                  (self.root / selected['module']).read_bytes(),
                                  (stage / 'alpha1-module.ko').read_bytes(),
                                  (stage / 'alpha1-manifest.json').read_bytes(),
                                  (self.root / selected['other_module']).read_bytes()), before)
                self.assertEqual(json.loads(self.manifest.read_bytes())['owner_calibration'], owner)
                self.assertFalse((stage / 'rollback').exists())

    def test_mca_pins_match_the_qualified_spec_and_the_fixed_module(self):
        spec = json.loads((ROOT / 'audio/j713/qualified.json').read_text())
        artifact = spec['module_artifacts'][MCA_NAME]
        metadata = spec['files'][MCA_MODULE]
        reloaded = importlib.util.module_from_spec(SPEC)
        SPEC.loader.exec_module(reloaded)
        stock = getattr(reloaded, 'MCA_STOCK', {})
        fixed = getattr(reloaded, 'MCA_FIXED', {})
        self.assertEqual(artifact['path'], '/' + MCA_MODULE)
        self.assertEqual((artifact['sha256'], artifact['size'], artifact['note']),
                         (MCA_STOCK_PIN['sha256'], MCA_STOCK_PIN['size'], MCA_STOCK_PIN['note']))
        self.assertEqual((metadata['sha256'], metadata['size']), (MCA_STOCK_PIN['sha256'], MCA_STOCK_PIN['size']))
        self.assertEqual((stock.get('sha256'), stock.get('size'), stock.get('note')),
                         (MCA_STOCK_PIN['sha256'], MCA_STOCK_PIN['size'], MCA_STOCK_PIN['note']))
        self.assertEqual((fixed.get('sha256'), fixed.get('size'), fixed.get('note')),
                         (MCA_FIXED_PIN['sha256'], MCA_FIXED_PIN['size'], MCA_FIXED_PIN['note']))
        self.assertEqual(getattr(reloaded, 'NAME', None), 'snd_soc_j713_group')

    def test_pins_match_the_qualified_spec(self):
        spec = json.loads((ROOT / 'audio/j713/qualified.json').read_text())
        artifact = spec['module_artifacts'][MODULE.NAME]
        reloaded = importlib.util.module_from_spec(SPEC)  # unpatched pins
        SPEC.loader.exec_module(reloaded)
        self.assertEqual((artifact['sha256'], artifact['size'], artifact['note']),
                         (reloaded.FIXED['sha256'], reloaded.FIXED['size'], reloaded.FIXED['note']))
        self.assertEqual('/' + reloaded.MODULE, artifact['path'])
        self.assertEqual(spec['kernel_release'], reloaded.RELEASE)

    def stock_mca_on_fixed_speaker(self, owner):
        """Fixed speaker, stock MCA, and no component backup.

        The speaker is already fixed, so a speaker boot never enters ``apply``.
        MCA is the stock module with stock pins and no ``mca-module`` directory.
        """
        document = with_mca(manifest(FIXED), identity(MCA_OLD, MCA_OLD_NOTE))
        document['owner_calibration'] = owner
        self.put(MODULE.MODULE, NEW)
        self.put(MODULE.STAGED, NEW)
        self.put(MCA_MODULE, MCA_OLD)
        self.put(MCA_STAGED, MCA_NEW)
        return document

    def test_stock_mca_boot_applies_noncanonical_manifest_and_rollback_keeps_owner_edits(self):
        """Valid alpha.1 JSON that is not ``serialize`` bytes still applies MCA.

        ``package-j713-audio`` and ``prepare-j713-release`` write
        ``json.dumps(..., indent=2, sort_keys=True) + '\\n'``. An installed
        manifest can be those bytes without the final newline, or the same
        object with different indentation and key order. Boot must apply the
        stock MCA module, leave the fixed speaker and owner keys in place, and
        store the original manifest bytes as the backup. A later owner edit
        survives MCA rollback, which puts the stock MCA pins back.
        """
        owner = {'gain': 3, 'label': 'installed'}
        document = self.stock_mca_on_fixed_speaker(owner)
        canonical = MODULE.serialize(document)
        forms = {
            'no trailing newline': canonical[:-1],
            'different indent and key order': json.dumps(document, indent=4).encode(),
        }
        for label, raw in forms.items():
            with self.subTest(label):
                self.setUp()
                document = self.stock_mca_on_fixed_speaker(owner)
                self.assertNotEqual(raw, MODULE.serialize(document))
                self.assertEqual(json.loads(raw), document)
                self.manifest.write_bytes(raw)
                self.assertFalse((self.root / MCA_STATE).exists())
                self.assertFalse((self.root / MODULE.STATE).exists())
                code, report = self.run_tool('--component', 'mca')
                self.assertEqual(code, 0, self.last_error)
                self.assertEqual(report['state'], 'stock')
                code, report = self.run_tool('--boot', '--component', 'mca')
                self.assertEqual(code, 0, self.last_error)
                self.assertIn('applied', report['status'])
                self.assertEqual((self.root / MODULE.MODULE).read_bytes(), NEW)
                self.assertEqual((self.root / MCA_MODULE).read_bytes(), MCA_NEW)
                applied = json.loads(self.manifest.read_bytes())
                self.assertEqual(applied['owner_calibration'], owner)
                self.assertEqual(applied['modules'][MODULE.NAME], FIXED['note'])
                self.assertEqual(applied['files']['/' + MODULE.MODULE], FIXED['sha256'])
                self.assertEqual(applied['modules'][MCA_NAME], MCA_NEW_NOTE)
                self.assertEqual(applied['module_artifacts'][MCA_NAME]['sha256'],
                                 hashlib.sha256(MCA_NEW).hexdigest())
                self.assertEqual(applied['file_metadata'][MCA_MODULE]['size'], len(MCA_NEW))
                stage = self.root / MCA_STATE
                self.assertEqual((stage / 'alpha1-module.ko').read_bytes(), MCA_OLD)
                self.assertEqual((stage / 'alpha1-manifest.json').read_bytes(), raw)
                self.assertFalse((self.root / MODULE.STATE).exists())
                live = json.loads(self.manifest.read_bytes())
                live['owner_calibration'] = {'gain': 9, 'label': 'later'}
                live['owner_note'] = 'kept'
                self.manifest.write_bytes(MODULE.serialize(live))
                self.assertEqual(self.run_tool('--rollback', '--component', 'mca')[0], 0)
                code, report = self.run_tool('--boot', '--component', 'mca')
                self.assertEqual(code, 0, self.last_error)
                self.assertIn('rolled back', report['status'])
                self.assertEqual((self.root / MCA_MODULE).read_bytes(), MCA_OLD)
                self.assertEqual((self.root / MODULE.MODULE).read_bytes(), NEW)
                restored = json.loads(self.manifest.read_bytes())
                self.assertEqual(restored['modules'][MCA_NAME], MCA_OLD_NOTE)
                self.assertEqual(restored['module_artifacts'][MCA_NAME]['sha256'],
                                 hashlib.sha256(MCA_OLD).hexdigest())
                self.assertEqual(restored['module_artifacts'][MCA_NAME]['note'], MCA_OLD_NOTE)
                self.assertEqual(restored['files']['/' + MCA_MODULE], hashlib.sha256(MCA_OLD).hexdigest())
                self.assertEqual(restored['file_metadata'][MCA_MODULE]['sha256'],
                                 hashlib.sha256(MCA_OLD).hexdigest())
                self.assertEqual(restored['file_metadata'][MCA_MODULE]['size'], len(MCA_OLD))
                self.assertEqual(restored['modules'][MODULE.NAME], FIXED['note'])
                self.assertEqual(restored['files']['/' + MODULE.MODULE], FIXED['sha256'])
                self.assertEqual(restored['owner_calibration'], {'gain': 9, 'label': 'later'})
                self.assertEqual(restored['owner_note'], 'kept')
                self.assertTrue((stage / 'hold').exists())
                self.assertFalse((self.root / MODULE.STATE).exists())

    def test_malformed_manifest_or_unknown_mca_pins_stay_unchanged(self):
        """A broken manifest or pins that are not stock or fixed is left as it is."""
        owner = {'gain': 3, 'label': 'installed'}
        document = self.stock_mca_on_fixed_speaker(owner)
        cases = {
            'malformed': b'{"kernel_release":',
            'unknown pin without newline': MODULE.serialize(
                with_mca(manifest(FIXED), {**identity(MCA_OLD, MCA_OLD_NOTE), 'note': 'not-a-stock-note'})
            )[:-1],
        }
        for label, raw in cases.items():
            with self.subTest(label):
                self.setUp()
                self.stock_mca_on_fixed_speaker(owner)
                self.manifest.write_bytes(raw)
                before = (self.manifest.read_bytes(),
                          (self.root / MODULE.MODULE).read_bytes(),
                          (self.root / MCA_MODULE).read_bytes())
                self.assertEqual(self.run_tool('--boot', '--component', 'mca')[0], 1)
                self.assertEqual((self.manifest.read_bytes(),
                                  (self.root / MODULE.MODULE).read_bytes(),
                                  (self.root / MCA_MODULE).read_bytes()), before)
                self.assertFalse((self.root / MCA_STATE).exists())
                self.assertFalse((self.root / MODULE.STATE).exists())


if __name__ == '__main__':
    unittest.main()
