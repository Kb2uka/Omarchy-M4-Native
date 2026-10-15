# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Every M4 (T8132) Mac in packaging/m4-boards.json, against every board gate.

The table is the one place that says which M4 boards this project supports.
These tests fail when the installer, the shipped manifests or the source code
disagree with it, and they feed the real devicetree identity of every sibling
M4 Mac to each live-system board gate. The siblings carry the same
"apple,t8132" SoC string as the J713, so a gate that checks only the SoC, or
fakes a board, fails here. Passing proves the gates refuse; it does not show
that anything works on another board.
"""
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

from m4_board_identities import (BOARDS, FOREIGN_SOC, NEAR_MISS_BOARD, NEAR_MISS_SOC, QUALIFIED, REFUSED, ROOT, SOC, TABLE,
                                 TABLE_PATH, identity, refused_identities)
from test_j713_gpu_air_gate import SOURCE as G16, item

DOC = ROOT / 'docs/m4-boards.md'
COMPATIBLE = 'sys/firmware/devicetree/base/compatible'
SHIPPED = ('tools', 'packaging', 'audio', 'gpu', 'patches', 'patches-kernel', 'patches-m1n1')
BOARD_LITERAL = re.compile(r'apple,(j\d{3}[a-z]*)(?![\w-])')


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def render_rows():
    rows = ['| Board | Model identifiers | Mac | Support | Installer profile |',
            '|---|---|---|---|---|']
    for board in BOARDS:
        installer = board['installer']
        profile = 'none' if installer is None else installer['stub'] + (
            ', expert mode only' if installer['expert_only'] else '')
        rows.append(f"| {board['target']} | {', '.join(board['models'])} | {board['name']} "
                    f"| {board['support']} | {profile} |")
    return '\n'.join(rows)


class BoardTable(unittest.TestCase):
    def test_table_is_well_formed(self):
        self.assertEqual(TABLE['schema'], 1)
        self.assertEqual(TABLE['soc'], {'chip': 'T8132', 'compatible': 'apple,t8132'})
        self.assertTrue(QUALIFIED, 'at least one board must be qualified')
        for key in ('target', 'compatible'):
            values = [board[key] for board in BOARDS]
            self.assertEqual(len(values), len(set(values)), key)
        models = [model for board in BOARDS for model in board['models']]
        other = [model for entry in TABLE['other_socs'] for model in entry['models']]
        self.assertEqual(len(models + other), len(set(models + other)), 'a model identifier is listed twice')
        for board in BOARDS:
            with self.subTest(board=board['target']):
                self.assertEqual(board['compatible'], 'apple,' + board['target'].lower())
                self.assertIn(board['support'], ('qualified', 'refused'))
                self.assertTrue(all(re.fullmatch(r'Mac\d+,\d+', model) for model in board['models']))
                installer = board['installer']
                if installer is not None:
                    self.assertEqual(set(installer), {'stub', 'expert_only'})

    def test_install_command_accepts_exactly_the_qualified_models(self):
        """Run Install.command's real model gate for every model, with sysctl stubbed."""
        script = (ROOT / 'packaging/Install.command').read_text()
        gates = [line for line in script.splitlines() if 'sysctl -n hw.model' in line]
        self.assertEqual(len(gates), 1, 'Install.command must have one hardware-model gate')
        self.assertIn('/usr/sbin/sysctl -n hw.model', gates[0])
        gate = 'sysctl() { printf "%s\\n" "$MODEL"; }\n' + gates[0].replace('/usr/sbin/sysctl', 'sysctl')
        qualified = {model for board in QUALIFIED for model in board['models']}
        models = [model for board in BOARDS for model in board['models']]
        models += [model for entry in TABLE['other_socs'] for model in entry['models']]
        for model in models + ['Mac15,12', '']:
            with self.subTest(model=model):
                result = subprocess.run(['bash', '-c', gate], capture_output=True, text=True,
                                        env={'MODEL': model, 'PATH': os.environ.get('PATH', '/usr/bin:/bin')})
                self.assertEqual(result.returncode == 0, model in qualified, result.stderr)
                if model not in qualified:
                    self.assertIn('This release supports only', result.stderr)

    def test_installer_profiles_match_the_table(self):
        patch = (ROOT / 'patches/installer/j713-recovery-radio-profile.patch').read_text()
        block = re.search(r'^\+DEVICES = \{\n((?:\+.*\n)+?)\+\}', patch, re.M)
        self.assertIsNotNone(block, 'the installer DEVICES table moved')
        devices = {name: (stub, expert == 'True') for name, stub, expert in
                   re.findall(r'"(j\w+ap)": Device\("([\d.]+)", (True|False)', block.group(1))}
        expected = {board['target'].lower() + 'ap': (board['installer']['stub'], board['installer']['expert_only'])
                    for board in BOARDS if board['installer'] is not None}
        self.assertEqual(devices, expected)
        for board in QUALIFIED:
            stub, name = board['installer']['stub'], board['target'].lower() + 'ap'
            with self.subTest(board=board['target']):
                self.assertRegex(patch, rf'\+    IPSW\("{re.escape(stub)}", .*\["{name}"\]',
                                 'no qualified IPSW for the board stub')

    def test_shipped_manifests_name_a_qualified_board(self):
        boards = {board['compatible'] for board in QUALIFIED}
        qualified = json.loads((ROOT / 'audio/j713/qualified.json').read_text())
        self.assertIn(qualified['board'], boards)
        self.assertEqual(qualified['soc'], SOC)
        owner = json.loads((ROOT / 'audio/j713/owner-fir/manifest.json').read_text())
        self.assertIn(owner['board'], boards)

    def test_shipped_code_names_only_qualified_boards(self):
        """Code accepts a board by naming it, so it may name only qualified boards."""
        boards = {board['compatible'].split(',', 1)[1] for board in QUALIFIED}
        found = {}
        for top in SHIPPED:
            if not (ROOT / top).is_dir():
                continue
            for path in sorted((ROOT / top).rglob('*')):
                if not path.is_file() or path.suffix == '.md' or path == TABLE_PATH:
                    continue
                try:
                    text = path.read_text()
                except UnicodeDecodeError:
                    continue
                for name in BOARD_LITERAL.findall(text):
                    found.setdefault(name, path.relative_to(ROOT))
        self.assertIn('j713', found, 'the board scan found nothing; it is not looking in the right place')
        self.assertEqual({name: str(path) for name, path in found.items() if name not in boards}, {})

    def test_board_doc_matches_the_table(self):
        text = DOC.read_text()
        begin, end = '<!-- m4-boards:begin -->\n', '\n<!-- m4-boards:end -->'
        self.assertIn(begin, text)
        self.assertIn(end, text)
        rendered = text[text.index(begin) + len(begin):text.index(end)]
        self.assertEqual(rendered, render_rows(), 'regenerate the table in docs/m4-boards.md from the JSON')


