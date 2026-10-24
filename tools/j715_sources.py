# SPDX-License-Identifier: MIT
"""Verify original source identities without network or hardware access."""
import hashlib
import json
import os
import posixpath
import re
import stat
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final, TypeAlias

MAX_MEMBERS: Final = 4096
MAX_EXPANDED: Final = 16 * 1024 * 1024
JsonValue: TypeAlias = "None | bool | int | str | list[JsonValue] | dict[str, JsonValue]"
JsonMap: TypeAlias = dict[str, JsonValue]


class SourceError(ValueError):
    """A source input failed its locked contract."""


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise SourceError(reason)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def relative(name: str) -> str:
    require(bool(name) and "\\" not in name and "\0" not in name,
            "Invalid source path: " + name)
    require(not name.startswith("/") and
            all(p not in ("", ".", "..", ".git") for p in name.split("/")),
            "Unsafe source path: " + name)
    return name


def unique_pairs(pairs: list[tuple[str, JsonValue]]) -> JsonMap:
    result: JsonMap = {}
    for key, value in pairs:
        require(key not in result, "Duplicate JSON key: " + key)
        result[key] = value
    return result


@dataclass(frozen=True, slots=True)
class FilePin:
    path: str
    mode: str
    blob: str
    size: int
    sha256: str

    def verify(self, data: bytes) -> None:
        require(len(data) == self.size and digest(data) == self.sha256,
                "Source bytes differ: " + self.path)
        identity = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        require(identity == self.blob, "Git blob differs: " + self.path)


@dataclass(frozen=True, slots=True)
class ArchivePin:
    size: int
    sha256: str
    root: str


@dataclass(frozen=True, slots=True)
class SourceLock:
    raw: bytes
    repository: str
    commit: str
    files: tuple[FilePin, ...]
    archive: ArchivePin | None
    selected_paths: tuple[str, ...] = ()

    @classmethod
    def read(cls, path: Path) -> "SourceLock":
        raw = path.read_bytes()
        spec = json.loads(raw, object_pairs_hook=unique_pairs)
        require(spec["schema"] == 1 and re.fullmatch("[0-9a-f]{40}", spec["commit"]),
                "Invalid source lock revision")
        files = tuple(FilePin(**entry) for entry in spec["files"])
        names = {relative(pin.path) for pin in files}
        require(len(names) == len(files) and 0 < len(files) <= MAX_MEMBERS,
                "Duplicate or excessive source entries")
        require(sum(pin.size for pin in files) <= MAX_EXPANDED, "Source closure too large")
        for pin in files:
            require(pin.mode in ("100644", "100755", "120000") and
                    type(pin.size) is int and pin.size >= 0 and
                    re.fullmatch("[0-9a-f]{40}", pin.blob) is not None and
                    re.fullmatch("[0-9a-f]{64}", pin.sha256) is not None,
                    "Invalid source identity: " + pin.path)
            require(not any(str(p) in names for p in PurePosixPath(pin.path).parents),
                    "Source file used as directory: " + pin.path)
        require(bool(spec["licenses"]) and set(spec["licenses"]) <= names,
                "Missing license closure")
        require(set(spec.get("owner_tests", [])) <= names, "Missing owner tests")
        selected = tuple(spec["selected_paths"])
        require(bool(selected) and len(set(selected)) == len(selected),
                "Missing or duplicate source selection")
        for name in selected:
            require(name == "." or relative(name) == name, "Invalid source selection")
            require(any(name == "." or pin == name or pin.startswith(name + "/")
                        for pin in names), "Empty source selection: " + name)
        require(all(any(name == "." or pin == name or pin.startswith(name + "/")
                        for name in selected) for pin in names),
                "Source outside selected closure")
        if "tracked_file_bytes" in spec:
            require(spec["tracked_file_bytes"] == sum(pin.size for pin in files),
                    "Tracked source byte count differs")
        archive = spec.get("archive")
        if archive:
            require(type(archive["size"]) is int and 0 < archive["size"] <= MAX_EXPANDED and
                    re.fullmatch("[0-9a-f]{64}", archive["sha256"]) is not None and
                    "/" not in relative(archive["root"]), "Invalid archive identity")
        return cls(raw, spec["repository"], spec["commit"], files,
                   ArchivePin(archive["size"], archive["sha256"], relative(archive["root"]))
                   if archive else None, selected)


@dataclass(frozen=True, slots=True)
class SourceFile:
    pin: FilePin
    data: bytes


def check_links(files: tuple[SourceFile, ...]) -> None:
    """Allow only links to retained in-tree files; never link directory parents."""
    names = {f.pin.path for f in files}
    for source in files:
        if source.pin.mode == "120000":
            target = source.data.decode("utf-8")
            require(not target.startswith("/") and "\\" not in target and "\0" not in target,
                    "Escaping source link: " + source.pin.path)
            target_name = posixpath.normpath(posixpath.join(
                posixpath.dirname(source.pin.path), target))
            require(target_name in names and target_name != source.pin.path,
                    "Escaping or missing source link: " + source.pin.path)
            require(not any(f.pin.path.startswith(source.pin.path + "/") for f in files),
                    "Symlink source ancestor")
    links = {f.pin.path: posixpath.normpath(posixpath.join(
        posixpath.dirname(f.pin.path), f.data.decode())) for f in files if f.pin.mode == "120000"}
    for name, target in links.items():
        seen = set()
        seen.add(name)
        name = target
        while name in links:
            require(name not in seen, "Cyclic source link")
            seen.add(name)
            name = links[name]


