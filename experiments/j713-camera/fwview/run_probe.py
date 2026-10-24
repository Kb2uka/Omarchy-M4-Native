#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Private one-shot J713 ISP firmware-view launcher.

Default is --check-only. --validate loads the module with map_check=false
(no power, overlay, or domain). --map-check is the only launcher path that
writes /dev/mem. Before insmod it reads DATA through a read-only
/dev/mem map, checks the qualified DATA hash, and saves that byte copy to
the --data-ref file mode 0600. It refuses if that file already exists and
differs. Parameter reads, the unknown-state gate, firmware result
printing, and DATA restore run only after insmod was attempted. A refusal
before insmod, including an already-loaded module or a failed identity or
hash check, propagates unchanged and does not write /dev/mem. After insmod
it re-hashes TEXT and DATA. If the module is still
loaded with map_count or retain nonzero, it does not write /dev/mem. If
reading the module parameters raises (a file is missing, unreadable, or
unparsable), or any OSError is raised after insmod was attempted (the
module fd close, the modules list, or a sysfs read), the mapped state
is unknown: it does not write /dev/mem, it still hashes TEXT and DATA
and prints both results, it prints that --cleanup-only is the recovery
step and that --restore-data restores DATA from the reference, and it
exits nonzero. A missing result file is that same unknown state when
the module name is still listed in the configured modules file. The module counts as absent, and
a DATA restore is allowed, only when that name is also absent. If DATA
changed and the module is known to be unmapped, it writes the saved DATA
bytes back through a shared /dev/mem map of exactly that range, without
msync, and exits 2. The re-hash decides: "DATA restored" when it matches,
"DATA restore failed" when it does not. An OSError from the write still
re-hashes when the map was opened. It never writes TEXT and never writes
any other range. Exit 0 is a clean load. Exit 1 is any other failure.
After insmod, published result, first_error, retain, cleanup_pending,
map_count, and ladder_stop must all be 0 or the run fails. text_prot
must not have the write bit. A nonzero DATA, heap, or IPC prot must have
it. The launcher does not write cleanup_only. When retain,
cleanup_pending, or map_count is set, --cleanup-only is the recovery
step. --cleanup-only writes the root sysfs recovery trigger on an
already-loaded module and does not insmod, remap, or retry.
There is no identity-only --apply and no --inspect. Load modes require
--module and --sha256 of that opened file; the hash is supplied by the
caller and is not hardcoded here. --map-check also requires --data-ref.
--restore-data is mutually exclusive with the other modes and requires
--data-ref. It checks the same boot, kernel, and target identity. It
refuses unless the module is absent from the modules list, or present
with readable map_count, retain, and cleanup_pending all 0. It reads
the reference (regular file, mode 0600, exact DATA size, looping until
the full size or EOF) and requires its sha256 to equal the qualified
DATA hash, then hashes current DATA. A match prints "DATA already
matches" and exits 0. Otherwise it writes that reference through the
exact-range path, re-hashes, and prints "DATA restored" (exit 0) or
"DATA restore failed" (exit 2). It never insmods and never writes TEXT.
Before any DATA write, DATA and TEXT must be fully disjoint with no
overflow, and data_size must equal the qualified size. After a
successful --map-check load that published parameters, lookup_count
must equal the TEXT+DATA+HEAP+IPC 16 KiB page count, readback_count
must equal the module's complete readback count, and pte_walk_count
must equal the samples per region times 4. A mismatch fails the run.
TEXT and DATA expected hashes are REQUALIFY_* placeholders, so this tree
refuses a real boot. This script is not installed at boot. The flock is
the same file as the other ISP probes. Never retries, reboots, or changes
machine power. insmod returning 0 is not proof the map succeeded: the
module parameters decide.
--dart-survey is mutually exclusive with the other modes. It loads with
map_check=true and dart_survey=true and requires --module and --sha256,
the same identity checks as --validate. It does not take a DATA reference
and does not write /dev/mem. It hashes TEXT and DATA before and after and
requires both unchanged. Those hashes are not compared with the REQUALIFY
placeholders. After insmod, the six clean parameters must be 0, and
lookup_count, readback_count, and pte_walk_count must be 0. A hash change
is reported instead of an insmod or unknown-state error, and it does not
write /dev/mem.
"""
from __future__ import annotations

import argparse
import dataclasses
import fcntl
import hashlib
import mmap
import os
import stat
import struct
import subprocess
import sys
from contextlib import contextmanager
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

PT_LOAD = 1
ELF64_EHDR_SIZE = 64
PHDR_SIZE = 56
PHDR_STRUCT = struct.Struct("<IIQQQQQQ")
MAX_PHNUM = 4096
MAX_PHOFF = 1 << 20
U64_MAX = (1 << 64) - 1

EXPECTED_RELEASE = "7.1.9-m4-gpu-backport-20261008"
EXPECTED_BOOT_UUID = "REQUALIFY_BOOT_UUID"
EXPECTED_COMPAT = ("apple,j713", "apple,t8132")
ADT_PHYS = 0x10004610000
ADT_SIZE = 0x74000
EXPECTED_ADT_SHA256 = (
    "REQUALIFY_ADT_SHA256"
)
TEXT_PHYS = 0x10000d20000
TEXT_SIZE = 0xca4000
DATA_PHYS = 0x10001f30000
DATA_SIZE = 0x748000
# j713_isp_fwview_layout.h: J713_DM_PAGE_SIZE, J713_DM_HEAP_SIZE,
# J713_DM_IPC_SIZE. lookup_count is J713_DM_PAGE_COUNT, the
# TEXT+DATA+HEAP+IPC 16 KiB pages.
DM_PAGE_SIZE = 0x4000
HEAP_SIZE = 0x14000
IPC_SIZE = 0x1c000
LOOKUP_COUNT = (
    TEXT_SIZE // DM_PAGE_SIZE
    + DATA_SIZE // DM_PAGE_SIZE
    + HEAP_SIZE // DM_PAGE_SIZE
    + IPC_SIZE // DM_PAGE_SIZE
)
# j713_isp_probe_alg.h: J713_ISP_DART_INSTANCES.
# j713_isp_fwview_main.c op_readback: readback_count == J713_ISP_DART_INSTANCES * 2.
DART_INSTANCES = 3
READBACK_COUNT = DART_INSTANCES * 2
# j713_isp_fwview_layout.h: J713_DM_PTE_SAMPLES.
# j713_isp_fwview_main.c walk_sid0_ptes: pte_walk_count == J713_DM_PTE_SAMPLES * 4.
PTE_SAMPLES = 3
PTE_WALK_COUNT = PTE_SAMPLES * 4
EXPECTED_TEXT_SHA256 = "REQUALIFY_TEXT_SHA256"
EXPECTED_DATA_SHA256 = "REQUALIFY_DATA_SHA256"
DEV_MEM_PATH = "/dev/mem"
FLASHREG_PATH = (
    "/sys/firmware/devicetree/base/reserved-memory/flash@10004610000/reg"
)
KCORE_PATH = "/proc/kcore"
BOOT_ID_PATH = "/proc/sys/kernel/random/boot_id"
COMPAT_PATH = "/sys/firmware/devicetree/base/compatible"
MODULES_PATH = "/proc/modules"
LOCK_PATH = "/run/lock/j713-isp-probe.lock"
MODULE_NAME = "j713_isp_fwview"
PARAM_DIR = "/sys/module/j713_isp_fwview/parameters"
CLEANUP_PATH = "/sys/module/j713_isp_fwview/parameters/cleanup_only"
PARAM_NAMES = (
    "result",
    "first_error",
    "retain",
    "cleanup_pending",
    "map_count",
    "ladder_stop",
    "text_prot",
    "data_prot",
    "heap_prot",
    "ipc_prot",
    "lookup_count",
    "readback_count",
    "pte_walk_count",
)
CLEAN_NAMES = (
    "result",
    "first_error",
    "retain",
    "cleanup_pending",
    "map_count",
    "ladder_stop",
)
RW_PROT_NAMES = ("data_prot", "heap_prot", "ipc_prot")
PROT_WRITE = 2
EXIT_LADDER = 2
IDLE_PARAMS = ("map_count", "retain", "cleanup_pending")


class ProbeError(Exception):
    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


@dataclasses.dataclass(frozen=True)
class ProbeConfig:
    release: str = EXPECTED_RELEASE
    boot_uuid: str = EXPECTED_BOOT_UUID
    compat: Tuple[str, ...] = EXPECTED_COMPAT
    adt_phys: int = ADT_PHYS
    adt_size: int = ADT_SIZE
    adt_sha256: str = EXPECTED_ADT_SHA256
    flashreg_path: str = FLASHREG_PATH
    kcore_path: str = KCORE_PATH
    boot_id_path: str = BOOT_ID_PATH
    compatible_path: str = COMPAT_PATH
    modules_path: str = MODULES_PATH
    lock_path: str = LOCK_PATH
    module_name: str = MODULE_NAME
    cleanup_path: str = CLEANUP_PATH
    insmod: str = "insmod"
    text_phys: int = TEXT_PHYS
    text_size: int = TEXT_SIZE
    data_phys: int = DATA_PHYS
    data_size: int = DATA_SIZE
    text_sha256: str = EXPECTED_TEXT_SHA256
    data_sha256: str = EXPECTED_DATA_SHA256
    devmem_path: str = DEV_MEM_PATH


Phdr = Tuple[int, int, int, int, int, int, int, int]
PreadFn = Callable[[str, int, int], bytes]


def add_u64(a: int, b: int, what: str) -> int:
    if a < 0 or b < 0 or a > U64_MAX or b > U64_MAX:
        raise ProbeError(what)
    s = a + b
    if s > U64_MAX:
        raise ProbeError(what)
    return s


def parse_elf64_le_header(header: bytes) -> Tuple[int, int, int]:
    if len(header) < ELF64_EHDR_SIZE:
        raise ProbeError("ELF header short")
    if header[:4] != b"\x7fELF":
        raise ProbeError("not ELF")
    if header[4] != 2:
        raise ProbeError("not ELF64")
    if header[5] != 1:
        raise ProbeError("not little-endian")
    phoff = struct.unpack_from("<Q", header, 32)[0]
    phentsize = struct.unpack_from("<H", header, 54)[0]
    phnum = struct.unpack_from("<H", header, 56)[0]
    if phentsize != PHDR_SIZE:
        raise ProbeError("phentsize")
    if phnum == 0 or phnum > MAX_PHNUM:
        raise ProbeError("phnum")
    if phoff < ELF64_EHDR_SIZE or phoff > MAX_PHOFF:
        raise ProbeError("phoff")
    table_size = phnum * phentsize
    add_u64(phoff, table_size, "phdr table overflow")
    return phoff, phentsize, phnum


def unpack_phdrs(table: bytes, phnum: int) -> List[Phdr]:
    need = phnum * PHDR_SIZE
    if len(table) != need:
        raise ProbeError("phdr table short")
    phdrs: List[Phdr] = []
    for i in range(phnum):
        phdrs.append(PHDR_STRUCT.unpack_from(table, i * PHDR_SIZE))
    return phdrs


def find_unique_pt_load(phdrs: Sequence[Phdr], phys: int, size: int) -> Phdr:
    if size <= 0 or phys < 0 or phys > U64_MAX:
        raise ProbeError("ADT range")
    adt_end = add_u64(phys, size, "ADT range overflow")
    hits: List[Phdr] = []
    for ph in phdrs:
        p_type, _flags, _off, _vaddr, p_paddr, _filesz, p_memsz, _align = ph
        if p_type != PT_LOAD:
            continue
        if p_paddr > phys:
            continue
        try:
            seg_end = add_u64(p_paddr, p_memsz, "PT_LOAD memsz overflow")
        except ProbeError:
            continue
        if adt_end > seg_end:
            continue
        hits.append(ph)
    if not hits:
        raise ProbeError("no PT_LOAD covers reserved ADT")
    if len(hits) != 1:
        raise ProbeError("ambiguous PT_LOAD mapping")
    return hits[0]


def mapped_file_offset(ph: Phdr, phys: int, size: int) -> int:
    _p_type, _flags, p_offset, _vaddr, p_paddr, p_filesz, _memsz, _align = ph
    delta = phys - p_paddr
    file_end_need = add_u64(delta, size, "ADT filesz overflow")
    if file_end_need > p_filesz:
        raise ProbeError("ADT exceeds PT_LOAD filesz")
    return add_u64(p_offset, delta, "ADT file offset overflow")


def read_kcore_adt(pread: PreadFn, path: str, phys: int, size: int) -> bytes:
    header = pread(path, ELF64_EHDR_SIZE, 0)
    phoff, phentsize, phnum = parse_elf64_le_header(header)
    table = pread(path, phnum * phentsize, phoff)
    phdrs = unpack_phdrs(table, phnum)
    ph = find_unique_pt_load(phdrs, phys, size)
    file_off = mapped_file_offset(ph, phys, size)
    data = pread(path, size, file_off)
    if len(data) != size:
        raise ProbeError("ADT short read")
    return data


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_euid(euid: int) -> None:
    if euid != 0:
        raise ProbeError("euid is not 0")


def check_release(release: str, expected: str) -> None:
    if release != expected:
        raise ProbeError("kernel release mismatch")


def check_boot_id(text: str, expected: str) -> None:
    got = text.strip()
    if got != expected:
        raise ProbeError("boot UUID mismatch")


def check_compatible(data: bytes, required: Iterable[str]) -> None:
    parts = [p.decode("ascii", "replace") for p in data.split(b"\0") if p]
    missing = [name for name in required if name not in parts]
    if missing:
        raise ProbeError("compatible mismatch")


def check_flashreg(data: bytes, phys: int, size: int) -> None:
    if len(data) != 16:
        raise ProbeError("flashreg size")
    addr, reg_size = struct.unpack(">QQ", data)
    if addr != phys or reg_size != size:
        raise ProbeError("flashreg mismatch")


def check_adt_hash(data: bytes, expected: str) -> None:
    if sha256_hex(data) != expected.lower():
        raise ProbeError("ADT sha256 mismatch")


def hash_readonly_map(fd: int, size: int, offset: int) -> str:
    if size <= 0 or offset < 0:
        raise ProbeError("phys range")
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    if (flags & os.O_ACCMODE) != os.O_RDONLY:
        raise ProbeError("phys map is not read-only")
    mapped = mmap.mmap(
        fd, size, flags=mmap.MAP_SHARED, prot=mmap.PROT_READ, offset=offset
    )
    try:
        return hashlib.sha256(mapped).hexdigest()
    finally:
        mapped.close()


def _mark_opened(exc: BaseException, opened: bool) -> None:
    setattr(exc, "opened", opened)


def write_exact_map(fd: int, blob: bytes, offset: int) -> None:
    """Replace exactly len(blob) bytes at offset. No other range is mapped."""
    if not isinstance(blob, (bytes, bytearray)) or len(blob) <= 0 or offset < 0:
        raise ProbeError("phys range")
    try:
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    except OSError as exc:
        _mark_opened(exc, False)
        raise
    if (flags & os.O_ACCMODE) != os.O_RDWR:
        raise ProbeError("phys map is not writable")
    try:
        mapped = mmap.mmap(
            fd,
            len(blob),
            flags=mmap.MAP_SHARED,
            prot=mmap.PROT_READ | mmap.PROT_WRITE,
            offset=offset,
        )
    except OSError as exc:
        _mark_opened(exc, False)
        raise
    try:
        # mmap.size() reports the size of the backing file (0 for /dev/mem),
        # not of this mapping.
        if len(mapped) != len(blob):
            err = ProbeError("phys map length")
            _mark_opened(err, True)
            raise err
        mapped[:] = bytes(blob)
        # No msync. /dev/mem has no fsync, and this map of non-RAM is uncached.
        # munmap makes the stores visible.
    except OSError as exc:
        _mark_opened(exc, True)
        raise
    finally:
        mapped.close()


def module_is_loaded(modules_text: str, name: str) -> bool:
    for line in modules_text.splitlines():
        if not line:
            continue
        token = line.split()[0]
        if token == name:
            return True
    return False


def check_module_not_loaded(modules_text: str, name: str) -> None:
    if module_is_loaded(modules_text, name):
        raise ProbeError("module already loaded")


def check_module_loaded(modules_text: str, name: str) -> None:
    if not module_is_loaded(modules_text, name):
        raise ProbeError("module not loaded")


def check_sha256_arg(value: str) -> str:
    got = value.strip().lower()
    if len(got) != 64:
        raise ProbeError("module sha256 length")
    try:
        int(got, 16)
    except ValueError:
        raise ProbeError("module sha256") from None
    return got


def _read_until(fd: int, size: int) -> bytes:
    """Read until size bytes have arrived or the file hits EOF."""
    parts: List[bytes] = []
    left = size
    while left > 0:
        chunk = os.read(fd, min(left, 1024 * 1024))
        if not chunk:
            break
        parts.append(chunk)
        left -= len(chunk)
    return b"".join(parts)


def hash_opened_bytes(read_chunks: Callable[[], Iterable[bytes]]) -> str:
    h = hashlib.sha256()
    for chunk in read_chunks():
        if chunk:
            h.update(chunk)
    return h.hexdigest()


def _copy_all_bytes(src: int, dst: int) -> int:
    total = 0
    while True:
        buf = os.read(src, 1024 * 1024)
        if not buf:
            break
        view = memoryview(buf)
        while view:
            n = os.write(dst, view)
            if n <= 0:
                raise ProbeError("module copy short write")
            total += n
            view = view[n:]
    return total


class RealHost:
    def geteuid(self) -> int:
        return os.geteuid()

    def uname_release(self) -> str:
        return os.uname().release

    def read_bytes(self, path: str) -> bytes:
        with open(path, "rb") as f:
            return f.read()

    def read_text(self, path: str) -> str:
        with open(path, "r", encoding="ascii", errors="replace") as f:
            return f.read()

    def pread(self, path: str, size: int, offset: int) -> bytes:
        fd = os.open(path, os.O_RDONLY)
        try:
            return os.pread(fd, size, offset)
        finally:
            os.close(fd)

    @contextmanager
    def acquire_lock(self, path: str):
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ProbeError("probe lock busy") from exc
            yield
        finally:
            os.close(fd)

    def open_module(self, path: str) -> int:
        flags = os.O_RDONLY
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        opened: List[int] = []
        try:
            src = os.open(path, flags)
            opened.append(src)
            info = os.fstat(src)
            if not stat.S_ISREG(info.st_mode):
                raise ProbeError("module is not a regular file")
            need_os = ("memfd_create", "MFD_CLOEXEC", "MFD_ALLOW_SEALING")
            need_fcntl = (
                "F_ADD_SEALS",
                "F_GET_SEALS",
                "F_SEAL_WRITE",
                "F_SEAL_GROW",
                "F_SEAL_SHRINK",
                "F_SEAL_SEAL",
            )
            if any(not hasattr(os, name) for name in need_os) or any(
                not hasattr(fcntl, name) for name in need_fcntl
            ):
                raise ProbeError("memfd sealing unavailable")
            mem = os.memfd_create(
                "module", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING
            )
            opened.append(mem)
            copied = _copy_all_bytes(src, mem)
            seals = (
                fcntl.F_SEAL_WRITE
                | fcntl.F_SEAL_GROW
                | fcntl.F_SEAL_SHRINK
                | fcntl.F_SEAL_SEAL
            )
            fcntl.fcntl(mem, fcntl.F_ADD_SEALS, seals)
            got = fcntl.fcntl(mem, fcntl.F_GET_SEALS)
            if (got & seals) != seals:
                raise ProbeError("module seals incomplete")
            # O_RDWR memfd holds deny_write_access (ETXTBSY in insmod).
            # Reopen read-only and close that writable fd before return.
            out = os.open("/proc/self/fd/%d" % mem, flags)
            opened.append(out)
            out_info = os.fstat(out)
            if not stat.S_ISREG(out_info.st_mode) or out_info.st_size != copied:
                raise ProbeError("module copy mismatch")
            os.lseek(out, 0, os.SEEK_SET)
            for live in (mem, src):
                opened.remove(live)
                try:
                    os.close(live)
                except OSError:
                    opened.append(live)
                    raise
            opened.remove(out)
            return out
        except BaseException:
            while opened:
                fd = opened.pop()
                try:
                    os.close(fd)
                except OSError:
                    pass
            raise

    def hash_fd(self, fd: int) -> str:
        os.lseek(fd, 0, os.SEEK_SET)

        def chunks() -> Iterable[bytes]:
            while True:
                buf = os.read(fd, 1024 * 1024)
                if not buf:
                    break
                yield buf

        digest = hash_opened_bytes(chunks)
        os.lseek(fd, 0, os.SEEK_SET)
        return digest

    def close_fd(self, fd: int) -> None:
        os.close(fd)

    def run_insmod(
        self, argv: Sequence[str], pass_fds: Sequence[int]
    ) -> subprocess.CompletedProcess:
        return subprocess.run(
            list(argv),
            pass_fds=tuple(pass_fds),
            close_fds=True,
            shell=False,
            capture_output=True,
            text=True,
        )

    def hash_phys(self, phys: int, size: int, path: Optional[str] = None) -> str:
        target = path if path else DEV_MEM_PATH
        flags = os.O_RDONLY | os.O_SYNC
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        fd = os.open(target, flags)
        try:
            got = fcntl.fcntl(fd, fcntl.F_GETFL)
            if (got & os.O_ACCMODE) != os.O_RDONLY:
                raise ProbeError("phys map is not read-only")
            info = os.fstat(fd)
            if stat.S_ISREG(info.st_mode):
                if phys < 0 or size <= 0 or phys + size > info.st_size:
                    raise ProbeError("phys image short")
            return hash_readonly_map(fd, size, phys)
        finally:
            os.close(fd)

    def read_phys(self, phys: int, size: int, path: Optional[str] = None) -> bytes:
        target = path if path else DEV_MEM_PATH
        flags = os.O_RDONLY | os.O_SYNC
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        fd = os.open(target, flags)
        try:
            got = fcntl.fcntl(fd, fcntl.F_GETFL)
            if (got & os.O_ACCMODE) != os.O_RDONLY:
                raise ProbeError("phys map is not read-only")
            info = os.fstat(fd)
            if stat.S_ISREG(info.st_mode):
                if phys < 0 or size <= 0 or phys + size > info.st_size:
                    raise ProbeError("phys image short")
            if size <= 0 or phys < 0:
                raise ProbeError("phys range")
            mapped = mmap.mmap(
                fd,
                size,
                flags=mmap.MAP_SHARED,
                prot=mmap.PROT_READ,
                offset=phys,
            )
            try:
                return bytes(mapped)
            finally:
                mapped.close()
        finally:
            os.close(fd)

    def write_phys(self, phys: int, blob: bytes, path: Optional[str] = None) -> None:
        target = path if path else DEV_MEM_PATH
        flags = os.O_RDWR | os.O_SYNC
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        try:
            fd = os.open(target, flags)
        except OSError as exc:
            _mark_opened(exc, False)
            raise
        try:
            write_exact_map(fd, blob, phys)
        finally:
            os.close(fd)

    def _accept_existing_reference(self, path: str, blob: bytes) -> None:
        flags = os.O_RDONLY
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(path, flags)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise ProbeError("DATA reference is not a regular file")
            if stat.S_IMODE(info.st_mode) != 0o600:
                raise ProbeError("DATA reference mode")
            if info.st_uid != os.geteuid():
                raise ProbeError("DATA reference owner")
            if info.st_size != len(blob):
                raise ProbeError("DATA reference differs")
            got = _read_until(fd, len(blob))
            if got != blob:
                raise ProbeError("DATA reference differs")
        finally:
            os.close(fd)

    def read_data_reference(self, path: str, size: int) -> bytes:
        if size <= 0:
            raise ProbeError("DATA reference size")
        flags = os.O_RDONLY
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(path, flags)
        except FileNotFoundError as exc:
            raise ProbeError("DATA reference missing") from exc
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise ProbeError("DATA reference is not a regular file")
            if stat.S_IMODE(info.st_mode) != 0o600:
                raise ProbeError("DATA reference mode")
            if info.st_uid != os.geteuid():
                raise ProbeError("DATA reference owner")
            if info.st_size != size:
                raise ProbeError("DATA reference size")
            got = _read_until(fd, size)
            if len(got) != size:
                raise ProbeError("DATA reference size")
            return got
        finally:
            os.close(fd)

    def store_reference(self, path: str, blob: bytes) -> None:
        if not isinstance(blob, (bytes, bytearray)) or not blob:
            raise ProbeError("DATA reference empty")
        if not path or "\0" in path:
            raise ProbeError("DATA reference path")
        payload = bytes(blob)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(path, flags, 0o600)
        except FileExistsError:
            self._accept_existing_reference(path, payload)
            return
        try:
            os.fchmod(fd, 0o600)
            view = memoryview(payload)
            while view:
                n = os.write(fd, view)
                if n <= 0:
                    raise ProbeError("DATA reference short write")
                view = view[n:]
        except BaseException:
            os.close(fd)
            try:
                os.unlink(path)
            except OSError:
                pass
            raise
        os.close(fd)

    def write_text(self, path: str, text: str) -> None:
        flags = os.O_WRONLY
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        fd = os.open(path, flags)
        try:
            data = text.encode("ascii")
            view = memoryview(data)
            while view:
                n = os.write(fd, view)
                if n <= 0:
                    raise ProbeError("short write")
                view = view[n:]
        finally:
            os.close(fd)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Private one-shot J713 ISP firmware-view launcher"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check-only",
        action="store_true",
        help="boot/ADT guards only (default)",
    )
    mode.add_argument(
        "--validate",
        action="store_true",
        help="load module with map_check=false; no power, overlay, or domain",
    )
    mode.add_argument(
        "--map-check",
        action="store_true",
        help="load module with map_check=true; the only /dev/mem write path",
    )
    mode.add_argument(
        "--dart-survey",
        action="store_true",
        help="load with map_check=true dart_survey=true; read-only DART survey",
    )
    mode.add_argument(
        "--cleanup-only",
        action="store_true",
        help="root recovery on an already-loaded module; no insmod and no remap",
    )
    mode.add_argument(
        "--restore-data",
        action="store_true",
        help="write --data-ref back to DATA when the module is absent or idle",
    )
    parser.add_argument("--module", help="path to j713_isp_fwview.ko")
    parser.add_argument("--sha256", help="sha256 of the opened module file")
    parser.add_argument(
        "--data-ref",
        help="DATA byte copy saved before insmod, or the source for --restore-data",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    if (
        not args.validate
        and not args.map_check
        and not args.dart_survey
        and not args.cleanup_only
        and not args.restore_data
    ):
        args.check_only = True
    if args.validate or args.map_check or args.dart_survey:
        if not args.module or not args.sha256:
            parser.error("--module and --sha256 are required to load")
    if args.map_check and not args.data_ref:
        parser.error("--data-ref is required for --map-check")
    if args.restore_data and not args.data_ref:
        parser.error("--data-ref is required for --restore-data")
    if args.cleanup_only and (args.module or args.sha256):
        parser.error("--cleanup-only does not load a module")
    return args


def _module_fd_before_insmod(
    args: argparse.Namespace, host, cfg: ProbeConfig
) -> int:
    """Checks that refuse before insmod. The caller closes the fd."""
    check_module_not_loaded(host.read_text(cfg.modules_path), cfg.module_name)
    expected = check_sha256_arg(args.sha256)
    fd = host.open_module(args.module)
    try:
        digest = host.hash_fd(fd)
        if digest != expected:
            raise ProbeError("module sha256 mismatch")
        check_boot_id(host.read_text(cfg.boot_id_path), cfg.boot_uuid)
    except BaseException:
        host.close_fd(fd)
        raise
    return fd


def _insmod_opened_module(
    args: argparse.Namespace, host, cfg: ProbeConfig, fd: int
) -> None:
    try:
        survey = bool(getattr(args, "dart_survey", False))
        map_on = bool(args.map_check) or survey
        map_val = "true" if map_on else "false"
        argv = [
            cfg.insmod,
            "/proc/self/fd/%d" % fd,
            "map_check=%s" % map_val,
        ]
        if survey:
            argv.append("dart_survey=true")
        proc = host.run_insmod(argv, pass_fds=(fd,))
    finally:
        host.close_fd(fd)
    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        out = (proc.stdout or "").strip()
        detail = err or out or "rc=%d" % proc.returncode
        raise ProbeError("insmod failed %s" % detail, code=1)


def _parse_param(name: str, text: str) -> int:
    got = text.strip()
    try:
        return int(got, 10)
    except ValueError:
        raise ProbeError("module param %s" % name) from None


def _read_params(host) -> Optional[dict]:
    """None when the result file is missing. Any other missing file fails."""
    try:
        result_text = host.read_text(PARAM_DIR + "/result")
    except FileNotFoundError:
        return None
    params = {"result": _parse_param("result", result_text)}
    for name in PARAM_NAMES:
        if name == "result":
            continue
        try:
            text = host.read_text(PARAM_DIR + "/" + name)
        except FileNotFoundError as exc:
            raise ProbeError("module param missing %s" % name) from exc
        params[name] = _parse_param(name, text)
    return params


def _format_params(params: dict) -> str:
    return " ".join("%s=%d" % (name, params[name]) for name in PARAM_NAMES)


def _print_cleanup_hint() -> None:
    print("--cleanup-only is the recovery step", file=sys.stderr)


def _print_restore_data_hint() -> None:
    print("--restore-data restores DATA from the reference", file=sys.stderr)


def _note_recovery(params: dict) -> None:
    if (
        params["retain"] != 0
        or params["cleanup_pending"] != 0
        or params["map_count"] != 0
    ):
        _print_cleanup_hint()


def _print_hash_result(name: str, before: str, after: str) -> None:
    state = "unchanged" if before == after else "changed"
    print("%s hash %s" % (name, state), file=sys.stderr)


def _print_firmware_results(host, cfg: ProbeConfig, text_before: str, data_hash: str) -> None:
    text_after = host.hash_phys(cfg.text_phys, cfg.text_size, cfg.devmem_path)
    data_after = host.hash_phys(cfg.data_phys, cfg.data_size, cfg.devmem_path)
    _print_hash_result("TEXT", text_before, text_after)
    _print_hash_result("DATA", data_hash, data_after)


def _require_clean_params(params: dict) -> None:
    _note_recovery(params)
    for name in CLEAN_NAMES:
        if params[name] != 0:
            raise ProbeError("module parameters are not clean")
    if params["text_prot"] & PROT_WRITE:
        raise ProbeError("text_prot has WRITE")
    for name in RW_PROT_NAMES:
        value = params[name]
        if value != 0 and (value & PROT_WRITE) == 0:
            raise ProbeError("%s lacks WRITE" % name)


def _require_firmware_reference(host, cfg: ProbeConfig, data_ref: Optional[str]):
    if not data_ref:
        raise ProbeError("--data-ref is required for --map-check")
    text = host.hash_phys(cfg.text_phys, cfg.text_size, cfg.devmem_path)
    if text != cfg.text_sha256.strip().lower():
        raise ProbeError("TEXT hash is not the qualified reference")
    data = host.read_phys(cfg.data_phys, cfg.data_size, cfg.devmem_path)
    if len(data) != cfg.data_size:
        raise ProbeError("DATA short read")
    data_hash = hashlib.sha256(data).hexdigest()
    if data_hash != cfg.data_sha256.strip().lower():
        raise ProbeError("DATA hash is not the qualified reference")
    host.store_reference(data_ref, data)
    return text, data_hash, data


def _ladder_result(message: str, cause: Optional[BaseException] = None) -> None:
    err = ProbeError(message, code=EXIT_LADDER)
    if cause is None:
        raise err
    raise err from cause


def _data_ranges_allow_write(cfg: ProbeConfig, blob_len: int) -> bool:
    """DATA must be the qualified size and fully miss TEXT, with no overflow.

    Ranges are half-open. Adjacent ranges are disjoint. A wrapping sum
    is not a legal range.
    """
    qualified = cfg.data_size
    if blob_len != qualified or qualified <= 0 or cfg.text_size <= 0:
        return False
    try:
        text_end = add_u64(cfg.text_phys, cfg.text_size, "TEXT range overflow")
        data_end = add_u64(cfg.data_phys, qualified, "DATA range overflow")
    except ProbeError:
        return False
    return data_end <= cfg.text_phys or text_end <= cfg.data_phys


def _rewrite_data(
    host, cfg: ProbeConfig, blob: bytes, data_hash: str
) -> Optional[BaseException]:
    """Write DATA and re-hash. Returns a write error when the re-hash matches.

    An OSError after the map was opened still re-hashes. An OSError before
    the map was opened does not. Failure raises EXIT_LADDER. Never writes TEXT.
    """
    if not _data_ranges_allow_write(cfg, len(blob)):
        raise ProbeError("DATA restore failed", code=EXIT_LADDER)
    write_error: Optional[BaseException] = None
    opened = True
    try:
        host.write_phys(cfg.data_phys, blob, cfg.devmem_path)
    except OSError as exc:
        write_error = exc
        # A host that stored the bytes and then raised leaves this unset.
        opened = bool(getattr(exc, "opened", True))
    except ProbeError as exc:
        write_error = exc
        opened = bool(getattr(exc, "opened", False))
    if write_error is not None and not opened:
        _ladder_result("DATA restore failed", write_error)
    try:
        verified = host.hash_phys(cfg.data_phys, cfg.data_size, cfg.devmem_path)
    except (OSError, ProbeError) as exc:
        _ladder_result("DATA restore failed", exc)
    if verified != data_hash:
        _ladder_result("DATA restore failed", write_error)
    return write_error


def _restore_data(host, cfg: ProbeConfig, blob: bytes, data_hash: str) -> None:
    """Map-check restore. The re-hash decides. The exit is always the ladder."""
    write_error = _rewrite_data(host, cfg, blob, data_hash)
    _ladder_result("DATA restored", write_error)


def _module_allows_data_restore(host, cfg: ProbeConfig) -> None:
    if not module_is_loaded(host.read_text(cfg.modules_path), cfg.module_name):
        return
    params = {}
    for name in IDLE_PARAMS:
        try:
            text = host.read_text(PARAM_DIR + "/" + name)
        except OSError as exc:
            raise ProbeError("module parameters unreadable") from exc
        try:
            params[name] = _parse_param(name, text)
        except ProbeError as exc:
            raise ProbeError("module parameters unreadable") from exc
    for name in IDLE_PARAMS:
        if params[name] != 0:
            raise ProbeError("module not idle; not writing /dev/mem")


def _restore_data_mode(host, cfg: ProbeConfig, data_ref: Optional[str]) -> str:
    if not data_ref:
        raise ProbeError("--data-ref is required for --restore-data")
    _module_allows_data_restore(host, cfg)
    blob = host.read_data_reference(data_ref, cfg.data_size)
    digest = hashlib.sha256(blob).hexdigest()
    if digest != cfg.data_sha256.strip().lower():
        raise ProbeError("DATA reference hash")
    check_boot_id(host.read_text(cfg.boot_id_path), cfg.boot_uuid)
    current = host.hash_phys(cfg.data_phys, cfg.data_size, cfg.devmem_path)
    if current == digest:
        return "DATA already matches"
    _rewrite_data(host, cfg, blob, digest)
    return "DATA restored"


def _survey_hashes(host, cfg: ProbeConfig):
    text = host.hash_phys(cfg.text_phys, cfg.text_size, cfg.devmem_path)
    data = host.hash_phys(cfg.data_phys, cfg.data_size, cfg.devmem_path)
    return text, data


def _require_survey_unchanged(host, cfg: ProbeConfig, text_before: str, data_before: str) -> None:
    text_after = host.hash_phys(cfg.text_phys, cfg.text_size, cfg.devmem_path)
    data_after = host.hash_phys(cfg.data_phys, cfg.data_size, cfg.devmem_path)
    _print_hash_result("TEXT", text_before, text_after)
    _print_hash_result("DATA", data_before, data_after)
    if text_after != text_before:
        raise ProbeError("TEXT hash changed")
    if data_after != data_before:
        raise ProbeError("DATA hash changed")


def _require_survey_counters(params: dict) -> None:
    for name in ("lookup_count", "readback_count", "pte_walk_count"):
        if params[name] != 0:
            raise ProbeError("%s is not 0" % name)


def _require_map_counters(params: dict) -> None:
    expected = (
        ("lookup_count", LOOKUP_COUNT),
        ("readback_count", READBACK_COUNT),
        ("pte_walk_count", PTE_WALK_COUNT),
    )
    for name, want in expected:
        if params[name] != want:
            raise ProbeError("%s mismatch" % name)


def _firmware_after(
    host, cfg: ProbeConfig, text_before: str, data_hash: str, data_blob: bytes
) -> None:
    text_after = host.hash_phys(cfg.text_phys, cfg.text_size, cfg.devmem_path)
    data_after = host.hash_phys(cfg.data_phys, cfg.data_size, cfg.devmem_path)
    text_changed = text_after != text_before
    data_changed = data_after != data_hash
    if data_changed:
        if text_changed:
            print("TEXT hash changed", file=sys.stderr)
        _restore_data(host, cfg, data_blob, data_hash)
    if text_changed:
        raise ProbeError("TEXT hash changed")


def _cleanup_only(host, cfg: ProbeConfig) -> None:
    check_module_loaded(host.read_text(cfg.modules_path), cfg.module_name)
    check_boot_id(host.read_text(cfg.boot_id_path), cfg.boot_uuid)
    host.write_text(cfg.cleanup_path, "1\n")


def run_probe(
    args: argparse.Namespace,
    host=None,
    cfg: Optional[ProbeConfig] = None,
) -> str:
    host = host or RealHost()
    cfg = cfg or ProbeConfig()
    survey = bool(getattr(args, "dart_survey", False))
    with host.acquire_lock(cfg.lock_path):
        check_euid(host.geteuid())
        check_release(host.uname_release(), cfg.release)
        check_boot_id(host.read_text(cfg.boot_id_path), cfg.boot_uuid)
        check_compatible(host.read_bytes(cfg.compatible_path), cfg.compat)
        check_flashreg(
            host.read_bytes(cfg.flashreg_path), cfg.adt_phys, cfg.adt_size
        )
        adt = read_kcore_adt(
            host.pread, cfg.kcore_path, cfg.adt_phys, cfg.adt_size
        )
        try:
            check_adt_hash(adt, cfg.adt_sha256)
        finally:
            del adt
        if args.check_only:
            return "check-only: ok"
        if getattr(args, "restore_data", False):
            return _restore_data_mode(host, cfg, getattr(args, "data_ref", None))
        if args.cleanup_only:
            _cleanup_only(host, cfg)
            return "cleanup-only: requested"
        text_before = None
        data_hash = None
        data_blob = None
        data_before = None
        if args.map_check:
            text_before, data_hash, data_blob = _require_firmware_reference(
                host, cfg, getattr(args, "data_ref", None)
            )
        elif survey:
            text_before, data_before = _survey_hashes(host, cfg)
        # A ProbeError here is a refusal before insmod. It must propagate
        # with no parameter read, firmware print, or DATA restore.
        fd = _module_fd_before_insmod(args, host, cfg)
        load_error = None
        state_error = None
        params = None
        try:
            _insmod_opened_module(args, host, cfg, fd)
        except ProbeError as exc:
            load_error = exc
        except OSError as exc:
            if not args.map_check and not survey:
                raise
            state_error = ProbeError("module state unknown")
            state_error.__cause__ = exc
            state_error.__suppress_context__ = True
        if state_error is None:
            try:
                params = _read_params(host)
            except ProbeError as exc:
                state_error = exc
            except OSError as exc:
                if not args.map_check and not survey:
                    raise
                state_error = ProbeError("module parameters unreadable")
                state_error.__cause__ = exc
                state_error.__suppress_context__ = True
        if state_error is None and params is None:
            try:
                loaded = module_is_loaded(
                    host.read_text(cfg.modules_path), cfg.module_name
                )
            except OSError as exc:
                if not args.map_check and not survey:
                    raise
                state_error = ProbeError("module state unknown")
                state_error.__cause__ = exc
                state_error.__suppress_context__ = True
                loaded = False
            if loaded:
                state_error = ProbeError("module parameters unknown")
        if params is not None:
            print(_format_params(params))
            if args.map_check and (
                params["map_count"] != 0 or params["retain"] != 0
            ):
                _note_recovery(params)
                raise ProbeError("module still mapped; not writing /dev/mem")
        if survey:
            _require_survey_unchanged(host, cfg, text_before, data_before)
        if state_error is not None:
            if args.map_check:
                _print_firmware_results(host, cfg, text_before, data_hash)
                _print_cleanup_hint()
                _print_restore_data_hint()
            raise state_error
        if args.map_check:
            _firmware_after(host, cfg, text_before, data_hash, data_blob)
        if load_error is not None:
            raise load_error
        if params is not None:
            _require_clean_params(params)
            if args.map_check:
                _require_map_counters(params)
            if survey:
                _require_survey_counters(params)
        if args.map_check:
            mode = "map-check"
        elif survey:
            mode = "dart-survey"
        else:
            mode = "validate"
        return "%s: loaded" % mode


def main(argv: Optional[Sequence[str]] = None, host=None, cfg=None) -> int:
    try:
        args = parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        return int(code) if not isinstance(code, int) else code
    try:
        msg = run_probe(args, host=host, cfg=cfg)
    except ProbeError as exc:
        print(str(exc), file=sys.stderr)
        return exc.code
    except OSError as exc:
        print("io error: %s" % exc, file=sys.stderr)
        return 1
    print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
