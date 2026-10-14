# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Lid lock for installed Airs: only missing pieces, only Omarchy configs, never twice."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load('m4_lid_lock', 'tools/m4-lid-lock.py')
DESKTOP = load('configure_m4_desktop', 'tools/configure-m4-desktop.py')

STACK = ('#%PAM-1.0\n'
         'auth       [success=1 default=bad]     pam_unix.so try_first_pass nullok\n'
         'account    include                     system-local-login\n')
HELPER = ("#!/bin/bash\n\n"
          "as_root tee /etc/pam.d/omarchy-lock-password >/dev/null <<'EOF'\n" + STACK + "EOF\n\n"
          "  as_root tee /etc/pam.d/omarchy-lock-fingerprint >/dev/null <<'EOF'\n"
          "#%PAM-1.0\nauth       required                    pam_fprintd.so\nEOF\n")
OMARCHY = ('dofile((os.getenv("OMARCHY_PATH") or "/usr/share/omarchy") .. "/default/hypr/bootstrap.lua")\n'
           'require("default.hypr.omarchy")\n')
PASSWD = ('root:x:0:0::/root:/usr/bin/bash\n'
          'nobody:x:65534:65534:Kernel Overflow User:/:/usr/bin/nologin\n'
          'owner:x:1000:1000::/home/owner:/bin/bash\n'
          'guest:x:1001:1001::/home/guest:/usr/bin/nologin\n')


