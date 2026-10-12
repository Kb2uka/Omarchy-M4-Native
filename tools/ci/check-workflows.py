#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Check the repository's small, hosted-only workflow security contract."""
from pathlib import Path
import re
import sys
import yaml

ROOT = Path(__file__).resolve().parents[2]
JOBS = {'ci.yml': {'policy', 'tests', 'speaker-daemon', 'macos'},
        'security.yml': {'secrets', 'malware', 'workflow-lint', 'dependencies'},
        'codeql.yml': {'analyze'}}
ACTIONS = {'actions/checkout', 'actions/setup-python',
           'actions/dependency-review-action', 'github/codeql-action/init',
           'github/codeql-action/analyze'}


def check(workflows):
    errors = []
    def need(condition, message):
        if not condition:
            errors.append(message)
    need(set(workflows) == set(JOBS), 'Unexpected or missing workflow')
    for filename, flow in workflows.items():
        if not isinstance(flow, dict) or filename not in JOBS:
            errors.append(f'{filename}: unknown workflow or invalid document')
            continue
        triggers = flow.get('on', {})
        need(isinstance(triggers, dict), f'{filename}: explicit triggers required')
        if not isinstance(triggers, dict):
            continue
        need(set(triggers) <= {'pull_request', 'push', 'schedule', 'workflow_dispatch'},
             f'{filename}: unsafe workflow trigger')
        for event in ('pull_request', 'push'):
            need(triggers.get(event) == {'branches': ['develop', 'main']},
                 f'{filename}: {event} must cover both branches without path filters')
        need(flow.get('permissions') == {'contents': 'read'}, f'{filename}: read-only default token required')
        need('env' not in flow, f'{filename}: workflow-wide environment not allowed')
        jobs = flow.get('jobs', {})
        need(set(jobs) == JOBS[filename], f'{filename}: required jobs changed')
        for job_id, job in jobs.items():
            label = f'{filename}/{job_id}'
            need(isinstance(job, dict), label + ': invalid job')
            if not isinstance(job, dict):
                continue
            need(job.get('runs-on') in ('ubuntu-24.04', 'macos-14'), label + ': standard hosted runner required')
            need('uses' not in job and 'container' not in job and 'services' not in job,
                 label + ': reusable jobs or containers require separate review')
            need(job.get('continue-on-error', 'false') == 'false', label + ': cannot ignore failure')
            limit = job.get('timeout-minutes', '')
            need(str(limit).isdigit() and 0 < int(limit) <= 30, label + ': bounded timeout required')
            expected = {'contents': 'read', 'security-events': 'write'} if filename == 'codeql.yml' else {'contents': 'read'}
            need(job.get('permissions', flow['permissions']) == expected, label + ': unexpected token permissions')
            condition = job.get('if')
            if filename == 'codeql.yml':
                need(condition == '!github.event.repository.private', label + ': public-only CodeQL required')
            elif job_id == 'dependencies':
                need(condition == "github.event_name == 'pull_request' && !github.event.repository.private", label + ': unexpected dependency gate')
            else:
                need(condition is None, label + ': required job cannot be conditional')
            need('env' not in job, label + ': job-wide environment not allowed')
            for step in job.get('steps', []):
                need('if' not in step, label + ': checks must not be skipped')
                need(step.get('continue-on-error', 'false') == 'false', label + ': step cannot ignore failure')
                serialized = str(step)
                need('secrets.' not in serialized and 'secrets[' not in serialized,
                     label + ': repository secrets forbidden')
                if 'uses' in step:
                    action, _, revision = step['uses'].partition('@')
                    need(action in ACTIONS and re.fullmatch('[0-9a-f]{40}', revision), label + ': action must be allowlisted and SHA pinned')
                    if action == 'actions/checkout':
                        need(step.get('with', {}).get('persist-credentials') == 'false', label + ': checkout credentials must not persist')
                        need('ref' not in step.get('with', {}), label + ': checkout must test the event revision')
                if 'run' in step:
                    need('${{' not in step['run'], label + ': put event data in environment, not shell source')
    return errors


def load(root):
    return {p.name: yaml.load(p.read_text(), Loader=yaml.BaseLoader)
            for p in (root / '.github/workflows').glob('*.y*ml')}


def main():
    errors = check(load(ROOT))
    for error in errors:
        print(error, file=sys.stderr)
    if errors:
        return 1
    print('Hosted runner and workflow permission policy passed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
