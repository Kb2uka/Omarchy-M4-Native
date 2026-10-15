#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Show the J713 camera notch rows as desktop, or hide them again (issue #89).

The J713 panel is 2560x1664. m1n1 stage 2, the loader at the start of
boot.bin, hides the 64 rows beside the camera cutout and hands Linux a
2560x1600 framebuffer; nothing after it can get those rows back. The owner opts
in with a stage 2 that keeps them (display/j713, docs/j713-notch.md):

  m4-notch                 report the current and the next-boot state
  sudo m4-notch --show     use the notch stage 2 from the next boot
  sudo m4-notch --hide     go back to the stage 2 this Air had before

--show replaces only the first 0x110000 bytes of boot.bin, and only when they
are a stage 2 this tool knows. It keeps the whole old file as boot.bin.pre-notch
(the macOS recovery copy) and --hide splices that file's stage 2 back, so a
kernel or devicetree update installed meanwhile is kept. m4-boot-compose
carries the stage 2 byte for byte, so updates keep the owner's choice.

--desktop is run at login by omarchy-m4-notch.service as the desktop user. When
the booted framebuffer is the whole panel it fits the Omarchy bar to the cutout
strip: a marked spacer, as wide as the cutout, becomes the bar's center anchor,
so the center widgets sit left and right of the camera, and the bar becomes as
tall as the strip. When the framebuffer is the cropped 2560x1600 it removes only
what it added. It changes nothing on a cropped boot that has none of its marks.
"""
import argparse
import contextlib
import copy
import functools
import hashlib
import importlib.machinery
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

BOARD = b'apple,j713'
COMPATIBLE = 'sys/firmware/devicetree/base/compatible'
CHOSEN = 'sys/firmware/devicetree/base/chosen'
BOOT = 'boot/efi/omarchy/boot.bin'
BACKUP = 'boot/efi/omarchy/boot.bin.pre-notch'
STAGED = 'usr/lib/omarchy-m4/m1n1-stage2-notch.bin'
PACMAN_LOCK = 'var/lib/pacman/db.lck'
LOADER_SIZE = 0x110000
# Built by display/j713/build-notch-loader.sh: alpha.1's stage 2 plus
# m1n1-show-notch.patch, version tag air-c9ef56f-gpu-stage2-20261008-notch1.
NOTCH = {'sha256': 'f2417a74d13838004847a6fbdf946b524005301b88b5a403b1e79cb36d0ec7ca', 'size': LOADER_SIZE}
# Stage 2 loaders --show may replace, all of which boot the owner's Air.
KNOWN_LOADERS = {
    '3a3f7a64c65fc366d5ac9aece3e1bee64119be012aa1ec5b15d2743c6f91fce4': 'alpha.1',
    '7bc78b9138ae449eeab08d0a713c96f25e0d7477b7a48f2804da2c17509a9609': '#82 test build (dctx1)',
}
MARGIN = 1 << 20

# The panel and its camera cutout, in panel pixels. AppKit reports the cutout as
# 179x32 pt at 1470x956 (M4 Air) and 209x38 pt at 1710x1112 (M2 Air, same panel):
# about 312x56 px, centred. Two pixels of slack cover the rounding.
PANEL = (2560, 1664)
CROPPED = (2560, 1600)
STRIP = PANEL[1] - CROPPED[1]
CUTOUT_WIDTH = 314
PAD = 8  # logical px of bar left clear on each side of the cutout
DEFAULT_SCALE = 2.0  # what Hyprland's auto scale picks for this panel
THEME_BAR_SIZE = 26  # Omarchy's size-horizontal at the 12 px base font size
BASE_FONT = 12

SHELL_JSON = '.config/omarchy/shell.json'
SHELL_TOML = '.config/omarchy/shell.toml'
DEFAULT_SHELL_JSON = 'usr/share/omarchy/config/omarchy/shell.json'
THEME_TOML = '.local/state/omarchy/current/theme/shell.toml'
SPACER = 'omarchy.spacer'
MARK = 'm4-notch'
TOML_MARK = '# m4-notch'
SPACER_NOTE = 'J713 camera cutout; managed by m4-notch'
# split_bar notes for a bar that ends up split; only such a bar gets the strip height.
SPLIT = ('split around the cutout', 'spacer resized', 'present')


class Refused(Exception):
    pass


def require(condition, message):
    if not condition:
        raise Refused(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


@functools.cache
def composer():
    """m4-boot-compose, installed beside this tool, owns the boot.bin layout and its writes."""
    here = Path(__file__).resolve().parent
    for name in ('m4-boot-compose', 'm4-boot-compose.py'):
        path = here / name
        if path.is_file():
            loader = importlib.machinery.SourceFileLoader('m4_boot_compose', str(path))
            module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
            loader.exec_module(module)
            return module
    raise Refused('m4-boot-compose is missing beside ' + str(here))


def refusals():
    """The errors that end a run with a refusal, the composer's own included."""
    try:
        return (Refused, OSError, ValueError, composer().Refused)
    except Refused:
        return (Refused, OSError, ValueError)


