#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Offline unit tests for run_probe.py. Orchestrator runs these remotely."""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import io
import os
import struct
import sys
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_probe as rp  # noqa: E402


ADT_PHYS = rp.ADT_PHYS
ADT_SIZE = rp.ADT_SIZE
MODULE_BYTES = b"fake-j713-isp-fwview-module\n"
TEXT_IMAGE = b"text-image"
DATA_IMAGE = b"data-image"
TEXT_PHYS = 0x1000
DATA_PHYS = 0x2000
DATA_REF = "/tmp/j713-data.ref"
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
        self.hash_calls = []
        self.phys_reads = []
        self.mem_writes = []
        self.reference_events = []
        self.reference_reads = []
        self.close_raises = kwargs.get("close_raises", False)
        self.modules_raises = kwargs.get("modules_raises", False)
        self.ref_regular = dict(kwargs.get("ref_regular", {}))
        self.phys = dict(kwargs.get("phys", {
            (TEXT_PHYS, len(TEXT_IMAGE)): TEXT_IMAGE,
            (DATA_PHYS, len(DATA_IMAGE)): DATA_IMAGE,
        }))
        self.phys_after = dict(kwargs.get("phys_after", {}))
        self.ref_files = dict(kwargs.get("ref_files", {}))
        self.ref_mode = dict(kwargs.get("ref_mode", {}))
        self.ref_owner = dict(kwargs.get("ref_owner", {}))
        self._obs = {}
        self._restored = {}
        self.restore_sticks = kwargs.get("restore_sticks", True)
        self.write_raises = kwargs.get("write_raises", False)
        self.write_open_fails = kwargs.get("write_open_fails", False)
        self.unreadable_params = kwargs.get("unreadable_params", False)
        self.second_text = dict(kwargs.get("second_text", {}))
        self.text_reads = {}
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
        if self.unreadable_params and path.startswith(rp.PARAM_DIR + "/"):
            raise OSError(13, "Permission denied")
        if (
            self.modules_raises
            and self.subprocess_calls
            and path == rp.MODULES_PATH
        ):
            raise OSError(5, "Input/output error")
        if path == rp.BOOT_ID_PATH:
            self.boot_reads += 1
            if self.boot_reads > 1 and self.boot_id_second is not None:
                return self.boot_id_second
        self.text_reads[path] = self.text_reads.get(path, 0) + 1
        if self.text_reads[path] > 1 and path in self.second_text:
            return self.second_text[path]
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
        if self.close_raises and self.subprocess_calls:
            raise OSError(9, "Bad file descriptor")

    def run_insmod(self, argv, pass_fds):
        self.subprocess_calls.append((list(argv), tuple(pass_fds)))
        return self.insmod_result

    def _observe(self, phys, size):
        key = (phys, size)
        image = self.phys.get(key)
        if image is None:
            raise rp.ProbeError("phys image missing")
        seen = self._obs.get(key, 0) + 1
        self._obs[key] = seen
        if seen >= 2 and key in self.phys_after and not self._restored.get(key):
            image = self.phys_after[key]
        if len(image) != size:
            raise rp.ProbeError("phys image short")
        return bytes(image)

    def hash_phys(self, phys, size, path=None):
        self.hash_calls.append((phys, size, path))
        return hashlib.sha256(self._observe(phys, size)).hexdigest()

    def read_phys(self, phys, size, path=None):
        self.phys_reads.append((phys, size, path))
        return self._observe(phys, size)

    def write_phys(self, phys, blob, path=None):
        if self.write_open_fails:
            err = OSError("open")
            err.opened = False
            raise err
        payload = bytes(blob)
        self.mem_writes.append((phys, len(payload), payload, path))
        if self.restore_sticks:
            key = (phys, len(payload))
            self._restored[key] = True
            self.phys[key] = payload
        if self.write_raises:
            raise OSError("msync")

    def read_data_reference(self, path, size):
        self.reference_reads.append((path, size))
        if size <= 0:
            raise rp.ProbeError("DATA reference size")
        if path not in self.ref_files:
            raise rp.ProbeError("DATA reference missing")
        if not self.ref_regular.get(path, True):
            raise rp.ProbeError("DATA reference is not a regular file")
        if self.ref_mode.get(path, 0o600) != 0o600:
            raise rp.ProbeError("DATA reference mode")
        if self.ref_owner.get(path, self.euid) != self.euid:
            raise rp.ProbeError("DATA reference owner")
        blob = self.ref_files[path]
        if not isinstance(blob, (bytes, bytearray)) or len(blob) != size:
            raise rp.ProbeError("DATA reference size")
        return bytes(blob)

    def store_reference(self, path, blob):
        payload = bytes(blob)
        if path in self.ref_files:
            mode = self.ref_mode.get(path, 0o600)
            owner = self.ref_owner.get(path, self.euid)
            if mode != 0o600:
                raise rp.ProbeError("DATA reference mode")
            if owner != self.euid:
                raise rp.ProbeError("DATA reference owner")
            if self.ref_files[path] != payload:
                raise rp.ProbeError("DATA reference differs")
            self.reference_events.append((path, payload, "existing"))
            return
        self.ref_files[path] = payload
        self.ref_mode[path] = 0o600
        self.ref_owner[path] = self.euid
        self.reference_events.append((path, payload, "created"))

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


def fw_cfg(payload):
    return dataclasses.replace(
        good_cfg(payload),
        text_phys=TEXT_PHYS,
        text_size=len(TEXT_IMAGE),
        data_phys=DATA_PHYS,
        data_size=len(DATA_IMAGE),
        text_sha256=hashlib.sha256(TEXT_IMAGE).hexdigest(),
        data_sha256=hashlib.sha256(DATA_IMAGE).hexdigest(),
    )


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
        "/tmp/j713_isp_fwview.ko": MODULE_BYTES,
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
        data_ref=None,
    )


def load_args(map_check=False):
    return argparse.Namespace(
        check_only=False,
        validate=not map_check,
        map_check=bool(map_check),
        cleanup_only=False,
        module="/tmp/j713_isp_fwview.ko",
        sha256=MODULE_SHA,
        data_ref=DATA_REF if map_check else None,
    )


def survey_argv():
    return [
        "--dart-survey",
        "--module",
        "/tmp/j713_isp_fwview.ko",
        "--sha256",
        MODULE_SHA,
    ]


def survey_args():
    return rp.parse_args(survey_argv())


def param_files(**overrides):
    values = {name: 0 for name in rp.PARAM_NAMES}
    values.update(overrides)
    return {
        rp.PARAM_DIR + "/" + name: "%d\n" % values[name]
        for name in rp.PARAM_NAMES
    }


def map_counter_params(**overrides):
    values = {
        "lookup_count": rp.LOOKUP_COUNT,
        "readback_count": rp.READBACK_COUNT,
        "pte_walk_count": rp.PTE_WALK_COUNT,
    }
    values.update(overrides)
    return param_files(**values)


def restore_argv():
    return ["--restore-data", "--data-ref", DATA_REF]


def run_restore(host, cfg):
    err = io.StringIO()
    out = io.StringIO()
    with redirect_stderr(err), redirect_stdout(out):
        rc = rp.main(restore_argv(), host=host, cfg=cfg)
    return rc, out.getvalue(), err.getvalue()


def loaded_module_line():
    return "j713_isp_fwview 4096 0 - Live 0x0\n"


