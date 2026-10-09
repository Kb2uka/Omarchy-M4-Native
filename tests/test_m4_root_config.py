# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
PROPERTIES = {
    "omarchy,root-partition": "cb2e29b7-ff84-41cc-a30f-b9c91b81e9ca",
    "omarchy,root-filesystem": "61924e2a-307d-4f34-9b04-4eef87d46729",
    "gravity,efi-system-partition": "5870193f-b249-4d1e-ae24-c3a3fb0ebd9a",
}


class RootConfigurationTests(unittest.TestCase):
    def read(self, replacements=None):
        with tempfile.TemporaryDirectory(prefix="m4 chosen ") as directory:
            for name, value in PROPERTIES.items():
                data = (replacements or {}).get(name, value.encode() + b"\0")
                if data is not None:
                    (Path(directory) / name).write_bytes(data)
            env = {"PATH": os.defpath, "LC_ALL": "C", "CHOSEN": directory,
                   "REPO": str(REPO), "ROOT_PARTUUID": "ambient", "ROOT_FSUUID": "ambient",
                   "EFI_PARTUUID": "ambient"}
            return subprocess.run(["sh", "-c", '''
. "$REPO/tools/m4-root-identity.sh"
. "$REPO/tools/m4-root-config.sh"
read_root_config "$CHOSEN" || exit 1
printf '%s\\n%s\\n%s\\n' "$ROOT_PARTUUID" "$ROOT_FSUUID" "$EFI_PARTUUID"
'''], env=env, capture_output=True, text=True)

    def test_exact_chosen_properties(self):
        result = self.read()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), list(PROPERTIES.values()))

    def test_each_malformed_or_missing_property_rejects(self):
        for name, value in PROPERTIES.items():
            for data in (None, b"", value.encode(), value.upper().encode() + b"\0",
                         value.encode() + b"\n", b"\0" + value.encode(),
                         value.encode() + b"\0\0", ("z" + value[1:]).encode() + b"\0"):
                with self.subTest(name=name, data=data):
                    result = self.read({name: data})
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(result.stdout, "")

    def test_efi_and_linux_partition_must_differ(self):
        result = self.read({"gravity,efi-system-partition":
                            PROPERTIES["omarchy,root-partition"].encode() + b"\0"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
