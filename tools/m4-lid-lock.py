#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Lock the J713 on lid close (issue #84), for installs that predate the image fix.

omarchy-m4-boot's install script runs this with --hook on every
omarchy-m4-update, so an installed Air gets what a new image already carries
(docs/j713-desktop-image.md, "Lid close and the lock screen"):

- The lock screen's PAM stack, /etc/pam.d/omarchy-lock-password. Omarchy's
  installer writes it with omarchy-apply-lock; an offline image never ran that,
  so the lock screen refused every lock with missing-pam. Written only when the
  file is missing, from the stack in the packaged omarchy-apply-lock, and
  refused for a helper this tool does not recognise.
- The two lid binds, appended once to each user's own ~/.config/hypr/hyprland.lua.
  Only an Omarchy Lua configuration that lacks the marker line gets them, and
  the append runs as that user, never as root. This tool reloads nothing, but a
  running Hyprland reloads a changed configuration, so the update asks for a
  fresh login (a reload leaves later-added input devices ignored).

Only on the J713. Inspection is the default and changes nothing.
"""
import argparse
import json
import os
from pathlib import Path
import pwd
import re
import stat
import sys

BOARD = b'apple,j713'
COMPATIBLE = 'sys/firmware/devicetree/base/compatible'
APPLY_LOCK = 'usr/bin/omarchy-apply-lock'
LOCK_PAM = 'etc/pam.d/omarchy-lock-password'
CONFIG = '.config/hypr/hyprland.lua'
OMARCHY_CONFIG = 'require("default.hypr.omarchy")'
# The same marker and binds as the image (tools/configure-m4-desktop.py) and the
# documented existing-install step, so none of them ever adds the binds twice.
MARKER = '-- J713 lid (issue #84)'
LID_BINDS = (MARKER + ': lock on close, light the lock screen on open.\n'
             'o.bind("switch:on:Apple SMC power/lid events", nil, "omarchy-system-lock", { locked = true })\n'
             'o.bind("switch:off:Apple SMC power/lid events", nil, "omarchy-brightness-display on", { locked = true })\n')
FIRST_UID, LAST_UID = 1000, 60000


def lock_pam_stack(helper_text):
    """The password PAM stack omarchy-apply-lock writes, or None for an unknown helper."""
    stacks = re.findall(r"\btee /etc/pam\.d/omarchy-lock-password >/dev/null <<'EOF'\n(.*?\n)EOF\n",
                        helper_text, re.S)
    if len(stacks) != 1 or not stacks[0].startswith('#%PAM-1.0\n'):
        return None
    return stacks[0]


def is_j713(root):
    try:
        return BOARD in (root / COMPATIBLE).read_bytes().split(b'\0')
    except OSError:
        return False


def lock_pam(root, apply):
    target = root / LOCK_PAM
    if os.path.lexists(target):
        return 'present'
    try:
        stack = lock_pam_stack((root / APPLY_LOCK).read_text(encoding='utf-8'))
    except OSError:
        return 'refused: no ' + APPLY_LOCK
    if stack is None:
        return 'refused: unrecognised ' + APPLY_LOCK
    if not apply:
        return 'missing'
    temp = target.with_name('.' + target.name + '.m4-lid-lock')
    temp.unlink(missing_ok=True)  # left by an interrupted run; the directory is root's
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            os.fchmod(stream.fileno(), 0o644)  # whatever the umask
            stream.write(stack)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, target)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    return 'written'


def add_binds(path, apply):
    """Append the binds to one Omarchy configuration. Runs with the owner's rights."""
    try:
        # O_NONBLOCK: a FIFO in its place must not hang the update that runs this.
        fd = os.open(path, (os.O_RDWR | os.O_APPEND if apply else os.O_RDONLY) | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return 'no configuration'
    except OSError as error:
        return 'skipped: ' + (error.strerror or str(error))
    with os.fdopen(fd, 'r+b' if apply else 'rb', buffering=0) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            return 'skipped: not a regular file'
        text = stream.read().decode('utf-8', 'replace')
        if MARKER in text:
            return 'present'
        if OMARCHY_CONFIG not in text:
            return 'skipped: not an Omarchy configuration'
        if not apply:
            return 'missing'
        stream.write((('' if not text or text.endswith('\n') else '\n') + LID_BINDS).encode())
        os.fsync(stream.fileno())
    return 'added; takes effect at next login'


def as_user(account, function, *args):
    """Run function in a child that has dropped to the account's own uid and groups."""
    read, write = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(read)
        try:
            os.setgroups([])
            os.initgroups(account.pw_name, account.pw_gid)
            os.setgid(account.pw_gid)
            os.setuid(account.pw_uid)
            result = function(*args)
        except BaseException as error:  # never return into the parent's code
            result = 'skipped: ' + str(error)
        os.write(write, result.encode())
        os._exit(0)
    os.close(write)
    with os.fdopen(read, 'rb') as stream:
        result = stream.read().decode()
    os.waitpid(child, 0)
    return result or 'skipped: no result'


def users(root):
    """Login accounts with a home under root/home, from root's own passwd."""
    accounts = []
    for line in (root / 'etc/passwd').read_text(encoding='utf-8').splitlines():
        fields = line.split(':')
        if len(fields) != 7 or not fields[2].isdigit() or not fields[3].isdigit():
            continue
        name, _, uid, gid, gecos, home, shell = fields
        if FIRST_UID <= int(uid) < LAST_UID and home.startswith('/home/') and not shell.endswith('nologin'):
            accounts.append(pwd.struct_passwd((name, 'x', int(uid), int(gid), gecos, home, shell)))
    return accounts


def run(root, apply, switch_user=True):
    if not is_j713(root):
        return {'status': 'not a J713; nothing to do'}
    try:
        pam = lock_pam(root, apply)
    except OSError as error:  # the binds still go in
        pam = 'refused: ' + str(error)
    report = {'lock_pam': pam, 'lid_binds': {}}
    for account in users(root):
        path = root / account.pw_dir.lstrip('/') / CONFIG
        if switch_user:
            report['lid_binds'][account.pw_name] = as_user(account, add_binds, path, apply)
        else:
            report['lid_binds'][account.pw_name] = add_binds(path, apply)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--apply', action='store_true', help='write what is missing (root only)')
    action.add_argument('--hook', action='store_true', help=argparse.SUPPRESS)  # the package's install script
    parser.add_argument('--root', type=Path, default=Path('/'), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    apply = args.apply or args.hook
    if args.root == Path('/') and apply and os.geteuid() != 0:
        print('m4-lid-lock: run with sudo', file=sys.stderr)
        return 1
    try:
        report = run(args.root, apply, switch_user=os.geteuid() == 0)
    except (OSError, ValueError) as error:
        print('m4-lid-lock: refused: ' + str(error), file=sys.stderr)
        return 0 if args.hook else 1  # a refusal never fails the package upgrade
    if not args.hook:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report.get('lock_pam') == 'written' or any(
            state.startswith('added') for state in report.get('lid_binds', {}).values()):
        print('==> Closing the lid now locks the screen. Log out and back in once to start it.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
