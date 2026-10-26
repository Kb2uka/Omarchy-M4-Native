# SPDX-License-Identifier: MIT
"""Native command contract and the narrow N6 build-backend validation seam."""
import hashlib
import json
import os
import re
import tarfile
from pathlib import Path

from j715_sources import relative, require, unique_pairs

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "audio/j715/native-build-contract.json"
BACKEND = "native-prepared-external"
SYSTEM_TOOLS = ("bin/make", "bin/modinfo", "bin/pkg-config", "bin/gpgv", "bin/stat")


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def contract():
    spec = json.loads(CONTRACT.read_bytes(), object_pairs_hook=unique_pairs)
    require(spec["schema"] == 1 and spec["backend"] == BACKEND and
            spec["board"] == "apple,j715" and spec["hardware_qualified"] is False,
            "Wrong native contract")
    for name, pin in spec["integration_sources"].items():
        require(sha(ROOT / relative(name)) == pin, "Native integration differs: " + name)
    require(set(spec["integration_sources"]) == {
        "tools/build-j715-native-audio.py", "tools/j715_native_audio_receipt.py"},
        "Incomplete native integration")
    for name, pin in spec["inherited_inputs"].items():
        require(sha(ROOT / relative(name)) == pin, "Inherited native input differs: " + name)
    return spec


def abi_pins(root: Path, spec) -> dict[str, str]:
    pins = {}
    for name, pin in spec["abi_files"].items():
        path = root / relative(name)
        if "symlink" in pin:
            require(path.is_symlink() and os.readlink(path) == pin["symlink"], "Native ABI link differs")
            pins[name] = "link:" + pin["symlink"]
        else:
            require(path.is_file() and not path.is_symlink() and
                    path.stat().st_size == pin["size"] and sha(path) == pin["sha256"],
                    "Native ABI differs: " + name)
            pins[name] = pin["sha256"]
    return pins


def kernel_inventory(spec) -> bytes:
    pins = spec["kernel"]["inputs"]
    require(set(pins) == {".config", "Module.symvers", "include/generated/autoconf.h",
                         "include/config/auto.conf", "include/generated/utsrelease.h",
                         "include/config/kernel.release"} and
            all(re.fullmatch("[0-9a-f]{64}", p) for p in pins.values()),
            "Native target kernel evidence incomplete")
    return "".join(f"{pin}  {name}\n" for name, pin in pins.items()).encode()


def rust_archive(root: Path, package: Path) -> None:
    """Bind the entire extracted Rust toolchain to its pinned package, without extraction."""
    names = set()
    with tarfile.open(package, "r:*") as archive:
        for member in archive:
            if member.name.startswith("usr/") and not member.isdir():
                name = relative(member.name[4:])
                path = root / name
                require(not any(p.is_symlink() for p in path.parents), "Rust link ancestor")
                names.add(name)
                if member.issym():
                    require(path.is_symlink() and os.readlink(path) == member.linkname,
                            "Rust package link differs")
                else:
                    require(member.isfile() and not path.is_symlink(), "Unsafe Rust package member")
                    with archive.extractfile(member) as stream:
                        require(sha(path) == hashlib.file_digest(stream, "sha256").hexdigest(),
                                "Rust package member differs")
    require(names == {str(p.relative_to(root)) for p in root.rglob("*")
                      if p.is_symlink() or not p.is_dir()},
            "Rust package closure differs")


def plan(paths, spec) -> list[list[str]]:
    """Exact argv, shared by execution and receipt verification; never shell strings."""
    work, kernel = paths["work"], paths["kernel"]
    llvm, rust = paths["llvm"] + "/lib/llvm-21/bin/", paths["rust"] + "/bin/"
    daemon = work + "/source/" + spec["daemon_source"]
    metadata = [rust + "cargo", "metadata", "--manifest-path", daemon + "/Cargo.toml",
                "--features", "j715", "--filter-platform", spec["target"],
                "--locked", "--offline", "--format-version", "1"]
    modules = [
        ["/usr/bin/make", "-C", kernel, "ARCH=arm64", "LLVM=" + llvm, "LOCALVERSION=",
         "KCFLAGS=-Werror", "KBUILD_EXTRA_SYMBOLS=" + paths["exports"],
         "J713_PROFILE=conservative-v1", "M4_BOARD=j715",
         "M=" + work + "/source/" + component["source"], "-j2", "modules"]
        for component in spec["components"]]
    daemon_command = [
        rust + "cargo", "rustc", "--manifest-path", daemon + "/Cargo.toml", "--release",
        "--target", spec["target"], "--features", "j715", "--locked", "--offline", "-vv",
        "--", "-C", "link-arg=-Wl,-Map=" + work + "/link.map"]
    return [metadata, *modules, daemon_command, *modules, daemon_command]


