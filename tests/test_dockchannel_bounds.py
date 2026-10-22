"""Contract for the DockChannel HID receive path.

The harness compiles the driver's checksum, init parser, packet, ACK, event,
and report functions. Ready and GPIO command handlers stay mocked. By default
that driver is a temporary copy of the pinned source with
patches-kernel/retained-hardening/dockchannel-packet-bounds.patch applied.
DCHID_SOURCE bypasses the patch for baseline and mutant runs.
"""

from hashlib import sha256
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

PIN = Path(__file__).resolve().parent / "fixtures" / "dockchannel" / "dockchannel-hid.c"
PATCH = Path(__file__).resolve().parents[1] / "patches-kernel" / "retained-hardening" / "dockchannel-packet-bounds.patch"
HASH = "e4eebbee612eee32bd51ce370d412afc8cf62a9a7e087a3797c2c29306cedfb4"


def driver_text(directory):
    if "DCHID_SOURCE" in os.environ:
        return Path(os.environ["DCHID_SOURCE"]).read_text()
    dest = directory / "drivers" / "hid" / "dockchannel-hid" / "dockchannel-hid.c"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(PIN.read_bytes())
    subprocess.run(
        ["patch", "-p1", "--fuzz=0", "--forward", "-i", str(PATCH)],
        cwd=directory,
        check=True,
        capture_output=True,
        text=True,
    )
    return dest.read_text()


def between(source, start, end):
    try:
        first = source.index(start)
        last = source.index(end, first)
    except ValueError as exc:
        raise AssertionError("pinned driver is missing an extraction marker: " + start) from exc
    return source[first:last]


def build_source(driver):
    fixture = Path(__file__).with_name("dockchannel_bounds_fixture.c").read_text()
    license_at = driver.index("/*\n * SPDX-License-Identifier: GPL-2.0 OR MIT\n")
    license_end = driver.index("*/", license_at)
    types = between(driver, "#define MAX_INTERFACES 16\n", "\nstatic ssize_t apple_layout_id_show(")
    checksum = between(driver, "static u32 dchid_checksum(", "\nstatic int dchid_send(")
    init = between(driver, "static void dchid_handle_init(", "\nstatic void dchid_handle_gpio(")
    rx = between(driver, "static void dchid_handle_event(", "\nstatic int dockchannel_hid_probe(")
    for name in ("dchid_handle_packet", "dchid_packet_work", "dchid_handle_ack", "dchid_handle_report"):
        if name not in rx:
            raise AssertionError("extracted receive path is missing " + name)
    if "dchid_checksum" not in checksum:
        raise AssertionError("extracted checksum is missing")
    if "dchid_get_interface" not in init or "dchid_handle_descriptor" not in init:
        raise AssertionError("extracted init path is not the driver parser")
    if "dchid_handle_ready" in init or "dchid_handle_gpio" in init:
        raise AssertionError("extracted init path includes a different event handler")
    generated = fixture
    for marker, chunk in (
        ("/* ACTUAL_TYPES */", types),
        ("/* ACTUAL_INIT */", init),
        ("/* ACTUAL_CHECKSUM */", checksum),
        ("/* ACTUAL_RX */", rx),
    ):
        if generated.count(marker) != 1:
            raise AssertionError("fixture marker is not unique: " + marker)
        generated = generated.replace(marker, chunk)
    return driver[license_at:license_end + 2] + "\n" + generated


class PinnedDockChannelDriverTest(unittest.TestCase):
    def test_snapshot_hash_and_license(self):
        data = PIN.read_bytes()
        self.assertEqual(sha256(data).hexdigest(), HASH)
        text = data.decode("utf-8")
        self.assertIn("SPDX-License-Identifier: GPL-2.0 OR MIT", text)
        self.assertIn("Copyright The Asahi Linux Contributors", text)
        self.assertIn("Apple DockChannel HID transport driver", text)


class DockChannelBoundsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="dchid-bounds-")
        directory = Path(cls.tmp.name)
        driver = driver_text(directory)
        source = directory / "dockchannel_bounds.c"
        source.write_text(build_source(driver))
        cls.exe = directory / "dockchannel_bounds"
        compiler = os.environ.get("CC", "cc")
        # Imported dchid_handle_packet(cookie, avail) does not read avail.
        # The flag covers that callback only; the extracted driver is not edited.
        subprocess.run(
            [compiler, "-std=gnu11", "-O0", "-g", "-Wall", "-Wextra", "-Werror",
             "-Wno-unused-parameter",
             "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
             str(source), "-o", str(cls.exe)],
            check=True,
        )
        cls.env = os.environ.copy()
        cls.env["ASAN_OPTIONS"] = "abort_on_error=1:detect_leaks=0:halt_on_error=1"
        cls.env["UBSAN_OPTIONS"] = "abort_on_error=1:halt_on_error=1:print_stacktrace=1"

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def assert_pass(self, case):
        result = subprocess.run(
            [str(self.exe), case],
            capture_output=True,
            text=True,
            timeout=20,
            env=self.env,
            check=False,
        )
        self.assertEqual(result.returncode, 0, "\n".join((case, result.stdout, result.stderr)))
        self.assertIn("PASS " + case, result.stdout)

    def test_normal_report(self):
        self.assert_pass("normal_report")

    def test_one_byte_report(self):
        self.assert_pass("one_byte_report")

    def test_normal_ack(self):
        self.assert_pass("normal_ack")

    def test_interface_15_normal_report(self):
        self.assert_pass("iface_15")

    def test_interface_16_rejected_before_index(self):
        self.assert_pass("iface_16")

    def test_interface_255_rejected_before_index(self):
        self.assert_pass("iface_255")

    def test_all_invalid_interfaces(self):
        self.assert_pass("all_invalid_ifaces")

    def test_short_subheader_0(self):
        self.assert_pass("short_0")

    def test_short_subheader_1(self):
        self.assert_pass("short_1")

    def test_short_subheader_2(self):
        self.assert_pass("short_2")

    def test_short_subheader_3(self):
        self.assert_pass("short_3")

    def test_short_subheader_4(self):
        self.assert_pass("short_4")

    def test_short_subheader_5(self):
        self.assert_pass("short_5")

    def test_short_subheader_6(self):
        self.assert_pass("short_6")

    def test_short_subheader_7(self):
        self.assert_pass("short_7")

    def test_empty_comm_event(self):
        self.assert_pass("empty_comm_event")

    def test_empty_comm_event_exact_subheader(self):
        self.assert_pass("empty_comm_event_8")

    def test_empty_input_report(self):
        self.assert_pass("empty_input_report")

    def test_comm_ready_event(self):
        self.assert_pass("comm_ready")

    def test_oversized_subpayload_frees_worker(self):
        self.assert_pass("oversized_subpayload")

    def test_receive_rearms_when_allocation_fails(self):
        self.assert_pass("alloc_fail_rearm")

    def test_init_zero_length_product_is_rejected(self):
        self.assert_pass("init_product_zero")

    def test_init_zero_length_product_does_not_read_tail(self):
        self.assert_pass("init_product_zero_walk")

    def test_init_empty_product_is_accepted(self):
        self.assert_pass("init_product_empty")

    def test_init_product_text_is_accepted(self):
        self.assert_pass("init_product_text")

    def test_init_unterminated_product_is_not_accepted(self):
        self.assert_pass("init_product_unterminated")

    def test_init_short_gpio_does_not_read_next_block(self):
        self.assert_pass("init_gpio_short")

    def test_init_gpio_request_is_stored(self):
        self.assert_pass("init_gpio_valid")

    def test_init_unterminated_interface_name_rejects_before_lookup(self):
        self.assert_pass("init_name_unterminated")

    def test_init_interface_name_is_looked_up(self):
        self.assert_pass("init_name_valid")

    def test_init_fifteen_character_name_is_looked_up(self):
        self.assert_pass("init_name_15")

    def test_init_stm_is_created(self):
        self.assert_pass("init_stm_creates")

    def test_init_truncated_header_rearms(self):
        self.assert_pass("init_header_truncated")

    def test_init_oversized_block_is_not_a_descriptor(self):
        self.assert_pass("init_block_oversized")

    def test_init_descriptor_stops_at_terminator(self):
        self.assert_pass("init_descriptor_terminator")

    def test_init_unknown_block_rearms(self):
        self.assert_pass("init_unknown_block")


if __name__ == "__main__":
    unittest.main()
