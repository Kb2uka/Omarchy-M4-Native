# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

SOURCE = (Path(__file__).resolve().parents[1] / 'tools/m4-ssd-root.sh').read_text()
ROOT_PART = 'cb2e29b7-ff84-41cc-a30f-b9c91b81e9ca'
ROOT_FS = '61924e2a-307d-4f34-9b04-4eef87d46729'
EFI_PART = '5870193f-b249-4d1e-ae24-c3a3fb0ebd9a'
IDENTITY = f'{ROOT_PART}\n{ROOT_FS}\n{EFI_PART}\n'
FIXTURE = r'''
record() { printf '%s\n' "$*" >> "$TRACE"; }
read_root_config() { record config; test "$MODE" != config; }
resolve_root() { record root; test "$MODE" != root || return 1; echo /dev/nvme0n1p5; }
resolve_efi() {
 record efi
 test "$MODE" != efi || return 1
 if test "$MODE" = same; then echo /dev/nvme0n1p5; else echo /dev/nvme0n1p4; fi
}
mount() { record "mount $*"; test "$MODE" != mount_failure; }
umount() {
 record "umount $*"
 UNMOUNT_COUNT=$((${UNMOUNT_COUNT:-0} + 1))
 test "$MODE" != umount_failure || return 1
 test "$MODE" != post_umount_failure || test "$UNMOUNT_COUNT" != 2
}
blockdev() {
 record "blockdev $*"
 case "$1" in
  --getro) printf '%s\n' "${ROOT_RO:-0}";;
  --setro) test "$MODE" != setro_failure || return 1
           if test "$MODE" != setro_ignored; then ROOT_RO=1; fi;;
  --setrw) test "$MODE" != setrw_failure || return 1
           if test "$MODE" != setrw_ignored; then ROOT_RO=0; fi;;
  *) return 1;;
 esac
}
sync() {
 new=n; final=n
 test ! -f "$SANDBOX/newroot/etc/m4-install-complete.new" || new=y
 test ! -f "$SANDBOX/newroot/etc/m4-install-complete" || final=y
 record "sync new=$new final=$final"
 SYNC_COUNT=$((${SYNC_COUNT:-0} + 1))
 if test "$SYNC_COUNT" = 1; then
  test "$#" = 3 && test "$1" = "$SANDBOX/newroot/etc/fstab" &&
   test "$2" = "$SANDBOX/newroot/etc/m4-install-complete.new" &&
   test "$3" = "$SANDBOX/newroot/etc" || return 1
 else
  test "$#" = 2 && test "$1" = "$SANDBOX/newroot/etc/m4-install-complete" &&
   test "$2" = "$SANDBOX/newroot/etc" || return 1
 fi
 test "$MODE" != "sync_failure_$SYNC_COUNT"
}
e2fsck() {
 record "e2fsck $*"
 case " $* " in *' -f '*) FULL_CHECK=1;; esac
 case "$MODE" in
  replay_record|replay_foreign)
   printf '%s\n%s\n%s\n' "$ROOT_PARTUUID" "$ROOT_FSUUID" "$EFI_PARTUUID" \
    > "$SANDBOX/newroot/etc/m4-install-complete"
   if test "$MODE" = replay_foreign; then
    printf 'foreign installation\n' > "$SANDBOX/newroot/etc/m4-install-complete"
   fi;;
  replay_init) chmod 755 "$SANDBOX/newroot/usr/lib/systemd/systemd";;
  lost_record) rm "$SANDBOX/newroot/etc/m4-install-complete";;
 esac
 return "$FSCK_STATUS"
}
resize2fs() {
 record "resize2fs $*"
 test "$MODE" != resize_failure || return 1
 test "$MODE" != interrupted || test "${FULL_CHECK:-0}" = 1
}
prepare_ssd_root
'''


