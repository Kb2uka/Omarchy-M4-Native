# SPDX-License-Identifier: MIT
import copy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('policy', ROOT / 'tools/ci/check-workflows.py')
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


class WorkflowPolicyTests(unittest.TestCase):
    def setUp(self):
        self.flows = policy.load(ROOT)

    def test_shipped_workflows(self):
        self.assertEqual(policy.check(self.flows), [])

    def test_missing_security_workflow_fails(self):
        del self.flows['security.yml']
        self.assertTrue(policy.check(self.flows))

    def test_untrusted_workflow_mutations_fail(self):
        mutations = [
            lambda f: f['ci.yml']['on'].update({'pull_request_target': {}}),
            lambda f: f['ci.yml']['on']['pull_request'].update({'paths': ['docs/**']}),
            lambda f: f['ci.yml']['jobs']['tests'].update({'runs-on': ['self-hosted', 'linux']}),
            lambda f: f['ci.yml'].update({'permissions': 'write-all'}),
            lambda f: f['ci.yml']['jobs']['tests'].update({'permissions': {'contents': 'write'}}),
            lambda f: f['ci.yml']['jobs']['tests'].update({'if': 'false'}),
            lambda f: f['ci.yml']['jobs']['tests'].update({'continue-on-error': 'true'}),
            lambda f: f['ci.yml']['jobs']['tests'].pop('timeout-minutes'),
            lambda f: f['security.yml']['jobs'].pop('malware'),
            lambda f: f['ci.yml']['jobs']['tests']['steps'][0].update({'uses': 'actions/checkout@v6'}),
            lambda f: f['ci.yml']['jobs']['tests']['steps'][0]['with'].update({'persist-credentials': 'true'}),
            lambda f: f['ci.yml']['jobs']['tests']['steps'][0]['with'].update({'ref': 'main'}),
            lambda f: f['ci.yml']['jobs']['tests']['steps'].append({'run': 'echo ${{ github.event.pull_request.title }}'}),
            lambda f: f['ci.yml']['jobs']['tests']['steps'].append({'run': 'scanner', 'env': {'TOKEN': '${{ secrets.TOKEN }}'}}),
            lambda f: f['ci.yml']['jobs']['tests']['steps'].append({'run': 'scanner', 'continue-on-error': 'true'}),
            lambda f: f['ci.yml']['jobs']['tests']['steps'].append({'run': 'scanner', 'if': 'false'}),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                changed = copy.deepcopy(self.flows)
                mutate(changed)
                self.assertTrue(policy.check(changed))


if __name__ == '__main__':
    unittest.main()