def build_environment(paths, inherited) -> dict[str, str]:
    """Do not inherit caller compiler overrides, Cargo configuration or library paths."""
    work, sysroot = paths["work"], paths["sysroot"]
    llvm, rust = paths["llvm"], paths["rust"]
    result = {k: inherited[k] for k in (
        "TMPDIR", "NODE_COMPILE_CACHE", "BUN_RUNTIME_TRANSPILER_CACHE_PATH")}
    result.update(
        HOME=work, LANG="C", LC_ALL="C", PYTHONDONTWRITEBYTECODE="1", GIT_MASTER="1",
        PATH=f"{rust}/bin:{llvm}/lib/llvm-21/bin:/usr/bin:/bin",
        LD_LIBRARY_PATH=f"{rust}/lib:{llvm}/lib/aarch64-linux-gnu:{llvm}/lib/llvm-21/lib",
        CARGO_HOME=work + "/cargo", CARGO_TARGET_DIR=work + "/target",
        RUSTC=rust + "/bin/rustc", RUSTDOC=rust + "/bin/rustdoc",
        CC=llvm + "/lib/llvm-21/bin/clang",
        CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER=llvm + "/lib/llvm-21/bin/clang",
        PKG_CONFIG_ALLOW_CROSS="1", PKG_CONFIG_SYSROOT_DIR=sysroot,
        PKG_CONFIG_LIBDIR=sysroot + "/usr/lib/pkgconfig",
        RUSTFLAGS=f"--remap-path-prefix={work}=/j715 -C link-arg=--sysroot={sysroot} "
                  f"-C link-arg=-fuse-ld={llvm}/lib/llvm-21/bin/ld.lld "
                  f"-C link-arg=-B{sysroot}/usr/lib/ -C link-arg=-L{sysroot}/usr/lib "
                  f"-C link-arg=-Wl,-rpath-link,{sysroot}/usr/lib -C link-arg=-Wl,-t",
    )
    return result


def docker(receipt, spec, original) -> None:
    """Preserve the original Docker receipt checks, including legacy absent backend."""
    require(all(re.fullmatch("sha256:[0-9a-f]{64}", i) for i in receipt["toolchain_image_ids"]) and
            len(receipt["toolchain_image_ids"]) == 2, "Build inputs differ")
    require(len(receipt["commands"]) == 5 and
            set(receipt["abi_build_ids"]) == {c["source"] for c in spec["components"]} | {"daemon"},
            "Incomplete compiler evidence")
    for command in receipt["commands"]:
        require(command[:8] == ["docker", "run", "--rm", "--network", "none", "--pull", "never", "--entrypoint"]
                and command[-3] in receipt["toolchain_image_ids"] and command[-2] == "-c",
                "Compiler command is not bound to an offline immutable builder")
    require("--locked --offline" in receipt["commands"][0][-1] and
            all("ARCH=arm64 LLVM=1 LOCALVERSION= KCFLAGS=-Werror" in c[-1] and
                "M4_BOARD=j715" in c[-1] for c in receipt["commands"][1:4]) and
            "--features j715 --locked --offline" in receipt["commands"][4][-1], "Build flags differ")


