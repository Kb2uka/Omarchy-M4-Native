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
import sys
import tempfile
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

    def test_patch_set_is_complete_and_a_bad_patch_is_refused(self):
        self.assertGreaterEqual(len(self.report['patches']), 17)
        with tempfile.TemporaryDirectory() as temporary:
            bad = Path(temporary) / '9999-bad.patch'
            bad.write_text('--- a/arch/arm64/boot/dts/apple/t8132.dtsi\n'
                           '+++ b/arch/arm64/boot/dts/apple/t8132.dtsi\n'
                           '@@ -1,1 +1,1 @@\n-no such line in the file\n+replacement\n')
            with self.assertRaisesRegex(RuntimeError, '9999-bad.patch does not apply'):
                MODULE.apply_patches(Path(TREE) / MODULE.APPLE, [bad], Path(temporary) / 'work')

    def test_j713_keeps_its_single_warning_baseline(self):
        """tools/mkdtb.sh documents exactly one simple_bus_reg warning for the J713 build."""
        warnings = self.report['boards']['j713']['warnings']
        self.assertEqual(len(warnings), 1, warnings)
        self.assertIn('Warning (simple_bus_reg)', warnings[0])


SYNTHETIC = """/dts-v1/;
/ {
    compatible = "test,board";
    #address-cells = <1>;
    #size-cells = <0>;
    off: iommu@1 { compatible = "test,iommu"; reg = <1>; #iommu-cells = <1>; status = "disabled"; };
    on: iommu@2 { compatible = "test,iommu"; reg = <2>; #iommu-cells = <1>; };
    domain: power@3 { compatible = "test,power"; reg = <3>; #power-domain-cells = <0>; };
    clk: clock@c { compatible = "test,clock"; reg = <0xc>; #clock-cells = <1>; };
    good@4 { compatible = "test,good"; reg = <4>; iommus = <&on 7>, <&on 8>; power-domains = <&domain>;
             clocks = <&clk 1>; assigned-clocks = <&clk 2>; };
    stale@5 { compatible = "test,stale"; reg = <5>; iommus = <&on 1>, <&off 2>;
              phys = <&offphy>; mboxes = <&offmbox>; };
    offphy: phy@9 { compatible = "test,phy"; reg = <9>; #phy-cells = <0>; status = "disabled"; };
    offmbox: mbox@b { compatible = "test,mbox"; reg = <0xb>; #mbox-cells = <0>; status = "disabled"; };
    orphan@6 { compatible = "test,orphan"; reg = <6>; power-domains = <0x7777>; };
    quiet@7 { compatible = "test,quiet"; reg = <7>; iommus = <&off 3>; status = "disabled"; };
    bus@8 {
        compatible = "test,bus"; reg = <8>; status = "disabled";
        #address-cells = <1>; #size-cells = <0>;
        child@0 { compatible = "test,child"; reg = <0>; };
    };
};
"""


