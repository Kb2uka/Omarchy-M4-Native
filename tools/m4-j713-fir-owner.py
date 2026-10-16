#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Reproduce exact J713 FIR files in the pinned macOS development environment."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_BUNDLE = (SCRIPT_DIR / 'bundle' if (SCRIPT_DIR / 'bundle').is_dir()
                  else SCRIPT_DIR.parent / 'audio/j713/owner-fir')
SOURCE_NAMES = {
    'inspect_strip.py', 'measure-transfer.c', 'run-transfer.py',
    'measure-memory.c', 'run-memory.py', 'analyze_transfer.py', 'qualify_memory.py',
}
HELPER_NAMES = {'measure-transfer', 'measure-memory'}
WHEEL = 'numpy-2.5.3-cp313-cp313-macosx_14_0_arm64.whl'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def checked_bytes(path, record):
    require(path.is_file() and not path.is_symlink(), 'Missing regular input: ' + str(path))
    require(path.stat().st_size == record['size'], 'Unsupported input size: ' + str(path))
    data = path.read_bytes()
    require(hashlib.sha256(data).hexdigest() == record['sha256'],
            'Unsupported input hash: ' + str(path))
    return data


def qualified_input(record):
    """Read an installed owner input that matches exactly one qualified build."""
    qualified = record['qualified']
    require(isinstance(qualified, list) and qualified and
            all(set(entry) <= {'sha256', 'size', 'macos'} and
                isinstance(entry['sha256'], str) and
                re.fullmatch('[0-9a-f]{64}', entry['sha256']) and
                type(entry['size']) is int and entry['size'] > 0
                for entry in qualified) and
            len({entry['sha256'] for entry in qualified}) == len(qualified),
            'Invalid qualified input list: ' + record['path'])
    path = Path(record['path'])
    require(path.is_file() and not path.is_symlink(), 'Missing regular input: ' + str(path))
    size = path.stat().st_size
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    matches = [entry for entry in qualified
               if entry['size'] == size == len(data) and entry['sha256'] == digest]
    require(len(matches) == 1, 'Unsupported input hash: ' + str(path))
    return data, matches[0]


def unpack_wheel(data_path, destination):
    """Extract the pinned wheel without executing pip or making network requests."""
    with zipfile.ZipFile(data_path) as archive:
        entries = archive.infolist()
        require(sum(entry.file_size for entry in entries) < 100_000_000,
                'Unexpected numerical dependency size')
        names = set()
        for entry in entries:
            name = PurePosixPath(entry.filename)
            require(not name.is_absolute() and '..' not in name.parts and
                    '\\' not in entry.filename and entry.filename not in names,
                    'Invalid numerical dependency path')
            require(not stat.S_ISLNK(entry.external_attr >> 16),
                    'Unexpected numerical dependency symlink')
            names.add(entry.filename)
        archive.extractall(destination)


