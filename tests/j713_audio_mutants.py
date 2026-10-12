# SPDX-License-Identifier: MIT
"""Run only in a disposable leased fleet copy; restore each mutated source."""
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
cases = [
    ('tools/configure-m4-audio.py', 'require(board == manifest["board"] == "apple,j713", "Unsupported board")', 'require(True, "Unsupported board")', 'test_board_kernel_abi_refused_before_write'),
    ('tools/configure-m4-audio.py', '    verify_abi(root, manifest["abi_files"])', '    pass', 'test_board_kernel_abi_refused_before_write'),
    ('tools/configure-m4-audio.py', 'require(path.read_bytes() == expected, "Conflicting existing audio file: " + name)', 'require(True, "Conflicting existing audio file: " + name)', 'test_custom_file_refuses_before_any_write'),
    ('tools/configure-m4-audio.py', '            os.replace(temporary, path)', '            temporary.unlink()', 'test_apply_check_idempotent_and_enable_links'),
    ('tools/m4-j713-audio-register.py', "    write_record(runtime / 'attempt.json', identity)", '    pass', 'test_attempt_failure_retained_and_never_retried'),
    ('tools/m4-j713-audio-register.py', '        bus = self.wait_providers()', "        bus = '1'", 'test_stage4_does_not_replace_provider_binding'),
    ('tools/configure-m4-audio.py', '            current.chmod(0o755)', '            pass', 'test_restrictive_umask_public_parents_and_check'),
    ('tools/m4-j713-audio-register.py', "            self.p('/sys/bus/platform/drivers/apple-mca/bind').write_text('393600000.i2s\\n')", '            pass', 'test_complete_registration_population_order'),
    ('tools/m4-j713-audio-register.py', '        bus = self.wait_providers()', '        bus = self.providers()', 'test_deferred_provider_binds_without_reloading'),
    ('tools/m4-j713-audio-register.py', "                    raise RuntimeError('Provider readiness timed out: ' + str(error)) from error", "                    return '1'", 'test_provider_wait_timeout_and_wrong_driver_refuse'),
    ('tools/m4-j713-audio-register.py', '        self.driver(device, name, module)', '        pass', 'test_provider_wait_timeout_and_wrong_driver_refuse'),
    ('tools/configure-m4-audio.py', 'not path.exists() or stat.S_IMODE(path.stat().st_mode) != modes[name] or', 'not path.exists() or', 'test_existing_file_mode_drift_check_refuses_and_apply_repairs'),
    ('tools/configure-m4-audio.py', '    planned[MANIFEST] = raw', '    planned = {name: (package / "root" / name).read_bytes() for name in manifest["file_metadata"]}\n    planned[MANIFEST] = raw', 'test_apply_consumes_verified_payload_snapshot'),
    ('tools/package-j713-audio.py', "payload[name] = (verified(readiness, relative, spec['readiness_inputs'][relative]), 0o644)", "payload[name] = ((verified(readiness, relative, spec['readiness_inputs'][relative]), (readiness / relative).read_bytes())[1], 0o644)", 'test_assembly_consumes_verified_readiness_snapshot'),
    ('tools/m4-j713-audio-register.py', '        trusted_file(Path(name), expected)', '        pass', 'RegistrationEntryTests.test_main_checks_payload_before_registration'),
    ('tools/m4-j713-audio-register.py', "        require(entry['module'] == name and manifest['files'].get(entry['path']) == entry['sha256'], 'Module not pinned')", '        pass', 'RegistrationEntryTests.test_main_checks_module_map_before_registration'),
    ('tools/m4_audio_package.py', 'payload = {name: verified(package / "root", name, entry)', 'payload = {name: (verified(package / "root", name, entry), (package / "root" / name).read_bytes())[1]', 'test_apply_consumes_verified_payload_snapshot'),
    ('tools/m4_audio_package.py', '    return data\n', '    return path.read_bytes()\n', 'test_apply_consumes_verified_payload_snapshot'),
    ('tools/package-j713-audio.py', "payload[name] = (verified(origin, entry.get('source', name), entry), entry['mode'])", "payload[name] = ((verified(origin, entry.get('source', name), entry), (origin / entry.get('source', name)).read_bytes())[1], entry['mode'])", 'test_assembly_consumes_verified_readiness_snapshot'),
    ('tools/package-j713-audio.py', "    require(set(readiness_sources.values()) == set(spec['readiness_inputs']),\n            'Readiness source/pin set mismatch')\n    for name, relative in readiness_sources.items():\n        payload[name] = (verified(readiness, relative, spec['readiness_inputs'][relative]), 0o644)", "    for name, relative in readiness_sources.items():\n        data = verified(readiness, relative, spec['readiness_inputs'][relative]) if relative in spec['readiness_inputs'] else (readiness / relative).read_bytes()\n        payload[name] = (data, 0o644)", 'test_assembly_requires_each_readiness_pin'),
    ('tools/m4-j713-audio-register.py', "            require(self.command(['modinfo', '-F', 'vermagic', entry['path']]).startswith(\n                m['kernel_release'] + ' SMP preempt mod_unload aarch64'), 'Module ABI mismatch')", "            self.command(['modinfo', '-F', 'vermagic', entry['path']])", 'test_preflight_wrong_vermagic_refused'),

    ('tools/package-j713-audio.py', "payload[name] = (verified(readiness, relative, spec['readiness_inputs'][relative]), 0o644)", "payload[name] = ((readiness / relative).read_bytes(), 0o644)", 'test_assembly_consumes_verified_readiness_snapshot'),
    ('tools/package-j713-audio.py', "payload[name] = (verified(origin, entry.get('source', name), entry), entry['mode'])", "payload[name] = ((origin / entry.get('source', name)).read_bytes(), entry['mode'])", 'test_assembly_consumes_verified_readiness_snapshot'),
    ('tools/package-j713-audio.py', "set(readiness_sources.values()) == set(spec['readiness_inputs'])", "set(readiness_sources.values()) <= set(spec['readiness_inputs'])", 'test_assembly_requires_each_readiness_pin'),
    ('tests/test_j713_audio_installer.py', "target = (self.package / 'root/usr/lib/omarchy/example').resolve()", "target = self.package / 'root/usr/lib/omarchy/example'", 'test_apply_consumes_verified_payload_snapshot'),

    ('tools/m4_audio_package.py', 'len(data) == entry["size"] and digest(data) == entry["sha256"]', 'len(data) == entry["size"]', 'test_package_pin_and_payload_corruption'),
    ('tools/package-j713-audio.py', "payload[name] = (verified(origin, entry.get('source', name), entry), entry['mode'])", "payload[name] = (verified(origin, entry.get('source', name), entry), 0o644)", 'test_assembly_consumes_verified_readiness_snapshot'),
    ('tools/package-j713-audio.py', "('m4-j713-audio-register.service', 'speakersafetyd-j713.service')", "('m4-j713-audio-register.service',)", 'test_assembly_consumes_verified_readiness_snapshot'),

]
for number, (filename, before, after, test) in enumerate(cases):
    path = root / filename
    original = path.read_text()
    assert original.count(before) == 1, (filename, before)
    try:
        path.write_text(original.replace(before, after))
        cls = 'RegistrationTests' if filename.endswith('m4-j713-audio-register.py') else 'ImageTests'
        target = test if '.' in test else cls + '.' + test
        p = subprocess.run(['python3', '-B', '-m', 'unittest', 'test_j713_audio_installer.' + target],
                           cwd=root / 'tests', text=True, capture_output=True)
        print('MUTANT', number, test, 'rc', p.returncode, flush=True)
        print(p.stdout + p.stderr, flush=True)
        assert p.returncode and ('FAIL:' in p.stderr or 'ERROR:' in p.stderr), 'Survived or invalid fixture'
        assert 'SyntaxError' not in p.stderr and 'ImportError' not in p.stderr, 'Invalid mutant'
    finally:
        path.write_text(original)
