#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Start and control the J713 native panel backlight, delivered by omarchy-m4-update.

Boot startup is guarded before any module or firmware operation. A failed or
interrupted attempt leaves a durable marker, so the next boot does not repeat
it. --disable holds future startup without unloading a live display domain;
--enable clears the hold and failed-attempt marker for the next boot.

Brightness keys use the native backlight only for the focused Unknown-1 panel.
Other focused monitors retain Omarchy's existing Apple-display/DDC routing.
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

BOARD = b'apple,j713'
COMPATIBLE = 'sys/firmware/devicetree/base/compatible'
MODULE = 'usr/lib/omarchy-m4/backlight/j713_dcp_backlight.ko'
PINS = 'usr/lib/omarchy-m4/backlight/module.json'
STATE = 'var/lib/omarchy-m4/backlight'
DEBUG = 'sys/kernel/debug/j713_dcp_backlight'
BACKLIGHT = 'sys/class/backlight/j713-dcp'
CLIENT = 'sys/firmware/devicetree/base/soc/dcp-backlight@30ae00000'
NAME = 'j713_dcp_backlight'
INITIAL_NITS = 300
MARKER = '-- J713 native panel brightness (omarchy-m4-backlight)'
KEYS = (
    ('XF86MonBrightnessUp', 'Brightness up', '+5%'),
    ('XF86MonBrightnessDown', 'Brightness down', '5%-'),
    ('SHIFT + XF86MonBrightnessUp', 'Brightness maximum', '100%'),
    ('SHIFT + XF86MonBrightnessDown', 'Brightness minimum', '1%'),
    ('ALT + XF86MonBrightnessUp', 'Brightness up precise', '+1%'),
    ('ALT + XF86MonBrightnessDown', 'Brightness down precise', '1%-'),
)
BINDS = MARKER + '\n' + '\n'.join(
    f'hl.unbind("{key}")\no.bind("{key}", "{description}", '
    f'"m4-backlight brightness {step}", {{ locked = true, repeating = true }})'
    for key, description, step in KEYS
) + '\n'


class Refused(Exception):
    pass


def require(condition, message):
    if not condition:
        raise Refused(message)


def text(path):
    return path.read_text().strip()


def is_j713(root):
    try:
        return BOARD in (root / COMPATIBLE).read_bytes().split(b'\0')
    except OSError:
        return False