def run_script(source, work, dependencies, name):
    # Isolated Python uses only the retained source and wheel in addition to its
    # own standard library. The retained programs keep their original math.
    bootstrap = (
        'import runpy,sys; '
        'sys.path[:0]=sys.argv[1:3]; '
        'runpy.run_path(sys.argv[3],run_name="__main__")'
    )
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(('PYTHON', 'DYLD_'))}
    environment.update(PATH='/usr/bin:/bin', OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1')
    # The relocated executable has an absolute Python.framework load command.
    # Derive its search paths from the running bundle, never inherited overrides.
    versions = [parent for parent in Path(sys.executable).resolve().parents
                if parent.name == '3.13' and parent.parent.name == 'Versions'
                and parent.parent.parent.name == 'Python.framework']
    require(len(versions) == 1, "Run with the reproduction environment's Python.framework.")
    version = versions[0]
    require((version / 'Python').is_file() and (version / 'lib').is_dir(),
            'Incomplete reproduction Python framework.')
    environment.update(DYLD_FRAMEWORK_PATH=str(version.parent.parent.parent),
                       DYLD_LIBRARY_PATH=str(version / 'lib'))
    result = subprocess.run(
        [sys.executable, '-I', '-B', '-c', bootstrap, str(source), str(dependencies),
         str(source / name)], cwd=work, env=environment,
        capture_output=True, text=True, timeout=240,
    )
    require(result.returncode == 0,
            'Filter reproduction stopped at ' + name + ': ' + result.stderr[-2000:].strip())


def generate(bundle, output):
    require(platform.system() == 'Darwin' and platform.machine() == 'arm64',
            'Filter reproduction requires native arm64 macOS.')
    require(sys.version_info[:3] == (3, 13, 9) and not sys.flags.optimize,
            "Use the reproduction environment's CPython 3.13.9 without optimization.")
    require(output.is_absolute() and output.name not in ('', '.', '..'),
            '--output must name a new absolute directory.')
    require(not output.exists() and not output.is_symlink(),
            'Output already exists; choose a new directory.')
    output = output.parent.resolve(strict=True) / output.name
    bundle = bundle.resolve(strict=True)
    manifest_bytes = (bundle / 'manifest.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    require(manifest['schema'] == 2 and manifest['board'] == 'apple,j713',
            'Unsupported filter reproduction bundle.')
    require(set(manifest['sources']) == SOURCE_NAMES and set(manifest['helpers']) == HELPER_NAMES,
            'Incomplete filter reproduction bundle.')
    require(manifest['numpy']['filename'] == WHEEL, 'Unsupported numerical dependency.')
    for name, record in manifest['sources'].items():
        checked_bytes(bundle / 'src' / name, record)
    for name, record in manifest['helpers'].items():
        checked_bytes(bundle / 'bin' / name, record)
    wheel_path = bundle / 'dependencies' / WHEEL
    checked_bytes(wheel_path, manifest['numpy'])
    # Several macOS builds may carry different qualified inputs. Each must
    # still render the single set of pinned outputs checked below.
    owner_inputs, matched = {}, {}
    for name, record in manifest['owner_inputs'].items():
        owner_inputs[name], matched[name] = qualified_input(record)

    with tempfile.TemporaryDirectory(prefix='.m4-fir-owner-', dir=output.parent) as temporary:
        work = Path(temporary)
        dependencies = work / 'dependencies'
        unpack_wheel(wheel_path, dependencies)
        source = bundle / 'src'
        (work / 'source').mkdir()
        for name in ('builtin_speaker_out_general.austrip', 'builtin_speaker_out_general.propstrip'):
            (work / 'source' / name).write_bytes(owner_inputs[name])
        for name in HELPER_NAMES:
            path = work / name
            path.write_bytes(checked_bytes(bundle / 'bin' / name, manifest['helpers'][name]))
            path.chmod(0o700)
        (work / 'owner-inputs.json').write_text(
            json.dumps({name: entry['sha256'] for name, entry in matched.items()}))

        run_script(source, work, dependencies, 'inspect_strip.py')
        for name, record in manifest['presets'].items():
            checked_bytes(work / 'decoded' / name, record)
        print('Reproducing J713 audio filters...', flush=True)
        (work / 'output').mkdir()
        run_script(source, work, dependencies, 'run-transfer.py')
        (work / 'output').rename(work / 'native-transfer')
        (work / 'output').mkdir()
        run_script(source, work, dependencies, 'run-memory.py')
        (work / 'output').rename(work / 'native-memory')
        run_script(source, work, dependencies, 'qualify_memory.py')

        files = {}
        require(set(manifest['outputs']) == {
            'j713-static-minimal-lane' + str(lane) + '-48000.wav' for lane in range(4)
        }, 'Unexpected filter output set.')
        for name, record in manifest['outputs'].items():
            files[name] = checked_bytes(work / 'output' / name, record)
        # Recheck the installed inputs before writing the reproduction outputs.
        for name, record in manifest['owner_inputs'].items():
            require(qualified_input(record)[1] == matched[name],
                    'Owner input changed during generation: ' + record['path'])
        receipt = {
            'schema': 2,
            'board': 'apple,j713',
            'method': 'owner-local AudioDSP memory rendering and retained finite FIR pipeline',
            'bundle_manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest(),
            'python_version': platform.python_version(),
            'numpy_version': '2.5.3',
            'macos_version': platform.mac_ver()[0],
            'owner_inputs': {name: {'path': record['path'], **matched[name]}
                             for name, record in manifest['owner_inputs'].items()},
            'files': manifest['outputs'],
            'exact_native_outputs': True,
            'hardware_playback_performed': False,
        }
        output.mkdir(mode=0o700)
        try:
            for name, data in files.items():
                with (output / name).open('xb') as stream:
                    stream.write(data)
            with (output / 'receipt.json').open('x') as stream:
                json.dump(receipt, stream, indent=2, sort_keys=True)
                stream.write('\n')
        except BaseException:
            shutil.rmtree(output)
            raise
    print('J713 filter reproduction ready: ' + str(output), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path, help='new absolute output directory')
    parser.add_argument('--bundle', type=Path, default=DEFAULT_BUNDLE,
                        help='retained owner-fir reproduction bundle directory')
    args = parser.parse_args()
    try:
        generate(args.bundle, args.output)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError,
            zipfile.BadZipFile) as error:
        print('J713 filter reproduction did not complete: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