def git(root: Path, args: list[str]) -> str:
    """Run read-only Git with explicit scratch/cache inheritance and no replacements."""
    environment = os.environ.copy()
    for key in ("TMPDIR", "NODE_COMPILE_CACHE", "BUN_RUNTIME_TRANSPILER_CACHE_PATH"):
        require(bool(environment.get(key)), "Missing subprocess environment: " + key)
    environment.update(PYTHONDONTWRITEBYTECODE="1", GIT_MASTER="1",
                       GIT_NO_REPLACE_OBJECTS="1", GIT_OPTIONAL_LOCKS="0")
    return subprocess.check_output(
        ["git", "-C", str(root), *args], env=environment, stderr=subprocess.PIPE,
        text=True).strip()


def local_sources(root: Path, lock: SourceLock) -> tuple[SourceFile, ...]:
    require(root.is_dir() and not root.is_symlink(), "Invalid source directory")
    require(git(root, ["rev-parse", "--show-toplevel"]) == str(root.resolve()),
            "Source must be a checkout root")
    try:
        ancestor = git(root, ["merge-base", lock.commit, "HEAD"])
    except subprocess.CalledProcessError as error:
        raise SourceError("Pinned source commit is not an ancestor") from error
    require(ancestor == lock.commit, "Pinned source commit is not an ancestor")
    require(not git(root, ["diff", "--name-only", lock.commit, "HEAD", "--",
                          *(lock.selected_paths or (pin.path for pin in lock.files))]),
            "Committed selected source differs from its pin")
    # Compare the locked tree, not the branch name or the mutable Git index.
    tree = git(root, ["-c", "core.quotePath=false", "ls-tree", "-r", lock.commit])
    identities = {}
    for line in tree.splitlines():
        header, name = line.split("\t", 1)
        mode, kind, blob = header.split()
        identities[name] = (mode, kind, blob)
    if lock.selected_paths:
        selected = {name for name in identities if any(
            path == "." or name == path or name.startswith(path + "/")
            for path in lock.selected_paths)}
        require(selected == {pin.path for pin in lock.files},
                "Locked files differ from committed selected closure")
    files = []
    for pin in lock.files:
        require(identities.get(pin.path) == (pin.mode, "blob", pin.blob),
                "Committed source identity differs: " + pin.path)
        path = root / pin.path
        require(not any(p.is_symlink() for p in path.parents), "Symlink source ancestor")
        mode = path.lstat().st_mode
        require((stat.S_ISLNK(mode) and pin.mode == "120000") or
                (stat.S_ISREG(mode) and pin.mode != "120000" and
                 stat.S_IMODE(mode) == int(pin.mode, 8) & 0o777),
                "Source mode differs: " + pin.path)
        if stat.S_ISLNK(mode):
            data = os.fsencode(os.readlink(path))
        else:
            require(path.stat().st_size == pin.size, "Source size differs: " + pin.path)
            with path.open("rb") as stream:
                data = stream.read(pin.size + 1)
        pin.verify(data)
        files.append(SourceFile(pin, data))
    check_links(tuple(files))
    return tuple(files)


def archive_sources(path: Path, lock: SourceLock) -> tuple[SourceFile, ...]:
    archive_pin = lock.archive
    if archive_pin is None:
        raise SourceError("No archive pinned for this source")
    require(path.is_file() and not path.is_symlink() and path.stat().st_size == archive_pin.size,
            "Archive size differs")
    with path.open("rb") as stream:
        require(hashlib.file_digest(stream, "sha256").hexdigest() == archive_pin.sha256,
                "Archive hash differs")
        stream.seek(0)
        expected = {pin.path: pin for pin in lock.files}
        directories = {str(p) for pin in lock.files for p in PurePosixPath(pin.path).parents}
        files, seen, expanded = [], set(), 0
        with tarfile.open(fileobj=stream, mode="r:*") as archive:
            for count, member in enumerate(archive, 1):
                require(count <= MAX_MEMBERS, "Too many archive members")
                name = relative(member.name.rstrip("/"))
                require(name not in seen, "Duplicate archive entry: " + name)
                seen.add(name)
                prefix, _, name = name.partition("/")
                require(prefix == archive_pin.root, "Wrong archive root")
                expanded += member.size
                require(0 <= member.size and expanded <= MAX_EXPANDED, "Archive expanded size exceeded")
                if member.isdir():
                    require(not name or name in directories, "Unexpected archive directory")
                    continue
                require(name in expected and (member.isfile() or member.issym()),
                        "Unexpected or special archive entry: " + name)
                pin = expected[name]
                require((member.issym() and pin.mode == "120000") or
                        (member.isfile() and pin.mode != "120000" and
                         member.mode & 0o111 == int(pin.mode, 8) & 0o111 and
                         member.mode & ~0o777 == 0), "Archive mode differs: " + name)
                if member.issym():
                    data = member.linkname.encode()
                else:
                    require(member.size == pin.size, "Archive file size differs: " + name)
                    with archive.extractfile(member) as source:
                        data = source.read()
                pin.verify(data)
                files.append(SourceFile(pin, data))
        require({f.pin.path for f in files} == set(expected), "Archive closure incomplete")
    check_links(tuple(files))
    return tuple(files)


def materialize(root: Path, files: tuple[SourceFile, ...]) -> None:
    """Write only already verified, frozen bytes into a new source tree."""
    root.mkdir()
    for source in files:
        path = root / source.pin.path
        path.parent.mkdir(parents=True, exist_ok=True)
        if source.pin.mode == "120000":
            path.symlink_to(source.data.decode())
        else:
            with path.open("xb") as stream:
                stream.write(source.data)
            path.chmod(int(source.pin.mode, 8) & 0o777)
