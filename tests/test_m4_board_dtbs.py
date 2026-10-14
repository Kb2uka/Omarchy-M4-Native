# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Every upstream M4 (T8132) board devicetree, built with this repository's patches.

CI fetches the pinned Asahi tree with tools/ci/fetch-asahi-dts.sh and points
M4_ASAHI_TREE at it. tests/fixtures/m4-board-dtbs.json records, for each board,
what our patches turn on or off; a patch that changes any board's devicetree
changes that file, so the change shows in review. Building is not booting:
nothing here shows that another board runs.
"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / 'tests/fixtures/m4-board-dtbs.json'
DOC = ROOT / 'docs/m4-board-dtbs.md'
TREE = os.environ.get('M4_ASAHI_TREE')

spec = importlib.util.spec_from_file_location('m4_board_dtbs', ROOT / 'tools/m4-board-dtbs.py')
MODULE = importlib.util.module_from_spec(spec)
spec.loader.exec_module(MODULE)


def render_rows(report):
    rows = ['| Board | Model | dtc warnings | Nodes the patches enable | Nodes the patches disable '
            '| Broken references added |',
            '|---|---|---|---|---|---|']
    for board, entry in sorted(report['boards'].items()):
        rows.append(f"| {board.upper()} | {entry['model']} | {len(entry['warnings'])} "
                    f"| {len(entry['enabled_by_patches'])} | {len(entry['disabled_by_patches'])} "
                    f"| {len(entry['dangling_added'])} |")
    return '\n'.join(rows)


class BoardDevicetrees(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        missing = [name for name, ok in (('M4_ASAHI_TREE', TREE), ('dtc', shutil.which('dtc'))) if not ok]
        if missing:
            if os.environ.get('CI'):
                raise AssertionError(f'{", ".join(missing)} required in CI (tools/ci/fetch-asahi-dts.sh)')
            raise unittest.SkipTest(f'needs {", ".join(missing)}; see tools/ci/fetch-asahi-dts.sh')
        head = subprocess.run(['git', '-C', TREE, 'rev-parse', 'HEAD'],
                              capture_output=True, text=True, check=True).stdout.strip()
        if head != MODULE.ASAHI_COMMIT:
            raise AssertionError(f'M4_ASAHI_TREE is at {head}, not the pinned {MODULE.ASAHI_COMMIT}')
        cls.report = MODULE.report(TREE)

    def test_report_matches_the_recorded_board_impact(self):
        golden = json.loads(GOLDEN.read_text())
        if self.report != golden:
            print('\n--- computed tests/fixtures/m4-board-dtbs.json ---\n'
                  + json.dumps(self.report, indent=1, sort_keys=True) + '\n--- end ---')
        self.assertEqual(self.report, golden,
                         'a patch changed what some M4 board devicetree contains; review the change, '
                         'then copy the computed report above into tests/fixtures/m4-board-dtbs.json')

    def test_patches_add_no_broken_reference_on_any_board(self):
        for board, entry in self.report['boards'].items():
            with self.subTest(board=board):
                self.assertEqual(entry['dangling_added'], [])

    def test_every_patch_was_applied(self):
        self.assertEqual(self.report['patches'], [patch.name for patch in MODULE.PATCHES])
        self.assertGreaterEqual(len(self.report['patches']), 16)

    def test_j713_keeps_its_single_warning_baseline(self):
        """tools/mkdtb.sh documents exactly one simple_bus_reg warning for the J713 build."""
        warnings = self.report['boards']['j713']['warnings']
        self.assertEqual(len(warnings), 1, warnings)
        self.assertIn('Warning (simple_bus_reg)', warnings[0])


class BoardDevicetreeRecord(unittest.TestCase):
    """The committed record itself; runs everywhere, no tree needed."""

    def test_record_covers_the_six_upstream_boards(self):
        golden = json.loads(GOLDEN.read_text())
        self.assertEqual(golden['asahi_commit'], MODULE.ASAHI_COMMIT)
        self.assertEqual(sorted(golden['boards']), ['j604', 'j623', 'j624', 'j713', 'j715', 'j773g'])

    def test_doc_table_matches_the_record(self):
        text = DOC.read_text()
        begin, end = '<!-- m4-board-dtbs:begin -->\n', '\n<!-- m4-board-dtbs:end -->'
        self.assertIn(begin, text)
        self.assertIn(end, text)
        rendered = text[text.index(begin) + len(begin):text.index(end)]
        self.assertEqual(rendered, render_rows(json.loads(GOLDEN.read_text())),
                         'regenerate the table in docs/m4-board-dtbs.md from the fixture')


if __name__ == '__main__':
    unittest.main()