def sync_directory(path):
    fd = os.open(path, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_state(state, name, value):
    fd = os.open(state / name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(state)


def remove_state(state, name):
    if os.path.lexists(state / name):
        (state / name).unlink()
        sync_directory(state)

@contextlib.contextmanager
def locked_state(root):
    state = root / STATE
    state.mkdir(parents=True, exist_ok=True)
    require(not state.is_symlink(), 'Backlight state directory is a symlink')
    fd = os.open(state / 'lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused('Another backlight state change is running') from None
        yield state
    finally:
        os.close(fd)



def status(root):
    result = {'j713': is_j713(root), 'loaded': (root / 'sys/module' / NAME).exists(),
              'held': (root / STATE / 'hold').exists(),
              'failed_attempt': (root / STATE / 'attempt').exists(),
              'backlight': (root / BACKLIGHT).exists()}
    if result['backlight']:
        result['nits'] = int(text(root / BACKLIGHT / 'actual_brightness'))
        result['maximum_nits'] = int(text(root / BACKLIGHT / 'max_brightness'))
    return result


def start(root, run=subprocess.run):
    require(is_j713(root), 'This is not a J713 MacBook Air')
    with locked_state(root) as state:
        require(not os.path.lexists(state / 'hold'), 'Native backlight is disabled until --enable')
        require(not os.path.lexists(state / 'attempt'),
                'An earlier startup failed or was interrupted; inspect the kernel log before --enable')
        if (root / 'sys/module' / NAME).exists():
            require((root / BACKLIGHT).exists(), 'Loaded backlight is incomplete; reboot instead of retrying')
            return status(root)
        node = root / CLIENT
        require(text(node / 'status').rstrip('\0') == 'okay' and
                (node / 'omarchy,display-handoff-verified').exists() and
                (node / 'memory-region').exists(),
                'Verified display handoff is not booted; restart after omarchy-m4-update')
        pins_path = root / PINS
        require(pins_path.is_file() and not pins_path.is_symlink(), 'Module pins are missing')
        pins = json.loads(pins_path.read_text())
        require(text(root / 'proc/sys/kernel/osrelease') == pins['kernel_release'],
                'Native backlight is not qualified for this kernel')
        module = root / MODULE
        require(module.is_file() and not module.is_symlink(), 'Pinned module is missing')
        data = module.read_bytes()
        require(len(data) == pins['module_bytes'] and
                hashlib.sha256(data).hexdigest() == pins['module_sha256'], 'Module differs from its pin')
        write_state(state, 'attempt', 'starting native backlight\n')
        run(['insmod', str(module)], check=True)
        (root / DEBUG / 'start').write_text('1\n')
        (root / DEBUG / 'brightness_nits').write_text(f'{INITIAL_NITS}\n')
        fields = dict(field.split('=', 1) for field in text(root / DEBUG / 'status').split())
        require(fields.get('online') == '1' and fields.get('failed') == '0' and
                0 <= int(fields.get('applied_nits', '-1')) <= 525 and
                (root / BACKLIGHT).exists(), 'Native backlight startup did not complete; no retry this boot')
        remove_state(state, 'attempt')
        return status(root)


def brightness(step, no_osd=False, run=subprocess.run, root=Path('/')):
    require(is_j713(root), 'This is not a J713 MacBook Air')
    result = run(['hyprctl', 'monitors', '-j'], check=True, capture_output=True, text=True)
    focused = [monitor for monitor in json.loads(result.stdout) if monitor.get('focused')]
    require(len(focused) == 1, 'No unique focused monitor')
    if focused[0]['name'] != 'Unknown-1':
        command = ['omarchy', 'brightness', 'display']
        if no_osd:
            command.append('--no-osd')
        return run(command + [step]).returncode
    require((root / BACKLIGHT).exists(), 'Native panel backlight is not started')
    # Share Omarchy's event lock, but let external routing take its own lock.
    fd = os.open(Path(os.environ['XDG_RUNTIME_DIR']) / 'omarchy-brightness-display.lock',
                 os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0  # Same intentional overlap drop as Omarchy's brightness keys.
        result = run(['brightnessctl', '-d', 'j713-dcp', '-m'], check=True, capture_output=True, text=True)
        current = int(result.stdout.strip().split(',')[3].rstrip('%'))
        # Preserve Omarchy's small steps near minimum and absolute percentage rounding.
        if step == '+5%':
            step = f'{min(100, current + (1 if current < 5 else 5))}%'
        elif step == '5%-':
            step = f'{max(1, current - (1 if current <= 5 else 5))}%'
        run(['brightnessctl', '-d', 'j713-dcp', 'set', step], check=True, capture_output=True, text=True)
        if not no_osd:
            result = run(['brightnessctl', '-d', 'j713-dcp', '-m'], check=True, capture_output=True, text=True)
            percent = result.stdout.strip().split(',')[3].rstrip('%')
            run(['omarchy-osd', '-i', 'brightness', '-p', percent], check=True)
        return 0
    finally:
        os.close(fd)


def add_keys(path, apply):
    flags = (os.O_RDWR | os.O_APPEND if apply else os.O_RDONLY) | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return 'no configuration'
    with os.fdopen(fd, 'r+b' if apply else 'rb', buffering=0) as stream:
        require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), 'Configuration is not a regular file')
        source = stream.read().decode('utf-8')
        if MARKER in source:
            return 'present'
        if 'require("default.hypr.omarchy")' not in source:
            return 'not an Omarchy configuration'
        custom = path.with_name('bindings.lua')
        if custom.exists():
            custom_fd = os.open(custom, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(custom_fd, 'rb') as custom_stream:
                require(stat.S_ISREG(os.fstat(custom_stream.fileno()).st_mode),
                        'Custom bindings are not a regular file')
                # Ignore whole-line examples, but preserve any active custom line.
                if any(b'XF86MonBrightness' in line and not line.lstrip().startswith(b'--')
                       for line in custom_stream.read().splitlines()):
                    return 'custom brightness bindings preserved'
        if not apply:
            return 'missing'
        backup = path.with_name(path.name + '.pre-m4-backlight')
        if not os.path.lexists(backup):
            backup_fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(backup_fd, 'wb') as backup_stream:
                backup_stream.write(source.encode())
                backup_stream.flush()
                os.fsync(backup_stream.fileno())
            sync_directory(path.parent)
        stream.write((('' if source.endswith('\n') else '\n') + BINDS).encode())
        os.fsync(stream.fileno())
    return 'added'


def remove_keys(path):
    try:
        fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return 'no configuration'
    with os.fdopen(fd, 'r+b', buffering=0) as stream:
        require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), 'Configuration is not a regular file')
        source = stream.read().decode('utf-8')
        if MARKER not in source:
            return 'absent'
        require(source.count(BINDS) == 1, 'Managed brightness bindings were edited; preserving them')
        stream.seek(0)
        stream.write(source.replace(BINDS, '', 1).encode())
        stream.truncate()
        os.fsync(stream.fileno())
    return 'removed'


def keys_hook(root, remove=False):
    require(is_j713(root), 'This is not a J713 MacBook Air')
    # Root's package hook only enumerates users; each append drops all privileges.
    report = {}
    for line in (root / 'etc/passwd').read_text().splitlines():
        fields = line.split(':')
        if len(fields) != 7 or not fields[2].isdigit() or not fields[3].isdigit():
            continue
        name, _, uid, gid, _, home, shell = fields
        if not (1000 <= int(uid) < 60000 and home.startswith('/home/') and not shell.endswith('nologin')):
            continue
        path = root / home.lstrip('/') / '.config/hypr/hyprland.lua'
        read_fd, write_fd = os.pipe()
        child = os.fork()
        if child == 0:
            os.close(read_fd)
            try:
                os.setgroups([])
                os.initgroups(name, int(gid))
                os.setgid(int(gid))
                os.setuid(int(uid))
                value = remove_keys(path) if remove else add_keys(path, True)
            except (OSError, ValueError, Refused) as error:
                value = 'skipped: ' + str(error)
            os.write(write_fd, value.encode())
            os._exit(0)
        os.close(write_fd)
        with os.fdopen(read_fd, 'rb') as stream:
            report[name] = stream.read().decode()
        os.waitpid(child, 0)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--boot', action='store_true')
    action.add_argument('--enable', action='store_true')
    action.add_argument('--disable', action='store_true')
    action.add_argument('--keys-hook', action='store_true', help=argparse.SUPPRESS)
    action.add_argument('--keys-remove-hook', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--root', type=Path, default=Path('/'), help=argparse.SUPPRESS)
    parser.add_argument('command', nargs='?', choices=['brightness'])
    parser.add_argument('step', nargs='?')
    parser.add_argument('--no-osd', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.command:
            require(args.step is not None and not any((args.boot, args.enable, args.disable,
                                                       args.keys_hook, args.keys_remove_hook)),
                    'Use brightness STEP without a startup action')
            return brightness(args.step, args.no_osd, root=args.root)
        if args.boot or args.enable or args.disable or args.keys_hook or args.keys_remove_hook:
            require(os.geteuid() == 0, 'This action requires root')
        if args.keys_hook or args.keys_remove_hook:
            print(json.dumps(keys_hook(args.root, remove=args.keys_remove_hook), indent=2))
        elif args.boot:
            print(json.dumps(start(args.root), indent=2))
        elif args.enable or args.disable:
            require(is_j713(args.root), 'This is not a J713 MacBook Air')
            with locked_state(args.root) as state:
                if args.disable:
                    write_state(state, 'hold', 'disabled until --enable\n')
                else:
                    remove_state(state, 'hold')
                    remove_state(state, 'attempt')
            print('Native backlight ' + ('disabled' if args.disable else 'enabled') + ' from the next boot; no live module unloaded')
        else:
            print(json.dumps(status(args.root), indent=2))
    except (Refused, OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print('m4-backlight: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
