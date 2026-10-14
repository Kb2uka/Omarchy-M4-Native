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
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_j713_gpu_air_gate import SOURCE as G16, item

ROOT = Path(__file__).resolve().parents[1]
TABLE_PATH = ROOT / 'packaging/m4-boards.json'
TABLE = json.loads(TABLE_PATH.read_text())
BOARDS = TABLE['boards']
SOC = TABLE['soc']['compatible']
QUALIFIED = [board for board in BOARDS if board['support'] == 'qualified']
DOC = ROOT / 'docs/m4-boards.md'
COMPATIBLE = 'sys/firmware/devicetree/base/compatible'
SHIPPED = ('tools', 'packaging', 'audio', 'gpu', 'patches', 'patches-kernel', 'patches-m1n1')
BOARD_LITERAL = re.compile(r'apple,(j\d{3}[a-z]*)(?![\w-])')


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def identity(board):
    """The root compatible list m1n1 hands Linux on that Mac (upstream t8132-<board>.dts)."""
    return f"{board['compatible']}\0{SOC}\0apple,arm-platform\0".encode()


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


# (name, call(root), exception the gate raises, message of the board refusal)
GATES = (
    ('m4-boot-compose plan', COMPOSE.plan, COMPOSE.Refused, 'This is not a J713 MacBook Air'),
    ('m4-boot-compose rollback', lambda root: COMPOSE.rollback(root, apply=True),
     COMPOSE.Refused, 'This is not a J713 MacBook Air'),
    ('m4-channel-setup preflight', lambda root: CHANNEL.preflight(root, None),
     CHANNEL.Refused, 'This is not a J713 MacBook Air'),
    ('m4-headphones boot', lambda root: HEADPHONES.run(root, 'boot', runner=never),
     HEADPHONES.Refused, 'This is not a J713 MacBook Air'),
    ('m4-lid-lock run', lid_lock, LookupError, 'not a J713; nothing to do'),
    ('m4-session-placement layout', placement_layout, ValueError, 'qualified only for J713/T8132'),
    ('m4-j713-audio-ready payload', lambda root: READY.payload(root, dict(schema=1, service=READY.SERVICE)),
     ValueError, 'Expected J713/T8132'),
    ('m4-j713-audio-register preflight', register_preflight, RuntimeError, 'Unsupported live board'),
)


def snapshot(root):
    return {str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
            for path in sorted(root.rglob('*'))}


def set_writable(root, writable):
    paths = [root, *root.rglob('*')]
    for path in paths if writable else reversed(paths):
        mode = 0o755 if path.is_dir() else 0o644
        path.chmod(mode if writable else mode & 0o555)


class LiveBoardGates(unittest.TestCase):
    """Each gate refuses every refused board with its own message and writes nothing."""

    def outcome(self, call, compatible, frozen=False):
        """Run one gate on a fixture root; frozen makes the tree read-only during the call.

        Read-only modes catch a write that is undone before the refusal, which a
        content snapshot alone would miss (except as root, where only the snapshot holds).
        """
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / COMPATIBLE).parent.mkdir(parents=True)
            (root / COMPATIBLE).write_bytes(compatible)
            before = snapshot(root)
            if frozen:
                set_writable(root, False)
            try:
                call(root)
                error = None
            except unittest.SkipTest:
                raise
            except Exception as caught:  # each gate reports through its own exception type
                error = caught
            finally:
                set_writable(root, True)
            return error, snapshot(root) == before

    def test_refused_boards_stop_at_the_board_gate(self):
        for name, call, kind, message in GATES:
            for board in BOARDS:
                if board['support'] != 'refused':
                    continue
                with self.subTest(gate=name, board=board['target']):
                    error, untouched = self.outcome(call, identity(board), frozen=True)
                    self.assertIsInstance(error, kind)
                    self.assertIn(message, str(error))
                    self.assertTrue(untouched, 'a refused board changed the filesystem')

    def test_qualified_boards_pass_the_board_gate(self):
        """Control: the same fixture with a qualified board gets past the board check."""
        for name, call, _, message in GATES:
            for board in QUALIFIED:
                with self.subTest(gate=name, board=board['target']):
                    error, _ = self.outcome(call, identity(board))
                    self.assertNotIn(message, str(error))


if __name__ == '__main__':
    unittest.main()
