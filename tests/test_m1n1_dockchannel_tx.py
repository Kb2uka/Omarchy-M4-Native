# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""m1n1's dockchannel console must never hang the boot (issue #82).

Compiles m1n1's real iodev.c and dockchannel_uart.c, as shipped in alpha.1 and
with patches-m1n1/dockchannel-tx-bound.patch, against a simulated TX FIFO.
"""
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
M1N1 = ROOT / "patches-m1n1"
BASE = M1N1 / "base/src"
PATCH = M1N1 / "dockchannel-tx-bound.patch"
FIXTURE = Path(__file__).with_name("m1n1_dockchannel_tx_fixture.c")
STUBS = ("adt.h", "memory.h", "string.h", "types.h", "utils.h", "vsprintf.h")

# Per-file hashes from the published alpha.1 inventories
# (companion source/m1n1-stage1-files.json and m1n1-stage2-files.json agree).
PUBLISHED = {
    "dockchannel_uart.c": "3612d461d38afb42ab60b953f0509b8f22c83305ccfa7788ff0b3770b6172e09",
    "iodev.c": "6f6c633a7cf842444ae17fc72d3b8ae679f3998ffdc6a96942454c446e39a702",
    "iodev.h": "46edd5802ea95b66c0a54cd8b9ef5ae892dd11b6fd0fbfca6484aff076c0c180",
}
CASES = ("undrained", "slow_drain", "recover", "fast_host_pause", "slow_host_pause", "consumer_vanishes", "secondary")

# Each mutant undoes one part of the fix; the named case must catch it.
MUTANTS = {
    "no_timeout": ("            if (timeout_expired(timeout)) {", "            if (timeout_expired(timeout) && false) {",
                   "undrained"),
    "no_latch_check": ("        if (tx_stalled)\n            return;\n", "", "undrained"),
    "latch_never_set": ("                tx_stalled = true;\n", "", "undrained"),
    "latch_never_cleared": ("    tx_stalled = false;\n    tx_free", "    tx_free", "recover"),
    "zero_timeout": ("#define TX_TIMEOUT_US          100000", "#define TX_TIMEOUT_US          0", "slow_drain"),
    "longer_timeout": ("#define TX_TIMEOUT_US          100000", "#define TX_TIMEOUT_US          150000", "undrained"),
    "consumer_never_seen": ("    if (space > tx_free)\n        tx_consumer = true;\n", "", "fast_host_pause"),
    "expectation_never_updated": ("    tx_free = space - 1;\n", "", "fast_host_pause"),
    "short_consumer_timeout": ("#define TX_CONSUMER_TIMEOUT_US 2000000", "#define TX_CONSUMER_TIMEOUT_US 100000",
                               "fast_host_pause"),
    "wait_drain_not_seen": ("        }\n        tx_consumer = true;\n    }\n", "        }\n    }\n", "slow_host_pause"),
    "unbounded_consumer_wait": ("#define TX_CONSUMER_TIMEOUT_US 2000000", "#define TX_CONSUMER_TIMEOUT_US 0xffffffff",
                                "consumer_vanishes"),
    "secondary_waits": ("        if (tx_stalled)\n            return;\n", "        if (tx_stalled && is_boot_cpu())\n            return;\n",
                        "secondary"),
}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class M1n1DockchannelTxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="m1n1-dctx-")
        cls.work = Path(cls.temp.name)
        stubs = cls.work / "stubs"
        stubs.mkdir()
        for name in STUBS:
            (stubs / name).write_text("/* provided by the fixture */\n")
        cls.after = cls.work / "after"
        shutil.copytree(BASE, cls.after)
        subprocess.run(["patch", "-p2", "--no-backup-if-mismatch", "--forward", "-d", str(cls.after),
                        "-i", str(PATCH)], check=True, capture_output=True, text=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def build(self, name, src):
        exe = self.work / "bin" / name
        exe.parent.mkdir(exist_ok=True)
        subprocess.run(["cc", "-std=gnu11", "-O2", "-Wall", "-Wsign-compare", "-Wundef", "-Wunused-parameter",
                        "-Werror", "-iquote", str(src), "-iquote",
                        str(self.work / "stubs"), str(FIXTURE), "-o", str(exe)], check=True)
        return exe

    def run_case(self, exe, case):
        result = subprocess.run([str(exe), case], capture_output=True, text=True, timeout=60)
        return result.returncode, (result.stdout + result.stderr).strip()

    def test_base_is_the_published_alpha1_source(self):
        sums = dict(line.split()[::-1] for line in (M1N1 / "base/SHA256SUMS").read_text().splitlines())
        self.assertEqual(sums, {"src/" + p.name: sha256(p) for p in BASE.iterdir()})
        for name, digest in PUBLISHED.items():
            self.assertEqual(sums["src/" + name], digest, name)

    def test_patch_changes_only_the_dockchannel_driver(self):
        changed = sorted(p.name for p in self.after.iterdir() if sha256(p) != sha256(BASE / p.name))
        self.assertEqual(changed, ["dockchannel_uart.c"])
        headers = [line for line in PATCH.read_text().splitlines() if line.startswith(("--- ", "+++ "))]
        self.assertEqual(headers, ["--- a/src/dockchannel_uart.c", "+++ b/src/dockchannel_uart.c"])

    def test_shipped_source_hangs_when_nothing_drains(self):
        code, output = self.run_case(self.build("before", BASE), "undrained")
        self.assertEqual(code, 3, output)
        self.assertTrue(output.startswith("HANG"), output)
        print(f"\nshipped undrained: {output}", flush=True)

    def test_patched_source_never_hangs_and_loses_nothing_when_drained(self):
        exe = self.build("after", self.after)
        for case in CASES:
            with self.subTest(case=case):
                code, output = self.run_case(exe, case)
                self.assertEqual(code, 0, output)
                self.assertTrue(output.startswith("OK " + case), output)
                print(f"\npatched {output}", flush=True)

    def test_shipped_source_already_passes_the_drained_cases(self):
        exe = self.build("before_drained", BASE)
        for case in ("slow_drain", "fast_host_pause", "slow_host_pause"):
            with self.subTest(case=case):
                code, output = self.run_case(exe, case)
                self.assertEqual(code, 0, output)

    def test_each_mutant_is_caught(self):
        source = (self.after / "dockchannel_uart.c").read_text()
        for name, (old, new, case) in MUTANTS.items():
            with self.subTest(mutant=name):
                self.assertEqual(source.count(old), 1, name)
                src = self.work / ("mutant_" + name)
                shutil.copytree(self.after, src)
                (src / "dockchannel_uart.c").write_text(source.replace(old, new))
                code, output = self.run_case(self.build("bin_" + name, src), case)
                self.assertNotEqual(code, 0, f"{name} survived {case}: {output}")
                print(f"\nmutant {name} caught by {case}: {output}", flush=True)


if __name__ == "__main__":
    unittest.main()
