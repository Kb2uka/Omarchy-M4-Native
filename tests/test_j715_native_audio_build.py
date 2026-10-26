# SPDX-License-Identifier: MIT
"""Synthetic native compiler boundary: no fixture output is a qualified binary."""
import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tarfile
import unittest
from pathlib import Path
from unittest import mock

import test_j715_audio_build as legacy

ROOT = legacy.ROOT
SPEC = importlib.util.spec_from_file_location("native_audio_build", ROOT / "tools/build-j715-native-audio.py")
audio = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audio)


class NativeAudioBuildTests(unittest.TestCase):
    def setUp(self):
        self.fixture = legacy.AudioBuildTests()
        self.fixture.originals = legacy.sources.local_sources(legacy.DONOR, legacy.sources.SourceLock.read(
            ROOT / "audio/j715/source-lock.json"))
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        old = self.fixture.fixture()
        self.base = self.fixture.base
        self.args = argparse.Namespace(
            donor=old.donor, kernel=old.kernel, exports=old.exports, sysroot=old.sysroot,
            cargo=old.cargo, output=old.output, llvm=self.base / "llvm", rust=self.base / "rust",
            rust_package=self.base / "rust.tar.xz", rust_signature=self.base / "rust.sig",
            rust_keyring=self.base / "trusted.gpg")
        self.reviewed = json.loads(audio.native.CONTRACT.read_bytes())
        self.reviewed["kernel"] = {
            "inputs": legacy.audio.kernel_inputs(old.kernel, next(
                f.data for f in self.fixture.fixture_files if f.pin.path ==
                "audio/j713/kernel-inputs.sha256")),
            "release": (old.kernel / "include/config/kernel.release").read_text().strip(),
            "build_version": "SYNTHETIC KERNEL #19",
            "gnu_build_id": "1234abcd",
        }
        (old.kernel / "include/generated/utsversion.h").write_text("SYNTHETIC KERNEL #19")
        (old.kernel / "vmlinux").write_bytes(b"SYNTHETIC KERNEL ELF")
        self.reviewed["abi_files"] = json.loads(next(
            f.data for f in self.fixture.fixture_files if f.pin.path ==
            "audio/j713/qualified.json"))["abi_files"]
        for name in self.reviewed["tool_executables"]:
            kind, path = name.split(":")
            path = getattr(self.args, kind) / path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("SYNTHETIC TOOL: " + name).encode())
            path.chmod(0o755)
            self.reviewed["tool_executables"][name] = audio.native.sha(path)
        for name in self.reviewed["host_helpers"]:
            path = old.kernel / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"SYNTHETIC HOST HELPER")
            path.chmod(0o755)
            self.reviewed["host_helpers"][name] = audio.native.sha(path)
        (self.args.rust / "tools").symlink_to("bin", target_is_directory=True)
        with tarfile.open(self.args.rust_package, "w:xz") as archive:
            archive.add(self.args.rust, arcname="usr")
        self.reviewed["rust_package_sha256"] = audio.native.sha(self.args.rust_package)
        for path in (self.args.rust_signature, self.args.rust_keyring):
            path.write_bytes(b"SYNTHETIC SIGNATURE BOUNDARY")
        self.library = self.base / "runtime.so"
        self.library.write_bytes(b"SYNTHETIC TOOL RUNTIME")
        contract = self.base / "native-contract.json"
        self.reviewed["integration_sources"] = {
            n: audio.native.sha(ROOT / n) for n in self.reviewed["integration_sources"]}
        contract.write_text(json.dumps(self.reviewed))
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(audio.native, "CONTRACT", contract).start()
        mock.patch.object(audio, "n2", legacy.audio).start()
        mock.patch.object(audio, "local_sources", return_value=self.fixture.fixture_files).start()
        mock.patch.dict(os.environ, self.fixture.environment).start()
        self.compilations = []

    def compiler(self, command, environment):
        """Simulate only subprocess results, consuming the production command plan."""
        self.assertEqual(environment["PYTHONDONTWRITEBYTECODE"], "1")
        self.assertEqual(environment["GIT_MASTER"], "1")
        self.assertIn(str(self.args.rust / "lib"), environment["LD_LIBRARY_PATH"])
        stdout = ""
        if command[0] == "/usr/bin/stat":
            stdout = "tmpfs\n"
        elif command[0] == "/usr/bin/gpgv":
            stdout = "[GNUPG:] VALIDSIG synthetic-fixture-only\n"
        elif command[0] == "/usr/bin/ldd":
            stdout = f"libfixture => {self.library} (0x123)\n"
        elif command[-1] in ("--version", "-vV"):
            stdout = self.reviewed["llvm_version"] + "\n" + self.reviewed["rust_version"]
            stdout += "\nhost: aarch64-unknown-linux-gnu\n"
        elif command[0] == "/usr/bin/make":
            self.compilations.append(command)
            directory = Path(next(v[2:] for v in command if v.startswith("M=")))
            component = next(c for c in self.fixture.contract["components"]
                             if directory.as_posix().endswith(c["source"]))
            self.assertFalse(list(directory.glob("*.ko")))
            for name in component["outputs"]:
                (directory / name).write_bytes(("SYNTHETIC NATIVE OUTPUT: " + name).encode())
        elif command[1] == "metadata":
            self.assertFalse(self.args.output.exists())
            self.assertIn("--offline", command)
        elif command[1] == "rustc":
            self.compilations.append(command)
            target = Path(environment["CARGO_TARGET_DIR"])
            self.assertFalse(target.exists())
            path = target / self.fixture.contract["target"] / "release/speakersafetyd"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"SYNTHETIC NATIVE DAEMON")
            (target.parent / "link.map").write_bytes(b"SYNTHETIC LINK MAP")
            stdout = "libasound.so libc.so libgcc_s.so.1 libm.so.6\n"
        elif command[0] == "/usr/bin/modinfo":
            stdout = (self.args.kernel / "include/config/kernel.release").read_text().strip()
            stdout += " SMP preempt mod_unload aarch64\n"
        else:
            self.assertTrue(command[0].endswith("/llvm-readelf"))
            stdout = "Machine: AArch64\nBuild ID: 1234abcd\n"
            stdout += "Requesting program interpreter: /lib/ld-linux-aarch64.so.1\n"
            stdout += "".join(f"Shared library: [{n}]\n" for n in self.reviewed["needed"])
        return {"argv": command, "returncode": 0, "stdout": stdout, "stderr": ""}

    def test_two_clean_native_passes_retain_all_eight_outputs(self):
        # Given: explicitly synthetic external tool/archive/compiler identities.
        argv = ["build-j715-native-audio.py"]
        for name, value in vars(self.args).items():
            argv.extend(["--" + name.replace("_", "-"), str(value)])
        with (mock.patch.object(audio, "run", side_effect=self.compiler),
              mock.patch.object(sys, "argv", argv)):
            # When
            self.assertEqual(audio.main(), 0)
        # Then
        receipt = json.loads((self.args.output / "build-receipt.json").read_bytes())
        self.assertEqual(len(receipt["outputs"]), 8)
        self.assertEqual(len(self.compilations), 8)
        self.assertEqual(receipt["passes"], [receipt["outputs"], receipt["outputs"]])
        self.assertTrue(receipt["repeatable"])
        self.assertFalse(receipt["hardware_qualified"])
        self.assertFalse(receipt["rebuilt_identical"])
        self.assertNotIn("toolchain_image_ids", receipt)
        self.assertEqual(receipt["backend"], "native-prepared-external")
        self.assertFalse(Path(receipt["native_paths"]["work"]).exists())
        for command in self.compilations:
            if command[0] == "/usr/bin/make":
                for flag in ("ARCH=arm64", "LOCALVERSION=", "KCFLAGS=-Werror",
                             "J713_PROFILE=conservative-v1", "M4_BOARD=j715"):
                    self.assertIn(flag, command)
                self.assertIn("LLVM=" + str(self.args.llvm) + "/lib/llvm-21/bin/", command)
            else:
                self.assertIn("--locked", command)
                self.assertIn("--offline", command)
                self.assertIn("--release", command)
                self.assertEqual(command[command.index("--features") + 1], "j715")

    def test_wrong_or_missing_prerequisites_never_invoke_subprocess(self):
        # Given / When / Then
        targets = [self.args.kernel / ".config", self.args.exports, self.args.rust_package,
                   self.args.rust / "bin/rustc", self.args.llvm / "lib/llvm-21/bin/clang",
                   self.args.kernel / "scripts/basic/fixdep", self.library.parent / "sysroot/usr/lib/fixture.so",
                   next((self.args.cargo / "registry/cache").glob("*/*.crate"))]
        for path in targets:
            before = path.read_bytes()
            path.write_bytes(b"CORRUPT")
            with self.subTest(path=path), mock.patch.object(audio, "run") as run:
                with self.assertRaises(ValueError):
                    audio.build(self.args)
                run.assert_not_called()
                self.assertFalse(self.args.output.exists())
            path.write_bytes(before)
        self.args.kernel = self.base / "absent"
        with mock.patch.object(audio, "run") as run, self.assertRaises(OSError):
            audio.build(self.args)
        run.assert_not_called()

    def test_existing_overlap_live_and_symlink_outputs_refuse(self):
        # Given
        link = self.base / "link"
        link.symlink_to(self.base, target_is_directory=True)
        existing = self.base / "existing"
        existing.mkdir()
        # When / Then
        for output in (existing, self.args.donor / "new", self.args.kernel / "new",
                       self.args.sysroot / "new", link / "new", Path("/usr/native-forbidden")):
            self.args.output = output
            with self.subTest(output=output), mock.patch.object(audio, "run") as run:
                with self.assertRaises(ValueError):
                    audio.build(self.args)
                run.assert_not_called()

    def test_nonrepeatable_or_missing_output_has_no_success_receipt(self):
        # Given
        original = self.compiler
        def changed(command, environment):
            result = original(command, environment)
            if command[0] == "/usr/bin/make" and command[-1] == "modules" and len(self.compilations) > 4:
                directory = Path(next(v[2:] for v in command if v.startswith("M=")))
                next(directory.glob("*.ko")).write_bytes(b"CHANGED SECOND PASS")
            return result
        # When / Then
        with mock.patch.object(audio, "run", side_effect=changed), self.assertRaises(ValueError):
            audio.build(self.args)
        self.assertFalse((self.args.output / "build-receipt.json").exists())

    def test_missing_module_refuses_success_receipt(self):
        # Given
        original = self.compiler
        def incomplete(command, environment):
            result = original(command, environment)
            if command[0] == "/usr/bin/make" and command[-1] == "modules":
                directory = Path(next(v[2:] for v in command if v.startswith("M=")))
                next(directory.glob("*.ko")).unlink()
            return result
        # When / Then
        with mock.patch.object(audio, "run", side_effect=incomplete), self.assertRaises(ValueError):
            audio.build(self.args)
        self.assertFalse((self.args.output / "build-receipt.json").exists())

    def test_offline_graph_failure_precedes_output_and_compilation(self):
        # Given
        original = self.compiler
        def offline_failure(command, environment):
            if command[1] == "metadata":
                raise subprocess.CalledProcessError(1, command, stderr="SYNTHETIC MISSING INDEX ENTRY")
            return original(command, environment)
        # When / Then
        with mock.patch.object(audio, "run", side_effect=offline_failure), self.assertRaises(subprocess.SubprocessError):
            audio.build(self.args)
        self.assertEqual(self.compilations, [])
        self.assertFalse(self.args.output.exists())


if __name__ == "__main__":
    unittest.main()
