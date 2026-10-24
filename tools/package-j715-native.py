# SPDX-License-Identifier: MIT
"""Assemble an inactive J715 filesystem and channel inventory, never install."""
import argparse
import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Final

import j715_native_audio_receipt
import j715_sources
from j715_sources import SourceLock, digest, local_sources, relative, require

ROOT: Final = Path(__file__).resolve().parents[1]
BASE: Final = "usr/lib/omarchy-m4/j715/"


def load(name: str):
    spec = importlib.util.spec_from_file_location("_package_" + name, ROOT / "tools" / (name + ".py"))
    require(spec is not None and spec.loader is not None, "Missing dependency")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def read(path: Path):
    require(path.is_file() and not any(p.is_symlink() for p in (path, *path.parents)),
            "Input must be a regular file without symlink ancestors")
    return json.loads(path.read_bytes(), object_pairs_hook=j715_sources.unique_pairs)


def pin(data: bytes) -> dict[str, str | int]:
    return {"size": len(data), "sha256": digest(data)}


def checked(path: Path, record) -> bytes:
    require(path.is_file() and not any(p.is_symlink() for p in (path, *path.parents)),
            "Unsafe payload path")
    data = path.read_bytes()
    require(len(data) > 0 and digest(data) == record["sha256"] and
            len(data) == record.get("size", record.get("bytes")), "Payload identity differs")
    return data


def integration(record) -> None:
    require(bool(record), "Missing integration source identities")
    for name, sha in record.items():
        require(digest((ROOT / relative(name)).read_bytes()) == sha, "Integration source differs")


def audio(receipt_path: Path, donor: Path) -> dict[str, bytes]:
    """Reconstruct preparation and both N3 candidates from verified donor bytes."""
    builder, renderer = load("prepare-j715-audio-build"), load("m4-j715-audio-profile")
    spec = read(ROOT / "audio/j715/build-contract.json")
    lock = SourceLock.read(ROOT / spec["source_lock"])
    sources = local_sources(donor, lock)
    original = {f.pin.path: f.data for f in sources}
    receipt = read(receipt_path)
    prepared_path = receipt_path.parent / "source/audio-build-receipt.json"
    prepared = read(prepared_path)
    require(receipt["schema"] == prepared["schema"] == 1 and
            receipt["board"] == prepared["board"] == "apple,j715" and
            receipt["profile"] == spec["profile"] and receipt["hardware_qualified"] is False and
            receipt["rebuilt_identical"] is False and prepared["hardware_qualified"] is False,
            "Wrong audio receipt identity")
    require(prepared["commit"] == lock.commit and prepared["repository"] == lock.repository and
            prepared["lock_sha256"] == digest(lock.raw) and
            prepared["archive"] == json.loads(lock.raw)["archive"] and
            prepared["input_form"] == "local-checkout" and prepared["archive_verified"] is False and
            receipt["source_receipt_sha256"] == digest(prepared_path.read_bytes()),
            "Wrong source provenance")
    patch = (ROOT / spec["patch"]).read_bytes()
    require(patch == (ROOT / spec["patch_reference"]).read_bytes() and
            prepared["patch_sha256"] == digest(patch), "Wrong MCA patch")
    lines = patch.decode().splitlines(keepends=True)[3:]
    before = "".join(line[1:] for line in lines if line.startswith((" ", "-"))).encode()
    after = "".join(line[1:] for line in lines if line.startswith((" ", "+"))).encode()
    require(original[builder.MCA].count(before) == 1, "MCA patch context differs")
    expected = {name: digest(data.replace(before, after, 1) if name == builder.MCA else data)
                for name, data in original.items()}
    require(prepared["output_files"] == expected and
            prepared["original_files"] == json.loads(lock.raw)["files"], "Prepared closure differs")
    integration(prepared["integration_sources"])
    require(set(prepared["integration_sources"]) == {
        "tools/prepare-j715-audio-build.py", "tools/build-j715-audio.sh",
        "tools/j715_sources.py", "audio/j715/build-contract.json"}, "Incomplete build integration")
    require(receipt["runtime_profile_sha256"] == digest(original["audio/j715/j715.conf"]) and
            receipt["sound_exports_sha256"] == spec["sound_exports_sha256"] and
            bool(receipt["kernel_release"]), "Build inputs differ")
    j715_native_audio_receipt.validate(receipt, spec, original, receipt_path.parent, checked)
    names = {n for c in spec["components"] for n in c["outputs"]} | {spec["daemon_output"]}
    require(set(receipt["outputs"]) == names and
            {p.name for p in (receipt_path.parent / spec["payload_namespace"]).iterdir()} == names,
            "Incomplete audio binaries")
    files = {BASE + "audio/" + name: checked(
        receipt_path.parent / spec["payload_namespace"] / name, receipt["outputs"][name]) for name in names}
    files[BASE + "audio/build-receipt.json"] = receipt_path.read_bytes()
    files[BASE + "audio/source-receipt.json"] = prepared_path.read_bytes()
    if receipt.get("backend") == j715_native_audio_receipt.BACKEND:
        files[BASE + "audio/native-build-contract.json"] = j715_native_audio_receipt.CONTRACT.read_bytes()
        for name, record in receipt["linker_evidence"].items():
            files[BASE + "audio/evidence/" + name] = checked(
                receipt_path.parent / "evidence" / relative(name), record)
    files[BASE + "audio/config/apple/j715.conf"] = original["audio/j715/j715.conf"]
    runtime = read(ROOT / "audio/j715/runtime-contract.json")
    files[BASE + "audio/runtime-contract.json"] = (ROOT / "audio/j715/runtime-contract.json").read_bytes()
    files[BASE + "audio/session/80-j715-audio-ready.conf"] = (
        ROOT / "packaging/j715-audio/80-j715-audio-ready.conf").read_bytes()
    for name in runtime["runtime_python_sources"]:
        files[BASE + "audio/" + Path(name).name] = (ROOT / relative(name)).read_bytes()
    files[BASE + "audio/j715/profile-contract.json"] = (ROOT / "audio/j715/profile-contract.json").read_bytes()
    for speaker_id in (0, 3):
        report = renderer.IdReport.parse(json.dumps({
            "board": True, "chip": "gpiochip0", "pins": {"4": speaker_id & 1, "13": speaker_id >> 1},
            "speaker_id": speaker_id}).encode())
        candidate = renderer.candidate(sources, report, lock)
        prefix = BASE + f"audio/profiles/{speaker_id}/"
        files.update((prefix + n, data) for n, data in candidate.files)
        files[prefix + "profile-receipt.json"] = candidate.receipt
    # Preserve original FIR source inputs, not just the selected output filters.
    for name, data in original.items():
        if name.startswith(("audio/j715/owner-fir/", "audio/j715/filters/")):
            files[BASE + "audio/original/" + name] = data
    channels = read(ROOT / "packaging/j715-native/channel-paths.json")
    for destination, source in channels["audio_sources"].items():
        files[relative(destination)] = (ROOT / relative(source)).read_bytes() if source.startswith(
            "packaging/") else original[source]
    for name in json.loads(lock.raw)["licenses"]:
        files[BASE + "audio/licenses/" + name] = original[name]
    files[BASE + "audio/source-lock.json"] = lock.raw
    return files


