#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Build the portable M4 early root; firmware is added by the Mac installer."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess


def root_file(root, name):
    """Resolve symlinks inside the candidate, including absolute target paths."""
    pending = list(PurePosixPath(name).parts)
    resolved = []
    links = 0
    while pending:
        component = pending.pop(0)
        if component in ("/", "."):
            continue
        if component == "..":
            if not resolved:
                raise ValueError("Path escapes candidate root")
            resolved.pop()
            continue
        path = root.joinpath(*resolved, component)
        if path.is_symlink():
            links += 1
            if links > 40:
                raise ValueError("Symlink cycle in candidate root")
            target = path.readlink()
            if target.is_absolute():
                resolved = []
            pending = list(target.parts) + pending
        else:
            resolved.append(component)
    result = root.joinpath(*resolved)
    if not result.is_file():
        raise FileNotFoundError(result)
    return result


def elf_dependencies(path):
    header = subprocess.check_output(["readelf", "-h", str(path)], text=True)
    if not re.search(r"Machine:\s+AArch64\b", header):
        raise ValueError(f"Expected AArch64 ELF: {path}")
    program = subprocess.check_output(["readelf", "-l", str(path)], text=True)
    dynamic = subprocess.check_output(["readelf", "-d", str(path)], text=True)
    needed = re.findall(r"\(NEEDED\).*\[([^\]]+)\]", dynamic)
    for name in needed:
        if not re.fullmatch(r"[A-Za-z0-9_.+-]+", name):
            raise ValueError("Unexpected ELF dependency name")
    interpreter = re.findall(r"Requesting program interpreter: ([^\]]+)\]", program)
    return interpreter + ["/usr/lib/" + name for name in needed]


def build(args):
    root = args.root.resolve()
    output = args.output.resolve()
    # gen_init_cpio's manifest uses whitespace-separated paths.
    if any(char.isspace() for char in str(output)):
        raise ValueError("Build output path may not contain whitespace")
    output.mkdir(parents=True, exist_ok=False)
    files = output / "files"
    files.mkdir()
    inputs = {"/bin/busybox": args.busybox.resolve(),
              "/lib/modules/nvme-apple.ko": args.nvme_module.resolve()}
    repo = Path(__file__).resolve().parents[1]
    inputs["/init"] = repo / "tools/m4-ssd-init"
    for name in ("m4-root-identity.sh", "m4-root-config.sh", "m4-ssd-root.sh"):
        inputs["/lib/" + name] = repo / "tools" / name
    queue = ["/usr/bin/blkid", "/usr/bin/e2fsck", "/usr/bin/resize2fs"]
    # BusyBox is static in the qualified input, but verify that contract.
    if elf_dependencies(inputs["/bin/busybox"]):
        raise ValueError("The early-root BusyBox must be static")
    while queue:
        name = queue.pop(0)
        if name in inputs:
            continue
        source = root_file(root, name)
        inputs[name] = source
        queue.extend(elf_dependencies(source))
    directories = {"/bin", "/dev", "/proc", "/sys", "/run", "/newroot", "/vendorfw"}
    for name in inputs:
        directories.update(str(p) for p in PurePosixPath(name).parents if str(p) != "/")
    manifest = [f"dir {name} 755 0 0" for name in sorted(directories, key=lambda n: (n.count('/'), n))]
    records = {}
    for index, (name, source) in enumerate(sorted(inputs.items())):
        staged = files / str(index)
        shutil.copyfile(source, staged)
        mode = "644" if name.endswith((".sh", ".ko")) else "755"
        manifest.append(f"file {name} {staged} {mode} 0 0")
        records[name] = {"size": staged.stat().st_size,
                         "sha256": hashlib.sha256(staged.read_bytes()).hexdigest()}
    manifest += ["slink /bin/sh busybox 777 0 0", "slink /lib/firmware ../vendorfw 777 0 0",
                 "nod /dev/console 600 0 0 c 5 1", "nod /dev/null 666 0 0 c 1 3"]
    description = output / "initramfs.manifest"
    description.write_text("\n".join(manifest) + "\n")
    archive = output / "initramfs.cpio"
    with archive.open("xb") as stream:
        subprocess.run([str(args.gen_init_cpio), "-t", "0", str(description)], stdout=stream, check=True)
    compressed = output / "initramfs.cpio.gz"
    with archive.open("rb") as source, compressed.open("xb") as target:
        with gzip.GzipFile(filename="", mode="wb", fileobj=target, mtime=0) as zipped:
            shutil.copyfileobj(source, zipped)
    report = {"scope": "Portable early root; no vendor firmware or root filesystem included",
              "files": records, "compressed_size": compressed.stat().st_size,
              "compressed_sha256": hashlib.sha256(compressed.read_bytes()).hexdigest()}
    (output / "manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "files"}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("root", "busybox", "nvme-module", "gen-init-cpio", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    build(parser.parse_args())
