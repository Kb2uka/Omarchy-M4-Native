#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Give GStreamer apps sound on J713 installs that lack gst-plugins-good.

The alpha.1 image omits gst-plugins-good, so GStreamer has no autoaudiosink
and every WebKitGTK app (Zeus Link, for one) plays silence. The ARM mirrors
no longer carry the build that matches the installed GStreamer, and the newer
one needs a full system update. This builds only the autodetect and pulse
plugins from the hash-pinned upstream source matching the installed GStreamer,
and installs them for the current user. WebKitGTK plays sound from a bubblewrap
sandbox that hides the user plugin directory, so it also names that directory
in GST_PLUGIN_PATH for the desktop session; WebKitGTK exposes the directories
listed there. Inspection is the default.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

SOURCES = {
    # Upstream release tarball SHA256, from gstreamer.freedesktop.org/src.
    '1.28.7': '87256969c82cf3bc8574301f3e7044a90de0ac500a5a27d8ba38c4dde894dd8b',
}
SOURCE_URL = 'https://gstreamer.freedesktop.org/src/gst-plugins-good/gst-plugins-good-{0}.tar.xz'
BUILD_TOOLS = (
    'meson==1.12.1 --hash=sha256:930bc7542cbd9f57009e182fd014eba48cf1a0180a7b9006d2d32d8f168d8b02\n'
    'ninja==1.13.2 --hash=sha256:d775a5e43e9088f507a6250d57fcf5678eb31268c545feb5064ffeee33735622\n'
)
MESON_OPTIONS = ('--buildtype=release', '-Dauto_features=disabled', '-Dautodetect=enabled',
                 '-Dpulse=enabled', '-Dtests=disabled', '-Dexamples=disabled', '-Ddoc=disabled',
                 '-Dnls=disabled', '-Dorc=disabled', '-Dpackage-origin=omarchy-m4-native')
PLUGINS = {'libgstautodetect.so': 'gst/autodetect', 'libgstpulseaudio.so': 'ext/pulse'}
ELEMENTS = ('autoaudiosink', 'pulsesink')
RESTART = 'log out and back in, then restart apps to get sound'
RECEIPT = '.m4-webkit-audio.json'
# gst-plugins-good itself, or the M4 channel's package of just its two sinks.
SYSTEM_PACKAGES = ('gst-plugins-good', 'omarchy-m4-gst-audio')
SESSION = '60-m4-webkit-audio.conf'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def plugin_dir(environment):
    data = environment.get('XDG_DATA_HOME') or str(Path(environment['HOME']) / '.local/share')
    return Path(data) / 'gstreamer-1.0/plugins'


def session_file(environment):
    config = environment.get('XDG_CONFIG_HOME') or str(Path(environment['HOME']) / '.config')
    return Path(config) / 'environment.d' / SESSION


def expressible(directory):
    # environment.d expands $ and treats quotes and spaces specially, and
    # GST_PLUGIN_PATH splits at colons.
    return not set(str(directory)) & set('$\\"\' \t\n`:')


def session_text(directory):
    require(expressible(directory),
            'Plugin directory has characters environment.d cannot hold: ' + str(directory))
    # systemd reads environment.d at login; ${...:+...} keeps any existing value.
    return ('# Written by m4-webkit-audio-plugins.py; removed by its --remove.\n'
            '# WebKitGTK runs web content in a sandbox that only exposes GStreamer\n'
            '# plugin directories listed in GST_PLUGIN_PATH.\n'
            'GST_PLUGIN_PATH=' + str(directory) + '${GST_PLUGIN_PATH:+:$GST_PLUGIN_PATH}\n')


def session_state(environment, directory):
    """'present' when ours, 'missing', or 'foreign' when something else is there."""
    path = session_file(environment)
    if not path.exists() and not path.is_symlink():
        return 'missing'
    # This tool never writes a file for a directory it cannot express.
    if (expressible(directory) and path.is_file() and not path.is_symlink() and
            path.read_text() == session_text(directory)):
        return 'present'
    return 'foreign'


