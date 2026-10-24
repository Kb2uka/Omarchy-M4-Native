# SPDX-License-Identifier: MIT
"""Offline assembly tests; synthetic compiler bytes never qualify hardware."""
import argparse
import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_j715_backlight_dtb as dt_fixture

ROOT = Path(__file__).resolve().parents[1]
DONOR = Path(os.environ.get("J715_DONOR_SOURCE", ROOT.parent / "donor-source"))
sys.path.insert(0, str(ROOT / "tools"))
SPEC = importlib.util.spec_from_file_location("j715_package", ROOT / "tools/package-j715-native.py")
package = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(package)


class NativePackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lock = package.SourceLock.read(ROOT / "audio/j715/source-lock.json")
        cls.sources = package.local_sources(DONOR, cls.lock)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="n6-package-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.output = self.base / "stage"
        self.build = self.base / "build"
        self.build.mkdir()
        self.builder = package.load("prepare-j715-audio-build")
        self.builder.prepare(DONOR, self.build / "source")
        spec = package.read(ROOT / "audio/j715/build-contract.json")
        original = {f.pin.path: f.data for f in self.sources}
        payload = self.build / spec["payload_namespace"]
        payload.mkdir(parents=True)
        names = {n for c in spec["components"] for n in c["outputs"]} | {spec["daemon_output"]}
        for name in names:
            (payload / name).write_bytes(("SYNTHETIC COMPILER OUTPUT: " + name).encode())
        abi = json.loads(original[spec["abi_reference"]])["abi_files"]
        self.receipt = {
            "schema": 1, "board": "apple,j715", "profile": spec["profile"],
            "hardware_qualified": False, "rebuilt_identical": False,
            "commands": [self.builder.container("sha256:" + "a" * 64, [], command) for command in (
                "cargo metadata --locked --offline",
                *(["make ARCH=arm64 LLVM=1 LOCALVERSION= KCFLAGS=-Werror M4_BOARD=j715"] * 3),
                "cargo rustc --features j715 --locked --offline")],
            "kernel_release": "fixture-kernel-not-qualified",
            "kernel_inputs": dict(line.split()[::-1] for line in
                                  original[spec["kernel_inventory"]].decode().splitlines()),
            "sound_exports_sha256": spec["sound_exports_sha256"],
            "toolchain_image_ids": ["sha256:" + "a" * 64, "sha256:" + "b" * 64],
            "abi_build_ids": {name: "synthetic inspection only" for name in
                              [*(c["source"] for c in spec["components"]), "daemon"]},
            "source_receipt_sha256": package.digest(
                (self.build / "source/audio-build-receipt.json").read_bytes()),
            "runtime_profile_sha256": package.digest(original["audio/j715/j715.conf"]),
            "external_inputs": {"sysroot": {
                n: "link:" + p["symlink"] if "symlink" in p else p["sha256"] for n, p in abi.items()}},
            "outputs": {n: package.pin((payload / n).read_bytes()) for n in names},
        }
        self.receipt_path = self.build / "build-receipt.json"
        self.save()
        self.args = argparse.Namespace(audio_build=self.receipt_path, donor_source=DONOR,
                                       output=self.output, display_build=None,
                                       display_loader=None, display_base=None)
        self.environment = {**os.environ, "TMPDIR": str(self.base),
                            "NODE_COMPILE_CACHE": str(self.base / "node"),
                            "BUN_RUNTIME_TRANSPILER_CACHE_PATH": str(self.base / "bun"),
                            "PYTHONDONTWRITEBYTECODE": "1", "GIT_MASTER": "1"}

    def save(self):
        self.receipt_path.write_text(json.dumps(self.receipt))

    def cli(self, *extra):
        return subprocess.run(
            [sys.executable, "-B", str(ROOT / "tools/package-j715-native.py"),
             "--audio-build", str(self.receipt_path), "--donor-source", str(DONOR),
             "--output", str(self.output), *map(str, extra)],
            env=self.environment, capture_output=True, text=True, check=False)

    def test_audio_cli_inventory_binds_every_staged_file(self):
        # Given: real source preparation and explicitly synthetic compiler output.
        # When
        result = self.cli()
        # Then
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = package.read(self.output / "payload-receipt.json")
        paths = {"/" + p.relative_to(self.output).as_posix() for p in self.output.rglob("*")
                 if p.is_file() and p.name != "payload-receipt.json"}
        self.assertEqual(set(receipt["inventory"]), paths)
        for name, record in receipt["inventory"].items():
            path = self.output / name.lstrip("/")
            self.assertEqual(package.pin(path.read_bytes()),
                             {k: record[k] for k in ("size", "sha256")})
            self.assertEqual(format(path.stat().st_mode & 0o777, "04o"), record["mode"])
            if name.endswith(".ko"):
                self.assertTrue(name.startswith("/usr/lib/omarchy-m4/j715/audio/"))
        self.assertEqual(receipt["features"], {"audio": "prepared", "display": "omitted"})
        self.assertTrue(all(receipt[k] is False for k in ("hardware_qualified", "activated", "qualification_receipt_present")))

    def test_both_profiles_retain_canonical_graph_and_filter_consumers(self):
        # Given
        renderer = package.load("m4-j715-audio-profile")
        # When
        package.assemble(self.args)
        # Then
        for speaker_id in (0, 3):
            report = renderer.IdReport.parse(json.dumps({
                "board": True, "chip": "gpiochip0",
                "pins": {"4": speaker_id & 1, "13": speaker_id >> 1},
                "speaker_id": speaker_id}).encode())
            expected = renderer.candidate(self.sources, report, self.lock)
            root = self.output / package.BASE / f"audio/profiles/{speaker_id}"
            renderer.verify_profile(root, expected)
            graph = package.read(root / "usr/share/asahi-audio/j715/graph.json")
            for node in graph["filter.graph"]["nodes"][2:]:
                self.assertTrue((root / node["config"]["filename"].lstrip("/")).is_file())
        for item in self.sources:
            if item.pin.path.startswith(("audio/j715/filters/", "audio/j715/owner-fir/")):
                self.assertEqual((self.output / package.BASE / "audio/original" /
                                  item.pin.path).read_bytes(), item.data)

    def test_runtime_import_and_service_paths_resolve_in_staging(self):
        # Given
        contract = package.read(ROOT / "audio/j715/runtime-contract.json")
        # When
        package.assemble(self.args)
        # Then
        base = self.output / package.BASE / "audio"
        for source in contract["runtime_python_sources"]:
            self.assertEqual((base / Path(source).name).read_bytes(), (ROOT / source).read_bytes())
        spec = importlib.util.spec_from_file_location("staged_session", base / "m4-j715-audio-session.py")
        staged = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(staged)
        self.assertTrue(callable(staged.load("m4-j715-audio-profile.py").verify_profile))
        self.assertEqual(package.read(base / "j715/profile-contract.json"),
                         package.read(ROOT / "audio/j715/profile-contract.json"))
        for name in ("m4-j715-audio-register", "speakersafetyd-j715"):
            unit = self.output / f"usr/lib/systemd/system/{name}.service"
            for line in unit.read_text().splitlines():
                if line.startswith("Exec"):
                    argv = line.split("=", 1)[1].split()
                    for arg in argv:
                        if arg.startswith("/usr/lib/omarchy-m4/"):
                            self.assertTrue((self.output / arg.lstrip("/")).exists(), arg)
        self.assertFalse((self.output / "usr/lib/systemd/user").exists())

    def test_corrupt_or_missing_binary_refuses_without_creating_output(self):
        # Given
        path = self.build / "payload/j715/apple-admac.ko"
        path.write_bytes(b"corrupt")
        # When
        result = self.cli()
        # Then
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists())

    def test_wrong_board_source_patch_abi_and_build_identities_refuse(self):
        # Given
        original = json.dumps(self.receipt)
        mutations = [("board", "apple,j713"), ("source_receipt_sha256", "0" * 64),
                     ("runtime_profile_sha256", "0" * 64), ("kernel_inputs", {}),
                     ("toolchain_image_ids", ["moving:tag"]), ("outputs", {}),
                     ("external_inputs", {"sysroot": {}})]
        for key, value in mutations:
            with self.subTest(key=key):
                self.receipt = json.loads(original)
                self.receipt[key] = value
                self.save()
                # When
                result = self.cli()
                # Then
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.output.exists())

    def test_preparation_receipt_cannot_substitute_for_compilation(self):
        # Given
        self.receipt_path.write_bytes((self.build / "source/audio-build-receipt.json").read_bytes())
        # When
        result = self.cli()
        # Then
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists())

    def test_existing_live_overlap_and_symlink_outputs_refuse(self):
        # Given
        self.output.mkdir()
        marker = self.output / "owner"
        marker.write_bytes(b"preserve")
        link = self.base / "link"
        link.symlink_to(self.base, target_is_directory=True)
        for output in (self.output, Path("/"), Path("/usr/n6-forbidden"), Path("/tmp/../usr/n6-forbidden"),
                       DONOR / "new", link / "new", self.build / "new"):
            with self.subTest(output=output):
                self.args.output = output
                # When / Then
                with self.assertRaises(ValueError):
                    package.assemble(self.args)
        self.assertEqual(marker.read_bytes(), b"preserve")

    def test_missing_or_synthetic_display_profile_refuses_before_output(self):
        # Given: retain the explicit synthetic identity; never set qualified=true.
        (self.base / "profile.json").write_bytes(
            (ROOT / "tests/fixtures/j715-display-synthetic.json").read_bytes())
        module = self.base / "module.json"
        loader = self.base / "loader-receipt.json"
        module.write_text("{}")
        loader.write_text("{}")
        for extra in ([], ["--display-loader", loader, "--display-base", self.base / "absent-base.dtb"]):
            # When
            result = self.cli("--display-build", module, *extra)
            # Then
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(self.output.exists())

    def test_changed_preparation_patch_and_source_closure_refuse(self):
        # Given
        path = self.build / "source/audio-build-receipt.json"
        original = path.read_bytes()
        for field, value in (("patch_sha256", "0" * 64), ("output_files", {}),
                             ("integration_sources", {}), ("original_files", [])):
            with self.subTest(field=field):
                prepared = json.loads(original)
                prepared[field] = value
                path.write_text(json.dumps(prepared))
                self.receipt["source_receipt_sha256"] = package.digest(path.read_bytes())
                self.save()
                # When
                result = self.cli()
                # Then
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.output.exists())

    def native_receipt(self):
        """Explicitly synthetic ELF/tool evidence, with real source and ABI contract."""
        native = package.j715_native_audio_receipt
        reviewed = native.contract()
        spec = package.read(ROOT / "audio/j715/build-contract.json")
        paths = {n: "/synthetic-native/" + n for n in (
            "work", "kernel", "exports", "llvm", "rust", "sysroot")}
        self.receipt.pop("toolchain_image_ids")
        self.receipt.update(
            backend=native.BACKEND, repeatable=True, native_contract_sha256=native.sha(native.CONTRACT),
            native_integration_sources=reviewed["integration_sources"], native_paths=paths,
            host_helpers=reviewed["host_helpers"],
            environment=native.build_environment(paths, {
                "TMPDIR": "/synthetic-native/tmp", "NODE_COMPILE_CACHE": "/synthetic-native/node",
                "BUN_RUNTIME_TRANSPILER_CACHE_PATH": "/synthetic-native/bun"}),
            commands=native.plan(paths, spec), passes=[self.receipt["outputs"]] * 2,
            rust_package_sha256=reviewed["rust_package_sha256"],
            rust_signature="[GNUPG:] VALIDSIG synthetic-boundary-only",
            kernel_inputs=reviewed["kernel"]["inputs"], kernel_release=reviewed["kernel"]["release"],
            kernel_build_version=reviewed["kernel"]["build_version"],
            kernel_gnu_build_id=reviewed["kernel"]["gnu_build_id"],
            kernel_elf={"argv": [paths["llvm"] + "/lib/llvm-21/bin/llvm-readelf", "-n",
                                 paths["kernel"] + "/vmlinux"], "returncode": 0,
                        "stdout": "Build ID: " + reviewed["kernel"]["gnu_build_id"], "stderr": ""},
            toolchains={n: {"sha256": p, "version": reviewed["llvm_version"] + "\n" +
                           reviewed["rust_version"] + "\nhost: " + spec["target"],
                           "closure": {"synthetic-runtime": "a" * 64}}
                        for n, p in reviewed["tool_executables"].items()})
        self.receipt["toolchains"].update({
            "host:" + n: {"sha256": "a" * 64, "version": "SYNTHETIC HOST TOOL",
                         "closure": {"synthetic-runtime": "a" * 64}} for n in native.SYSTEM_TOOLS})
        self.receipt["command_results"] = [
            {"argv": c, "returncode": 0, "stdout": "SYNTHETIC COMPILER TRANSCRIPT", "stderr": ""}
            for c in self.receipt["commands"]]
        self.receipt["abi_build_ids"] = {
            n: {"elf": "Machine: AArch64\nBuild ID: 1234abcd\nld-linux-aarch64.so.1\n" +
                "".join(f"Shared library: [{lib}]\n" for lib in reviewed["needed"]),
                "vermagic": self.receipt["kernel_release"] + " SMP preempt mod_unload aarch64"}
            for n in self.receipt["outputs"]}
        self.receipt["external_inputs"]["sysroot"] = {
            n: "link:" + p["symlink"] if "symlink" in p else p["sha256"]
            for n, p in reviewed["abi_files"].items()}
        evidence = self.build / "evidence"
        evidence.mkdir()
        self.receipt["linker_evidence"] = {}
        for name in ("pass1.map", "pass2.map", "pass1.link", "pass2.link"):
            data = b"SYNTHETIC LINKER EVIDENCE: libasound.so libc.so"
            (evidence / name).write_bytes(data)
            self.receipt["linker_evidence"][name] = package.pin(data)
        self.save()

    def test_explicit_synthetic_native_receipt_stages_inactive_payload(self):
        # Given: synthetic compiler boundary is labeled, never hardware qualified.
        self.native_receipt()
        # When
        result = self.cli()
        # Then
        self.assertEqual(result.returncode, 0, result.stderr)
        staged = package.read(self.output / "payload-receipt.json")
        self.assertFalse(staged["activated"])
        self.assertFalse(staged["hardware_qualified"])
        copied = package.read(self.output / package.BASE / "audio/build-receipt.json")
        self.assertEqual(copied, self.receipt)
        self.assertEqual(len(copied["outputs"]), 8)
        self.assertTrue((self.output / package.BASE / "audio/native-build-contract.json").is_file())
        for name in copied["linker_evidence"]:
            self.assertTrue((self.output / package.BASE / "audio/evidence" / name).is_file())

    def test_native_receipt_tampering_refuses_before_staging(self):
        # Given
        self.native_receipt()
        mutations = {
            "backend": "unknown", "native_contract_sha256": "0" * 64,
            "native_integration_sources": {}, "commands": [["docker", "run"]],
            "command_results": [], "environment": {}, "external_inputs": {"sysroot": {}},
            "passes": [], "toolchains": {}, "rust_package_sha256": "0" * 64,
            "rust_signature": "", "host_helpers": {}, "abi_build_ids": {},
            "linker_evidence": {}, "hardware_qualified": True, "rebuilt_identical": True,
            "repeatable": False, "kernel_inputs": {}, "runtime_profile_sha256": "0" * 64,
            "kernel_gnu_build_id": "0" * 40, "kernel_elf": {},
        }
        original = json.dumps(self.receipt)
        # When / Then
        for key, value in mutations.items():
            self.receipt = json.loads(original)
            self.receipt[key] = value
            self.save()
            with self.subTest(field=key):
                result = self.cli()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.output.exists())

    def test_relabeling_docker_receipt_as_native_is_refused(self):
        # Given: real legacy validation fixture, not native commands.
        self.receipt["backend"] = package.j715_native_audio_receipt.BACKEND
        self.save()
        # When
        result = self.cli()
        # Then
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists())

    def test_native_loader_needed_entry_is_bound_to_pinned_runtime(self):
        self.native_receipt()
        daemon = self.receipt["abi_build_ids"]["speakersafetyd-j715"]
        daemon["elf"] = daemon["elf"].replace(
            "Shared library: [ld-linux-aarch64.so.1]\n", "")
        self.save()
        result = self.cli()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists())

    def test_docker_command_and_abi_checks_remain_strict(self):
        # Given
        original = json.dumps(self.receipt)
        changes = [(1, -1, "make M4_BOARD=j713"), (0, 0, "native"),
                   (4, -1, "cargo rustc --features j715")]
        # When / Then
        for command, argument, value in changes:
            self.receipt = json.loads(original)
            self.receipt["commands"][command][argument] = value
            self.save()
            result = self.cli()
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(self.output.exists())

    def test_native_binary_and_linker_evidence_tampering_refuses(self):
        # Given
        self.native_receipt()
        for path in (self.build / "payload/j715/apple-admac.ko", self.build / "evidence/pass1.link"):
            before = path.read_bytes()
            path.write_bytes(b"TAMPERED")
            # When / Then
            with self.subTest(path=path):
                result = self.cli()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.output.exists())
            path.write_bytes(before)

    def test_display_adapter_keeps_owner_schema_and_key_consumers_together(self):
        # Given: synthetic typed profile and unavailable stock-compiler boundary.
        fixture = dt_fixture.DisplayDT()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        profiles = package.load("j715_display_profile")
        (self.base / "profile.json").write_bytes(
            (ROOT / "tests/fixtures/j715-display-synthetic.json").read_bytes())
        (self.base / "evidence").mkdir()
        for entry in package.read(self.base / "profile.json")["evidence"].values():
            (self.base / entry["path"]).write_bytes(b"synthetic boundary fixture only")
        lock = package.SourceLock.read(ROOT / "display/j715/source-lock.json")
        source = {"source_lock": json.loads(lock.raw), "source_lock_sha256": package.digest(lock.raw),
                  "commit": lock.commit, "profile_sha256": fixture.profile.digest, "hardware_success": False,
                  "files": {s.pin.path: s.pin.sha256 for s in package.local_sources(ROOT, lock)}}
        source_path = self.base / "source-receipt.json"
        source_path.write_text(json.dumps(source))
        module_name = "j715_dcp_backlight.ko"
        for receipt_name, names in (("module", [module_name]), ("loader-receipt", [
                "stage2-stock-m1n1.bin", "stage2-j715-hidden-m1n1.bin", "stage2-j715-shown-m1n1.bin"])):
            for name in names:
                (self.base / name).write_bytes(("synthetic " + name).encode())
            record = {"profile_sha256": fixture.profile.digest, "hardware_success": False,
                      "comparison": "not-rebuilt-identical", "builder_image": "sha256:" + "a" * 64,
                      "integration_sources": {n: package.digest((ROOT / n).read_bytes()) for n in
                          package.read(ROOT / "packaging/j715-native/channel-paths.json")["display_integration_sources"]},
                      "patch_sha256": package.digest((ROOT / "display/j715/m1n1-j715-handoff.patch").read_bytes()),
                      "source_receipt_sha256": package.digest(source_path.read_bytes()),
                      "outputs": {n: package.pin((self.base / n).read_bytes()) for n in names},
                      "module_sha256": package.digest((self.base / module_name).read_bytes()),
                      "module_bytes": (self.base / module_name).stat().st_size,
                      "kernel_release": "fixture", "kernel_inputs": {"fixture": "fixture"},
                      "abi_and_build_notes": "synthetic"}
            if receipt_name == "loader-receipt":
                record["outputs"]["stage2-stock-m1n1.bin"]["sha256"] = "7431196da2fd48681b262f6f605984f82d46ce66bf7ba56cd64118358684a2c7"
            (self.base / (receipt_name + ".json")).write_text(json.dumps(record))
        self.args.display_build, self.args.display_loader = self.base / "module.json", self.base / "loader-receipt.json"
        self.args.display_base = fixture.base
        actual_load = package.load
        # When: isolate qualification/compiled bytes, never change fixture fields.
        with (patch.object(profiles, "qualified", return_value=fixture.profile),
              patch.object(package, "load", side_effect=lambda n: profiles if n == "j715_display_profile" else actual_load(n)),
              patch.object(package, "checked", side_effect=lambda p, _: p.read_bytes())):
            current = package.read(self.args.display_loader)
            legacy = copy.deepcopy(current)
            legacy["outputs"]["stage2-stock-m1n1.bin"]["sha256"] = "3a3f7a64c65fc366d5ac9aece3e1bee64119be012aa1ec5b15d2743c6f91fce4"
            self.args.display_loader.write_text(json.dumps(legacy))
            with self.assertRaisesRegex(ValueError, "Display binary pins differ"):
                package.display(self.args)
            self.args.display_loader.write_text(json.dumps(current))
            files = package.display(self.args)
        # Then: execute the staged owner import and inspect actual managed bindings.
        for name, data in files.items():
            path = self.output / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        owner = self.output / package.BASE / "backlight/tools/m4-j715-backlight.py"
        spec = importlib.util.spec_from_file_location("staged_display", owner)
        staged = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(staged)
        self.assertEqual(staged.PROFILE, package.BASE + "backlight/profile.json")
        self.assertIn("/usr/bin/python3 -B /" + package.BASE + "backlight/tools/m4-j715-backlight.py brightness", staged.BINDS)


if __name__ == "__main__":
    unittest.main()
