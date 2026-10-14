# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
import hashlib
import json
from pathlib import Path
import subprocess

assert hashlib.sha256(Path("/System/Library/Components/AudioDSP.component/Contents/MacOS/AudioDSP").read_bytes()).hexdigest() == json.loads(Path("owner-inputs.json").read_text())["AudioDSP"]
rows = []
for bypass, amplitude in ((0, "0.0001"), (0, "0.0002"), (1, "0.0001")):
    for lane in range(4):
        name = f"bypass{bypass}-amp{amplitude}-lane{lane}"
        raw = Path("output") / (name + ".f32")
        run = subprocess.run(["./measure-transfer", "decoded/Tone3.aupreset", str(raw), str(lane), amplitude, str(bypass)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
        (Path("output") / (name + ".stdout")).write_text(run.stdout)
        (Path("output") / (name + ".stderr")).write_text(run.stderr)
        if run.returncode:
            raise RuntimeError((name, run.returncode, run.stderr))
        row = json.loads(run.stdout)
        row.update(file=raw.name, sha256=hashlib.sha256(raw.read_bytes()).hexdigest(), bytes=raw.stat().st_size)
        rows.append(row)
        print(json.dumps(row), flush=True)
Path("output/transfers.json").write_text(json.dumps(rows, indent=2) + "\n")
