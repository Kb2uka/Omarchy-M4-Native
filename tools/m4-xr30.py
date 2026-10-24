#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Select the packaged Mesa prefix for a qualified J715 compositor at login.

Monitor configuration remains the owner's responsibility. This wrapper neither
requests a bit depth nor contacts a running compositor. --disable and --enable
only change the global hold for subsequent logins; inspection is the default.
"""
import argparse
import json
import os
from pathlib import Path
import re
import sys

DT = 'sys/firmware/devicetree/base'
DRM = 'sys/class/drm'
PREFIX = 'usr/lib/omarchy-m4/mesa'
VENDOR = 'share/glvnd/egl_vendor.d/50_mesa.json'
STATE = 'var/lib/omarchy-m4/xr30'
LAUNCHER = 'usr/bin/start-hyprland'


class Refused(Exception):
    pass


def tokens(path):
    """Read a DT string list; malformed, unterminated values cannot qualify."""
    data = path.read_bytes()
    return data[:-1].split(b'\0') if data.endswith(b'\0') else []


def native_panel(root):
    """Find one connected DRM connector backed by the native XR30 framebuffer."""
    tree = root / DT
    if b'apple,j715' not in tokens(tree / 'compatible'):
        raise Refused('board is not apple,j715')
    panels = []
    for card in sorted((root / DRM).glob('card*')):
        if not re.fullmatch(r'card[0-9]+', card.name):
            continue
        device = card / 'device'
        try:
            if (device / 'driver').resolve(strict=True).name != 'simple-framebuffer':
                continue
            node = (device / 'of_node').resolve(strict=True)
        except FileNotFoundError:
            continue
        if node.parent != (tree / 'chosen').resolve():
            continue
        if b'simple-framebuffer' not in tokens(node / 'compatible'):
            continue
        if (node / 'format').read_bytes() != b'x2r10g10b10\0':
            continue
        if (node / 'status').exists() and tokens(node / 'status') not in ([b'ok'], [b'okay']):
            continue
        for connector in sorted((root / DRM).glob(card.name + '-*')):
            if (connector / 'status').read_text().strip() == 'connected':
                panels.append(connector.name.split('-', 1)[1])
    if len(panels) != 1:
        raise Refused('no unique connected native XR30 simple-framebuffer display')
    return panels[0]


def inspect(root):
    """Report next-login selection, not the state of a running session."""
    report = {'held': False, 'eligible': False, 'next_login': 'original'}
    try:
        # Any directory entry holds selection; unreadable state fails closed.
        try:
            (root / STATE / 'hold').lstat()
            report['held'] = True
        except FileNotFoundError:
            report['held'] = False
        if report['held']:
            report['reason'] = 'held'
            return report
        report['panel'] = native_panel(root)
        prefix = root / PREFIX
        if not all((prefix / name).is_dir() for name in ('lib', 'lib/gbm', 'lib/dri')):
            raise Refused('packaged Mesa directories are missing')
        if not (prefix / VENDOR).is_file():
            raise Refused('packaged EGL vendor manifest is missing')
    except (Refused, OSError, UnicodeError) as error:
        report['reason'] = str(error)
        return report
    report.update(eligible=True, next_login='private-mesa', reason='qualified')
    return report


def session_environment(original, report):
    """Change only loader selection, and only for a qualified, unheld login."""
    environment = original.copy()
    if report['eligible']:
        prefix = '/' + PREFIX
        previous = environment.get('LD_LIBRARY_PATH', '')
        environment.update({
            'LD_LIBRARY_PATH': prefix + '/lib' + (':' + previous if previous else ''),
            'GBM_BACKENDS_PATH': prefix + '/lib/gbm',
            'LIBGL_DRIVERS_PATH': prefix + '/lib/dri',
            '__EGL_VENDOR_LIBRARY_FILENAMES': prefix + '/' + VENDOR,
        })
        # M4 compressed color-target preparation is not qualified. Preserve
        # other debug flags, but retain the live desktop's compression guard.
        flags = environment.get('ASAHI_MESA_DEBUG', '')
        if 'nocompress' not in re.split(r'[, :]+', flags):
            environment['ASAHI_MESA_DEBUG'] = flags + (',' if flags else '') + 'nocompress'
    return environment


def set_enabled(root, enabled):
    """Change the root-owned hold only; never stop or modify a live session."""
    if root == Path('/') and os.geteuid() != 0:
        raise Refused('enable/disable requires root')
    hold = root / STATE / 'hold'
    if enabled:
        hold.unlink(missing_ok=True)
    else:
        previous_umask = os.umask(0o022)
        try:
            hold.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            try:
                with hold.open('x'):
                    return
            except FileExistsError:
                return
        finally:
            os.umask(previous_umask)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--start', action='store_true', help='exec start-hyprland for this login')
    action.add_argument('--status', action='store_true', help='report next-login selection (default)')
    action.add_argument('--enable', action='store_true', help='remove the next-login hold (root)')
    action.add_argument('--disable', action='store_true', help='hold the private prefix at next login (root)')
    parser.add_argument('--root', type=Path, default=Path('/'), help=argparse.SUPPRESS)
    parser.add_argument('arguments', nargs=argparse.REMAINDER, help='arguments for start-hyprland, after --')
    args = parser.parse_args(argv)
    if args.arguments and not args.start:
        parser.error('launcher arguments require --start')
    root = args.root.resolve()
    try:
        if args.enable or args.disable:
            set_enabled(root, args.enable)
        report = inspect(root)
        if args.start:
            print('m4-xr30: ' + json.dumps(report, sort_keys=True), file=sys.stderr, flush=True)
            os.execve(str(root / LAUNCHER), ['/' + LAUNCHER, *args.arguments],
                      session_environment(dict(os.environ), report))
        else:
            print(json.dumps(report, indent=2, sort_keys=True))
    except (Refused, OSError) as error:
        print('m4-xr30: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
