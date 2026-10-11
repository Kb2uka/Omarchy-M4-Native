#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Apply/check an explicit J713 audio package in a pending offline owner image."""
import argparse
import json
import os
from pathlib import Path
import stat
import subprocess
from m4_audio_package import digest, dtb_compatible, image_path, load_package, require, verify_abi


MANIFEST = "etc/omarchy/j713-audio-manifest.json"
DEFAULT_POLICY_ADDITIONS = {
    "etc/wireplumber/wireplumber.conf.d/82-j713-protected-default.conf",
    "usr/share/wireplumber/scripts/default-nodes/j713-protected.lua",
}


def closed_default_transition(previous, current):
    """The default-policy join adds two files; every prior runtime pin stays exact."""
    changed_fields = {"file_metadata", "files", "source_revision"}
    old_identity = {key: value for key, value in previous.items() if key not in changed_fields}
    new_identity = {key: value for key, value in current.items() if key not in changed_fields}
    require(old_identity == new_identity, "Closed audio default transition: package identity changed")
    old_files, new_files = previous["file_metadata"], current["file_metadata"]
    require(set(new_files) - set(old_files) == DEFAULT_POLICY_ADDITIONS and
            all(new_files.get(name) == entry for name, entry in old_files.items()),
            "Closed audio default transition: payload set or metadata changed")
    require(previous["files"] == {"/" + name: entry["sha256"] for name, entry in old_files.items()},
            "Closed audio default transition: previous file maps disagree")



def public_parents(root, name, create=False):
    """Public package paths must remain traversable under restrictive installer umasks."""
    current = root
    for part in Path(name).parts[:-1]:
        current /= part
        require(not current.is_symlink(), "Symlink in public parent: " + str(current.relative_to(root)))
        if current.exists():
            info = current.stat()
            require(info.st_uid == os.geteuid(), "Foreign public parent owner: " + str(current.relative_to(root)))
            mode = stat.S_IMODE(info.st_mode)
            require(current.is_dir() and mode & 0o555 == 0o555 and not mode & 0o022,
                    "Inaccessible or writable public parent: " + str(current.relative_to(root)))
        elif create:
            current.mkdir(mode=0o755)
            current.chmod(0o755)