class GpuBoardGate(unittest.TestCase):
    """The g16.rs Air bootstrap gate admits exactly the qualified boards."""

    def test_air_bootstrap_admits_only_qualified_boards(self):
        rustc = shutil.which('rustc')
        if rustc is None:
            if os.environ.get('CI'):
                self.fail('rustc is required in CI')
            self.skipTest('rustc not installed')
        source = G16.read_text()
        parts = [item(source, start) for start in ('fn firmware_allowed(', 'fn air_bootstrap_allowed(')]
        cases = ['const FW: &[u8] = b"mBoot-18000.120.36\\0";']
        for index, board in enumerate(BOARDS):
            literal = identity(board).decode().replace('\0', '\\0')
            expected = 'true' if board['support'] == 'qualified' else 'false'
            cases.append(f'#[test] fn board_{index}_{board["target"].lower()}() {{\n'
                         f'    let board: &[u8] = b"{literal}";\n'
                         f'    assert_eq!(air_bootstrap_allowed(FW, board, 1, 0), {expected});\n'
                         f'    assert_eq!(firmware_allowed(FW, board, true), {expected});\n}}')
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            (path / 'gate.rs').write_text('\n'.join(parts + cases) + '\n')
            subprocess.run([rustc, '--edition=2021', '--test', str(path / 'gate.rs'),
                            '-o', str(path / 'gate')], check=True, capture_output=True)
            result = subprocess.run([str(path / 'gate')], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f'{len(BOARDS)} passed', result.stdout)