def is_j713(root):
    try:
        return BOARD in (root / COMPATIBLE).read_bytes().split(b'\0')
    except OSError:
        return False


def chosen_text(root, name):
    try:
        return (root / CHOSEN / name).read_bytes().rstrip(b'\0').decode(errors='replace')
    except OSError:
        return None


def framebuffer(root):
    """The (width, height) Linux was handed, or None without exactly one framebuffer node."""
    nodes = sorted((root / CHOSEN).glob('framebuffer@*'))
    if len(nodes) != 1:
        return None
    try:
        size = [int.from_bytes((nodes[0] / name).read_bytes()[:4], 'big') for name in ('width', 'height')]
    except OSError:
        return None
    return tuple(size)


def booted_state(root):
    return {PANEL: 'shown', CROPPED: 'hidden'}.get(framebuffer(root), 'unknown')


# ---------------------------------------------------------------- boot.bin

def loader_name(digest):
    if digest == NOTCH['sha256']:
        return 'notch'
    return KNOWN_LOADERS.get(digest, 'unknown')


def read_boot(root, compose, path=BOOT, what='boot.bin'):
    data = compose.read_regular(root / path, what)
    layout = compose.parse(data)
    require(len(layout['loader']) == LOADER_SIZE, what + ' does not start with a stage 2 loader')
    return data, layout


def splice(compose, data, layout, loader):
    """boot.bin with only its stage 2 replaced, checked to keep every other segment."""
    desired = loader + data[LOADER_SIZE:]
    result = compose.parse(desired)
    require(result['loader'] == loader and
            all(result[name] == layout[name] for name in ('bootargs', 'kernel', 'dtb', 'initramfs')),
            'Composed boot.bin verification failed')
    return desired


@contextlib.contextmanager
def guard(root, data):
    """Hold pacman's own database lock while boot.bin is written, so m4-boot-compose's
    hook cannot rewrite it at the same time, and refuse a boot.bin that moved meanwhile."""
    try:
        fd = os.open(root / PACMAN_LOCK, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o000)
    except FileExistsError:
        raise Refused('a package transaction is running; run this again when it ends (if none is, '
                      + str(root / PACMAN_LOCK) + ' was left behind and can be removed)') from None
    os.close(fd)
    try:
        require((root / BOOT).read_bytes() == data, 'boot.bin changed while working; nothing changed, run it again')
        yield
    finally:
        (root / PACMAN_LOCK).unlink(missing_ok=True)


def drop_backup(root, report):
    """The backup is only needed while the notch stage 2 is installed; a failed removal is reported."""
    try:
        (root / BACKUP).unlink(missing_ok=True)
    except OSError as error:
        report['backup'] = 'could not remove ' + str(root / BACKUP) + ': ' + (error.strerror or str(error))
    return report


def space(root, need):
    fs = os.statvfs(root / BOOT)
    require(fs.f_bavail * fs.f_frsize >= need + MARGIN, 'Not enough free space on the EFI partition')


