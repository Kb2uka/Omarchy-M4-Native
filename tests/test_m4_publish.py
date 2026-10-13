# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Package publishing: never replace a package, repair interrupted runs, verify what is served."""
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import types
import unittest
from unittest import mock
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('publish_m4_packages', ROOT / 'tools/publish-m4-packages.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
REPO = MODULE.REPO


def signature(data):
    return b'sig:' + hashlib.sha256(data).hexdigest().encode()


class Release:
    """A GitHub release, the gpg signer and repo-add, simulated."""

    def __init__(self, fingerprint=MODULE.FPR):
        self.exists, self.assets, self.uploads, self.fingerprint = False, {}, [], fingerprint

    def run(self, *command, **options):
        command = [str(part) for part in command]
        if command[:3] == ['gpg', '--batch', '--import']:
            return types.SimpleNamespace(returncode=0, stdout='')
        if command[:3] == ['gh', 'release', 'create']:
            self.exists = True
        elif command[:3] == ['gh', 'release', 'download']:
            directory = Path(command[command.index('-D') + 1])
            for index, word in enumerate(command):
                if word == '-p':
                    (directory / command[index + 1]).write_bytes(self.assets[command[index + 1]])
        elif command[:3] == ['gh', 'release', 'upload']:
            clobber = '--clobber' in command
            for path in (Path(c) for c in command[3:] if c not in (MODULE.TAG, '--clobber')):
                if path.name in self.assets and not clobber:
                    raise subprocess.CalledProcessError(1, command)
                self.assets[path.name] = path.read_bytes()
                self.uploads.append(path.name)
        elif command[:2] == ['gpg', '--batch'] and '--detach-sign' in command:
            target = Path(command[-1])
            target.with_name(target.name + '.sig').write_bytes(signature(target.read_bytes()))
        elif command[0] == 'repo-add':
            self.repo_add(Path(command[4]), [Path(c) for c in command[5:]])
        else:
            raise AssertionError('unexpected command: ' + ' '.join(command))
        return types.SimpleNamespace(returncode=0, stdout='')

    def repo_add(self, database, packages):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w:gz') as archive:
            for package in packages:
                data = (f'%FILENAME%\n{package.name}\n\n%SHA256SUM%\n'
                        f'{hashlib.sha256(package.read_bytes()).hexdigest()}\n').encode()
                info = tarfile.TarInfo(package.name + '/desc')
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        for kind in ('db', 'files'):
            real = database.with_name(f'{REPO}.{kind}.tar.gz')
            real.write_bytes(stream.getvalue())
            real.with_name(real.name + '.sig').write_bytes(signature(real.read_bytes()))
            database.with_name(f'{REPO}.{kind}').symlink_to(real.name)
            database.with_name(f'{REPO}.{kind}.sig').symlink_to(real.name + '.sig')

    def output(self, *command):
        command = [str(part) for part in command]
        if command[:2] == ['gpg', '--list-secret-keys']:
            return f'sec:u:255:22:X:::::::::\nfpr:::::::::{self.fingerprint}:\n'
        if command[:3] == ['gh', 'release', 'view']:
            return '\n'.join(self.assets)
        raise AssertionError('unexpected output command: ' + ' '.join(command))

    def view(self, command, capture_output=False):
        return types.SimpleNamespace(returncode=0 if self.exists else 1)

    def fetch(self, name, attempts=4):
        if name not in self.assets:
            raise urllib.error.HTTPError(name, 404, 'Not Found', {}, None)
        return self.assets[name]


def signed(sig, data):
    return signed_bytes(sig.read_bytes(), data.read_bytes())


def signed_bytes(sig, data):
    return sig == signature(data)


class PublishTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.built = Path(temp.name) / 'built'
        self.built.mkdir()
        for name, data in (('omarchy-m4-boot-1-1-aarch64.pkg.tar.zst', b'boot 1'),
                           ('omarchy-m4-keyring-20261012-1-any.pkg.tar.zst', b'keyring')):
            (self.built / name).write_bytes(data)
        self.release = Release()

    def publish(self, release=None):
        release = release or self.release
        with mock.patch.object(MODULE, 'run', release.run), \
             mock.patch.object(MODULE, 'output', release.output), \
             mock.patch.object(MODULE, 'fetch', release.fetch), \
             mock.patch.object(MODULE, 'signed', signed), \
             mock.patch.object(MODULE.subprocess, 'run', release.view), \
             mock.patch.dict(os.environ, {'PACKAGE_SIGNING_KEY': 'private key'}), \
             mock.patch('builtins.print'):
            MODULE.publish(self.built)

    def test_first_publication(self):
        self.publish()
        self.assertTrue(self.release.exists)
        names = set(self.release.assets)
        for package in self.built.iterdir():
            self.assertIn(package.name, names)
            self.assertEqual(self.release.assets[package.name + '.sig'], signature(package.read_bytes()))
        self.assertEqual(self.release.uploads[-1], f'{REPO}.db.sig', 'the client-facing database goes last')
        self.assertIn(MODULE.SETUP, names)

    def test_connect_script_carries_the_published_setup_hash(self):
        self.publish()
        digest = hashlib.sha256(self.release.assets[MODULE.SETUP]).hexdigest()
        connect = self.release.assets[MODULE.CONNECT].decode()
        self.assertIn(f"expected='{digest}'", connect)
        self.assertNotIn(MODULE.CONNECT_HASH, connect)
        self.assertGreater(self.release.uploads.index(MODULE.CONNECT), self.release.uploads.index(MODULE.SETUP),
                           'the connect script goes up after the script it checks')

    def test_rerun_changes_nothing_but_the_setup_scripts(self):
        self.publish()
        self.release.uploads.clear()
        self.publish()
        self.assertEqual(sorted(self.release.uploads), sorted([MODULE.SETUP, 'SETUP-SHA256SUMS', MODULE.CONNECT]))

    def test_stale_served_database_is_retried(self):
        self.publish()
        (self.built / 'omarchy-m4-boot-1-1-aarch64.pkg.tar.zst').unlink()
        (self.built / 'omarchy-m4-boot-2-1-aarch64.pkg.tar.zst').write_bytes(b'boot 2')
        stale, real_fetch, served = self.release.assets[f'{REPO}.db'], self.release.fetch, []

        def lagging_fetch(name, attempts=4):
            uploaded = self.release.assets[f'{REPO}.db'] != stale
            if name == f'{REPO}.db' and uploaded and len(served) < 2:  # the CDN lags twice after upload
                served.append(name)
                return stale
            return real_fetch(name)
        self.release.fetch = lagging_fetch
        with mock.patch.object(MODULE.time, 'sleep') as sleep:
            self.publish()
        self.assertEqual(sleep.call_count, 2)

    def test_stale_served_connect_script_fails(self):
        self.publish()
        real_fetch = self.release.fetch
        self.release.fetch = (lambda name, attempts=4:
                              b'old connect script' if name == MODULE.CONNECT else real_fetch(name))
        with mock.patch.object(MODULE.time, 'sleep'), self.assertRaises(SystemExit) as raised:
            self.publish()
        self.assertIn('connect script differs', str(raised.exception))

    def test_corrupt_served_database_is_retried(self):
        import gzip
        good = gzip.compress(b'x' * 4096)
        for label, corrupt in (('not an archive', b'truncated'),
                               ('corrupt deflate body', good[:10] + bytes(len(good) - 10)),
                               ('truncated gzip', good[:len(good) // 2])):
            with self.subTest(label):
                self.setUp()
                self.corrupt_database_once(corrupt)

    def test_local_errors_are_not_retried(self):
        self.publish()
        def failing_signs(signature_path, data_path):
            def accept(data):
                raise OSError('scratch disk full')
            return accept
        with mock.patch.object(MODULE, 'signs', failing_signs), \
             mock.patch.object(MODULE.time, 'sleep') as sleep, self.assertRaises(OSError):
            self.publish()
        self.assertEqual(sleep.call_count, 0)

    def corrupt_database_once(self, corrupt):
        self.publish()
        (self.built / 'omarchy-m4-boot-1-1-aarch64.pkg.tar.zst').unlink()
        (self.built / 'omarchy-m4-boot-2-1-aarch64.pkg.tar.zst').write_bytes(b'boot 2')
        stale, real_fetch, served = self.release.assets[f'{REPO}.db'], self.release.fetch, []

        def corrupt_once(name, attempts=4):
            if name == f'{REPO}.db' and self.release.assets[name] != stale and not served:
                served.append(name)
                return corrupt
            return real_fetch(name)
        self.release.fetch = corrupt_once
        with mock.patch.object(MODULE.time, 'sleep') as sleep:
            self.publish()
        self.assertEqual(sleep.call_count, 1)

    def test_database_that_never_matches_fails(self):
        self.publish()
        stale = self.release.assets[f'{REPO}.db']
        (self.built / 'omarchy-m4-boot-1-1-aarch64.pkg.tar.zst').unlink()
        (self.built / 'omarchy-m4-boot-2-1-aarch64.pkg.tar.zst').write_bytes(b'boot 2')
        real_fetch = self.release.fetch
        self.release.fetch = lambda name, attempts=4: stale if name == f'{REPO}.db' else real_fetch(name)
        with mock.patch.object(MODULE.time, 'sleep'), self.assertRaises(SystemExit):
            self.publish()

    def test_missing_package_signature_is_repaired(self):
        self.publish()
        del self.release.assets['omarchy-m4-boot-1-1-aarch64.pkg.tar.zst.sig']
        self.publish()
        self.assertEqual(self.release.assets['omarchy-m4-boot-1-1-aarch64.pkg.tar.zst.sig'], signature(b'boot 1'))

    def test_missing_database_signature_is_repaired(self):
        self.publish()
        del self.release.assets[f'{REPO}.db.sig']
        self.release.uploads.clear()
        self.publish()
        self.assertIn(f'{REPO}.db.sig', self.release.assets)
        self.assertIn(f'{REPO}.db', self.release.uploads)

    def test_corrupt_database_signatures_are_repaired(self):
        for kind in ('db', 'files'):
            with self.subTest(kind):
                self.setUp()
                self.publish()
                self.release.assets[f'{REPO}.{kind}.sig'] = b'corrupt'
                self.release.uploads.clear()
                self.publish()
                self.assertIn(f'{REPO}.{kind}.sig', self.release.uploads)
                self.assertTrue(signed_bytes(self.release.assets[f'{REPO}.{kind}.sig'],
                                             self.release.assets[f'{REPO}.{kind}']))

    def test_published_package_is_never_replaced(self):
        self.publish()
        (self.built / 'omarchy-m4-boot-1-1-aarch64.pkg.tar.zst').write_bytes(b'rebuilt bytes')
        self.publish()
        self.assertEqual(self.release.assets['omarchy-m4-boot-1-1-aarch64.pkg.tar.zst'], b'boot 1')

    def test_stray_signature_does_not_block_a_new_package(self):
        self.release.exists = True
        self.release.assets['omarchy-m4-boot-1-1-aarch64.pkg.tar.zst.sig'] = b'stale'
        self.publish()
        self.assertEqual(self.release.assets['omarchy-m4-boot-1-1-aarch64.pkg.tar.zst.sig'], signature(b'boot 1'))

    def test_new_version_is_added(self):
        self.publish()
        (self.built / 'omarchy-m4-boot-1-1-aarch64.pkg.tar.zst').unlink()
        (self.built / 'omarchy-m4-boot-2-1-aarch64.pkg.tar.zst').write_bytes(b'boot 2')
        self.publish()
        listed = MODULE.database_entries(self.release.assets[f'{REPO}.db'])
        self.assertEqual({name for name, _ in listed},
                         {'omarchy-m4-boot-2-1-aarch64.pkg.tar.zst', 'omarchy-m4-keyring-20261012-1-any.pkg.tar.zst'})
        self.assertIn('omarchy-m4-boot-1-1-aarch64.pkg.tar.zst', self.release.assets, 'old versions stay downloadable')

    def test_wrong_key_publishes_nothing(self):
        release = Release(fingerprint='0' * 40)
        with self.assertRaises(SystemExit):
            self.publish(release)
        self.assertEqual(release.assets, {})


if __name__ == '__main__':
    unittest.main()
