#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Preview or apply this repository's contribution controls; never change visibility."""
import argparse
import base64
import json
import time
from pathlib import Path
import subprocess

REPO = 'Kb2uka/Omarchy-M4-Native'
BASE = 'repos/' + REPO
ROOT = Path(__file__).resolve().parents[2]
CHECKS = ['PR policy', 'Tests (Linux)', 'Speaker daemon (Rust)', 'Installer transport (macOS)', 'Package channel',
          'Secrets', 'Malware', 'Workflow lint', 'Dependency review',
          'CodeQL (python)', 'CodeQL (c-cpp)', 'CodeQL (rust)', 'CodeQL (actions)']
SETTINGS = {
    '/actions/permissions': {'enabled': True, 'allowed_actions': 'selected', 'sha_pinning_required': True},
    '/actions/permissions/selected-actions': {'github_owned_allowed': True, 'verified_allowed': False, 'patterns_allowed': []},
    '/actions/permissions/workflow': {'default_workflow_permissions': 'read', 'can_approve_pull_request_reviews': False},
}

FORK_APPROVAL = {'approval_policy': 'all_external_contributors'}

def rules(owner_id):
    bypass = [{'actor_type': 'User', 'actor_id': owner_id, 'bypass_mode': 'pull_request'}]
    return [
        {'name': 'Contribution checks and maintainer review', 'target': 'branch',
         'enforcement': 'active', 'bypass_actors': bypass,
         'conditions': {'ref_name': {'include': ['refs/heads/develop', 'refs/heads/main'], 'exclude': []}},
         'rules': [
             {'type': 'deletion'}, {'type': 'non_fast_forward'},
             {'type': 'pull_request', 'parameters': {
                 'required_approving_review_count': 1, 'dismiss_stale_reviews_on_push': True,
                 'require_code_owner_review': True, 'require_last_push_approval': True,
                 'required_review_thread_resolution': True}},
             {'type': 'required_status_checks', 'parameters': {
                 'strict_required_status_checks_policy': True,
                 'do_not_enforce_on_create': False,
                 'required_status_checks': [{'context': name, 'integration_id': 15368} for name in CHECKS]}},
             {'type': 'code_scanning', 'parameters': {'code_scanning_tools': [
                 {'tool': 'CodeQL', 'alerts_threshold': 'errors', 'security_alerts_threshold': 'medium_or_higher'}]}}]},
        {'name': 'Maintainer merges only', 'target': 'branch',
          'enforcement': 'active', 'bypass_actors': bypass,
          'conditions': {'ref_name': {'include': ['refs/heads/develop', 'refs/heads/main'], 'exclude': []}},
          'rules': [{'type': 'update', 'parameters': {'update_allows_fetch_and_merge': False}}]},
    ]


def api(gh, endpoint, method='GET', body=None):
    command = [gh, 'api', '--method', method, '-H', 'Accept: application/vnd.github+json',
               '-H', 'X-GitHub-Api-Version: 2026-03-10', endpoint]
    if body is not None:
        command += ['--input', '-']
    result = subprocess.run(command, input=json.dumps(body) if body is not None else None,
                            text=True, capture_output=True, check=True)
    return json.loads(result.stdout) if result.stdout.strip() else None


def require_subset(expected, actual, where):
    # Normalize documented GitHub omitted defaults before strict comparison.
    # GitHub omits false-valued `parameters.update_allows_fetch_and_merge`
    # on `update` rules, returning just `{"type": "update"}`. Treat a missing
    # field as False only when the expected value is explicitly False.
    if isinstance(expected, dict) and isinstance(actual, dict):
        if expected.get('type') == 'update' and actual.get('type') == 'update':
            exp_params = expected.get('parameters')
            if isinstance(exp_params, dict) and exp_params.get('update_allows_fetch_and_merge') is False:
                act_params = actual.get('parameters')
                if act_params is None:
                    actual = dict(actual, parameters={'update_allows_fetch_and_merge': False})
                elif isinstance(act_params, dict) and 'update_allows_fetch_and_merge' not in act_params:
                    actual = dict(actual, parameters=dict(act_params, update_allows_fetch_and_merge=False))
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            raise ValueError('Unexpected response: ' + where)
        for key, value in expected.items():
            require_subset(value, actual.get(key), where + '/' + key)
    elif expected != actual:
        raise ValueError('Readback mismatch: ' + where)