def show(root):
    compose = composer()
    data, layout = read_boot(root, compose)
    current = sha(layout['loader'])
    if current == NOTCH['sha256']:
        return {'status': 'already shown from the next boot', 'loader': 'notch',
                'backup': str(root / BACKUP) if (root / BACKUP).exists() else None}
    require(current in KNOWN_LOADERS, 'boot.bin starts with an unknown stage 2 ' + current + '; nothing changed')
    notch = compose.read_regular(root / STAGED, 'The notch stage 2')
    require(len(notch) == NOTCH['size'] and sha(notch) == NOTCH['sha256'],
            'The notch stage 2 does not match its pin; reinstall omarchy-m4-boot')
    desired = splice(compose, data, layout, notch)
    space(root, 2 * len(data))
    with guard(root, data):
        compose.write(root / BACKUP, data)
        compose.write(root / BOOT, desired)
    return {'status': 'notch shown from the next boot', 'loader': 'notch', 'replaced': KNOWN_LOADERS[current],
            'backup': str(root / BACKUP), 'undo': 'sudo m4-notch --hide'}


def hide(root):
    compose = composer()
    data, layout = read_boot(root, compose)
    current = sha(layout['loader'])
    if current in KNOWN_LOADERS:
        report = {'status': 'already hidden from the next boot', 'loader': KNOWN_LOADERS[current]}
        if os.path.lexists(root / BACKUP):
            with guard(root, data):
                drop_backup(root, report)
        return report
    require(current == NOTCH['sha256'], 'boot.bin starts with an unknown stage 2 ' + current + '; nothing changed')
    _, saved = read_boot(root, compose, BACKUP, 'The notch backup ' + BACKUP)
    original = sha(saved['loader'])
    require(original in KNOWN_LOADERS, 'The notch backup holds an unknown stage 2 ' + original + '; nothing changed')
    desired = splice(compose, data, layout, saved['loader'])
    space(root, len(data))
    report = {'status': 'notch hidden from the next boot', 'loader': KNOWN_LOADERS[original]}
    with guard(root, data):  # the backup goes under the lock too, so a --show cannot slip in between
        compose.write(root / BOOT, desired)
        drop_backup(root, report)
    return report


def status(root):
    report = {'j713': is_j713(root), 'booted': booted_state(root),
              'framebuffer': 'x'.join(map(str, framebuffer(root) or ())) or None,
              'running_stage2': chosen_text(root, 'asahi,m1n1-stage2-version')}
    try:
        # The EFI directory is root's: stat raises the PermissionError that is_file() would hide.
        os.stat(root / BOOT)
        compose = composer()
        _, layout = read_boot(root, compose)
        name = loader_name(sha(layout['loader']))
        report['next_boot'] = {'notch': 'shown', 'unknown': 'unknown stage 2'}.get(name, 'hidden')
        report['next_boot_stage2'] = name
    except PermissionError:
        report['next_boot'] = 'run with sudo to read boot.bin'
    except refusals() as error:
        report['next_boot'] = 'unreadable: ' + str(error)
    report['backup'] = str(root / BACKUP) if os.path.lexists(root / BACKUP) else None
    return report


# ------------------------------------------------------------ the desktop

def entry_id(entry):
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict) and entry.get('id') not in (None, ''):
        return str(entry['id'])
    return ''


def is_mark(entry):
    return isinstance(entry, dict) and entry_id(entry) == SPACER and MARK in entry


def spacer_size(scale):
    return math.ceil(CUTOUT_WIDTH / scale) + 2 * PAD


def bar_size(scale, base):
    """size-horizontal that renders at least as tall as the strip and never below the theme's bar."""
    strip = math.ceil(STRIP / scale)
    return max(THEME_BAR_SIZE, math.ceil(strip * BASE_FONT / base))


def sections(config):
    """The bar's layout lists, or None when the file is not a bar configuration this tool knows."""
    bar = config.get('bar') if isinstance(config, dict) else None
    layout = bar.get('layout') if isinstance(bar, dict) else None
    if not isinstance(layout, dict) or not all(isinstance(layout.get(k, []), list) for k in ('left', 'center', 'right')):
        return None
    return bar, layout


