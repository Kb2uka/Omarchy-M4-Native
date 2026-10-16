# SPDX-License-Identifier: MIT
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tools' / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


release = module('release', 'prepare-j713-release.py')


class OwnerFilterReleaseTests(unittest.TestCase):
    def setUp(self):
        self.profile = json.loads((ROOT / 'audio/j713/owner-fir/manifest.json').read_bytes())
        self.profile['reference_outputs'] = {
            name: {'size': record['size'], 'sha256': str(index) * 64}
            for index, (name, record) in enumerate(self.profile['outputs'].items())
        }
        self.baseline = {'schema': 1, 'board': 'apple,j713', 'service': 'protected-speakers',
                         'files': {'/usr/lib/protection-daemon': 'f' * 64},
                         'file_metadata': {'usr/lib/protection-daemon':
                                           {'sha256': 'f' * 64, 'size': 256, 'mode': 493}},
                         'modules': {'unchanged': 'note'}, 'readiness_inputs': {'gain': 0},
                         'redistributable': False,
                         'distribution_hold': ['Measured FIR impulse responses have no established redistribution license; not included in tracked source.']}
        for name, record in self.profile['reference_outputs'].items():
            key = 'usr/share/asahi-audio/j713/' + name
            self.baseline['files']['/' + key] = record['sha256']
            self.baseline['file_metadata'][key] = {**record, 'mode': 420}

    def convert(self, baseline=None, profile=None, pin=None):
        raw = json.dumps(self.baseline if baseline is None else baseline).encode()
        return release.owner_audio_manifest(
            raw, pin or hashlib.sha256(raw).hexdigest(), profile or self.profile)

    def test_manifest_with_static_filters_updates_hash_pairs_and_replaces_historical_policy(self):
        actual = json.loads(self.convert())
        expected = copy.deepcopy(self.baseline)
        for name, record in self.profile['outputs'].items():
            key = 'usr/share/asahi-audio/j713/' + name
            expected['files']['/' + key] = record['sha256']
            expected['file_metadata'][key]['sha256'] = record['sha256']
        expected.pop('distribution_hold')
        expected.pop('redistributable')
        expected['filter_provenance'] = (
            'Derived from Apple J713 tuning through the retained pipeline; see audio/j713/filters/README.md.'
        )
        self.assertEqual(actual, expected)
        self.assertNotEqual(actual['files'], self.baseline['files'])

    def test_foreign_baseline_manifest_refuses(self):
        with self.assertRaisesRegex(RuntimeError, 'audio manifest'):
            self.convert(pin='f' * 64)

    def test_wrong_baseline_filter_hash_refuses(self):
        self.baseline['files']['/usr/share/asahi-audio/j713/' + release.FIRS[0]] = 'e' * 64
        with self.assertRaisesRegex(RuntimeError, 'reference filter'):
            self.convert()

    def test_wrong_baseline_metadata_refuses(self):
        for field, value in [('size', 1), ('mode', 511), ('sha256', 'e' * 64)]:
            with self.subTest(field=field):
                base = copy.deepcopy(self.baseline)
                base['file_metadata']['usr/share/asahi-audio/j713/' + release.FIRS[0]][field] = value
                with self.assertRaisesRegex(RuntimeError, 'reference filter'):
                    self.convert(baseline=base)

    def test_missing_or_additional_filter_refuses(self):
        for group in ('outputs', 'reference_outputs'):
            for change in ('missing', 'extra'):
                with self.subTest(group=group, change=change):
                    profile = copy.deepcopy(self.profile)
                    if change == 'missing':
                        profile[group].pop(release.FIRS[0])
                    else:
                        profile[group]['unexpected.wav'] = profile[group][release.FIRS[0]]
                    with self.assertRaisesRegex(RuntimeError, 'filter set'):
                        self.convert(profile=profile)

    def test_changed_filter_length_or_invalid_hash_refuses(self):
        for field, value in [('size', 1), ('sha256', 'invalid')]:
            with self.subTest(field=field):
                profile = copy.deepcopy(self.profile)
                profile['outputs'][release.FIRS[0]][field] = value
                with self.assertRaisesRegex(RuntimeError, 'native filter'):
                    self.convert(profile=profile)

    def test_static_profile_with_changed_last_filter_refuses_before_image_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            tree = Path(temporary)
            records = {}
            for index, name in enumerate(release.FIRS):
                data = bytes([index]) * 16
                target = tree / 'audio/j713/filters' / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                records[name] = {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
            manifest = tree / 'audio/j713/owner-fir/manifest.json'
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({'outputs': records}))
            (tree / 'audio/j713/filters' / release.FIRS[-1]).write_bytes(b'changed')
            with mock.patch.object(release, 'command') as command:
                with self.assertRaisesRegex(RuntimeError, 'Static FIR identity mismatch'):
                    release.prepare_root(tree / 'old', tree / 'new', tree / 'mounted', tree, 'id')
            command.assert_not_called()

    def test_root_builder_installs_native_manifest_before_first_boot(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            tree, root = base / 'source', base / 'root'
            (tree / 'audio/j713/owner-fir').mkdir(parents=True)
            for directory in ('tools', 'packaging'):
                shutil.copytree(ROOT / directory, tree / directory)
            # This synthetic root carries no unpackaged binaries, so it joins none.
            (tree / release.SOURCE_JOINS).write_text('{"schema": 1, "binaries": {}}')
            originals = {}
            approved = {}
            for index, name in enumerate(release.FIRS):
                data = bytes([index]) * self.profile['outputs'][name]['size']
                originals[name] = data
                approved[name] = bytes([index + 10]) * len(data)
                self.profile['outputs'][name] = {'size': len(data), 'sha256': hashlib.sha256(approved[name]).hexdigest()}
                target = tree / 'audio/j713/filters' / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(approved[name])
                record = {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                self.profile['reference_outputs'][name] = record
                key = 'usr/share/asahi-audio/j713/' + name
                self.baseline['files']['/' + key] = record['sha256']
                self.baseline['file_metadata'][key] = {**record, 'mode': 420}
            (tree / 'audio/j713/owner-fir/manifest.json').write_text(json.dumps(self.profile))
            raw = json.dumps(self.baseline).encode()
            source, image = base / 'old.ext4', base / 'new.ext4'
            source.write_bytes(b'retained image')

            def command(*args):
                if args[0] == 'cp':
                    shutil.copyfile(args[-2], args[-1])
                elif args[0] == 'mount':
                    def write(name, data):
                        path = root / name
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(data)
                    write('etc/omarchy/j713-audio-manifest.json', raw)
                    for name, data in originals.items():
                        write('usr/share/asahi-audio/j713/' + name, data)
                        (root / 'usr/share/asahi-audio/j713' / name).chmod(0o600)
                    for name in release.PROFILES:
                        write('etc/skel/.config/omarchy/pending/widevine-' + name + '.json',
                              json.dumps({'schema': 1, 'profile': name,
                                          'consent': 'owner-requested'}).encode())
                    (root / 'etc/systemd/system/multi-user.target.wants').mkdir(parents=True)
                elif args[0] not in ('umount', 'tune2fs', 'e2fsck'):
                    self.fail('Unexpected disk command: ' + str(args))

            with mock.patch.object(release, 'BASE_ROOT', hashlib.sha256(source.read_bytes()).hexdigest()), \
                    mock.patch.object(release, 'BASE_AUDIO', hashlib.sha256(raw).hexdigest()), \
                    mock.patch.object(release, 'command', side_effect=command), \
                    mock.patch.object(release, 'prepare_startup', side_effect=lambda r, t, a: a) as startup, \
                    mock.patch.object(release, 'prepare_decoder', return_value='v' * 64) as decoder, \
                    mock.patch.object(release, 'fill_free_space'):
                pin = release.prepare_root(source, image, root, tree, 'new-root-id')
            startup.assert_called_once_with(root, tree, self.convert())
            decoder.assert_called_once_with(root, tree)
            actual = (root / 'etc/omarchy/j713-audio-manifest.json').read_bytes()
            self.assertEqual(pin, {'audio': hashlib.sha256(actual).hexdigest(), 'video': 'v' * 64,
                                   'unverified': []})
            self.assertEqual(actual, self.convert())
            self.assertNotEqual(pin['audio'], hashlib.sha256(raw).hexdigest())
            self.assertEqual((root / 'etc/m4-root-id').read_text(), 'new-root-id\n')
            self.assertTrue((root / 'etc/systemd/system/multi-user.target.wants/j713-owner-assets.service').is_symlink())
            timer = root / 'etc/systemd/system/timers.target.wants/m4-channel-connect.timer'
            self.assertEqual(str(timer.readlink()), '/usr/lib/systemd/system/m4-channel-connect.timer')
            self.assertEqual((root / 'usr/lib/omarchy/m4-channel-connect.py').read_bytes(),
                             (ROOT / 'tools/m4-channel-setup.py').read_bytes())
            self.assertFalse((root / 'usr/lib/omarchy-m4').exists(), 'that path belongs to the channel packages')
            for unit in ('m4-channel-connect.service', 'm4-channel-connect.timer'):
                self.assertEqual((root / 'usr/lib/systemd/system' / unit).read_bytes(),
                                 (ROOT / 'packaging/j713-first-boot' / unit).read_bytes())
            service = (root / 'usr/lib/systemd/system/m4-channel-connect.service').read_text()
            self.assertIn('ExecStart=/usr/bin/python3 /usr/lib/omarchy/m4-channel-connect.py --apply\n', service)
            self.assertIn('TimeoutStartSec=infinity\n', service)
            self.assertIn('ExecStartPost=/usr/bin/systemctl disable --now --no-block m4-channel-connect.timer\n', service)
            self.assertIn('OnCalendar=', (root / 'usr/lib/systemd/system/m4-channel-connect.timer').read_text())
            self.assertIn('ConditionPathExists=!/var/lib/omarchy/provisioning/pending\n', service)
            self.assertIn('ConditionPathExists=!/var/lib/omarchy-m4/connected\n', service)
            for name in release.FIRS:
                self.assertEqual((root / 'usr/share/asahi-audio/j713' / name).read_bytes(), approved[name])
                self.assertEqual((root / 'usr/share/asahi-audio/j713' / name).stat().st_mode & 0o777, 0o644)


if __name__ == '__main__':
    unittest.main()
