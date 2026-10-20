#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Private one-shot J713 ISP DART scratch-map launcher.

Default is --check-only. --validate loads the module with map_check=false
(no power, overlay, or domain). --map-check is the only load path that
may write. --cleanup-only writes the root sysfs recovery trigger on an
already-loaded module and does not insmod, remap, or retry. There is no
identity-only --apply and no --inspect. Load modes require --module and
--sha256 of that opened file; the hash is supplied by the caller and is
not hardcoded here. This script is not installed at boot. The flock is
the same file as the identity probe. Never retries, reboots, or changes
machine power. insmod returning 0 is not proof the map succeeded: read
the module result parameters and dmesg.
"""
from __future__ import annotations

import argparse
import dataclasses
import fcntl
import hashlib
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
FLASHREG_PATH = (
    "/sys/firmware/devicetree/base/reserved-memory/flash@10004610000/reg"
)
KCORE_PATH = "/proc/kcore"
BOOT_ID_PATH = "/proc/sys/kernel/random/boot_id"
COMPAT_PATH = "/sys/firmware/devicetree/base/compatible"
MODULES_PATH = "/proc/modules"
LOCK_PATH = "/run/lock/j713-isp-probe.lock"
MODULE_NAME = "j713_isp_dartmap"
CLEANUP_PATH = "/sys/module/j713_isp_dartmap/parameters/cleanup_only"


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
        description="Private one-shot J713 ISP DART scratch-map launcher"
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
        help="load module with map_check=true; the only write path",
    )
    mode.add_argument(
        "--cleanup-only",
        action="store_true",
        help="root recovery on an already-loaded module; no insmod and no remap",
    )
    parser.add_argument("--module", help="path to j713_isp_dartmap.ko")
    parser.add_argument("--sha256", help="sha256 of the opened module file")
    args = parser.parse_args(list(argv) if argv is not None else None)
    if not args.validate and not args.map_check and not args.cleanup_only:
        args.check_only = True
    if args.validate or args.map_check:
        if not args.module or not args.sha256:
            parser.error("--module and --sha256 are required to load")
    if args.cleanup_only and (args.module or args.sha256):
        parser.error("--cleanup-only does not load a module")
    return args


def _load_module(args: argparse.Namespace, host, cfg: ProbeConfig) -> None:
    check_module_not_loaded(host.read_text(cfg.modules_path), cfg.module_name)
    expected = check_sha256_arg(args.sha256)
    fd = host.open_module(args.module)
    try:
        digest = host.hash_fd(fd)
        if digest != expected:
            raise ProbeError("module sha256 mismatch")
        check_boot_id(host.read_text(cfg.boot_id_path), cfg.boot_uuid)
        map_val = "true" if args.map_check else "false"
        argv = [
            cfg.insmod,
            "/proc/self/fd/%d" % fd,
            "map_check=%s" % map_val,
        ]
        proc = host.run_insmod(argv, pass_fds=(fd,))
    finally:
        host.close_fd(fd)
    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        out = (proc.stdout or "").strip()
        detail = err or out or "rc=%d" % proc.returncode
        raise ProbeError("insmod failed %s" % detail, code=1)


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
        if args.cleanup_only:
            _cleanup_only(host, cfg)
            return "cleanup-only: requested"
        _load_module(args, host, cfg)
        if args.map_check:
            mode = "map-check"
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