def verify_subset_with_retry(expected, read_fn, where, attempts=5, delay_s=2.0, sleep=None):
    """Verify a readback with bounded propagation-delay tolerance.

    Calls read_fn() up to `attempts` times until require_subset passes.
    Sleeps delay_s between attempts. Raises the last ValueError on exhaustion.
    """
    rest = time.sleep if sleep is None else sleep
    last_error = None
    for attempt in range(attempts):
        try:
            require_subset(expected, read_fn(), where)
            return
        except ValueError as error:
            last_error = error
            if attempt + 1 < attempts:
                rest(delay_s)
    raise last_error


def activation_preflight(gh, repo):
    if repo.get('private') is not False:
        raise ValueError('Public activation requires public visibility; visibility is never changed here')
    paths = ['.github/CODEOWNERS', '.github/workflows/ci.yml',
             '.github/workflows/security.yml', '.github/workflows/codeql.yml',
             '.github/security/gitleaks.toml', 'tools/ci/check-workflows.py', 'tools/ci/scan-source.py']
    for branch in ('develop', 'main'):
        for path in paths:
            remote = api(gh, BASE + '/contents/' + path + '?ref=' + branch)
            if base64.b64decode(remote['content']) != (ROOT / path).read_bytes():
                raise ValueError(f'{branch}/{path} differs from this reviewed configuration')


def apply_settings(gh):
    for suffix, expected in SETTINGS.items():
        api(gh, BASE + suffix, 'PUT', expected)
        verify_subset_with_retry(expected, lambda s=suffix: api(gh, BASE + s), suffix)
    api(gh, BASE + '/vulnerability-alerts', 'PUT')
    api(gh, BASE + '/vulnerability-alerts')


def activate(gh, repo, desired):
    activation_preflight(gh, repo)
    apply_settings(gh)
    endpoint = BASE + '/actions/permissions/fork-pr-contributor-approval'
    api(gh, endpoint, 'PUT', FORK_APPROVAL)
    verify_subset_with_retry(FORK_APPROVAL, lambda: api(gh, endpoint), 'fork approval')
    existing = api(gh, BASE + '/rulesets?per_page=100')
    for body in desired:
        matches = [r for r in existing if r['name'] == body['name']]
        if len(matches) > 1:
            raise ValueError('Ambiguous existing ruleset: ' + body['name'])
        suffix = '/rulesets' + ('/' + str(matches[0]['id']) if matches else '')
        saved = api(gh, BASE + suffix, 'PUT' if matches else 'POST', body)
        # GitHub adds metadata to rule entries; compare the requested fields.
        # Readback may lag or omit documented false defaults; retry boundedly.
        def read_ruleset(sid=saved['id']):
            return api(gh, BASE + '/rulesets/' + str(sid))
        for key in ('name', 'target', 'enforcement', 'conditions', 'bypass_actors'):
            verify_subset_with_retry(body[key], lambda k=key: read_ruleset()[k], key)
        for rule in body['rules']:
            verify_subset_with_retry(
                rule,
                lambda r=rule: {t: e for t, e in ((x['type'], x) for x in read_ruleset()['rules'])}.get(r['type']),
                rule['type'])
    security = {'secret_scanning': {'status': 'enabled'},
                'secret_scanning_push_protection': {'status': 'enabled'}}
    api(gh, BASE, 'PATCH', {'security_and_analysis': security})
    verify_subset_with_retry(security, lambda: api(gh, BASE)['security_and_analysis'], 'security_and_analysis')
    api(gh, BASE + '/private-vulnerability-reporting', 'PUT')
    verify_subset_with_retry({'enabled': True}, lambda: api(gh, BASE + '/private-vulnerability-reporting'), 'private reporting')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gh', required=True, help='Absolute authenticated GitHub CLI executable')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--apply-settings', action='store_true')
    group.add_argument('--activate-public', action='store_true')
    args = parser.parse_args()
    if not Path(args.gh).is_absolute() or not Path(args.gh).is_file():
        parser.error('--gh must name an absolute executable')
    repo = api(args.gh, BASE)
    owner = repo['owner']
    if repo['full_name'].lower() != REPO.lower() or owner['login'].lower() != 'kb2uka':
        raise ValueError('Unexpected repository')
    desired = rules(owner['id'])
    if args.activate_public:
        activate(args.gh, repo, desired)
    elif args.apply_settings:
        apply_settings(args.gh)
    print(json.dumps({'repository': REPO, 'visibility': repo['visibility'],
                      'settings': SETTINGS, 'public_fork_approval': FORK_APPROVAL, 'rulesets': desired,
                      'settings_applied': args.apply_settings,
                      'public_activation_applied': args.activate_public}, indent=2))


if __name__ == '__main__':
    main()
