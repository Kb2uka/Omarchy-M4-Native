# SPDX-License-Identifier: MIT
# Copyright (c) 2026 studioxradio aka kb2uka
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import stat
import struct
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

spec = importlib.util.spec_from_file_location('widevine', Path(os.environ.get(
    'WIDEVINE_SOURCE', Path(__file__).resolve().parents[1] / 'tools/setup-brave-widevine.py')))
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)


class Response(io.BytesIO):
    status = 200

    def geturl(self):
        return w.URL


class WidevineTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.proc = self.home / 'proc'
        self.proc.mkdir()
        self.component = self.home / '.local/share/omarchy/widevine' / w.VERSION
        self.profiles = {}
        self.state = {'brave': {'unrelated': [1, True], 'widevine_opted_in': False},
                      'owner_pref': {'text': 'keep π', 'default': 42}}
        for name in ('Brave-Browser', 'Brave-Browser-M4'):
            profile = self.home / '.config/BraveSoftware' / name
            profile.mkdir(parents=True)
            for directory in (profile, *profile.parents):
                directory.chmod(0o700)
                if directory == self.home:
                    break
            (profile / 'Local State').write_text(json.dumps(self.state))
            (profile / 'Local State').chmod(0o600)
            self.profiles[name] = profile
        elf = b'\x7fELF\x02\x01\x01' + bytes(9) + struct.pack('<HH', 3, 183) + b'fixture-only'
        self.files = {'LICENSE': b'fixture terms', 'manifest.json': json.dumps({
            'version': w.VERSION, 'accept_arch': ['arm64', 'aarch64']}).encode(),
            w.LIBRARY: elf, '_metadata/verified_contents.json': b'{"fixture":true}'}
        self.addCleanup(patch.stopall)
        patch.object(w.Path, 'home', return_value=self.home).start()
        patch.object(w.platform, 'system', return_value='Linux').start()
        patch.object(w.platform, 'machine', return_value='aarch64').start()
        patch.dict(os.environ, {'XDG_CONFIG_HOME': '', 'XDG_DATA_HOME': ''}).start()
        for key in ('CHROME_CONFIG_HOME', 'CHROME_USER_DATA_DIR'):
            os.environ.pop(key, None)
        original_closed = w.browser_closed
        self.closed = patch.object(w, 'browser_closed', side_effect=lambda p, **kw: original_closed(p, self.proc, **kw)).start()
        self.payload = self.archive()
        self.response_url = w.URL
        test = self

        class Payload(Response):
            def geturl(self):
                return test.response_url

        self.opener = patch.object(w.urllib.request, 'build_opener').start().return_value
        self.opener.open.side_effect = lambda *a, **kw: Payload(self.payload)
        self.pins()

    def archive(self, entries=None):
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w') as archive:
            for directory in sorted(w.DIRECTORIES):
                archive.writestr(directory, b'')
            for name, content in self.files.items():
                archive.writestr(name, content)
            for entry, content in entries or []:
                archive.writestr(entry, content)
        return b'Cr24\x03\x00\x00\x00\x00\x00\x00\x00' + out.getvalue()

    def pins(self):
        patch.object(w, 'ARCHIVE_SIZE', len(self.payload)).start()
        patch.object(w, 'ARCHIVE_SHA512', hashlib.sha512(self.payload).hexdigest()).start()
        patch.object(w, 'FILES', {n: hashlib.sha256(b).hexdigest() for n, b in self.files.items()}).start()

    def invoke(self, name='Brave-Browser', error=None, extra=()):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as errors:
            if error:
                with self.assertRaises(SystemExit) as caught:
                    w.main(['--profile', name, *extra])
                self.assertEqual(caught.exception.code, 1)
                self.assertIn(error, errors.getvalue())
            else:
                w.main(['--profile', name, *extra])

    def process(self, executable='/opt/brave-bin/brave'):
        p = self.proc / '1234'
        p.mkdir()
        (p / 'status').write_text('Name:\tbrave\nUid:\t' + '\t'.join([str(os.getuid())] * 4) + '\n')
        (p / 'exe').symlink_to(executable)
        # The observed browser rewrites its command line. The guard uses exe instead.
        (p / 'cmdline').write_bytes(b'/opt/brave-bin/brave --user-data-dir=flattened\0')

    def test_cli_download_registers_only_selected_profile_and_pref_key(self):
        other = (self.profiles['Brave-Browser-M4'] / 'Local State').read_bytes()
        original = (self.profiles['Brave-Browser'] / 'Local State').read_bytes()
        self.invoke()
        expected = json.loads(original)
        expected['brave']['widevine_opted_in'] = True
        self.assertEqual(json.loads((self.profiles['Brave-Browser'] / 'Local State').read_bytes()), expected)
        self.assertEqual((self.profiles['Brave-Browser-M4'] / 'Local State').read_bytes(), other)
        marker = self.profiles['Brave-Browser'] / 'WidevineCdm/latest-component-updated-widevine-cdm'
        self.assertEqual(json.loads(marker.read_text()), {'Path': str(self.component)})
        for name, data in self.files.items():
            self.assertEqual((self.component / name).read_bytes(), data)
        backups = list(self.profiles['Brave-Browser'].glob('Local State.before-widevine-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertEqual(stat.S_IMODE(backups[0].stat().st_mode), 0o600)
        self.opener.open.assert_called_once_with(w.URL, timeout=30)

    def test_repeat_is_idempotent_and_cached_component_serves_second_profile(self):
        self.invoke()
        snapshot = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.home.rglob('*') if p.is_file()}
        self.invoke()
        self.assertEqual(snapshot, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in snapshot})
        self.invoke('Brave-Browser-M4')
        self.assertEqual(self.opener.open.call_count, 1)

    def test_standard_xdg_profile_and_fixed_m4_profile(self):
        config = self.home / 'custom-config'
        target = config / 'BraveSoftware/Brave-Browser'
        target.mkdir(parents=True)
        for directory in (target, *target.parents):
            directory.chmod(0o700)
            if directory == self.home:
                break
        (target / 'Local State').write_text(json.dumps(self.state))
        (target / 'Local State').chmod(0o600)
        with patch.dict(os.environ, {'XDG_CONFIG_HOME': str(config)}):
            self.invoke()
            self.invoke('Brave-Browser-M4')
        self.assertTrue((target / 'WidevineCdm/latest-component-updated-widevine-cdm').is_file())
        self.assertFalse((self.profiles['Brave-Browser'] / 'WidevineCdm').exists())

    def test_download_bad_hash_refuses_before_registration(self):
        with patch.object(w, 'ARCHIVE_SHA512', '0' * 128):
            self.invoke(error='SHA-512 mismatch')
        self.assertFalse(self.component.exists())

    def test_new_storage_is_private_with_permissive_caller_umask(self):
        previous = os.umask(0o002)
        try:
            self.invoke()
        finally:
            os.umask(previous)
        for directory in self.component.parents:
            if directory == self.home:
                break
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)

    def test_download_truncated_and_oversize_refused(self):
        original = self.payload
        for payload in (original[:-1], original + b'x'):
            with self.subTest(size=len(payload)):
                self.payload = payload
                self.invoke(error='size')
                self.assertFalse(self.component.exists())

    def test_download_redirect_refused(self):
        self.response_url = 'http://example.invalid/component'
        self.invoke(error='Unexpected download response')
        with self.assertRaisesRegex(ValueError, 'redirect refused'):
            w.NoRedirect().redirect_request(None, None, 302, '', {}, self.response_url)

    def test_archive_unknown_path_traversal_and_duplicate_refused(self):
        for name in ('extra', '../escape', '/absolute', 'LICENSE'):
            with self.subTest(name=name):
                self.payload = self.archive([(name, self.files[name] if name in self.files else b'wrong')])
                self.pins()
                self.invoke(error='archive member')
                self.assertFalse(self.component.exists())

    def test_archive_symlink_refused(self):
        entry = zipfile.ZipInfo('LICENSE')
        entry.create_system = 3
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        original = self.files.pop('LICENSE')
        self.payload = self.archive([(entry, original)])
        self.files['LICENSE'] = original
        self.pins()
        self.invoke(error='links/special/encrypted')

    def test_archive_member_hash_refused(self):
        wrong = dict(w.FILES, LICENSE='0' * 64)
        with patch.object(w, 'FILES', wrong):
            self.invoke(error='member hash mismatch')

    def test_manifest_and_elf_architecture_refused(self):
        original = dict(self.files)
        for field in ('manifest', 'elf'):
            with self.subTest(field=field):
                self.files = dict(original)
                if field == 'manifest':
                    self.files['manifest.json'] = b'{"version":"4.10.3057.0","accept_arch":["x64"]}'
                else:
                    elf = bytearray(self.files[w.LIBRARY]); struct.pack_into('<H', elf, 18, 62)
                    self.files[w.LIBRARY] = bytes(elf)
                self.payload = self.archive(); self.pins()
                self.invoke(error='architecture mismatch' if field == 'manifest' else 'ARM64 shared object')

    def test_alive_flattened_or_deleted_brave_refused_before_download(self):
        self.process()
        self.invoke(error='Close all Brave processes')
        (self.proc / '1234/exe').unlink()
        (self.proc / '1234/exe').symlink_to('/opt/brave-bin/brave (deleted)')
        self.invoke(error='Close all Brave processes')
        self.opener.open.assert_not_called()

    def test_singleton_lock_and_symlink_profile_refused(self):
        lock = self.profiles['Brave-Browser'] / 'SingletonLock'
        lock.symlink_to('host-1234')
        self.invoke(error='SingletonLock')
        lock.unlink()
        original = self.profiles['Brave-Browser']
        moved = original.with_name('custom-owner-profile')
        original.rename(moved)
        original.symlink_to(moved, target_is_directory=True)
        self.invoke(error='Unsafe owner path')
        self.opener.open.assert_not_called()

    def test_conflicting_registration_and_bad_component_never_overwritten(self):
        self.invoke()
        marker = self.profiles['Brave-Browser'] / 'WidevineCdm/latest-component-updated-widevine-cdm'
        original = marker.read_bytes()
        marker.write_text('{"Path":"/existing/owner/component"}')
        self.invoke(error='Another Widevine registration')
        self.assertEqual(marker.read_text(), '{"Path":"/existing/owner/component"}')
        marker.write_bytes(original)
        library = self.component / w.LIBRARY
        library.write_bytes(b'owner-changed')
        self.invoke(error='Existing Widevine component differs')
        self.assertEqual(library.read_bytes(), b'owner-changed')

    def test_preferences_or_browser_changed_during_download_refused(self):
        original_download = w.download
        def changed(timeout):
            result = original_download(timeout)
            (self.profiles['Brave-Browser'] / 'Local State').write_text('{"changed":true}')
            return result
        with patch.object(w, 'download', side_effect=changed):
            self.invoke(error='Preferences changed during download')
        self.assertFalse(self.component.exists())
        self.assertEqual((self.profiles['Brave-Browser'] / 'Local State').read_text(), '{"changed":true}')

    def test_browser_starts_during_download_refused(self):
        original_download = w.download
        def started(timeout):
            result = original_download(timeout); self.process(); return result
        with patch.object(w, 'download', side_effect=started):
            self.invoke(error='Close all Brave processes')
        self.assertFalse(self.component.exists())

    def test_readable_python_brave_launcher_is_not_a_running_browser(self):
        self.process('/usr/bin/python3.14')
        # A shebang launcher can retain comm=brave until it execs the real browser.
        (self.proc / '1234/cmdline').write_bytes(b'/usr/bin/python3\0/usr/local/bin/brave\0')
        self.invoke()
        self.assertTrue(self.component.is_dir())

    def test_unreadable_unrelated_systemd_exe_does_not_block_cli(self):
        self.process('/usr/lib/systemd/systemd')
        (self.proc / '1234/status').write_text('Name:\tsystemd\nUid:\t' +
                                             '\t'.join([str(os.getuid())] * 4) + '\n')
        (self.proc / '1234/cmdline').write_bytes(b'/usr/lib/systemd/systemd\0--user\0')
        original = Path.readlink
        def readlink(path):
            if path == self.proc / '1234/exe':
                raise PermissionError('non-dumpable systemd --user')
            return original(path)
        with patch.object(Path, 'readlink', readlink):
            self.invoke()
        self.assertTrue(self.component.is_dir())

    def test_unreadable_brave_exe_still_refuses_name_or_flattened_command(self):
        self.process()
        original = Path.readlink
        def readlink(path):
            if path == self.proc / '1234/exe':
                raise PermissionError('non-dumpable Brave')
            return original(path)
        with patch.object(Path, 'readlink', readlink):
            self.invoke(error='Brave process is closed')
            (self.proc / '1234/status').write_text('Name:\tChrome_ChildIOT\nUid:\t' +
                                                 '\t'.join([str(os.getuid())] * 4) + '\n')
            self.invoke(error='Brave process is closed')
        self.opener.open.assert_not_called()

    def fresh(self, name='Brave-Browser', absent=True):
        profile = self.profiles[name]
        (profile / 'Local State').unlink()
        if absent:
            profile.rmdir()
        return profile

    def test_initialize_missing_and_empty_profiles_then_idempotence(self):
        for name, absent in (('Brave-Browser', True), ('Brave-Browser-M4', False)):
            with self.subTest(name=name):
                profile = self.fresh(name, absent)
                self.invoke(name, extra=['--initialize', '--timeout', '15'])
                self.assertEqual(json.loads((profile / 'Local State').read_text()),
                                 {'brave': {'widevine_opted_in': True}})
                self.assertEqual(stat.S_IMODE(profile.stat().st_mode), 0o700)
                snapshot = {p: (p.read_bytes(), p.stat().st_mtime_ns)
                            for p in profile.rglob('*') if p.is_file()}
                self.invoke(name, extra=['--initialize'])
                self.assertEqual(snapshot, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in snapshot})
        self.opener.open.assert_called_once_with(w.URL, timeout=15)

    def test_default_still_refuses_missing_or_empty_profile(self):
        for name, absent in (('Brave-Browser', True), ('Brave-Browser-M4', False)):
            profile = self.fresh(name, absent)
            self.invoke(name, error='Required path is missing')
            self.assertFalse((profile / 'Local State').exists())
        self.opener.open.assert_not_called()

    def test_initialize_offline_or_bad_archive_leaves_no_profile_state(self):
        profile = self.fresh()
        self.opener.open.side_effect = OSError('offline fixture')
        self.invoke(extra=['--initialize', '--timeout', '15'], error='offline fixture')
        self.assertFalse(profile.exists())
        self.assertFalse(self.component.exists())
        self.opener.open.side_effect = lambda *a, **kw: Response(self.payload)
        with patch.object(w, 'ARCHIVE_SHA512', '0' * 128):
            self.invoke(extra=['--initialize'], error='SHA-512 mismatch')
        self.assertFalse(profile.exists())

    def test_initialize_refuses_populated_stateless_or_changed_profile(self):
        profile = self.fresh(absent=False)
        sentinel = profile / 'Preferences'
        sentinel.write_text('owner-data')
        sentinel.chmod(0o600)
        self.invoke(extra=['--initialize'], error='absent or empty profile')
        self.assertEqual(sentinel.read_text(), 'owner-data')
        self.opener.open.assert_not_called()
        sentinel.unlink()
        original = w.download
        def changed(timeout):
            data = original(timeout)
            sentinel.write_text('created-during-download')
            return data
        with patch.object(w, 'download', changed):
            self.invoke(extra=['--initialize'], error='absent or empty profile')
        self.assertFalse((profile / 'Local State').exists())
        self.assertFalse(self.component.exists())

    def test_initialize_keeps_existing_preferences_and_checks_browser_after_download(self):
        self.invoke(extra=['--initialize'])
        state = json.loads((self.profiles['Brave-Browser'] / 'Local State').read_text())
        self.assertEqual(state['owner_pref'], self.state['owner_pref'])
        profile = self.fresh('Brave-Browser-M4')
        self.process()
        self.invoke('Brave-Browser-M4', extra=['--initialize'], error='Close all Brave processes')
        self.assertFalse(profile.exists())

    def test_initialize_refuses_browser_start_during_download_before_state_creation(self):
        profile = self.fresh()
        original = w.download
        def started(timeout):
            data = original(timeout)
            self.process()
            return data
        with patch.object(w, 'download', started):
            self.invoke(extra=['--initialize'], error='Close all Brave processes')
        self.assertFalse(profile.exists())
        self.assertFalse(self.component.exists())

    def test_timeout_interrupts_blocked_connect_and_read_without_state(self):
        profile = self.fresh()
        previous = signal.getsignal(signal.SIGALRM)
        for phase in ('connect', 'read'):
            with self.subTest(phase=phase):
                def blocked(*args, **kwargs):
                    time.sleep(2)
                    return Response(self.payload)
                class SlowResponse(Response):
                    def read(inner, size):
                        time.sleep(2)
                        return super().read(size)
                self.opener.open.side_effect = (blocked if phase == 'connect' else
                                                lambda *a, **kw: SlowResponse(self.payload))
                start = time.monotonic()
                self.invoke(extra=['--initialize', '--timeout', '1'], error='time limit')
                self.assertLess(time.monotonic() - start, 1.8)
                self.assertFalse(profile.exists())
                self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))
                self.assertEqual(signal.getsignal(signal.SIGALRM), previous)

    def test_timeout_monotonic_limit_and_argument_validation(self):
        with patch.object(w.time, 'monotonic', side_effect=[100, 100, 116]):
            self.invoke(extra=['--timeout', '15'], error='time limit')
        for value in ('0', '-1', '181', '1.5', 'invalid'):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                w.main(['--profile', 'Brave-Browser', '--timeout', value])
            self.assertEqual(caught.exception.code, 2)
        self.assertFalse(self.component.exists())

    def test_bootstrap_dispatches_real_setup_offline_then_success_then_no_work(self):
        spec = importlib.util.spec_from_file_location('bootstrap_entry', Path(__file__).resolve().parents[1] /
                                                     'tools/bootstrap-brave-widevine.py')
        bootstrap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bootstrap)
        profile = self.fresh()
        marker = self.home / '.config/omarchy/pending/widevine-Brave-Browser.json'
        marker.parent.mkdir(parents=True)
        for directory in (marker.parent, *marker.parents):
            directory.chmod(0o700)
            if directory == self.home:
                break
        marker.write_text(json.dumps({'schema': 1, 'profile': 'Brave-Browser', 'consent': 'owner-requested'}))
        marker.chmod(0o600)
        raw = marker.read_bytes()
        def actual_setup(command, **kwargs):
            try:
                w.main(command[2:])
                return subprocess.CompletedProcess(command, 0)
            except SystemExit as error:
                return subprocess.CompletedProcess(command, error.code)
        with patch.object(bootstrap.subprocess, 'run', side_effect=actual_setup) as child:
            self.opener.open.side_effect = OSError('offline fixture')
            with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                self.assertEqual(bootstrap.main(['--profile', 'Brave-Browser']), 1)
            self.assertEqual(marker.read_bytes(), raw)
            self.assertFalse(profile.exists())
            self.opener.open.side_effect = lambda *a, **kw: Response(self.payload)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(bootstrap.main(['--profile', 'Brave-Browser']), 0)
                self.assertEqual(bootstrap.main(['--profile', 'Brave-Browser']), 0)
            self.assertEqual(child.call_count, 2)
        self.assertFalse(marker.exists())
        self.assertTrue(json.loads((profile / 'Local State').read_text())['brave']['widevine_opted_in'])
        self.assertEqual(self.opener.open.call_count, 2)

    def chromium(self, existing=True):
        profile = self.home / '.config/chromium'
        profile.mkdir(mode=0o700)
        if existing:
            (profile / 'Local State').write_bytes(b'{"chromium_owner": true, "brave": "unrelated"}\n')
            (profile / 'Local State').chmod(0o600)
        return profile

    def test_chromium_registers_shared_cdm_without_brave_preference_or_state_rewrite(self):
        self.invoke()
        profile = self.chromium()
        state = profile / 'Local State'
        before = state.read_bytes(), state.stat().st_mtime_ns
        self.invoke('Chromium')
        self.assertEqual((state.read_bytes(), state.stat().st_mtime_ns), before)
        marker = profile / 'WidevineCdm/latest-component-updated-widevine-cdm'
        self.assertEqual(json.loads(marker.read_text()), {'Path': str(self.component)})
        self.assertEqual(list(profile.glob('Local State.before-*')), [])
        self.invoke('Chromium')
        self.assertEqual(self.opener.open.call_count, 1)

    def test_chromium_initialization_and_xdg_profile(self):
        config = self.home / 'alternate-config'
        with patch.dict(os.environ, {'XDG_CONFIG_HOME': str(config)}):
            self.invoke('Chromium', extra=['--initialize'])
        profile = config / 'chromium'
        self.assertEqual((profile / 'Local State').read_bytes(), b'{}\n')
        self.assertTrue((profile / 'WidevineCdm/latest-component-updated-widevine-cdm').exists())
        self.assertFalse((config / 'BraveSoftware').exists())

    def test_chromium_custom_profile_environment_refuses_before_download(self):
        for name in ('CHROME_CONFIG_HOME', 'CHROME_USER_DATA_DIR'):
            with patch.dict(os.environ, {name: str(self.home / 'custom')}):
                self.invoke('Chromium', extra=['--initialize'], error='requires manual registration')
        self.opener.open.assert_not_called()
        self.assertFalse((self.home / '.config/chromium').exists())

    def test_chromium_process_refusal_is_scoped_and_catches_unreadable_flattened_child(self):
        self.chromium()
        self.process('/opt/brave-bin/brave')
        self.invoke('Chromium')
        executable = self.proc / '1234/exe'
        executable.unlink()
        executable.symlink_to('/usr/lib/chromium/chromium')
        (self.proc / '1234/status').write_text('Name:\tchromium\nUid:\t' +
                                             '\t'.join([str(os.getuid())] * 4) + '\n')
        (self.proc / '1234/cmdline').write_bytes(b'/usr/lib/chromium/chromium --type=renderer\0')
        self.invoke('Chromium', error='Close all Chromium processes')
        original = Path.readlink
        def readlink(path):
            if path == executable:
                raise PermissionError('non-dumpable Chromium')
            return original(path)
        with patch.object(Path, 'readlink', readlink):
            self.invoke('Chromium', error='Cannot verify Chromium process')
        self.assertEqual(self.opener.open.call_count, 1)

    def test_chromium_existing_conflicting_registration_is_preserved(self):
        profile = self.chromium()
        marker = profile / 'WidevineCdm/latest-component-updated-widevine-cdm'
        marker.parent.mkdir(mode=0o700)
        marker.write_text('{"Path":"/existing/cdm"}')
        marker.chmod(0o600)
        self.invoke('Chromium', error='Another Widevine registration')
        self.assertEqual(marker.read_text(), '{"Path":"/existing/cdm"}')
        self.opener.open.assert_not_called()

    def test_root_and_wrong_platform_refused(self):
        with patch.object(w.os, 'getuid', return_value=0):
            self.invoke(error='without sudo')
        with patch.object(w.platform, 'machine', return_value='x86_64'):
            self.invoke(error='Linux ARM64')
        self.opener.open.assert_not_called()

    def test_cli_requires_explicit_known_profile(self):
        for arguments in ([], ['--profile', '../another-profile']):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                w.main(arguments)
            self.assertEqual(caught.exception.code, 2)
        self.opener.open.assert_not_called()


if __name__ == '__main__':
    unittest.main()
