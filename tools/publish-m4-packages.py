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
import zlib

ROOT = Path(__file__).resolve().parents[1]
CHANNEL = json.loads((ROOT / 'packaging/omarchy-m4/channel.json').read_text())
TAG, REPO, FPR = CHANNEL['release_tag'], CHANNEL['repository'], CHANNEL['signing_fingerprint']
SETUP = 'omarchy-m4-setup.py'
CONNECT = 'm4-connect.sh'
CONNECT_HASH = '@SETUP_SHA256@'
NOTES = ('Signed pacman repository for the experimental M4 build ([issue #49]'
         '(https://github.com/Kb2uka/Omarchy-M4-Native/issues/49)). Owners connect once with '
         '`m4-connect.sh` (it checks and runs `omarchy-m4-setup.py`); see docs/updates.md. Not an installer.')


def require(condition, message):
    if not condition:
        raise SystemExit('publish-m4-packages: ' + message)


def run(*command, **options):
    return subprocess.run([str(part) for part in command], check=True, **options)


def output(*command):
    return run(*command, capture_output=True, text=True).stdout


def signed(signature, data_file):
    """Return whether data_file has a good signature by the channel key, by fingerprint."""
    status = subprocess.run(['gpg', '--batch', '--status-fd', '1', '--verify', str(signature), str(data_file)],
                            capture_output=True, text=True)
    valid = [line.split()[2] for line in status.stdout.splitlines() if line.startswith('[GNUPG:] VALIDSIG ')]
    return status.returncode == 0 and valid == [FPR]


def verify(signature, data_file):
    require(signed(signature, data_file), 'Bad or foreign signature on ' + data_file.name)


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


def database_published(assets, desired, directory):
    """Return whether both signed databases are published, intact and list exactly desired."""
    names = [f'{REPO}.{kind}{suffix}' for kind in ('db', 'files') for suffix in ('', '.sig')]
    if not set(names) <= assets:
        return False
    try:
        for name in names:
            (directory / name).write_bytes(fetch(name))
    except (OSError, urllib.error.URLError):
        return False
    return all(signed(directory / f'{REPO}.{kind}.sig', directory / f'{REPO}.{kind}') and
               database_entries((directory / f'{REPO}.{kind}').read_bytes()) == desired
               for kind in ('db', 'files'))


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


def connect_script(setup_sha256):
    """The one-paste connect script, carrying the published setup script's SHA256."""
    template = (ROOT / 'tools' / CONNECT).read_text()
    require(template.count(CONNECT_HASH) == 1, 'Connect script template needs exactly one hash slot')
    return template.replace(CONNECT_HASH, setup_sha256)


def served(name, accept, message, attempts=6):
    """Download a release asset until accept(data) holds, and return it.

    The release CDN can serve an object's previous bytes for a few seconds after
    an upload, so a mismatch is retried before it counts as a failure.
    """
    for attempt in range(attempts):
        data = fetch(name)
        if accept(data):
            return data
        if attempt + 1 < attempts:
            time.sleep(10)
    require(False, message)


def lists(desired):
    """An accept() for served(): a database listing exactly these packages.

    Truncated or corrupt bytes count as not served yet, so they are retried.
    """
    def accept(data):
        try:  # parses in memory only, so an OSError here means bad bytes (BadGzipFile)
            return database_entries(data) == desired
        except (tarfile.TarError, zlib.error, EOFError, OSError):
            return False
    return accept


def signs(signature_path, data_path):
    """An accept() for served(): the bytes are a good signature of data_path."""
    def accept(data):
        signature_path.write_bytes(data)
        return signed(signature_path, data_path)
    return accept


def sign(package):
    run('gpg', '--batch', '--yes', '--local-user', FPR, '--detach-sign', '--no-armor', package)
    verify(package.with_name(package.name + '.sig'), package)


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
            name, signature = package.name, repo / (package.name + '.sig')
            if name in assets:
                # The policy check already proved the published contents equal this build.
                run('gh', 'release', 'download', TAG, '-D', repo, '-p', name)
                if name + '.sig' in assets:
                    run('gh', 'release', 'download', TAG, '-D', repo, '-p', name + '.sig')
                    verify(signature, repo / name)
                    print('kept published ' + name)
                    continue
                # An earlier run stopped between the package and its signature.
                sign(repo / name)
                run('gh', 'release', 'upload', TAG, '--clobber', signature)
                print('signed published ' + name)
                continue
            shutil.copyfile(package, repo / name)
            sign(repo / name)
            uploads.append((repo / name, signature))
        packages = sorted(repo.glob('*.pkg.tar.zst'))
        run('repo-add', '--sign', '--key', FPR, repo / f'{REPO}.db.tar.gz', *packages)
        desired = database_entries((repo / f'{REPO}.db.tar.gz').read_bytes())
        require({name for name, _ in desired} == {p.name for p in packages}, 'Database does not list every package')
        for package, signature in uploads:
            run('gh', 'release', 'upload', TAG, package)  # never replaces a published package
            run('gh', 'release', 'upload', TAG, '--clobber', signature)
        setup = scratch / SETUP
        shutil.copyfile(ROOT / 'tools/m4-channel-setup.py', setup)
        sums = scratch / 'SETUP-SHA256SUMS'
        digest = hashlib.sha256(setup.read_bytes()).hexdigest()
        sums.write_text(digest + '  ' + SETUP + '\n')
        run('gh', 'release', 'upload', TAG, '--clobber', setup, sums)
        connect = scratch / CONNECT
        connect.write_text(connect_script(digest))
        run('gh', 'release', 'upload', TAG, '--clobber', connect)  # after the script it checks
        current = scratch / 'current'
        current.mkdir()
        if not database_published(assets, desired, current):
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
            print('database already published and verified')
        check = scratch / 'check'
        check.mkdir()
        database = check / f'{REPO}.db'
        database.write_bytes(served(database.name, lists(desired), 'The published database does not match'))
        served(database.name + '.sig', signs(check / (database.name + '.sig'), database),
               'Bad or foreign signature on ' + database.name)
        for name, digest in sorted(desired):
            (check / name).write_bytes(served(name, lambda d, digest=digest: hashlib.sha256(d).hexdigest() == digest,
                                              'Published package differs: ' + name))
            served(name + '.sig', signs(check / (name + '.sig'), check / name), 'Bad or foreign signature on ' + name)
            print('verified ' + name)
        served(SETUP, lambda d: d == setup.read_bytes(), 'Published setup script differs')
        served(CONNECT, lambda d: d == connect.read_bytes(), 'Published connect script differs')


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('directory', type=Path, help='checked packages from build-m4-packages.py')
    publish(parser.parse_args().directory.resolve())


if __name__ == '__main__':
    main()