def display(args) -> dict[str, bytes]:
    """Consume separate N5 module/loader receipts; no synthetic profile override."""
    require(args.display_loader is not None and args.display_base is not None,
            "Display requires module receipt, loader receipt and unchanged base DTB")
    module, loader = read(args.display_build), read(args.display_loader)
    profile_path = args.display_build.parent / "profile.json"
    profiles, dt = load("j715_display_profile"), load("build-j715-backlight-dt")
    profile = profiles.qualified(profile_path)
    lock = SourceLock.read(ROOT / "display/j715/source-lock.json")
    sources = local_sources(ROOT, lock)
    files = {}
    prefix = BASE + "backlight/"
    for receipt_path, record in ((args.display_build, module), (args.display_loader, loader)):
        require(record["profile_sha256"] == profile.digest and record["hardware_success"] is False and
                record["comparison"] == "not-rebuilt-identical" and
                re.fullmatch("sha256:[0-9a-f]{64}", record["builder_image"]), "Display build differs")
        integration(record["integration_sources"])
        require(set(record["integration_sources"]) == set(read(ROOT / "packaging/j715-native/channel-paths.json")["display_integration_sources"]), "Incomplete display integration")
        require(record["patch_sha256"] == digest((ROOT / "display/j715/m1n1-j715-handoff.patch").read_bytes()),
                "Display patch differs")
        source_path = receipt_path.parent / "source-receipt.json"
        source = read(source_path)
        require(record["source_receipt_sha256"] == digest(source_path.read_bytes()) and
                source["source_lock_sha256"] == digest(lock.raw) and
                source["source_lock"] == json.loads(lock.raw) and source["commit"] == lock.commit and
                source["profile_sha256"] == profile.digest and source["hardware_success"] is False and
                source["files"] == {s.pin.path: s.pin.sha256 for s in sources},
                "Display source receipt differs")
        require((receipt_path.parent / "profile.json").read_bytes() == profile_path.read_bytes(),
                "Display profiles differ")
        profiles.qualified(receipt_path.parent / "profile.json")
        for name, record_pin in record["outputs"].items():
            files[prefix + relative(name)] = checked(receipt_path.parent / name, record_pin)
        files[prefix + ("module.json" if receipt_path == args.display_build else "loader-receipt.json")] = receipt_path.read_bytes()
        files[prefix + receipt_path.stem + "-source.json"] = source_path.read_bytes()
    require(set(module["outputs"]) == {"j715_dcp_backlight.ko"} and set(loader["outputs"]) == {
        "stage2-stock-m1n1.bin", "stage2-j715-hidden-m1n1.bin", "stage2-j715-shown-m1n1.bin"},
        "Incomplete display binaries")
    require(module["module_sha256"] == digest(files[prefix + "j715_dcp_backlight.ko"]) and
            module["module_bytes"] == len(files[prefix + "j715_dcp_backlight.ko"]) and
            loader["outputs"]["stage2-stock-m1n1.bin"]["sha256"] ==
            "7431196da2fd48681b262f6f605984f82d46ce66bf7ba56cd64118358684a2c7",
            "Display binary pins differ")
    require(bool(module["kernel_release"]) and bool(module["kernel_inputs"]) and
            bool(module["abi_and_build_notes"]), "Missing display ABI evidence")
    base = args.display_base.read_bytes()
    require(digest(base) == profile.base_dtb_sha256, "Display base differs")
    old, reservations, cpu = dt.tree(base)
    files[prefix + "j715-backlight.dtb"] = dt.encode(dt.expected_nodes(old, profile), reservations, cpu)
    files[prefix + "profile.json"] = profile_path.read_bytes()
    for entry in read(profile_path)["evidence"].values():
        files[prefix + relative(entry["path"])] = (profile_path.parent / entry["path"]).read_bytes()
    for name in ("m4-j715-backlight", "m4-backlight", "j715_display_profile", "j715_sources"):
        raw = (ROOT / "tools" / (name + ".py")).read_bytes()
        if name == "m4-j715-backlight":
            require(raw.count(b"usr/lib/omarchy-m4/backlight/j715/") == 3, "Owner path contract changed")
            raw = raw.replace(b"usr/lib/omarchy-m4/backlight/j715/", BASE.encode() + b"backlight/")
            raw = raw.replace(b"m4-j715-backlight brightness",
                              b"/usr/bin/python3 -B /" + prefix.encode() + b"tools/m4-j715-backlight.py brightness")
        files[prefix + "tools/" + name + ".py"] = raw
    files[prefix + "display/j715/profile.schema.json"] = (ROOT / "display/j715/profile.schema.json").read_bytes()
    for name in json.loads(lock.raw)["licenses"]:
        files[prefix + "licenses/" + name] = next(s.data for s in sources if s.pin.path == name)
    return files


