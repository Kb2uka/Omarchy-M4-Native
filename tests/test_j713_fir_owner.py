# SPDX-License-Identifier: MIT
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import tempfile
import types
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / 'audio/j713/owner-fir'
AUDIODSP = '/System/Library/Components/AudioDSP.component/Contents/MacOS/AudioDSP'


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tools' / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


owner = module('fir_owner', 'm4-j713-fir-owner.py')


def sha(data):
    return hashlib.sha256(data).hexdigest()


class QualifiedInputTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'AudioDSP'
        self.path.write_bytes(b'second build')
        self.first = {'sha256': sha(b'first build'), 'size': 11, 'macos': ['26.5.1 (25F80)']}
        self.second = {'sha256': sha(b'second build'), 'size': 12, 'macos': ['26.6.2 (25G83)']}

    def record(self, qualified):
        return {'path': str(self.path), 'qualified': qualified}

    def test_each_listed_build_is_accepted(self):
        for data, entry in ((b'first build', self.first), (b'second build', self.second)):
            self.path.write_bytes(data)
            self.assertEqual(owner.qualified_input(self.record([self.first, self.second])),
                             (data, entry))

    def test_unlisted_build_is_refused(self):
        self.path.write_bytes(b'third build!')
        with self.assertRaisesRegex(RuntimeError, 'Unsupported input hash'):
            owner.qualified_input(self.record([self.first, self.second]))

    def test_matching_hash_with_wrong_size_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, 'Unsupported input hash'):
            owner.qualified_input(self.record([{**self.second, 'size': 13}]))

    def test_symlink_is_refused(self):
        link = self.path.with_name('link')
        link.symlink_to(self.path)
        with self.assertRaisesRegex(RuntimeError, 'Missing regular input'):
            owner.qualified_input({'path': str(link), 'qualified': [self.second]})

    def test_malformed_lists_are_refused(self):
        for qualified in ([], [self.second, self.second], [{**self.second, 'sha256': 'A' * 64}],
                          [{**self.second, 'size': 0}], [{**self.second, 'size': True}],
                          [{**self.second, 'note': 'unexpected'}]):
            with self.subTest(qualified=qualified):
                with self.assertRaisesRegex(RuntimeError, 'Invalid qualified input list'):
                    owner.qualified_input(self.record(qualified))


class BundleConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((BUNDLE / 'manifest.json').read_bytes())

    def test_schema_and_input_lists(self):
        self.assertEqual(self.manifest['schema'], 2)
        for name, record in self.manifest['owner_inputs'].items():
            with self.subTest(name=name):
                self.assertEqual(set(record), {'path', 'qualified'})
                self.assertTrue(record['qualified'])

    def test_retained_sources_match_their_pins(self):
        for name, record in self.manifest['sources'].items():
            data = (BUNDLE / 'src' / name).read_bytes()
            self.assertEqual((len(data), sha(data)), (record['size'], record['sha256']), name)

    def test_run_scripts_take_the_matched_hash_from_the_generator(self):
        for name in ('run-transfer.py', 'run-memory.py'):
            text = (BUNDLE / 'src' / name).read_text()
            assertion = next(line for line in text.splitlines() if 'AudioDSP.component' in line)
            self.assertIn('owner-inputs.json', assertion, name)
            self.assertIsNone(re.search('[0-9a-f]{64}', assertion), name)

    def test_manifest_with_static_profile_keeps_qualified_output_identities(self):
        directory = ROOT / 'audio/j713/filters'
        self.assertEqual({path.name for path in directory.glob('*.wav')},
                         set(self.manifest['outputs']))
        for name, record in self.manifest['outputs'].items():
            with self.subTest(name=name):
                data = (directory / name).read_bytes()
                self.assertEqual((len(data), sha(data)), (record['size'], record['sha256']))


