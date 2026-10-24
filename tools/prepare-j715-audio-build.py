# SPDX-License-Identifier: MIT
"""Prepare the locked donor audio closure, or compile with prepared local builders."""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Final

from j715_sources import (
    SourceFile,
    SourceLock,
    digest,
    local_sources,
    materialize,
    relative,
    require,
)

ROOT: Final = Path(__file__).resolve().parents[1]
CONTRACT: Final = ROOT / "audio/j715/build-contract.json"
MCA: Final = "audio/j713/kernel/mca.c"


@dataclass(frozen=True, slots=True)
class Component:
    source: str
    outputs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BuildInputs:
    donor: Path
    kernel: Path
    exports: Path
    sysroot: Path
    cargo: Path
    kernel_image: str
    daemon_image: str
    output: Path


def environment() -> dict[str, str]:
    result = os.environ.copy()
    for key in ("TMPDIR", "NODE_COMPILE_CACHE", "BUN_RUNTIME_TRANSPILER_CACHE_PATH"):
        require(bool(result.get(key)), "Missing subprocess environment: " + key)
    result.update(PYTHONDONTWRITEBYTECODE="1", GIT_MASTER="1")
    return result


def run(command: list[str]) -> str:
    return subprocess.run(command, env=environment(), check=True,
                          capture_output=True, text=True).stdout.strip()


def new_output(output: Path, sources: tuple[Path, ...]) -> None:
    require(not output.exists() and not output.is_symlink(), "Output must be new")
    require(output.parent.is_dir() and not any(p.is_symlink() for p in output.parents),
            "Output parent must exist without symlinks")
    for source in (*sources, ROOT):
        require(not output.resolve().is_relative_to(source.resolve()) and
                not source.resolve().is_relative_to(output.resolve()), "Output overlaps input")