def configure(root, package, board, kernel_release, base_dtb, package_sha256, apply=False,
              previous_package_sha256=None):
    require(not any(p.is_symlink() for p in (root, *root.parents)), "Symlink image root")
    root = root.resolve(strict=True)
    require(root != Path(root.anchor), "Offline image required; refusing live root")
    raw, manifest, planned = load_package(package.resolve(strict=True))
    require(digest(raw) == package_sha256, "Package manifest pin mismatch")
    previous_raw = None
    if previous_package_sha256 is not None:
        installed_manifest = image_path(root, MANIFEST)
        require(installed_manifest.is_file(), "Previous package manifest missing")
        installed_raw = installed_manifest.read_bytes()
        if installed_raw != raw:
            require(digest(installed_raw) == previous_package_sha256,
                    "Previous package manifest pin mismatch")
            closed_default_transition(json.loads(installed_raw), manifest)
            previous_raw = installed_raw
    require(board == manifest["board"] == "apple,j713", "Unsupported board")
    require(kernel_release == manifest["kernel_release"], "Kernel ABI mismatch")
    base = base_dtb.read_bytes()
    require({b"apple,j713", b"apple,t8132"} <= set(dtb_compatible(base)), "Wrong base DTB board")
    require(digest(base) in manifest["base_dtb_sha256"], "Unqualified base DTB")
    for marker in ("etc/m4-root-id", "var/lib/omarchy/provisioning/pending"):
        require(image_path(root, marker).is_file(), "Not a pending owner image: " + marker)
    require(image_path(root, "usr/lib/modules/" + kernel_release).is_dir(), "Missing exact image kernel")
    verify_abi(root, manifest["abi_files"])
    for conflicting in ("etc/systemd/system/multi-user.target.wants/speakersafetyd.service",
                        "etc/modules-load.d/j713-audio.conf",
                        "etc/systemd/system/speakersafetyd-j713.service",
                        "etc/systemd/system/m4-j713-audio-register.service"):

        p = image_path(root, conflicting)
        require(not p.exists(), "Conflicting audio startup: " + conflicting)

    for unit in ("speakersafetyd-j713.service", "m4-j713-audio-register.service"):
        overrides = image_path(root, "etc/systemd/system/" + unit + ".d")
        require(not overrides.exists() or overrides.is_dir() and not list(overrides.iterdir()),
                "Unreviewed system service overrides: " + unit)

    planned[MANIFEST] = raw
    modes = {name: e["mode"] for name, e in manifest["file_metadata"].items()}
    modes[MANIFEST] = 0o644
    changes = []
    symlinks = manifest.get("symlinks", {})
    for name, target in symlinks.items():
        public_parents(root, name)
        require(name.startswith("etc/systemd/system/multi-user.target.wants/") and
                target.startswith("/usr/lib/systemd/system/") and
                target.lstrip("/") in planned, "Unsupported enable link")
        path = image_path(root, str(Path(name).parent)) / Path(name).name
        require(not path.exists() and not path.is_symlink() or
                path.is_symlink() and os.readlink(path) == target, "Conflicting startup link: " + name)
        if not path.is_symlink():
            changes.append(name)
    # Complete validation before the first write; preserve customized owners/configs.
    for name, content in planned.items():
        public_parents(root, name)
        path = image_path(root, name)
        require(not path.with_name(path.name + ".m4-audio-new").exists() and
                not path.with_name(path.name + ".m4-audio-new").is_symlink(), "Stale temporary file: " + name)
        require(not path.exists() or path.is_file(), "Non-file destination: " + name)
        if path.exists():
            require(path.stat().st_uid == os.geteuid(), "Foreign image file owner: " + name)
            expected = previous_raw if name == MANIFEST and previous_raw is not None else content
            require(path.read_bytes() == expected, "Conflicting existing audio file: " + name)
        if (not path.exists() or stat.S_IMODE(path.stat().st_mode) != modes[name] or
                name == MANIFEST and previous_raw is not None):
            changes.append(name)
    require(apply or not changes, "Audio image configuration differs: " + ", ".join(changes))
    if apply:
        for name in sorted(changes, key=lambda item: (item in symlinks, item == MANIFEST, item)):
            if name in symlinks:
                path = image_path(root, str(Path(name).parent)) / Path(name).name
                public_parents(root, name, create=True)
                path.symlink_to(symlinks[name])
                continue
            path = image_path(root, name)
            public_parents(root, name, create=True)
            temporary = path.with_name(path.name + ".m4-audio-new")
            with temporary.open("xb") as stream:
                stream.write(planned[name])
                stream.flush()
                os.fsync(stream.fileno())
            temporary.chmod(modes[name])
            os.replace(temporary, path)
    return {"package_sha256": digest(raw), "configured_files": sorted(planned),
            "changed_files": changes, "scope": "Offline image only; no host service or device action",
            "redistributable": manifest["redistributable"]}


def mounted_image_root(root):
    mount = subprocess.run(["findmnt", "--json", "--target", str(root), "--output", "TARGET,FSTYPE"],
                           check=True, text=True, capture_output=True)
    mounts = json.loads(mount.stdout)["filesystems"]
    require(len(mounts) == 1 and mounts[0]["fstype"] == "ext4" and
            Path(mounts[0]["target"]).resolve() == root.resolve(),
            "Explicit mounted ext4 image root required")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--board", required=True)
    parser.add_argument("--kernel-release", required=True)
    parser.add_argument("--package-sha256", required=True)
    parser.add_argument("--previous-package-sha256",
                        help="Allow only the pinned two-file protected-default addition")
    parser.add_argument("--base-dtb", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    require(os.geteuid() == 0, "Root required for mounted image apply/check")
    mounted_image_root(args.root)
    print(json.dumps(configure(args.root, args.package, args.board, args.kernel_release,
                               args.base_dtb, args.package_sha256, args.apply,
                               args.previous_package_sha256), indent=2))