def idle_param_files(**overrides):
    values = {"map_count": 0, "retain": 0, "cleanup_pending": 0}
    values.update(overrides)
    return {
        rp.PARAM_DIR + "/" + name: "%d\n" % values[name]
        for name in values
    }


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
        self.assertEqual(host.hash_calls, [])

    def test_wrong_boot_no_subprocess(self):
        payload = good_payload()
        files = good_files(payload)
        files[rp.BOOT_ID_PATH] = "deadbeef-0977-4eb0-93a9-e60359bec201\n"
        host = FakeHost(files=files)
        with self.assertRaisesRegex(rp.ProbeError, "boot UUID"):
            rp.run_probe(load_args(True), host=host, cfg=good_cfg(payload))
        self.assertEqual(host.subprocess_calls, [])
        self.assertEqual(host.hash_calls, [])

    def test_wrong_kernel_no_subprocess(self):
        payload = good_payload()
        host = FakeHost(files=good_files(payload), release="7.1.9-generic")
        with self.assertRaisesRegex(rp.ProbeError, "kernel release"):
            rp.run_probe(load_args(), host=host, cfg=good_cfg(payload))
        self.assertEqual(host.subprocess_calls, [])
        self.assertEqual(host.hash_calls, [])

    def test_wrong_euid_no_subprocess(self):
        payload = good_payload()
        host = FakeHost(files=good_files(payload), euid=1000)
        with self.assertRaisesRegex(rp.ProbeError, "euid"):
            rp.run_probe(load_args(), host=host, cfg=good_cfg(payload))
        self.assertEqual(host.subprocess_calls, [])
        self.assertEqual(host.hash_calls, [])

    def test_wrong_module_hash_no_subprocess(self):
        payload = good_payload()
        host = FakeHost(files=good_files(payload))
        args = load_args(True)
        args.sha256 = "0" * 64
        with self.assertRaisesRegex(rp.ProbeError, "module sha256 mismatch"):
            rp.run_probe(args, host=host, cfg=fw_cfg(payload))
        self.assertEqual(host.subprocess_calls, [])
        self.assertEqual(host.closed, [7])
        self.assertEqual(host.hash_calls, [
            (TEXT_PHYS, len(TEXT_IMAGE), rp.DEV_MEM_PATH),
        ])
        self.assertEqual(host.writes, [])
        self.assertEqual(host.mem_writes, [])

    def test_wrong_adt_hash_no_subprocess(self):
        payload = good_payload()
        host = FakeHost(files=good_files(payload))
        cfg = rp.ProbeConfig(adt_sha256="0" * 64)
        with self.assertRaisesRegex(rp.ProbeError, "ADT sha256"):
            rp.run_probe(load_args(), host=host, cfg=cfg)
        self.assertEqual(host.subprocess_calls, [])
        self.assertEqual(host.hash_calls, [])

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
        self.assertEqual(host.hash_calls, [])

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
            extra={rp.MODULES_PATH: "j713_isp_fwview 4096 0 - Live 0x0\n"},
        )
        host = FakeHost(files=files)
        with self.assertRaisesRegex(rp.ProbeError, "already loaded"):
            rp.run_probe(load_args(), host=host, cfg=good_cfg(payload))
        self.assertEqual(host.subprocess_calls, [])
        if hasattr(host, "mem_writes"):
            self.assertEqual(host.mem_writes, [])

    def test_boot_recheck_before_load(self):
        payload = good_payload()
        host = FakeHost(
            files=good_files(payload),
            boot_id_second="11111111-1111-1111-1111-111111111111\n",
        )
        with self.assertRaisesRegex(rp.ProbeError, "boot UUID"):
            rp.run_probe(load_args(True), host=host, cfg=fw_cfg(payload))
        self.assertEqual(host.boot_reads, 2)
        self.assertEqual(host.subprocess_calls, [])
        self.assertEqual(host.hash_calls, [
            (TEXT_PHYS, len(TEXT_IMAGE), rp.DEV_MEM_PATH),
        ])
        self.assertEqual(host.writes, [])

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
        self.assertEqual(host.hash_calls, [])

    def test_map_check_calls_insmod_map_check_true(self):
        payload = good_payload()
        host = FakeHost(files=good_files(payload))
        msg = rp.run_probe(load_args(True), host=host, cfg=fw_cfg(payload))
        self.assertEqual(msg, "map-check: loaded")
        self.assertEqual(
            host.subprocess_calls,
            [(["insmod", "/proc/self/fd/7", "map_check=true"], (7,))],
        )
        self.assertEqual(host.closed, [7])
        self.assertEqual(host.writes, [])
        self.assertEqual(host.mem_writes, [])
        self.assertEqual(host.hash_calls, [
            (TEXT_PHYS, len(TEXT_IMAGE), rp.DEV_MEM_PATH),
            (TEXT_PHYS, len(TEXT_IMAGE), rp.DEV_MEM_PATH),
            (DATA_PHYS, len(DATA_IMAGE), rp.DEV_MEM_PATH),
        ])
        self.assertEqual(host.phys_reads, [
            (DATA_PHYS, len(DATA_IMAGE), rp.DEV_MEM_PATH),
        ])
        self.assertEqual(host.reference_events, [
            (DATA_REF, DATA_IMAGE, "created"),
        ])
        self.assertEqual(host.ref_files[DATA_REF], DATA_IMAGE)
        self.assertEqual(host.ref_mode[DATA_REF], 0o600)

    def test_insmod_failure_reported_once(self):
        payload = good_payload()
        host = FakeHost(
            files=good_files(payload),
            insmod_result=FakeProc(returncode=1, stderr="insmod: ERROR: foo"),
        )
        with self.assertRaisesRegex(rp.ProbeError, "insmod failed"):
            rp.run_probe(load_args(True), host=host, cfg=fw_cfg(payload))
        self.assertEqual(len(host.subprocess_calls), 1)
        self.assertEqual(host.writes, [])
        self.assertEqual(host.mem_writes, [])
        self.assertEqual(len(host.hash_calls), 3)

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
        self.assertFalse(args.dart_survey)
        self.assertFalse(args.cleanup_only)
        self.assertFalse(args.restore_data)

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
        self.assertEqual(host.hash_calls, [])

    def test_map_check_hash_guard_no_insmod(self):
        payload = good_payload()
        host = FakeHost(files=good_files(payload))
        args = load_args(True)
        args.sha256 = "0" * 64
        with self.assertRaisesRegex(rp.ProbeError, "module sha256 mismatch"):
            rp.run_probe(args, host=host, cfg=fw_cfg(payload))
        self.assertEqual(host.subprocess_calls, [])
        self.assertEqual(host.closed, [7])
        self.assertEqual(host.hash_calls, [
            (TEXT_PHYS, len(TEXT_IMAGE), rp.DEV_MEM_PATH),
        ])
        self.assertEqual(host.writes, [])
        self.assertEqual(host.mem_writes, [])

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
                    "/tmp/j713_isp_fwview.ko",
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
            extra={rp.MODULES_PATH: "j713_isp_fwview 4096 0 - Live 0x0\n"},
        )
        host = FakeHost(files=files)
        args = rp.parse_args(["--cleanup-only"])
        msg = rp.run_probe(args, host=host, cfg=good_cfg(payload))
        self.assertEqual(msg, "cleanup-only: requested")
        self.assertEqual(host.subprocess_calls, [])
        self.assertEqual(host.opened, [])
        self.assertEqual(host.writes, [(rp.CLEANUP_PATH, "1\n")])
        self.assertEqual(host.boot_reads, 2)
        self.assertEqual(host.hash_calls, [])

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
                    "/tmp/j713_isp_fwview.ko",
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
            extra={rp.MODULES_PATH: "j713_isp_fwview 4096 0 - Live 0x0\n"},
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
            extra={rp.MODULES_PATH: "j713_isp_fwview 4096 0 - Live 0x0\n"},
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
            "/tmp/j713_isp_fwview.ko",
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
            ["--map-check", "--cleanup-only", "--module", "/tmp/j713_isp_fwview.ko",
             "--sha256", MODULE_SHA],
            ["--validate", "--map-check", "--cleanup-only",
             "--module", "/tmp/j713_isp_fwview.ko", "--sha256", MODULE_SHA],
            ["--apply"],
            ["--inspect"],
            ["--map-check", "--apply"],
            ["--restore-data", "--map-check"],
            ["--restore-data", "--validate"],
            ["--restore-data", "--cleanup-only"],
            ["--restore-data", "--check-only"],
            ["--dart-survey", "--check-only"],
            ["--dart-survey", "--validate"],
            ["--dart-survey", "--map-check"],
            ["--dart-survey", "--cleanup-only"],
            ["--dart-survey", "--restore-data"],
            ["--dart-survey", "--map-check", "--module", "/tmp/j713_isp_fwview.ko",
             "--sha256", MODULE_SHA, "--data-ref", DATA_REF],
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
                    "j713_isp_fwview 4096 0 - Live 0x0\n"
                )
            },
        )
        host = FakeHost(files=files)
        args = argparse.Namespace(
            check_only=False,
            validate=False,
            map_check=False,
            cleanup_only=True,
            module="/tmp/j713_isp_fwview.ko",
            sha256=MODULE_SHA,
            data_ref=None,
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
            extra={rp.MODULES_PATH: "j713_isp_fwview_extra 4096 0 - Live 0x0\n"},
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
            extra={rp.MODULES_PATH: "j713_isp_fwview 4096 0 - Live 0x0\n"},
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
            extra={rp.MODULES_PATH: "j713_isp_fwview 4096 0 - Live 0x0\n"},
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
            extra={rp.MODULES_PATH: "j713_isp_fwview 4096 0 - Live 0x0\n"},
        )
        host = FakeHost(files=files, fail_write="os")
        rc = rp.main(["--cleanup-only"], host=host, cfg=good_cfg(payload))
        self.assertEqual(rc, 1)
        self.assertEqual(host.subprocess_calls, [])
        self.assertEqual(host.writes, [])
        self.assertEqual(host.opened, [])
        self.assertEqual(host.hash_calls, [])

        host = FakeHost(files=files, fail_write="short")
        rc = rp.main(["--cleanup-only"], host=host, cfg=good_cfg(payload))
        self.assertEqual(rc, 1)
        self.assertEqual(host.subprocess_calls, [])
        self.assertEqual(host.opened, [])

    def test_check_only_wins_over_cleanup_flag(self):
        payload = good_payload()
        files = good_files(
            payload,
            extra={rp.MODULES_PATH: "j713_isp_fwview 4096 0 - Live 0x0\n"},
        )
        host = FakeHost(files=files)
        args = argparse.Namespace(
            check_only=True,
            validate=False,
            map_check=False,
            cleanup_only=True,
            module=None,
            sha256=None,
            data_ref=None,
        )
        msg = rp.run_probe(args, host=host, cfg=good_cfg(payload))
        self.assertEqual(msg, "check-only: ok")
        self.assertEqual(host.writes, [])
        self.assertEqual(host.subprocess_calls, [])
        self.assertEqual(host.hash_calls, [])

    def test_text_reference_mismatch_refuses_before_insmod(self):
        payload = good_payload()
        host = FakeHost(files=good_files(payload))
        cfg = dataclasses.replace(fw_cfg(payload), text_sha256="0" * 64)
        with self.assertRaisesRegex(rp.ProbeError, "TEXT hash is not the qualified reference"):
            rp.run_probe(load_args(True), host=host, cfg=cfg)
        self.assertEqual(host.hash_calls, [
            (TEXT_PHYS, len(TEXT_IMAGE), rp.DEV_MEM_PATH),
        ])
        self.assertEqual(host.phys_reads, [])
        self.assertEqual(host.reference_events, [])
        self.assertEqual(host.subprocess_calls, [])
        self.assertEqual(host.opened, [])
        self.assertEqual(host.writes, [])

    def test_data_reference_mismatch_refuses_before_insmod(self):
        payload = good_payload()
        host = FakeHost(files=good_files(payload))
        cfg = dataclasses.replace(fw_cfg(payload), data_sha256="0" * 64)
        with self.assertRaisesRegex(rp.ProbeError, "DATA hash is not the qualified reference"):
            rp.run_probe(load_args(True), host=host, cfg=cfg)
        self.assertEqual(host.hash_calls, [
            (TEXT_PHYS, len(TEXT_IMAGE), rp.DEV_MEM_PATH),
        ])
        self.assertEqual(host.phys_reads, [
            (DATA_PHYS, len(DATA_IMAGE), rp.DEV_MEM_PATH),
        ])
        self.assertEqual(host.reference_events, [])
        self.assertEqual(host.subprocess_calls, [])
        self.assertEqual(host.opened, [])
        self.assertEqual(host.writes, [])

    def test_uppercase_firmware_hash_loads(self):
        payload = good_payload()
        host = FakeHost(files=good_files(payload))
        base = fw_cfg(payload)
        cfg = dataclasses.replace(
            base,
            text_sha256=base.text_sha256.upper(),
            data_sha256=base.data_sha256.upper(),
        )
        msg = rp.run_probe(load_args(True), host=host, cfg=cfg)
        self.assertEqual(msg, "map-check: loaded")
        self.assertEqual(len(host.hash_calls), 3)
        self.assertEqual(host.writes, [])
        self.assertEqual(host.mem_writes, [])

    def test_text_hash_changed_after_run(self):
        payload = good_payload()
        host = FakeHost(
            files=good_files(payload),
            phys_after={(TEXT_PHYS, len(TEXT_IMAGE)): b"text-imagX"},
        )
        with self.assertRaisesRegex(rp.ProbeError, "TEXT hash changed"):
            rp.run_probe(load_args(True), host=host, cfg=fw_cfg(payload))
        self.assertEqual(len(host.subprocess_calls), 1)
        self.assertEqual(len(host.hash_calls), 3)
        self.assertEqual(host.writes, [])
        self.assertEqual(host.mem_writes, [])

    def test_data_changed_restores_saved_bytes(self):
        payload = good_payload()
        changed = b"data-imagX"
        host = FakeHost(
            files=good_files(payload),
            phys_after={(DATA_PHYS, len(DATA_IMAGE)): changed},
        )
        with self.assertRaisesRegex(rp.ProbeError, "^DATA restored$") as caught:
            rp.run_probe(load_args(True), host=host, cfg=fw_cfg(payload))
        self.assertEqual(caught.exception.code, rp.EXIT_LADDER)
        self.assertEqual(len(host.subprocess_calls), 1)
        self.assertEqual(len(host.hash_calls), 4)
        self.assertEqual(host.writes, [])
        self.assertEqual(host.mem_writes, [
            (DATA_PHYS, len(DATA_IMAGE), DATA_IMAGE, rp.DEV_MEM_PATH),
        ])
        self.assertEqual(host.reference_events, [
            (DATA_REF, DATA_IMAGE, "created"),
        ])
        self.assertNotIn(changed, [item[2] for item in host.mem_writes])

    def test_hash_change_wins_over_insmod_failure(self):
        payload = good_payload()
        host = FakeHost(
            files=good_files(payload),
            insmod_result=FakeProc(returncode=1, stderr="insmod: ERROR: foo"),
            phys_after={(TEXT_PHYS, len(TEXT_IMAGE)): b"text-imagX"},
        )
        with self.assertRaisesRegex(rp.ProbeError, "TEXT hash changed"):
            rp.run_probe(load_args(True), host=host, cfg=fw_cfg(payload))
        self.assertEqual(len(host.subprocess_calls), 1)
        self.assertEqual(len(host.hash_calls), 3)
        self.assertEqual(host.writes, [])
        self.assertEqual(host.mem_writes, [])

    def test_existing_identical_reference_is_kept(self):
        payload = good_payload()
        host = FakeHost(
            files=good_files(payload),
            ref_files={DATA_REF: DATA_IMAGE},
        )
        msg = rp.run_probe(load_args(True), host=host, cfg=fw_cfg(payload))
        self.assertEqual(msg, "map-check: loaded")
        self.assertEqual(host.reference_events, [
            (DATA_REF, DATA_IMAGE, "existing"),
        ])
        self.assertEqual(host.ref_files[DATA_REF], DATA_IMAGE)
        self.assertEqual(host.mem_writes, [])

    def test_different_reference_refuses_before_insmod(self):
        payload = good_payload()
        host = FakeHost(
            files=good_files(payload),
            ref_files={DATA_REF: b"not-the-data"},
        )
        with self.assertRaisesRegex(rp.ProbeError, "DATA reference differs"):
            rp.run_probe(load_args(True), host=host, cfg=fw_cfg(payload))
        self.assertEqual(host.subprocess_calls, [])
        self.assertEqual(host.opened, [])
        self.assertEqual(host.mem_writes, [])
        self.assertEqual(host.ref_files[DATA_REF], b"not-the-data")

    def test_restore_while_mapped_is_refused(self):
        payload = good_payload()
        files = good_files(payload, extra=param_files(map_count=3))
        host = FakeHost(
            files=files,
            phys_after={(DATA_PHYS, len(DATA_IMAGE)): b"data-imagX"},
        )
        err = io.StringIO()
        with redirect_stderr(err):
            with self.assertRaisesRegex(rp.ProbeError, "not writing /dev/mem"):
                rp.run_probe(load_args(True), host=host, cfg=fw_cfg(payload))
        self.assertEqual(len(host.subprocess_calls), 1)
        self.assertEqual(len(host.hash_calls), 1)
        self.assertEqual(host.mem_writes, [])
        self.assertEqual(host.writes, [])
        self.assertIn("--cleanup-only is the recovery step", err.getvalue())
        self.assertNotIn("--restore-data restores", err.getvalue())

    def test_restore_verify_failure_is_reported(self):
        payload = good_payload()
        host = FakeHost(
            files=good_files(payload),
            phys_after={(DATA_PHYS, len(DATA_IMAGE)): b"data-imagX"},
            restore_sticks=False,
        )
        with self.assertRaisesRegex(rp.ProbeError, "^DATA restore failed$") as caught:
            rp.run_probe(load_args(True), host=host, cfg=fw_cfg(payload))
        self.assertEqual(caught.exception.code, rp.EXIT_LADDER)
        self.assertEqual(host.mem_writes, [
            (DATA_PHYS, len(DATA_IMAGE), DATA_IMAGE, rp.DEV_MEM_PATH),
        ])
        self.assertEqual(len(host.hash_calls), 4)

    def test_retain_fails_and_names_cleanup(self):
        payload = good_payload()
        files = good_files(payload, extra=param_files(retain=1))
        host = FakeHost(files=files)
        err = io.StringIO()
        with redirect_stderr(err):
            with self.assertRaisesRegex(rp.ProbeError, "not writing /dev/mem"):
                rp.run_probe(load_args(True), host=host, cfg=fw_cfg(payload))
        self.assertEqual(len(host.subprocess_calls), 1)
        self.assertEqual(host.insmod_result.returncode, 0)
        self.assertEqual(host.mem_writes, [])
        self.assertEqual(host.writes, [])
        self.assertIn("--cleanup-only is the recovery step", err.getvalue())
        self.assertNotIn("--restore-data restores", err.getvalue())

    def test_map_count_fails_closed(self):
        payload = good_payload()
        files = good_files(payload, extra=param_files(map_count=1))
        host = FakeHost(files=files)
        err = io.StringIO()
        with redirect_stderr(err):
            with self.assertRaisesRegex(rp.ProbeError, "not writing /dev/mem"):
                rp.run_probe(load_args(True), host=host, cfg=fw_cfg(payload))
        self.assertEqual(host.mem_writes, [])
        self.assertEqual(host.writes, [])
        self.assertIn("--cleanup-only is the recovery step", err.getvalue())
        self.assertNotIn("--restore-data restores", err.getvalue())

    def test_all_zero_params_succeed(self):
        payload = good_payload()
        files = good_files(payload, extra=map_counter_params())
        host = FakeHost(files=files)
        err = io.StringIO()
        out = io.StringIO()
        with redirect_stderr(err), redirect_stdout(out):
            msg = rp.run_probe(load_args(True), host=host, cfg=fw_cfg(payload))
        self.assertEqual(msg, "map-check: loaded")
        self.assertEqual(rp.LOOKUP_COUNT, 0x329 + 0x1d2 + 5 + 7)
        self.assertEqual(rp.READBACK_COUNT, 6)
        self.assertEqual(rp.PTE_WALK_COUNT, 12)
        printed = out.getvalue()
        self.assertIn("lookup_count=%d" % rp.LOOKUP_COUNT, printed)
        self.assertIn("readback_count=%d" % rp.READBACK_COUNT, printed)
        self.assertIn("pte_walk_count=%d" % rp.PTE_WALK_COUNT, printed)
        self.assertEqual(host.mem_writes, [])
        self.assertEqual(host.writes, [])
        self.assertNotIn("--cleanup-only is the recovery step", err.getvalue())

    def test_published_prot_values_succeed(self):
        payload = good_payload()
        files = good_files(
            payload,
            extra=map_counter_params(
                text_prot=5, data_prot=7, heap_prot=7, ipc_prot=7
            ),
        )
        host = FakeHost(files=files)
        msg = rp.run_probe(load_args(True), host=host, cfg=fw_cfg(payload))
        self.assertEqual(msg, "map-check: loaded")

    def test_text_prot_write_bit_fails(self):
        payload = good_payload()
        files = good_files(payload, extra=param_files(text_prot=2))
        host = FakeHost(files=files)
        with self.assertRaisesRegex(rp.ProbeError, "text_prot has WRITE"):
            rp.run_probe(load_args(True), host=host, cfg=fw_cfg(payload))
        self.assertEqual(host.mem_writes, [])

    def test_rw_prot_without_write_bit_fails(self):
        payload = good_payload()
        files = good_files(
            payload,
            extra=param_files(text_prot=5, data_prot=5, heap_prot=7, ipc_prot=7),
        )
        host = FakeHost(files=files)
        with self.assertRaisesRegex(rp.ProbeError, "data_prot lacks WRITE"):
            rp.run_probe(load_args(True), host=host, cfg=fw_cfg(payload))

    def test_cleanup_pending_names_recovery_without_writing_it(self):
        payload = good_payload()
        files = good_files(payload, extra=param_files(cleanup_pending=1))
        host = FakeHost(files=files)
        err = io.StringIO()
        with redirect_stderr(err):
            with self.assertRaisesRegex(rp.ProbeError, "module parameters are not clean"):
                rp.run_probe(load_args(True), host=host, cfg=fw_cfg(payload))
        self.assertIn("--cleanup-only is the recovery step", err.getvalue())
        self.assertEqual(host.writes, [])
        self.assertEqual(host.mem_writes, [])
        self.assertEqual(len(host.hash_calls), 3)

    def test_both_hashes_changed_restores_data_only(self):
        payload = good_payload()
        host = FakeHost(
            files=good_files(payload),
            phys_after={
                (TEXT_PHYS, len(TEXT_IMAGE)): b"text-imagX",
                (DATA_PHYS, len(DATA_IMAGE)): b"data-imagX",
            },
        )
        err = io.StringIO()
        with redirect_stderr(err):
            with self.assertRaisesRegex(rp.ProbeError, "^DATA restored$") as caught:
                rp.run_probe(load_args(True), host=host, cfg=fw_cfg(payload))
        self.assertEqual(caught.exception.code, rp.EXIT_LADDER)
        self.assertIn("TEXT hash changed", err.getvalue())
        self.assertEqual(len(host.mem_writes), 1)
        self.assertEqual(host.mem_writes[0][0], DATA_PHYS)
        self.assertNotEqual(host.mem_writes[0][0], TEXT_PHYS)

    def test_main_restore_exit_code(self):
        payload = good_payload()
        host = FakeHost(
            files=good_files(payload),
            phys_after={(DATA_PHYS, len(DATA_IMAGE)): b"data-imagX"},
        )
        err = io.StringIO()
        with redirect_stderr(err):
            rc = rp.main(
                [
                    "--map-check",
                    "--module",
                    "/tmp/j713_isp_fwview.ko",
                    "--sha256",
                    MODULE_SHA,
                    "--data-ref",
                    DATA_REF,
                ],
                host=host,
                cfg=fw_cfg(payload),
            )
        self.assertEqual(rc, 2)
        self.assertIn("DATA restored", err.getvalue())

    def test_main_restore_failed_exit_code(self):
        payload = good_payload()
        host = FakeHost(
            files=good_files(payload),
            phys_after={(DATA_PHYS, len(DATA_IMAGE)): b"data-imagX"},
            restore_sticks=False,
        )
        err = io.StringIO()
        with redirect_stderr(err):
            rc = rp.main(
                [
                    "--map-check",
                    "--module",
                    "/tmp/j713_isp_fwview.ko",
                    "--sha256",
                    MODULE_SHA,
                    "--data-ref",
                    DATA_REF,
                ],
                host=host,
                cfg=fw_cfg(payload),
            )
        self.assertEqual(rc, 2)
        self.assertIn("DATA restore failed", err.getvalue())

    def test_restore_oserror_after_write_rehash_matches(self):
        payload = good_payload()
        host = FakeHost(
            files=good_files(payload),
            phys_after={(DATA_PHYS, len(DATA_IMAGE)): b"data-imagX"},
            write_raises=True,
        )
        with self.assertRaisesRegex(rp.ProbeError, "^DATA restored$") as caught:
            rp.run_probe(load_args(True), host=host, cfg=fw_cfg(payload))
        self.assertEqual(caught.exception.code, rp.EXIT_LADDER)
        self.assertEqual(host.mem_writes, [
            (DATA_PHYS, len(DATA_IMAGE), DATA_IMAGE, rp.DEV_MEM_PATH),
        ])
        self.assertEqual(len(host.hash_calls), 4)

    def test_restore_oserror_after_write_rehash_differs(self):
        payload = good_payload()
        host = FakeHost(
            files=good_files(payload),
            phys_after={(DATA_PHYS, len(DATA_IMAGE)): b"data-imagX"},
            restore_sticks=False,
            write_raises=True,
        )
        with self.assertRaisesRegex(rp.ProbeError, "^DATA restore failed$") as caught:
            rp.run_probe(load_args(True), host=host, cfg=fw_cfg(payload))
        self.assertEqual(caught.exception.code, rp.EXIT_LADDER)
        self.assertEqual(host.mem_writes, [
            (DATA_PHYS, len(DATA_IMAGE), DATA_IMAGE, rp.DEV_MEM_PATH),
        ])
        self.assertEqual(len(host.hash_calls), 4)

    def test_restore_oserror_before_map_is_failed(self):
        payload = good_payload()
        host = FakeHost(
            files=good_files(payload),
            phys_after={(DATA_PHYS, len(DATA_IMAGE)): b"data-imagX"},
            write_open_fails=True,
        )
        with self.assertRaisesRegex(rp.ProbeError, "^DATA restore failed$") as caught:
            rp.run_probe(load_args(True), host=host, cfg=fw_cfg(payload))
        self.assertEqual(caught.exception.code, rp.EXIT_LADDER)
        self.assertEqual(host.mem_writes, [])
        self.assertEqual(len(host.hash_calls), 3)

    def _expect_param_read_failure(self, host, message):
        payload = good_payload()
        err = io.StringIO()
        with redirect_stderr(err):
            with self.assertRaisesRegex(rp.ProbeError, message) as caught:
                rp.run_probe(load_args(True), host=host, cfg=fw_cfg(payload))
        self.assertEqual(caught.exception.code, 1)
        self.assertEqual(len(host.subprocess_calls), 1)
        self.assertEqual(host.mem_writes, [])
        self.assertEqual(host.writes, [])
        self.assertEqual(len(host.hash_calls), 3)
        text = err.getvalue()
        self.assertIn("TEXT hash unchanged", text)
        self.assertIn("DATA hash changed", text)
        self.assertIn("--cleanup-only is the recovery step", text)
        self.assertIn("--restore-data restores DATA from the reference", text)
        return text

    def test_missing_param_still_hashes_without_writing(self):
        payload = good_payload()
        extra = param_files()
        del extra[rp.PARAM_DIR + "/map_count"]
        host = FakeHost(
            files=good_files(payload, extra=extra),
            phys_after={(DATA_PHYS, len(DATA_IMAGE)): b"data-imagX"},
        )
        self._expect_param_read_failure(host, "module param missing map_count")

    def test_unparsable_param_still_hashes_without_writing(self):
        payload = good_payload()
        extra = param_files()
        extra[rp.PARAM_DIR + "/result"] = "bad\n"
        host = FakeHost(
            files=good_files(payload, extra=extra),
            phys_after={(DATA_PHYS, len(DATA_IMAGE)): b"data-imagX"},
        )
        self._expect_param_read_failure(host, "module param result")

    def test_unreadable_params_still_hash_without_writing(self):
        payload = good_payload()
        host = FakeHost(
            files=good_files(payload, extra=param_files()),
            phys_after={(DATA_PHYS, len(DATA_IMAGE)): b"data-imagX"},
            unreadable_params=True,
        )
        err = io.StringIO()
        with redirect_stderr(err):
            rc = rp.main(
                [
                    "--map-check",
                    "--module",
                    "/tmp/j713_isp_fwview.ko",
                    "--sha256",
                    MODULE_SHA,
                    "--data-ref",
                    DATA_REF,
                ],
                host=host,
                cfg=fw_cfg(payload),
            )
        self.assertEqual(rc, 1)
        self.assertEqual(len(host.subprocess_calls), 1)
        self.assertEqual(host.mem_writes, [])
        self.assertEqual(host.writes, [])
        self.assertEqual(len(host.hash_calls), 3)
        text = err.getvalue()
        self.assertIn("TEXT hash unchanged", text)
        self.assertIn("DATA hash changed", text)
        self.assertIn("--cleanup-only is the recovery step", text)
        self.assertIn("--restore-data restores DATA from the reference", text)
        self.assertIn("module parameters unreadable", text)
        self.assertNotIn("io error:", text)

    def _map_check_main(self, host, cfg):
        err = io.StringIO()
        with redirect_stderr(err):
            rc = rp.main(
                [
                    "--map-check",
                    "--module",
                    "/tmp/j713_isp_fwview.ko",
                    "--sha256",
                    MODULE_SHA,
                    "--data-ref",
                    DATA_REF,
                ],
                host=host,
                cfg=cfg,
            )
        return rc, err.getvalue()

    def test_missing_result_while_module_listed_does_not_write(self):
        payload = good_payload()
        modules_path = "/proc/modules.fake"
        cfg = dataclasses.replace(fw_cfg(payload), modules_path=modules_path)
        files = good_files(payload)
        files[rp.MODULES_PATH] = "cfg80211 123 0 - Live 0x0\n"
        files[modules_path] = "cfg80211 123 0 - Live 0x0\n"
        self.assertNotIn(rp.PARAM_DIR + "/result", files)
        self.assertNotIn(cfg.module_name, files[modules_path])
        self.assertNotIn(cfg.module_name, files[rp.MODULES_PATH])
        host = FakeHost(
            files=files,
            second_text={
                modules_path: "j713_isp_fwview 4096 0 - Live 0x0\n",
            },
            phys_after={(DATA_PHYS, len(DATA_IMAGE)): b"data-imagX"},
        )
        rc, text = self._map_check_main(host, cfg)
        self.assertEqual(rc, 1)
        self.assertEqual(len(host.subprocess_calls), 1)
        self.assertEqual(host.mem_writes, [])
        self.assertEqual(host.writes, [])
        self.assertEqual(len(host.hash_calls), 3)
        self.assertIn("TEXT hash unchanged", text)
        self.assertIn("DATA hash changed", text)
        self.assertIn("--cleanup-only is the recovery step", text)
        self.assertIn("--restore-data restores DATA from the reference", text)
        self.assertIn("module parameters unknown", text)

    def test_missing_result_and_module_absent_restores_changed_data(self):
        payload = good_payload()
        modules_path = "/proc/modules.fake"
        cfg = dataclasses.replace(fw_cfg(payload), modules_path=modules_path)
        files = good_files(payload)
        files[rp.MODULES_PATH] = "j713_isp_fwview 4096 0 - Live 0x0\n"
        files[modules_path] = "cfg80211 123 0 - Live 0x0\n"
        self.assertNotIn(rp.PARAM_DIR + "/result", files)
        self.assertNotIn(cfg.module_name, files[modules_path])
        self.assertIn(cfg.module_name, files[rp.MODULES_PATH])
        host = FakeHost(
            files=files,
            phys_after={(DATA_PHYS, len(DATA_IMAGE)): b"data-imagX"},
        )
        rc, text = self._map_check_main(host, cfg)
        self.assertEqual(rc, rp.EXIT_LADDER)
        self.assertIn("DATA restored", text)
        self.assertEqual(host.mem_writes, [
            (DATA_PHYS, len(DATA_IMAGE), DATA_IMAGE, rp.DEV_MEM_PATH),
        ])
        self.assertEqual(host.writes, [])

    def test_map_check_requires_data_ref(self):
        err = io.StringIO()
        with redirect_stderr(err):
            with self.assertRaises(SystemExit) as caught:
                rp.parse_args([
                    "--map-check",
                    "--module",
                    "/tmp/j713_isp_fwview.ko",
                    "--sha256",
                    MODULE_SHA,
                ])
        self.assertNotEqual(caught.exception.code, 0)
        self.assertIn("--data-ref is required", err.getvalue())

    def test_map_counter_mismatch_fails(self):
        payload = good_payload()
        expected = {
            "lookup_count": rp.LOOKUP_COUNT,
            "readback_count": rp.READBACK_COUNT,
            "pte_walk_count": rp.PTE_WALK_COUNT,
        }
        for name, good in expected.items():
            bad = dict(expected)
            bad[name] = good + 1
            host = FakeHost(files=good_files(payload, extra=param_files(**bad)))
            with self.assertRaisesRegex(rp.ProbeError, name + " mismatch"):
                rp.run_probe(load_args(True), host=host, cfg=fw_cfg(payload))
            self.assertEqual(host.mem_writes, [])
            self.assertEqual(len(host.subprocess_calls), 1)

    def test_oserror_after_insmod_is_unknown_state(self):
        payload = good_payload()
        changed = {(DATA_PHYS, len(DATA_IMAGE)): b"data-imagX"}
        cases = (
            {"close_raises": True},
            {"modules_raises": True},
        )
        for flags in cases:
            host = FakeHost(
                files=good_files(payload),
                phys_after=changed,
                **flags,
            )
            err = io.StringIO()
            out = io.StringIO()
            with redirect_stderr(err), redirect_stdout(out):
                rc = rp.main(
                    [
                        "--map-check",
                        "--module",
                        "/tmp/j713_isp_fwview.ko",
                        "--sha256",
                        MODULE_SHA,
                        "--data-ref",
                        DATA_REF,
                    ],
                    host=host,
                    cfg=fw_cfg(payload),
                )
            text = err.getvalue()
            self.assertNotEqual(rc, 0)
            self.assertEqual(len(host.subprocess_calls), 1)
            self.assertEqual(host.mem_writes, [])
            self.assertEqual(host.writes, [])
            self.assertEqual(len(host.hash_calls), 3)
            self.assertNotIn(TEXT_PHYS, [item[0] for item in host.mem_writes])
            self.assertIn("TEXT hash unchanged", text)
            self.assertIn("DATA hash changed", text)
            self.assertIn("--cleanup-only is the recovery step", text)
            self.assertIn("--restore-data restores DATA from the reference", text)
            self.assertIn("module state unknown", text)
            self.assertNotIn("io error:", text)
            self.assertNotIn("DATA restored", out.getvalue())

    def test_overlapping_map_check_does_not_write(self):
        payload = good_payload()
        data_phys = TEXT_PHYS + 4
        cfg = dataclasses.replace(fw_cfg(payload), data_phys=data_phys)
        host = FakeHost(
            files=good_files(payload),
            phys={
                (TEXT_PHYS, len(TEXT_IMAGE)): TEXT_IMAGE,
                (data_phys, len(DATA_IMAGE)): DATA_IMAGE,
            },
            phys_after={(data_phys, len(DATA_IMAGE)): b"data-imagX"},
        )
        with self.assertRaisesRegex(rp.ProbeError, "^DATA restore failed$") as caught:
            rp.run_probe(load_args(True), host=host, cfg=cfg)
        self.assertEqual(caught.exception.code, rp.EXIT_LADDER)
        self.assertEqual(host.mem_writes, [])
        self.assertEqual(len(host.subprocess_calls), 1)

    def test_overflowing_data_range_does_not_write(self):
        payload = good_payload()
        data_phys = rp.U64_MAX - 3
        cfg = dataclasses.replace(fw_cfg(payload), data_phys=data_phys)
        host = FakeHost(
            files=good_files(payload),
            phys={
                (TEXT_PHYS, len(TEXT_IMAGE)): TEXT_IMAGE,
                (data_phys, len(DATA_IMAGE)): DATA_IMAGE,
            },
            phys_after={(data_phys, len(DATA_IMAGE)): b"data-imagX"},
        )
        with self.assertRaisesRegex(rp.ProbeError, "^DATA restore failed$") as caught:
            rp.run_probe(load_args(True), host=host, cfg=cfg)
        self.assertEqual(caught.exception.code, rp.EXIT_LADDER)
        self.assertEqual(host.mem_writes, [])

    def test_short_data_blob_is_not_written(self):
        payload = good_payload()
        host = FakeHost(files=good_files(payload))
        with self.assertRaisesRegex(rp.ProbeError, "^DATA restore failed$") as caught:
            rp._rewrite_data(host, fw_cfg(payload), b"x", "0" * 64)
        self.assertEqual(caught.exception.code, rp.EXIT_LADDER)
        self.assertEqual(host.mem_writes, [])

    def test_restore_data_requires_data_ref(self):
        err = io.StringIO()
        with redirect_stderr(err):
            with self.assertRaises(SystemExit) as caught:
                rp.parse_args(["--restore-data"])
        self.assertNotEqual(caught.exception.code, 0)
        self.assertIn("--data-ref is required for --restore-data", err.getvalue())

    def test_restore_data_already_matches(self):
        payload = good_payload()
        host = FakeHost(
            files=good_files(payload),
            ref_files={DATA_REF: DATA_IMAGE},
        )
        rc, out, err = run_restore(host, fw_cfg(payload))
        self.assertEqual(rc, 0)
        self.assertIn("DATA already matches", out)
        self.assertEqual(err, "")
        self.assertEqual(host.subprocess_calls, [])
        self.assertEqual(host.mem_writes, [])
        self.assertEqual(host.writes, [])
        self.assertEqual(host.hash_calls, [
            (DATA_PHYS, len(DATA_IMAGE), rp.DEV_MEM_PATH),
        ])
        self.assertEqual(host.boot_reads, 2)

    def test_restore_data_writes_data_only(self):
        payload = good_payload()
        host = FakeHost(
            files=good_files(payload),
            ref_files={DATA_REF: DATA_IMAGE},
            phys={(DATA_PHYS, len(DATA_IMAGE)): b"data-imagX",
                  (TEXT_PHYS, len(TEXT_IMAGE)): TEXT_IMAGE},
        )
        rc, out, err = run_restore(host, fw_cfg(payload))
        self.assertEqual(rc, 0)
        self.assertIn("DATA restored", out)
        self.assertNotIn("DATA restored", err)
        self.assertEqual(host.subprocess_calls, [])
        self.assertEqual(host.mem_writes, [
            (DATA_PHYS, len(DATA_IMAGE), DATA_IMAGE, rp.DEV_MEM_PATH),
        ])
        self.assertEqual(len(host.hash_calls), 2)
        self.assertTrue(all(item[0] == DATA_PHYS for item in host.hash_calls))

    def test_restore_data_before_text_is_disjoint(self):
        payload = good_payload()
        text_phys = 0x2000
        data_phys = 0x1000
        cfg = dataclasses.replace(
            fw_cfg(payload), text_phys=text_phys, data_phys=data_phys
        )
        host = FakeHost(
            files=good_files(payload),
            ref_files={DATA_REF: DATA_IMAGE},
            phys={
                (text_phys, len(TEXT_IMAGE)): TEXT_IMAGE,
                (data_phys, len(DATA_IMAGE)): b"data-imagX",
            },
        )
        rc, out, _err = run_restore(host, cfg)
        self.assertEqual(rc, 0)
        self.assertIn("DATA restored", out)
        self.assertEqual(host.mem_writes, [
            (data_phys, len(DATA_IMAGE), DATA_IMAGE, rp.DEV_MEM_PATH),
        ])

    def test_restore_data_failed_rehash(self):
        payload = good_payload()
        host = FakeHost(
            files=good_files(payload),
            ref_files={DATA_REF: DATA_IMAGE},
            phys={(DATA_PHYS, len(DATA_IMAGE)): b"data-imagX",
                  (TEXT_PHYS, len(TEXT_IMAGE)): TEXT_IMAGE},
            restore_sticks=False,
        )
        rc, out, err = run_restore(host, fw_cfg(payload))
        self.assertEqual(rc, rp.EXIT_LADDER)
        self.assertIn("DATA restore failed", err)
        self.assertNotIn("DATA restored", out)
        self.assertEqual(host.mem_writes, [
            (DATA_PHYS, len(DATA_IMAGE), DATA_IMAGE, rp.DEV_MEM_PATH),
        ])
        self.assertTrue(all(item[0] != TEXT_PHYS for item in host.mem_writes))

    def test_restore_data_wrong_hash_refuses(self):
        payload = good_payload()
        host = FakeHost(
            files=good_files(payload),
            ref_files={DATA_REF: b"data-imagX"},
        )
        with self.assertRaisesRegex(rp.ProbeError, "DATA reference hash"):
            rp.run_probe(rp.parse_args(restore_argv()), host=host, cfg=fw_cfg(payload))
        self.assertEqual(host.hash_calls, [])
        self.assertEqual(host.mem_writes, [])
        self.assertEqual(host.subprocess_calls, [])

    def test_restore_data_refuses_bad_reference(self):
        payload = good_payload()
        cases = (
            ({"ref_files": {DATA_REF: DATA_IMAGE}, "ref_mode": {DATA_REF: 0o644}},
             "DATA reference mode"),
            ({"ref_files": {DATA_REF: DATA_IMAGE}, "ref_regular": {DATA_REF: False}},
             "not a regular file"),
            ({"ref_files": {DATA_REF: b"short"}},
             "DATA reference size"),
            ({}, "DATA reference missing"),
        )
        for host_kw, pattern in cases:
            host = FakeHost(files=good_files(payload), **host_kw)
            with self.assertRaisesRegex(rp.ProbeError, pattern):
                rp.run_probe(
                    rp.parse_args(restore_argv()), host=host, cfg=fw_cfg(payload)
                )
            self.assertEqual(host.mem_writes, [])
            self.assertEqual(host.hash_calls, [])
            self.assertEqual(host.subprocess_calls, [])

    def test_restore_data_refuses_when_not_idle(self):
        payload = good_payload()
        for name in ("map_count", "retain", "cleanup_pending"):
            files = good_files(
                payload,
                extra={rp.MODULES_PATH: loaded_module_line()},
            )
            files.update(idle_param_files(**{name: 1}))
            host = FakeHost(
                files=files,
                ref_files={DATA_REF: b"data-imagX"},
            )
            with self.assertRaisesRegex(rp.ProbeError, "not writing /dev/mem"):
                rp.run_probe(
                    rp.parse_args(restore_argv()), host=host, cfg=fw_cfg(payload)
                )
            self.assertEqual(host.mem_writes, [])
            self.assertEqual(host.hash_calls, [])
            self.assertEqual(host.reference_reads, [])
            self.assertEqual(host.subprocess_calls, [])

    def test_restore_data_unreadable_params_while_loaded(self):
        payload = good_payload()
        files = good_files(payload, extra={rp.MODULES_PATH: loaded_module_line()})
        host = FakeHost(
            files=files,
            ref_files={DATA_REF: DATA_IMAGE},
            unreadable_params=True,
        )
        with self.assertRaisesRegex(rp.ProbeError, "module parameters unreadable"):
            rp.run_probe(rp.parse_args(restore_argv()), host=host, cfg=fw_cfg(payload))
        self.assertEqual(host.mem_writes, [])
        self.assertEqual(host.hash_calls, [])
        self.assertEqual(host.reference_reads, [])

    def test_restore_data_idle_module_may_write(self):
        payload = good_payload()
        files = good_files(payload, extra={rp.MODULES_PATH: loaded_module_line()})
        files.update(idle_param_files())
        host = FakeHost(
            files=files,
            ref_files={DATA_REF: DATA_IMAGE},
            phys={(DATA_PHYS, len(DATA_IMAGE)): b"data-imagX",
                  (TEXT_PHYS, len(TEXT_IMAGE)): TEXT_IMAGE},
        )
        rc, out, _err = run_restore(host, fw_cfg(payload))
        self.assertEqual(rc, 0)
        self.assertIn("DATA restored", out)
        self.assertEqual(host.subprocess_calls, [])
        self.assertEqual(host.mem_writes, [
            (DATA_PHYS, len(DATA_IMAGE), DATA_IMAGE, rp.DEV_MEM_PATH),
        ])

    def test_restore_data_absent_module_ignores_sysfs(self):
        payload = good_payload()
        host = FakeHost(
            files=good_files(payload),
            ref_files={DATA_REF: DATA_IMAGE},
            unreadable_params=True,
        )
        rc, out, _err = run_restore(host, fw_cfg(payload))
        self.assertEqual(rc, 0)
        self.assertIn("DATA already matches", out)
        self.assertEqual(host.mem_writes, [])

    def test_restore_data_identity_guards_do_not_write(self):
        payload = good_payload()
        base = good_files(payload)
        cases = []
        bad_boot = dict(base)
        bad_boot[rp.BOOT_ID_PATH] = "deadbeef-0977-4eb0-93a9-e60359bec201\n"
        cases.append((bad_boot, {}, fw_cfg(payload), "boot UUID"))
        cases.append((base, {"release": "7.1.9-generic"}, fw_cfg(payload), "kernel release"))
        cases.append((base, {"euid": 1000}, fw_cfg(payload), "euid"))
        bad_compat = dict(base)
        bad_compat[rp.COMPAT_PATH] = b"apple,j314\0apple,t6000\0"
        cases.append((bad_compat, {}, fw_cfg(payload), "compatible"))
        bad_reg = dict(base)
        bad_reg[rp.FLASHREG_PATH] = struct.pack(">QQ", ADT_PHYS, 0x1000)
        cases.append((bad_reg, {}, fw_cfg(payload), "flashreg"))
        cfg = dataclasses.replace(fw_cfg(payload), adt_sha256="0" * 64)
        cases.append((base, {}, cfg, "ADT sha256"))
        for files, host_kw, probe_cfg, pattern in cases:
            host = FakeHost(
                files=files,
                ref_files={DATA_REF: DATA_IMAGE},
                **host_kw,
            )
            with self.assertRaisesRegex(rp.ProbeError, pattern):
                rp.run_probe(rp.parse_args(restore_argv()), host=host, cfg=probe_cfg)
            self.assertEqual(host.mem_writes, [])
            self.assertEqual(host.hash_calls, [])
            self.assertEqual(host.subprocess_calls, [])
            self.assertEqual(host.reference_reads, [])

    def test_restore_data_boot_recheck_does_not_write(self):
        payload = good_payload()
        host = FakeHost(
            files=good_files(payload),
            ref_files={DATA_REF: DATA_IMAGE},
            boot_id_second="11111111-1111-1111-1111-111111111111\n",
        )
        with self.assertRaisesRegex(rp.ProbeError, "boot UUID"):
            rp.run_probe(rp.parse_args(restore_argv()), host=host, cfg=fw_cfg(payload))
        self.assertEqual(host.boot_reads, 2)
        self.assertEqual(host.mem_writes, [])
        self.assertEqual(host.hash_calls, [])
        self.assertEqual(len(host.reference_reads), 1)

    def test_restore_data_overlap_does_not_write(self):
        payload = good_payload()
        data_phys = TEXT_PHYS + 4
        cfg = dataclasses.replace(fw_cfg(payload), data_phys=data_phys)
        host = FakeHost(
            files=good_files(payload),
            ref_files={DATA_REF: DATA_IMAGE},
            phys={
                (TEXT_PHYS, len(TEXT_IMAGE)): TEXT_IMAGE,
                (data_phys, len(DATA_IMAGE)): b"data-imagX",
            },
        )
        rc, _out, err = run_restore(host, cfg)
        self.assertEqual(rc, rp.EXIT_LADDER)
        self.assertIn("DATA restore failed", err)
        self.assertEqual(host.mem_writes, [])
        self.assertEqual(host.subprocess_calls, [])
        self.assertTrue(all(item[0] == data_phys for item in host.hash_calls))

    def test_dart_survey_parses_without_data_ref(self):
        args = survey_args()
        self.assertTrue(args.dart_survey)
        self.assertFalse(args.map_check)
        self.assertFalse(args.validate)
        self.assertFalse(args.check_only)
        self.assertIsNone(args.data_ref)

    def test_dart_survey_requires_module_and_hash(self):
        err = io.StringIO()
        with redirect_stderr(err):
            with self.assertRaises(SystemExit) as caught:
                rp.parse_args(["--dart-survey"])
        self.assertNotEqual(caught.exception.code, 0)
        self.assertIn("--module and --sha256", err.getvalue())

    def test_dart_survey_loads_without_writing(self):
        payload = good_payload()
        cfg = dataclasses.replace(
            fw_cfg(payload),
            text_sha256="ab" * 32,
            data_sha256="cd" * 32,
        )
        host = FakeHost(files=good_files(payload, extra=param_files()))
        err = io.StringIO()
        out = io.StringIO()
        with redirect_stderr(err), redirect_stdout(out):
            msg = rp.run_probe(survey_args(), host=host, cfg=cfg)
        self.assertEqual(msg, "dart-survey: loaded")
        self.assertEqual(
            host.subprocess_calls,
            [(
                ["insmod", "/proc/self/fd/7", "map_check=true", "dart_survey=true"],
                (7,),
            )],
        )
        self.assertEqual(host.hash_calls, [
            (TEXT_PHYS, len(TEXT_IMAGE), rp.DEV_MEM_PATH),
            (DATA_PHYS, len(DATA_IMAGE), rp.DEV_MEM_PATH),
            (TEXT_PHYS, len(TEXT_IMAGE), rp.DEV_MEM_PATH),
            (DATA_PHYS, len(DATA_IMAGE), rp.DEV_MEM_PATH),
        ])
        self.assertEqual(host.phys_reads, [])
        self.assertEqual(host.reference_events, [])
        self.assertEqual(host.mem_writes, [])
        self.assertEqual(host.writes, [])
        self.assertIn("result=0", out.getvalue())
        self.assertIn("TEXT hash unchanged", err.getvalue())
        self.assertIn("DATA hash unchanged", err.getvalue())
        self.assertNotIn("TEXT hash changed", err.getvalue())
        self.assertNotIn("DATA hash changed", err.getvalue())

    def test_dart_survey_clean_params_reject_nonzero(self):
        payload = good_payload()
        for name in rp.CLEAN_NAMES:
            host = FakeHost(
                files=good_files(payload, extra=param_files(**{name: 1})),
            )
            with self.assertRaisesRegex(rp.ProbeError, "module parameters are not clean"):
                rp.run_probe(survey_args(), host=host, cfg=fw_cfg(payload))
            self.assertEqual(host.mem_writes, [])
            self.assertEqual(host.writes, [])
            self.assertEqual(host.phys_reads, [])
            self.assertEqual(host.reference_events, [])
            self.assertEqual(len(host.subprocess_calls), 1)

    def test_dart_survey_counters_must_be_zero(self):
        payload = good_payload()
        for name in ("lookup_count", "readback_count", "pte_walk_count"):
            host = FakeHost(
                files=good_files(payload, extra=param_files(**{name: 1})),
            )
            with self.assertRaisesRegex(rp.ProbeError, name + " is not 0"):
                rp.run_probe(survey_args(), host=host, cfg=fw_cfg(payload))
            self.assertEqual(host.mem_writes, [])
            self.assertEqual(host.writes, [])
            self.assertEqual(len(host.subprocess_calls), 1)
            self.assertEqual(len(host.hash_calls), 4)

    def test_dart_survey_text_hash_changed_does_not_write(self):
        payload = good_payload()
        host = FakeHost(
            files=good_files(payload, extra=param_files()),
            phys_after={(TEXT_PHYS, len(TEXT_IMAGE)): b"text-imagX"},
        )
        with self.assertRaisesRegex(rp.ProbeError, "TEXT hash changed"):
            rp.run_probe(survey_args(), host=host, cfg=fw_cfg(payload))
        self.assertEqual(len(host.subprocess_calls), 1)
        self.assertEqual(len(host.hash_calls), 4)
        self.assertEqual(host.mem_writes, [])
        self.assertEqual(host.writes, [])
        self.assertEqual(host.phys_reads, [])
        self.assertEqual(host.reference_events, [])

    def test_dart_survey_data_hash_changed_does_not_write(self):
        payload = good_payload()
        host = FakeHost(
            files=good_files(payload),
            phys_after={(DATA_PHYS, len(DATA_IMAGE)): b"data-imagX"},
        )
        with self.assertRaisesRegex(rp.ProbeError, "DATA hash changed"):
            rp.run_probe(survey_args(), host=host, cfg=fw_cfg(payload))
        self.assertEqual(len(host.hash_calls), 4)
        self.assertEqual(host.mem_writes, [])
        self.assertEqual(host.writes, [])
        self.assertEqual(host.reference_events, [])

    def test_dart_survey_hash_change_wins_over_insmod_failure(self):
        payload = good_payload()
        host = FakeHost(
            files=good_files(payload),
            insmod_result=FakeProc(returncode=1, stderr="insmod: ERROR: foo"),
            phys_after={(TEXT_PHYS, len(TEXT_IMAGE)): b"text-imagX"},
        )
        with self.assertRaisesRegex(rp.ProbeError, "TEXT hash changed"):
            rp.run_probe(survey_args(), host=host, cfg=fw_cfg(payload))
        self.assertEqual(len(host.subprocess_calls), 1)
        self.assertEqual(len(host.hash_calls), 4)
        self.assertEqual(host.mem_writes, [])
        self.assertEqual(host.writes, [])

    def test_dart_survey_wrong_boot_does_not_hash(self):
        payload = good_payload()
        files = good_files(payload)
        files[rp.BOOT_ID_PATH] = "deadbeef-0977-4eb0-93a9-e60359bec201\n"
        host = FakeHost(files=files)
        with self.assertRaisesRegex(rp.ProbeError, "boot UUID"):
            rp.run_probe(survey_args(), host=host, cfg=fw_cfg(payload))
        self.assertEqual(host.hash_calls, [])
        self.assertEqual(host.subprocess_calls, [])
        self.assertEqual(host.opened, [])
        self.assertEqual(host.mem_writes, [])

    def test_dart_survey_sha_mismatch_hashes_both_before_insmod(self):
        payload = good_payload()
        host = FakeHost(files=good_files(payload))
        args = survey_args()
        args.sha256 = "0" * 64
        with self.assertRaisesRegex(rp.ProbeError, "module sha256 mismatch"):
            rp.run_probe(args, host=host, cfg=fw_cfg(payload))
        self.assertEqual(host.subprocess_calls, [])
        self.assertEqual(host.hash_calls, [
            (TEXT_PHYS, len(TEXT_IMAGE), rp.DEV_MEM_PATH),
            (DATA_PHYS, len(DATA_IMAGE), rp.DEV_MEM_PATH),
        ])
        self.assertEqual(host.mem_writes, [])
        self.assertEqual(host.phys_reads, [])
        self.assertEqual(host.reference_events, [])


if __name__ == "__main__":
    unittest.main()