def split_bar(config, size, created=False):
    """Return (new config, note); the config is None when nothing changes."""
    found = sections(config)
    if found is None:
        return None, 'skipped: not an Omarchy bar configuration'
    config = copy.deepcopy(config)
    bar, layout = sections(config)
    marked = [e for name in ('left', 'center', 'right') for e in layout.get(name, []) if is_mark(e)]
    if marked:
        if all(e.get('size') == size for e in marked):
            return None, 'present'
        for entry in marked:
            entry['size'] = size
        return config, 'spacer resized'
    center = layout.setdefault('center', [])
    if any(entry_id(e) == SPACER for e in center):
        return None, 'skipped: the bar center already has a spacer, which would take the anchor'
    anchor = bar.get('centerAnchor')
    ids = [entry_id(e) for e in center]
    at = ids.index(anchor) + 1 if anchor and anchor in ids else (len(center) + 1) // 2
    spacer = {'id': SPACER, 'size': size, MARK: SPACER_NOTE, MARK + '-anchor': anchor}
    if created:
        spacer[MARK + '-created'] = True
    center.insert(at, spacer)
    bar['centerAnchor'] = SPACER
    return config, 'split around the cutout'


def join_bar(config):
    """Undo split_bar: (new config, created flag); the config is None when it holds no mark."""
    found = sections(config)
    if found is None:
        return None, False
    config = copy.deepcopy(config)
    bar, layout = sections(config)
    removed = []
    for name in ('left', 'center', 'right'):
        if name in layout:
            removed += [e for e in layout[name] if is_mark(e)]
            layout[name] = [e for e in layout[name] if not is_mark(e)]
    if not removed:
        return None, False
    if bar.get('centerAnchor') == SPACER and not any(entry_id(e) == SPACER for e in layout.get('center', [])):
        anchor = removed[0].get(MARK + '-anchor')
        if anchor is None:
            bar.pop('centerAnchor', None)
        else:
            bar['centerAnchor'] = anchor
    return config, any(e.get(MARK + '-created') is True for e in removed)


SECTION = re.compile(r'^\s*\[([A-Za-z0-9_-]+)\]\s*(#.*)?$')
KEY = re.compile(r'^\s*([A-Za-z0-9_-]+)\s*=')


def toml_value(text, section, key):
    current = ''
    for line in (text or '').splitlines():
        match = SECTION.match(line)
        if match:
            current = match.group(1)
            continue
        found = KEY.match(line)
        if current == section and found and found.group(1) == key:
            value = line.split('=', 1)[1].split('#', 1)[0].strip().strip('"\'')
            return value
    return None


def set_bar_toml(text, size):
    """Return (new text, note); the text is None when nothing changes. Only marked lines are ours."""
    lines = (text or '').splitlines(keepends=True)
    line = f'size-horizontal = {size}  {TOML_MARK}: J713 camera cutout strip\n'
    for index, existing in enumerate(lines):
        if TOML_MARK in existing and KEY.match(existing):
            if existing == line:
                return None, 'present'
            lines[index] = line
            return ''.join(lines), 'bar height updated'
    if toml_value(text, 'bar', 'size-horizontal') is not None:
        return None, 'kept: your own [bar] size-horizontal'
    for index, existing in enumerate(lines):
        match = SECTION.match(existing)
        if match and match.group(1) == 'bar':
            lines.insert(index + 1, line)
            return ''.join(lines), 'bar height set'
    prefix = ''.join(lines)
    if prefix and not prefix.endswith('\n'):
        prefix += '\n'
    if prefix:
        prefix += '\n'
    return prefix + f'[bar]  {TOML_MARK}\n' + line, 'bar height set'


def clear_bar_toml(text):
    """Remove the marked lines; a marked header stays while other keys still follow it."""
    lines = (text or '').splitlines(keepends=True)
    if not any(TOML_MARK in line for line in lines):
        return None
    kept = []
    for index, line in enumerate(lines):
        if TOML_MARK not in line:
            kept.append(line)
            continue
        if SECTION.match(line):
            following = []
            for later in lines[index + 1:]:
                if SECTION.match(later):
                    break
                following.append(later)
            if any(KEY.match(x) and TOML_MARK not in x for x in following):
                kept.append(line.split('#', 1)[0].rstrip() + '\n')
    result = ''.join(kept)
    return result if result.strip() else ''


