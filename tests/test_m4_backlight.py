# SPDX-License-Identifier: MIT
"""Native backlight startup failure guards and preservation of owner key configuration."""
import hashlib
import importlib.util
import json
import io
import contextlib
import os
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'tools/m4-backlight.py'
SPEC = importlib.util.spec_from_file_location('m4_backlight', SOURCE)
BACKLIGHT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BACKLIGHT)


class BacklightGuards(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.put(BACKLIGHT.COMPATIBLE, b'apple,j713\0apple,t8132\0')
        self.put('proc/sys/kernel/osrelease', b'qualified-kernel\n')
        self.put(BACKLIGHT.CLIENT + '/status', b'okay\0')
        self.put(BACKLIGHT.CLIENT + '/omarchy,display-handoff-verified', b'')
        self.put(BACKLIGHT.CLIENT + '/memory-region', b'\0\0\0\1')
        module = b'a pinned module payload'
        self.put(BACKLIGHT.MODULE, module)
        self.put(BACKLIGHT.PINS, json.dumps({'kernel_release': 'qualified-kernel',
                                          'module_sha256': hashlib.sha256(module).hexdigest(),
                                          'module_bytes': len(module)}).encode())

    def put(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
        return path

    def test_interrupted_startup_blocks_the_next_attempt(self):
        attempts = []

        def interrupted(command, **kwargs):
            attempts.append(command)
            self.assertTrue((self.root / BACKLIGHT.STATE / 'attempt').is_file())
            raise subprocess.CalledProcessError(1, command)

        with self.assertRaises(subprocess.CalledProcessError):
            BACKLIGHT.start(self.root, interrupted)
        marker = self.root / BACKLIGHT.STATE / 'attempt'
        self.assertTrue(marker.is_file())
        with self.assertRaises(BACKLIGHT.Refused):
            BACKLIGHT.start(self.root, interrupted)
        self.assertEqual(len(attempts), 1)
        self.assertTrue(marker.is_file())

    def test_unqualified_module_is_refused_before_attempt_marker(self):
        self.put(BACKLIGHT.MODULE, b'changed module')

        def forbidden(*args, **kwargs):
            self.fail('Module loading occurred before pin validation')

        with self.assertRaises(BACKLIGHT.Refused):
            BACKLIGHT.start(self.root, forbidden)
        self.assertFalse((self.root / BACKLIGHT.STATE / 'attempt').exists())

    def test_unverified_handoff_never_loads_a_module(self):
        (self.root / BACKLIGHT.CLIENT / 'omarchy,display-handoff-verified').unlink()

        def forbidden(*args, **kwargs):
            self.fail('Module loading occurred without a verified handoff')

        with self.assertRaises(BACKLIGHT.Refused):
            BACKLIGHT.start(self.root, forbidden)
        self.assertFalse((self.root / BACKLIGHT.STATE / 'attempt').exists())

    def test_old_boot_without_handoff_node_gives_restart_guidance(self):
        (self.root / BACKLIGHT.CLIENT / 'status').unlink()
        with self.assertRaisesRegex(BACKLIGHT.Refused, 'restart after omarchy-m4-update'):
            BACKLIGHT.start(self.root, lambda *args, **kwargs: self.fail('must not load'))
        self.assertFalse((self.root / BACKLIGHT.STATE / 'attempt').exists())

    def test_successful_start_clears_marker_only_after_backlight_is_online(self):
        self.put(BACKLIGHT.DEBUG + '/status', b'online=1 failed=0 applied_nits=300\n')
        self.put(BACKLIGHT.DEBUG + '/start', b'')
        self.put(BACKLIGHT.DEBUG + '/brightness_nits', b'')
        self.put(BACKLIGHT.BACKLIGHT + '/actual_brightness', b'300\n')
        self.put(BACKLIGHT.BACKLIGHT + '/max_brightness', b'525\n')

        def load(command, **options):
            self.assertTrue((self.root / BACKLIGHT.STATE / 'attempt').exists())
            self.assertEqual(command[0], 'insmod')

        state = BACKLIGHT.start(self.root, load)
        self.assertFalse(state['failed_attempt'])
        self.assertEqual(state['nits'], 300)
        self.assertEqual((self.root / BACKLIGHT.DEBUG / 'start').read_text(), '1\n')
        self.assertEqual((self.root / BACKLIGHT.DEBUG / 'brightness_nits').read_text(), '300\n')

    def test_cli_disable_holds_boot_and_enable_clears_both_markers(self):
        with patch.object(BACKLIGHT.os, 'geteuid', return_value=0), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(BACKLIGHT.main(['--disable', '--root', str(self.root)]), 0)
            self.assertTrue(BACKLIGHT.status(self.root)['held'])
            with self.assertRaisesRegex(BACKLIGHT.Refused, 'disabled until --enable'):
                BACKLIGHT.start(self.root, lambda *args, **kwargs: self.fail('disabled module loaded'))
            self.assertFalse((self.root / BACKLIGHT.STATE / 'attempt').exists())
            self.put(BACKLIGHT.STATE + '/attempt', b'interrupted\n')
            self.assertEqual(BACKLIGHT.main(['--enable', '--root', str(self.root)]), 0)
        self.assertFalse(BACKLIGHT.status(self.root)['held'])
        self.assertFalse(BACKLIGHT.status(self.root)['failed_attempt'])

    def test_startup_actions_require_root_even_with_an_alternate_root(self):
        with patch.object(BACKLIGHT.os, 'geteuid', return_value=1000), contextlib.redirect_stderr(io.StringIO()):
            for action in ['--boot', '--enable', '--disable', '--keys-hook', '--keys-remove-hook']:
                with self.subTest(action=action):
                    self.assertEqual(BACKLIGHT.main([action, '--root', str(self.root)]), 1)
        self.assertFalse((self.root / BACKLIGHT.STATE).exists())

    def test_state_change_cannot_clear_an_inflight_marker(self):
        with BACKLIGHT.locked_state(self.root) as state:
            BACKLIGHT.write_state(state, 'attempt', 'inflight\n')
            with self.assertRaises(BACKLIGHT.Refused):
                with BACKLIGHT.locked_state(self.root):
                    BACKLIGHT.remove_state(state, 'attempt')
            self.assertEqual((state / 'attempt').read_text(), 'inflight\n')


class BrightnessRouting(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        board = self.root / BACKLIGHT.COMPATIBLE
        board.parent.mkdir(parents=True)
        board.write_bytes(b'apple,j713\0apple,t8132\0')
        (self.root / BACKLIGHT.BACKLIGHT).mkdir(parents=True)
        self.calls = []
        self.focused = 'Unknown-1'
        self.percent = 1

    def run_command(self, command, **options):
        self.calls.append(command)
        if command == ['hyprctl', 'monitors', '-j']:
            return SimpleNamespace(stdout=json.dumps([{'name': self.focused, 'focused': True}]))
        if command == ['brightnessctl', '-d', 'j713-dcp', '-m']:
            return SimpleNamespace(stdout=f'j713-dcp,backlight,5,{self.percent}%,525\n')
        if command[:4] == ['brightnessctl', '-d', 'j713-dcp', 'set']:
            self.percent = int(command[4].rstrip('%'))
        return SimpleNamespace(returncode=0)

    def change(self, step, no_osd=False):
        with patch.dict(os.environ, {'XDG_RUNTIME_DIR': str(self.root)}):
            return BACKLIGHT.brightness(step, no_osd, self.run_command, self.root)

    def test_precise_down_key_stops_at_one_percent(self):
        self.change('1%-')
        self.assertIn(['brightnessctl', '-d', 'j713-dcp', 'set', '1%'], self.calls)
        self.assertNotIn(['brightnessctl', '-d', 'j713-dcp', 'set', '1%-'], self.calls)

    def test_normal_step_near_minimum_and_osd(self):
        self.percent = 4
        self.change('+5%')
        self.assertIn(['brightnessctl', '-d', 'j713-dcp', 'set', '5%'], self.calls)
        self.assertIn(['omarchy-osd', '-i', 'brightness', '-p', '5'], self.calls)

    def test_external_monitor_keeps_existing_routing(self):
        self.focused = 'DP-1'
        self.change('1%-', no_osd=True)
        self.assertEqual(self.calls, [['hyprctl', 'monitors', '-j'],
                                     ['omarchy', 'brightness', 'display', '--no-osd', '1%-']])


class OwnerKeys(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.config = self.home / 'hyprland.lua'
        self.original = 'require("default.hypr.omarchy")\nrequire("hypr.bindings")\n'
        self.config.write_text(self.original)

    def test_removal_preserves_changes_made_after_migration(self):
        self.assertEqual(BACKLIGHT.add_keys(self.config, True), 'added')
        added = self.config.read_bytes()
        self.assertEqual(BACKLIGHT.add_keys(self.config, True), 'present')
        self.assertEqual(self.config.read_bytes(), added)
        self.config.write_text(self.config.read_text() + 'hl.config({ input = { kb_layout = "us" } })\n')
        BACKLIGHT.remove_keys(self.config)
        self.assertEqual(self.config.read_text(), self.original + 'hl.config({ input = { kb_layout = "us" } })\n')
        backup = self.config.with_name('hyprland.lua.pre-m4-backlight')
        self.assertEqual(backup.read_text(), self.original)

    def test_custom_brightness_keys_are_not_overridden(self):
        (self.home / 'bindings.lua').write_text('o.bind("XF86MonBrightnessUp", "Custom", "custom-brightness")\n')
        self.assertEqual(BACKLIGHT.add_keys(self.config, True), 'custom brightness bindings preserved')
        self.assertEqual(self.config.read_text(), self.original)
        self.assertFalse(self.config.with_name('hyprland.lua.pre-m4-backlight').exists())

    def test_commented_examples_do_not_count_as_custom_brightness_keys(self):
        (self.home / 'bindings.lua').write_text(
            '-- hl.unbind("XF86MonBrightnessUp")\n'
            '  -- o.bind("XF86MonBrightnessDown", "Example", "example")\n')
        self.assertEqual(BACKLIGHT.add_keys(self.config, True), 'added')
        self.assertIn(BACKLIGHT.BINDS, self.config.read_text())

    def test_active_custom_binding_with_command_flags_is_preserved(self):
        (self.home / 'bindings.lua').write_text(
            'o.bind("XF86MonBrightnessUp", "Custom", "custom-brightness --up")\n')
        self.assertEqual(BACKLIGHT.add_keys(self.config, True), 'custom brightness bindings preserved')
        self.assertEqual(self.config.read_text(), self.original)

    def test_inline_custom_brightness_keys_are_preserved(self):
        source = self.original + 'o.bind("XF86MonBrightnessDown", "Custom", "custom --down")\n'
        self.config.write_text(source)
        self.assertEqual(BACKLIGHT.add_keys(self.config, True), 'custom brightness bindings preserved')
        self.assertEqual(self.config.read_text(), source)

    def test_symlink_configuration_cannot_modify_another_file(self):
        other = self.home / 'other'
        other.write_text(self.original)
        self.config.unlink()
        self.config.symlink_to(other)
        with self.assertRaises(OSError):
            BACKLIGHT.add_keys(self.config, True)
        self.assertEqual(other.read_text(), self.original)


if __name__ == '__main__':
    unittest.main()
