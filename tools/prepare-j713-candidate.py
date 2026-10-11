#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 studioxradio aka kb2uka
"""Join verified local inputs into a new private J713 installer kit, without imaging."""
import argparse
import copy
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys

sys.dont_write_bytecode = True
from m4_audio_package import digest, image_path, load_package, require, verified
from m4_video_package import read_json

REPO = Path(__file__).resolve().parent.parent
BASE_KIT = '88f4926265caaa9bef3d3d8e305a1759bb1fba6f8f0fe5bf8419ceedf332347f'
OLD_AUDIO = '65ddf60ccf257971bcc6b611e5a9a242a6b0f79906ddb88a3a128fae360dc8c5'
OLD_KERNEL = '69b92b6531b1f1c0748427a9f6975ae63352e3c3bb630517ca0f3eb0236242ea'
NEW_KERNEL = '2ddab32174f5a0d1153b177bb9fabc645e8d9f72d09d2fa523e78758f05290b9'
VIDEO_MANIFEST = 'etc/omarchy/j713-video-manifest.json'
BROWSER_SOURCES = {
    'usr/local/bin/brave': ('tools/brave-m4-hardware.py', 0o755),
    'usr/local/bin/brave-m4-hardware': ('tools/brave-m4-hardware.py', 0o755),
    'usr/local/bin/chromium': ('tools/brave-m4-hardware.py', 0o755),
    'usr/lib/omarchy/setup-brave-widevine.py': ('tools/setup-brave-widevine.py', 0o644),
    'usr/lib/omarchy/bootstrap-brave-widevine.py': ('tools/bootstrap-brave-widevine.py', 0o644),
}
BROWSER_PATHS = {name: value[1] for name, value in BROWSER_SOURCES.items()}
REQUIRED_BROWSER = set(BROWSER_PATHS)


def serialized(value):
    return (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()


def module(name, path):
    definition = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(definition)
    definition.loader.exec_module(loaded)
    return loaded


def closed_members(root, expected):
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*')
              if not p.is_dir() or p.is_symlink()}
    require(actual == set(expected), 'Unexpected input members')


def baseline_kit(root):
    raw = image_path(root, 'manifest.json').read_bytes()
    require(digest(raw) == BASE_KIT, 'Unqualified baseline kit')
    manifest = read_json(raw)
    closed_members(root, {'manifest.json', *manifest['files'], *manifest['links']})
    for name, entry in manifest['files'].items():
        data = verified(root, name, {'size': entry['bytes'], 'sha256': entry['sha256']})
        require(stat.S_IMODE(image_path(root, name).stat().st_mode) == entry['mode'],
                'Baseline file mode changed: ' + name)
    for name, target in manifest['links'].items():
        path = image_path(root, str(Path(name).parent)) / Path(name).name
        require(path.is_symlink() and os.readlink(path) == target, 'Baseline link changed: ' + name)
    require(manifest['private_only'] and not manifest['fresh_install_qualified'], 'Unexpected baseline state')
    return manifest


def browser_bundle(root, pin, revision):
    raw = image_path(root, 'manifest.json').read_bytes()
    require(digest(raw) == pin, 'Browser input manifest pin mismatch')
    spec = read_json(raw)
    require(set(spec) == {'schema', 'source_revision', 'files'} and
            spec['schema'] == 1 and
            re.fullmatch('[0-9a-f]{40}', spec['source_revision']) is not None, 'Browser input contract mismatch')
    require(REQUIRED_BROWSER <= set(spec['files']) <= set(BROWSER_PATHS),
            'Browser payload set is not the reviewed code-only scope')
    closed_members(root, {'manifest.json', *['root/' + n for n in spec['files']]})
    payload = {}
    for name, entry in spec['files'].items():
        require(set(entry) == {'sha256', 'size', 'mode'} and entry['mode'] == BROWSER_PATHS[name],
                'Unexpected browser metadata: ' + name)
        data = verified(root / 'root', name, entry)
        for source_revision in {revision, spec['source_revision']}:
            committed = subprocess.check_output(['git', '-C', str(REPO), 'show',
                                                 source_revision + ':' + BROWSER_SOURCES[name][0]])
            require(data == committed, 'Browser payload differs from source revision: ' + name)
        payload[name] = (data, entry['mode'])
    return payload, spec['source_revision']


