# SPDX-License-Identifier: MIT
"""Native backlight startup failure guards and preservation of owner key configuration."""
import hashlib
import importlib.util
import json
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

    def test_state_change_cannot_clear_an_inflight_marker(self):
        with BACKLIGHT.locked_state(self.root) as state:
            BACKLIGHT.write_state(state, 'attempt', 'inflight\n')
            with self.assertRaises(BACKLIGHT.Refused):
                with BACKLIGHT.locked_state(self.root):
                    BACKLIGHT.remove_state(state, 'attempt')
            self.assertEqual((state / 'attempt').read_text(), 'inflight\n')


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
