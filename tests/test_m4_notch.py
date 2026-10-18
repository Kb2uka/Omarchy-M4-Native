# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""m4-notch: opt-in notch stage 2 and the bar split around the J713 camera cutout (#89)."""
import contextlib
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import random
import re
import stat
import struct
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'tools/m4-notch.py'
CHANNEL = json.loads((ROOT / 'packaging/omarchy-m4/channel.json').read_text())
PACKAGE = ROOT / 'packaging/omarchy-m4/omarchy-m4-boot'


def load():
    spec = importlib.util.spec_from_file_location('m4_notch', SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


NOTCH = load()
COMPOSE = NOTCH.composer()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fdt(body):
    return struct.pack('>10I', 0xd00dfeed, 40 + len(body), 40, 40, 40, 17, 16, 0, 0, len(body)) + body


SIZE = 64
# Loaders are fixed-size and, like m1n1, contain the boot-argument tag text.
STOCK_LOADER = (b'stock stage2 chosen.bootargs= table\0').ljust(SIZE, b'\0')
TEST_LOADER = (b'dctx1 stage2 chosen.bootargs= table\0').ljust(SIZE, b'\0')
NOTCH_LOADER = (b'notch stage2 chosen.bootargs= table\0').ljust(SIZE, b'\0')
ODD_LOADER = (b'hand-built stage2\0').ljust(SIZE, b'\0')
BOOTARGS = b'chosen.bootargs=dockchannel_hid.m4_power_v2=1\n'
K1 = gzip.compress(random.Random(1).randbytes(512), mtime=0)
K2 = gzip.compress(random.Random(2).randbytes(512), mtime=0)
D1, D2 = fdt(b'alpha.1 tree'), fdt(b'newer tree')
FIRMWARE = b'070701 owner firmware'
INITRAMFS = b'm1n1_initramfs' + struct.pack('<I', len(FIRMWARE)) + FIRMWARE


def boot(loader=STOCK_LOADER, kernel=K1, dtb=D1):
    return loader + BOOTARGS + kernel + dtb + INITRAMFS


# Doug's Air layout (shell.json as found on the owner's Air, 2026-10-15).
LAYOUT = {
    'version': 1,
    'idle': {'screensaver': 150, 'lock': 300},
    'bar': {'position': 'top', 'transparent': False, 'centerAnchor': 'omarchy.clock', 'layout': {
        'left': [{'id': 'omarchy.menu'}, {'id': 'omarchy.workspaces'}],
        'center': [{'id': 'omarchy.indicators'}, {'id': 'omarchy.clock', 'format': 'dddd HH:mm'},
                   {'id': 'omarchy.keyboard-layout'}, {'id': 'omarchy.weather'}, {'id': 'omarchy.system-update'}],
        'right': [{'id': 'omarchy.tray'}, {'id': 'omarchy.audio'}, {'id': 'omarchy.power'}]}},
    'plugins': []}
DEFAULTS = {'version': 1, 'bar': {'position': 'top', 'centerAnchor': 'omarchy.clock', 'layout': {
    'left': [{'id': 'omarchy.menu'}], 'center': [{'id': 'omarchy.clock'}], 'right': [{'id': 'omarchy.power'}]}}}


class Air:
    """A temporary root and home that look like an installed J713."""

    def __init__(self, test, loader=STOCK_LOADER, framebuffer=(2560, 1600), board=b'apple,j713'):
        temp = tempfile.TemporaryDirectory()
        test.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / 'root'
        self.home = Path(temp.name) / 'home'
        self.home.mkdir(parents=True)
        for patcher in (mock.patch.object(NOTCH, 'LOADER_SIZE', SIZE),
                        mock.patch.dict(NOTCH.NOTCH, {'sha256': sha(NOTCH_LOADER), 'size': SIZE}),
                        mock.patch.dict(NOTCH.KNOWN_LOADERS, {sha(STOCK_LOADER): 'alpha.1',
                                                              sha(TEST_LOADER): '#82 test build (dctx1)'}, clear=True)):
            patcher.start()
            test.addCleanup(patcher.stop)
        self.put(NOTCH.COMPATIBLE, board + b'\0apple,t8132\0apple,arm-platform\0')
        (self.root / NOTCH.PACMAN_LOCK).parent.mkdir(parents=True, exist_ok=True)
        self.put(NOTCH.BOOT, boot(loader))
        self.put(NOTCH.STAGED, NOTCH_LOADER)
        self.put(NOTCH.DEFAULT_SHELL_JSON, json.dumps(DEFAULTS, indent=2).encode())
        self.put(NOTCH.CHOSEN + '/asahi,m1n1-stage2-version', b'air-c9ef56f-gpu-stage2-20261008\0')
        self.framebuffer(*framebuffer)

    def put(self, path, data):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def framebuffer(self, width, height):
        for old in (self.root / NOTCH.CHOSEN).glob('framebuffer@*'):
            for item in old.iterdir():
                item.unlink()
            old.rmdir()
        node = NOTCH.CHOSEN + '/framebuffer@103e1c8c000'
        self.put(node + '/width', width.to_bytes(4, 'big'))
        self.put(node + '/height', height.to_bytes(4, 'big'))

    def boot_bytes(self):
        return (self.root / NOTCH.BOOT).read_bytes()

    def shell(self, value=LAYOUT):
        path = self.home / NOTCH.SHELL_JSON
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2) + '\n')
        os.chmod(path, 0o700)  # not the writer's own 0600 default, so a lost mode shows
        return path

    def desktop(self, scale=2.0):
        return NOTCH.desktop(self.root, self.home, scale)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = NOTCH.main(argv)
    return code, out.getvalue(), err.getvalue()


