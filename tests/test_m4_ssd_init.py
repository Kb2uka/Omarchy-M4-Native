# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

SOURCE = (Path(__file__).resolve().parents[1] / 'tools/m4-ssd-init').read_text()


class SSDInitTests(unittest.TestCase):
    def run_init(self, mode='valid'):
        with tempfile.TemporaryDirectory(prefix='m4 init ') as directory:
            root = Path(directory)
            for name in ('bin', 'dev', 'lib', 'newroot', 'proc', 'sys', 'run'):
                (root / name).mkdir()
            (root / 'dev/console').touch()
            (root / 'lib/m4-root-identity.sh').write_text('''
resolve_root() {
 ROOT_TRIES=$((${ROOT_TRIES:-0} + 1))
 if test "$MODE" = delayed_root && test "$ROOT_TRIES" = 1; then return 1; fi
 printf '/dev/nvme0n1p5\n'
}
resolve_efi() {
 EFI_TRIES=$((${EFI_TRIES:-0} + 1))
 if test "$MODE" = delayed_efi && test "$EFI_TRIES" = 1; then return 1; fi
 printf '/dev/nvme0n1p4\n'
}
''')
            (root / 'lib/m4-root-config.sh').write_text('''
read_root_config() {
 record config
 ROOT_PARTUUID=root ROOT_FSUUID=filesystem EFI_PARTUUID=efi
 test "$MODE" != bad_identity
}
''')
            (root / 'lib/m4-ssd-root.sh').write_text('''
prepare_ssd_root() { record prepare; test "$MODE" != bad_root; }
''')
            switch = root / 'bin/switch_root'
            switch.write_text('''#!/bin/sh
printf 'environment:%s:%s:%s:%s\\n' "${SYSTEMD_IN_CHROOT-unset}" "${SYSTEMD_UNIT_PATH-unset}" "${SYSTEMD_GENERATOR_PATH-unset}" "${SYSTEMD_ENVIRONMENT_GENERATOR_PATH-unset}" >> "$TRACE"
printf 'handoff %s %s\\n' "$PPID" "$*" >> "$TRACE"
''')
            switch.chmod(0o755)
            # Relocate regular file accesses before executing. Privileged
            # operations are shell fixtures; the final exec only records argv.
            path_line = 'export PATH=/usr/bin:/bin:/sbin:/usr/sbin'
            applets = '/bin/busybox --install -s /bin'
            self.assertEqual(SOURCE.count(path_line), 1, 'Update sandbox for changed PATH')
            self.assertEqual(SOURCE.count(applets), 1, 'Update sandbox for changed applet setup')
            self.assertRegex(SOURCE, r'(?m)^(?:exec )?switch_root ',
                             'Update sandbox for changed handoff command')
            code = SOURCE.replace(applets, 'busybox --install -s /bin').replace(
                path_line, 'export PATH="$SANDBOX/bin"')
            # One pass over shell tokens never rewrites an inserted sandbox path
            # or the systemd path relative to the new root.
            paths = r'"/newroot/\$name"|(?<![\w/])/(?:dev/console|lib/[\w./-]+|newroot|proc|sys(?:/firmware/devicetree/base/chosen)?|run)(?![\w/])'
            code = re.sub(paths, lambda match: '"${SANDBOX}' + match[0].strip('"') + '"', code)
            fixtures = '''
record() { printf '%s\n' "$*" >> "$TRACE"; }
busybox() { record install-applets; }
mount() { record "mount $*"; }
mkdir() { record "mkdir $*"; }
insmod() { record "insmod $*"; test "$MODE" != bad_module; }
seq() { record "wait-count $*"; printf '1\n2\n'; }
sleep() { record "sleep $*"; test "$*" = 1 || exit 99; }
'''
            result = subprocess.run(['sh', '-c', fixtures + code],
                env=dict(PATH=os.defpath, SANDBOX=directory, TRACE=str(root / 'trace'), MODE=mode,
                         SYSTEMD_IN_CHROOT='yes', SYSTEMD_UNIT_PATH='/wrong/units',
                         SYSTEMD_GENERATOR_PATH='/wrong/generators',
                         SYSTEMD_ENVIRONMENT_GENERATOR_PATH='/wrong/environment'),
                capture_output=True, text=True)
            trace_path = root / 'trace'
            trace = trace_path.read_text().splitlines() if trace_path.exists() else []
            return result, trace, (root / 'dev/console').read_text(), directory

    def test_verified_root_moves_all_runtime_mounts_before_graphical_handoff(self):
        result, trace, console, directory = self.run_init()
        self.assertEqual(result.returncode, 0, result.stderr + console)
        loads = [line for line in trace if line.startswith('insmod ')]
        self.assertEqual(len(loads), 1)
        self.assertIn('nvme-apple.ko', loads[0])
        moves = [line for line in trace if line.startswith('mount --move ')]
        self.assertEqual(moves, [f'mount --move /{name} {directory}/newroot/{name}'
                                 for name in ('dev', 'proc', 'sys', 'run')])
        self.assertLess(trace.index('prepare'), trace.index(moves[0]))
        self.assertIn('environment:unset:unset:unset:unset', trace)
        self.assertEqual(trace[-1], f'handoff {os.getpid()} {directory}/newroot '
                         '/usr/lib/systemd/systemd --system --unit=graphical.target '
                         '--log-target=kmsg --show-status=yes')

    def test_waits_for_root_and_efi_before_preparation(self):
        for mode in ('delayed_root', 'delayed_efi'):
            with self.subTest(mode=mode):
                result, trace, console, _ = self.run_init(mode)
                self.assertEqual(result.returncode, 0, result.stderr + console)
                self.assertIn('wait-count 1 30', trace)
                self.assertEqual(trace.count('sleep 1'), 1)
                self.assertLess(trace.index('sleep 1'), trace.index('prepare'))

    def test_bad_identity_module_or_root_never_hands_off(self):
        for mode in ('bad_identity', 'bad_module', 'bad_root'):
            with self.subTest(mode=mode):
                result, trace, console, _ = self.run_init(mode)
                self.assertEqual(result.returncode, 99, result.stderr + console)
                self.assertFalse(any(line.startswith(('handoff ', 'mount --move ')) for line in trace))
                self.assertIn('M4_SSD_BOOT_FAILED:', console)


if __name__ == '__main__':
    unittest.main()
