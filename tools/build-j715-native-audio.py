# SPDX-License-Identifier: MIT
"""Build only: explicit prepared native tools, offline inputs, two clean passes."""
import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from pathlib import Path

import j715_native_audio_receipt as native
from j715_sources import SourceLock, digest, local_sources, relative, require

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("_native_n2", ROOT / "tools/prepare-j715-audio-build.py")
n2 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = n2
SPEC.loader.exec_module(n2)


def run(command: list[str], environment: dict[str, str]):
    result = subprocess.run(command, env=environment, check=True, capture_output=True, text=True)
    return {"argv": command, "returncode": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr}


def preflight(args, spec, reviewed):
    n2.new_output(args.output, tuple(getattr(args, n) for n in (
        "donor", "kernel", "exports", "llvm", "rust", "cargo", "rust_package",
        "rust_signature", "rust_keyring")))
    require(not any(args.output.resolve().is_relative_to(Path("/") / n) for n in (
        "usr", "etc", "boot", "lib", "lib64", "var", "run", "sys", "proc", "dev", "bin", "sbin")),
        "Live output forbidden")
    require(not args.sysroot.resolve().is_relative_to(args.output.resolve()), "Output overlaps sysroot")
    require(args.sysroot == Path("/") or
            not args.output.resolve().is_relative_to(args.sysroot.resolve()), "Output overlaps sysroot")
    require(os.environ.get("M4_BOARD", "j715") == "j715", "Wrong build board")
    files = local_sources(args.donor, SourceLock.read(ROOT / spec["source_lock"]))
    original = {f.pin.path: f.data for f in files}
    pins = n2.kernel_inputs(args.kernel, native.kernel_inventory(reviewed))
    require((args.kernel / "include/config/kernel.release").read_text().strip() ==
            reviewed["kernel"]["release"] and reviewed["kernel"]["build_version"] in
            (args.kernel / "include/generated/utsversion.h").read_text(), "Native kernel build identity differs")
    require((args.kernel / "vmlinux").is_file(), "Matching target vmlinux required")
    require(native.sha(args.exports) == spec["sound_exports_sha256"], "Sound exports differ")
    require((ROOT / spec["patch"]).read_bytes() == (ROOT / spec["patch_reference"]).read_bytes(),
            "Patch differs from upstream")
    native.abi_pins(args.sysroot, reviewed)
    for name in ("usr/lib/pkgconfig/alsa.pc", "usr/include/alsa/asoundlib.h",
                 "usr/lib/libc.so", "usr/lib/crti.o", "usr/lib/crtn.o"):
        require((args.sysroot / name).is_file(), "Missing native development input: " + name)
    for name, pin in reviewed["host_helpers"].items():
        require(os.access(args.kernel / name, os.X_OK) and native.sha(args.kernel / name) == pin,
                "Missing or changed prepared native host helper")
    for package in tomllib.loads(original[spec["daemon_source"] + "/Cargo.lock"].decode())["package"]:
        if "source" in package:
            require(package["source"].startswith("registry+"), "Unsupported offline dependency")
            matches = list((args.cargo / "registry/cache").glob(
                "*/" + package["name"] + "-" + package["version"] + ".crate"))
            require(bool(matches) and all(native.sha(p) == package["checksum"] for p in matches),
                    "Offline crate absent or corrupt: " + package["name"])
    require(any((args.cargo / "registry/index").glob("*/*")), "Offline registry index absent")
    require(not any((args.cargo / n).exists() for n in ("config", "config.toml")),
            "Unreviewed Cargo configuration")
    require(all(not p.is_symlink() for p in args.cargo.rglob("*")), "Cargo cache contains links")
    require(all(os.access(Path("/usr") / n, os.X_OK) for n in native.SYSTEM_TOOLS),
            "Missing native build/inspection tool")
    for name, pin in reviewed["tool_executables"].items():
        kind, name = name.split(":")
        root = getattr(args, kind)
        path = root / relative(name)
        require(path.resolve().is_relative_to(root.resolve()) and native.sha(path) == pin and
                os.access(path, os.X_OK), "Native executable differs: " + name)
    require(native.sha(args.rust_package) == reviewed["rust_package_sha256"], "Rust package differs")
    native.rust_archive(args.rust, args.rust_package)
    return files, pins