def prepare(source: Path, output: Path) -> Path:
    """Freeze verified original bytes, then change only the exact upstream MCA hunk."""
    new_output(output, (source,))
    require(os.environ.get("M4_BOARD", "j715") == "j715", "Wrong build board")
    spec = json.loads(CONTRACT.read_bytes())
    require(spec["board"] == "apple,j715", "Wrong build board")
    lock = SourceLock.read(ROOT / spec["source_lock"])
    originals = local_sources(source, lock)
    patch = (ROOT / spec["patch"]).read_bytes()
    require(patch == (ROOT / spec["patch_reference"]).read_bytes(), "Patch differs from upstream")
    lines = patch.decode().splitlines(keepends=True)[3:]
    before = "".join(line[1:] for line in lines if line.startswith((" ", "-"))).encode()
    after = "".join(line[1:] for line in lines if line.startswith((" ", "+"))).encode()
    files = []
    for item in originals:
        if item.pin.path == MCA:
            require(item.data.count(before) == 1, "MCA hunk not exact")
            item = replace(item, data=item.data.replace(before, after, 1))
        files.append(item)
    materialize(output, tuple(files))
    receipt = {
        "schema": 1, "board": spec["board"], "hardware_qualified": False,
        "input_form": "local-checkout", "archive_verified": False,
        "repository": lock.repository, "commit": lock.commit,
        "archive": json.loads(lock.raw)["archive"], "lock_sha256": digest(lock.raw),
        "original_files": [asdict(f.pin) for f in originals],
        "patch_sha256": digest(patch),
        "output_files": {f.pin.path: digest(f.data) for f in files},
        "integration_sources": {p: digest((ROOT / p).read_bytes()) for p in (
            "tools/prepare-j715-audio-build.py", "tools/build-j715-audio.sh",
            "tools/j715_sources.py", "audio/j715/build-contract.json")},
    }
    (output / "audio-build-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return output


def kernel_inputs(kernel: Path, inventory: bytes) -> dict[str, str]:
    pins = {}
    for line in inventory.decode().splitlines():
        sha, name = line.split()
        relative(name)
        require(re.fullmatch("[0-9a-f]{64}", sha) is not None and name not in pins,
                "Invalid kernel inventory")
        require(digest((kernel / name).read_bytes()) == sha, "Kernel input differs: " + name)
        pins[name] = sha
    require(bool(pins) and (kernel / "Makefile").is_file(), "Kernel is not prepared")
    return pins


def tree_pins(root: Path) -> dict[str, str]:
    """Bind all external sysroot/cache bytes and retained link targets."""
    pins = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            pins[str(path.relative_to(root))] = "link:" + os.readlink(path)
        elif path.is_file():
            pins[str(path.relative_to(root))] = digest(path.read_bytes())
        else:
            require(path.is_dir(), "Special build input: " + str(path))
    return pins


def preflight(inputs: BuildInputs) -> tuple[SourceFile, ...]:
    """Refuse local prerequisite failures without contacting a container engine."""
    new_output(inputs.output, (inputs.donor, inputs.kernel, inputs.exports, inputs.sysroot, inputs.cargo))
    require(os.environ.get("M4_BOARD", "j715") == "j715", "Wrong build board")
    for image in (inputs.kernel_image, inputs.daemon_image):
        require(re.fullmatch("sha256:[a-f0-9]{64}", image) is not None, "Immutable image ID required")
    spec = json.loads(CONTRACT.read_bytes())
    require(spec["board"] == "apple,j715", "Wrong build board")
    require((ROOT / spec["patch"]).read_bytes() == (ROOT / spec["patch_reference"]).read_bytes(),
            "Patch differs from upstream")
    files = local_sources(inputs.donor, SourceLock.read(ROOT / spec["source_lock"]))
    data = {f.pin.path: f.data for f in files}
    kernel_inputs(inputs.kernel, data[spec["kernel_inventory"]])
    require(digest(inputs.exports.read_bytes()) == spec["sound_exports_sha256"], "Sound exports differ")
    for name, pin in json.loads(data[spec["abi_reference"]])["abi_files"].items():
        path = inputs.sysroot / relative(name)
        if "symlink" in pin:
            require(path.is_symlink() and os.readlink(path) == pin["symlink"], "Sysroot link differs")
        else:
            require(path.stat().st_size == pin["size"] and digest(path.read_bytes()) == pin["sha256"],
                    "Sysroot ABI differs: " + name)
    for name in ("usr/lib/pkgconfig/alsa.pc", "usr/include/alsa/asoundlib.h",
                 "usr/lib/libasound.so", "usr/lib/libc.so", "usr/lib/crti.o", "usr/lib/crtn.o"):
        require((inputs.sysroot / name).is_file(), "Missing development sysroot input: " + name)
    packages = tomllib.loads(data[spec["daemon_source"] + "/Cargo.lock"].decode())["package"]
    for package in packages:
        if "source" in package:
            require(package["source"].startswith("registry+"), "Unsupported offline dependency")
            matches = list((inputs.cargo / "registry/cache").glob(
                "*/" + package["name"] + "-" + package["version"] + ".crate"))
            require(bool(matches) and all(digest(p.read_bytes()) == package["checksum"] for p in matches),
                    "Offline crate absent or corrupt: " + package["name"])
    require(any((inputs.cargo / "registry/index").glob("*/*")), "Offline registry index absent")
    return files


def container(image: str, mounts: list[str], command: str) -> list[str]:
    prefix = ("export TMPDIR=/tmp NODE_COMPILE_CACHE=/tmp/node-cache "
              "BUN_RUNTIME_TRANSPILER_CACHE_PATH=/tmp/bun-cache "
              "PYTHONDONTWRITEBYTECODE=1 GIT_MASTER=1; ")
    return ["docker", "run", "--rm", "--network", "none", "--pull", "never",
            "--entrypoint", "/bin/bash", *mounts, image, "-c", "set -euo pipefail; " + prefix + command]


def build(inputs: BuildInputs) -> None:
    files = preflight(inputs)
    spec = json.loads(CONTRACT.read_bytes())
    components = tuple(Component(c["source"], tuple(c["outputs"])) for c in spec["components"])
    for image in (inputs.kernel_image, inputs.daemon_image):
        require(run(["docker", "image", "inspect", "--format", "{{.Id}}", image]) == image,
                "Local builder image missing or different")
    # Resolve the complete locked graph offline before making build output.
    probe = container(inputs.daemon_image, [
        "-v", f"{inputs.donor}:/source:ro", "-v", f"{inputs.cargo}:/cargo-home:ro"],
        "export CARGO_HOME=/cargo-home CARGO_TARGET_DIR=/tmp/target; "
        "cargo metadata --manifest-path /source/audio/j713/speakersafetyd/Cargo.toml "
        "--features j715 --filter-platform aarch64-unknown-linux-gnu --locked --offline --format-version 1")
    run(probe)
    external = {"sysroot": tree_pins(inputs.sysroot), "cargo": tree_pins(inputs.cargo)}
    inputs.output.mkdir()
    source = prepare(inputs.donor, inputs.output / "source")
    shutil.copytree(inputs.cargo, inputs.output / "cargo-home", symlinks=True)
    shutil.copytree(inputs.sysroot, inputs.output / "air-sysroot", symlinks=True)
    payload = inputs.output / spec["payload_namespace"]
    payload.mkdir(parents=True)
    commands, abi = [probe], {}
    for component in components:
        command = container(inputs.kernel_image, [
            "-v", f"{inputs.kernel}:/kernel:ro", "-v", f"{inputs.exports}:/reference-sound-exports.symvers:ro",
            "-v", f"{source / component.source}:/job"],
            "make -C /kernel ARCH=arm64 LLVM=1 LOCALVERSION= KCFLAGS=-Werror "
            "KBUILD_EXTRA_SYMBOLS=/reference-sound-exports.symvers J713_PROFILE=conservative-v1 "
            "M4_BOARD=j715 M=/job -j2 modules; "
            + "; ".join(f"readelf -h -n /job/{name}; modinfo -F vermagic /job/{name}"
                        for name in component.outputs))
        commands.append(command)
        abi[component.source] = run(command)
        require({p.name for p in (source / component.source).glob("*.ko")} == set(component.outputs),
                "Module output closure differs")
        for name in component.outputs:
            shutil.copyfile(source / component.source / name, payload / name)
    command = container(inputs.daemon_image, ["-v", f"{inputs.output}:/job"],
        "cd /job/source/audio/j713/speakersafetyd; "
        "export CARGO_HOME=/job/cargo-home CARGO_TARGET_DIR=/job/target-air "
        "PKG_CONFIG_ALLOW_CROSS=1 PKG_CONFIG_SYSROOT_DIR=/job/air-sysroot "
        "PKG_CONFIG_LIBDIR=/job/air-sysroot/usr/lib/pkgconfig "
        "CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER=aarch64-linux-gnu-gcc; "
        'export RUSTFLAGS="-C link-arg=--sysroot=/job/air-sysroot '
        '-C link-arg=-B/job/air-sysroot/usr/lib/ -C link-arg=-L/job/air-sysroot/usr/lib '
        '-C link-arg=-Wl,-rpath-link,/job/air-sysroot/usr/lib -C link-arg=-Wl,-t"; '
        "cargo rustc --release --target aarch64-unknown-linux-gnu --features j715 --locked --offline -vv "
        "-- -C link-arg=-Wl,-Map=/job/air-link.map > /job/air-link-build.log 2>&1; "
        "cp /job/target-air/aarch64-unknown-linux-gnu/release/speakersafetyd /job/payload/j715/speakersafetyd-j715; "
        "readelf -h -n --version-info /job/payload/j715/speakersafetyd-j715")
    commands.append(command)
    abi["daemon"] = run(command)
    pins = kernel_inputs(inputs.kernel, next(f.data for f in files if f.pin.path == spec["kernel_inventory"]))
    require(digest(inputs.exports.read_bytes()) == spec["sound_exports_sha256"], "Sound exports changed")
    receipt = {"schema": 1, "board": spec["board"], "profile": spec["profile"],
               "hardware_qualified": False, "rebuilt_identical": False, "commands": commands,
               "kernel_inputs": pins, "external_inputs": external,
               "kernel_release": (inputs.kernel / "include/config/kernel.release").read_text().strip(),
               "sound_exports_sha256": digest(inputs.exports.read_bytes()),
               "toolchain_image_ids": [inputs.kernel_image, inputs.daemon_image], "abi_build_ids": abi,
               "source_receipt_sha256": digest((source / "audio-build-receipt.json").read_bytes()),
               "runtime_profile_sha256": digest((source / "audio/j715/j715.conf").read_bytes()),
               "outputs": {p.name: {"size": p.stat().st_size, "sha256": digest(p.read_bytes())}
                           for p in payload.iterdir()}}
    require(all(p.stat().st_size > 0 for p in payload.iterdir()) and len(receipt["outputs"]) == 8,
            "Incomplete compiled payload")
    (inputs.output / "build-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--build", nargs=8)
    args = parser.parse_args()
    try:
        environment()
        if args.build is not None:
            values = args.build
            build(BuildInputs(*(Path(v).resolve() for v in values[:5]), values[5], values[6],
                              Path(values[7]).absolute()))
        else:
            require(args.source is not None and args.output is not None, "--source and --output required")
            prepare(args.source, args.output.absolute())
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        print(f"Audio build refused: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