COMPOSE = load('m4_boot_compose', 'tools/m4-boot-compose.py')
CHANNEL = load('m4_channel_setup', 'tools/m4-channel-setup.py')
HEADPHONES = load('m4_headphones', 'tools/m4-headphones.py')
LID = load('m4_lid_lock', 'tools/m4-lid-lock.py')
READY = load('m4_j713_audio_ready', 'tools/m4-j713-audio-ready.py')
REGISTER = load('m4_j713_audio_register', 'tools/m4-j713-audio-register.py')


def never(*args, **kwargs):
    raise AssertionError('a refused board reached a command: ' + repr(args))


def placement_layout(root):
    if sys.platform != 'linux':
        raise unittest.SkipTest('m4-session-placement imports Linux-only scheduling calls')
    return load('m4_session_placement', 'tools/m4-session-placement.py').cpu_layout(root)


def register_preflight(root):
    manifest = dict(module_artifacts={name: {} for name in REGISTER.MODULES},
                    kernel_release=os.uname().release, board=QUALIFIED[0]['compatible'], soc=SOC)
    return REGISTER.Registration(manifest, root=root, command=never, sleeper=never).preflight()


def lid_lock(root):
    report = LID.run(root, apply=True, switch_user=False)
    if report == {'status': 'not a J713; nothing to do'}:
        raise LookupError(report['status'])
    return report


OWNER_ASSETS = load('m4_j713_owner_assets', 'tools/m4-j713-owner-assets.py')
VIDEO_START = ROOT / 'packaging/j713-startup/m4-video-start'
LIVE_COMPATIBLE = '/' + COMPATIBLE


def owner_assets_install(root):
    """install() reads fixed paths as root; run it with every absolute path moved under the fixture."""
    def rooted(*parts):
        path = Path(*parts)
        return root / str(path).lstrip('/') if path.is_absolute() else path
    with unittest.mock.patch.object(OWNER_ASSETS, 'Path', rooted), \
         unittest.mock.patch.object(OWNER_ASSETS.os, 'getuid', return_value=0), \
         unittest.mock.patch.object(OWNER_ASSETS.os, 'geteuid', return_value=0):
        return OWNER_ASSETS.install()


def video_start_guards(root):
    """Run m4-video-start's own fail() and board and SoC lines against the fixture's compatible list."""
    lines = VIDEO_START.read_text().splitlines()
    fail = [line for line in lines if line.startswith('fail() ')]
    guards = [line for line in lines if line.startswith('grep ') and ' apple,' in line and LIVE_COMPATIBLE in line]
    if len(fail) != 1 or len(guards) != 2:
        raise AssertionError('m4-video-start board guard lines changed; update this harness')
    script = '\n'.join(fail + [line.replace(LIVE_COMPATIBLE, shlex.quote(str(root / COMPATIBLE))) for line in guards])
    result = subprocess.run(['bash', '-c', script], capture_output=True, text=True,
                            env={'PATH': '/usr/bin:/bin'})
    if result.returncode:
        raise RuntimeError(result.stderr.strip())


