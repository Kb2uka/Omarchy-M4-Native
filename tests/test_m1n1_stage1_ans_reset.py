# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Stage 1 must not reset an M4's ANS through PMGR after stopping it (issue #82).

Compiles m1n1's real nvme.c, as shipped in alpha.1 and with
patches-m1n1/stage1-ans-no-reset.patch, against a simulated ANS, and checks the
order in which nvme_init() and nvme_shutdown() boot, sleep, reset and release it.
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
PATCH = M1N1 / "stage1-ans-no-reset.patch"
FIXTURE = Path(__file__).with_name("m1n1_nvme_ans_fixture.c")
STUBS = ("adt.h", "assert.h", "malloc.h", "pmgr.h", "rtkit.h", "sart.h", "string.h", "types.h", "utils.h")

# Per-file hashes from the published alpha.1 inventories
# (companion source/m1n1-stage1-files.json and m1n1-stage2-files.json agree).
PUBLISHED = {
    "nvme.c": "a99c813c9ce94873aaf02b8b4d92a1be07c80d1eaa631d3cdad9db43808d4d52",
    "nvme.h": "11f5ade3814c70e7d385395800148bbcebcfda277d843cce5b01dc6259662200",
}

RESET = "boot sleep reset:0.ANS reset:0.ANS2 free"
NO_RESET = "boot sleep free"
# Every case ends with ANS stopped and nvme torn down.
SHIPPED = {"m4": (RESET, "t8132"), "m1": (RESET, "t8103"),
           "m4_initfail": (RESET, "t8132"), "m1_initfail": (RESET, "t8103")}
PATCHED = {"m4": (NO_RESET, "t8132"), "m1": (RESET, "t8103"),
           "m4_initfail": (NO_RESET, "t8132"), "m1_initfail": (RESET, "t8103")}

OLD_RESET = ('    // Some machines call this ANS, some ANS2...\n'
             '    pmgr_reset(nvme_die, "ANS");\n'
             '    pmgr_reset(nvme_die, "ANS2");\n')
# Each mutant undoes one part of the fix; the named case must catch it.
MUTANTS = {
    "m4_still_resets": ("    if (nvme_type == NVME_T8132)\n        return;\n", "", "m4"),
    "no_reset_anywhere": ("    if (nvme_type == NVME_T8132)\n", "    if (nvme_type == NVME_T8132 || true)\n", "m1"),
    "shutdown_bypasses_helper": ("    nvme_reset_ans();\n    rtkit_free(nvme_rtkit);\n    sart_free",
                                 OLD_RESET + "    rtkit_free(nvme_rtkit);\n    sart_free", "m4"),
    "error_path_bypasses_helper": ("    nvme_reset_ans();\nout_rtkit:", OLD_RESET + "out_rtkit:", "m4_initfail"),
    "reset_before_sleep": ("    rtkit_sleep(nvme_rtkit);\n    nvme_reset_ans();\n    rtkit_free(nvme_rtkit);\n    sart_free",
                           "    nvme_reset_ans();\n    rtkit_sleep(nvme_rtkit);\n    rtkit_free(nvme_rtkit);\n    sart_free",
                           "m1"),
}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def expected(case, table):
    events, soc = table[case]
    return f"TRACE {events} type={soc} initialized=0 ans_running=0"


class M1n1Stage1AnsResetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="m1n1-ans-")
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
        subprocess.run(["cc", "-std=gnu11", "-O2", "-Wall", "-Wextra", "-Wsign-compare", "-Wundef",
                        "-Wunused-parameter", "-Werror", "-iquote", str(src), "-iquote", str(self.work / "stubs"),
                        str(FIXTURE), "-o", str(exe)], check=True)
        return exe

    def run_case(self, exe, case):
        result = subprocess.run([str(exe), case], capture_output=True, text=True, timeout=60)
        lines = result.stdout.strip().splitlines()
        return result.returncode, lines[-1] if lines else result.stderr.strip()

    def test_base_is_the_published_alpha1_source(self):
        for name, digest in PUBLISHED.items():
            self.assertEqual(sha256(BASE / name), digest, name)
        sums = dict(line.split()[::-1] for line in (M1N1 / "base/SHA256SUMS").read_text().splitlines())
        for name, digest in PUBLISHED.items():
            self.assertEqual(sums["src/" + name], digest, name)

    def test_patch_changes_only_nvme_c(self):
        changed = sorted(p.name for p in self.after.iterdir() if sha256(p) != sha256(BASE / p.name))
        self.assertEqual(changed, ["nvme.c"])
        headers = [line for line in PATCH.read_text().splitlines() if line.startswith(("--- ", "+++ "))]
        self.assertEqual(headers, ["--- a/src/nvme.c", "+++ b/src/nvme.c"])

    def test_shipped_source_resets_the_stopped_ans_on_m4(self):
        exe = self.build("before", BASE)
        for case in SHIPPED:
            with self.subTest(case=case):
                code, output = self.run_case(exe, case)
                self.assertEqual(code, 0, output)
                self.assertEqual(output, expected(case, SHIPPED))
                print(f"\nshipped {case}: {output}", flush=True)

    def test_patched_source_leaves_the_m4_ans_stopped_and_keeps_older_socs(self):
        exe = self.build("after", self.after)
        for case in PATCHED:
            with self.subTest(case=case):
                code, output = self.run_case(exe, case)
                self.assertEqual(code, 0, output)
                self.assertEqual(output, expected(case, PATCHED))
                print(f"\npatched {case}: {output}", flush=True)

    def test_each_mutant_is_caught(self):
        source = (self.after / "nvme.c").read_text()
        for name, (old, new, case) in MUTANTS.items():
            with self.subTest(mutant=name):
                self.assertEqual(source.count(old), 1, name)
                src = self.work / ("mutant_" + name)
                shutil.copytree(self.after, src)
                (src / "nvme.c").write_text(source.replace(old, new))
                code, output = self.run_case(self.build("bin_" + name, src), case)
                self.assertNotEqual((code, output), (0, expected(case, PATCHED)), f"{name} survived {case}")
                print(f"\nmutant {name} caught by {case}: {output}", flush=True)


if __name__ == "__main__":
    unittest.main()