class ShowHideTests(unittest.TestCase):
    def test_show_replaces_only_the_stage2_and_keeps_the_whole_old_file(self):
        air = Air(self)
        before = air.boot_bytes()
        report = NOTCH.show(air.root)
        after = air.boot_bytes()
        self.assertEqual(report['status'], 'notch shown from the next boot')
        self.assertEqual(after, NOTCH_LOADER + before[SIZE:])
        self.assertEqual((air.root / NOTCH.BACKUP).read_bytes(), before)
        layout = COMPOSE.parse(after)
        self.assertEqual((layout['kernel'], layout['dtb'], layout['bootargs']), (K1, D1, BOOTARGS))

    def test_show_replaces_the_issue82_test_build_too(self):
        air = Air(self, loader=TEST_LOADER)
        NOTCH.show(air.root)
        self.assertEqual(air.boot_bytes()[:SIZE], NOTCH_LOADER)
        NOTCH.hide(air.root)
        self.assertEqual(air.boot_bytes(), boot(TEST_LOADER))

    def test_show_twice_changes_nothing(self):
        air = Air(self)
        NOTCH.show(air.root)
        shown, backup = air.boot_bytes(), (air.root / NOTCH.BACKUP).read_bytes()
        report = NOTCH.show(air.root)
        self.assertEqual(report['status'], 'already shown from the next boot')
        self.assertEqual((air.boot_bytes(), (air.root / NOTCH.BACKUP).read_bytes()), (shown, backup))

    def test_show_refuses_an_unknown_stage2(self):
        air = Air(self, loader=ODD_LOADER)
        with self.assertRaisesRegex(NOTCH.Refused, 'unknown stage 2'):
            NOTCH.show(air.root)
        self.assertEqual(air.boot_bytes(), boot(ODD_LOADER))
        self.assertFalse((air.root / NOTCH.BACKUP).exists())

    def test_show_refuses_a_staged_loader_that_is_not_the_pin(self):
        air = Air(self)
        air.put(NOTCH.STAGED, ODD_LOADER)
        with self.assertRaisesRegex(NOTCH.Refused, 'does not match its pin'):
            NOTCH.show(air.root)
        self.assertEqual(air.boot_bytes(), boot())
        self.assertFalse((air.root / NOTCH.BACKUP).exists())

    def test_show_refuses_while_pacman_runs(self):
        air = Air(self)
        air.put(NOTCH.PACMAN_LOCK, b'')
        with self.assertRaisesRegex(NOTCH.Refused, 'package transaction'):
            NOTCH.show(air.root)
        self.assertEqual(air.boot_bytes(), boot())
        self.assertTrue((air.root / NOTCH.PACMAN_LOCK).exists())  # pacman's lock is not ours to remove

    def test_pacman_lock_is_held_while_boot_bin_is_written_and_released_after(self):
        air = Air(self)
        held = []
        real = COMPOSE.write

        def write(path, data):
            held.append((path.name, (air.root / NOTCH.PACMAN_LOCK).exists()))
            real(path, data)
        with mock.patch.object(COMPOSE, 'write', side_effect=write):
            NOTCH.show(air.root)
            NOTCH.hide(air.root)
        self.assertEqual(held, [('boot.bin.pre-notch', True), ('boot.bin', True), ('boot.bin', True)])

    def test_hide_drops_the_backup_while_it_still_holds_the_lock(self):
        air = Air(self)
        NOTCH.show(air.root)
        seen = []
        real = NOTCH.drop_backup

        def drop(root, report):
            seen.append((air.root / NOTCH.PACMAN_LOCK).exists())
            return real(root, report)
        with mock.patch.object(NOTCH, 'drop_backup', side_effect=drop):
            NOTCH.hide(air.root)
            air.put(NOTCH.BACKUP, air.boot_bytes())  # a stale copy on an Air already hidden
            NOTCH.hide(air.root)
        self.assertEqual(seen, [True, True])
        self.assertFalse((air.root / NOTCH.BACKUP).exists())
        self.assertFalse((air.root / NOTCH.PACMAN_LOCK).exists())

    def test_the_lock_is_released_when_the_write_fails(self):
        air = Air(self)
        with mock.patch.object(COMPOSE, 'write', side_effect=OSError('EFI full')):
            with self.assertRaises(OSError):
                NOTCH.show(air.root)
        self.assertFalse((air.root / NOTCH.PACMAN_LOCK).exists())
        self.assertEqual(air.boot_bytes(), boot())

    def test_show_refuses_a_boot_bin_the_composer_does_not_recognise(self):
        air = Air(self)
        air.put(NOTCH.BOOT, STOCK_LOADER + b'not a boot object')
        with self.assertRaises(COMPOSE.Refused):
            NOTCH.show(air.root)

    def test_show_refuses_without_space_for_the_backup(self):
        air = Air(self)
        with mock.patch.object(NOTCH.os, 'statvfs', return_value=os.statvfs_result((4096, 4096, 0, 0, 1, 0, 0, 0, 0, 255))):
            with self.assertRaisesRegex(NOTCH.Refused, 'free space'):
                NOTCH.show(air.root)
        self.assertEqual(air.boot_bytes(), boot())

    def test_hide_restores_the_old_stage2_and_keeps_a_later_kernel_and_devicetree(self):
        air = Air(self)
        NOTCH.show(air.root)
        # A package update composed a newer kernel and devicetree while the notch was shown.
        air.put(NOTCH.BOOT, boot(NOTCH_LOADER, K2, D2))
        report = NOTCH.hide(air.root)
        self.assertEqual(report['status'], 'notch hidden from the next boot')
        self.assertEqual(air.boot_bytes(), boot(STOCK_LOADER, K2, D2))
        self.assertFalse((air.root / NOTCH.BACKUP).exists())

    def test_hide_when_hidden_changes_nothing(self):
        air = Air(self)
        self.assertEqual(NOTCH.hide(air.root)['status'], 'already hidden from the next boot')
        self.assertEqual(air.boot_bytes(), boot())

    def test_hide_when_hidden_with_no_backup_does_not_wait_for_pacman(self):
        air = Air(self)
        air.put(NOTCH.PACMAN_LOCK, b'')  # a package transaction is running
        self.assertEqual(NOTCH.hide(air.root)['status'], 'already hidden from the next boot')
        self.assertEqual(air.boot_bytes(), boot())

    def test_hide_after_a_macos_recovery_removes_the_stale_backup(self):
        air = Air(self)
        NOTCH.show(air.root)
        air.put(NOTCH.BOOT, (air.root / NOTCH.BACKUP).read_bytes())  # the documented macOS copy
        report = NOTCH.hide(air.root)
        self.assertEqual(report['status'], 'already hidden from the next boot')
        self.assertFalse((air.root / NOTCH.BACKUP).exists())
        self.assertEqual(air.boot_bytes(), boot())

    def test_a_backup_that_cannot_be_removed_does_not_undo_a_finished_hide(self):
        air = Air(self)
        NOTCH.show(air.root)
        real = Path.unlink

        def unlink(path, *args, **kwargs):
            if path.name == 'boot.bin.pre-notch':
                raise PermissionError(13, 'Read-only file system')
            return real(path, *args, **kwargs)
        with mock.patch.object(Path, 'unlink', unlink):
            report = NOTCH.hide(air.root)
        self.assertEqual(report['status'], 'notch hidden from the next boot')
        self.assertIn('could not remove', report['backup'])
        self.assertEqual(air.boot_bytes(), boot())

    def test_hide_refuses_without_the_backup(self):
        air = Air(self, loader=NOTCH_LOADER)
        with self.assertRaises(COMPOSE.Refused):
            NOTCH.hide(air.root)
        self.assertEqual(air.boot_bytes(), boot(NOTCH_LOADER))

    def test_hide_refuses_a_backup_with_an_unknown_stage2(self):
        air = Air(self, loader=NOTCH_LOADER)
        air.put(NOTCH.BACKUP, boot(ODD_LOADER))
        with self.assertRaisesRegex(NOTCH.Refused, 'unknown stage 2'):
            NOTCH.hide(air.root)
        self.assertEqual(air.boot_bytes(), boot(NOTCH_LOADER))

    def test_the_composer_keeps_the_notch_stage2_across_updates(self):
        air = Air(self)
        NOTCH.show(air.root)
        layout = COMPOSE.parse(air.boot_bytes())
        updated = COMPOSE.join({**layout, 'kernel': K2, 'dtb': D2})
        self.assertEqual(COMPOSE.parse(updated)['loader'], NOTCH_LOADER)

    def test_main_refuses_another_mac_and_needs_root(self):
        air = Air(self, board=b'apple,j613')
        code, _, err = run(['--show', '--root', str(air.root)])
        self.assertEqual(code, 1)
        self.assertIn('not a J713', err)
        self.assertEqual(air.boot_bytes(), boot())
        with mock.patch.object(NOTCH.os, 'geteuid', return_value=1000):
            code, _, err = run(['--hide'])
        self.assertEqual(code, 1)
        self.assertIn('run with sudo', err)

    def test_status_reports_this_boot_and_the_next(self):
        air = Air(self)
        code, out, _ = run(['--root', str(air.root)])
        report = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual((report['booted'], report['next_boot'], report['framebuffer']), ('hidden', 'hidden', '2560x1600'))
        self.assertEqual(report['running_stage2'], 'air-c9ef56f-gpu-stage2-20261008')
        NOTCH.show(air.root)
        report = NOTCH.status(air.root)
        self.assertEqual((report['booted'], report['next_boot'], report['next_boot_stage2']), ('hidden', 'shown', 'notch'))
        self.assertEqual(report['backup'], str(air.root / NOTCH.BACKUP))

    def test_status_without_root_rights_still_reports_this_boot(self):
        air = Air(self)
        with mock.patch.object(COMPOSE, 'read_regular', side_effect=PermissionError('denied')):
            report = NOTCH.status(air.root)
        self.assertEqual(report['next_boot'], 'run with sudo to read boot.bin')
        # On the Air the EFI directory is root's, so the file cannot even be looked up.
        efi = air.root / NOTCH.BOOT
        os.chmod(efi.parent.parent, 0)
        self.addCleanup(os.chmod, efi.parent.parent, 0o755)
        if os.geteuid() != 0:
            report = NOTCH.status(air.root)
            self.assertEqual(report['next_boot'], 'run with sudo to read boot.bin')
            self.assertEqual(report['booted'], 'hidden')