# (name, call(root), exception the gate raises, board refusal message,
#  SoC refusal message or None for a gate that checks only the board name)
GATES = (
    ('m4-boot-compose plan', COMPOSE.plan, COMPOSE.Refused, 'This is not a J713 MacBook Air', None),
    ('m4-boot-compose rollback', lambda root: COMPOSE.rollback(root, apply=True),
     COMPOSE.Refused, 'This is not a J713 MacBook Air', None),
    ('m4-channel-setup preflight', lambda root: CHANNEL.preflight(root, None),
     CHANNEL.Refused, 'This is not a J713 MacBook Air', None),
    ('m4-headphones boot', lambda root: HEADPHONES.run(root, 'boot', runner=never),
     HEADPHONES.Refused, 'This is not a J713 MacBook Air', None),
    ('m4-lid-lock run', lid_lock, LookupError, 'not a J713; nothing to do', None),
    ('m4-session-placement layout', placement_layout, ValueError, 'qualified only for J713/T8132',
     'qualified only for J713/T8132'),
    ('m4-j713-audio-ready payload', lambda root: READY.payload(root, dict(schema=1, service=READY.SERVICE)),
     ValueError, 'Expected J713/T8132', 'Expected J713/T8132'),
    ('m4-j713-audio-register preflight', register_preflight, RuntimeError, 'Unsupported live board',
     'Unsupported live board'),
    ('m4-video-start board and SoC lines', video_start_guards, RuntimeError, 'unsupported board',
     'unsupported SoC'),
    ('m4-j713-owner-assets install', owner_assets_install, ValueError, 'This service requires J713/T8132',
     'This service requires J713/T8132'),
)


def snapshot(root):
    return {str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
            for path in sorted(root.rglob('*'))}


WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
# Mutating os.* audit events: (argument positions holding a target path, positions holding a dir_fd).
MUTATING = {
    'os.remove': ((0,), (1,)), 'os.rmdir': ((0,), (1,)), 'os.mkdir': ((0,), (2,)),
    'os.chmod': ((0,), (2,)), 'os.chown': ((0,), (3,)), 'os.utime': ((0,), (3,)),
    'os.truncate': ((0,), ()), 'os.symlink': ((1,), (2,)),
    'os.rename': ((0, 1), (2, 3)), 'os.link': ((0, 1), (2, 3)),
    'os.setxattr': ((0,), ()), 'os.removexattr': ((0,), ()),
}
UNSET_DIR_FD = (None, -1)  # CPython reports an omitted dir_fd as -1
WATCH = {'root': None, 'attempts': []}


def touches(value, root):
    """True unless the target is provably outside root; a bare fd or relative path cannot be placed."""
    if value is None:
        return False
    if isinstance(value, int):
        return True
    path = os.fsdecode(os.fspath(value))
    if not os.path.isabs(path):
        return True
    path = os.path.realpath(path)
    return path == root or path.startswith(root + os.sep)


def record_write_attempts(event, args):
    """Audit hook: note every attempt to change a file under the watched root, even a caught one."""
    root = WATCH['root']
    if root is None:
        return
    try:
        if event == 'open':
            hit = bool(args[2] and args[2] & WRITE_FLAGS) and touches(args[0], root)
        elif event in MUTATING:
            paths, dir_fds = MUTATING[event]
            hit = (any(touches(args[index], root) for index in paths)
                   or any(args[index] not in UNSET_DIR_FD for index in dir_fds if index < len(args)))
        else:
            return
        if hit:
            WATCH['attempts'].append((event, repr(args[0])))
    except Exception:  # an audit hook must never break the code it watches
        WATCH['attempts'].append((event, 'unclassified'))


sys.addaudithook(record_write_attempts)  # a no-op unless WATCH['root'] is set


