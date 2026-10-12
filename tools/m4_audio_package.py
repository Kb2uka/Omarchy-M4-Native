# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Shared, offline J713 package validation. No device or service operations."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import struct


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def image_path(root, relative):
    name = PurePosixPath(relative)
    require(not name.is_absolute() and name.parts and ".." not in name.parts,
            "Unsafe package path: " + str(relative))
    path = root
    for index, part in enumerate(name.parts):
        require(not path.exists() or path.is_dir(), "Non-directory path ancestor: " + str(relative))
        path /= part
        require(not path.is_symlink(), "Symlink in image/package path: " + str(relative))
    return path


def verified(root, name, entry):
    path = image_path(root, name)
    require(path.is_file(), "Missing regular file: " + name)
    data = path.read_bytes()
    require(len(data) == entry["size"] and digest(data) == entry["sha256"],
            "Hash/size mismatch: " + name)
    return data


def verify_abi(root, entries):
    for name, entry in entries.items():
        if "symlink" in entry:
            path = image_path(root, str(PurePosixPath(name).parent)) / PurePosixPath(name).name
            require(path.is_symlink() and os.readlink(path) == entry["symlink"],
                    "ABI link mismatch: " + name)
        else:
            verified(root, name, entry)


def dtb_compatible(data):
    """Read only the root compatible property from a bounded flattened tree."""
    require(len(data) >= 40, "Truncated DTB")
    magic, total, off, strings, _, version, _, _, string_size, size = struct.unpack(">10I", data[:40])
    require(magic == 0xd00dfeed and version >= 17 and total == len(data), "Invalid DTB header")
    require(off + size <= total and strings + string_size <= total, "Invalid DTB bounds")
    end = off + size
    depth = -1
    while off + 4 <= end:
        token = struct.unpack_from(">I", data, off)[0]
        off += 4
        if token == 1:
            stop = data.find(b"\0", off, end)
            require(stop >= 0, "Invalid DTB node")
            off = (stop + 4) & ~3
            depth += 1
        elif token == 2:
            depth -= 1
        elif token == 3:
            require(off + 8 <= end, "Invalid DTB property")
            length, name = struct.unpack_from(">II", data, off)
            off += 8
            require(off + length <= end and name < string_size, "Invalid DTB property bounds")
            stop = data.find(b"\0", strings + name, strings + string_size)
            require(stop >= 0, "Invalid DTB string")
            if depth == 0 and data[strings + name:stop] == b"compatible":
                value = data[off:off + length]
                require(value.endswith(b"\0"), "Invalid compatible property")
                return value.split(b"\0")[:-1]
            off = (off + length + 3) & ~3
        elif token == 4:
            continue
        elif token == 9:
            break
        else:
            raise ValueError("Invalid DTB token")
    raise ValueError("Missing root compatible")


def load_package(package):
    raw = image_path(package, "manifest.json").read_bytes()
    manifest = json.loads(raw)
    require(manifest.get("schema") == 1, "Unsupported audio package schema")
    payload = {name: verified(package / "root", name, entry)
               for name, entry in manifest["file_metadata"].items()}
    require(manifest["files"] == {"/" + n: e["sha256"] for n, e in manifest["file_metadata"].items()},
            "Package/readiness file maps disagree")
    return raw, manifest, payload
