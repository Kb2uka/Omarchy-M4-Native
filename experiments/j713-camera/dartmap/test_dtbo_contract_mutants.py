#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Mutants for the DTBO node-set, property-set, and empty reserve map contract.

DTC must name the experiment dtc. An unset or non-executable DTC fails
the test. require_dtc() returns that file's absolute path, so a slash-free
name is the executable in the working directory and not a PATH lookup.
That file's --version probe must exit 0 and include DTC in its output.
This module never falls back to a system dtc. The unmutated overlay must
pass check(). Each mutant must raise AssertionError for its own reason.
A dtc compile failure is a test error, not a killed mutant.

Duplicate-node and duplicate-property guards are fed hand-written FDT
blobs. dtc cannot emit those duplicates.

Remote-only. Do not run this on a development machine.
"""

from __future__ import annotations

import os
import struct
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import embed_dtbo
import test_dtbo_contract

DTS_NAME = "j713-isp-dart-map-overlay.dts"
MASTER_COMPAT = '\t\t\t\tcompatible = "kb2uka,j713-isp-dart-map-test";\n'
TARGET_PATH = '\t\ttarget-path = "/soc";\n'
OVERLAY_OPEN = "\t\t__overlay__ {\n"
IOMMUS_CLOSE = (
    "\t\t\t\tiommus = <&dart_isp0 0 &dart_isp1 0 &dart_isp2 0>;\n"
    "\t\t\t};\n"
)
FRAGMENT_CLOSE = "\t\t\t};\n\t\t};\n\t};\n"
FRAGMENT1 = (
    "\tfragment@1 {\n"
    '\t\ttarget-path = "/soc";\n'
    "\t\t__overlay__ {\n"
    "\t\t\textra-node {\n"
    '\t\t\t\tcompatible = "kb2uka,extra";\n'
    "\t\t\t};\n"
    "\t\t};\n"
    "\t};\n"
)
MASTER_WITH_CHILD = (
    "\t\t\t\tiommus = <&dart_isp0 0 &dart_isp1 0 &dart_isp2 0>;\n"
    "\t\t\t\textra-child { };\n"
    "\t\t\t};\n"
)


def require_dtc() -> str:
    dtc = os.environ.get("DTC", "")
    if not dtc:
        raise AssertionError(
            "DTC is unset; export DTC and do not fall back to a system dtc"
        )
    if not os.path.isfile(dtc) or not os.access(dtc, os.X_OK):
        raise AssertionError("DTC is not executable: " + dtc)
    # Absolute, so subprocess runs this file instead of searching PATH.
    path = os.path.abspath(dtc)
    try:
        probe = subprocess.run(
            [path, "--version"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AssertionError(("DTC is not a working dtc", path, error)) from error
    detail = probe.stdout + probe.stderr
    if probe.returncode != 0 or "DTC" not in detail:
        raise AssertionError(("DTC is not a working dtc", path, detail))
    return path


def replace_once(text: str, old: str, new: str) -> str:
    count = text.count(old)
    if count != 1:
        raise AssertionError(("replacement not unique", count))
    return text.replace(old, new, 1)


def compile_overlay(dtc: str, source: str, symbols: bool) -> tuple[bytes, str]:
    with tempfile.TemporaryDirectory(prefix="j713-dtbo-mutant-") as td:
        directory = Path(td)
        src = directory / "overlay.dts"
        out = directory / "overlay.dtbo"
        src.write_text(source)
        # Same argv as `make dtbo`, plus -@ only for the symbols mutant.
        cmd = [dtc, "-I", "dts", "-O", "dtb", "-o", str(out), str(src)]
        if symbols:
            cmd.insert(1, "-@")
        # A compiler failure propagates. It is not an AssertionError kill.
        subprocess.run(cmd, check=True, capture_output=True, timeout=60)
        blob = out.read_bytes()
    return blob, embed_dtbo.embed(blob)


class DtboContractMutantTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dtc = require_dtc()
        self.source = (Path(__file__).resolve().parent / DTS_NAME).read_text()

    def test_unmutated_overlay_passes(self) -> None:
        blob, inc_text = compile_overlay(self.dtc, self.source, symbols=False)
        test_dtbo_contract.check(blob, inc_text)

    def test_mutants_fail_for_their_reason(self) -> None:
        status = MASTER_COMPAT + '\t\t\t\tstatus = "disabled";\n'
        extra_prop = "\t\tkb2uka,extra = <1>;\n"
        overlay_prop = "\t\t\tkb2uka,extra = <1>;\n"
        specs = [
            (
                "master-status",
                MASTER_COMPAT,
                status,
                ("properties", "status"),
            ),
            (
                "extra-fragment",
                FRAGMENT_CLOSE,
                FRAGMENT_CLOSE + FRAGMENT1,
                ("nodes", "fragment@1"),
            ),
            (
                "stray-root",
                FRAGMENT_CLOSE,
                FRAGMENT_CLOSE + "\tstray { };\n",
                ("nodes", "/stray"),
            ),
            (
                "root-property",
                "/ {\n\tfragment@0 {\n",
                "/ {\n\tkb2uka,extra = <1>;\n\tfragment@0 {\n",
                ("'/', 'properties'", "kb2uka,extra"),
            ),
            (
                "fragment-property",
                TARGET_PATH,
                TARGET_PATH + extra_prop,
                ("'/fragment@0', 'properties'", "kb2uka,extra"),
            ),
            (
                "overlay-property",
                OVERLAY_OPEN,
                OVERLAY_OPEN + overlay_prop,
                ("'/fragment@0/__overlay__', 'properties'", "kb2uka,extra"),
            ),
            (
                "master-child",
                IOMMUS_CLOSE,
                MASTER_WITH_CHILD,
                ("nodes", "extra-child"),
            ),
            (
                "memreserve",
                "/plugin/;\n",
                "/plugin/;\n/memreserve/ 0x10000000 0x4000;\n",
                ("memreserve",),
            ),
            (
                "external-label",
                "\t\t\t\treg = <0x4 0x8e8c0000 0x0 0xc000>;\n"
                "\t\t\t\t#iommu-cells = <1>;\n"
                "\t\t\t\tinterrupt-parent = <0x0d>;\n",
                "\t\t\t\treg = <0x4 0x8e8c0000 0x0 0xc000>;\n"
                "\t\t\t\t#iommu-cells = <1>;\n"
                "\t\t\t\tinterrupt-parent = <&aic>;\n",
                ("exported symbols", "/__fixups__"),
            ),
        ]
        for name, old, new, needles in specs:
            with self.subTest(name):
                mutated = replace_once(self.source, old, new)
                self.assertNotEqual(mutated, self.source)
                self.refuse(mutated, symbols=False, needles=needles)
        with self.subTest("dtc-symbols"):
            self.refuse(
                self.source, symbols=True, needles=("exported symbols",)
            )

    def refuse(
        self, source: str, symbols: bool, needles: tuple[str, ...]
    ) -> None:
        blob, inc_text = compile_overlay(self.dtc, source, symbols)
        with self.assertRaises(AssertionError) as caught:
            test_dtbo_contract.check(blob, inc_text)
        message = repr(caught.exception)
        for needle in needles:
            self.assertIn(needle, message)


def _pad4(data: bytes) -> bytes:
    return data + b"\0" * ((-len(data)) & 3)


def build_fdt(
    root: tuple[str, list[tuple[str, bytes]], list],
) -> bytes:
    """Return one version-17 FDT blob for parse_fdt.

    root is (name, properties, children). properties is a list of
    (name, value) pairs. children is a list of the same node shape.
    The header is ten big-endian words, and the reserve map is the empty
    16-byte terminator. Node names sit inline, NUL-terminated and padded
    to 4 bytes. Property names live in the strings block. A repeated name
    is kept, which dtc itself will not emit.
    """
    strings = bytearray()
    offsets: dict[str, int] = {}

    def intern(name: str) -> int:
        offset = offsets.get(name)
        if offset is None:
            offset = len(strings)
            offsets[name] = offset
            strings.extend(name.encode() + b"\0")
        return offset

    body = bytearray()

    def emit(
        node: tuple[str, list[tuple[str, bytes]], list],
    ) -> None:
        name, properties, children = node
        body.extend(struct.pack(">I", test_dtbo_contract.FDT_BEGIN_NODE))
        body.extend(_pad4(name.encode() + b"\0"))
        for prop_name, value in properties:
            body.extend(
                struct.pack(
                    ">III",
                    test_dtbo_contract.FDT_PROP,
                    len(value),
                    intern(prop_name),
                )
            )
            body.extend(_pad4(value))
        for child in children:
            emit(child)
        body.extend(struct.pack(">I", test_dtbo_contract.FDT_END_NODE))

    emit(root)
    body.extend(struct.pack(">I", test_dtbo_contract.FDT_END))
    reserve = b"\0" * 16
    off_mem_rsvmap = 40
    off_dt_struct = off_mem_rsvmap + len(reserve)
    off_dt_strings = off_dt_struct + len(body)
    header = struct.pack(
        ">10I",
        0xD00DFEED,
        off_dt_strings + len(strings),
        off_dt_struct,
        off_dt_strings,
        off_mem_rsvmap,
        17,
        16,
        0,
        len(strings),
        len(body),
    )
    return header + reserve + bytes(body) + bytes(strings)


def tree_from_props(
    props: dict[str, dict[str, bytes]],
) -> tuple[str, list[tuple[str, bytes]], list]:
    """Nest a parse_fdt map into the build_fdt node shape.

    parse_fdt keeps insertion order. A node's children are the paths
    whose parent is that node, in the order those paths appear.
    """
    def parent(path: str) -> str:
        head = path.rsplit("/", 1)[0]
        return head if head else "/"

    def node(path: str) -> tuple[str, list[tuple[str, bytes]], list]:
        name = "" if path == "/" else path.rsplit("/", 1)[1]
        children = [
            node(child)
            for child in props
            if child != "/" and parent(child) == path
        ]
        return name, list(props[path].items()), children

    return node("/")


class RequireDtcPathTest(unittest.TestCase):
    def test_slash_free_name_resolves_to_the_cwd_executable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="j713-dtc-cwd-") as td:
            name = "experiment-dtc"
            binary = Path(td) / name
            binary.write_bytes(b"#!/bin/sh\necho 'Version: DTC 0.0-test'\n")
            binary.chmod(0o755)
            previous = os.getcwd()
            try:
                os.chdir(td)
                with unittest.mock.patch.dict(os.environ, {"DTC": name}):
                    resolved = require_dtc()
                expected = os.path.join(os.getcwd(), name)
            finally:
                os.chdir(previous)
            self.assertEqual(resolved, expected)
            self.assertTrue(os.path.samefile(resolved, binary))

    def test_unset_dtc_raises(self) -> None:
        with unittest.mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DTC", None)
            with self.assertRaises(AssertionError) as caught:
                require_dtc()
        self.assertIn("DTC is unset", repr(caught.exception))

    def test_empty_dtc_raises(self) -> None:
        with unittest.mock.patch.dict(os.environ, {"DTC": ""}, clear=False):
            with self.assertRaises(AssertionError) as caught:
                require_dtc()
        self.assertIn("DTC is unset", repr(caught.exception))

    def test_missing_path_raises(self) -> None:
        with tempfile.TemporaryDirectory(prefix="j713-dtc-missing-") as td:
            missing = os.path.join(td, "missing-dtc")
            with unittest.mock.patch.dict(
                os.environ, {"DTC": missing}, clear=False
            ):
                with self.assertRaises(AssertionError) as caught:
                    require_dtc()
        self.assertIn(
            "DTC is not executable: " + missing, repr(caught.exception)
        )

    def test_nonexecutable_file_raises(self) -> None:
        with tempfile.TemporaryDirectory(prefix="j713-dtc-mode-") as td:
            binary = Path(td) / "not-executable"
            binary.write_bytes(b"")
            binary.chmod(0o644)
            with unittest.mock.patch.dict(
                os.environ, {"DTC": str(binary)}, clear=False
            ):
                with self.assertRaises(AssertionError) as caught:
                    require_dtc()
        self.assertIn(
            "DTC is not executable: " + str(binary), repr(caught.exception)
        )

    def test_executable_that_is_not_dtc_raises(self) -> None:
        with tempfile.TemporaryDirectory(prefix="j713-dtc-hello-") as td:
            binary = Path(td) / "not-dtc"
            binary.write_bytes(b"#!/bin/sh\necho hello\n")
            binary.chmod(0o755)
            with unittest.mock.patch.dict(
                os.environ, {"DTC": str(binary)}, clear=False
            ):
                with self.assertRaises(AssertionError) as caught:
                    require_dtc()
        message = repr(caught.exception)
        self.assertIn("DTC is not a working dtc", message)
        self.assertIn(str(binary), message)

    def test_executable_nonzero_status_raises(self) -> None:
        with tempfile.TemporaryDirectory(prefix="j713-dtc-exit-") as td:
            binary = Path(td) / "bad-dtc"
            binary.write_bytes(b"#!/bin/sh\nexit 3\n")
            binary.chmod(0o755)
            with unittest.mock.patch.dict(
                os.environ, {"DTC": str(binary)}, clear=False
            ):
                with self.assertRaises(AssertionError) as caught:
                    require_dtc()
        message = repr(caught.exception)
        self.assertIn("DTC is not a working dtc", message)
        self.assertIn(str(binary), message)

    def test_exec_format_error_raises(self) -> None:
        with tempfile.TemporaryDirectory(prefix="j713-dtc-exec-") as td:
            binary = Path(td) / "not-an-executable"
            binary.write_bytes(b"\x00\x01not-an-executable\n")
            binary.chmod(0o755)
            with unittest.mock.patch.dict(
                os.environ, {"DTC": str(binary)}, clear=False
            ):
                with self.assertRaises(AssertionError) as caught:
                    require_dtc()
        message = repr(caught.exception)
        self.assertIn("DTC is not a working dtc", message)
        self.assertIn(str(binary), message)

    def test_version_probe_timeout_raises(self) -> None:
        with tempfile.TemporaryDirectory(prefix="j713-dtc-timeout-") as td:
            binary = Path(td) / "slow-dtc"
            binary.write_bytes(b"#!/bin/sh\necho 'Version: DTC 0.0-test'\n")
            binary.chmod(0o755)
            with unittest.mock.patch.dict(
                os.environ, {"DTC": str(binary)}, clear=False
            ):
                with unittest.mock.patch.object(
                    subprocess,
                    "run",
                    side_effect=subprocess.TimeoutExpired(
                        cmd=["dtc", "--version"], timeout=30
                    ),
                ):
                    with self.assertRaises(AssertionError) as caught:
                        require_dtc()
        self.assertIn("DTC is not a working dtc", repr(caught.exception))


class HandwrittenFdtTest(unittest.TestCase):
    def test_distinct_names_parse(self) -> None:
        blob = build_fdt((
            "",
            [],
            [
                ("left", [("one", b"\x11"), ("two", b"\x22\x33")], []),
                ("right", [("one", b"\x44\x55\x66")], []),
            ],
        ))
        self.assertEqual(
            test_dtbo_contract.parse_fdt(blob),
            {
                "/": {},
                "/left": {"one": b"\x11", "two": b"\x22\x33"},
                "/right": {"one": b"\x44\x55\x66"},
            },
        )

    def test_duplicate_sibling_node_raises(self) -> None:
        blob = build_fdt(("", [], [("same", [], []), ("same", [], [])]))
        with self.assertRaises(AssertionError) as caught:
            test_dtbo_contract.parse_fdt(blob)
        message = repr(caught.exception)
        self.assertIn("duplicate node", message)
        self.assertIn("'/same'", message)

    def test_duplicate_property_raises(self) -> None:
        blob = build_fdt((
            "",
            [],
            [("node", [("same", b"\x01"), ("same", b"\x02")], [])],
        ))
        with self.assertRaises(AssertionError) as caught:
            test_dtbo_contract.parse_fdt(blob)
        message = repr(caught.exception)
        self.assertIn("duplicate property", message)
        self.assertIn("'/node'", message)
        self.assertIn("'same'", message)


class LocalFixupPropertyRoundTripTest(unittest.TestCase):
    def setUp(self) -> None:
        dtc = require_dtc()
        source = (Path(__file__).resolve().parent / DTS_NAME).read_text()
        blob, _inc_text = compile_overlay(dtc, source, symbols=False)
        self.props = test_dtbo_contract.parse_fdt(blob)

    def test_unmodified_round_trip_passes(self) -> None:
        raw = build_fdt(tree_from_props(self.props))
        test_dtbo_contract.check(raw, embed_dtbo.embed(raw))

    def test_extra_local_fixup_property_fails(self) -> None:
        paths = (
            "/__local_fixups__",
            "/__local_fixups__/fragment@0",
            "/__local_fixups__/fragment@0/__overlay__",
            "/__local_fixups__/fragment@0/__overlay__/j713-isp-dart-map-test",
        )
        for path in paths:
            with self.subTest(path):
                mutated = {
                    key: dict(values) for key, values in self.props.items()
                }
                mutated[path]["kb2uka,extra"] = b"\0\0\0\1"
                raw = build_fdt(tree_from_props(mutated))
                with self.assertRaises(AssertionError) as caught:
                    test_dtbo_contract.check(raw, embed_dtbo.embed(raw))
                message = repr(caught.exception)
                self.assertIn(repr(path) + ", 'properties'", message)
                self.assertIn("kb2uka,extra", message)


if __name__ == "__main__":
    unittest.main()