def native(receipt, spec, directory: Path, checked) -> None:
    reviewed = contract()
    require(receipt["native_contract_sha256"] == sha(CONTRACT) and
            receipt["native_integration_sources"] == reviewed["integration_sources"] and
            "toolchain_image_ids" not in receipt and receipt["repeatable"] is True and
            receipt["host_helpers"] == reviewed["host_helpers"],
            "Native contract identity differs")
    kernel_inventory(reviewed)
    require(receipt["kernel_inputs"] == reviewed["kernel"]["inputs"] and
            receipt["kernel_release"] == reviewed["kernel"]["release"] and
            receipt["kernel_build_version"] == reviewed["kernel"]["build_version"] and
            receipt["kernel_gnu_build_id"] == reviewed["kernel"]["gnu_build_id"],
            "Native target kernel differs")
    paths = receipt["native_paths"]
    require(set(paths) == {"work", "kernel", "exports", "llvm", "rust", "sysroot"}, "Native paths differ")
    require(all(Path(p).is_absolute() and ".." not in Path(p).parts for p in paths.values()),
            "Invalid native command path")
    require(receipt["kernel_elf"]["argv"] == [
        paths["llvm"] + "/lib/llvm-21/bin/llvm-readelf", "-n", paths["kernel"] + "/vmlinux"] and
        receipt["kernel_elf"]["returncode"] == 0 and
        re.findall(r"Build ID: ([0-9a-f]+)", receipt["kernel_elf"]["stdout"]) ==
        [reviewed["kernel"]["gnu_build_id"]], "Native target kernel ELF differs")
    require(receipt["commands"] == plan(paths, spec), "Native commands differ")
    environment = receipt["environment"]
    require(environment == build_environment(paths, environment), "Native environment differs")
    require(receipt["external_inputs"]["sysroot"] == {
        n: "link:" + p["symlink"] if "symlink" in p else p["sha256"]
        for n, p in reviewed["abi_files"].items()}, "Native runtime ABI differs")
    require(receipt["rust_package_sha256"] == reviewed["rust_package_sha256"] and
            "[GNUPG:] VALIDSIG " in receipt["rust_signature"], "Native Rust package differs")
    expected_tools = {**reviewed["tool_executables"],
                      **{"host:" + n: None for n in SYSTEM_TOOLS}}
    require(set(receipt["toolchains"]) == set(expected_tools), "Native tool set differs")
    for name, expected in expected_tools.items():
        record = receipt["toolchains"][name]
        require((record["sha256"] == expected if expected else
                 re.fullmatch("[0-9a-f]{64}", record["sha256"])) and bool(record["version"]) and
                bool(record["closure"]) and all(re.fullmatch("[0-9a-f]{64}", h)
                                              for h in record["closure"].values()),
                "Native tool identity differs")
    require(reviewed["llvm_version"] in receipt["toolchains"]["llvm:lib/llvm-21/bin/clang"]["version"] and
            reviewed["rust_version"] in receipt["toolchains"]["rust:bin/rustc"]["version"] and
            "host: " + spec["target"] in receipt["toolchains"]["rust:bin/rustc"]["version"],
            "Native compiler version differs")
    require(len(receipt["command_results"]) == 9 and
            all(set(r) == {"argv", "returncode", "stdout", "stderr"} and
                r["argv"] == c and r["returncode"] == 0 and
                isinstance(r["stdout"], str) and isinstance(r["stderr"], str)
                for r, c in zip(receipt["command_results"], receipt["commands"], strict=True)),
            "Native compiler transcript differs")
    require(receipt["passes"] == [receipt["outputs"], receipt["outputs"]], "Native repeatability differs")
    require(set(receipt["abi_build_ids"]) == set(receipt["outputs"]), "Native ELF closure differs")
    for name, note in receipt["abi_build_ids"].items():
        require("AArch64" in note["elf"] and re.search(r"Build ID: [0-9a-f]+", note["elf"]),
                "Missing native ELF build ID")
        if name.endswith(".ko"):
            require(note["vermagic"].startswith(receipt["kernel_release"] + " ") and
                    note["vermagic"].endswith("aarch64"), "Native module ABI differs")
        else:
            require("Shared library: [libasound.so.2]" in note["elf"] and
                    "ld-linux-aarch64.so.1" in note["elf"], "Native daemon linkage differs")
            require(sorted(re.findall(r"Shared library: \[([^]]+)\]", note["elf"])) ==
                    sorted(reviewed["needed"]), "Native runtime closure differs")
    require(set(receipt["linker_evidence"]) ==
            {"pass1.map", "pass2.map", "pass1.link", "pass2.link"}, "Missing native link evidence")
    for name, pin in receipt["linker_evidence"].items():
        data = checked(directory / "evidence" / relative(name), pin)
        require(bool(data), "Empty native linker evidence")
        if name.endswith(".link"):
            require(b"libasound.so" in data and b"libc.so" in data, "Native linker evidence differs")


def validate(receipt, spec, original, directory: Path, checked) -> None:
    require(receipt.get("backend", "docker") in ("docker", BACKEND), "Unknown audio build backend")
    if receipt.get("backend", "docker") == BACKEND:
        native(receipt, spec, directory, checked)
        return
    docker(receipt, spec, original)
    require(receipt["kernel_inputs"] == dict(line.split()[::-1] for line in
            original[spec["kernel_inventory"]].decode().splitlines()), "Incomplete compiler evidence")
    for name, entry in json.loads(original[spec["abi_reference"]])["abi_files"].items():
        require(receipt["external_inputs"]["sysroot"][name] ==
                ("link:" + entry["symlink"] if "symlink" in entry else entry["sha256"]),
                "Runtime ABI differs")
