# SPDX-License-Identifier: MIT
import copy
import importlib.util
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import subprocess
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from m4_audio_package import digest, dtb_compatible


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tools' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


installer = load('installer', 'configure-m4-audio.py')
registration = load('registration', 'm4-j713-audio-register.py')
package = load('package', 'package-j713-audio.py')


def tree(compatible=b'apple,j713\0apple,t8132\0'):
    strings = b'compatible\0'
    body = struct.pack('>I', 1) + bytes(4) + struct.pack('>III', 3, len(compatible), 0)
    body += compatible + bytes((-len(compatible)) % 4) + struct.pack('>II', 2, 9)
    return struct.pack('>10I', 0xd00dfeed, 56 + len(body) + len(strings), 56,
                       56 + len(body), 40, 17, 16, 0, len(strings), len(body)) + bytes(16) + body + strings


def put(path, value=b'x'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)


class ImageTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(os.umask, os.umask(0o022))
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.image, self.package = base / 'image', base / 'package'
        self.image.mkdir()
        self.dtb = base / 'base.dtb'
        self.dtb.write_bytes(tree())
        for path in ('etc/m4-root-id', 'var/lib/omarchy/provisioning/pending', 'usr/lib/libc.so.6'):
            put(self.image / path)
        (self.image / 'usr/lib/modules/exact').mkdir(parents=True)
        files = {'usr/lib/omarchy/example': b'qualified', 'usr/lib/systemd/system/example.service': b'unit'}
        self.manifest = dict(schema=1, board='apple,j713', kernel_release='exact', redistributable=False,
                             base_dtb_sha256=[digest(self.dtb.read_bytes())],
                             abi_files={'usr/lib/libc.so.6': dict(sha256=digest(b'x'), size=1)},
                             file_metadata={n: dict(sha256=digest(d), size=len(d), mode=0o644) for n, d in files.items()},
                             files={'/' + n: digest(d) for n, d in files.items()},
                             symlinks={'etc/systemd/system/multi-user.target.wants/example.service':
                                       '/usr/lib/systemd/system/example.service'})
        for name, data in files.items():
            put(self.package / 'root' / name, data)
        self.write_manifest()
        for directory in self.image.rglob('*'):
            if directory.is_dir():
                directory.chmod(0o755)

    def write_manifest(self):
        raw = json.dumps(self.manifest).encode()
        put(self.package / 'manifest.json', raw)
        self.pin = digest(raw)

    def configure(self, **kwargs):
        args = dict(root=self.image, package=self.package, board='apple,j713', kernel_release='exact',
                    base_dtb=self.dtb, package_sha256=self.pin, apply=True)
        args.update(kwargs)
        return installer.configure(**args)

    def assert_no_install(self):
        self.assertFalse((self.image / 'usr/lib/omarchy/example').exists())
        self.assertFalse((self.image / installer.MANIFEST).exists())

    def test_apply_check_idempotent_and_enable_links(self):
        self.configure()
        self.assertEqual((self.image / 'usr/lib/omarchy/example').read_bytes(), b'qualified')
        self.assertEqual(os.readlink(self.image / next(iter(self.manifest['symlinks']))),
                         '/usr/lib/systemd/system/example.service')
        self.assertEqual(self.configure(apply=False)['changed_files'], [])
        self.assertEqual(self.configure()['changed_files'], [])

    def test_check_refuses_missing_without_write(self):
        with self.assertRaisesRegex(ValueError, 'differs'):
            self.configure(apply=False)
        self.assert_no_install()

    def test_board_kernel_abi_refused_before_write(self):
        for values in (dict(board='apple,j413'), dict(kernel_release='other')):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.configure(**values)
        put(self.image / 'usr/lib/libc.so.6', b'new')
        with self.assertRaisesRegex(ValueError, 'Hash/size'):
            self.configure()
        self.assert_no_install()

    def test_qualified_board_dtb_both_compat_and_hash(self):
        self.dtb.write_bytes(tree(b'apple,j413\0apple,t8132\0'))
        self.manifest['base_dtb_sha256'] = [digest(self.dtb.read_bytes())]
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, 'board'):
            self.configure()
        self.dtb.write_bytes(tree())
        with self.assertRaisesRegex(ValueError, 'Unqualified'):
            self.configure()
        self.assert_no_install()

    def test_package_pin_and_payload_corruption(self):
        with self.assertRaisesRegex(ValueError, 'pin'):
            self.configure(package_sha256='0' * 64)
        put(self.package / 'root/usr/lib/omarchy/example', b'changed')
        with self.assertRaisesRegex(ValueError, 'Hash/size'):
            self.configure()
        self.assert_no_install()

    def test_custom_file_refuses_before_any_write(self):
        put(self.image / 'usr/lib/systemd/system/example.service', b'owner customization')
        with self.assertRaisesRegex(ValueError, 'Conflicting existing'):
            self.configure()
        self.assert_no_install()

    def test_symlink_and_non_directory_parent_refused(self):
        (self.image / 'usr/lib/omarchy').symlink_to(self.package, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            self.configure()
        (self.image / 'usr/lib/omarchy').unlink()
        put(self.image / 'usr/lib/omarchy')
        with self.assertRaisesRegex(ValueError, 'Non-directory|public parent'):
            self.configure()
        self.assertFalse((self.image / installer.MANIFEST).exists())

    def test_conflicting_enable_and_stale_temp_refused(self):
        path = self.image / next(iter(self.manifest['symlinks']))
        path.parent.mkdir(parents=True)
        path.symlink_to('/different.service')
        with self.assertRaisesRegex(ValueError, 'startup link'):
            self.configure()
        path.unlink()
        put(self.image / 'usr/lib/omarchy/example.m4-audio-new')
        with self.assertRaisesRegex(ValueError, 'Stale temporary'):
            self.configure()
        self.assert_no_install()

    def test_non_pending_image_refused(self):
        (self.image / 'var/lib/omarchy/provisioning/pending').unlink()
        with self.assertRaisesRegex(ValueError, 'pending'):
            self.configure()
        self.assert_no_install()

    def test_restrictive_umask_public_parents_and_check(self):
        previous = os.umask(0o077)
        try:
            self.configure()
        finally:
            os.umask(previous)
        for name in ('usr/lib/omarchy', 'etc/omarchy', 'etc/systemd/system/multi-user.target.wants'):
            self.assertEqual((self.image / name).stat().st_mode & 0o777, 0o755)
        self.assertEqual(self.configure(apply=False)['changed_files'], [])
        (self.image / 'etc/omarchy').chmod(0o700)
        with self.assertRaisesRegex(ValueError, 'public parent'):
            self.configure(apply=False)

    def test_existing_private_parent_preserved_before_write(self):
        p = self.image / 'etc/omarchy'
        p.mkdir(mode=0o700)
        with self.assertRaisesRegex(ValueError, 'public parent'):
            self.configure()
        self.assertEqual(p.stat().st_mode & 0o777, 0o700)
        self.assert_no_install()

    def test_existing_foreign_owner_refused(self):
        target = self.image / 'usr/lib/omarchy/example'
        put(target, b'qualified')
        original = Path.stat
        def metadata(path, *args, **kwargs):
            result = original(path, *args, **kwargs)
            if path == target:
                fields = list(result)
                fields[4] = os.geteuid() + 1
                return os.stat_result(fields)
            return result
        with mock.patch.object(Path, 'stat', new=metadata):
            with self.assertRaisesRegex(ValueError, 'Foreign image file owner'):
                self.configure()
        self.assertFalse((self.image / installer.MANIFEST).exists())

    def test_shadow_service_refused(self):
        put(self.image / 'etc/systemd/system/speakersafetyd-j713.service', b'old manual service')
        with self.assertRaisesRegex(ValueError, 'Conflicting audio startup'):
            self.configure()
        self.assert_no_install()

    def test_ext4_mount_entry_and_live_root_refusal(self):
        for fstype, target, accepted in [('ext4', str(self.image), True), ('btrfs', str(self.image), False), ('ext4', '/', False)]:
            result = subprocess.CompletedProcess([], 0, json.dumps({'filesystems': [dict(fstype=fstype, target=target)]}))
            with mock.patch.object(installer.subprocess, 'run', return_value=result) as run:
                if accepted:
                    installer.mounted_image_root(self.image)
                else:
                    with self.assertRaisesRegex(ValueError, 'ext4'):
                        installer.mounted_image_root(self.image)
                self.assertEqual(run.call_args.args[0][:3], ['findmnt', '--json', '--target'])
        with self.assertRaisesRegex(ValueError, 'live root'):
            self.configure(root=Path('/'))

    def test_local_distribution_hold(self):
        with self.assertRaisesRegex(ValueError, 'Redistribution'):
            package.assemble(self.image, self.image, self.image / 'out')
        self.assertFalse((self.image / 'out').exists())


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        spec = json.loads((ROOT / 'audio/j713/qualified.json').read_text())
        spec['kernel_release'] = os.uname().release
        self.commands = []
        def command(argv):
            self.commands.append(argv)
            if argv[0] == 'modinfo':
                return os.uname().release + ' SMP preempt mod_unload aarch64'
            return 'inactive'
        self.r = registration.Registration(spec, self.root, command, lambda seconds: None)
        put(self.r.dt / 'compatible', b'apple,j713\0apple,t8132\0')
        (self.root / 'sys/module').mkdir(parents=True)
        self.runtime = self.root / 'run/task'
        self.runtime.mkdir(parents=True)

    def test_preflight_exact_fresh(self):
        self.r.preflight()
        self.assertEqual(sum(c[0] == 'modinfo' for c in self.commands), 7)

    def test_preflight_wrong_board_or_existing_topology(self):
        put(self.r.dt / 'compatible', b'apple,j413\0apple,t8132\0')
        with self.assertRaisesRegex(RuntimeError, 'board'):
            self.r.preflight()
        put(self.r.dt / 'compatible', b'apple,j713\0apple,t8132\0')
        (self.r.dt / 'sound').mkdir()
        with self.assertRaisesRegex(RuntimeError, 'topology'):
            self.r.preflight()
        self.assertEqual(self.commands, [])

    def test_low_all_fields_and_hardware(self):
        path = self.root / 'sys/bus/platform/devices/speaker-group/status'
        def status(values):
            put(path, ' '.join(k + '=' + v for k, v in values.items()).encode())
        status(registration.FRESH_LOW)
        self.assertEqual(self.r.low(), registration.FRESH_LOW)
        for key, value in (('gpio_hw','00070a03'), ('generation','1'), ('sequence','1'), ('sensing','1'), ('faulted','1')):
            values = dict(registration.FRESH_LOW, **{key: value})
            status(values)
            with self.subTest(key=key), self.assertRaisesRegex(RuntimeError, 'fresh LOW'):
                self.r.low()

    def test_stage4_does_not_replace_provider_binding(self):
        self.r.module = lambda name: None
        put(self.root / 'sys/module/j713_audio_bootstrap/parameters/result', b'0')
        put(self.root / 'sys/module/j713_audio_bootstrap/parameters/stage', b'4')
        put(self.root / 'sys/module/j713_audio_bootstrap/parameters/overlay_id', b'1')
        with self.assertRaises((RuntimeError, FileNotFoundError)):
            self.r.apply()
        loaded = [c[1] for c in self.commands if c[0] == 'insmod']
        self.assertEqual(len(loaded), 6)
        self.assertFalse(any('dt3' in p for p in loaded))
        self.assertEqual(self.commands[0][-1], 'invalidate')

    def _complete_registration_population_order(self, delayed=False):
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
            if argv[0] == 'modinfo':
                return os.uname().release + ' SMP preempt mod_unload aarch64'
            if argv[0] == 'systemctl':
                return 'inactive'
            if argv[0] == 'insmod':
                name = next(n for n, e in self.r.manifest['module_artifacts'].items() if e['path'] == argv[1])
                base = self.root / 'sys/module' / name
                put(base / 'initstate', b'live')
                put(base / 'notes/.note.gnu.build-id', bytes.fromhex(self.r.manifest['module_artifacts'][name]['note']))
                if name == 'j713_audio_bootstrap':
                    for key, val in dict(result='0', stage='4', overlay_id='1').items():
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
                    for key, val in dict(result='0', overlay_id='2').items():
                        put(base / 'parameters' / key, val.encode())
                    platform('speaker-group', 'j713-speaker-group', 'snd_soc_j713_group')
                    put(self.root / 'sys/bus/platform/devices/speaker-group/status',
                        ' '.join(k + '=' + v for k, v in registration.FRESH_LOW.items()).encode())
                    target = self.root / 'sys/bus/i2c/drivers/tas2764'
                    target.mkdir(parents=True)
                    (target / 'module').symlink_to(self.root / 'sys/module/snd_soc_tas2764')
                    for addr in registration.ADDRESSES:
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
                        put(self.root / ('proc/asound/AppleJ713/pcm0' + direction + '/sub0/status'), b'closed')
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
            registration.register(self.r, self.runtime, dict(boot='fixture'))
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

    def test_provider_wait_timeout_and_wrong_driver_refuse(self):
        sleeps = []
        self.r.sleep = sleeps.append
        with self.assertRaisesRegex(RuntimeError, 'Provider readiness timed out'):
            self.r.wait_providers()
        self.assertEqual(sleeps, [.1] * 100)
        self.assertEqual(self.commands, [])
        base = self.root / 'sys/bus/platform/devices/388284000.power-management:power-controller@20'
        base.mkdir(parents=True)
        wrong = self.root / 'sys/bus/platform/drivers/foreign'
        wrong.mkdir(parents=True)
        (base / 'driver').symlink_to(wrong)
        sleeps.clear()
        with self.assertRaisesRegex(RuntimeError, 'Provider binding'):
            self.r.wait_providers()
        self.assertEqual(sleeps, [])
        self.assertEqual(self.commands, [])

    def test_attempt_failure_retained_and_never_retried(self):
        self.r.apply = mock.Mock(side_effect=RuntimeError('provider absent'))
        with self.assertRaisesRegex(RuntimeError, 'provider absent'):
            registration.register(self.r, self.runtime, dict(boot='test'))
        self.assertTrue((self.runtime / 'attempt.json').exists())
        self.assertTrue((self.runtime / 'failure.json').exists())
        with self.assertRaisesRegex(RuntimeError, 'already attempted'):
            registration.register(self.r, self.runtime, dict(boot='test'))
        self.r.apply.assert_called_once()
        self.assertFalse((self.runtime / 'registered.json').exists())

    def test_no_latch_on_preflight_refusal(self):
        (self.root / 'sys/module/snd_soc_macaudio').mkdir()
        with self.assertRaisesRegex(RuntimeError, 'already present'):
            registration.register(self.r, self.runtime, {})
        self.assertFalse((self.runtime / 'attempt.json').exists())


if __name__ == '__main__':
    unittest.main()
