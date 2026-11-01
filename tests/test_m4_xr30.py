# SPDX-License-Identifier: MIT
"""Login-only Mesa selection; monitor policy and the live session are untouched."""
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('m4_xr30', ROOT / 'tools/m4-xr30.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
PACKAGE = ROOT / 'packaging/omarchy-m4/omarchy-m4-xr30'
ORIGINAL = {'PATH': '/usr/bin', 'LD_LIBRARY_PATH': '/opt/m4-mesa/lib:/owner/lib',
            'GBM_BACKENDS_PATH': '/owner/gbm', 'LIBGL_DRIVERS_PATH': '/owner/dri',
            '__EGL_VENDOR_LIBRARY_FILENAMES': '/owner/egl.json',
            'VK_DRIVER_FILES': '/owner/vulkan.json', 'KEEP': 'unchanged'}


class Xr30Fixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.tree = self.root / MODULE.DT
        self.node = self.tree / 'chosen/framebuffer@1000'
        self.put(self.tree / 'compatible', b'apple,t8140\0apple,j715\0')
        self.put(self.node / 'compatible', b'simple-framebuffer\0')
        self.put(self.node / 'format', b'x2r10g10b10\0')
        self.card = self.root / MODULE.DRM / 'card0'
        (self.card / 'device').mkdir(parents=True)
        driver = self.root / 'sys/bus/platform/drivers/simple-framebuffer'
        driver.mkdir(parents=True)
        (self.card / 'device/driver').symlink_to(driver)
        (self.card / 'device/of_node').symlink_to(self.node)
        self.connector = self.root / MODULE.DRM / 'card0-Unknown-1'
        self.put(self.connector / 'status', b'connected\n')
        for name in ('lib', 'lib/gbm', 'lib/dri'):
            (self.root / MODULE.PREFIX / name).mkdir(parents=True, exist_ok=True)
        self.put(self.root / MODULE.PREFIX / MODULE.VENDOR, b'{}\n')
        self.hold = self.root / MODULE.STATE / 'hold'
        self.protected = [self.root / path for path in (
            'home/owner/.config/hypr/monitors.lua', 'opt/m4-mesa/lib/libEGL.so',
            'opt/m4-gpu/runtime.sha256')]
        for path in self.protected:
            self.put(path, b'owner content\n')
        launcher = self.root / MODULE.LAUNCHER
        self.put(launcher, (
            f'#!{sys.executable}\nimport json, os, sys\n'
            'print(json.dumps({"arguments": sys.argv[1:], "environment": dict(os.environ)}))\n'
            'sys.exit(int(os.environ.get("FAKE_EXIT", "0")))\n').encode())
        launcher.chmod(0o755)

    def put(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def run_tool(self, *arguments):
        output = io.StringIO()
        with mock.patch('sys.stdout', output), mock.patch('sys.stderr', io.StringIO()):
            code = MODULE.main(['--root', str(self.root), *arguments])
        return code, json.loads(output.getvalue()) if code == 0 else None

    def launch(self, arguments=(), environment=None):
        return subprocess.run(
            [sys.executable, str(ROOT / 'tools/m4-xr30.py'), '--root', str(self.root),
             '--start', *arguments], env=ORIGINAL if environment is None else environment,
            capture_output=True, text=True, timeout=10)

    def assert_bypass(self):
        report = MODULE.inspect(self.root)
        self.assertEqual(report['next_login'], 'original')
        self.assertFalse(report['eligible'])
        self.assertEqual(MODULE.session_environment(ORIGINAL, report), ORIGINAL)


class Xr30Tests(Xr30Fixture):
    def test_exact_board_token_selects_private_prefix(self):
        report = MODULE.inspect(self.root)
        self.assertEqual((report['eligible'], report['panel'], report['next_login']),
                         (True, 'Unknown-1', 'private-mesa'))

    def test_other_boards_and_partial_tokens_preserve_environment(self):
        for value in (b'apple,j713\0', b'apple,j715x\0', b'xapple,j715\0', b'apple,j715'):
            with self.subTest(value=value):
                self.put(self.tree / 'compatible', value)
                self.assert_bypass()

    def test_only_exact_native_xr30_format_qualifies(self):
        for value in (b'x8r8g8b8\0', b'x2b10g10r10\0', b'x2r10g10b10', b'x2r10g10b10x\0'):
            with self.subTest(value=value):
                self.put(self.node / 'format', value)
                self.assert_bypass()

    def test_devicetree_declaration_without_bound_driver_does_not_qualify(self):
        (self.card / 'device/driver').unlink()
        self.assert_bypass()

    def test_unrelated_cards_without_driver_or_node_do_not_block_real_startup(self):
        device = self.root / MODULE.DRM / 'card1/device'
        device.mkdir(parents=True)
        for with_driver in (False, True):
            with self.subTest(with_driver=with_driver):
                if with_driver:
                    (device / 'driver').symlink_to(self.card / 'device/driver')
                result = self.launch(environment={**os.environ, **ORIGINAL})
                self.assertEqual(result.returncode, 0, result.stderr)
                environment = json.loads(result.stdout)['environment']
                self.assertEqual(environment['GBM_BACKENDS_PATH'], '/usr/lib/omarchy-m4/mesa/lib/gbm')

    def test_unreadable_card_link_still_fails_qualification_closed(self):
        driver = self.root / MODULE.DRM / 'card1/device/driver'
        driver.parent.mkdir(parents=True)
        resolve = Path.resolve

        def unreadable(path, *args, **kwargs):
            if path == driver:
                raise PermissionError('unreadable card link')
            return resolve(path, *args, **kwargs)

        with mock.patch.object(Path, 'resolve', unreadable):
            self.assert_bypass()

    def test_another_driver_does_not_qualify(self):
        (self.card / 'device/driver').unlink()
        driver = self.root / 'sys/other-driver'
        driver.mkdir()
        (self.card / 'device/driver').symlink_to(driver)
        self.assert_bypass()

    def test_simple_framebuffer_compatible_is_an_exact_token(self):
        self.put(self.node / 'compatible', b'simple-framebuffer-other\0')
        self.assert_bypass()

    def test_disconnected_or_ambiguous_panel_does_not_qualify(self):
        self.put(self.connector / 'status', b'disconnected\n')
        self.assert_bypass()
        self.put(self.connector / 'status', b'connected\n')
        self.put(self.root / MODULE.DRM / 'card0-Unknown-2/status', b'connected\n')
        self.assert_bypass()

    def test_disabled_framebuffer_does_not_qualify(self):
        self.put(self.node / 'status', b'disabled\0')
        self.assert_bypass()

    def test_non_boot_framebuffer_does_not_qualify(self):
        self.node.rename(self.tree / 'other-framebuffer')
        (self.card / 'device/of_node').unlink()
        (self.card / 'device/of_node').symlink_to(self.tree / 'other-framebuffer')
        self.assert_bypass()

    def test_missing_qualification_data_preserves_environment(self):
        (self.tree / 'compatible').unlink()
        self.assert_bypass()

    def test_missing_prefix_contents_preserve_environment(self):
        for name in ('lib/gbm', 'lib/dri'):
            with self.subTest(name=name):
                (self.root / MODULE.PREFIX / name).rmdir()
                self.assert_bypass()
                (self.root / MODULE.PREFIX / name).mkdir()
        (self.root / MODULE.PREFIX / MODULE.VENDOR).unlink()
        self.assert_bypass()

    def test_hold_and_dangling_hold_preserve_environment(self):
        self.put(self.hold, b'')
        self.assert_bypass()
        self.hold.unlink()
        self.hold.symlink_to(self.root / 'absent')
        self.assert_bypass()

    def test_unreadable_hold_state_preserves_environment(self):
        with mock.patch.object(Path, 'lstat', side_effect=PermissionError('unreadable state')):
            self.assert_bypass()

    def test_selection_changes_loader_variables_and_disables_compression(self):
        before = ORIGINAL.copy()
        environment = MODULE.session_environment(ORIGINAL, MODULE.inspect(self.root))
        prefix = '/usr/lib/omarchy-m4/mesa'
        self.assertEqual(environment, {
            **ORIGINAL, 'LD_LIBRARY_PATH': prefix + '/lib:' + ORIGINAL['LD_LIBRARY_PATH'],
            'GBM_BACKENDS_PATH': prefix + '/lib/gbm', 'LIBGL_DRIVERS_PATH': prefix + '/lib/dri',
            '__EGL_VENDOR_LIBRARY_FILENAMES': prefix + '/share/glvnd/egl_vendor.d/50_mesa.json',
            'ASAHI_MESA_DEBUG': 'nocompress'})
        self.assertEqual(ORIGINAL, before)

    def test_selection_preserves_debug_flags_without_duplicating_nocompress(self):
        for flags, expected in (('trace', 'trace,nocompress'),
                                ('trace,nocompress', 'trace,nocompress'),
                                ('trace nocompress', 'trace nocompress')):
            with self.subTest(flags=flags):
                environment = MODULE.session_environment(
                    {**ORIGINAL, 'ASAHI_MESA_DEBUG': flags}, MODULE.inspect(self.root))
                self.assertEqual(environment['ASAHI_MESA_DEBUG'], expected)

    def test_empty_library_path_does_not_add_current_directory(self):
        for original in ({}, {'LD_LIBRARY_PATH': ''}):
            with self.subTest(original=original):
                environment = MODULE.session_environment(original, MODULE.inspect(self.root))
                self.assertEqual(environment['LD_LIBRARY_PATH'], '/usr/lib/omarchy-m4/mesa/lib')

    def test_controls_are_idempotent_and_only_change_hold(self):
        for flag, selected in (('--disable', 'original'), ('--disable', 'original'),
                               ('--enable', 'private-mesa'), ('--enable', 'private-mesa')):
            with self.subTest(flag=flag):
                code, report = self.run_tool(flag)
                self.assertEqual((code, report['next_login']), (0, selected))
        self.assertEqual([path.read_bytes() for path in self.protected], [b'owner content\n'] * 3)

    def test_enable_does_not_override_hardware_gate(self):
        self.put(self.tree / 'compatible', b'apple,j713\0')
        self.put(self.hold, b'')
        code, report = self.run_tool('--enable')
        self.assertEqual((code, report['eligible']), (0, False))

    def test_disable_creates_globally_readable_state_under_restrictive_umask(self):
        previous = os.umask(0o077)
        try:
            self.run_tool('--disable')
        finally:
            os.umask(previous)
        for path in (self.hold.parent, self.hold.parent.parent):
            self.assertEqual(path.stat().st_mode & 0o777, 0o755)
        self.assertEqual(self.hold.stat().st_mode & 0o777, 0o644)

    def test_live_root_controls_require_root_even_with_equivalent_root_path(self):
        for flag in ('--disable', '--enable'):
            with self.subTest(flag=flag), mock.patch.object(os, 'geteuid', return_value=1000):
                with mock.patch('sys.stderr', io.StringIO()):
                    self.assertEqual(MODULE.main(['--root', '/./', flag]), 1)

    def test_status_is_read_only_and_does_not_create_state(self):
        code, report = self.run_tool('--status')
        self.assertEqual((code, report['next_login']), (0, 'private-mesa'))
        self.assertFalse((self.root / MODULE.STATE).exists())
        self.assertEqual([path.read_bytes() for path in self.protected], [b'owner content\n'] * 3)

    def test_real_startup_preserves_config_arguments_and_launcher_exit_code(self):
        result = self.launch(('--', '--config', '/owner/hyprland.lua'),
                             {**ORIGINAL, 'FAKE_EXIT': '17'})
        self.assertEqual(result.returncode, 17, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['arguments'], ['--', '--config', '/owner/hyprland.lua'])
        self.assertEqual(data['environment']['GBM_BACKENDS_PATH'], '/usr/lib/omarchy-m4/mesa/lib/gbm')
        self.assertEqual([path.read_bytes() for path in self.protected], [b'owner content\n'] * 3)

    def test_real_held_startup_preserves_original_environment(self):
        self.put(self.hold, b'')
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        environment = json.loads(result.stdout)['environment']
        self.assertEqual({key: environment[key] for key in ORIGINAL}, ORIGINAL)
        self.assertNotIn('MESA_GL_VERSION_OVERRIDE', environment)

    def test_missing_launcher_reports_failure(self):
        (self.root / MODULE.LAUNCHER).unlink()
        self.assertEqual(self.run_tool('--start')[0], 1)

    def test_dropin_keeps_uwsm_and_replaces_only_execstart(self):
        directives = [line for line in (PACKAGE / '90-m4-xr30.conf').read_text().splitlines()
                      if line and not line.startswith(('#', '['))]
        self.assertEqual(directives[0], 'ExecStart=')
        self.assertEqual([line.split('=', 1)[0] for line in directives], ['ExecStart', 'ExecStart'])
        self.assertEqual(shlex.split(directives[1].split('=', 1)[1]),
                         ['/usr/bin/uwsm', 'aux', 'exec', '--', '%I', '/usr/bin/m4-xr30', '--start'])


class FormatQuirkTests(Xr30Fixture):
    def setUp(self):
        super().setUp()
        self.home = self.root / 'home/owner'
        self.config = self.home / '.config/hypr/hyprland.lua'
        self.looknfeel = self.config.with_name('looknfeel.lua')
        self.owner_config = ('require("default.hypr.omarchy")\n'
                             'require("hypr.looknfeel")\n'
                             'require("hypr.monitors")\n')
        self.owner_look = ('-- Owner appearance\n'
                           'hl.config({ decoration = { rounding = 7 } })\n')
        self.put(self.config, self.owner_config.encode())
        self.put(self.looknfeel, self.owner_look.encode())
        self.put(self.root / 'etc/passwd', (
            'root:x:0:0::/root:/bin/bash\n'
            'owner:x:1000:1000::/home/owner:/bin/bash\n'
            'guest:x:1001:1001::/home/guest:/usr/bin/nologin\n').encode())

    def test_append_is_idempotent_and_preserves_owner_bytes_and_metadata(self):
        self.looknfeel.chmod(0o640)
        before = self.looknfeel.stat()
        self.assertEqual(MODULE.add_format_quirk(self.home, False), 'missing')
        self.assertEqual(self.looknfeel.read_text(), self.owner_look)
        self.assertTrue(MODULE.add_format_quirk(self.home, True).startswith('added'))
        self.assertEqual(MODULE.add_format_quirk(self.home, True), 'present')
        self.assertEqual(self.looknfeel.read_text(), self.owner_look + MODULE.FORMAT_QUIRK)
        after = self.looknfeel.stat()
        self.assertEqual((after.st_uid, after.st_gid, stat.S_IMODE(after.st_mode)),
                         (before.st_uid, before.st_gid, stat.S_IMODE(before.st_mode)))
        self.assertEqual(self.config.read_text(), self.owner_config)
        self.assertEqual([path.read_bytes() for path in self.protected], [b'owner content\n'] * 3)

    def test_owner_quirk_in_either_loaded_file_is_not_overridden(self):
        for path in (self.looknfeel, self.config):
            original = path.read_bytes()
            for setting in ('true', 'false'):
                with self.subTest(path=path, setting=setting):
                    self.put(path, original + (
                        'hl.config({ quirks = { skip_non_kms_dmabuf_formats = ' +
                        setting + ' } })\n').encode())
                    before = self.looknfeel.read_bytes()
                    self.assertEqual(MODULE.add_format_quirk(self.home, True),
                                     'skipped: owner format quirk preserved')
                    self.assertEqual(self.looknfeel.read_bytes(), before)
            self.put(path, original)

    def test_unloaded_or_unknown_configuration_is_not_changed(self):
        for source in ('-- custom config\n', self.owner_config.replace('hypr.looknfeel', 'hypr.other'),
                       self.owner_config.replace('require("hypr.looknfeel")',
                                                 '-- require("hypr.looknfeel")')):
            with self.subTest(source=source):
                self.put(self.config, source.encode())
                self.assertTrue(MODULE.add_format_quirk(self.home, True).startswith('skipped'))
                self.assertEqual(self.looknfeel.read_text(), self.owner_look)
        self.put(self.config, self.owner_config.encode())
        for source in (self.owner_look + 'return {}\n',):
            with self.subTest(source=source):
                self.put(self.looknfeel, source.encode())
                self.assertTrue(MODULE.add_format_quirk(self.home, True).startswith('skipped'))
                self.assertEqual(self.looknfeel.read_text(), source)

    def test_loaded_comments_only_looknfeel_receives_format_quirk(self):
        source = '-- Default Omarchy appearance overrides are commented out.\n'
        self.put(self.looknfeel, source.encode())
        self.assertEqual(MODULE.add_format_quirk(self.home, True),
                         'added; takes effect at next graphical login')
        self.assertEqual(self.looknfeel.read_text(), source + MODULE.FORMAT_QUIRK)
        self.assertEqual(MODULE.add_format_quirk(self.home, True), 'present')

    def test_missing_symlink_and_fifo_configurations_never_get_created_or_followed(self):
        for path in (self.config, self.looknfeel):
            original = path.read_bytes()
            path.unlink()
            self.assertTrue(MODULE.add_format_quirk(self.home, True).startswith('no '))
            self.assertFalse(path.exists())
            target = path.with_suffix('.target')
            self.put(target, original)
            path.symlink_to(target)
            with self.assertRaises(OSError):
                MODULE.add_format_quirk(self.home, True)
            self.assertEqual(target.read_bytes(), original)
            path.unlink()
            os.mkfifo(path)
            self.assertTrue(MODULE.add_format_quirk(self.home, True).startswith('skipped'))
            path.unlink()
            self.put(path, original)

    def test_no_final_newline_is_preserved_before_append(self):
        self.put(self.looknfeel, self.owner_look.rstrip('\n').encode())
        MODULE.add_format_quirk(self.home, True)
        self.assertEqual(self.looknfeel.read_text(), self.owner_look + MODULE.FORMAT_QUIRK)

    def test_lua_version_gate_uses_running_compositor_not_package_version(self):
        MODULE.add_format_quirk(self.home, True)
        # Execute the actual migrated file, including the owner's settings.
        # Unsupported versions have a config API that rejects this option.
        harness = '''
local version, expected = arg[2], tonumber(arg[3])
local calls = 0
hl = {
  version = version ~= "missing" and function() return version end or nil,
  config = function(config)
    if config.quirks then
      assert(expected == 1, "unsupported format option")
      assert(config.quirks.skip_non_kms_dmabuf_formats == false)
      calls = calls + 1
    else
      assert(config.decoration.rounding == 7)
    end
  end,
}
dofile(arg[1])
assert(calls == expected)
'''
        script = self.root / 'gate.lua'
        script.write_text(harness)
        for version, expected in (('missing', 0), ('unknown', 0), ('0.56.3', 0),
                                  ('0.9.9', 0), ('0.57.0', 1), ('v0.57.1', 1),
                                  ('0.58.0', 1), ('1.0.0', 1), ('0.56.9', 0)):
            with self.subTest(version=version):
                result = subprocess.run([os.environ.get('LUA', 'lua'), str(script),
                                         str(self.looknfeel), version, str(expected)],
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_hook_rejects_other_boards_and_non_native_displays_before_user_access(self):
        for board in (b'apple,j713\0', b'apple,j715x\0', b'apple,j715'):
            with self.subTest(board=board):
                self.put(self.tree / 'compatible', board)
                with mock.patch.object(MODULE.os, 'fork', side_effect=AssertionError('user access')):
                    with self.assertRaises(MODULE.Refused):
                        MODULE.format_hook(self.root)
        self.put(self.tree / 'compatible', b'apple,j715\0')
        self.put(self.node / 'format', b'x8r8g8b8\0')
        with mock.patch.object(MODULE.os, 'fork', side_effect=AssertionError('user access')):
            with self.assertRaises(MODULE.Refused):
                MODULE.format_hook(self.root)
        self.assertEqual(self.looknfeel.read_text(), self.owner_look)

    def test_failed_privilege_drop_writes_nothing_and_skips_non_login_accounts(self):
        with mock.patch.object(MODULE.os, 'setgroups', side_effect=PermissionError('drop refused')):
            self.assertEqual(MODULE.format_hook(self.root), {'owner': 'skipped: drop refused'})
        self.assertEqual(self.looknfeel.read_text(), self.owner_look)

    def test_hook_requires_root_and_status_stays_read_only(self):
        with mock.patch.object(MODULE.os, 'geteuid', return_value=1000):
            self.assertEqual(self.run_tool('--config-hook')[0], 1)
        self.assertEqual(self.run_tool('--status')[0], 0)
        self.assertEqual(self.looknfeel.read_text(), self.owner_look)

    def test_package_install_and_upgrade_deliver_hook_even_when_refused(self):
        launcher = self.root / 'usr/bin/m4-xr30'
        self.put(launcher, b'#!/bin/sh\nprintf "%s\\n" "$*" >> hook-calls\nexit 7\n')
        launcher.chmod(0o755)
        result = subprocess.run(
            ['bash', '-ec', 'source "$1"; source "$(dirname "$1")/$install"; post_install; post_upgrade',
             'recipe', str(PACKAGE / 'PKGBUILD')],
            cwd=self.root, capture_output=True, text=True, timeout=10,
            env={**os.environ, 'BASH_ENV': '/dev/null'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / 'hook-calls').read_text().splitlines(),
                         ['--config-hook', '--config-hook'])


if __name__ == '__main__':
    unittest.main()
