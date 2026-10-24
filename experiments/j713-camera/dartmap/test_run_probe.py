#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Offline unit tests for run_probe.py. Orchestrator runs these remotely."""
from __future__ import annotations

import argparse
import hashlib
import io
import os
import struct
import sys
import unittest
from contextlib import contextmanager
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_probe as rp  # noqa: E402


ADT_PHYS = rp.ADT_PHYS
ADT_SIZE = rp.ADT_SIZE
MODULE_BYTES = b"fake-j713-isp-dartmap-module\n"
MODULE_SHA = hashlib.sha256(MODULE_BYTES).hexdigest()


def elf64_le(phdrs, ident_extra=None, phentsize=56, phoff=None, phnum=None):
    ident = bytearray(b"\x7fELF\x02\x01\x01" + b"\x00" * 9)
    if ident_extra:
        ident[: len(ident_extra)] = ident_extra
    if phoff is None:
        phoff = 64
    if phnum is None:
        phnum = len(phdrs)
    hdr = bytes(ident) + struct.pack(
        "<HHIQQQIHHHHHH",
        4,
        183,
        1,
        0,
        phoff,
        0,
        0,
        64,
        phentsize,
        phnum,
        0,
        0,
        0,
    )
    table = b"".join(struct.pack("<IIQQQQQQ", *ph) for ph in phdrs)
    return hdr, table


def pt_load(p_offset, p_paddr, filesz, memsz=None):
    if memsz is None:
        memsz = filesz
    return (rp.PT_LOAD, 4, p_offset, p_paddr, p_paddr, filesz, memsz, 4096)


def assemble_kcore(phdrs, payload_off, payload, **elf_kw):
    hdr, table = elf64_le(phdrs, **elf_kw)
    phoff = elf_kw.get("phoff", 64)
    blob = bytearray(max(len(hdr), phoff + len(table), payload_off + len(payload)))
    blob[0:len(hdr)] = hdr
    blob[phoff:phoff + len(table)] = table
    blob[payload_off:payload_off + len(payload)] = payload
    return bytes(blob)


class FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeHost:
    def __init__(self, **kwargs):
        self.euid = kwargs.get("euid", 0)
        self.release = kwargs.get("release", rp.EXPECTED_RELEASE)
        self.files = dict(kwargs.get("files", {}))
        self.boot_id_second = kwargs.get("boot_id_second")
        self.boot_reads = 0
        self.lock_busy = kwargs.get("lock_busy", False)
        self.lock_held = False
        self.subprocess_calls = []
        self.insmod_result = kwargs.get("insmod_result", FakeProc())
        self.opened = []
        self.closed = []
        self.writes = []
        self.next_fileno = kwargs.get("fileno", 7)
        self.fail_write = kwargs.get("fail_write")
        self.saw_lock_while_reading = False

    def geteuid(self):
        return self.euid

    def uname_release(self):
        return self.release

    def _note_lock(self):
        if self.lock_held:
            self.saw_lock_while_reading = True

    def read_bytes(self, path):
        self._note_lock()
        if path not in self.files:
            raise FileNotFoundError(path)
        data = self.files[path]
        if isinstance(data, str):
            return data.encode("ascii")
        return data

    def read_text(self, path):
        self._note_lock()
        if path == rp.BOOT_ID_PATH:
            self.boot_reads += 1
            if self.boot_reads > 1 and self.boot_id_second is not None:
                return self.boot_id_second
        data = self.read_bytes(path)
        return data.decode("ascii", "replace")

    def pread(self, path, size, offset):
        self._note_lock()
        data = self.read_bytes(path)
        # A short ADT is a shorter payload in the file image. Do not clip
        # the ELF header here; that would hide a truncated program header.
        return data[offset:offset + size]

    @contextmanager
    def acquire_lock(self, path):
        if self.lock_busy:
            raise rp.ProbeError("probe lock busy")
        self.lock_held = True
        try:
            yield
        finally:
            self.lock_held = False

    def open_module(self, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        fd = self.next_fileno
        self.opened.append((path, fd))
        return fd

    def hash_fd(self, fd):
        for path, opened_fd in self.opened:
            if opened_fd == fd:
                return hashlib.sha256(self.read_bytes(path)).hexdigest()
        raise rp.ProbeError("unknown fd")

    def close_fd(self, fd):
        self.closed.append(fd)

    def run_insmod(self, argv, pass_fds):
        self.subprocess_calls.append((list(argv), tuple(pass_fds)))
        return self.insmod_result

    def write_text(self, path, text):
        if self.fail_write == "os":
            raise OSError("sysfs write")
        if self.fail_write == "short":
            raise rp.ProbeError("short write")
        self.writes.append((path, text))
        self.files[path] = text


def truncate_payload(blob, payload_off, keep):
    """Keep the ELF header and program headers; shorten only the payload."""
    if payload_off < rp.ELF64_EHDR_SIZE:
        raise AssertionError("truncation would cut the ELF header")
    end = payload_off + keep
    if keep < 0 or end > len(blob):
        raise AssertionError("payload truncation out of range")
    return bytes(blob[:end])


def good_payload():
    return b"\x11" * ADT_SIZE


def good_cfg(payload):
    return rp.ProbeConfig(adt_sha256=hashlib.sha256(payload).hexdigest())


def good_files(payload, extra=None):
    p_offset = 4096
    kcore = assemble_kcore(
        [pt_load(p_offset, ADT_PHYS, ADT_SIZE)],
        p_offset,
        payload,
    )
    files = {
        rp.BOOT_ID_PATH: rp.EXPECTED_BOOT_UUID + "\n",
        rp.COMPAT_PATH: b"apple,mac16,12\0apple,j713\0apple,t8132\0",
        rp.FLASHREG_PATH: struct.pack(">QQ", ADT_PHYS, ADT_SIZE),
        rp.KCORE_PATH: kcore,
        rp.MODULES_PATH: "cfg80211 123 0 - Live 0x0\n",
        "/tmp/j713_isp_dartmap.ko": MODULE_BYTES,
    }
    if extra:
        files.update(extra)
    return files


def check_args():
    return argparse.Namespace(
        check_only=True,
        validate=False,
        map_check=False,
        cleanup_only=False,
        module=None,
        sha256=None,
    )


def load_args(map_check=False):
    return argparse.Namespace(
        check_only=False,
        validate=not map_check,
        map_check=bool(map_check),
        cleanup_only=False,
        module="/tmp/j713_isp_dartmap.ko",
        sha256=MODULE_SHA,
    )


class TestElfMapping(unittest.TestCase):
    def test_header_ok(self):
        hdr, table = elf64_le([pt_load(4096, ADT_PHYS, ADT_SIZE)])
        phoff, phentsize, phnum = rp.parse_elf64_le_header(hdr)
        self.assertEqual((phoff, phentsize, phnum), (64, 56, 1))
        phdrs = rp.unpack_phdrs(table, phnum)
        ph = rp.find_unique_pt_load(phdrs, ADT_PHYS, ADT_SIZE)
        self.assertEqual(rp.mapped_file_offset(ph, ADT_PHYS, ADT_SIZE), 4096)

    def test_malformed_magic(self):
        hdr, _table = elf64_le(
            [pt_load(4096, ADT_PHYS, ADT_SIZE)], ident_extra=b"NOPE"
        )
        with self.assertRaisesRegex(rp.ProbeError, "not ELF"):
            rp.parse_elf64_le_header(hdr)

    def test_malformed_class_and_endian(self):
        hdr32, _ = elf64_le(
            [pt_load(4096, ADT_PHYS, ADT_SIZE)],
            ident_extra=b"\x7fELF\x01\x01\x01",
        )
        with self.assertRaisesRegex(rp.ProbeError, "not ELF64"):
            rp.parse_elf64_le_header(hdr32)
        hdrbe, _ = elf64_le(
            [pt_load(4096, ADT_PHYS, ADT_SIZE)],
            ident_extra=b"\x7fELF\x02\x02\x01",
        )
        with self.assertRaisesRegex(rp.ProbeError, "not little-endian"):
            rp.parse_elf64_le_header(hdrbe)

    def test_malformed_phentsize(self):
        hdr, _ = elf64_le([pt_load(4096, ADT_PHYS, ADT_SIZE)], phentsize=48)
        with self.assertRaisesRegex(rp.ProbeError, "phentsize"):
            rp.parse_elf64_le_header(hdr)

    def test_malformed_short_header(self):
        with self.assertRaisesRegex(rp.ProbeError, "ELF header short"):
            rp.parse_elf64_le_header(b"\x7fELF" + b"\x00" * 10)

    def test_malformed_phnum_and_phoff(self):
        hdr, _ = elf64_le([], phnum=0)
        with self.assertRaisesRegex(rp.ProbeError, "phnum"):
            rp.parse_elf64_le_header(hdr)
        hdr, _ = elf64_le([pt_load(4096, ADT_PHYS, ADT_SIZE)], phoff=32)
        with self.assertRaisesRegex(rp.ProbeError, "phoff"):
            rp.parse_elf64_le_header(hdr)
        hdr, _ = elf64_le(
            [pt_load(4096, ADT_PHYS, ADT_SIZE)], phoff=rp.MAX_PHOFF + 1
        )
        with self.assertRaisesRegex(rp.ProbeError, "phoff"):
            rp.parse_elf64_le_header(hdr)
        hdr, _ = elf64_le([pt_load(4096, ADT_PHYS, ADT_SIZE)], phnum=rp.MAX_PHNUM + 1)
        with self.assertRaisesRegex(rp.ProbeError, "phnum"):
            rp.parse_elf64_le_header(hdr)

    def test_ambiguous_mapping(self):
        phdrs = [
            pt_load(4096, ADT_PHYS, ADT_SIZE),
            pt_load(8192, ADT_PHYS - 0x1000, ADT_SIZE + 0x1000),
        ]
        with self.assertRaisesRegex(rp.ProbeError, "ambiguous"):
            rp.find_unique_pt_load(phdrs, ADT_PHYS, ADT_SIZE)

    def test_no_mapping(self):
        phdrs = [pt_load(4096, ADT_PHYS + ADT_SIZE, ADT_SIZE)]
        with self.assertRaisesRegex(rp.ProbeError, "no PT_LOAD"):
            rp.find_unique_pt_load(phdrs, ADT_PHYS, ADT_SIZE)

    def test_ignores_non_load(self):
        note = (4, 0, 0, 0, 0, 32, 32, 8)
        phdrs = [note, pt_load(4096, ADT_PHYS, ADT_SIZE)]
        ph = rp.find_unique_pt_load(phdrs, ADT_PHYS, ADT_SIZE)
        self.assertEqual(ph[0], rp.PT_LOAD)

    def test_filesz_too_small(self):
        ph = pt_load(4096, ADT_PHYS, ADT_SIZE - 1, memsz=ADT_SIZE)
        with self.assertRaisesRegex(rp.ProbeError, "filesz"):
            rp.mapped_file_offset(ph, ADT_PHYS, ADT_SIZE)

    def test_short_read(self):
        payload = good_payload()
        full = assemble_kcore(
            [pt_load(4096, ADT_PHYS, ADT_SIZE)], 4096, payload
        )
        kcore = truncate_payload(full, 4096, 16)
        self.assertEqual(kcore[:rp.ELF64_EHDR_SIZE], full[:rp.ELF64_EHDR_SIZE])
        self.assertEqual(kcore[64:64 + 56], full[64:64 + 56])
        self.assertTrue(kcore.startswith(b"\x7fELF"))
        phoff, phentsize, phnum = rp.parse_elf64_le_header(kcore[:64])
        self.assertEqual((phoff, phentsize, phnum), (64, 56, 1))
        host = FakeHost(files={rp.KCORE_PATH: kcore})
        with self.assertRaisesRegex(rp.ProbeError, "ADT short read"):
            rp.read_kcore_adt(host.pread, rp.KCORE_PATH, ADT_PHYS, ADT_SIZE)

    def test_phdr_table_short(self):
        hdr, table = elf64_le([pt_load(4096, ADT_PHYS, ADT_SIZE)])
        blob = hdr + table[:-8]
        host = FakeHost(files={rp.KCORE_PATH: bytes(blob)})
        with self.assertRaisesRegex(rp.ProbeError, "phdr table short"):
            rp.read_kcore_adt(host.pread, rp.KCORE_PATH, ADT_PHYS, ADT_SIZE)

    def test_unique_read(self):
        payload = good_payload()
        kcore = assemble_kcore(
            [pt_load(4096, ADT_PHYS, ADT_SIZE)], 4096, payload
        )
        host = FakeHost(files={rp.KCORE_PATH: kcore})
        data = rp.read_kcore_adt(host.pread, rp.KCORE_PATH, ADT_PHYS, ADT_SIZE)
        self.assertEqual(data, payload)
        self.assertEqual(len(data), ADT_SIZE)


class TestGuards(unittest.TestCase):
    def test_wrong_euid(self):
        with self.assertRaisesRegex(rp.ProbeError, "euid"):
            rp.check_euid(1000)

    def test_wrong_kernel(self):
        with self.assertRaisesRegex(rp.ProbeError, "kernel release"):
            rp.check_release("6.1.0", rp.EXPECTED_RELEASE)

    def test_wrong_boot(self):
        with self.assertRaisesRegex(rp.ProbeError, "boot UUID"):
            rp.check_boot_id("00000000-0000-0000-0000-000000000000\n",
                             rp.EXPECTED_BOOT_UUID)

    def test_wrong_compat_and_flashreg(self):
        with self.assertRaisesRegex(rp.ProbeError, "compatible"):
            rp.check_compatible(b"apple,j314\0apple,t6000\0", rp.EXPECTED_COMPAT)
        with self.assertRaisesRegex(rp.ProbeError, "flashreg mismatch"):
            rp.check_flashreg(
                struct.pack(">QQ", ADT_PHYS, 0x1000), ADT_PHYS, ADT_SIZE
            )
        with self.assertRaisesRegex(rp.ProbeError, "flashreg size"):
            rp.check_flashreg(b"\x00" * 8, ADT_PHYS, ADT_SIZE)
        with self.assertRaisesRegex(rp.ProbeError, "flashreg mismatch"):
            rp.check_flashreg(
                struct.pack("<QQ", ADT_PHYS, ADT_SIZE), ADT_PHYS, ADT_SIZE
            )

    def test_wrong_adt_hash(self):
        with self.assertRaisesRegex(rp.ProbeError, "ADT sha256"):
            rp.check_adt_hash(b"nope", rp.EXPECTED_ADT_SHA256)

    def test_wrong_module_hash_arg(self):
        with self.assertRaisesRegex(rp.ProbeError, "module sha256 length"):
            rp.check_sha256_arg("abc")
        with self.assertRaisesRegex(rp.ProbeError, "module sha256"):
            rp.check_sha256_arg("z" * 64)


class TestRunProbe(unittest.TestCase):
    def test_check_only_ok_no_subprocess(self):
        payload = good_payload()
        host = FakeHost(files=good_files(payload))
        msg = rp.run_probe(check_args(), host=host, cfg=good_cfg(payload))
        self.assertEqual(msg, "check-only: ok")
        self.assertEqual(host.subprocess_calls, [])
        self.assertEqual(host.opened, [])

    def test_wrong_boot_no_subprocess(self):
        payload = good_payload()
        files = good_files(payload)
        files[rp.BOOT_ID_PATH] = "deadbeef-0977-4eb0-93a9-e60359bec201\n"
        host = FakeHost(files=files)
        with self.assertRaisesRegex(rp.ProbeError, "boot UUID"):
            rp.run_probe(load_args(True), host=host, cfg=good_cfg(payload))
        self.assertEqual(host.subprocess_calls, [])

    def test_wrong_kernel_no_subprocess(self):
        payload = good_payload()
        host = FakeHost(files=good_files(payload), release="7.1.9-generic")
        with self.assertRaisesRegex(rp.ProbeError, "kernel release"):
            rp.run_probe(load_args(), host=host, cfg=good_cfg(payload))
        self.assertEqual(host.subprocess_calls, [])

    def test_wrong_euid_no_subprocess(self):
        payload = good_payload()
        host = FakeHost(files=good_files(payload), euid=1000)
        with self.assertRaisesRegex(rp.ProbeError, "euid"):
            rp.run_probe(load_args(), host=host, cfg=good_cfg(payload))
        self.assertEqual(host.subprocess_calls, [])

    def test_wrong_module_hash_no_subprocess(self):
        payload = good_payload()
        host = FakeHost(files=good_files(payload))
        args = load_args(True)
        args.sha256 = "0" * 64
        with self.assertRaisesRegex(rp.ProbeError, "module sha256 mismatch"):
            rp.run_probe(args, host=host, cfg=good_cfg(payload))
        self.assertEqual(host.subprocess_calls, [])
        self.assertEqual(host.closed, [7])

    def test_wrong_adt_hash_no_subprocess(self):
        payload = good_payload()
        host = FakeHost(files=good_files(payload))
        cfg = rp.ProbeConfig(adt_sha256="0" * 64)
        with self.assertRaisesRegex(rp.ProbeError, "ADT sha256"):
            rp.run_probe(load_args(), host=host, cfg=cfg)
        self.assertEqual(host.subprocess_calls, [])

    def test_malformed_elf_no_subprocess(self):
        payload = good_payload()
        files = good_files(payload)
        files[rp.KCORE_PATH] = b"not-an-elf"
        host = FakeHost(files=files)
        with self.assertRaises(rp.ProbeError):
            rp.run_probe(load_args(), host=host, cfg=good_cfg(payload))
        self.assertEqual(host.subprocess_calls, [])

    def test_ambiguous_elf_no_subprocess(self):
        payload = good_payload()
        files = good_files(payload)
        files[rp.KCORE_PATH] = assemble_kcore(
            [
                pt_load(4096, ADT_PHYS, ADT_SIZE),
                pt_load(8192, ADT_PHYS, ADT_SIZE),
            ],
            4096,
            payload,
        )
        host = FakeHost(files=files)
        with self.assertRaisesRegex(rp.ProbeError, "ambiguous"):
            rp.run_probe(load_args(True), host=host, cfg=good_cfg(payload))
        self.assertEqual(host.subprocess_calls, [])

    def test_short_read_no_subprocess(self):
        payload = good_payload()
        files = good_files(payload)
        full = files[rp.KCORE_PATH]
        files[rp.KCORE_PATH] = truncate_payload(full, 4096, 16)
        self.assertEqual(files[rp.KCORE_PATH][:4], b"\x7fELF")
        self.assertEqual(files[rp.KCORE_PATH][:64], full[:64])
        host = FakeHost(files=files)
        with self.assertRaisesRegex(rp.ProbeError, "ADT short read"):
            rp.run_probe(load_args(), host=host, cfg=good_cfg(payload))
        self.assertEqual(host.subprocess_calls, [])

    def test_already_loaded_no_subprocess(self):
        payload = good_payload()
        files = good_files(
            payload,
            extra={rp.MODULES_PATH: "j713_isp_dartmap 4096 0 - Live 0x0\n"},
        )
        host = FakeHost(files=files)
        with self.assertRaisesRegex(rp.ProbeError, "already loaded"):
            rp.run_probe(load_args(), host=host, cfg=good_cfg(payload))
        self.assertEqual(host.subprocess_calls, [])

    def test_boot_recheck_before_load(self):
        payload = good_payload()
        host = FakeHost(
            files=good_files(payload),
            boot_id_second="11111111-1111-1111-1111-111111111111\n",
        )
        with self.assertRaisesRegex(rp.ProbeError, "boot UUID"):
            rp.run_probe(load_args(True), host=host, cfg=good_cfg(payload))
        self.assertEqual(host.boot_reads, 2)
        self.assertEqual(host.subprocess_calls, [])

    def test_validate_calls_insmod_map_check_false(self):
        payload = good_payload()
        host = FakeHost(files=good_files(payload))
        msg = rp.run_probe(load_args(False), host=host, cfg=good_cfg(payload))
        self.assertEqual(msg, "validate: loaded")
        self.assertEqual(
            host.subprocess_calls,
            [(["insmod", "/proc/self/fd/7", "map_check=false"], (7,))],
        )
        self.assertEqual(host.writes, [])

    def test_map_check_calls_insmod_map_check_true(self):
        payload = good_payload()
        host = FakeHost(files=good_files(payload))
        msg = rp.run_probe(load_args(True), host=host, cfg=good_cfg(payload))
        self.assertEqual(msg, "map-check: loaded")
        self.assertEqual(
            host.subprocess_calls,
            [(["insmod", "/proc/self/fd/7", "map_check=true"], (7,))],
        )
        self.assertEqual(host.closed, [7])
        self.assertEqual(host.writes, [])

    def test_insmod_failure_reported_once(self):
        payload = good_payload()
        host = FakeHost(
            files=good_files(payload),
            insmod_result=FakeProc(returncode=1, stderr="insmod: ERROR: foo"),
        )
        with self.assertRaisesRegex(rp.ProbeError, "insmod failed"):
            rp.run_probe(load_args(True), host=host, cfg=good_cfg(payload))
        self.assertEqual(len(host.subprocess_calls), 1)
        self.assertEqual(host.writes, [])

    def test_load_requires_module_and_hash(self):
        err = io.StringIO()
        old = sys.stderr
        sys.stderr = err
        try:
            with self.assertRaises(SystemExit) as caught:
                rp.parse_args(["--map-check"])
        finally:
            sys.stderr = old
        self.assertNotEqual(caught.exception.code, 0)
        self.assertIn("--module and --sha256", err.getvalue())

    def test_default_is_check_only(self):
        args = rp.parse_args([])
        self.assertTrue(args.check_only)
        self.assertFalse(args.validate)
        self.assertFalse(args.map_check)
        self.assertFalse(args.cleanup_only)

    def test_map_check_boot_guard_no_insmod(self):
        payload = good_payload()
        files = good_files(payload)
        files[rp.BOOT_ID_PATH] = "deadbeef-0977-4eb0-93a9-e60359bec201\n"
        host = FakeHost(files=files)
        with self.assertRaisesRegex(rp.ProbeError, "boot UUID"):
            rp.run_probe(load_args(True), host=host, cfg=good_cfg(payload))
        self.assertEqual(host.subprocess_calls, [])
        self.assertEqual(host.opened, [])
        self.assertEqual(host.writes, [])

    def test_map_check_hash_guard_no_insmod(self):
        payload = good_payload()
        host = FakeHost(files=good_files(payload))
        args = load_args(True)
        args.sha256 = "0" * 64
        with self.assertRaisesRegex(rp.ProbeError, "module sha256 mismatch"):
            rp.run_probe(args, host=host, cfg=good_cfg(payload))
        self.assertEqual(host.subprocess_calls, [])
        self.assertEqual(host.closed, [7])

    def test_map_check_requires_module_and_hash(self):
        err = io.StringIO()
        old = sys.stderr
        sys.stderr = err
        try:
            with self.assertRaises(SystemExit) as caught:
                rp.parse_args(["--validate"])
        finally:
            sys.stderr = old
        self.assertNotEqual(caught.exception.code, 0)
        self.assertIn("--module and --sha256", err.getvalue())

    def test_map_check_and_validate_are_exclusive(self):
        err = io.StringIO()
        old = sys.stderr
        sys.stderr = err
        try:
            with self.assertRaises(SystemExit):
                rp.parse_args([
                    "--map-check",
                    "--validate",
                    "--module",
                    "/tmp/j713_isp_dartmap.ko",
                    "--sha256",
                    MODULE_SHA,
                ])
        finally:
            sys.stderr = old
        self.assertIn("not allowed", err.getvalue())

    def test_old_apply_and_inspect_are_rejected(self):
        err = io.StringIO()
        old = sys.stderr
        sys.stderr = err
        try:
            with self.assertRaises(SystemExit):
                rp.parse_args(["--apply"])
        finally:
            sys.stderr = old
        self.assertIn("unrecognized arguments", err.getvalue())

    def test_cleanup_only_writes_sysfs_once(self):
        payload = good_payload()
        files = good_files(
            payload,
            extra={rp.MODULES_PATH: "j713_isp_dartmap 4096 0 - Live 0x0\n"},
        )
        host = FakeHost(files=files)
        args = rp.parse_args(["--cleanup-only"])
        msg = rp.run_probe(args, host=host, cfg=good_cfg(payload))
        self.assertEqual(msg, "cleanup-only: requested")
        self.assertEqual(host.subprocess_calls, [])
        self.assertEqual(host.opened, [])
        self.assertEqual(host.writes, [(rp.CLEANUP_PATH, "1\n")])
        self.assertEqual(host.boot_reads, 2)

    def test_cleanup_only_requires_loaded_module(self):
        payload = good_payload()
        host = FakeHost(files=good_files(payload))
        args = rp.parse_args(["--cleanup-only"])
        with self.assertRaisesRegex(rp.ProbeError, "module not loaded"):
            rp.run_probe(args, host=host, cfg=good_cfg(payload))
        self.assertEqual(host.writes, [])
        self.assertEqual(host.subprocess_calls, [])

    def test_cleanup_only_rejects_module_args(self):
        err = io.StringIO()
        old = sys.stderr
        sys.stderr = err
        try:
            with self.assertRaises(SystemExit):
                rp.parse_args([
                    "--cleanup-only",
                    "--module",
                    "/tmp/j713_isp_dartmap.ko",
                    "--sha256",
                    MODULE_SHA,
                ])
        finally:
            sys.stderr = old
        self.assertIn("does not load", err.getvalue())

    def test_cleanup_only_boot_guard_does_not_write(self):
        payload = good_payload()
        files = good_files(
            payload,
            extra={rp.MODULES_PATH: "j713_isp_dartmap 4096 0 - Live 0x0\n"},
        )
        files[rp.BOOT_ID_PATH] = "deadbeef-0977-4eb0-93a9-e60359bec201\n"
        host = FakeHost(files=files)
        with self.assertRaisesRegex(rp.ProbeError, "boot UUID"):
            rp.run_probe(rp.parse_args(["--cleanup-only"]), host=host, cfg=good_cfg(payload))
        self.assertEqual(host.writes, [])

    def test_main_wrong_kernel_no_subprocess(self):
        payload = good_payload()
        host = FakeHost(files=good_files(payload), release="not-the-kernel")
        rc = rp.main(["--check-only"], host=host, cfg=good_cfg(payload))
        self.assertEqual(rc, 1)
        self.assertEqual(host.subprocess_calls, [])

    def test_lock_held_during_checks_and_released(self):
        payload = good_payload()
        host = FakeHost(files=good_files(payload))
        msg = rp.run_probe(check_args(), host=host, cfg=good_cfg(payload))
        self.assertEqual(msg, "check-only: ok")
        self.assertTrue(host.saw_lock_while_reading)
        self.assertFalse(host.lock_held)
        self.assertEqual(host.subprocess_calls, [])
        self.assertEqual(host.writes, [])

    def test_lock_busy_skips_load_and_cleanup(self):
        payload = good_payload()
        loaded = good_files(
            payload,
            extra={rp.MODULES_PATH: "j713_isp_dartmap 4096 0 - Live 0x0\n"},
        )
        for args in (load_args(False), load_args(True), rp.parse_args(["--cleanup-only"])):
            host = FakeHost(files=loaded, lock_busy=True)
            with self.assertRaisesRegex(rp.ProbeError, "probe lock busy"):
                rp.run_probe(args, host=host, cfg=good_cfg(payload))
            self.assertEqual(host.subprocess_calls, [])
            self.assertEqual(host.writes, [])
            self.assertEqual(host.opened, [])
            self.assertFalse(host.lock_held)
            self.assertEqual(host.boot_reads, 0)

    def test_uppercase_module_hash_loads(self):
        payload = good_payload()
        host = FakeHost(files=good_files(payload))
        args = load_args(False)
        args.sha256 = MODULE_SHA.upper()
        msg = rp.run_probe(args, host=host, cfg=good_cfg(payload))
        self.assertEqual(msg, "validate: loaded")
        self.assertEqual(host.subprocess_calls[0][0][2], "map_check=false")
        self.assertEqual(host.writes, [])

    def test_check_only_ignores_module_path(self):
        payload = good_payload()
        host = FakeHost(files=good_files(payload))
        args = rp.parse_args([
            "--check-only",
            "--module",
            "/tmp/j713_isp_dartmap.ko",
            "--sha256",
            MODULE_SHA,
        ])
        msg = rp.run_probe(args, host=host, cfg=good_cfg(payload))
        self.assertEqual(msg, "check-only: ok")
        self.assertEqual(host.subprocess_calls, [])
        self.assertEqual(host.opened, [])
        self.assertEqual(host.writes, [])

    def test_modes_are_mutually_exclusive(self):
        pairs = [
            ["--check-only", "--validate"],
            ["--check-only", "--map-check"],
            ["--check-only", "--cleanup-only"],
            ["--validate", "--cleanup-only"],
            ["--map-check", "--cleanup-only", "--module", "/tmp/j713_isp_dartmap.ko",
             "--sha256", MODULE_SHA],
            ["--validate", "--map-check", "--cleanup-only",
             "--module", "/tmp/j713_isp_dartmap.ko", "--sha256", MODULE_SHA],
            ["--apply"],
            ["--inspect"],
            ["--map-check", "--apply"],
        ]
        for argv in pairs:
            err = io.StringIO()
            old = sys.stderr
            sys.stderr = err
            try:
                with self.assertRaises(SystemExit):
                    rp.parse_args(argv)
            finally:
                sys.stderr = old
            text = err.getvalue()
            self.assertTrue(
                "not allowed" in text or "unrecognized arguments" in text,
                argv,
            )

    def test_cleanup_only_writes_sysfs_and_never_insmods(self):
        payload = good_payload()
        files = good_files(
            payload,
            extra={
                rp.MODULES_PATH: (
                    "cfg80211 1 0 - Live 0x0\n"
                    "j713_isp_dartmap 4096 0 - Live 0x0\n"
                )
            },
        )
        host = FakeHost(files=files)
        args = argparse.Namespace(
            check_only=False,
            validate=False,
            map_check=False,
            cleanup_only=True,
            module="/tmp/j713_isp_dartmap.ko",
            sha256=MODULE_SHA,
        )
        msg = rp.run_probe(args, host=host, cfg=good_cfg(payload))
        self.assertEqual(msg, "cleanup-only: requested")
        self.assertEqual(host.subprocess_calls, [])
        self.assertEqual(host.opened, [])
        self.assertEqual(host.writes, [(rp.CLEANUP_PATH, "1\n")])

    def test_cleanup_only_rejects_lookalike_module_name(self):
        payload = good_payload()
        files = good_files(
            payload,
            extra={rp.MODULES_PATH: "j713_isp_dartmap_extra 4096 0 - Live 0x0\n"},
        )
        host = FakeHost(files=files)
        with self.assertRaisesRegex(rp.ProbeError, "module not loaded"):
            rp.run_probe(rp.parse_args(["--cleanup-only"]), host=host,
                         cfg=good_cfg(payload))
        self.assertEqual(host.writes, [])
        self.assertEqual(host.subprocess_calls, [])

    def test_cleanup_only_guards_do_not_write(self):
        payload = good_payload()
        loaded = good_files(
            payload,
            extra={rp.MODULES_PATH: "j713_isp_dartmap 4096 0 - Live 0x0\n"},
        )
        cases = []
        bad_boot = dict(loaded)
        bad_boot[rp.BOOT_ID_PATH] = "deadbeef-0977-4eb0-93a9-e60359bec201\n"
        cases.append((bad_boot, {}, "boot UUID"))
        cases.append((loaded, {"release": "7.1.9-generic"}, "kernel release"))
        cases.append((loaded, {"euid": 1000}, "euid"))
        bad_compat = dict(loaded)
        bad_compat[rp.COMPAT_PATH] = b"apple,j314\0apple,t6000\0"
        cases.append((bad_compat, {}, "compatible"))
        bad_reg = dict(loaded)
        bad_reg[rp.FLASHREG_PATH] = struct.pack(">QQ", ADT_PHYS, 0x1000)
        cases.append((bad_reg, {}, "flashreg"))
        bad_elf = dict(loaded)
        bad_hdr, _table = elf64_le(
            [pt_load(4096, ADT_PHYS, ADT_SIZE)],
            ident_extra=b"NOPE",
        )
        self.assertEqual(len(bad_hdr), rp.ELF64_EHDR_SIZE)
        self.assertNotEqual(bad_hdr[:4], b"\x7fELF")
        bad_elf[rp.KCORE_PATH] = bad_hdr
        cases.append((bad_elf, {}, "not ELF"))
        for files, host_kw, pattern in cases:
            host = FakeHost(files=files, **host_kw)
            with self.assertRaisesRegex(rp.ProbeError, pattern):
                rp.run_probe(
                    rp.parse_args(["--cleanup-only"]),
                    host=host,
                    cfg=good_cfg(payload),
                )
            self.assertEqual(host.writes, [])
            self.assertEqual(host.subprocess_calls, [])
            self.assertEqual(host.opened, [])

    def test_cleanup_only_boot_recheck_does_not_write(self):
        payload = good_payload()
        files = good_files(
            payload,
            extra={rp.MODULES_PATH: "j713_isp_dartmap 4096 0 - Live 0x0\n"},
        )
        host = FakeHost(
            files=files,
            boot_id_second="11111111-1111-1111-1111-111111111111\n",
        )
        with self.assertRaisesRegex(rp.ProbeError, "boot UUID"):
            rp.run_probe(rp.parse_args(["--cleanup-only"]), host=host,
                         cfg=good_cfg(payload))
        self.assertEqual(host.boot_reads, 2)
        self.assertEqual(host.writes, [])
        self.assertEqual(host.subprocess_calls, [])

    def test_cleanup_only_write_failure_does_not_insmod(self):
        payload = good_payload()
        files = good_files(
            payload,
            extra={rp.MODULES_PATH: "j713_isp_dartmap 4096 0 - Live 0x0\n"},
        )
        host = FakeHost(files=files, fail_write="os")
        rc = rp.main(["--cleanup-only"], host=host, cfg=good_cfg(payload))
        self.assertEqual(rc, 1)
        self.assertEqual(host.subprocess_calls, [])
        self.assertEqual(host.writes, [])
        self.assertEqual(host.opened, [])

        host = FakeHost(files=files, fail_write="short")
        rc = rp.main(["--cleanup-only"], host=host, cfg=good_cfg(payload))
        self.assertEqual(rc, 1)
        self.assertEqual(host.subprocess_calls, [])
        self.assertEqual(host.opened, [])

    def test_check_only_wins_over_cleanup_flag(self):
        payload = good_payload()
        files = good_files(
            payload,
            extra={rp.MODULES_PATH: "j713_isp_dartmap 4096 0 - Live 0x0\n"},
        )
        host = FakeHost(files=files)
        args = argparse.Namespace(
            check_only=True,
            validate=False,
            map_check=False,
            cleanup_only=True,
            module=None,
            sha256=None,
        )
        msg = rp.run_probe(args, host=host, cfg=good_cfg(payload))
        self.assertEqual(msg, "check-only: ok")
        self.assertEqual(host.writes, [])
        self.assertEqual(host.subprocess_calls, [])


if __name__ == "__main__":
    unittest.main()
