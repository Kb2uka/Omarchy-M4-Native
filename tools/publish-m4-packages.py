#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Sign the checked M4 packages and publish the [omarchy-m4] repository.

Runs only in the protected publishing job. The signing key arrives in
PACKAGE_SIGNING_KEY and lives in a temporary keyring. A package file that is
already published is never replaced: its published signature is verified and
the published bytes stay in the database. New packages and signatures are
uploaded first; the signed database is replaced last, and only when its
package set changed. The result is then downloaded anonymously and verified.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CHANNEL = json.loads((ROOT / 'packaging/omarchy-m4/channel.json').read_text())
TAG, REPO, FPR = CHANNEL['release_tag'], CHANNEL['repository'], CHANNEL['signing_fingerprint']
SETUP = 'omarchy-m4-setup.py'
NOTES = ('Signed pacman repository for the experimental M4 build ([issue #49]'
         '(https://github.com/Kb2uka/Omarchy-M4-Native/issues/49)). Owners connect once with '
         '`omarchy-m4-setup.py`; see docs/updates.md. Not an installer.')


def require(condition, message):
    if not condition:
        raise SystemExit('publish-m4-packages: ' + message)


def run(*command, **options):
    return subprocess.run([str(part) for part in command], check=True, **options)


def output(*command):
    return run(*command, capture_output=True, text=True).stdout


def verify(signature, data_file):
    """Require a good signature by the channel key, by fingerprint."""
    status = subprocess.run(['gpg', '--batch', '--status-fd', '1', '--verify', str(signature), str(data_file)],
                            capture_output=True, text=True)
    valid = [line.split()[2] for line in status.stdout.splitlines() if line.startswith('[GNUPG:] VALIDSIG ')]
    require(status.returncode == 0 and valid == [FPR], 'Bad or foreign signature on ' + data_file.name)


def database_entries(data):
    """Return {(filename, sha256)} listed in a repository database archive."""
    entries = set()
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for member in archive:
            if member.isfile() and member.name.endswith('/desc'):
                fields, key = {}, None
                for line in archive.extractfile(member).read().decode().splitlines():
                    if line.startswith('%') and line.endswith('%'):
                        key = line
                    elif line and key:
                        fields[key] = line
                entries.add((fields.get('%FILENAME%'), fields.get('%SHA256SUM%')))
    return entries


def fetch(name, attempts=4):
    """Download a published file anonymously, as an owner's pacman would."""
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(CHANNEL['server'] + '/' + name, timeout=300) as response:
                return response.read()
        except urllib.error.URLError:
            if attempt == attempts - 1:
                raise
            time.sleep(10 * (attempt + 1))


def publish(directory):
    key = os.environ.get('PACKAGE_SIGNING_KEY', '')
    require(key.strip(), 'PACKAGE_SIGNING_KEY is not available')
    with tempfile.TemporaryDirectory(prefix='m4-publish-') as scratch:
        scratch = Path(scratch)
        os.environ['GNUPGHOME'] = str(scratch / 'gnupg')
        (scratch / 'gnupg').mkdir(mode=0o700)
        run('gpg', '--batch', '--import', input=key, text=True)
        secret = [line.split(':')[9] for line in output('gpg', '--list-secret-keys', '--with-colons').splitlines()
                  if line.startswith('fpr:')]
        require(secret[:1] == [FPR], 'The signing key is not the channel key ' + FPR)
        if subprocess.run(['gh', 'release', 'view', TAG], capture_output=True).returncode != 0:
            run('gh', 'release', 'create', TAG, '--prerelease', '--target', 'main',
                '--title', 'M4 package repository', '--notes', NOTES)
        assets = set(output('gh', 'release', 'view', TAG, '--json', 'assets', '-q', '.assets[].name').split())
        repo, uploads = scratch / 'repo', []
        repo.mkdir()
        for package in sorted(directory.glob('*.pkg.tar.zst')):
            if package.name in assets:
                require(package.name + '.sig' in assets, 'Published package has no signature: ' + package.name)
                run('gh', 'release', 'download', TAG, '-D', repo, '-p', package.name, '-p', package.name + '.sig')
                verify(repo / (package.name + '.sig'), repo / package.name)
                print('kept published ' + package.name)
                continue
            shutil.copyfile(package, repo / package.name)
            run('gpg', '--batch', '--yes', '--local-user', FPR, '--detach-sign', '--no-armor', repo / package.name)
            verify(repo / (package.name + '.sig'), repo / package.name)
            uploads += [repo / package.name, repo / (package.name + '.sig')]
        packages = sorted(repo.glob('*.pkg.tar.zst'))
        run('repo-add', '--sign', '--key', FPR, repo / f'{REPO}.db.tar.gz', *packages)
        desired = database_entries((repo / f'{REPO}.db.tar.gz').read_bytes())
        require({name for name, _ in desired} == {p.name for p in packages}, 'Database does not list every package')
        if uploads:
            run('gh', 'release', 'upload', TAG, *uploads)
        setup = scratch / SETUP
        shutil.copyfile(ROOT / 'tools/m4-channel-setup.py', setup)
        sums = scratch / 'SETUP-SHA256SUMS'
        sums.write_text(hashlib.sha256(setup.read_bytes()).hexdigest() + '  ' + SETUP + '\n')
        run('gh', 'release', 'upload', TAG, '--clobber', setup, sums)
        published = database_entries(fetch(f'{REPO}.db')) if f'{REPO}.db' in assets else set()
        if published != desired:
            names = []
            for kind in ('files', 'db'):  # the database a client reads first goes last
                for suffix in ('', '.sig'):
                    target = repo / f'{REPO}.{kind}{suffix}'
                    target.unlink()
                    shutil.copyfile(repo / f'{REPO}.{kind}.tar.gz{suffix}', target)
                    names.append(target)
            for target in names:
                run('gh', 'release', 'upload', TAG, '--clobber', target)
        else:
            print('database unchanged')
        check = scratch / 'check'
        check.mkdir()
        for name in (f'{REPO}.db', f'{REPO}.db.sig'):
            (check / name).write_bytes(fetch(name))
        verify(check / f'{REPO}.db.sig', check / f'{REPO}.db')
        require(database_entries((check / f'{REPO}.db').read_bytes()) == desired,
                'The published database does not match')
        for name, digest in sorted(desired):
            data = fetch(name)
            require(hashlib.sha256(data).hexdigest() == digest, 'Published package differs: ' + name)
            (check / name).write_bytes(data)
            (check / (name + '.sig')).write_bytes(fetch(name + '.sig'))
            verify(check / (name + '.sig'), check / name)
            print('verified ' + name)
        require(fetch(SETUP) == setup.read_bytes(), 'Published setup script differs')


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('directory', type=Path, help='checked packages from build-m4-packages.py')
    publish(parser.parse_args().directory.resolve())


if __name__ == '__main__':
    main()