class DesktopTests(unittest.TestCase):
    def snapshot(self, air):
        # The inode catches a rewrite with identical bytes.
        return {str(p): (p.read_bytes(), p.stat().st_ino) for p in air.home.rglob('*') if p.is_file()}

    def test_cropped_boot_without_marks_changes_nothing(self):
        air = Air(self)
        air.shell()
        before = self.snapshot(air)
        report = air.desktop()
        self.assertEqual((report['bar'], report['bar_height']), ('unchanged', 'unchanged'))
        self.assertEqual(self.snapshot(air), before)
        self.assertFalse((air.home / NOTCH.SHELL_TOML).exists())

    def test_cropped_boot_without_a_shell_file_creates_none(self):
        air = Air(self)
        air.desktop()
        self.assertEqual(self.snapshot(air), {})

    def test_shown_boot_splits_the_center_around_the_cutout(self):
        air = Air(self, framebuffer=(2560, 1664))
        air.shell()
        report = air.desktop()
        self.assertEqual(report['booted'], 'shown')
        bar = json.loads((air.home / NOTCH.SHELL_JSON).read_text())['bar']
        center = [e['id'] for e in bar['layout']['center']]
        self.assertEqual(center, ['omarchy.indicators', 'omarchy.clock', 'omarchy.spacer',
                                  'omarchy.keyboard-layout', 'omarchy.weather', 'omarchy.system-update'])
        self.assertEqual(bar['centerAnchor'], 'omarchy.spacer')
        spacer = bar['layout']['center'][2]
        self.assertEqual(spacer['size'], 157 + 16)  # 314 px of cutout at scale 2, 8 px clear each side
        self.assertEqual(spacer['m4-notch-anchor'], 'omarchy.clock')
        self.assertEqual(bar['layout']['left'], LAYOUT['bar']['layout']['left'])
        self.assertEqual(bar['layout']['right'], LAYOUT['bar']['layout']['right'])
        toml = (air.home / NOTCH.SHELL_TOML).read_text()
        self.assertEqual(NOTCH.toml_value(toml, 'bar', 'size-horizontal'), '32')

    def test_a_second_login_rewrites_nothing(self):
        air = Air(self, framebuffer=(2560, 1664))
        air.shell()
        air.desktop()
        before = self.snapshot(air)
        report = air.desktop()
        self.assertEqual((report['bar'], report['bar_height']), ('present', 'present'))
        self.assertEqual(self.snapshot(air), before)

    def test_a_cropped_boot_after_a_shown_one_restores_the_owner_files(self):
        air = Air(self, framebuffer=(2560, 1664))
        path = air.shell()
        original = path.read_bytes()
        air.desktop()
        air.framebuffer(2560, 1600)
        report = air.desktop()
        self.assertEqual((report['bar'], report['bar_height']), ('joined', 'cleared'))
        self.assertEqual(json.loads(path.read_text()), json.loads(original))
        self.assertFalse((air.home / NOTCH.SHELL_TOML).exists())
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)

    def test_marks_survive_the_shell_rewriting_its_file(self):
        air = Air(self, framebuffer=(2560, 1664))
        path = air.shell()
        air.desktop()
        # Omarchy's shell rewrites shell.json with sorted keys when the bar is changed.
        path.write_text(json.dumps(json.loads(path.read_text()), indent=2, sort_keys=True) + '\n')
        air.framebuffer(2560, 1600)
        air.desktop()
        self.assertEqual(json.loads(path.read_text()), LAYOUT)

    def test_a_missing_anchor_is_restored_missing(self):
        air = Air(self, framebuffer=(2560, 1664))
        layout = json.loads(json.dumps(LAYOUT))
        del layout['bar']['centerAnchor']
        path = air.shell(layout)
        air.desktop()
        center = json.loads(path.read_text())['bar']['layout']['center']
        self.assertEqual([e['id'] for e in center][3], 'omarchy.spacer')  # middle of five
        air.framebuffer(2560, 1600)
        air.desktop()
        self.assertEqual(json.loads(path.read_text()), layout)

    def test_string_entries_and_an_empty_center(self):
        air = Air(self, framebuffer=(2560, 1664))
        layout = json.loads(json.dumps(LAYOUT))
        layout['bar']['layout']['center'] = ['omarchy.clock', 'omarchy.weather']
        path = air.shell(layout)
        air.desktop()
        center = json.loads(path.read_text())['bar']['layout']['center']
        self.assertEqual(center[0], 'omarchy.clock')
        self.assertEqual(center[1]['id'], 'omarchy.spacer')
        layout['bar']['layout']['center'] = []
        path = air.shell(layout)
        air.desktop()
        self.assertEqual([e['id'] for e in json.loads(path.read_text())['bar']['layout']['center']], ['omarchy.spacer'])

    def test_a_center_that_already_has_a_spacer_is_left_alone(self):
        air = Air(self, framebuffer=(2560, 1664))
        layout = json.loads(json.dumps(LAYOUT))
        layout['bar']['layout']['center'].insert(0, {'id': 'omarchy.spacer', 'size': 12})
        path = air.shell(layout)
        before = path.read_bytes()
        report = air.desktop()
        self.assertTrue(report['bar'].startswith('skipped'))
        self.assertEqual(path.read_bytes(), before)
        # Without the split, a strip-tall bar would only leave the cutout over the widgets.
        self.assertEqual(report['bar_height'], 'unchanged')
        self.assertFalse((air.home / NOTCH.SHELL_TOML).exists())

    def test_a_split_that_is_skipped_later_clears_its_bar_height(self):
        air = Air(self, framebuffer=(2560, 1664))
        path = air.shell()
        air.desktop()
        config = json.loads(path.read_text())
        config['bar']['layout'] = 'not a layout'
        path.write_text(json.dumps(config))
        report = air.desktop()
        self.assertEqual(report['bar_height'], 'cleared')
        self.assertFalse((air.home / NOTCH.SHELL_TOML).exists())

    def test_the_login_command_runs_the_desktop_step(self):
        air = Air(self, framebuffer=(2560, 1664))
        path = air.shell()
        with mock.patch.object(NOTCH.Path, 'home', return_value=air.home), \
                mock.patch.object(NOTCH.os, 'geteuid', return_value=1000), \
                mock.patch.object(NOTCH, 'panel_scale', return_value=2.0):
            code, out, _ = run(['--desktop', '--root', str(air.root)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['bar'], 'split around the cutout')
        self.assertEqual(json.loads(path.read_text())['bar']['centerAnchor'], 'omarchy.spacer')

    def test_without_a_shell_file_the_defaults_are_split_and_the_file_removed_again(self):
        air = Air(self, framebuffer=(2560, 1664))
        air.desktop()
        path = air.home / NOTCH.SHELL_JSON
        bar = json.loads(path.read_text())['bar']
        self.assertEqual([e['id'] for e in bar['layout']['center']], ['omarchy.clock', 'omarchy.spacer'])
        air.framebuffer(2560, 1600)
        report = air.desktop()
        self.assertEqual(report['bar'], 'joined; removed the file it created')
        self.assertFalse(path.exists())

    def test_a_created_file_the_owner_changed_is_kept(self):
        air = Air(self, framebuffer=(2560, 1664))
        air.desktop()
        path = air.home / NOTCH.SHELL_JSON
        config = json.loads(path.read_text())
        config['bar']['transparent'] = True
        path.write_text(json.dumps(config))
        air.framebuffer(2560, 1600)
        air.desktop()
        self.assertTrue(json.loads(path.read_text())['bar']['transparent'])

    def test_an_existing_shell_toml_keeps_every_owner_line(self):
        air = Air(self, framebuffer=(2560, 1664))
        air.shell()
        toml = air.home / NOTCH.SHELL_TOML
        original = '[font]\nbase-size = 9\n\n[bar]\nbackground-alpha = 0.8\n'
        toml.write_text(original)
        air.desktop()
        text = toml.read_text()
        # 32 px of strip at base 9: 43 * 9 / 12 renders 32 px tall.
        self.assertEqual(NOTCH.toml_value(text, 'bar', 'size-horizontal'), '43')
        self.assertIn('[bar]\nsize-horizontal = 43', text)
        air.framebuffer(2560, 1600)
        air.desktop()
        self.assertEqual(toml.read_text(), original)

    def test_a_marked_header_stays_while_the_owner_added_keys_under_it(self):
        air = Air(self, framebuffer=(2560, 1664))
        air.shell()
        air.desktop()
        toml = air.home / NOTCH.SHELL_TOML
        toml.write_text(toml.read_text() + 'background-alpha = 0.5\n')
        air.framebuffer(2560, 1600)
        air.desktop()
        self.assertEqual(toml.read_text(), '[bar]\nbackground-alpha = 0.5\n')

    def test_the_owner_bar_height_is_kept(self):
        air = Air(self, framebuffer=(2560, 1664))
        air.shell()
        toml = air.home / NOTCH.SHELL_TOML
        toml.write_text('[bar]\nsize-horizontal = 40\n')
        report = air.desktop()
        self.assertEqual(report['bar_height'], 'kept: your own [bar] size-horizontal')
        self.assertEqual(toml.read_text(), '[bar]\nsize-horizontal = 40\n')

    def test_sizes_follow_the_scale_and_the_font(self):
        self.assertEqual(NOTCH.spacer_size(2.0), 173)
        self.assertEqual(NOTCH.spacer_size(1.6), 213)
        self.assertEqual(NOTCH.bar_size(2.0, 12), 32)
        self.assertEqual(NOTCH.bar_size(1.6, 12), 40)
        self.assertEqual(NOTCH.bar_size(2.0, 20), 26)  # the theme bar is already taller than the strip
        # Omarchy renders size-horizontal * base / 12, so the bar always covers the strip.
        for scale in (1.0, 1.25, 1.5, 1.6, 2.0):
            for base in range(9, 21):
                rendered = round(NOTCH.bar_size(scale, base) * base / 12)
                self.assertGreaterEqual(rendered, -(-64 // scale), (scale, base))

    def test_other_macs_and_unknown_framebuffers_are_left_alone(self):
        for board, framebuffer in ((b'apple,j613', (2560, 1664)), (b'apple,j713', (1920, 1200))):
            air = Air(self, framebuffer=framebuffer, board=board)
            path = air.shell()
            before = path.read_bytes()
            self.assertEqual(air.desktop()['bar'], 'unchanged')
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse((air.home / NOTCH.SHELL_TOML).exists())

    def test_a_fifo_in_place_of_the_file_is_refused_not_waited_on(self):
        air = Air(self, framebuffer=(2560, 1664))
        path = air.home / NOTCH.SHELL_JSON
        path.parent.mkdir(parents=True)
        os.mkfifo(path)
        with self.assertRaisesRegex(NOTCH.Refused, 'not a regular file'):
            air.desktop()

    def test_desktop_refuses_root(self):
        with mock.patch.object(NOTCH.os, 'geteuid', return_value=0):
            code, _, err = run(['--desktop'])
        self.assertEqual(code, 1)
        self.assertIn('desktop user', err)

    def test_the_panel_scale_comes_from_hyprland(self):
        monitors = [{'name': 'Unknown-1', 'width': 2560, 'height': 1664, 'scale': 1.6}]
        result = mock.Mock(stdout=json.dumps(monitors))
        with mock.patch.object(NOTCH.subprocess, 'run', return_value=result):
            self.assertEqual(NOTCH.panel_scale(), 1.6)
        with mock.patch.object(NOTCH.subprocess, 'run', side_effect=FileNotFoundError):
            self.assertEqual(NOTCH.panel_scale(), 2.0)


class PackagingTests(unittest.TestCase):
    def test_the_channel_pins_the_tool_loader(self):
        self.assertEqual(CHANNEL['notch_loader'], {NOTCH.STAGED: NOTCH.NOTCH['sha256']})
        self.assertEqual(NOTCH.NOTCH['size'], 0x110000)
        for path in ('usr/bin/m4-notch', NOTCH.STAGED, 'usr/lib/systemd/user/omarchy-m4-notch.service'):
            self.assertIn(path, CHANNEL['allowed_paths']['omarchy-m4-boot'])

    def test_the_boot_package_ships_the_tool_unit_and_m1n1_notices(self):
        recipe = (PACKAGE / 'PKGBUILD').read_text()
        # The notch switch ships from omarchy-m4-boot 1-12 on.
        self.assertGreaterEqual(int(re.search(r'^pkgrel=(\d+)$', recipe, re.M).group(1)), 12)
        self.assertIn('j713-m1n1-stage2-notch1.bin', recipe)
        source = re.search(r'^source=\((.*?)\)\n', recipe, re.S | re.M).group(1)
        names = [n.split('::')[0] for n in re.findall(r"['\"]([^'\"]+)['\"]", re.sub(r'#[^\n]*', '', source))]
        sums = re.findall(r"'([0-9a-f]{64})'", re.search(r'^sha256sums=\((.*?)\)\n', recipe, re.S | re.M).group(1))
        pins = dict(zip(names, sums))
        self.assertEqual(pins['m4-notch.py'], sha(SOURCE.read_bytes()))
        self.assertEqual(pins['omarchy-m4-notch.service'], sha((PACKAGE / 'omarchy-m4-notch.service').read_bytes()))
        self.assertEqual(pins['m1n1-stage2-notch.bin'], NOTCH.NOTCH['sha256'])
        self.assertEqual(pins['m1n1-LICENSE'], sha((ROOT / 'display/j713/m1n1-base/LICENSE').read_bytes()))
        self.assertIn('install -Dm755 m4-notch.py "$pkgdir/usr/bin/m4-notch"', recipe)

    def test_the_login_unit_only_runs_the_desktop_step(self):
        unit = (PACKAGE / 'omarchy-m4-notch.service').read_text()
        self.assertIn('ExecStart=/usr/bin/m4-notch --desktop\n', unit)
        self.assertIn('WantedBy=graphical-session.target\n', unit)
        script = (PACKAGE / 'omarchy-m4-boot.install').read_text()
        self.assertIn('systemctl --global enable omarchy-m4-notch.service', script)
        self.assertIn('systemctl --global disable omarchy-m4-notch.service', script)
        # Installing the package never touches boot.bin: only an owner's --show does.
        commands = [line for line in script.splitlines() if line.strip() and not line.strip().startswith('#')]
        self.assertFalse([line for line in commands if 'm4-notch' in line and 'omarchy-m4-notch.service' not in line])


if __name__ == '__main__':
    unittest.main()


# Each mutant undoes one part of the change; the named test must fail against it.
MUTANTS = {
    'show_any_loader': ("    require(current in KNOWN_LOADERS, 'boot.bin starts with an unknown stage 2 ' + current + '; nothing changed')\n    notch =",
                        "    notch =", ShowHideTests, 'test_show_refuses_an_unknown_stage2'),
    'show_no_pin_check': ("    require(len(notch) == NOTCH['size'] and sha(notch) == NOTCH['sha256'],",
                          "    require(True,", ShowHideTests, 'test_show_refuses_a_staged_loader_that_is_not_the_pin'),
    'show_no_backup': ("    compose.write(root / BACKUP, data)\n", "", ShowHideTests,
                       'test_show_replaces_only_the_stage2_and_keeps_the_whole_old_file'),
    'show_ignores_pacman': ("os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o000)", "os.O_WRONLY | os.O_CREAT, 0o000)",
                            ShowHideTests, 'test_show_refuses_while_pacman_runs'),
    'backup_dropped_after_unlock': ("        compose.write(root / BOOT, desired)\n        drop_backup(root, report)\n    return report",
                                    "        compose.write(root / BOOT, desired)\n    drop_backup(root, report)\n    return report",
                                    ShowHideTests, 'test_hide_drops_the_backup_while_it_still_holds_the_lock'),
    'lock_never_released': ("        (root / PACMAN_LOCK).unlink(missing_ok=True)\n", "        pass\n", ShowHideTests,
                            'test_pacman_lock_is_held_while_boot_bin_is_written_and_released_after'),
    'write_outside_the_lock': ("    with guard(root, data):\n        compose.write(root / BACKUP, data)\n        compose.write(root / BOOT, desired)\n",
                               "    compose.write(root / BACKUP, data)\n    compose.write(root / BOOT, desired)\n",
                               ShowHideTests, 'test_pacman_lock_is_held_while_boot_bin_is_written_and_released_after'),
    'stale_backup_kept': ("                drop_backup(root, report)\n        return report", "                pass\n        return report",
                          ShowHideTests, 'test_hide_after_a_macos_recovery_removes_the_stale_backup'),
    'lock_without_a_backup': ("        if os.path.lexists(root / BACKUP):\n", "        if True:\n", ShowHideTests,
                              'test_hide_when_hidden_with_no_backup_does_not_wait_for_pacman'),
    'unlink_failure_refuses': ("    except OSError as error:\n        report['backup'] = 'could not remove '",
                               "    except KeyError as error:\n        report['backup'] = 'could not remove '",
                               ShowHideTests, 'test_a_backup_that_cannot_be_removed_does_not_undo_a_finished_hide'),
    'hide_restores_the_old_file': ("    desired = splice(compose, data, layout, saved['loader'])",
                                   "    desired = (root / BACKUP).read_bytes()", ShowHideTests,
                                   'test_hide_restores_the_old_stage2_and_keeps_a_later_kernel_and_devicetree'),
    'hide_keeps_backup': ("        (root / BACKUP).unlink(missing_ok=True)\n", "        pass\n", ShowHideTests,
                          'test_hide_restores_the_old_stage2_and_keeps_a_later_kernel_and_devicetree'),
    'spacer_first': ("    at = ids.index(anchor) + 1 if anchor and anchor in ids else (len(center) + 1) // 2",
                     "    at = 0", DesktopTests, 'test_shown_boot_splits_the_center_around_the_cutout'),
    'spacer_not_anchor': ("    bar['centerAnchor'] = SPACER\n    return config, 'split", "    return config, 'split",
                          DesktopTests, 'test_shown_boot_splits_the_center_around_the_cutout'),
    'anchor_not_restored': ("            bar['centerAnchor'] = anchor\n", "            pass\n", DesktopTests,
                            'test_a_cropped_boot_after_a_shown_one_restores_the_owner_files'),
    'cropped_boot_writes': ("        if new is not None:\n            write_text(json_path,",
                            "        if True:\n            new = new or config\n            write_text(json_path,",
                            DesktopTests, 'test_cropped_boot_without_marks_changes_nothing'),
    'no_font_compensation': ("    return max(THEME_BAR_SIZE, math.ceil(strip * BASE_FONT / base))",
                             "    return max(THEME_BAR_SIZE, strip)", DesktopTests, 'test_sizes_follow_the_scale_and_the_font'),
    'no_padding': ("    return math.ceil(CUTOUT_WIDTH / scale) + 2 * PAD", "    return math.ceil(CUTOUT_WIDTH / scale)",
                   DesktopTests, 'test_shown_boot_splits_the_center_around_the_cutout'),
    'owner_bar_height_replaced': ("    if toml_value(text, 'bar', 'size-horizontal') is not None:",
                                  "    if False:", DesktopTests, 'test_the_owner_bar_height_is_kept'),
    'marked_header_always_removed': ("            if any(KEY.match(x) and TOML_MARK not in x for x in following):",
                                     "            if False:", DesktopTests,
                                     'test_a_marked_header_stays_while_the_owner_added_keys_under_it'),
    'created_file_kept': ("                    write_text(json_path, '')\n", "", DesktopTests,
                          'test_without_a_shell_file_the_defaults_are_split_and_the_file_removed_again'),
    'height_without_split': ("    if shown and report['bar'] in SPLIT:", "    if shown:", DesktopTests,
                             'test_a_center_that_already_has_a_spacer_is_left_alone'),
    'desktop_cli_skipped': ("            report = desktop(args.root, Path.home())", "            report = {}", DesktopTests,
                            'test_the_login_command_runs_the_desktop_step'),
    'mode_not_kept': ("        mode = path.stat().st_mode & 0o777\n", "        pass\n", DesktopTests,
                      'test_a_cropped_boot_after_a_shown_one_restores_the_owner_files'),
}


class MutantTests(unittest.TestCase):
    def test_each_mutant_fails_its_test(self):
        global NOTCH
        text = SOURCE.read_text()
        for name, (old, new, case, test) in MUTANTS.items():
            with self.subTest(name):
                self.assertEqual(text.count(old), 1, name)
                module = types.ModuleType('m4_notch_' + name)
                module.__file__ = str(SOURCE)
                exec(compile(text.replace(old, new), str(SOURCE), 'exec'), module.__dict__)
                module.composer = lambda: COMPOSE
                saved, NOTCH = NOTCH, module
                try:
                    result = unittest.TestResult()
                    case(test).run(result)
                finally:
                    NOTCH = saved
                self.assertFalse(result.wasSuccessful(), name + ' survived ' + test)