class GenerateTests(unittest.TestCase):
    """Run generate() against a synthetic bundle with the native steps mocked."""

    OUTPUTS = {'j713-static-minimal-lane%d-48000.wav' % lane: b'lane%d' % lane for lane in range(4)}

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        self.bundle = base / 'bundle'
        self.system = base / 'system'
        self.output = base / 'out' / 'firs'
        self.output.parent.mkdir()
        for directory in ('src', 'bin', 'dependencies'):
            (self.bundle / directory).mkdir(parents=True)
        self.system.mkdir()
        manifest = {'schema': 2, 'board': 'apple,j713', 'sources': {}, 'helpers': {},
                    'presets': {'Tone1.aupreset': self.pin(b'preset')}}
        for name in owner.SOURCE_NAMES:
            data = name.encode()
            (self.bundle / 'src' / name).write_bytes(data)
            manifest['sources'][name] = self.pin(data)
        for name in owner.HELPER_NAMES:
            (self.bundle / 'bin' / name).write_bytes(name.encode())
            manifest['helpers'][name] = self.pin(name.encode())
        wheel = io.BytesIO()
        with zipfile.ZipFile(wheel, 'w') as archive:
            archive.writestr('numpy/__init__.py', '')
        (self.bundle / 'dependencies' / owner.WHEEL).write_bytes(wheel.getvalue())
        manifest['numpy'] = {'filename': owner.WHEEL, **self.pin(wheel.getvalue())}
        manifest['outputs'] = {name: self.pin(data) for name, data in self.OUTPUTS.items()}
        manifest['owner_inputs'] = {}
        self.inputs = {'AudioDSP': b'dsp 26.6.2',
                       'builtin_speaker_out_general.austrip': b'austrip',
                       'builtin_speaker_out_general.propstrip': b'propstrip'}
        for name, data in self.inputs.items():
            (self.system / name).write_bytes(data)
            manifest['owner_inputs'][name] = {'path': str(self.system / name),
                                              'qualified': [self.pin(data)]}
        manifest['owner_inputs']['AudioDSP']['qualified'].insert(
            0, {**self.pin(b'dsp 26.5.1'), 'macos': ['26.5.1 (25F80)']})
        self.manifest = manifest
        self.write_manifest()
        self.outputs = dict(self.OUTPUTS)
        self.seen_inputs = []

    @staticmethod
    def pin(data):
        return {'sha256': sha(data), 'size': len(data)}

    def write_manifest(self):
        (self.bundle / 'manifest.json').write_text(json.dumps(self.manifest))

    def fake_run(self, source, work, dependencies, name):
        if name == 'inspect_strip.py':
            (work / 'decoded').mkdir()
            (work / 'decoded' / 'Tone1.aupreset').write_bytes(b'preset')
        elif name == 'run-transfer.py':
            self.seen_inputs.append(json.loads((work / 'owner-inputs.json').read_text()))
        elif name == 'qualify_memory.py':
            (work / 'output').mkdir()
            for filename, data in self.outputs.items():
                (work / 'output' / filename).write_bytes(data)

    def generate(self):
        fake_sys = types.SimpleNamespace(version_info=(3, 13, 9),
                                         flags=types.SimpleNamespace(optimize=0))
        with mock.patch.object(owner, 'run_script', self.fake_run), \
             mock.patch.object(owner, 'sys', fake_sys), \
             mock.patch.object(owner.platform, 'system', return_value='Darwin'), \
             mock.patch.object(owner.platform, 'machine', return_value='arm64'), \
             mock.patch.object(owner.platform, 'mac_ver', return_value=('26.6.2', ('', '', ''), '')), \
             mock.patch('builtins.print'):
            owner.generate(self.bundle, self.output)

    def test_second_qualified_build_generates_the_pinned_outputs(self):
        self.generate()
        self.assertEqual(self.seen_inputs, [{name: sha(data) for name, data in self.inputs.items()}])
        for name, data in self.OUTPUTS.items():
            self.assertEqual((self.output / name).read_bytes(), data)
        receipt = json.loads((self.output / 'receipt.json').read_text())
        self.assertEqual(receipt['schema'], 2)
        self.assertEqual(receipt['macos_version'], '26.6.2')
        self.assertEqual(receipt['owner_inputs']['AudioDSP'],
                         {'path': str(self.system / 'AudioDSP'), **self.pin(b'dsp 26.6.2')})

    def test_unlisted_build_is_refused_before_rendering(self):
        (self.system / 'AudioDSP').write_bytes(b'dsp 26.7.1')
        with self.assertRaisesRegex(RuntimeError, 'Unsupported input hash'):
            self.generate()
        self.assertEqual(self.seen_inputs, [])
        self.assertFalse(self.output.exists())

    def test_output_differing_by_one_byte_is_refused(self):
        name = next(iter(self.outputs))
        self.outputs[name] = self.outputs[name][:-1] + b'X'
        with self.assertRaisesRegex(RuntimeError, 'Unsupported input hash'):
            self.generate()
        self.assertFalse(self.output.exists())

    def test_input_replaced_during_generation_is_refused(self):
        original = self.fake_run

        def swap(source, work, dependencies, name):
            original(source, work, dependencies, name)
            if name == 'qualify_memory.py':
                (self.system / 'AudioDSP').write_bytes(b'dsp 26.5.1')

        self.fake_run = swap
        with self.assertRaisesRegex(RuntimeError, 'Owner input changed during generation'):
            self.generate()
        self.assertFalse(self.output.exists())

    def test_schema_1_bundle_is_refused(self):
        self.manifest['schema'] = 1
        self.write_manifest()
        with self.assertRaisesRegex(RuntimeError, 'Unsupported filter reproduction bundle'):
            self.generate()


if __name__ == '__main__':
    unittest.main()
