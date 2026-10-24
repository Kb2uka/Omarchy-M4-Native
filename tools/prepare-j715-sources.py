# SPDX-License-Identifier: MIT
"""Prepare isolated, verified J715 inputs, never install or activate them."""
import argparse
import json
import shutil
import subprocess
import sys
import tarfile
from dataclasses import asdict
from pathlib import Path

from j715_sources import (
    SourceLock,
    archive_sources,
    digest,
    git,
    local_sources,
    materialize,
    require,
)

REPO = Path(__file__).resolve().parents[1]


def prepare(donor: Path, output: Path, *, archive: Path | None = None) -> Path:
    """Keep the untouched donor original separate from the future patched build."""
    output = output.absolute()
    require(not output.exists() and not output.is_symlink(), "Output must be new")
    require(not any(p.is_symlink() for p in output.parents), "Symlink output ancestor")
    require(output.parent.is_dir(), "Output parent must exist")
    output = output.resolve()
    for source in (donor, REPO):
        require(not output.is_relative_to(source.resolve()), "Output overlaps source input")
    audio = SourceLock.read(REPO / "audio/j715/source-lock.json")
    display = SourceLock.read(REPO / "display/j715/source-lock.json")
    originals = archive_sources(archive, audio) if archive is not None else local_sources(donor, audio)
    reused = local_sources(REPO, display)
    # Every consumed byte is frozen and verified before output creation.
    output.mkdir()
    try:
        materialize(output / "original", originals)
        materialize(output / "build", originals)
        materialize(output / "upstream-original", reused)
        for name, lock in (("audio-source-lock.json", audio), ("display-source-lock.json", display)):
            (output / name).write_bytes(lock.raw)
        receipt = {
            "schema": 1, "hardware_qualified": False,
            "input_form": "pinned-archive" if archive is not None else "local-checkout",
            "archive_verified": archive is not None,
            "history": "unavailable-in-archive" if archive is not None else "HEAD-and-locked-blobs-verified",
            "checkout_shallow": None if archive is not None else
                git(donor, ["rev-parse", "--is-shallow-repository"]) == "true",
            "input_identity": json.loads(audio.raw)["archive"] if archive is not None else
                {"repository": audio.repository, "commit": audio.commit},
            "original": "original", "build": "build", "build_patches": [],
            "audio": {"repository": audio.repository, "commit": audio.commit,
                      "lock_sha256": digest(audio.raw), "files": [asdict(f.pin) for f in originals]},
            "display": {"repository": display.repository, "commit": display.commit,
                        "lock_sha256": digest(display.raw), "files": [asdict(f.pin) for f in reused]},
        }
        (output / "source-receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    except (OSError, UnicodeError):
        shutil.rmtree(output)
        raise
    return output / "source-receipt.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--donor-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--archive", type=Path, help="Explicit local archive; never downloaded")
    args = parser.parse_args(argv)
    try:
        receipt = prepare(args.donor_source, args.output, archive=args.archive)
    except (OSError, subprocess.CalledProcessError, KeyError, TypeError, ValueError,
            tarfile.TarError, EOFError) as error:
        print(f"Source preparation refused: {error}", file=sys.stderr)
        return 1
    print(receipt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
