# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

SOURCE = (Path(__file__).resolve().parents[1] / 'tools/m4-root-identity.sh').read_text()
PART = '5870193f-b249-4d1e-ae24-c3a3fb0ebd9a'
FIXTURE = r'''
test() {
 case "$1" in
 -b) command test "$MODE" != not_block && command test "$2" = /dev/loop0p1;;
 -f) command test "$MODE" != not_partition && command test "$2" = /sys/class/block/loop0p1/partition;;
 *) command test "$@";; esac
}
readlink() {
 case "$2" in
 /dev/loop0p1) echo /dev/loop0p1;;
 /sys/class/block/loop0p1/..)
  if command test "$MODE" = wrong_parent; then echo /sys/devices/loop1; else echo /sys/devices/loop0; fi;;
 *) return 1;; esac
}
blkid() {
 printf '%s\n' "$*" >> "$TRACE"
 case "$1" in
 -c)
  command test "$*" = "-c /dev/null -t PARTUUID=$PART -o device /dev/loop0p*" || return 1
  case "$MODE" in missing) return 2;; duplicate) printf '/dev/loop0p1\n/dev/loop0p2\n';; *) echo /dev/loop0p1;; esac;;
 -p)
  case "$3:$MODE" in
  PART_ENTRY_UUID:wrong_part) echo cb2e29b7-ff84-41cc-a30f-b9c91b81e9ca;;
  PART_ENTRY_UUID:*) echo "$PART";;
  UUID:*) echo AB12-CD34;;
  TYPE:wrong_type) echo ext4;; TYPE:*) echo vfat;;
  PART_ENTRY_TYPE:wrong_gpt) echo 0fc63daf-8483-4772-8e79-3d69d8477de4;;
  PART_ENTRY_TYPE:*) echo c12a7328-f81f-11d2-ba4b-00a0c93ec93b;;
  *) return 1;; esac;;
 *) return 1;; esac
}
resolve_efi "$PART" "$PARENT"
'''


class EFIResolutionTests(unittest.TestCase):
    def run_case(self, mode, part=PART, parent='loop0'):
        with tempfile.TemporaryDirectory(prefix='efi identity ') as directory:
            trace = Path(directory) / 'trace'
            result = subprocess.run(['sh', '-f', '-c', SOURCE + '\n' + FIXTURE],
                env=dict(PATH=os.defpath, LC_ALL='C', MODE=mode, PART=part,
                         PARENT=parent, TRACE=str(trace)), capture_output=True, text=True)
            return result, trace.read_text().splitlines() if trace.exists() else []

    def test_fat_efi_on_selected_parent(self):
        result, trace = self.run_case('valid')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '/dev/loop0p1\n')
        self.assertEqual(trace[0], f'-c /dev/null -t PARTUUID={PART} -o device /dev/loop0p*')

    def test_wrong_device_or_identity_rejects(self):
        for mode in ('missing', 'duplicate', 'not_block', 'not_partition',
                     'wrong_parent', 'wrong_part', 'wrong_type', 'wrong_gpt'):
            with self.subTest(mode=mode):
                result, _ = self.run_case(mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, '')

    def test_bad_selector_never_probes(self):
        for part, parent in [('bad', 'loop0'), (PART, 'nvme0n2'), (PART, 'loop'),
                             (PART, 'loop0/../nvme0n1')]:
            with self.subTest(part=part, parent=parent):
                result, trace = self.run_case('valid', part, parent)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(trace, [])


if __name__ == '__main__':
    unittest.main()
