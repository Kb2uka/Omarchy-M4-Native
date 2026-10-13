# SPDX-License-Identifier: MIT
import base64
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('settings', ROOT / 'tools/ci/configure-public-security.py')
settings = importlib.util.module_from_spec(spec)
spec.loader.exec_module(settings)


class PublicConfigurationTests(unittest.TestCase):
    def test_private_preflight_refuses_before_api_mutation(self):
        with patch.object(settings, 'api') as api:
            with self.assertRaises(ValueError):
                settings.activation_preflight('/gh', {'private': True})
            api.assert_not_called()

    def test_drift_on_branch_refuses(self):
        with patch.object(settings, 'api', return_value={'content': base64.b64encode(b'wrong').decode()}):
            with self.assertRaises(ValueError):
                settings.activation_preflight('/gh', {'private': False})

    def test_matching_branches_checked_read_only(self):
        calls = []
        def api(gh, endpoint):
            calls.append(endpoint)
            path = endpoint.split('/contents/')[1].split('?')[0]
            return {'content': base64.b64encode((ROOT / path).read_bytes()).decode()}
        with patch.object(settings, 'api', side_effect=api):
            settings.activation_preflight('/gh', {'private': False})
        self.assertEqual(len(calls), 14)
        self.assertEqual({call.rsplit('=', 1)[1] for call in calls}, {'main', 'develop'})

    def test_rules_require_checks_reviews_and_block_findings(self):
        common, main = settings.rules(42)
        self.assertEqual(common['name'], 'Contribution checks and maintainer review')
        rules = {rule['type']: rule for rule in common['rules']}
        review = rules['pull_request']['parameters']
        self.assertTrue(review['require_code_owner_review'])
        self.assertTrue(review['dismiss_stale_reviews_on_push'])
        checks = rules['required_status_checks']['parameters']['required_status_checks']
        self.assertEqual({c['context'] for c in checks}, set(settings.CHECKS))
        self.assertTrue(all(c['integration_id'] == 15368 for c in checks))
        self.assertEqual(rules['code_scanning']['parameters']['code_scanning_tools'][0]['security_alerts_threshold'], 'medium_or_higher')
        self.assertEqual(main['name'], 'Maintainer merges only')
        self.assertEqual(main['conditions']['ref_name']['include'], ['refs/heads/develop', 'refs/heads/main'])
        self.assertEqual(main['bypass_actors'], [{'actor_type': 'User', 'actor_id': 42, 'bypass_mode': 'pull_request'}])

    def test_private_settings_do_not_call_public_only_api(self):
        calls = []
        def api(gh, endpoint, method='GET', body=None):
            calls.append((endpoint, method))
            suffix = endpoint.removeprefix(settings.BASE)
            if suffix in settings.SETTINGS:
                return settings.SETTINGS[suffix]
            return None
        with patch.object(settings, 'api', side_effect=api):
            settings.apply_settings('/gh')
        self.assertFalse(any('fork-pr-contributor' in path for path, _ in calls))
        self.assertEqual(settings.FORK_APPROVAL['approval_policy'], 'all_external_contributors')

    def test_readback_mismatch_fails(self):
        with self.assertRaises(ValueError):
            settings.require_subset({'a': {'b': True}}, {'a': {'b': False}}, 'test')

    def test_update_rule_omitted_false_default_accepted(self):
        expected = {'type': 'update', 'parameters': {'update_allows_fetch_and_merge': False}}
        # GitHub returns just {"type": "update"} when the false default is omitted.
        settings.require_subset(expected, {'type': 'update'}, 'update')
        settings.require_subset(
            expected,
            {'type': 'update', 'parameters': {}},
            'update')
        # A true value must still match exactly; omitted-then-true is a mismatch.
        with self.assertRaises(ValueError):
            settings.require_subset(expected, {'type': 'update', 'parameters': {'update_allows_fetch_and_merge': True}}, 'update')
        # Unrelated omissions are still mismatches.
        with self.assertRaises(ValueError):
            settings.require_subset({'type': 'pull_request', 'parameters': {'a': 1}}, {'type': 'pull_request'}, 'pr')

    def test_verify_retry_tolerates_propagation_delay(self):
        calls = []
        def read_fn():
            calls.append(1)
            if len(calls) < 3:
                return {}
            return {'approval_policy': 'all_external_contributors'}
        settings.verify_subset_with_retry(
            {'approval_policy': 'all_external_contributors'}, read_fn, 'fork approval',
            attempts=5, delay_s=0, sleep=lambda s: None)
        self.assertEqual(len(calls), 3)

    def test_verify_retry_exhausts(self):
        with self.assertRaises(ValueError):
            settings.verify_subset_with_retry(
                {'a': 1}, lambda: {'a': 2}, 'test',
                attempts=3, delay_s=0, sleep=lambda s: None)

    def test_activate_happy_path_with_delayed_readback(self):
        # Stateful fake API: first fork-approval read lags, ruleset fields lag
        # once, security and private-reporting reads lag once, and the update
        # rule omits the documented false default. Activation must still succeed.
        state = {'fork_reads': 0, 'next_id': 7, 'saved': {},
                 'ruleset_reads': {}, 'security_reads': 0, 'pvuln_reads': 0}
        desired = settings.rules(42)
        def api(gh, endpoint, method='GET', body=None):
            if endpoint.endswith('/fork-pr-contributor-approval') and method == 'GET':
                state['fork_reads'] += 1
                if state['fork_reads'] == 1:
                    return {}
                return dict(settings.FORK_APPROVAL)
            if endpoint.endswith('/rulesets?per_page=100'):
                return []
            if '/rulesets' in endpoint and method in ('POST', 'PUT'):
                nid = state['next_id']
                state['next_id'] += 1
                state['saved'][nid] = body
                return {'id': nid}
            if '/rulesets/' in endpoint:
                nid = int(endpoint.rsplit('/', 1)[1])
                state['ruleset_reads'][nid] = state['ruleset_reads'].get(nid, 0) + 1
                posted = state['saved'][nid]
                # Readback: strip the documented false default like GitHub does.
                import copy
                result = copy.deepcopy(posted)
                for rule in result.get('rules', []):
                    if rule.get('type') == 'update' and isinstance(rule.get('parameters'), dict):
                        rule['parameters'].pop('update_allows_fetch_and_merge', None)
                        if not rule['parameters']:
                            rule.pop('parameters')
                # Lag once per verified field: reads 1..10 corrupt ORDER[(R-1)//2],
                # so every field's first read fails and its retry passes. A
                # single-read mutant fails on the first field it checks.
                order = ('name', 'target', 'enforcement', 'conditions', 'bypass_actors')
                n = state['ruleset_reads'][nid]
                if 1 <= n <= 2 * len(order):
                    result[order[(n - 1) // 2]] = 'lagged-wrong-value'
                return result
            if endpoint == settings.BASE:
                if method == 'PATCH':
                    return {'security_and_analysis': body['security_and_analysis']}
                state['security_reads'] += 1
                if state['security_reads'] == 1:
                    return {'security_and_analysis': {'secret_scanning': {'status': 'disabled'},
                                                      'secret_scanning_push_protection': {'status': 'enabled'}}}
                return {'security_and_analysis': {'secret_scanning': {'status': 'enabled'},
                                                  'secret_scanning_push_protection': {'status': 'enabled'}}}
            if endpoint.endswith('/private-vulnerability-reporting'):
                state['pvuln_reads'] += 1
                if state['pvuln_reads'] == 1:
                    return {'enabled': False}
                return {'enabled': True}
            if '/contents/' in endpoint:
                path = endpoint.split('/contents/')[1].split('?')[0]
                return {'content': base64.b64encode((ROOT / path).read_bytes()).decode()}
            if endpoint in (settings.BASE + s for s in settings.SETTINGS):
                return settings.SETTINGS[endpoint.removeprefix(settings.BASE)]
            return None
        with patch.object(settings, 'api', side_effect=api):
            with patch.object(settings.time, 'sleep', lambda s: None):
                settings.activate('/gh', {'private': False}, desired)
        self.assertGreaterEqual(state['fork_reads'], 2)
        self.assertTrue(all(n >= 2 for n in state['ruleset_reads'].values()))
        self.assertGreaterEqual(state['security_reads'], 2)
        self.assertGreaterEqual(state['pvuln_reads'], 2)
