# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Registration fixtures adapted from the retained J713 installer owner tests."""
import json
import shutil
import unittest
from pathlib import Path
from unittest import mock

from test_m4_j715_audio_ready import Fixture, s

registration = s.load('m4-j715-audio-register.py')


def put(path, value=b'x'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)


class RegistrationTests(Fixture):
    def setUp(self):
        super().setUp()
        shutil.rmtree(self.root / 'sys/module')
        (self.root / 'sys/module').mkdir()
        shutil.rmtree(self.root / 'proc/asound')
        self.runtime = self.root / 'run/omarchy/j715-audio-registration'
        (self.runtime / 'registered.json').unlink()
        self.commands, self.missing = [], None
        def command(argv):
            self.commands.append(argv)
            if argv[0] == 'modinfo':
                return 'fixture-kernel SMP preempt mod_unload aarch64'
            if argv[-1] == s.HELPER:
                return self.report.decode()
            return 'inactive'
        self.command = command
        self.r = registration.Registration(self.manifest, self.root, command, lambda seconds: None)

    def _complete_registration_population_order(self, delayed=False, busy_udev=False):
        events = []
        pending = []
        sleeps = []
        def platform(device, driver, module=None):
            base = self.root / 'sys/bus/platform/devices' / device
            target = self.root / 'sys/bus/platform/drivers' / driver
            target.mkdir(parents=True, exist_ok=True)
            base.mkdir(parents=True, exist_ok=True)
            (base / 'driver').symlink_to(target)
            if module and not (target / 'module').exists():
                (target / 'module').symlink_to(self.root / 'sys/module' / module)
        def command(argv):
            events.append(argv)
            if busy_udev and argv[:2] == ['udevadm', 'settle']:
                raise RuntimeError('Unrelated udev event did not finish before the global timeout')
            if argv[0] == 'modinfo':
                return 'fixture-kernel SMP preempt mod_unload aarch64'
            if argv[0] == 'systemctl':
                return 'inactive'
            if argv[0] == 'insmod':
                name = next(n for n, e in self.r.manifest['module_artifacts'].items() if e['path'] == argv[1])
                base = self.root / 'sys/module' / name
                put(base / 'initstate', b'live')
                put(base / 'notes/.note.gnu.build-id', bytes.fromhex(self.r.manifest['module_artifacts'][name]['note']))
                if name == 'j713_audio_bootstrap':
                    for key, val in {'result': '0', 'stage': '4', 'overlay_id': '1'}.items():
                        put(base / 'parameters' / key, val.encode())
                    for addr in ('20','90','b8','d8','e8'):
                        device = '388284000.power-management:power-controller@' + addr
                        if delayed and addr == 'd8':
                            # Native first boot: populated d8 was unbound, then bound.
                            (self.root / 'sys/bus/platform/devices' / device).mkdir(parents=True)
                            pending.append(device)
                        else:
                            platform(device, 'apple-pmgr-pwrstate')
                    platform('390f00000.iommu', 'apple-dart')
                    platform('3ad01c000.i2c', 'i2c-apple')
                    platform('393800000.dma-controller', 'apple-admac', 'apple_admac')
                    (self.root / 'sys/bus/platform/devices/393600000.i2s').mkdir()
                    target = self.r.dt / 'soc/i2c@3ad01c000'
                    target.mkdir(parents=True)
                    adapter = self.root / 'sys/bus/i2c/devices/i2c-7'
                    adapter.mkdir(parents=True)
                    (adapter / 'of_node').symlink_to(target)
                if name == 'j713_audio_dt3':
                    self.assertFalse((self.root / 'sys/bus/platform/devices/393600000.i2s/driver').exists())
                    for key, val in {'result': '0', 'overlay_id': '2'}.items():
                        put(base / 'parameters' / key, val.encode())
                    platform('speaker-group', 'j715-speaker-group', 'snd_soc_j713_group')
                    put(self.root / 'sys/bus/platform/devices/speaker-group/status',
                        ' '.join(k + '=' + v for k, v in registration.FRESH_LOW.items()).encode())
                    target = self.root / 'sys/bus/i2c/drivers/tas2764'
                    target.mkdir(parents=True)
                    (target / 'module').symlink_to(self.root / 'sys/module/snd_soc_tas2764')
                    for addr in registration.ADDRESSES:
                        if addr == self.missing:
                            continue
                        node = self.r.dt / ('soc/i2c@3ad01c000/amplifier@' + addr[-2:])
                        node.mkdir()
                        dev = self.root / ('sys/bus/i2c/devices/7-' + addr)
                        dev.mkdir()
                        (dev / 'of_node').symlink_to(node)
                        (dev / 'driver').symlink_to(target)
                    # Real qualified preload left MCA unbound until the explicit bind.
                    put(self.root / 'sys/bus/platform/drivers/apple-mca/bind', b'')
                    platform('sound', 'snd-soc-macaudio', 'snd_soc_macaudio')
                    for direction in ('p','c'):
                        put(self.root / ('proc/asound/AppleJ715/pcm0' + direction + '/sub0/status'), b'closed')
            return ''
        self.r.command = command
        def settle(seconds):
            sleeps.append(seconds)
            if pending and len(sleeps) == 2:
                platform(pending.pop(), 'apple-pmgr-pwrstate')
        self.r.sleep = settle
        original_write = Path.write_text
        bind_writes = []
        def write(path, text, *args, **kwargs):
            result = original_write(path, text, *args, **kwargs)
            if path == self.root / 'sys/bus/platform/drivers/apple-mca/bind':
                self.assertEqual(text, '393600000.i2s\n')
                self.assertEqual(self.r.low(), registration.FRESH_LOW)
                self.assertTrue(all((self.root / ('sys/bus/i2c/devices/7-' + a + '/driver/module')).exists()
                                    for a in registration.ADDRESSES))
                bind_writes.append(text)
                platform('393600000.i2s', 'apple-mca', 'snd_soc_apple_mca')
            return result
        with mock.patch.object(Path, 'write_text', new=write):
            registration.register(self.r, self.runtime, {'boot': 'fixture-boot'})
        self.assertEqual(len(bind_writes), 1)
        result = json.loads((self.runtime / 'registered.json').read_text())
        self.assertEqual(result['status'], registration.FRESH_LOW)
        loaded = [Path(c[1]).name for c in events if c[0] == 'insmod']
        self.assertEqual(loaded, [Path(self.r.manifest['module_artifacts'][n]['path']).name for n in registration.MODULES])
        self.assertTrue((self.runtime / 'attempt.json').exists())
        self.assertFalse((self.runtime / 'failure.json').exists())


    def test_complete_registration_population_order(self):
        self._complete_registration_population_order()

    def test_deferred_provider_binds_without_reloading(self):
        self._complete_registration_population_order(delayed=True)

    def test_registration_ignores_unrelated_udev_events(self):
        self._complete_registration_population_order(busy_udev=True)

    def test_wrong_board_receipt_abi_or_legacy_module_has_zero_loads(self):
        paths = [('/sys/firmware/devicetree/base/compatible', b'apple,j713\0apple,t8132\0'),
                 ('/usr/lib/fixture-abi', b'wrong ABI'),
                 (self.artifact['qualification'], b'{}')]
        for name, data in paths:
            target = s.at(self.root, name)
            original = target.read_bytes()
            self.write(name, data)
            with self.subTest(name=name):
                self.assertEqual(registration.main(self.root, self.command), 1)
                self.assertFalse((self.runtime / 'attempt.json').exists())
            self.write(name, original)
        for name in registration.MODULES:
            path = self.root / 'sys/module' / name
            path.mkdir()
            with self.subTest(module=name):
                self.assertEqual(registration.main(self.root, self.command), 1)
                self.assertFalse((self.runtime / 'attempt.json').exists())
            path.rmdir()
        self.assertFalse(any(c[0] in ('insmod', 'modprobe') for c in self.commands))

    def test_fresh_id_failure_and_unqualified_id_never_load(self):
        for response in ('{}', self.report.decode().replace('"speaker_id": 3', '"speaker_id": 0')):
            self.report = response.encode()
            self.assertEqual(registration.main(self.root, self.command), 1)
        self.assertFalse(any(c[0] in ('insmod', 'modprobe') for c in self.commands))
        self.assertFalse((self.runtime / 'attempt.json').exists())

    def test_failed_helper_or_selection_publication_has_no_latch(self):
        with mock.patch.object(registration.publication, 'atomic_record', side_effect=OSError('fixture')), \
                self.assertRaises(OSError):
            registration.register(self.r, self.runtime, {})
        self.assertFalse((self.runtime / 'attempt.json').exists())
        failing = mock.Mock(side_effect=RuntimeError('ID failed'))
        self.assertEqual(registration.main(self.root, failing), 1)
        self.assertEqual(failing.call_args.args[0][-1], s.HELPER)
        self.assertFalse(any(c[0] in ('insmod', 'modprobe') for c in self.commands))

    def test_preflight_vermagic_and_build_id_refused(self):
        self.r.command = lambda argv: 'wrong ABI' if argv[0] == 'modinfo' else 'inactive'
        with self.assertRaises(ValueError):
            registration.register(self.r, self.runtime, {})
        self.assertFalse((self.runtime / 'attempt.json').exists())
        module = registration.MODULES[0]
        self.write('/sys/module/' + module + '/initstate', 'live')
        self.write('/sys/module/' + module + '/notes/.note.gnu.build-id', b'wrong')
        with self.assertRaises(RuntimeError):
            self.r.module(module)

    def test_low_hardware_cache_and_mask_refused(self):
        for key, value in (('gpio_hw', '00070a03'), ('gpio_cache', '00070a03'),
                           ('mute_mask', 'f'), ('sensing', '1'), ('faulted', '1')):
            values = dict(registration.FRESH_LOW, **{key: value})
            self.write('/sys/bus/platform/devices/speaker-group/status',
                       ' '.join(k + '=' + v for k, v in values.items()))
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.r.low()

    def test_provider_timeout_or_wrong_binding_never_loads_topology(self):
        steps = []
        self.r.sleep = steps.append
        with self.assertRaises(RuntimeError):
            self.r.wait_providers()
        self.assertEqual(len(steps), 100)
        base = self.root / 'sys/bus/platform/devices/388284000.power-management:power-controller@20'
        base.mkdir(parents=True)
        wrong = self.root / 'sys/bus/platform/drivers/foreign'
        wrong.mkdir(parents=True)
        (base / 'driver').symlink_to(wrong)
        steps.clear()
        with self.assertRaises(RuntimeError):
            self.r.wait_providers()
        self.assertEqual(steps, [])
        self.assertEqual(self.commands, [])

    def test_partial_failure_retained_and_no_automatic_retry(self):
        self.r.apply = mock.Mock(side_effect=RuntimeError('provider absent'))
        with self.assertRaises(RuntimeError):
            registration.register(self.r, self.runtime, {'boot': 'fixture-boot'})
        self.assertTrue((self.runtime / 'failure.json').exists())
        with self.assertRaises(ValueError):
            registration.register(self.r, self.runtime, {'boot': 'fixture-boot'})
        self.r.apply.assert_called_once()
        self.assertFalse((self.runtime / 'registered.json').exists())

    def test_common_lock_excludes_registration(self):
        import fcntl
        lock = self.root / 'run/lock/m4-audio-test.lock'
        with lock.open('w') as stream:
            lock.chmod(0o600)
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(registration.main(self.root, self.command), 1)
        self.assertEqual(self.commands, [])

    def missing_codec(self):
        with self.assertRaises((OSError, ValueError)):
            self._complete_registration_population_order()
        self.assertEqual((self.root / 'sys/bus/platform/drivers/apple-mca/bind').read_bytes(), b'')
        self.assertTrue((self.runtime / 'failure.json').exists())


# Each missing codec gets an independent unittest fixture and retained attempt.
for address in registration.ADDRESSES:
    def missing(self, address=address):
        self.missing = address
        self.missing_codec()
    setattr(RegistrationTests, 'test_missing_codec_' + address, missing)


if __name__ == '__main__':
    unittest.main()
