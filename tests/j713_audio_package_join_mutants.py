# SPDX-License-Identifier: MIT
"""Exercise join guards only in a disposable leased fleet source copy."""
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'tools/configure-m4-audio.py'

CASES = [
    ('digest(installed_raw) == previous_package_sha256', 'True',
     'test_wrong_previous_pin_refuses_before_write'),
    ('closed_default_transition(json.loads(installed_raw), manifest)', 'None',
     'test_join_refuses_changed_or_removed_payload_and_metadata'),
    ('old_identity == new_identity', 'True',
     'test_join_refuses_changed_or_removed_payload_and_metadata'),
    ('set(new_files) - set(old_files) == DEFAULT_POLICY_ADDITIONS',
     'DEFAULT_POLICY_ADDITIONS >= set(new_files) - set(old_files)',
     'test_both_policy_files_required_and_no_extra_additions'),
    ('all(new_files.get(name) == entry for name, entry in old_files.items())', 'True',
     'test_join_refuses_changed_or_removed_payload_and_metadata'),
    ('path.read_bytes() == expected', 'True',
     'test_existing_owner_payload_change_is_not_allowed_by_transition'),
    ('(item in symlinks, item == MANIFEST, item)', '(item in symlinks, item)',
     'test_partial_addition_retry_publishes_manifest_last'),
]


def rejected(label, test):
    result = subprocess.run(['python3', '-B', '-m', 'unittest',
                             'test_j713_audio_package_join.AudioPackageJoinTests.' + test],
                            cwd=ROOT / 'tests', text=True, capture_output=True)
    print(label, 'exit', result.returncode, flush=True)
    print(result.stdout + result.stderr, flush=True)
    assert result.returncode and 'FAIL:' in result.stderr, 'Mutant survived or failed outside an assertion'
    assert 'SyntaxError' not in result.stderr and 'ImportError' not in result.stderr


for index, (before, after, test) in enumerate(CASES):
    original = SOURCE.read_text()
    assert original.count(before) == 1, (index, before)
    try:
        SOURCE.write_text(original.replace(before, after))
        rejected('JOIN_MUTANT_' + str(index), test)
    finally:
        SOURCE.write_text(original)

spec_path = ROOT / 'audio/j713/qualified.json'
original = spec_path.read_text()
for name in ('etc/wireplumber/wireplumber.conf.d/82-j713-protected-default.conf',
             'usr/share/wireplumber/scripts/default-nodes/j713-protected.lua'):
    try:
        spec = json.loads(original)
        del spec['files'][name]
        spec_path.write_text(json.dumps(spec))
        rejected('OMITTED_' + name, 'test_real_source_spec_includes_both_pinned_policy_files')
    finally:
        spec_path.write_text(original)
