#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Report a failed desktop handoff without interrupting setup or a graphical session."""
from array import array
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile


PENDING = 'var/lib/omarchy/provisioning/pending'
STATUS_LOG = 'var/log/omarchy-m4-boot-status.json'
PROPERTIES = ('ActiveState', 'SubState', 'Result', 'ExecMainCode', 'ExecMainStatus')
UNITS = ('display-manager.service', 'omarchy-provision-owner.service',
         'getty@tty1.service', 'm4-gpu.service', 'm4-video.service',
         'm4-j713-audio-register.service')
NOTICE = '''The desktop could not start.

Press Ctrl+Alt (Option)+F2 for the recovery login.
Use the account and password you created during setup.

If this Air is not connected to M4 updates yet, connect it now: GPU fixes
arrive that way. Join Wi-Fi with nmtui, run this line, then restart:
  curl -fsSL --retry 5 --retry-all-errors https://github.com/Kb2uka/Omarchy-M4-Native/releases/download/packages/m4-connect.sh | bash

To inspect the graphics failure:
  sudo journalctl -b -u m4-gpu.service -u sddm.service --no-pager
  sudo journalctl -b -k --grep 'G16|asahi' --no-pager

Service status is saved in /var/log/omarchy-m4-boot-status.json
'''


def service_state(unit):
    try:
        result = subprocess.run(
            ['/usr/bin/systemctl', 'show', '--no-pager', '--property=' + ','.join(PROPERTIES), unit],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=2, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return {'query': 'unavailable'}
    if result.returncode:
        return {'query': 'unavailable'}
    # No descriptions, command lines, environment, journal or provisioning log:
    # these five systemd properties contain only bounded service state values.
    fields = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition('=')
        if separator and key in PROPERTIES and re.fullmatch(r'[A-Za-z0-9_-]{0,64}', value):
            fields.setdefault(key, value)
    return fields


def pending(root):
    path = root / PENDING
    return path.exists() or path.is_symlink()


def unavailable(state):
    return state.get('ActiveState') in ('inactive', 'failed')


def owner_finished(state):
    return (state.get('ActiveState'), state.get('SubState')) in (
        ('active', 'exited'), ('inactive', 'dead'), ('failed', 'failed'))


def save_status(root, units):
    path = root
    for part in Path(STATUS_LOG).parts[:-1]:
        path /= part
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise ValueError('Unsafe boot status directory')
        path.mkdir(mode=0o755, exist_ok=True)
    path /= Path(STATUS_LOG).name
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError('Unsafe boot status file')
    raw = (json.dumps({'schema': 1, 'time': datetime.now(timezone.utc).isoformat(),
                      'units': units}, indent=2, sort_keys=True) + '\n').encode('ascii')
    if len(raw) > 8192:
        raise ValueError('Boot status exceeded its limit')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.m4-boot-status-', delete=False) as stream:
            temporary = stream.name
            os.fchmod(stream.fileno(), 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            os.unlink(temporary)


def write_console_notice():
    descriptor = None
    try:
        descriptor = os.open('/dev/tty1', os.O_WRONLY | os.O_NOCTTY | os.O_NONBLOCK | os.O_NOFOLLOW)
        if not stat.S_ISCHR(os.fstat(descriptor).st_mode):
            return False
        mode = array('i', [0])
        fcntl.ioctl(descriptor, 0x4B3B, mode, True)  # KDGETMODE: never draw over KD_GRAPHICS.
        if mode[0] != 0:
            return False
        payload = ('\033[0m\033[?25h\033[2J\033[H' + NOTICE.replace('\n', '\r\n')).encode('ascii')
        while payload:
            written = os.write(descriptor, payload)
            if written <= 0:
                return False
            payload = payload[written:]
        return True
    except OSError:
        return False
    finally:
        if descriptor is not None:
            os.close(descriptor)


def observe(root=Path('/')):
    root = Path(root)
    if pending(root):
        return {'status': 'setup-pending'}
    units = {'display-manager.service': service_state('display-manager.service')}
    if not unavailable(units['display-manager.service']):
        return {'status': 'desktop-active-or-unknown'}
    units['omarchy-provision-owner.service'] = service_state('omarchy-provision-owner.service')
    if not owner_finished(units['omarchy-provision-owner.service']):
        return {'status': 'setup-active-or-unknown'}
    for unit in UNITS[2:]:
        units[unit] = service_state(unit)
    save_status(root, units)
    # A restart may finish while state is collected. Recheck immediately before
    # opening tty1, and leave an existing text login untouched as well.
    shown = False
    if not pending(root) and unavailable(service_state('display-manager.service')) and \
            unavailable(service_state('getty@tty1.service')) and \
            owner_finished(service_state('omarchy-provision-owner.service')):
        shown = write_console_notice()
    return {'status': 'desktop-unavailable', 'notice': shown}


def main():
    if os.geteuid() != 0:
        print('Run boot status through its system service.', file=sys.stderr)
        return 1
    try:
        print(json.dumps(observe()))
        return 0
    except (OSError, ValueError):
        print('Could not retain the boot status record.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