class SSDRootTests(unittest.TestCase):
    def run_case(self, mode='valid', fsck=0, installed=False):
        with tempfile.TemporaryDirectory(prefix='m4 ssd root ') as directory:
            root = Path(directory)
            new = root / 'newroot'
            for name in ('etc', 'usr/lib/systemd', 'usr/lib/firmware'):
                (new / name).mkdir(parents=True, exist_ok=True)
            (new / 'etc/m4-root-id').write_text(ROOT_FS if mode != 'marker' else 'wrong')
            init = new / 'usr/lib/systemd/systemd'
            init.touch()
            init.chmod(0o644 if mode in ('init', 'replay_init') else 0o755)
            (new / 'etc/fstab').write_text('custom fstab\n')
            if installed:
                (new / 'etc/m4-install-complete').write_text(
                    'other installation\n' if mode == 'recorded' else IDENTITY)
            firmware = root / 'vendorfw'
            firmware.mkdir()
            if mode != 'firmware':
                (firmware / '.vendorfw.manifest').write_text('fixture manifest\n')
                (firmware / 'device.bin').write_bytes(b'device fixture')
            trace = root / 'trace'
            # Relocate filesystem paths only; real shell writes/copies stay inside
            # the temporary directory. Device calls are recorded and never run.
            source = SOURCE.replace('/newroot', '"$SANDBOX/newroot"').replace(
                '/vendorfw', '"$SANDBOX/vendorfw"').replace('/run/m4', '"$SANDBOX/run/m4"')
            result = subprocess.run(['sh', '-c', source + '\n' + FIXTURE], env=dict(
                PATH=os.defpath, LC_ALL='C', SANDBOX=directory, TRACE=str(trace),
                ROOT_PARTUUID=ROOT_PART, ROOT_FSUUID=ROOT_FS, EFI_PARTUUID=EFI_PART,
                MODE=mode, FSCK_STATUS=str(fsck), ROOT_RO='1' if mode == 'already_readonly' else '0'),
                capture_output=True, text=True)
            return result, trace.read_text().splitlines(), {
                'fstab': (new / 'etc/fstab').read_text(),
                'complete': (new / 'etc/m4-install-complete').read_text()
                            if (new / 'etc/m4-install-complete').exists() else None,
                'firmware': (new / 'usr/lib/firmware/device.bin').read_bytes()
                            if (new / 'usr/lib/firmware/device.bin').exists() else None,
                'verified': (root / 'run/m4/root-verified').read_text()
                            if (root / 'run/m4/root-verified').exists() else None,
            }

    def test_first_boot_verifies_then_expands_only_selected_root(self):
        result, trace, files = self.run_case()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([line.split()[0] for line in trace],
                         ['config', 'root', 'efi', 'blockdev', 'blockdev', 'blockdev',
                          'mount', 'umount', 'blockdev', 'blockdev', 'e2fsck',
                          'mount', 'umount', 'resize2fs', 'mount', 'sync', 'sync'])
        self.assertEqual(trace[3:6], ['blockdev --getro /dev/nvme0n1p5',
                                   'blockdev --setro /dev/nvme0n1p5',
                                   'blockdev --getro /dev/nvme0n1p5'])
        self.assertIn('-o ro,noload /dev/nvme0n1p5 ', trace[6])
        self.assertEqual(trace[8:11], ['blockdev --setrw /dev/nvme0n1p5',
                                     'blockdev --getro /dev/nvme0n1p5',
                                     'e2fsck -f -p /dev/nvme0n1p5'])
        self.assertIn('-o ro,noload /dev/nvme0n1p5 ', trace[11])
        self.assertEqual(trace[13], 'resize2fs /dev/nvme0n1p5')
        self.assertIn('-o rw /dev/nvme0n1p5 ', trace[14])
        self.assertEqual(trace[15:], ['sync new=y final=n', 'sync new=n final=y'])
        self.assertEqual(files['complete'], IDENTITY)
        self.assertEqual(files['verified'], IDENTITY)
        self.assertEqual(files['firmware'], b'device fixture')
        self.assertEqual(files['fstab'], f'PARTUUID={ROOT_PART} / ext4 defaults,noatime 0 1\n'
                         f'PARTUUID={EFI_PART} /boot/efi vfat defaults,noatime,umask=0077 0 2\n')

    def test_identity_and_read_only_failures_never_reach_disk_writes(self):
        for mode in ('config', 'root', 'efi', 'same', 'firmware', 'marker',
                     'mount_failure', 'umount_failure', 'recorded',
                     'already_readonly', 'setro_failure', 'setrw_failure',
                     'setro_ignored', 'setrw_ignored'):
            with self.subTest(mode=mode):
                result, trace, files = self.run_case(mode, installed=mode == 'recorded')
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any(line.startswith(('e2fsck', 'resize2fs')) or '-o rw' in line
                                     for line in trace), trace)
                self.assertIsNone(files['verified'])
                self.assertEqual(files['fstab'], 'custom fstab\n')
                if mode in ('marker', 'recorded'):
                    self.assertEqual(sum(line.startswith('umount ') for line in trace), 1)
                    self.assertFalse(any(line.startswith('blockdev --setrw') for line in trace))

    def test_fsck_and_resize_failures_stop_before_writable_mount(self):
        for status in (2, 4, 8, 16, 32, 128):
            with self.subTest(status=status):
                result, trace, files = self.run_case(fsck=status)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any(line.startswith('resize2fs') or '-o rw' in line for line in trace))
                self.assertIsNone(files['complete'])
        result, trace, files = self.run_case('resize_failure')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any('-o rw' in line for line in trace))
        self.assertIsNone(files['complete'])

    def test_existing_install_preserves_fstab_and_accepts_corrected_fsck(self):
        result, trace, files = self.run_case(fsck=1, installed=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(line.startswith('resize2fs') for line in trace))
        self.assertIn('e2fsck -p /dev/nvme0n1p5', trace)
        self.assertEqual(files['fstab'], 'custom fstab\n')
        self.assertEqual(files['complete'], IDENTITY)
        self.assertEqual(files['verified'], IDENTITY)
        self.assertEqual(files['firmware'], b'device fixture')

    def test_completion_flush_failures_prevent_verified_handoff(self):
        for mode in ('sync_failure_1', 'sync_failure_2'):
            with self.subTest(mode=mode):
                result, trace, files = self.run_case(mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertIsNone(files['verified'])
                if mode == 'sync_failure_1':
                    self.assertIsNone(files['complete'])

    def test_interrupted_first_install_rechecks_before_retrying_growth(self):
        result, trace, files = self.run_case('interrupted')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(trace.index('e2fsck -f -p /dev/nvme0n1p5'),
                        trace.index('resize2fs /dev/nvme0n1p5'))
        self.assertEqual(files['complete'], IDENTITY)

    def test_replayed_completion_preserves_fstab_and_skips_growth(self):
        result, trace, files = self.run_case('replay_record')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(line.startswith('resize2fs') for line in trace))
        self.assertEqual(files['fstab'], 'custom fstab\n')
        self.assertEqual(files['complete'], IDENTITY)
        self.assertEqual(files['verified'], IDENTITY)

    def test_replayed_systemd_is_checked_after_filesystem_recovery(self):
        result, trace, files = self.run_case('replay_init')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(files['verified'], IDENTITY)

    def test_invalid_replayed_state_stops_before_growth_or_writable_mount(self):
        for mode, installed in (('init', False), ('init', True),
                                ('replay_foreign', False), ('replay_foreign', True),
                                ('lost_record', True), ('post_umount_failure', False)):
            with self.subTest(mode=mode, installed=installed):
                result, trace, files = self.run_case(mode, installed=installed)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any(line.startswith('resize2fs') or '-o rw' in line
                                     for line in trace))
                self.assertEqual(files['fstab'], 'custom fstab\n')
                self.assertIsNone(files['firmware'])
                self.assertIsNone(files['verified'])
                self.assertEqual(sum(line.startswith('mount ') for line in trace), 2)
                self.assertEqual(sum(line.startswith('umount ') for line in trace), 2)


if __name__ == '__main__':
    unittest.main()