class LiveBoardGates(unittest.TestCase):
    """Each gate refuses every refused board with its own message and writes nothing."""

    def outcome(self, call, compatible):
        """Run one gate on a fixture root; return its error and every write it attempted there."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / COMPATIBLE).parent.mkdir(parents=True)
            (root / COMPATIBLE).write_bytes(compatible)
            before = snapshot(root)
            WATCH.update(root=os.path.realpath(root), attempts=[])
            try:
                call(root)
                error = None
            except unittest.SkipTest:
                raise
            except Exception as caught:  # each gate reports through its own exception type
                error = caught
            finally:
                WATCH['root'] = None
            attempts = list(WATCH['attempts'])
            # The hook sees only this process; the snapshot also catches a child process's writes.
            if snapshot(root) != before:
                attempts.append(('snapshot', 'contents changed'))
            return error, attempts

    def test_refused_boards_stop_at_the_board_gate(self):
        for name, call, kind, message, _ in GATES:
            for board in REFUSED:
                with self.subTest(gate=name, board=board['target']):
                    error, attempts = self.outcome(call, identity(board))
                    self.assertIsInstance(error, kind)
                    self.assertIn(message, str(error))
                    self.assertEqual(attempts, [], 'a refused board reached a filesystem write')

    def test_near_miss_names_are_refused(self):
        """Gates match whole names: apple,j713-x is not the J713, and apple,t8132-x is not the T8132."""
        for name, call, kind, board_message, soc_message in GATES:
            cases = [(NEAR_MISS_BOARD, board_message)] + ([(NEAR_MISS_SOC, soc_message)] if soc_message else [])
            for (label, compatible), message in cases:
                with self.subTest(gate=name, case=label):
                    error, attempts = self.outcome(call, compatible)
                    self.assertIsInstance(error, kind)
                    self.assertIn(message, str(error))
                    self.assertEqual(attempts, [], 'a refused board reached a filesystem write')

    def test_gates_that_check_the_soc_refuse_the_j713_name_on_another_soc(self):
        for name, call, kind, _, message in GATES:
            if message is None:
                continue
            with self.subTest(gate=name):
                error, attempts = self.outcome(call, FOREIGN_SOC[1])
                self.assertIsInstance(error, kind)
                self.assertIn(message, str(error))
                self.assertEqual(attempts, [], 'a refused board reached a filesystem write')

    def test_qualified_boards_pass_the_video_start_lines(self):
        """The extracted lines are the whole check, so a qualified board must pass them outright."""
        for board in QUALIFIED:
            with self.subTest(board=board['target']):
                error, attempts = self.outcome(video_start_guards, identity(board))
                self.assertIsNone(error)
                self.assertEqual(attempts, [])

    def test_qualified_boards_pass_the_board_gate(self):
        """Control: the same fixture with a qualified board gets past the board check."""
        for name, call, _, message, soc_message in GATES:
            for board in QUALIFIED:
                with self.subTest(gate=name, board=board['target']):
                    error, _ = self.outcome(call, identity(board))
                    self.assertNotIn(message, str(error))
                    if soc_message:
                        self.assertNotIn(soc_message, str(error))



POWEROFF_FIXTURES = load('poweroff_fixtures', 'tests/test_j713_poweroff_dtb.py')
# (tool, overlay it is built with): each checks a base devicetree before compiling anything.
DTB_BUILDERS = (('tools/build-j713-cpufreq-dtb.py', 'patches/j713-cpufreq.dtso'),
                ('tools/build-j713-cputemp-dtb.py', 'patches/j713-cpu-temperature.dtso'),
                ('tools/build-j713-poweroff-dtb.py', 'patches/j713-smc-poweroff.dtso'))


class DtbBuilderBoardGates(unittest.TestCase):
    """The J713 devicetree builders refuse a sibling M4 base before compiling or writing.

    Each builder pins its base DTB hash first; the test pins the sibling's hash so
    the board check itself is what has to refuse.
    """

    def build(self, tool, overlay, compatible):
        module = load('builder_' + Path(tool).stem.replace('-', '_'), tool)
        with tempfile.TemporaryDirectory() as temporary:
            base, output = Path(temporary) / 'base.dtb', Path(temporary) / 'out.dtb'
            base.write_bytes(POWEROFF_FIXTURES.dtb({(): {'compatible': compatible}}))
            with unittest.mock.patch.object(module, 'BASE_SHA256', module.sha(base.read_bytes())), \
                 unittest.mock.patch.object(module.subprocess, 'run', side_effect=never):
                try:
                    module.build(base, output, ROOT / overlay)
                    error = None
                except Exception as caught:  # each builder raises ValueError on refusal
                    error = caught
            return error, output.exists()

    def test_refused_boards_stop_at_the_board_check(self):
        for tool, overlay in DTB_BUILDERS:
            for label, compatible in refused_identities():
                with self.subTest(tool=tool, board=label):
                    error, written = self.build(tool, overlay, compatible)
                    self.assertIsInstance(error, ValueError)
                    self.assertEqual(str(error), 'Wrong board')
                    self.assertFalse(written)

    def test_qualified_boards_pass_the_board_check(self):
        for tool, overlay in DTB_BUILDERS:
            for board in QUALIFIED:
                with self.subTest(tool=tool, board=board['target']):
                    error, _ = self.build(tool, overlay, identity(board))
                    self.assertNotEqual(str(error), 'Wrong board')


class WriteRecorder(unittest.TestCase):
    """The recorder itself: it sees every kind of write under the root, and nothing else."""

    def watched(self, root, action):
        WATCH.update(root=os.path.realpath(root), attempts=[])
        try:
            action()
        except OSError:  # a refused or failed write still counts as an attempt
            pass
        finally:
            WATCH['root'] = None
        return list(WATCH['attempts'])

    def test_writes_under_the_root_are_recorded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'file').write_text('x')
            (root / 'dir').mkdir()
            actions = {
                'open': lambda: (root / 'new').write_text('x'),
                'os.mkdir': lambda: os.mkdir(root / 'made'),
                'os.remove': lambda: os.remove(root / 'new'),
                'os.rename': lambda: os.rename(root / 'made', root / 'moved'),
                'os.chmod': lambda: os.chmod(root / 'file', 0o600),
                'os.utime': lambda: os.utime(root / 'file'),
                'os.truncate': lambda: os.truncate(root / 'file', 0),
                'os.symlink': lambda: os.symlink('/nonexistent', root / 'link'),
                'os.link': lambda: os.link(root / 'file', root / 'hard'),
                'os.rmdir': lambda: os.rmdir(root / 'dir'),
            }
            if hasattr(os, 'chown'):
                actions['os.chown'] = lambda: os.chown(root / 'file', os.getuid(), os.getgid())
            if hasattr(os, 'setxattr'):
                actions['os.setxattr'] = lambda: os.setxattr(root / 'file', 'user.probe', b'x')
                actions['os.removexattr'] = lambda: os.removexattr(root / 'file', 'user.probe')
            if os.mkdir in os.supports_dir_fd:
                fd = os.open(root, os.O_RDONLY)
                self.addCleanup(os.close, fd)
                actions['os.mkdir dir_fd'] = lambda: os.mkdir('missing/probe', dir_fd=fd)
            for name, action in actions.items():
                with self.subTest(action=name):
                    self.assertNotEqual(self.watched(root, action), [])

    def test_reads_and_writes_elsewhere_are_ignored(self):
        with tempfile.TemporaryDirectory() as watched, tempfile.TemporaryDirectory() as elsewhere:
            root, other = Path(watched), Path(elsewhere)
            (root / 'file').write_text('x')
            actions = {
                'read under the root': lambda: (root / 'file').read_bytes(),
                'open elsewhere': lambda: (other / 'new').write_text('x'),
                'os.mkdir elsewhere': lambda: os.mkdir(other / 'made'),
                'os.rename elsewhere': lambda: os.rename(other / 'made', other / 'moved'),
                'os.chmod elsewhere': lambda: os.chmod(other / 'new', 0o600),
                'os.remove elsewhere': lambda: os.remove(other / 'new'),
                'os.rmdir elsewhere': lambda: os.rmdir(other / 'moved'),
            }
            for name, action in actions.items():
                with self.subTest(action=name):
                    self.assertEqual(self.watched(root, action), [])


if __name__ == '__main__':
    unittest.main()