def own_session(environment, directory):
    """The session state, refusing a file this tool did not write."""
    state = session_state(environment, directory)
    require(state != 'foreign', 'A session file this tool does not own already exists: ' +
            str(session_file(environment)))
    return state


def write_session(environment, directory):
    if own_session(environment, directory) == 'missing':
        path = session_file(environment)
        # The file lands by an atomic rename, so a leftover temporary file is
        # only this tool's interrupted write and holds nothing worth keeping.
        temp = path.with_name(SESSION + '.new')
        if temp.is_symlink() or temp.is_file():
            temp.unlink()
        path.parent.mkdir(parents=True, exist_ok=True)
        write(path, session_text(directory).encode())


def package_version(run, name):
    result = run(['pacman', '-Q', name], capture_output=True, text=True)
    if result.returncode:
        return None
    fields = result.stdout.split()
    require(len(fields) == 2 and fields[0] == name, 'Unexpected pacman output for ' + name)
    return fields[1]


def read_receipt(directory):
    path = directory / RECEIPT
    if not path.exists():
        return None
    require(path.is_file() and not path.is_symlink(), 'Receipt must be a regular file')
    receipt = json.loads(path.read_text())
    require(set(receipt.get('plugins', {})) == set(PLUGINS), 'Unrecognized receipt')
    return receipt


def installed(directory, receipt):
    """True when every recorded plugin is present and unchanged."""
    for name, digest in receipt['plugins'].items():
        path = directory / name
        if not path.is_file() or path.is_symlink() or sha(path.read_bytes()) != digest:
            return False
    return True


def fetch(url):
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def validate(run, advice):
    for element in ELEMENTS:
        result = run(['gst-inspect-1.0', '--exists', element])
        require(result.returncode == 0, 'GStreamer cannot load ' + element + '; ' + advice)


def build(version, scratch, run, download):
    source = download(SOURCE_URL.format(version))
    require(sha(source) == SOURCES[version], 'Source checksum mismatch; nothing was installed')
    archive = scratch / 'source.tar.xz'
    archive.write_bytes(source)
    with tarfile.open(archive) as stream:
        stream.extractall(scratch, filter='data')
    tree = scratch / ('gst-plugins-good-' + version)
    require(tree.is_dir(), 'Unexpected source layout')
    venv = scratch / 'tools'
    run([sys.executable, '-m', 'venv', str(venv)], check=True, stdout=sys.stderr)
    requirements = scratch / 'requirements.txt'
    requirements.write_text(BUILD_TOOLS)
    run([str(venv / 'bin/pip'), 'install', '--quiet', '--require-hashes', '--only-binary=:all:',
         '-r', str(requirements)], check=True, stdout=sys.stderr)
    environment = dict(os.environ, PATH=str(venv / 'bin') + os.pathsep + os.environ.get('PATH', ''))
    output = scratch / 'build'
    run([str(venv / 'bin/meson'), 'setup', str(output), str(tree), *MESON_OPTIONS],
        check=True, env=environment, stdout=sys.stderr)
    run([str(venv / 'bin/ninja'), '-C', str(output)], check=True, env=environment, stdout=sys.stderr)
    return {name: (output / subdir / name).read_bytes() for name, subdir in PLUGINS.items()}


def write(path, data):
    temp = path.with_name(path.name + '.new')
    require(not temp.exists() and not temp.is_symlink(),
            'Stale temporary file from an interrupted run; run --remove first: ' + str(temp))
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
    os.replace(temp, path)


