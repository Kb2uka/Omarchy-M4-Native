# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SOURCE = Path(os.environ.get('M4_BROWSER_SOURCE', Path(__file__).resolve().parents[1] / 'tools/brave-m4-hardware.py'))
spec = importlib.util.spec_from_file_location('m4_browser', SOURCE)
browser = importlib.util.module_from_spec(spec)
spec.loader.exec_module(browser)


class BrowserLaunchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='m4 browser ')
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name)
        (self.home / '.config').mkdir()
        self.env = {'HOME': str(self.home), 'PATH': '/usr/local/bin:/usr/bin'}

    def launch(self, args=(), ready=True, normal=True):
        with patch.object(browser, 'hardware_available', return_value=ready):
            return browser.launch(list(args), self.env, normal_profile=normal)

    def test_stock_desktop_entry_retains_standard_profile(self):
        with patch.dict(os.environ, self.env, clear=True), \
                patch.object(browser.sys, 'argv', ['/usr/local/bin/brave']), \
                patch.object(browser, 'hardware_available', return_value=True), \
                patch.object(browser.os, 'execve') as execute:
            browser.main([])
        binary, argv, env = execute.call_args.args
        self.assertEqual(binary, '/opt/brave-bin/brave')
        self.assertFalse(any(x.startswith('--user-data-dir=') for x in argv),
                         'The stock desktop entry must not switch the owner to a different profile')
        self.assertEqual(env['CHROME_DESKTOP'], 'brave-browser.desktop')

    def test_stock_launcher_keeps_owner_profile_and_requests_hardware(self):
        argv, env, hardware = self.launch(['https://example.com/video'])
        self.assertTrue(hardware)
        self.assertFalse(any(x.startswith('--user-data-dir') for x in argv))
        self.assertIn('AcceleratedVideoDecoder', next(x for x in argv if x.startswith('--enable-features=')))
        self.assertEqual(env['CHROME_DESKTOP'], 'brave-browser.desktop')
        self.assertEqual(env['LIBVA_DRIVER_NAME'], 'v4l2_request')
        self.assertEqual(argv[-1], 'https://example.com/video')

    def test_separate_profile_remains_supported(self):
        argv, env, hardware = self.launch(normal=False)
        self.assertIn('--user-data-dir=' + str(self.home / '.config/BraveSoftware/Brave-Browser-M4'), argv)
        self.assertEqual(env['CHROME_DESKTOP'], 'brave-m4-hardware.desktop')
        self.assertTrue(hardware)

    def test_normal_custom_profile_preserves_spaces(self):
        target = str(self.home / 'owner chosen profile')
        argv, _, _ = self.launch(['--user-data-dir', target, '--incognito'])
        self.assertEqual([x for x in argv if x.startswith('--user-data-dir=')], ['--user-data-dir=' + target])
        self.assertIn('--incognito', argv)
        with self.assertRaises(ValueError):
            self.launch(['--user-data-dir=' + target], normal=False)

    def test_unavailable_hardware_and_user_optout_retain_software(self):
        for args, ready in [([], False), (['--disable-gpu'], True), (['--disable-features=AcceleratedVideoDecoder'], True)]:
            with self.subTest(args=args, ready=ready):
                argv, env, hardware = self.launch(args, ready)
                self.assertFalse(hardware)
                self.assertIn('--disable-accelerated-video-decode', argv)
                self.assertNotIn('--disable-software-video-decoders', argv)
                self.assertNotIn('LIBVA_DRIVER_NAME', env)

    def test_owner_features_extensions_and_software_fallback_survive(self):
        (self.home / '.config/brave-flags.conf').write_text('--enable-features=OwnerFeature\n--disable-features=AnotherFeature\n--load-extension=/owner/extension\n')
        argv, _, _ = self.launch()
        self.assertEqual(len([a for a in argv if a.startswith('--disable-features=')]), 1)
        self.assertIn('OnBeginFrameThrottleVideo', next(a for a in argv if a.startswith('--disable-features=')))
        self.assertIn('AnotherFeature', next(a for a in argv if a.startswith('--disable-features=')))
        self.assertIn('OwnerFeature', next(a for a in argv if a.startswith('--enable-features=')))
        self.assertIn('/owner/extension', next(a for a in argv if a.startswith('--load-extension=')))
        with self.assertRaises(ValueError):
            self.launch(['--disable-software-video-decoders'])

    def test_first_owner_request_is_explicit_and_default_profile_only(self):
        entry = self.home / 'bootstrap.py'
        entry.write_text('')
        marker = self.home / '.config/omarchy/pending/widevine-Brave-Browser.json'
        marker.parent.mkdir(parents=True)
        with patch.object(browser, 'BOOTSTRAP', entry), patch.object(browser.subprocess, 'run') as run:
            browser.owner_media([browser.BINARY], self.env, True)
            run.assert_not_called()
            marker.write_text('{"schema":1,"profile":"Brave-Browser","consent":"owner-requested"}')
            browser.owner_media([browser.BINARY, '--version'], self.env, True)
            browser.owner_media([browser.BINARY, '--user-data-dir=/owner/custom'], self.env, True)
            browser.owner_media([browser.BINARY], {**self.env, 'CHROME_USER_DATA_DIR': '/owner/custom'}, True)
            run.assert_not_called()
            browser.owner_media([browser.BINARY], self.env, True)
            self.assertEqual(run.call_args.args[0], ['/usr/bin/python3', str(entry), '--profile', 'Brave-Browser'])
            self.assertEqual(run.call_args.kwargs['timeout'], 22)
            self.assertFalse(run.call_args.kwargs['check'])

    def test_pending_owner_setup_failure_does_not_prevent_browser_launch(self):
        entry = self.home / 'bootstrap.py'
        entry.write_text('')
        marker = self.home / '.config/omarchy/pending/widevine-Brave-Browser.json'
        marker.parent.mkdir(parents=True)
        marker.write_text('{}')
        with patch.dict(os.environ, self.env, clear=True), \
                patch.object(browser.sys, 'argv', ['/usr/local/bin/brave']), \
                patch.object(browser, 'BOOTSTRAP', entry), \
                patch.object(browser.subprocess, 'run', side_effect=OSError('offline')), \
                patch.object(browser, 'hardware_available', return_value=True), \
                patch.object(browser.os, 'execve') as execute:
            browser.main([])
        execute.assert_called_once()
        self.assertTrue(marker.exists())

    def test_chromium_entry_retains_compiled_launcher_and_normal_profile(self):
        with patch.dict(os.environ, self.env, clear=True), \
                patch.object(browser.sys, 'argv', ['/usr/local/bin/chromium']), \
                patch.object(browser, 'CHROMIUM_SYSTEM_CONFIG', self.home / 'no-system-flags', create=True), \
                patch.object(browser, 'hardware_available', return_value=True), \
                patch.object(browser.os, 'execve') as execute:
            browser.main(['https://example.com/video'])
        binary, argv, env = execute.call_args.args
        self.assertEqual(binary, '/usr/bin/chromium')
        self.assertFalse(any(x.startswith('--user-data-dir=') for x in argv))
        self.assertEqual(env['CHROME_DESKTOP'], 'chromium.desktop')
        self.assertIn('--disable-accelerated-video-decode', argv)
        self.assertNotIn('LIBVA_DRIVER_NAME', env)

    def test_packaged_chromium_does_not_force_unqualified_v4l2_decoder(self):
        with patch.object(browser, 'CHROMIUM_SYSTEM_CONFIG', self.home / 'no-system-flags'), \
                patch.object(browser, 'hardware_available', return_value=True):
            argv, env, hardware = browser.launch(
                ['--enable-features=AcceleratedVideoDecoder,OwnerFeature',
                 '--load-extension=/owner/extension'], self.env,
                normal_profile=True, browser_name='chromium')
        self.assertFalse(hardware)
        self.assertIn('--disable-accelerated-video-decode', argv)
        self.assertNotIn('--disable-gpu', argv)
        self.assertNotIn('LIBVA_DRIVER_NAME', env)
        self.assertEqual(env.get('LD_LIBRARY_PATH'), str(browser.PREFIX / 'mesa/lib'))
        self.assertEqual(env.get('ASAHI_MESA_DEBUG'), 'nocompress')
        self.assertIn('--enable-features=OwnerFeature', argv)
        self.assertIn('--load-extension=/owner/extension', argv)
        self.assertFalse(any(str(browser.SITE_EXTENSION) in arg for arg in argv))

    def test_chromium_system_user_and_cli_flags_merge_with_quoted_profile(self):
        system = self.home / 'system.conf'
        system.write_text('--enable-features=SystemFeature --disable-features=SystemOff\n')
        (self.home / '.config/chromium-flags.conf').write_text(
            '# Chromium uses shell tokenization, without shell execution\n'
            '--enable-features="OwnerFeature<Trial" --disable-features=OwnerOff\n'
            '--user-data-dir="/owner/profile with spaces"\n')
        with patch.object(browser, 'CHROMIUM_SYSTEM_CONFIG', system), \
                patch.object(browser, 'hardware_available', return_value=True):
            argv, env, hardware = browser.launch(['--enable-features=CliFeature'], self.env,
                                                 normal_profile=True, browser_name='chromium')
        self.assertFalse(hardware)
        self.assertIn('--user-data-dir=/owner/profile with spaces', argv)
        feature = next(x for x in argv if x.startswith('--enable-features='))
        for value in ('SystemFeature', 'OwnerFeature<Trial', 'CliFeature'):
            self.assertIn(value, feature)
        self.assertNotIn('AcceleratedVideoDecoder', feature)
        disabled = next(x for x in argv if x.startswith('--disable-features='))
        for value in ('SystemOff', 'OwnerOff', 'OnBeginFrameThrottleVideo'):
            self.assertIn(value, disabled)

    def test_chromium_pending_request_uses_only_its_own_profile(self):
        entry = self.home / 'bootstrap.py'
        entry.write_text('')
        marker = self.home / '.config/omarchy/pending/widevine-Chromium.json'
        marker.parent.mkdir(parents=True)
        marker.write_text('{"schema":1,"profile":"Chromium","consent":"owner-requested"}')
        with patch.object(browser, 'BOOTSTRAP', entry), patch.object(browser.subprocess, 'run') as run:
            browser.owner_media(['/usr/bin/chromium'], self.env, True, 'chromium')
            self.assertEqual(run.call_args.args[0][-1], 'Chromium')
            run.reset_mock()
            browser.owner_media(['/usr/bin/chromium'], {**self.env, 'CHROME_CONFIG_HOME': '/custom'}, True, 'chromium')
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
