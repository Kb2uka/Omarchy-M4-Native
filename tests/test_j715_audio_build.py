# SPDX-License-Identifier: MIT
"""Preparation uses the real donor; compiler fixtures never qualify binaries."""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
DONOR = Path(os.environ.get("J715_DONOR_SOURCE", ROOT.parent / "donor-source"))
sys.path.insert(0, str(ROOT / "tools"))
import j715_sources as sources

SPEC = importlib.util.spec_from_file_location("j715_audio_build", ROOT / "tools/prepare-j715-audio-build.py")
audio = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audio
SPEC.loader.exec_module(audio)


class AudioBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lock = sources.SourceLock.read(ROOT / "audio/j715/source-lock.json")
        cls.originals = sources.local_sources(DONOR, cls.lock)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="audio-build-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.output = self.base / "new-output"
        self.environment = os.environ.copy()
        self.environment.update(TMPDIR=str(self.base), NODE_COMPILE_CACHE=str(self.base / "node"),
                                BUN_RUNTIME_TRANSPILER_CACHE_PATH=str(self.base / "bun"),
                                PYTHONDONTWRITEBYTECODE="1", GIT_MASTER="1")

    def cli(self, *args):
        return subprocess.run([sys.executable, "-B", str(ROOT / "tools/prepare-j715-audio-build.py"),
                               *map(str, args)], env=self.environment, check=False,
                              capture_output=True, text=True)

    def fixture(self):
        """Synthetic external prerequisites; the real source verifier is tested separately."""
        kernel, sysroot, cargo = (self.base / name for name in ("kernel", "sysroot", "cargo"))
        inventory = []
        for name in (".config", "Module.symvers", "include/generated/autoconf.h",
                     "include/config/auto.conf", "include/generated/utsrelease.h", "include/config/kernel.release"):
            path = kernel / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("fixture-only:" + name).encode())
            inventory.append(f"{sources.digest(path.read_bytes())}  {name}\n")
        (kernel / "Makefile").write_text("# fixture prepared kernel\n")
        exports = self.base / "exports"
        exports.write_bytes(b"fixture sound exports")
        library = sysroot / "usr/lib/fixture.so"
        library.parent.mkdir(parents=True)
        library.write_bytes(b"fixture ABI")
        for name in ("usr/lib/pkgconfig/alsa.pc", "usr/include/alsa/asoundlib.h",
                     "usr/lib/libasound.so", "usr/lib/libc.so", "usr/lib/crti.o", "usr/lib/crtn.o"):
            path = sysroot / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture development sysroot")
        crate = cargo / "registry/cache/fixture/fixture-1.0.crate"
        crate.parent.mkdir(parents=True)
        crate.write_bytes(b"fixture crate")
        index = cargo / "registry/index/fixture/config.json"
        index.parent.mkdir(parents=True)
        index.write_text("{}\n")
        replacements = {
            "audio/j713/kernel-inputs.sha256": "".join(inventory).encode(),
            "audio/j713/qualified.json": json.dumps({"abi_files": {"usr/lib/fixture.so": {
                "sha256": sources.digest(library.read_bytes()), "size": library.stat().st_size}}}).encode(),
            "audio/j713/speakersafetyd/Cargo.lock": (
                'version = 3\n[[package]]\nname = "fixture"\nversion = "1.0"\n'
                'source = "registry+https://github.com/rust-lang/crates.io-index"\n'
                f'checksum = "{sources.digest(crate.read_bytes())}"\n').encode(),
        }
        self.fixture_files = tuple(replace(f, data=replacements.get(f.pin.path, f.data))
                                   for f in self.originals)
        self.contract = json.loads(audio.CONTRACT.read_bytes())
        self.contract["sound_exports_sha256"] = sources.digest(exports.read_bytes())
        contract = self.base / "fixture-contract.json"
        contract.write_text(json.dumps(self.contract))
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(audio, "CONTRACT", contract).start()
        mock.patch.object(audio, "local_sources", return_value=self.fixture_files).start()
        return audio.BuildInputs(DONOR, kernel, exports, sysroot, cargo,
                                 "sha256:" + "a" * 64, "sha256:" + "b" * 64, self.output)

    def compiler(self, command):
        """Fake only the unavailable container engine, consuming its actual argv."""
        if command[1:3] == ["image", "inspect"]:
            return command[-1]
        self.assertEqual(command[command.index("--pull") + 1], "never")
        self.assertEqual(command[command.index("--network") + 1], "none")
        script = command[-1]
        self.assertIn("PYTHONDONTWRITEBYTECODE=1 GIT_MASTER=1", script)
        if "make -C" in script:
            directory = Path(next(arg[:-5] for arg in command if arg.endswith(":/job")))
            component = next(c for c in self.contract["components"]
                             if directory == self.output / "source" / c["source"])
            self.assertIn("M4_BOARD=j715", script)
            self.assertIn("J713_PROFILE=conservative-v1", script)
            for name in component["outputs"]:
                (directory / name).write_bytes(("fixture:" + component["source"] + "/" + name).encode())
        elif "cargo rustc" in script:
            self.assertIn("--features j715 --locked --offline", script)
            self.assertIn("cd /job/source/audio/j713/speakersafetyd;", script)
            for model in self.contract["models"]:
                self.assertEqual((self.output / "source" / model).read_bytes(), (DONOR / model).read_bytes())
            (self.output / "payload/j715/speakersafetyd-j715").write_bytes(b"fixture daemon")
        else:
            self.assertIn("cargo metadata", script)
            self.assertIn("--locked --offline", script)
            self.assertFalse(self.output.exists())
        return "fixture ABI/build-ID evidence only"

    def test_preparation_cli_retains_models_and_changes_only_mca(self):
        # Given: actual locked donor, not synthetic fixture source identities.
        before = {f.pin.path: sources.digest(f.data) for f in self.originals}
        # When
        result = self.cli("--source", DONOR, "--output", self.output)
        # Then
        self.assertEqual(result.returncode, 0, result.stderr)
        changed = {f.pin.path for f in self.originals
                   if sources.digest((self.output / f.pin.path).read_bytes()) != before[f.pin.path]}
        self.assertEqual(changed, {audio.MCA})
        receipt = json.loads((self.output / "audio-build-receipt.json").read_bytes())
        self.assertEqual(receipt["output_files"][audio.MCA],
                         sources.digest((self.output / audio.MCA).read_bytes()))
        self.assertEqual(receipt["patch_sha256"], sources.digest((ROOT / "patches-kernel/j713-audio/mca-bclk-width.patch").read_bytes()))
        self.assertFalse(receipt["hardware_qualified"])
        for match in re.finditer(r'include_str!\("([^"]+)"\)', (
                self.output / "audio/j713/speakersafetyd/src/j713.rs").read_text()):
            resolved = (self.output / "audio/j713/speakersafetyd/src" / match[1]).resolve()
            self.assertEqual(resolved.read_bytes(), (DONOR / resolved.relative_to(self.output)).read_bytes())
        self.assertEqual(sources.local_sources(DONOR, self.lock), self.originals)

    def test_changed_original_hash_is_refused_before_output(self):
        # Given
        source = self.base / "donor"
        shutil.copytree(DONOR, source, symlinks=True)
        (source / audio.MCA).write_bytes(b"substitution")
        # When
        result = self.cli("--source", source, "--output", self.output)
        # Then
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists())

    def test_existing_output_and_source_overlap_are_refused(self):
        # Given
        self.output.mkdir()
        (self.output / "owner").write_bytes(b"preserve")
        # When / Then
        for path in (self.output, DONOR / "new-audio-build"):
            with self.subTest(path=path):
                self.assertNotEqual(self.cli("--source", DONOR, "--output", path).returncode, 0)
        self.assertEqual((self.output / "owner").read_bytes(), b"preserve")

    def test_wrapper_refuses_real_missing_inputs_without_docker(self):
        # Given: a fake engine would raise if reached.
        args = [str(ROOT / "tools/build-j715-audio.sh"), str(DONOR),
                *[str(self.base / name) for name in ("kernel", "exports", "sysroot", "cargo")],
                "sha256:" + "a" * 64, "sha256:" + "b" * 64, str(self.output)]
        # When
        result = subprocess.run(["bash", *args], env={**self.environment, "M4_BOARD": "j713"},
                                check=False, capture_output=True, text=True)
        # Then
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Wrong build board", result.stderr)
        self.assertFalse(self.output.exists())

    def test_preparation_refuses_wrong_board_before_output(self):
        # Given
        self.environment["M4_BOARD"] = "j713"
        # When
        result = self.cli("--source", DONOR, "--output", self.output)
        # Then
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists())

    def test_assembled_build_stages_all_seven_original_module_names(self):
        # Given
        inputs = self.fixture()
        original = audio.tree_pins(inputs.cargo), audio.tree_pins(inputs.sysroot)
        # When
        with mock.patch.object(audio, "run", side_effect=self.compiler) as engine:
            audio.build(inputs)
        # Then
        receipt = json.loads((self.output / "build-receipt.json").read_bytes())
        names = {name for c in self.contract["components"] for name in c["outputs"]}
        self.assertEqual(set(receipt["outputs"]), names | {"speakersafetyd-j715"})
        self.assertEqual(engine.call_count, 7)  # Two inspections, metadata, three modules, daemon.
        for component in self.contract["components"]:
            for name in component["outputs"]:
                expected = ("fixture:" + component["source"] + "/" + name).encode()
                self.assertEqual((self.output / "payload/j715" / name).read_bytes(), expected)
        self.assertEqual((audio.tree_pins(inputs.cargo), audio.tree_pins(inputs.sysroot)), original)
        self.assertFalse(receipt["rebuilt_identical"])

    def test_local_prerequisite_failures_never_contact_docker(self):
        # Given
        inputs = self.fixture()
        mutations = [replace(inputs, kernel_image="moving:tag"), replace(inputs, daemon_image="latest"),
                     replace(inputs, kernel=self.base / "missing-kernel"),
                     replace(inputs, exports=self.base / "missing-exports"),
                     replace(inputs, sysroot=self.base / "missing-sysroot"),
                     replace(inputs, cargo=self.base / "missing-cache")]
        # When / Then
        for invalid in mutations:
            with self.subTest(invalid=invalid), mock.patch.object(audio, "run") as engine:
                with self.assertRaises((sources.SourceError, OSError)):
                    audio.build(invalid)
                engine.assert_not_called()
                self.assertFalse(self.output.exists())

    def test_corrupt_kernel_exports_and_cache_are_refused(self):
        # Given
        inputs = self.fixture()
        targets = [inputs.kernel / ".config", inputs.exports,
                   next((inputs.cargo / "registry/cache").glob("*/*.crate"))]
        # When / Then
        for target in targets:
            original = target.read_bytes()
            target.write_bytes(b"corrupt")
            with self.subTest(target=target), mock.patch.object(audio, "run") as engine:
                with self.assertRaises(sources.SourceError):
                    audio.build(inputs)
                engine.assert_not_called()
            target.write_bytes(original)

    def test_missing_local_builder_and_offline_resolution_fail_before_output(self):
        # Given
        inputs = self.fixture()
        # When / Then
        for effect in (["wrong-image"], [inputs.kernel_image, inputs.daemon_image,
                                       subprocess.CalledProcessError(1, ["fixture-offline-probe"])]):
            with self.subTest(effect=effect), mock.patch.object(audio, "run", side_effect=effect):
                with self.assertRaises((sources.SourceError, subprocess.CalledProcessError)):
                    audio.build(inputs)
                self.assertFalse(self.output.exists())

    def test_missing_module_is_not_a_successful_payload(self):
        # Given
        inputs = self.fixture()
        def incomplete(command):
            result = self.compiler(command)
            if command[1] == "run" and "make -C" in command[-1]:
                next((self.output / "source/audio/j713/kernel").glob("*.ko")).unlink()
            return result
        # When / Then
        with (mock.patch.object(audio, "run", side_effect=incomplete),
              self.assertRaises(sources.SourceError)):
            audio.build(inputs)
        self.assertFalse((self.output / "build-receipt.json").exists())

    def test_real_makefiles_select_only_j715_with_actual_build_flags(self):
        # Given: original source Makefiles and board headers.
        spec = json.loads(audio.CONTRACT.read_bytes())
        for component in spec["components"]:
            # When: ask make for its evaluated compiler flags; no kernel build.
            path = DONOR / component["source"]
            result = subprocess.run(["make", "-s", "-f", str(path / "Makefile"),
                                     "M4_BOARD=j715", "J713_PROFILE=conservative-v1",
                                     f"src={path}", "--eval", "print:;@echo $(ccflags-y)", "print"],
                                    env=self.environment, check=False, capture_output=True, text=True)
            # Then: preprocess the selected board rather than pinning header prose.
            self.assertEqual(result.returncode, 0, result.stderr)
            compiled = subprocess.run(["cc", "-dM", "-E", "-x", "c", *result.stdout.split(), "-"],
                                      input="", env=self.environment, check=False, capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            macros = dict(re.findall(r"^#define (M4_BOARD|M4_NAMP) (.+)$", compiled.stdout, re.MULTILINE))
            self.assertEqual(macros, {"M4_BOARD": '"apple,j715"', "M4_NAMP": "6"})


if __name__ == "__main__":
    unittest.main()