def install(environment, run=subprocess.run, download=fetch, apply=False):
    require(os.getuid() != 0, 'Run as the desktop user, without sudo')
    directory = plugin_dir(environment)
    report = {'plugin_dir': str(directory), 'session_file': str(session_file(environment)),
              'applied': False}
    # Either package puts both sinks in the system plugin directory, which every
    # app and WebKitGTK's sandbox can see.
    provider = None
    for name in SYSTEM_PACKAGES:
        version = package_version(run, name)
        if version:
            provider = (name, version)
            break
    receipt = read_receipt(directory)
    if provider:
        report.update(status='not needed: ' + ' '.join(provider) + ' is installed')
        if receipt or session_state(environment, directory) == 'present':
            report['note'] = 'remove the local plugins with --remove'
        return report
    full = package_version(run, 'gstreamer')
    require(full, 'GStreamer is not installed')
    version = full.split(':')[-1].rsplit('-', 1)[0]
    report['gstreamer'] = full
    if receipt and receipt.get('gstreamer') == full and installed(directory, receipt):
        validate(run, 'run --remove, then --apply again')
        if own_session(environment, directory) == 'present':
            report['status'] = 'already installed'
        elif not apply:
            report['status'] = 'plugins installed; session setting missing, run --apply'
        else:
            write_session(environment, directory)
            report.update(applied=True, status='session setting added; ' + RESTART)
        return report
    require(version in SOURCES, 'No pinned source for GStreamer ' + version +
            '; update the whole system and install gst-plugins-good instead')
    require(receipt is None, 'Existing local plugins differ; run --remove first')
    for name in PLUGINS:
        require(not (directory / name).exists() and not (directory / name).is_symlink(),
                'A plugin file this tool does not own already exists: ' + str(directory / name))
    for name in (*PLUGINS, RECEIPT):
        temp = directory / (name + '.new')
        require(not temp.exists() and not temp.is_symlink(),
                'Stale temporary file from an interrupted run; run --remove first: ' + str(temp))
    own_session(environment, directory)
    session_text(directory)  # refuses a directory environment.d cannot hold, before any download
    report['status'] = 'build available'
    if not apply:
        return report
    with tempfile.TemporaryDirectory(prefix='m4-webkit-audio-') as scratch:
        plugins = build(version, Path(scratch), run, download)
    directory.mkdir(parents=True, exist_ok=True)
    digests = {name: sha(data) for name, data in plugins.items()}
    # The receipt goes first, so --remove can clean up after any interruption.
    write(directory / RECEIPT, (json.dumps({'gstreamer': full, 'plugins': digests},
                                           indent=2, sort_keys=True) + '\n').encode())
    try:
        for name, data in plugins.items():
            write(directory / name, data)
        validate(run, 'the plugins were removed again')
        write_session(environment, directory)
    except BaseException:
        remove(environment)
        raise
    report.update(applied=True, status='installed; ' + RESTART, plugins=digests)
    return report


def remove(environment):
    require(os.getuid() != 0, 'Run as the desktop user, without sudo')
    directory = plugin_dir(environment)
    # Temporary files with these names are only ever this tool's interrupted writes.
    session = session_file(environment)
    for temp in (*(directory / (name + '.new') for name in (*PLUGINS, RECEIPT)),
                 session.with_name(SESSION + '.new')):
        if temp.is_symlink() or temp.is_file():
            temp.unlink()  # removes a link itself, never its target
    receipt = read_receipt(directory)
    for name, digest in (receipt or {'plugins': {}})['plugins'].items():
        path = directory / name
        require(not path.exists() or (path.is_file() and not path.is_symlink() and
                                      sha(path.read_bytes()) == digest),
                'Plugin changed since install; leaving it: ' + str(path))
    # A session file is ours only while it holds exactly what this tool writes.
    ours = session_state(environment, directory) == 'present'
    if ours:
        session.unlink()
    if receipt is None:
        return {'plugin_dir': str(directory), 'status': 'removed' if ours else 'nothing to remove'}
    for name in (*receipt['plugins'], RECEIPT):
        (directory / name).unlink(missing_ok=True)
    return {'plugin_dir': str(directory), 'status': 'removed'}


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--apply', action='store_true', help='build and install (default: inspect only)')
    action.add_argument('--remove', action='store_true', help='remove plugins this tool installed')
    args = parser.parse_args(arguments)
    try:
        report = remove(os.environ) if args.remove else install(os.environ, apply=args.apply)
    except (KeyError, OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print('error: ' + str(error), file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
