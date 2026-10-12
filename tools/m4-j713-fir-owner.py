#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Generate exact J713 FIR files from the owner's installed macOS inputs."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile


DEFAULT_BUNDLE = Path(__file__).resolve().parents[1] / 'audio/j713/owner-fir'
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
    # Isolated Python uses only the shipped source and wheel in addition to its
    # own standard library. The retained programs keep their original math.
    bootstrap = (
        'import runpy,sys; '
        'sys.path[:0]=sys.argv[1:3]; '
        'runpy.run_path(sys.argv[3],run_name="__main__")'
    )
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(('PYTHON', 'DYLD_'))}
    environment.update(PATH='/usr/bin:/bin', OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1')
    result = subprocess.run(
        [sys.executable, '-I', '-B', '-c', bootstrap, str(source), str(dependencies),
         str(source / name)], cwd=work, env=environment,
        capture_output=True, text=True, timeout=240,
    )
    require(result.returncode == 0,
            'Owner filter generation stopped at ' + name + ': ' + result.stderr[-2000:].strip())


def generate(bundle, output):
    require(platform.system() == 'Darwin' and platform.machine() == 'arm64',
            'Owner filter generation requires native arm64 macOS.')
    require(sys.version_info[:3] == (3, 13, 9) and not sys.flags.optimize,
            'Use the installer\'s bundled Python 3.13.9 without optimization.')
    require(output.is_absolute() and output.name not in ('', '.', '..'),
            '--output must name a new absolute directory.')
    require(not output.exists() and not output.is_symlink(),
            'Output already exists; choose a new directory.')
    output = output.parent.resolve(strict=True) / output.name
    bundle = bundle.resolve(strict=True)
    manifest_bytes = (bundle / 'manifest.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    require(manifest['schema'] == 1 and manifest['board'] == 'apple,j713',
            'Unsupported owner filter bundle.')
    require(set(manifest['sources']) == SOURCE_NAMES and set(manifest['helpers']) == HELPER_NAMES,
            'Incomplete owner filter bundle.')
    require(manifest['numpy']['filename'] == WHEEL, 'Unsupported numerical dependency.')
    for name, record in manifest['sources'].items():
        checked_bytes(bundle / 'src' / name, record)
    for name, record in manifest['helpers'].items():
        checked_bytes(bundle / 'bin' / name, record)
    wheel_path = bundle / 'dependencies' / WHEEL
    checked_bytes(wheel_path, manifest['numpy'])
    owner_inputs = {}
    for name, record in manifest['owner_inputs'].items():
        path = Path(record['path'])
        owner_inputs[name] = checked_bytes(path, record)

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

        run_script(source, work, dependencies, 'inspect_strip.py')
        for name, record in manifest['presets'].items():
            checked_bytes(work / 'decoded' / name, record)
        print('Generating the owner\'s J713 audio filters...', flush=True)
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
        # Recheck the installed inputs after generation. No unrecognized input
        # or partially generated output becomes an installation payload.
        for record in manifest['owner_inputs'].values():
            checked_bytes(Path(record['path']), record)
        receipt = {
            'schema': 1,
            'board': 'apple,j713',
            'method': 'owner-local AudioDSP memory rendering and retained finite FIR pipeline',
            'bundle_manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest(),
            'python_version': platform.python_version(),
            'numpy_version': '2.5.3',
            'owner_inputs': manifest['owner_inputs'],
            'files': manifest['outputs'],
            'exact_qualified_outputs': True,
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
    print('Owner audio filters ready: ' + str(output), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path, help='new absolute output directory')
    parser.add_argument('--bundle', type=Path, default=DEFAULT_BUNDLE,
                        help='owner-fir bundle directory supplied with the installer')
    args = parser.parse_args()
    try:
        generate(args.bundle, args.output)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError,
            zipfile.BadZipFile) as error:
        print('Cannot prepare J713 audio filters: ' + str(error), file=sys.stderr)
        print('No installation changes were made. This macOS input or generator output '
              'is not supported by this installer.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
