#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""RealHost file, hash, rewind, flock, sealed-copy, and non-regular rejection tests.

Uses temporary files only. No network and no sockets. A FIFO is not opened:
RealHost uses a blocking O_RDONLY, which would stall the test.
"""
from __future__ import annotations

import hashlib
import os
import stat
import tempfile
import unittest
from pathlib import Path

import fcntl

import run_probe as rp


def fd_count() -> int:
    return len(os.listdir("/proc/self/fd"))


def required_seals() -> int:
    return (
        fcntl.F_SEAL_WRITE
        | fcntl.F_SEAL_GROW
        | fcntl.F_SEAL_SHRINK
        | fcntl.F_SEAL_SEAL
    )


class RealIO(unittest.TestCase):
    def test_known_hash_chunks(self):
        digest = hashlib.sha256(b"abc").hexdigest()
        self.assertEqual(
            rp.hash_opened_bytes(lambda: iter([b"a", b"", b"bc"])),
            digest,
        )
        self.assertEqual(
            digest,
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        )

    def test_real_fd_hash_and_rewind(self):
        host = rp.RealHost()
        with tempfile.TemporaryDirectory() as tmp:
            data = b"A" * (1024 * 1024) + b"last-chunk"
            path = Path(tmp) / "module.ko"
            path.write_bytes(data)
            digest = hashlib.sha256(data).hexdigest()
            fd = host.open_module(str(path))
            try:
                os.lseek(fd, 17, os.SEEK_SET)
                self.assertEqual(host.hash_fd(fd), digest)
                self.assertEqual(os.lseek(fd, 0, os.SEEK_CUR), 0)
                self.assertEqual(host.hash_fd(fd), digest)
                self.assertEqual(os.read(fd, len(data)), data)
            finally:
                host.close_fd(fd)

    def test_empty_file_hash_rewinds(self):
        host = rp.RealHost()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.ko"
            path.write_bytes(b"")
            fd = host.open_module(str(path))
            try:
                os.lseek(fd, 0, os.SEEK_END)
                self.assertEqual(
                    host.hash_fd(fd),
                    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                )
                self.assertEqual(os.lseek(fd, 0, os.SEEK_CUR), 0)
            finally:
                host.close_fd(fd)

    def test_symlink_to_regular_file_hashes_target(self):
        host = rp.RealHost()
        with tempfile.TemporaryDirectory() as tmp:
            data = b"xyz-module"
            real = Path(tmp) / "real.ko"
            real.write_bytes(data)
            link = Path(tmp) / "link.ko"
            link.symlink_to(real)
            fd = host.open_module(str(link))
            try:
                self.assertEqual(host.hash_fd(fd), hashlib.sha256(data).hexdigest())
                self.assertEqual(os.read(fd, 3), b"xyz")
            finally:
                host.close_fd(fd)

    def _assert_rejected(self, path: str) -> None:
        host = rp.RealHost()
        before = fd_count()
        with self.assertRaisesRegex(rp.ProbeError, "not a regular file"):
            host.open_module(path)
        self.assertEqual(fd_count(), before)

    def test_reject_directory_without_fd_leak(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._assert_rejected(tmp)

    def test_reject_char_device_and_symlink_without_fd_leak(self):
        self._assert_rejected("/dev/null")
        info = os.stat("/dev/null")
        self.assertTrue(stat.S_ISCHR(info.st_mode))
        with tempfile.TemporaryDirectory() as tmp:
            link = os.path.join(tmp, "not-a-module")
            os.symlink("/dev/null", link)
            self._assert_rejected(link)
            dir_link = os.path.join(tmp, "dir-link")
            os.symlink(tmp, dir_link)
            self._assert_rejected(dir_link)

    def test_real_flock_is_exclusive_and_released(self):
        first = rp.RealHost()
        second = rp.RealHost()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "j713-isp-probe.lock")
            before = fd_count()
            with first.acquire_lock(path):
                self.assertTrue(os.path.isfile(path))
                self.assertEqual(fd_count(), before + 1)
                with self.assertRaisesRegex(rp.ProbeError, "probe lock busy"):
                    with second.acquire_lock(path):
                        pass
                self.assertEqual(fd_count(), before + 1)
            self.assertEqual(fd_count(), before)
            with second.acquire_lock(path):
                pass
            self.assertEqual(fd_count(), before)

    def test_missing_module_does_not_leave_an_fd(self):
        host = rp.RealHost()
        before = fd_count()
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "missing.ko")
            with self.assertRaises(FileNotFoundError):
                host.open_module(missing)
        self.assertEqual(fd_count(), before)

    def test_hash_then_inplace_write_keeps_sealed_bytes(self):
        host = rp.RealHost()
        original = b"verified-module-bytes"
        changed = b"X" * len(original)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "module.ko"
            path.write_bytes(original)
            before = fd_count()
            fd = host.open_module(str(path))
            try:
                self.assertEqual(
                    fcntl.fcntl(fd, fcntl.F_GET_SEALS) & required_seals(),
                    required_seals(),
                )
                self.assertEqual(
                    fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE,
                    os.O_RDONLY,
                )
                self.assertEqual(host.hash_fd(fd), hashlib.sha256(original).hexdigest())
                self.assertEqual(os.lseek(fd, 0, os.SEEK_CUR), 0)
                path.write_bytes(changed)
                self.assertEqual(path.read_bytes(), changed)
                self.assertEqual(os.read(fd, len(original)), original)
            finally:
                host.close_fd(fd)
            self.assertEqual(fd_count(), before)

    def test_path_replacement_keeps_sealed_bytes(self):
        host = rp.RealHost()
        original = b"verified-module-bytes"
        replaced = b"Y" * len(original)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "module.ko"
            path.write_bytes(original)
            other = Path(tmp) / "other.ko"
            other.write_bytes(replaced)
            before = fd_count()
            fd = host.open_module(str(path))
            try:
                digest = host.hash_fd(fd)
                os.replace(other, path)
                self.assertEqual(path.read_bytes(), replaced)
                self.assertEqual(host.hash_fd(fd), digest)
                self.assertEqual(os.read(fd, len(original)), original)
            finally:
                host.close_fd(fd)
            self.assertEqual(fd_count(), before)

    def test_returned_fd_is_sealed_readonly_copy(self):
        host = rp.RealHost()
        original = b"verified-module-bytes"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "module.ko"
            path.write_bytes(original)
            before = fd_count()
            fd = host.open_module(str(path))
            try:
                live = os.stat(path)
                frozen = os.fstat(fd)
                self.assertNotEqual(
                    (frozen.st_dev, frozen.st_ino),
                    (live.st_dev, live.st_ino),
                )
                self.assertEqual(
                    fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE,
                    os.O_RDONLY,
                )
                alias = os.open(
                    "/proc/self/fd/%d" % fd, os.O_RDWR | os.O_CLOEXEC
                )
                try:
                    self.assertEqual(
                        fcntl.fcntl(alias, fcntl.F_GETFL) & os.O_ACCMODE,
                        os.O_RDWR,
                    )
                    with self.assertRaises(OSError):
                        os.write(alias, b"Z")
                finally:
                    os.close(alias)
                self.assertEqual(
                    host.hash_fd(fd), hashlib.sha256(original).hexdigest()
                )
            finally:
                host.close_fd(fd)
            self.assertEqual(fd_count(), before)

    def test_partial_copy_matches_source_bytes(self):
        host = rp.RealHost()
        original = b"P" * 4096 + b"tail"
        real_write = rp.os.write
        calls = {"n": 0}

        def short(fd, data):
            calls["n"] += 1
            if calls["n"] > (len(original) * 2):
                raise OSError(5, "partial write did not advance")
            view = data if isinstance(data, memoryview) else memoryview(data)
            n = 1 if len(view) > 1 else len(view)
            return real_write(fd, view[:n])

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "module.ko"
            path.write_bytes(original)
            before = fd_count()
            rp.os.write = short
            try:
                fd = host.open_module(str(path))
            finally:
                rp.os.write = real_write
            try:
                self.assertGreater(calls["n"], 1)
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(
                    host.hash_fd(fd), hashlib.sha256(original).hexdigest()
                )
                self.assertEqual(os.read(fd, len(original)), original)
                self.assertEqual(
                    fcntl.fcntl(fd, fcntl.F_GET_SEALS) & required_seals(),
                    required_seals(),
                )
            finally:
                host.close_fd(fd)
            self.assertEqual(fd_count(), before)

    def test_open_module_failure_injections_do_not_leak_fds(self):
        host = rp.RealHost()
        original = b"verified-module-bytes"
        for stage in ("copy", "seal", "reopen", "fstat"):
            with self.subTest(stage=stage):
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / "module.ko"
                    path.write_bytes(original)
                    before = fd_count()
                    restore = self._install_open_failure(stage)
                    try:
                        with self.assertRaises(OSError):
                            host.open_module(str(path))
                    finally:
                        restore()
                    self.assertEqual(fd_count(), before)
                    self.assertEqual(path.read_bytes(), original)

    def _install_open_failure(self, stage: str):
        if stage == "copy":
            real = rp.os.write

            def boom(fd, data):
                raise OSError(5, "copy failed")

            rp.os.write = boom

            def restore():
                rp.os.write = real

            return restore
        if stage == "seal":
            real = rp.fcntl.fcntl

            def boom(fd, op, arg=0):
                if op == fcntl.F_ADD_SEALS:
                    raise OSError(5, "seal failed")
                return real(fd, op, arg)

            rp.fcntl.fcntl = boom

            def restore():
                rp.fcntl.fcntl = real

            return restore
        if stage == "reopen":
            real = rp.os.open

            def boom(path, flags, *args, **kwargs):
                if isinstance(path, str) and path.startswith("/proc/self/fd/"):
                    raise OSError(5, "reopen failed")
                return real(path, flags, *args, **kwargs)

            rp.os.open = boom

            def restore():
                rp.os.open = real

            return restore
        if stage == "fstat":
            real = rp.os.fstat
            state = {"n": 0}

            def boom(fd):
                state["n"] += 1
                if state["n"] >= 2:
                    raise OSError(5, "fstat failed")
                return real(fd)

            rp.os.fstat = boom

            def restore():
                rp.os.fstat = real

            return restore
        raise AssertionError(stage)


if __name__ == "__main__":
    unittest.main()
