# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
GETTY = '''[Unit]
Description=Getty on %I
After=systemd-user-sessions.service
[Service]
ExecStart=-/usr/bin/agetty --noreset --noclear - ${TERM}
StandardInput=tty
TTYPath=/dev/%I
[Install]
Alias=autovt@.service
WantedBy=getty.target
DefaultInstance=tty1
'''
GPU_GUARD = '[Unit]\nRequires=m4-gpu.service seatd.service\nAfter=m4-gpu.service seatd.service\n'
AUTOVT = 'etc/systemd/system/autovt@.service'
TTY2 = 'etc/systemd/system/getty.target.wants/getty@tty2.service'
OBSERVER = 'usr/lib/systemd/system/m4-boot-status.service'
STATUS_LOG = 'var/log/omarchy-m4-boot-status.json'
PENDING = 'var/lib/omarchy/provisioning/pending'


def module(filename):
    spec = importlib.util.spec_from_file_location(filename.replace('-', '_'), ROOT / 'tools' / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class FirstBootConfigTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / 'image'
        self.root.mkdir()
        self.write('etc/m4-root-id', 'root-identity\n')
        self.write(PENDING, '')
        self.write('usr/lib/systemd/system/getty@.service', GETTY)
        self.write('etc/systemd/system/sddm.service.d/90-m4-staging.conf', GPU_GUARD)
        self.write('etc/systemd/system/getty.target.wants/.keep', '')
        (self.root / 'etc/systemd/system/getty.target.wants/getty@tty1.service').symlink_to(
            '/usr/lib/systemd/system/getty@.service')

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def snapshot(self):
        return {str(p.relative_to(self.root)): ('link', os.readlink(p)) if p.is_symlink()
                else ('file', p.read_bytes(), stat.S_IMODE(p.stat().st_mode))
                for p in self.root.rglob('*') if p.is_symlink() or p.is_file()}

    def apply(self):
        # Older image builders supplied no recovery configuration at this seam.
        # Keeping that fixture unchanged reproduces their missing-console state.
        if (ROOT / 'tools/configure-j713-first-boot.py').is_file():
            return module('configure-j713-first-boot.py').apply(self.root, ROOT)

    def test_repairs_missing_automatic_console_alias(self):
        self.apply()
        self.assertTrue((self.root / AUTOVT).is_symlink(), 'Automatic console template is missing')
        self.assertEqual(os.readlink(self.root / AUTOVT), '/usr/lib/systemd/system/getty@.service')

    def test_enables_authenticated_tty2_without_changing_gpu_guard(self):
        self.apply()
        self.assertTrue((self.root / TTY2).is_symlink(), 'Independent tty2 login is missing')
        self.assertEqual(os.readlink(self.root / TTY2), '/usr/lib/systemd/system/getty@.service')
        self.assertEqual((self.root / 'usr/lib/systemd/system/getty@.service').read_text(), GETTY)
        self.assertEqual((self.root / 'etc/systemd/system/sddm.service.d/90-m4-staging.conf').read_text(), GPU_GUARD)

    def test_installs_observer_after_handoff_without_requiring_failed_units(self):
        self.apply()
        text = (self.root / OBSERVER).read_text()
        self.assertIn('After=omarchy-provision-owner.service display-manager.service', text)
        self.assertIn('ConditionPathExists=!/' + PENDING, text)
        self.assertNotIn('Requires=', text)
        self.assertNotIn('StandardOutput=tty', text)
        self.assertNotIn('TTYReset=', text)
        link = self.root / 'etc/systemd/system/graphical.target.wants/m4-boot-status.service'
        self.assertEqual(os.readlink(link), '/' + OBSERVER)
        for name in ('m4-gpu', 'sddm'):
            failure = self.root / ('etc/systemd/system/' + name + '.service.d/95-m4-boot-status.conf')
            self.assertEqual(failure.read_text(), '[Unit]\nOnFailure=m4-boot-status.service\n')
        self.assertEqual((self.root / 'usr/lib/omarchy/m4-boot-status.py').read_bytes(),
                         (ROOT / 'tools/m4-boot-status.py').read_bytes())

    def test_second_application_is_idempotent(self):
        self.apply()
        before = self.snapshot()
        self.apply()
        self.assertEqual(self.snapshot(), before)

    def test_setup_waits_for_hardware_jobs_without_requiring_success(self):
        self.apply()
        path = self.root / 'etc/systemd/system/omarchy-provision-owner.service.d/95-m4-console.conf'
        text = path.read_text()
        self.assertIn('After=m4-gpu.service m4-video.service m4-j713-audio-register.service', text)
        self.assertIn('Environment=TERM=linux', text)
        self.assertNotIn('Requires=', text)
        self.assertNotIn('Wants=', text)

    def test_setup_quiets_kernel_console_and_restores_it_on_success_or_failure(self):
        self.apply()
        text = (self.root / 'etc/systemd/system/omarchy-provision-owner.service.d/95-m4-console.conf').read_text()
        self.assertIn('ExecStartPre=-/usr/bin/dmesg --console-off', text)
        self.assertIn('ExecStartPost=-/usr/bin/dmesg --console-on', text)
        self.assertIn('ExecStopPost=-/usr/bin/dmesg --console-on', text)
        self.assertNotIn('--clear', text)
        self.assertNotIn('StandardError=null', text)

    def test_combined_boot_order_has_no_hardware_owner_display_cycle(self):
        self.apply()
        # Ordering from the frozen J713 services, combined with the installed
        # drop-ins. Edges point from a prerequisite to the unit it precedes.
        texts = {
            'omarchy-provision-owner.service': 'After=systemd-user-sessions.service omarchy-system-factory-reset-finish.service omarchy-m4-keyring.service\nBefore=display-manager.service getty@tty1.service\n',
            'm4-gpu.service': 'After=systemd-udev-trigger.service\nBefore=display-manager.service\n',
            'm4-video.service': 'After=local-fs.target systemd-udev-trigger.service m4-gpu.service\nBefore=display-manager.service\n',
            'm4-j713-audio-register.service': (ROOT / 'packaging/j713-audio/m4-j713-audio-register.service').read_text(),
            'j713-owner-assets.service': (ROOT / 'packaging/j713-owner-assets.service').read_text(),
            'systemd-user-sessions.service': 'After=j713-owner-assets.service\n',
            'display-manager.service': 'After=systemd-user-sessions.service getty@tty1.service plymouth-quit.service systemd-logind.service m4-gpu.service seatd.service\n',
            'getty@tty1.service': 'After=systemd-user-sessions.service plymouth-quit-wait.service getty-pre.target\nBefore=getty.target\n',
            'getty@tty2.service': 'After=systemd-user-sessions.service plymouth-quit-wait.service getty-pre.target\nBefore=getty.target\n',
            'plymouth-quit.service': 'After=systemd-user-sessions.service\n',
            'plymouth-quit-wait.service': 'After=systemd-user-sessions.service\n',
            'm4-boot-status.service': (self.root / OBSERVER).read_text(),
        }
        texts['omarchy-provision-owner.service'] += (self.root /
            'etc/systemd/system/omarchy-provision-owner.service.d/95-m4-console.conf').read_text()
        edges = {}
        for unit, text in texts.items():
            for line in text.splitlines():
                key, _, value = line.partition('=')
                if key not in ('Before', 'After', 'WantedBy'):
                    continue
                for other in value.split():
                    first, second = (other, unit) if key == 'After' else (unit, other)
                    edges.setdefault(first, set()).add(second)
        def visit(unit, visiting, done):
            self.assertNotIn(unit, visiting, 'Ordering cycle involving ' + unit)
            if unit not in done:
                for other in edges.get(unit, ()):
                    visit(other, visiting | {unit}, done)
                done.add(unit)
        done = set()
        for unit in edges:
            visit(unit, set(), done)
        for unit in ('m4-gpu.service', 'm4-video.service', 'm4-j713-audio-register.service'):
            self.assertIn('omarchy-provision-owner.service', edges[unit])
        self.assertIn('m4-boot-status.service', edges['display-manager.service'])

    def test_foreign_links_and_regular_files_fail_before_any_writes(self):
        for name in (AUTOVT, TTY2):
            for kind in ('link', 'file', 'directory'):
                with self.subTest(name=name, kind=kind):
                    path = self.root / name
                    if kind == 'link':
                        path.symlink_to('/unexpected/target')
                    elif kind == 'directory':
                        path.mkdir()
                    else:
                        path.write_text('custom unit')
                    before = self.snapshot()
                    with self.assertRaises(ValueError):
                        self.apply()
                    self.assertEqual(self.snapshot(), before)
                    path.rmdir() if kind == 'directory' else path.unlink()

    def test_foreign_observer_file_and_symlink_refuse_before_alias_repair(self):
        path = self.write(OBSERVER, 'unexpected unit')
        for symlink in (False, True):
            with self.subTest(symlink=symlink):
                if symlink:
                    path.unlink()
                    path.symlink_to('/missing')
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    self.apply()
                self.assertEqual(self.snapshot(), before)
                self.assertFalse((self.root / AUTOVT).is_symlink())

    def test_symlink_parent_and_non_directory_parent_refuse_without_escape(self):
        path = self.root / 'usr/lib/omarchy'
        outside = self.root.parent / 'outside'
        outside.mkdir()
        for kind in ('link', 'file'):
            with self.subTest(kind=kind):
                if kind == 'link':
                    path.symlink_to(outside)
                else:
                    path.write_text('not a directory')
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    self.apply()
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(list(outside.iterdir()), [])
                path.unlink()

    def test_getty_autologin_override_or_modified_command_refuses(self):
        override = 'etc/systemd/system/getty@tty2.service.d/autologin.conf'
        path = self.write(override, '[Service]\nExecStart=agetty --autologin root\n')
        with self.assertRaises(ValueError):
            self.apply()
        self.assertFalse((self.root / AUTOVT).is_symlink())
        path.unlink()
        self.write('usr/lib/systemd/system/getty@.service', GETTY.replace('--noreset', '--autologin root'))
        with self.assertRaises(ValueError):
            self.apply()

    def test_non_pending_or_filesystem_root_refuses(self):
        (self.root / PENDING).unlink()
        with self.assertRaises(ValueError):
            self.apply()
        with self.assertRaises(ValueError):
            module('configure-j713-first-boot.py').apply(Path('/'), ROOT)


class BootStatusTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.status = module('m4-boot-status.py')
        self.addCleanup(mock.patch.stopall)
        self.states = {
            'display-manager.service': {'ActiveState': 'inactive', 'SubState': 'dead', 'Result': 'success'},
            'omarchy-provision-owner.service': {'ActiveState': 'active', 'SubState': 'exited', 'Result': 'success'},
            'getty@tty1.service': {'ActiveState': 'inactive', 'SubState': 'dead'},
            'm4-gpu.service': {'ActiveState': 'failed', 'SubState': 'failed', 'Result': 'exit-code'},
        }
        self.query = mock.patch.object(self.status, 'service_state',
                                       side_effect=lambda name: self.states.get(name, {'ActiveState': 'inactive', 'SubState': 'dead'})).start()
        self.notice = mock.patch.object(self.status, 'write_console_notice', return_value=True).start()

    def observe(self):
        return self.status.observe(self.root)

    def test_completed_setup_failed_gpu_records_recovery_without_credentials(self):
        self.assertEqual(self.observe()['status'], 'desktop-unavailable')
        self.notice.assert_called_once()
        path = self.root / STATUS_LOG
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertLess(path.stat().st_size, 8192)
        record = json.loads(path.read_bytes())
        self.assertEqual(record['units']['m4-gpu.service']['Result'], 'exit-code')
        self.assertIn('Ctrl+Alt (Option)+F2', self.status.NOTICE)
        self.assertIn('sudo journalctl', self.status.NOTICE)
        self.assertNotIn('root shell', self.status.NOTICE)
        self.status.NOTICE.encode('ascii')
        connect = next(line for line in (ROOT / 'docs/install.md').read_text().splitlines()
                       if line.startswith('curl ') and line.endswith('m4-connect.sh | bash'))
        self.assertIn('  ' + connect + '\n', self.status.NOTICE, 'the notice must give the documented connect line')
        self.assertIn("sudo journalctl -b -k --grep 'G16|asahi' --no-pager", self.status.NOTICE)

    def test_healthy_or_starting_desktop_never_opens_console(self):
        for active in ('active', 'activating', 'reloading', 'unknown'):
            with self.subTest(active=active):
                self.states['display-manager.service']['ActiveState'] = active
                self.observe()
                self.notice.assert_not_called()
                self.assertFalse((self.root / STATUS_LOG).exists())

    def test_pending_form_or_live_owner_shell_never_opens_console(self):
        pending = self.root / PENDING
        pending.parent.mkdir(parents=True)
        pending.touch()
        self.observe()
        self.query.assert_not_called()
        pending.unlink()
        self.states['omarchy-provision-owner.service']['SubState'] = 'running'
        self.observe()
        self.notice.assert_not_called()
        self.assertFalse((self.root / STATUS_LOG).exists())

    def test_tty1_login_is_not_overwritten_and_tty2_is_never_touched(self):
        self.states['getty@tty1.service']['ActiveState'] = 'active'
        self.observe()
        self.notice.assert_not_called()
        self.assertNotIn('getty@tty2.service', [call.args[0] for call in self.query.call_args_list])

    def test_desktop_starting_during_snapshot_prevents_console_write(self):
        calls = 0
        def changing(name):
            nonlocal calls
            if name == 'display-manager.service':
                calls += 1
                if calls > 1:
                    return {'ActiveState': 'active', 'SubState': 'running'}
            return self.states.get(name, {'ActiveState': 'inactive', 'SubState': 'dead'})
        self.query.side_effect = changing
        self.observe()
        self.notice.assert_not_called()

    def test_pending_reappearing_during_snapshot_prevents_console_write(self):
        def changing(name):
            if name == 'm4-gpu.service':
                pending = self.root / PENDING
                pending.parent.mkdir(parents=True)
                pending.touch()
            return self.states.get(name, {'ActiveState': 'inactive', 'SubState': 'dead'})
        self.query.side_effect = changing
        self.observe()
        self.notice.assert_not_called()

    def test_latest_status_replaces_previous_record_without_growing_history(self):
        self.observe()
        self.states['m4-gpu.service']['Result'] = 'timeout'
        self.observe()
        path = self.root / STATUS_LOG
        self.assertEqual(json.loads(path.read_bytes())['units']['m4-gpu.service']['Result'], 'timeout')
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_symlink_log_or_parent_is_not_followed(self):
        outside = self.root / 'outside'
        outside.write_text('keep')
        path = self.root / STATUS_LOG
        path.parent.mkdir(parents=True)
        path.symlink_to(outside)
        with self.assertRaises(ValueError):
            self.observe()
        self.assertEqual(outside.read_text(), 'keep')
        self.notice.assert_not_called()


class StatusBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.status = module('m4-boot-status.py')

    def test_service_snapshot_uses_only_safe_properties_with_timeout(self):
        output = 'ActiveState=failed\nSubState=failed\nResult=exit-code\nExecMainStatus=1\nEnvironment=SECRET\nResult=SECRET value\n'
        with mock.patch.object(self.status.subprocess, 'run',
                               return_value=subprocess.CompletedProcess([], 0, output, 'SECRET')) as run:
            actual = self.status.service_state('m4-gpu.service')
        self.assertEqual(actual, {'ActiveState': 'failed', 'SubState': 'failed', 'Result': 'exit-code', 'ExecMainStatus': '1'})
        self.assertEqual(run.call_args.kwargs['timeout'], 2)
        self.assertNotIn('SECRET', json.dumps(actual))
        self.assertNotIn('Environment', ' '.join(run.call_args.args[0]))

    def test_command_failure_or_timeout_does_not_include_output(self):
        for error in (subprocess.TimeoutExpired(['systemctl'], 2, output='SECRET'), OSError('SECRET')):
            with self.subTest(error=type(error).__name__):
                with mock.patch.object(self.status.subprocess, 'run', side_effect=error):
                    self.assertEqual(self.status.service_state('m4-gpu.service'), {'query': 'unavailable'})

    def test_graphics_mode_and_non_terminal_destinations_are_never_written(self):
        for mode in ('graphics', 'regular'):
            with self.subTest(mode=mode), \
                    mock.patch.object(self.status.os, 'open', return_value=42), \
                    mock.patch.object(self.status.os, 'close'), \
                    mock.patch.object(self.status.os, 'fstat', return_value=mock.Mock(st_mode=stat.S_IFREG if mode == 'regular' else stat.S_IFCHR)), \
                    mock.patch.object(self.status.fcntl, 'ioctl', side_effect=lambda fd, request, value, mutate: value.__setitem__(0, 1)), \
                    mock.patch.object(self.status.os, 'write') as write:
                self.assertFalse(self.status.write_console_notice())
                write.assert_not_called()

    def test_text_console_gets_plain_recovery_message_and_cursor(self):
        with mock.patch.object(self.status.os, 'open', return_value=42) as open_tty, \
                mock.patch.object(self.status.os, 'close'), \
                mock.patch.object(self.status.os, 'fstat', return_value=mock.Mock(st_mode=stat.S_IFCHR)), \
                mock.patch.object(self.status.fcntl, 'ioctl'), \
                mock.patch.object(self.status.os, 'write', side_effect=lambda fd, data: len(data)) as write:
            self.assertTrue(self.status.write_console_notice())
        self.assertEqual(open_tty.call_args.args[0], '/dev/tty1')
        self.assertIn(b'\x1b[?25h', write.call_args.args[1])
        self.assertIn(b'Ctrl+Alt (Option)+F2', write.call_args.args[1])


if __name__ == '__main__':
    unittest.main()
