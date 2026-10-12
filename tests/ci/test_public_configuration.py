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
        rules = {rule['type']: rule for rule in common['rules']}
        review = rules['pull_request']['parameters']
        self.assertTrue(review['require_code_owner_review'])
        self.assertTrue(review['dismiss_stale_reviews_on_push'])
        checks = rules['required_status_checks']['parameters']['required_status_checks']
        self.assertEqual({c['context'] for c in checks}, set(settings.CHECKS))
        self.assertTrue(all(c['integration_id'] == 15368 for c in checks))
        self.assertEqual(rules['code_scanning']['parameters']['code_scanning_tools'][0]['security_alerts_threshold'], 'medium_or_higher')
        self.assertEqual(main['conditions']['ref_name']['include'], ['refs/heads/main'])
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
