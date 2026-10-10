# SPDX-License-Identifier: MIT
"""Run only in a disposable leased fleet copy; restore each mutated source."""
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
cases = [
    ('tools/configure-m4-audio.py', 'require(board == manifest["board"] == "apple,j713", "Unsupported board")', 'require(True, "Unsupported board")', 'test_board_kernel_abi_refused_before_write'),
    ('tools/configure-m4-audio.py', '    verify_abi(root, manifest["abi_files"])', '    pass', 'test_board_kernel_abi_refused_before_write'),
    ('tools/configure-m4-audio.py', 'require(path.read_bytes() == content, "Conflicting existing audio file: " + name)', 'require(True, "Conflicting existing audio file: " + name)', 'test_custom_file_refuses_before_any_write'),
    ('tools/configure-m4-audio.py', '            os.replace(temporary, path)', '            temporary.unlink()', 'test_apply_check_idempotent_and_enable_links'),
    ('tools/m4-j713-audio-register.py', "    write_record(runtime / 'attempt.json', identity)", '    pass', 'test_attempt_failure_retained_and_never_retried'),
    ('tools/m4-j713-audio-register.py', '        bus = self.wait_providers()', "        bus = '1'", 'test_stage4_does_not_replace_provider_binding'),
    ('tools/configure-m4-audio.py', '            current.chmod(0o755)', '            pass', 'test_restrictive_umask_public_parents_and_check'),
    ('tools/m4-j713-audio-register.py', "            self.p('/sys/bus/platform/drivers/apple-mca/bind').write_text('393600000.i2s\\n')", '            pass', 'test_complete_registration_population_order'),
    ('tools/m4-j713-audio-register.py', '        bus = self.wait_providers()', '        bus = self.providers()', 'test_deferred_provider_binds_without_reloading'),
    ('tools/m4-j713-audio-register.py', "                    raise RuntimeError('Provider readiness timed out: ' + str(error)) from error", "                    return '1'", 'test_provider_wait_timeout_and_wrong_driver_refuse'),
    ('tools/m4-j713-audio-register.py', '        self.driver(device, name, module)', '        pass', 'test_provider_wait_timeout_and_wrong_driver_refuse'),

]
for number, (filename, before, after, test) in enumerate(cases):
    path = root / filename
    original = path.read_text()
    assert original.count(before) == 1, (filename, before)
    try:
        path.write_text(original.replace(before, after))
        cls = 'ImageTests' if filename.endswith('configure-m4-audio.py') else 'RegistrationTests'
        p = subprocess.run(['python3', '-B', '-m', 'unittest', 'test_j713_audio_installer.' + cls + '.' + test],
                           cwd=root / 'tests', text=True, capture_output=True)
        print('MUTANT', number, test, 'rc', p.returncode, flush=True)
        print(p.stdout + p.stderr, flush=True)
        assert p.returncode and ('FAIL:' in p.stderr or 'ERROR:' in p.stderr), 'Survived or invalid fixture'
        assert 'SyntaxError' not in p.stderr and 'ImportError' not in p.stderr, 'Invalid mutant'
    finally:
        path.write_text(original)