def toolchains(args, reviewed, environment):
    records = {}
    for name in (*reviewed["tool_executables"], *("host:" + n for n in native.SYSTEM_TOOLS)):
        kind, executable = name.split(":")
        path = (Path("/usr") if kind == "host" else getattr(args, kind)) / executable
        version = run([str(path), "-vV" if executable == "bin/rustc" else "--version"], environment)
        libraries = run(["/usr/bin/ldd", str(path)], environment)
        closure = {}
        for word in libraries["stdout"].split():
            if word.startswith("/"):
                closure[word] = native.sha(Path(word))
        require(bool(closure), "Missing native tool runtime closure")
        records[name] = {"sha256": native.sha(path), "version": version["stdout"],
                         "closure": closure, "ldd": libraries}
    require(reviewed["llvm_version"] in records["llvm:lib/llvm-21/bin/clang"]["version"] and
            reviewed["rust_version"] in records["rust:bin/rustc"]["version"] and
            "host: aarch64-unknown-linux-gnu" in records["rust:bin/rustc"]["version"],
            "Wrong native compiler version")
    return records


def build(args) -> None:
    spec, reviewed = json.loads(n2.CONTRACT.read_bytes()), native.contract()
    environment = n2.environment()
    files, pins = preflight(args, spec, reviewed)
    with tempfile.TemporaryDirectory(prefix="j715-native-", dir=environment["TMPDIR"]) as temporary:
        work = Path(temporary)
        paths = {n: str(getattr(args, n)) for n in ("kernel", "exports", "llvm", "rust", "sysroot")}
        paths["work"] = str(work)
        env = native.build_environment(paths, environment)
        require(run(["/usr/bin/stat", "-f", "-c", "%T", str(work)], env)["stdout"].strip() == "tmpfs",
                "Native object trees require tmpfs")
        signature = run(["/usr/bin/gpgv", "--status-fd=1", "--keyring", str(args.rust_keyring),
                         str(args.rust_signature), str(args.rust_package)], env)
        require("[GNUPG:] VALIDSIG " in signature["stdout"], "Unsigned Rust package")
        kernel_note = run([str(args.llvm / "lib/llvm-21/bin/llvm-readelf"),
                           "-n", str(args.kernel / "vmlinux")], env)
        require(re.findall(r"Build ID: ([0-9a-f]+)", kernel_note["stdout"]) ==
                [reviewed["kernel"]["gnu_build_id"]], "Native target kernel build ID differs")
        tools = toolchains(args, reviewed, env)
        for name in ("cache", "index"):
            shutil.copytree(args.cargo / "registry" / name, work / "cargo/registry" / name)
        n2.prepare(args.donor, work / "source")
        commands = native.plan(paths, spec)
        results = [run(commands[0], env)]  # Resolve the complete graph before creating output.
        args.output.mkdir()
        payload = args.output / spec["payload_namespace"]
        payload.mkdir(parents=True)
        (args.output / "evidence").mkdir()
        n2.prepare(args.donor, args.output / "source")
        passes, notes, maps = [], {}, {}
        for index in range(2):
            if index:
                shutil.rmtree(work / "source")
                shutil.rmtree(work / "target")
                (work / "link.map").unlink()
                n2.prepare(args.donor, work / "source")
            for command in commands[1 + index * 4:5 + index * 4]:
                results.append(run(command, env))
            prepared = json.loads((work / "source/audio-build-receipt.json").read_bytes())
            for name, expected in prepared["output_files"].items():
                path = work / "source" / name
                require((digest(os.fsencode(os.readlink(path))) if path.is_symlink() else
                         native.sha(path)) == expected, "Build source changed: " + name)
            outputs = {}
            for component in spec["components"]:
                directory = work / "source" / component["source"]
                require({p.name for p in directory.glob("*.ko")} == set(component["outputs"]),
                        "Module output closure differs")
                outputs.update((n, directory / n) for n in component["outputs"])
            outputs[spec["daemon_output"]] = work / "target" / spec["target"] / "release/speakersafetyd"
            current = {}
            for name, path in outputs.items():
                data = path.read_bytes()
                require(bool(data), "Empty native output")
                current[name] = {"size": len(data), "sha256": digest(data)}
                elf = run([str(args.llvm / "lib/llvm-21/bin/llvm-readelf"),
                           "-h", "-n", "-l", "-d", "--version-info", str(path)], env)
                notes[name] = {"elf": elf["stdout"]}
                if name.endswith(".ko"):
                    notes[name]["vermagic"] = run(["/usr/bin/modinfo", "-F", "vermagic", str(path)], env)["stdout"].strip()
                if index:
                    require((payload / name).read_bytes() == data, "Native clean builds differ: " + name)
                else:
                    (payload / name).write_bytes(data)
            passes.append(current)
            link = (work / "link.map").read_bytes()
            name = f"pass{index + 1}.map"
            (args.output / "evidence" / name).write_bytes(link)
            maps[name] = {"size": len(link), "sha256": digest(link)}
            name = f"pass{index + 1}.link"
            link = (results[-1]["stdout"] + results[-1]["stderr"]).encode()
            (args.output / "evidence" / name).write_bytes(link)
            maps[name] = {"size": len(link), "sha256": digest(link)}
        require(n2.kernel_inputs(args.kernel, native.kernel_inventory(reviewed)) == pins and native.sha(args.exports) ==
                spec["sound_exports_sha256"], "Kernel or exports changed during build")
        require(reviewed["kernel"]["build_version"] in
                (args.kernel / "include/generated/utsversion.h").read_text(), "Native kernel build identity changed")
        require(run(kernel_note["argv"], env)["stdout"] == kernel_note["stdout"],
                "Native target kernel build ID changed")
        abi = native.abi_pins(args.sysroot, reviewed)
        after = toolchains(args, reviewed, env)
        require(all({k: r[k] for k in ("sha256", "version", "closure")} ==
                    {k: after[n][k] for k in ("sha256", "version", "closure")}
                    for n, r in tools.items()), "Native tools changed during build")
        require(local_sources(args.donor, SourceLock.read(ROOT / spec["source_lock"])) == files,
                "Donor source changed during build")
        require({n: native.sha(args.kernel / n) for n in reviewed["host_helpers"]} ==
                reviewed["host_helpers"], "Prepared host helpers changed")
        native.contract()
        receipt = {
            "schema": 1, "backend": native.BACKEND, "board": spec["board"], "profile": spec["profile"],
            "hardware_qualified": False, "rebuilt_identical": False, "repeatable": True,
            "native_contract_sha256": native.sha(native.CONTRACT),
            "native_integration_sources": reviewed["integration_sources"], "native_paths": paths,
            "environment": env, "commands": commands, "command_results": results, "passes": passes,
            "kernel_inputs": pins, "kernel_release": (args.kernel / "include/config/kernel.release").read_text().strip(),
            "kernel_build_version": reviewed["kernel"]["build_version"],
            "kernel_gnu_build_id": reviewed["kernel"]["gnu_build_id"], "kernel_elf": kernel_note,
            "sound_exports_sha256": native.sha(args.exports), "toolchains": tools,
            "host_helpers": reviewed["host_helpers"],
            "rust_package_sha256": native.sha(args.rust_package), "rust_signature": signature["stdout"],
            "external_inputs": {"sysroot": abi, "cargo": n2.tree_pins(args.cargo)},
            "abi_build_ids": notes, "linker_evidence": maps, "outputs": passes[0],
            "source_receipt_sha256": native.sha(args.output / "source/audio-build-receipt.json"),
            "runtime_profile_sha256": native.sha(args.output / "source/audio/j715/j715.conf"),
        }
        native.native(receipt, spec, args.output, lambda p, _: p.read_bytes())
        (args.output / "build-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("donor", "kernel", "exports", "llvm", "rust", "sysroot", "cargo",
                 "rust-package", "rust-signature", "rust-keyring", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    for name, path in vars(args).items():
        setattr(args, name, path.absolute())
    try:
        build(args)
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError, subprocess.SubprocessError) as error:
        print(f"Native audio build refused: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