def chromium_desktop(raw):
    """Preserve every desktop field/action; change only the Exec executable token."""
    text = raw.decode('utf-8')
    lines, count = [], 0
    for line in text.splitlines(keepends=True):
        if line.startswith('Exec='):
            require(re.match(r'^Exec=/usr/bin/chromium(?=\s|$)', line) is not None,
                    'Unexpected Chromium desktop Exec')
            line = line.replace('Exec=/usr/bin/chromium', 'Exec=/usr/local/bin/chromium', 1)
            count += 1
        lines.append(line)
    require(count > 0 and '[Desktop Entry]' in text, 'Missing Chromium desktop entry')
    return ''.join(lines).encode('utf-8')


def video_join(previous_raw, additions, revision):
    definition = read_json(previous_raw)
    require(definition['board'] == 'apple,j713' and definition['boot_kernel_sha256'] == OLD_KERNEL,
            'Unqualified prior video package')
    updated = copy.deepcopy(definition)
    updated['boot_kernel_sha256'] = NEW_KERNEL
    updated['source_revision'] = revision
    # Only exact bytes from the pinned prior package may be replaced in the cloned image.
    updated['allowed_previous'] = {VIDEO_MANIFEST: [digest(previous_raw)]}
    for name, (data, mode) in additions.items():
        if name in definition['files']:
            updated['allowed_previous'][name] = [definition['files'][name]['sha256']]
        updated['files'][name] = {'sha256': digest(data), 'size': len(data), 'mode': mode}
    return updated


def inventory(root):
    files, links = {}, {}
    for p in sorted(root.rglob('*')):
        name = p.relative_to(root).as_posix()
        if p.is_symlink():
            links[name] = os.readlink(p)
        elif p.is_file() and name != 'manifest.json':
            data = p.read_bytes()
            files[name] = {'sha256': digest(data), 'bytes': len(data), 'mode': stat.S_IMODE(p.stat().st_mode)}
    return files, links


