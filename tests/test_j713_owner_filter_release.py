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
handoff = module('handoff', 'm4-j713-owner-assets.py')


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
                         'modules': {'unchanged': 'note'}, 'readiness_inputs': {'gain': 0}}
        for name, record in self.profile['reference_outputs'].items():
            key = 'usr/share/asahi-audio/j713/' + name
            self.baseline['files']['/' + key] = record['sha256']
            self.baseline['file_metadata'][key] = {**record, 'mode': 420}

    def convert(self, baseline=None, profile=None, pin=None):
        raw = json.dumps(self.baseline if baseline is None else baseline).encode()
        return release.owner_audio_manifest(
            raw, pin or hashlib.sha256(raw).hexdigest(), profile or self.profile)

    def test_changes_exactly_four_hash_pairs_and_preserves_every_other_field(self):
        actual = json.loads(self.convert())
        expected = copy.deepcopy(self.baseline)
        for name, record in self.profile['outputs'].items():
            key = 'usr/share/asahi-audio/j713/' + name
            expected['files']['/' + key] = record['sha256']
            expected['file_metadata'][key]['sha256'] = record['sha256']
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

    def test_installer_handoff_accepts_the_same_exact_native_set(self):
        actual = {name: {'size': record[0], 'sha256': record[1]}
                  for name, record in handoff.FIRS.items()}
        self.assertEqual(actual, self.profile['outputs'])

    def test_handoff_rejects_one_byte_change_in_each_lane(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            data = {name: ('synthetic-lane-' + str(index)).encode()
                    for index, name in enumerate(release.FIRS)}
            pins = {name: (len(raw), hashlib.sha256(raw).hexdigest())
                    for name, raw in data.items()}
            with mock.patch.object(handoff, 'FIRS', pins):
                for name, raw in data.items():
                    (directory / name).write_bytes(raw)
                self.assertEqual(handoff.filters(directory), data)
                for name, raw in data.items():
                    (directory / name).write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
                    with self.assertRaisesRegex(ValueError, 'Unqualified speaker filter'):
                        handoff.filters(directory)
                    (directory / name).write_bytes(raw)

    def test_handoff_requires_complete_set_even_for_no_widevine(self):
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.dict(handoff.os.environ,
                                 M4_OWNER_FIRS=str(Path(temporary).resolve()), M4_OWNER_WIDEVINE='no'):
                with self.assertRaises(FileNotFoundError):
                    handoff.prepare_for_installer()

    def test_root_builder_installs_native_manifest_before_first_boot(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            tree, root = base / 'source', base / 'root'
            (tree / 'audio/j713/owner-fir').mkdir(parents=True)
            for directory in ('tools', 'packaging'):
                shutil.copytree(ROOT / directory, tree / directory)
            originals = {}
            for index, name in enumerate(release.FIRS):
                data = bytes([index]) * self.profile['outputs'][name]['size']
                originals[name] = data
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
            self.assertEqual(pin, {'audio': hashlib.sha256(actual).hexdigest(), 'video': 'v' * 64})
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
                self.assertFalse((root / 'usr/share/asahi-audio/j713' / name).exists())


if __name__ == '__main__':
    unittest.main()