class LidLock(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.put(MODULE.COMPATIBLE, 'apple,j713\0apple,t8132\0apple,arm-platform\0')
        self.put(MODULE.APPLY_LOCK, HELPER)
        self.put('etc/passwd', PASSWD)
        self.config = self.put('home/owner/' + MODULE.CONFIG, OMARCHY)
        self.pam = self.root / MODULE.LOCK_PAM
        self.pam.parent.mkdir(parents=True, exist_ok=True)

    def put(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def run_tool(self, apply=True):
        return MODULE.run(self.root, apply, switch_user=False)

    def test_installed_air_gets_both_parts_once(self):
        old = os.umask(0o077)
        try:
            report = self.run_tool()
        finally:
            os.umask(old)
        self.assertEqual(report, {'lock_pam': 'written',
                                  'lid_binds': {'owner': 'added; takes effect at next login'}})
        self.assertEqual(self.pam.read_text(), STACK)
        self.assertEqual(self.pam.stat().st_mode & 0o777, 0o644)
        self.assertEqual(self.config.read_text(), OMARCHY + MODULE.LID_BINDS)
        self.assertEqual(self.run_tool(), {'lock_pam': 'present', 'lid_binds': {'owner': 'present'}})
        self.assertEqual(self.config.read_text().count(MODULE.MARKER), 1)
        self.assertEqual(sorted(p.name for p in self.pam.parent.iterdir()), [self.pam.name])

    def test_image_made_config_and_existing_pam_are_left_alone(self):
        image = OMARCHY + '-- J713 display and session integration.\n' + MODULE.LID_BINDS + '-- mine\n'
        self.config.write_text(image)
        self.pam.write_text('#%PAM-1.0\n# the owner\'s own stack\n')
        self.assertEqual(self.run_tool(), {'lock_pam': 'present', 'lid_binds': {'owner': 'present'}})
        self.assertEqual(self.config.read_text(), image)
        self.assertEqual(self.pam.read_text(), '#%PAM-1.0\n# the owner\'s own stack\n')

    def test_inspection_changes_nothing(self):
        self.assertEqual(self.run_tool(apply=False), {'lock_pam': 'missing', 'lid_binds': {'owner': 'missing'}})
        self.assertFalse(self.pam.exists())
        self.assertEqual(self.config.read_text(), OMARCHY)

    def test_only_on_the_j713(self):
        self.put(MODULE.COMPATIBLE, 'apple,j613\0apple,t8112\0')
        self.assertEqual(self.run_tool(), {'status': 'not a J713; nothing to do'})
        self.assertFalse(self.pam.exists())
        self.assertEqual(self.config.read_text(), OMARCHY)

    def test_unrecognised_helper_writes_no_pam(self):
        for helper in ('#!/bin/bash\necho changed\n', HELPER + HELPER, HELPER.replace('#%PAM-1.0\nauth  ', 'auth  ')):
            self.put(MODULE.APPLY_LOCK, helper)
            self.assertEqual(self.run_tool()['lock_pam'], 'refused: unrecognised ' + MODULE.APPLY_LOCK)
            self.assertFalse(self.pam.exists())
        (self.root / MODULE.APPLY_LOCK).unlink()
        self.assertEqual(self.run_tool()['lock_pam'], 'refused: no ' + MODULE.APPLY_LOCK)

    def test_other_configs_and_accounts_are_skipped(self):
        self.config.write_text('-- hand-written, not Omarchy\n')
        self.put('home/guest/' + MODULE.CONFIG, OMARCHY)
        self.put('root/' + MODULE.CONFIG, OMARCHY)
        report = self.run_tool()
        self.assertEqual(report['lid_binds'], {'owner': 'skipped: not an Omarchy configuration'})
        self.assertEqual(self.config.read_text(), '-- hand-written, not Omarchy\n')
        self.assertNotIn(MODULE.MARKER, (self.root / 'home/guest' / MODULE.CONFIG).read_text())
        self.config.unlink()
        self.assertEqual(self.run_tool()['lid_binds'], {'owner': 'no configuration'})

    def test_symlinked_config_is_never_followed(self):
        target = self.put('etc/shadow-like', OMARCHY)
        self.config.unlink()
        self.config.symlink_to(target)
        self.assertTrue(self.run_tool()['lid_binds']['owner'].startswith('skipped'))
        self.assertEqual(target.read_text(), OMARCHY)

    def test_fifo_config_does_not_hang(self):
        self.config.unlink()
        os.mkfifo(self.config)
        self.assertEqual(self.run_tool()['lid_binds'], {'owner': 'skipped: not a regular file'})

    def test_interrupted_pam_write_is_recovered(self):
        temp = self.pam.with_name('.' + self.pam.name + '.m4-lid-lock')
        temp.write_text('half a stack')
        self.assertEqual(self.run_tool()['lock_pam'], 'written')
        self.assertEqual(self.pam.read_text(), STACK)
        self.assertFalse(temp.exists())

    def test_pam_failure_still_adds_binds(self):
        self.pam.parent.chmod(0o555)
        self.addCleanup(self.pam.parent.chmod, 0o755)
        if os.access(self.pam.parent, os.W_OK):
            self.skipTest('needs an unprivileged runner')
        report = self.run_tool()
        self.assertTrue(report['lock_pam'].startswith('refused: '), report)
        self.assertEqual(report['lid_binds'], {'owner': 'added; takes effect at next login'})

    def test_config_without_final_newline(self):
        self.config.write_text(OMARCHY.rstrip('\n'))
        self.run_tool()
        self.assertEqual(self.config.read_text(), OMARCHY + MODULE.LID_BINDS)

    def test_a_failed_privilege_drop_writes_nothing(self):
        if os.geteuid() == 0:
            self.skipTest('needs an unprivileged runner')
        account = MODULE.users(self.root)[0]
        result = MODULE.as_user(account, MODULE.add_binds, self.config, True)
        self.assertTrue(result.startswith('skipped: '), result)
        self.assertEqual(self.config.read_text(), OMARCHY)

    def test_hook_reports_once_and_never_fails_the_update(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(MODULE.main(['--hook', '--root', str(self.root)]), 0)
            self.assertEqual(MODULE.main(['--hook', '--root', str(self.root)]), 0)
        self.assertEqual(out.getvalue().count('Closing the lid now locks the screen'), 1)
        (self.root / 'etc/passwd').unlink()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(MODULE.main(['--hook', '--root', str(self.root)]), 0)
            self.assertEqual(MODULE.main(['--apply', '--root', str(self.root)]), 1)

    def test_same_binds_and_stack_as_the_image(self):
        self.assertIn('\n' + MODULE.LID_BINDS.splitlines()[0] + '\n', DESKTOP.M4_OVERRIDES)
        for line in MODULE.LID_BINDS.splitlines()[1:]:
            self.assertIn('\n' + line + '\n', DESKTOP.M4_OVERRIDES)
        self.assertEqual(MODULE.lock_pam_stack(HELPER), DESKTOP.lock_pam(self.root))
        doc = (ROOT / 'docs/j713-desktop-image.md').read_text()
        self.assertIn('```lua\n' + MODULE.LID_BINDS + '```\n', doc)


if __name__ == '__main__':
    unittest.main()
