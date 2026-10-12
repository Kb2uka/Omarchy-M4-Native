#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Assemble exact qualified runtime inputs; never download or invent payloads."""
import argparse
import json
from pathlib import Path
import shutil
from m4_audio_package import digest, require, verified

REPO = Path(__file__).resolve().parent.parent


def assemble(inputs, readiness, output, local_only=False):
    spec = json.loads((REPO / 'audio/j713/qualified.json').read_text())
    require(local_only, 'Redistribution unresolved for measured FIRs; explicit --local-only required')
    require(not output.exists(), 'Output must be new')
    payload = {}
    # Inputs mirror final absolute paths, without a leading slash. All pins are source-controlled.
    for name, entry in spec['files'].items():
        if name == 'usr/lib/systemd/system/speakersafetyd-j713.service':
            continue
        origin = REPO / 'audio/j713' if 'source' in entry else inputs
        payload[name] = (verified(origin, entry.get('source', name), entry), entry['mode'])
    repo_sources = {
        'usr/lib/omarchy/m4-j713-audio-register.py': REPO / 'tools/m4-j713-audio-register.py',
        'usr/lib/systemd/system/m4-j713-audio-register.service': REPO / 'packaging/j713-audio/m4-j713-audio-register.service',
    }
    readiness_sources = {
        'usr/lib/omarchy/m4-j713-audio-ready.py': 'tools/m4-j713-audio-ready.py',
        'usr/lib/systemd/system/speakersafetyd-j713.service': 'packaging/j713-audio/speakersafetyd-j713.service',
        'etc/systemd/user/wireplumber.service.d/80-j713-audio-ready.conf': 'packaging/j713-audio/wireplumber.service.d/80-j713-audio-ready.conf',
    }
    require(set(readiness_sources.values()) == set(spec['readiness_inputs']),
            'Readiness source/pin set mismatch')
    for name, relative in readiness_sources.items():
        payload[name] = (verified(readiness, relative, spec['readiness_inputs'][relative]), 0o644)
    for name, source in repo_sources.items():
        require(source.is_file() and not source.is_symlink(), 'Missing source integration: ' + str(source))
        payload[name] = (source.read_bytes(), 0o644)
    manifest = {key: value for key, value in spec.items() if key != 'files'}
    manifest['redistributable'] = False
    manifest['file_metadata'] = {name: dict(sha256=digest(data), size=len(data), mode=mode)
                                 for name, (data, mode) in sorted(payload.items())}
    manifest['files'] = {'/' + name: entry['sha256'] for name, entry in manifest['file_metadata'].items()}
    manifest['modules'] = {name: entry['note'] for name, entry in manifest['module_artifacts'].items()}
    manifest['symlinks'] = {'etc/systemd/system/multi-user.target.wants/' + name:
                           '/usr/lib/systemd/system/' + name
                           for name in ('m4-j713-audio-register.service', 'speakersafetyd-j713.service')}
    # Only after every input validates do we create the package.
    output.mkdir(parents=True)
    for name, (data, mode) in payload.items():
        target = output / 'root' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(mode)
    raw = (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode()
    (output / 'manifest.json').write_bytes(raw)
    return dict(manifest_sha256=digest(raw), files=len(payload), redistributable=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--readiness-source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--local-only', action='store_true')
    args = parser.parse_args()
    print(json.dumps(assemble(args.inputs, args.readiness_source, args.output, args.local_only), indent=2))