def assemble(args) -> Path:
    output = args.output.absolute()
    require(not any(output.resolve().is_relative_to(Path("/") / n) for n in (
        "usr", "etc", "boot", "lib", "lib64", "var", "run", "sys", "proc", "dev", "bin", "sbin")),
        "Live system output is forbidden")
    builder = load("prepare-j715-audio-build")
    inputs = tuple(p for p in (args.donor_source, args.audio_build.parent,
                              args.display_build.parent if args.display_build else None) if p is not None)
    builder.new_output(output, inputs)
    files = audio(args.audio_build, args.donor_source)
    require(args.display_build is not None or
            (args.display_loader is None and args.display_base is None), "Incomplete display request")
    if args.display_build:
        files.update(display(args))
        require(read(args.display_build)["kernel_release"] == read(args.audio_build)["kernel_release"],
                "Audio/display kernels differ")
    channels = read(ROOT / "packaging/j715-native/channel-paths.json")
    require(all(n.startswith(BASE) or n in channels["protected_exceptions"] for n in files),
            "Unlisted protected destination")
    inventory = {"/" + n: {**pin(data), "mode": "0755" if n.endswith(
        "speakersafetyd-j715") else "0644"} for n, data in sorted(files.items())}
    receipt = {"schema": 1, "board": "apple,j715", "activated": False, "hardware_qualified": False,
               "features": {"audio": "prepared", "display": "prepared" if args.display_build else "omitted"},
               "qualification_receipt_present": False, "inventory": inventory,
               "channel_paths_sha256": digest((ROOT / "packaging/j715-native/channel-paths.json").read_bytes()),
               "assembler_sha256": digest(Path(__file__).read_bytes())}
    output.mkdir()
    try:
        for name, data in files.items():
            path = output / relative(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(data)
            path.chmod(int(inventory["/" + name]["mode"], 8))
        with (output / "payload-receipt.json").open("x") as stream:
            json.dump(receipt, stream, indent=2, sort_keys=True)
            stream.write("\n")
    except OSError:
        shutil.rmtree(output)
        raise
    return output / "payload-receipt.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("audio-build", "donor-source", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("display-build", "display-loader", "display-base"):
        parser.add_argument("--" + name, type=Path)
    args = parser.parse_args()
    try:
        print(assemble(args))
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"J715 package refused: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