def write_text(path, text, mode=0o600):
    """Atomic replace that keeps the file's mode; an empty text removes the file."""
    if text == '':
        path.unlink(missing_ok=True)
        return
    try:
        mode = path.stat().st_mode & 0o777
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.' + path.name + '.m4-notch')
    temp.unlink(missing_ok=True)
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def read_text(path):
    """A regular file's text, None when it is missing; anything else is refused."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, 'rb') as stream:
        # O_NONBLOCK and this check: a FIFO in its place must not hang the login unit.
        require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), 'not a regular file: ' + str(path))
        return stream.read().decode('utf-8')


def panel_scale():
    """Hyprland's scale for the panel, or the auto scale it picks when Hyprland cannot say."""
    try:
        result = subprocess.run(['hyprctl', '-j', 'monitors'], capture_output=True, text=True, timeout=5)
        for monitor in json.loads(result.stdout):
            if (monitor.get('width'), monitor.get('height')) == PANEL and float(monitor.get('scale', 0)) > 0:
                return float(monitor['scale'])
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        pass
    return DEFAULT_SCALE


def base_font(home):
    for path in (home / SHELL_TOML, home / THEME_TOML):
        try:
            value = toml_value(read_text(path), 'font', 'base-size')
        except (Refused, OSError, UnicodeDecodeError):
            value = None
        if value and value.isdigit() and int(value) > 0:
            return int(value)
    return BASE_FONT


def desktop(root, home, scale=None):
    """Make the bar follow what this boot shows: split for the notch, joined for the crop."""
    report = {'booted': booted_state(root)}
    if not is_j713(root) or report['booted'] == 'unknown':
        report['bar'] = 'unchanged'
        return report
    shown = report['booted'] == 'shown'
    if shown and scale is None:
        scale = panel_scale()
    json_path, toml_path = home / SHELL_JSON, home / SHELL_TOML

    text = read_text(json_path)
    defaults = None
    if text is None and shown:
        defaults = read_text(root / DEFAULT_SHELL_JSON)
    if text is not None or defaults is not None:
        config = json.loads(text if text is not None else defaults)
        if shown:
            new, report['bar'] = split_bar(config, spacer_size(scale), created=text is None)
        else:
            new, created = join_bar(config)
            report['bar'] = 'joined' if new is not None else 'unchanged'
            if new is not None and created:
                stock = read_text(root / DEFAULT_SHELL_JSON)
                if stock is not None and json.loads(stock) == new:
                    write_text(json_path, '')
                    report['bar'] = 'joined; removed the file it created'
                    new = None
        if new is not None:
            write_text(json_path, json.dumps(new, indent=2) + '\n')
    else:
        report['bar'] = 'unchanged'

    text = read_text(toml_path)
    if shown and report['bar'] in SPLIT:
        new, report['bar_height'] = set_bar_toml(text, bar_size(scale, base_font(home)))
    else:  # a cropped boot, or a bar it did not split: no strip height of its own
        new = clear_bar_toml(text)
        report['bar_height'] = 'cleared' if new is not None else 'unchanged'
    if new is not None:
        write_text(toml_path, new)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--show', action='store_true', help='show the notch rows from the next boot')
    action.add_argument('--hide', action='store_true', help='hide them again from the next boot')
    action.add_argument('--desktop', action='store_true',
                        help='fit the bar to this boot; omarchy-m4-notch.service runs this at login')
    parser.add_argument('--root', type=Path, default=Path('/'), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    mode = next((name for name in ('show', 'hide', 'desktop') if getattr(args, name)), 'status')
    try:
        if mode in ('show', 'hide'):
            require(args.root != Path('/') or os.geteuid() == 0, 'run with sudo')
            require(is_j713(args.root), 'This is not a J713 MacBook Air')
            report = (show if mode == 'show' else hide)(args.root)
            report['takes_effect'] = 'at the next restart; the bar follows at the next login'
        elif mode == 'desktop':
            require(os.geteuid() != 0, 'run as the desktop user, not root')
            report = desktop(args.root, Path.home())
        else:
            report = status(args.root)
    except refusals() as error:
        print('m4-notch: refused: ' + str(error), file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
