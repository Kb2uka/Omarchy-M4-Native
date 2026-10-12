# SPDX-License-Identifier: MIT
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
SPEC = importlib.util.spec_from_file_location('owner_setup', ROOT / 'tools/configure-j713-owner-setup.py')
owner_setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(owner_setup)

# Exact retained image helper; exercised as sourced by install/helpers/logging.sh.
RETAINED_HELPER = b'''AMD_AUDIO_CARD=$(pactl list cards 2>/dev/null | grep -B20 "Family 17h/19h" | grep "Name: " | awk '{print $2}' || true)

if [[ -n $AMD_AUDIO_CARD ]]; then
  pactl set-card-profile "$AMD_AUDIO_CARD" "HiFi (Mic1, Mic2, Speaker)" 2>/dev/null || true
fi
'''


class OwnerSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'image'
        for name in ('etc/m4-root-id', 'var/lib/omarchy/provisioning/pending'):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fixture\n')
        self.helper = self.root / owner_setup.FRAMEWORK_HELPER
        self.helper.parent.mkdir(parents=True)
        self.helper.write_bytes(RETAINED_HELPER)
        self.helper.chmod(0o644)
        self.bin = self.base / 'bin'
        self.bin.mkdir()
        self.trace = self.base / 'pactl-calls'
        self.command('uname', '#!/bin/bash\nprintf "%s\\n" "$TEST_ARCH"\n')
        self.command('pactl', '''#!/bin/bash
printf '%s\\n' "$*" >> "$PROBE_LOG"
if [[ $* == 'list cards' ]]; then
  printf 'Name: alsa_card.fixture\\nDescription: %s\\n' "$TEST_AUDIO_FAMILY"
fi
''')

    def command(self, name, content):
        path = self.bin / name
        path.write_text(content)
        path.chmod(0o755)

    def run_helper(self, arch, family='Family 17h/19h'):
        env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ['PATH'],
                   TEST_ARCH=arch, TEST_AUDIO_FAMILY=family, PROBE_LOG=str(self.trace))
        result = subprocess.run(['bash', '-eE', '-c', 'source "$1"; printf "continued\\n"',
                                 'bash', str(self.helper)], env=env, capture_output=True, text=True,
                                timeout=5, check=True)
        self.assertEqual(result.stdout, 'continued\n')
        return self.trace.read_text().splitlines() if self.trace.exists() else []

    def test_arm64_does_not_probe_unrelated_audio(self):
        owner_setup.configure(self.root, apply=True)
        self.assertEqual(self.run_helper('aarch64'), [])

    def test_x86_preserves_original_amd_profile_selection(self):
        owner_setup.configure(self.root, apply=True)
        self.assertEqual(self.run_helper('x86_64'), [
            'list cards', 'set-card-profile alsa_card.fixture HiFi (Mic1, Mic2, Speaker)'])

    def test_x86_without_amd_card_only_lists_cards(self):
        owner_setup.configure(self.root, apply=True)
        self.assertEqual(self.run_helper('x86_64', 'Unrelated audio'), ['list cards'])

    def test_apply_check_idempotent_preserves_original_and_mode(self):
        with self.assertRaisesRegex(ValueError, 'configuration differs'):
            owner_setup.configure(self.root)
        self.assertEqual(self.helper.read_bytes(), RETAINED_HELPER)
        result = owner_setup.configure(self.root, apply=True)
        self.assertEqual(result['changed_files'], [owner_setup.FRAMEWORK_HELPER])
        self.assertEqual(self.helper.read_bytes(), owner_setup.ARM64_GUARD + RETAINED_HELPER)
        self.assertEqual(self.helper.stat().st_mode & 0o777, 0o644)
        self.assertEqual(owner_setup.configure(self.root)['changed_files'], [])
        self.assertEqual(owner_setup.configure(self.root, apply=True)['changed_files'], [])

    def test_unknown_source_refuses_without_rewriting(self):
        for contents in (RETAINED_HELPER + b'# changed\n', owner_setup.ARM64_GUARD + b'unknown\n'):
            with self.subTest(contents=contents):
                self.helper.write_bytes(contents)
                with self.assertRaisesRegex(ValueError, 'Unknown Framework audio helper source'):
                    owner_setup.configure(self.root, apply=True)
                self.assertEqual(self.helper.read_bytes(), contents)

    def test_mode_drift_refuses_without_rewriting(self):
        self.helper.chmod(0o600)
        with self.assertRaisesRegex(ValueError, 'helper mode'):
            owner_setup.configure(self.root, apply=True)
        self.assertEqual(self.helper.read_bytes(), RETAINED_HELPER)
        self.assertEqual(self.helper.stat().st_mode & 0o777, 0o600)

    def test_pending_image_markers_required(self):
        for name in ('etc/m4-root-id', 'var/lib/omarchy/provisioning/pending'):
            with self.subTest(name=name):
                marker = self.root / name
                marker.unlink()
                with self.assertRaisesRegex(ValueError, 'Not a pending M4 owner image'):
                    owner_setup.configure(self.root, apply=True)
                self.assertEqual(self.helper.read_bytes(), RETAINED_HELPER)
                marker.write_text('fixture\n')

    def test_installed_owner_image_refused(self):
        (self.root / 'etc/m4-install-complete').touch()
        with self.assertRaisesRegex(ValueError, 'Installed owner image'):
            owner_setup.configure(self.root, apply=True)
        self.assertEqual(self.helper.read_bytes(), RETAINED_HELPER)

    def test_host_root_and_noncanonical_image_refused(self):
        alias = self.base / 'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        for root in (Path('/'), Path('.'), alias):
            with self.subTest(root=root):
                with self.assertRaisesRegex(ValueError, 'canonical offline image'):
                    owner_setup.configure(root, apply=True)
        self.assertEqual(self.helper.read_bytes(), RETAINED_HELPER)

    def test_symlinked_helper_refused(self):
        outside = self.base / 'outside'
        outside.write_bytes(RETAINED_HELPER)
        self.helper.unlink()
        self.helper.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'Symlink in image/package path'):
            owner_setup.configure(self.root, apply=True)
        self.assertEqual(outside.read_bytes(), RETAINED_HELPER)

    def test_symlinked_directory_refused(self):
        outside = self.base / 'outside'
        self.helper.parent.rename(outside)
        self.helper.parent.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'Symlink in image/package path'):
            owner_setup.configure(self.root, apply=True)
        self.assertEqual((outside / self.helper.name).read_bytes(), RETAINED_HELPER)


if __name__ == '__main__':
    unittest.main()