class BoardDevicetreeTool(unittest.TestCase):
    """The tool's own logic, on a small synthetic devicetree with known answers."""

    @classmethod
    def setUpClass(cls):
        if shutil.which('dtc') is None:
            if os.environ.get('CI'):
                raise AssertionError('dtc is required in CI')
            raise unittest.SkipTest('dtc not installed')
        dtb = subprocess.run(['dtc', '-I', 'dts', '-O', 'dtb'], input=SYNTHETIC.encode(),
                             capture_output=True, check=True).stdout
        cls.root_dtb = dtb
        cls.root = MODULE.audit.parse_dtb(dtb)
        cls.enabled = MODULE.enabled_nodes(cls.root)

    def test_enabled_nodes_follow_status_and_parents(self):
        self.assertEqual(sorted(self.enabled), ['/', '/clock@c', '/good@4', '/iommu@2', '/orphan@6', '/power@3',
                                                '/stale@5'])
        self.assertEqual(self.enabled['/good@4'], 'test,good')

    def test_dangling_finds_disabled_and_missing_targets_only(self):
        self.assertEqual(MODULE.dangling(self.root, self.enabled), [
            '/orphan@6 power-domains -> missing phandle 0x7777',
            '/stale@5 iommus -> disabled /iommu@1',
            '/stale@5 mboxes -> disabled /mbox@b',
            '/stale@5 phys -> disabled /phy@9',
        ])

    def test_changed_ignores_phandle_renumbering_but_not_real_edits(self):
        renumbered = SYNTHETIC.replace(
            '    off: iommu@1',
            '    first@0 { compatible = "test,first"; reg = <0>; power-domains = <&extra>; };\n'
            '    extra: power@a { compatible = "test,power"; reg = <0xa>; #power-domain-cells = <0>; };\n'
            '    off: iommu@1').replace('iommus = <&on 7>, <&on 8>', 'iommus = <&on 7>, <&on 9>')
        before = MODULE.describe(self.root_dtb, [])
        after = MODULE.describe(subprocess.run(['dtc', '-I', 'dts', '-O', 'dtb'], input=renumbered.encode(),
                                               capture_output=True, check=True).stdout, [])
        self.assertNotEqual(MODULE.audit.be32s(before['props_raw']['/good@4']['power-domains']),
                            MODULE.audit.be32s(after['props_raw']['/good@4']['power-domains']),
                            'the fixture must actually renumber phandles')
        self.assertEqual(MODULE.changed(before['props'], after['props']), ['/good@4 iommus'])

    def test_enabled_digest_ignores_renumbering_and_disabled_nodes_only(self):
        quiet = SYNTHETIC.replace(
            '    off: iommu@1',
            '    first@0 { compatible = "test,first"; reg = <0>; power-domains = <&extra>; status = "disabled"; };\n'
            '    extra: power@a { compatible = "test,power"; reg = <0xa>; #power-domain-cells = <0>; status = "disabled"; };\n'
            '    off: iommu@1')
        edited = quiet.replace('iommus = <&on 7>, <&on 8>', 'iommus = <&on 7>, <&on 9>')
        explicit = SYNTHETIC.replace('compatible = "test,good"; reg = <4>;',
                                     'compatible = "test,good"; reg = <4>; status = "okay";')
        digest = lambda source: MODULE.describe(subprocess.run(
            ['dtc', '-I', 'dts', '-O', 'dtb'], input=source.encode(), capture_output=True, check=True).stdout,
            [])['enabled_digest']
        self.assertEqual(digest(quiet), digest(SYNTHETIC))
        self.assertEqual(digest(explicit), digest(SYNTHETIC), 'status "okay" means the same as no status')
        self.assertNotEqual(digest(edited), digest(SYNTHETIC))

    def test_tool_loads_without_tools_on_the_path(self):
        """The tool must import its parser itself, as when this file runs alone."""
        code = ('import importlib.util; '
                'spec = importlib.util.spec_from_file_location("tool", "tools/m4-board-dtbs.py"); '
                'spec.loader.exec_module(importlib.util.module_from_spec(spec))')
        result = subprocess.run([sys.executable, '-I', '-c', code], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_warning_location_is_stripped(self):
        line = '<stdin>:12.3-40.5: Warning (simple_bus_reg): /soc/x@1: message'
        self.assertEqual(MODULE.LOCATION.sub('', line), 'Warning (simple_bus_reg): /soc/x@1: message')


class BoardDevicetreeRecord(unittest.TestCase):
    """The committed record itself; runs everywhere, no tree needed."""

    def test_record_covers_the_six_upstream_boards(self):
        golden = json.loads(GOLDEN.read_text())
        self.assertEqual(golden['asahi_commit'], MODULE.ASAHI_COMMIT)
        self.assertEqual(sorted(golden['boards']), ['j604', 'j623', 'j624', 'j713', 'j715', 'j773g'])

    def test_fetch_script_pins_the_same_commit(self):
        script = (ROOT / 'tools/ci/fetch-asahi-dts.sh').read_text()
        self.assertIn(f'commit={MODULE.ASAHI_COMMIT}\n', script)

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