def put(root, name, data, mode=0o644):
    path = image_path(root, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(mode)


def assemble(baseline, audio, audio_pin, browser, browser_pin, kernel, desktop, desktop_pin,
             output, revision, private_consent=False):
    require(private_consent, 'Private owner consent must be explicit; no public automatic CDM consent')
    require(re.fullmatch('[0-9a-f]{40}', revision) is not None and
            revision != '8fbe9b551ecfc9b749c42202bf26ed2115634f28', 'Fresh full source revision required')
    require(not output.exists() and not output.is_symlink(), 'Output must be new')
    require(output.is_absolute() and not any(p.is_symlink() for p in (output.parent, *output.parent.parents)), 'Symlink or relative output ancestor')
    require(not any(output.is_relative_to(p.resolve()) for p in (baseline, audio, browser)),
            'Output cannot be inside an input tree')
    previous = baseline_kit(baseline)
    require(digest(image_path(baseline, 'inputs/Image.gz').read_bytes()) == OLD_KERNEL, 'Baseline kernel changed')
    kernel_data = kernel.read_bytes()
    require(kernel.is_file() and not kernel.is_symlink() and digest(kernel_data) == NEW_KERNEL,
            'Unqualified successor kernel')
    audio_raw, audio_spec, audio_payload = load_package(audio)
    require(digest(audio_raw) == audio_pin, 'Unqualified joined audio package')
    closed_members(audio, {'manifest.json', *['root/' + n for n in audio_payload]})
    old_audio_raw = image_path(baseline, 'packages/audio/manifest.json').read_bytes()
    require(digest(old_audio_raw) == OLD_AUDIO, 'Baseline audio package changed')
    configure_audio = module('candidate_audio', REPO / 'tools/configure-m4-audio.py')
    configure_audio.closed_default_transition(read_json(old_audio_raw), audio_spec)
    for name in configure_audio.DEFAULT_POLICY_ADDITIONS:
        committed = subprocess.check_output(['git', '-C', str(REPO), 'show',
                                             revision + ':audio/j713/policy/' + name])
        require(audio_payload[name] == committed, 'Audio policy differs from source revision: ' + name)
    additions, browser_revision = browser_bundle(browser, browser_pin, revision)
    desktop_raw = desktop.read_bytes()
    require(not desktop.is_symlink() and digest(desktop_raw) == desktop_pin, 'Chromium desktop pin mismatch')
    additions['etc/skel/.local/share/applications/chromium.desktop'] = (chromium_desktop(desktop_raw), 0o644)
    for profile in ('Brave-Browser', 'Brave-Browser-M4', 'Chromium'):
        name = 'etc/skel/.config/omarchy/pending/widevine-' + profile + '.json'
        additions[name] = (serialized({'schema': 1, 'profile': profile, 'consent': 'owner-requested'}), 0o600)
    old_video_raw = image_path(baseline, 'packages/video/manifest.json').read_bytes()
    video = video_join(old_video_raw, additions, revision)
    # Freeze all source bytes in memory before creating the successor tree.
    sources = {name: (REPO / 'tools' / name).read_bytes() for name in (
        'configure-m4-audio.py', 'm4_audio_package.py', 'm4_video_package.py',
        'configure-j713-installer.py', 'prepare-j713-candidate.py', 'package-j713-candidate.py')}
    for name, data in sources.items():
        committed = subprocess.check_output(['git', '-C', str(REPO), 'show', revision + ':tools/' + name])
        require(committed == data, 'Assembly source differs from source revision: ' + name)
    source_paths = ('patches-kernel/j713-input/dockchannel-start-retry.patch',
                    'docs/j713-input-startup-retry.md', 'docs/j713-private-candidate.md',
                    'LICENSE.md', 'THIRD_PARTY_NOTICES.md')
    source_overlay = {}
    for name in source_paths:
        data = (REPO / name).read_bytes()
        require(subprocess.check_output(['git', '-C', str(REPO), 'show', revision + ':' + name]) == data,
                'Source overlay differs from revision: ' + name)
        source_overlay[name] = data
    shutil.copytree(baseline, output, symlinks=True)
    baseline_kit(output)  # Refuse a changing reference rather than publishing a mixed copy.
    put(output, 'inputs/Image.gz', kernel_data)
    for name, data in source_overlay.items():
        put(output, 'source/successor/' + name, data)
    for name, data in sources.items():
        destination = 'prepare-image.py' if name == 'configure-j713-installer.py' else 'tools/' + name
        put(output, destination, data, 0o755 if name.endswith('.py') and name.startswith(('configure', 'prepare')) else 0o644)
    for name, data in audio_payload.items():
        put(output, 'packages/audio/root/' + name, data, audio_spec['file_metadata'][name]['mode'])
    put(output, 'packages/audio/manifest.json', audio_raw)
    for name, (data, mode) in additions.items():
        put(output, 'packages/video/root/' + name, data, mode)
    video_raw = serialized(video)
    put(output, 'packages/video/manifest.json', video_raw)
    put(output, 'source/video-package/qualified.json', serialized({k: v for k, v in video.items() if k != 'redistributable'}))
    put(output, 'inputs/chromium.desktop', desktop_raw)
    files, links = inventory(output)
    manifest = {**previous, 'files': files, 'links': links, 'source_revision': revision,
                'release_version': '0.1.0-alpha.1', 'private_build': 2,
                'native_qualified': False, 'fresh_install_qualified': False,
                'previous_kit_sha256': BASE_KIT, 'previous_audio_package_sha256': OLD_AUDIO,
                'audio_package_sha256': digest(audio_raw), 'video_package_sha256': digest(video_raw),
                'browser_input_manifest_sha256': browser_pin, 'browser_source_revision': browser_revision,
                'chromium_desktop_sha256': desktop_pin, 'private_owner_consent': True}
    raw = serialized(manifest)
    put(output, 'manifest.json', raw)
    return {'status': 'assembled_unqualified', 'kit_sha256': digest(raw),
            'source_revision': revision, 'private_build': 2, 'files': len(files),
            'audio_package_sha256': digest(audio_raw), 'video_package_sha256': digest(video_raw),
            'native_qualified': False, 'final_delivery_created': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('baseline-kit', 'audio-package', 'browser-bundle', 'kernel', 'chromium-desktop', 'output'):
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--audio-manifest-sha256', required=True)
    p.add_argument('--browser-manifest-sha256', required=True)
    p.add_argument('--chromium-desktop-sha256', required=True)
    p.add_argument('--source-revision', required=True)
    p.add_argument('--private-owner-consent', action='store_true')
    a = p.parse_args()
    print(json.dumps(assemble(a.baseline_kit, a.audio_package, a.audio_manifest_sha256, a.browser_bundle,
                             a.browser_manifest_sha256, a.kernel, a.chromium_desktop,
                             a.chromium_desktop_sha256, a.output, a.source_revision,
                             a.private_owner_consent), indent=2))


if __name__ == '__main__':
    main()
