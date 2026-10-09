# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
from pathlib import Path
import os
import subprocess
import tempfile
# Contaminate this process deliberately; fixtures get only their declared env.
os.environ.update(PART='ambient', FS='ambient', EXPANSION='ambient')
source=(Path(__file__).resolve().parents[1] / 'tools' / 'm4-root-identity.sh').read_text()
fixture=r'''
part=${PART:-71b71300-2026-4000-8000-000000000008}
fs=${FS:-19b71300-2026-4000-8000-000000000008}
test() {
 case "$1" in -b) command test "$2" = /dev/loop0p1;; -f) command test "$FIXTURE" != not_partition && command test "$2" = /sys/class/block/loop0p1/partition;; *) command test "$@";; esac
}
readlink() {
 case "$2" in /dev/loop0p1) printf '/dev/loop0p1\n';;
 /sys/class/block/loop0p1/..) if command test "$FIXTURE" = wrong_parent; then printf '/sys/devices/virtual/block/loop1\n'; else printf '/sys/devices/virtual/block/loop0\n'; fi;;
 *) return 1;; esac
}
findfs() {
 printf 'findfs %s\n' "$*" >> "$TRACE"
 case "$FIXTURE" in missing) return 2;; duplicate) printf '/dev/loop0p1\n/dev/loop0p2\n';; *) printf '/dev/loop0p1\n';; esac
}
blkid() {
 printf 'blkid %s\n' "$*" >> "$TRACE"
 case "$1" in
 -c)
  command test "$#" -ge 7 || return 1
  command test "$2" = /dev/null || return 1
  command test "$3" = -t || return 1
  command test "$4" = "PARTUUID=$part" || return 1
  command test "$5" = -o || return 1
  command test "$6" = device || return 1
  if command test "${EXPANSION:-0}" = 1; then
   command test "$#" = 8 || return 1
   command test "$7" = /dev/loop0p1 || return 1
   command test "$8" = /dev/loop0p2 || return 1
  else
   command test "$#" = 7 || return 1
   command test "$7" = '/dev/loop0p*' || return 1
  fi
  case "$FIXTURE" in missing) return 2;; duplicate) printf '/dev/loop0p1\n/dev/loop0p2\n';; *) printf '/dev/loop0p1\n';; esac;;
 -p)
  case "$3:$FIXTURE" in
   PART_ENTRY_UUID:wrong_part) echo "$fs";;
   UUID:wrong_fs) echo "$part";;
   TYPE:wrong_type) echo vfat;;
   PART_ENTRY_TYPE:wrong_gpt) echo c12a7328-f81f-11d2-ba4b-00a0c93ec93b;;
   PART_ENTRY_UUID:*) echo "$part";; UUID:*) echo "$fs";; TYPE:*) echo ext4;;
   PART_ENTRY_TYPE:*) echo 0fc63daf-8483-4772-8e79-3d69d8477de4;; *) return 1;; esac;;
 *) return 1;; esac
}
resolve_root "$part" "$fs" "$PARENT"
'''
cases = [
    ('selected', 'loop0', 'valid', True),
    ('missing', 'loop0', 'missing', False),
    ('duplicate', 'loop0', 'duplicate', False),
    ('other_namespace', 'nvme0n2', 'valid', False),
    ('bad_parent', 'loop0/../../nvme0n1', 'valid', False),
    ('multiline_parent', 'loop0\nx', 'valid', False),
    ('empty_loop_number', 'loop', 'valid', False),
    ('nondigit_loop_number', 'loop0x', 'valid', False),
    *[(mode, 'loop0', mode, False) for mode in (
        'wrong_part', 'wrong_fs', 'wrong_type', 'wrong_gpt', 'wrong_parent', 'not_partition',
    )],
]
for label, parent, mode, success in cases:
    with tempfile.TemporaryDirectory() as directory:
        trace = Path(directory) / 'commands'
        env = dict(PATH=os.defpath, LC_ALL='C', PARENT=parent, FIXTURE=mode, TRACE=str(trace))
        # Keep mocked candidates independent of real devices on the test host.
        result = subprocess.run(
            ['sh', '-f', '-c', source + '\n' + fixture],
            env=env, capture_output=True, text=True,
        )
        assert (result.returncode == 0) == success, (
            label, result.returncode, result.stdout, result.stderr,
        )
        commands = trace.read_text().splitlines() if trace.exists() else []
        if parent == 'loop0':
            # Resolution must query only this parent's candidates, with no cache.
            assert commands and commands[0] == (
                'blkid -c /dev/null -t '
                'PARTUUID=71b71300-2026-4000-8000-000000000008 '
                '-o device /dev/loop0p*'
            ), (label, commands)
        else:
            assert not commands, (label, commands)
        assert not any(command.startswith('findfs ') for command in commands)
        if success:
            assert result.stdout == '/dev/loop0p1\n'
    print('RESTRICTED_ROOT_RESOLVE_PASS', label)

for field in ('PART', 'FS'):
    with tempfile.TemporaryDirectory() as directory:
        trace = Path(directory) / 'commands'
        env = dict(PATH=os.defpath, LC_ALL='C', PARENT='loop0', FIXTURE='valid', TRACE=str(trace))
        baseline = subprocess.run(['sh', '-f', '-c', source + '\n' + fixture], env=env, capture_output=True)
        assert baseline.returncode == 0, (field, baseline.stderr)
        trace.unlink()
        env[field] = 'zzzzzzzz-2026-4000-8000-000000000008'
        result = subprocess.run(['sh', '-f', '-c', source + '\n' + fixture], env=env, capture_output=True)
        assert result.returncode != 0, field
        assert not trace.exists(), field
    print('MALFORMED_ROOT_RESOLVE_PASS', field)

# Relocate only device paths to a private directory. Keep real shell globbing
# enabled and require both partition arguments; no host /dev entries are used.
with tempfile.TemporaryDirectory(prefix='m4 root glob ') as directory:
    dev = Path(directory) / 'dev'
    dev.mkdir()
    for name in ('loop0p1', 'loop0p2', 'loop1p1'):
        (dev / name).touch()
    trace = Path(directory) / 'commands'
    env = dict(PATH=os.defpath, LC_ALL='C', PARENT='loop0', FIXTURE='valid', TRACE=str(trace), EXPANSION='1')
    code = (source + '\n' + fixture).replace('/dev/', './dev/')
    result = subprocess.run(['sh', '-c', code], cwd=directory, env=env, capture_output=True, text=True)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stdout == './dev/loop0p1\n'
    assert trace.read_text().splitlines()[0] == (
        'blkid -c ./dev/null -t PARTUUID=71b71300-2026-4000-8000-000000000008 '
        '-o device ./dev/loop0p1 ./dev/loop0p2'
    )
print('MULTI_PARTITION_GLOB_PASS')
