#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Build every upstream M4 (T8132) board devicetree, with and without our patches.

Usage: m4-board-dtbs.py <asahi-tree>
The tree comes from tools/ci/fetch-asahi-dts.sh. Prints a JSON report: for each
board, the dtc warnings, the nodes this repository's patches/00*.patch enable or
disable compared with upstream, and the enabled nodes whose iommus, phys,
power-domains or mboxes point at a node that is missing or disabled. Only the
J713 board is qualified; the others show what the shared patches change on
Macs nobody here has booted.
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

ROOT = Path(__file__).resolve().parents[1]
ASAHI_COMMIT = '77cb8f24c2381a8abb7272d7bbdec548d6426a8a'
APPLE = 'arch/arm64/boot/dts/apple'
PATCHES = sorted((ROOT / 'patches').glob('[0-9][0-9][0-9][0-9]-*.patch'))
# Phandle-list properties checked on enabled nodes, with the cells property of their target.
REFERENCES = (('iommus', '#iommu-cells'), ('phys', '#phy-cells'),
              ('power-domains', '#power-domain-cells'), ('mboxes', '#mbox-cells'))
LOCATION = re.compile(r'^.*?: (?=(?:Warning|ERROR) \()')

spec = importlib.util.spec_from_file_location('adt_audit', ROOT / 'tools/adt-audit.py')
audit = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = audit
spec.loader.exec_module(audit)


def boards(apple):
    return sorted(path.name[len('t8132-'):-len('.dts')] for path in Path(apple).glob('t8132-*.dts'))


def apply_patches(apple, patches, workspace):
    """Copy the Apple DTS directory into workspace and apply patches in order; return the copy."""
    target = Path(workspace) / APPLE
    shutil.copytree(apple, target)
    for patch in patches:
        result = subprocess.run(['git', 'apply', '--whitespace=nowarn', str(patch)],
                                cwd=workspace, capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(f'{patch.name} does not apply: {result.stderr.strip()}')
    return target


def compile_board(apple, include, board):
    """Preprocess and compile t8132-<board>.dts; return (dtb bytes, sorted warnings)."""
    preprocess = subprocess.run(
        [os.environ.get('CC', 'cc'), '-E', '-nostdinc', '-I', str(include), '-I', str(apple),
         '-undef', '-D__DTS__', '-x', 'assembler-with-cpp', str(Path(apple) / f't8132-{board}.dts')],
        capture_output=True, check=True)
    result = subprocess.run(['dtc', '-I', 'dts', '-O', 'dtb'], input=preprocess.stdout, capture_output=True)
    messages = result.stderr.decode(errors='replace').splitlines()
    if result.returncode:
        raise RuntimeError(f't8132-{board}.dts does not compile:\n' + '\n'.join(messages))
    warnings = sorted(LOCATION.sub('', line) for line in messages if LOCATION.match(line))
    return result.stdout, warnings


def status(node):
    return audit.cstrings(node.props['status'])[0] if 'status' in node.props else 'okay'


def enabled_nodes(root):
    """Map every enabled node path (its own status and all its parents okay) to its first compatible."""
    enabled = {}
    def walk(node):
        if status(node) not in ('okay', 'ok'):
            return
        enabled[node.path] = (audit.node_compatibles(node) or [''])[0]
        for child in node.children:
            walk(child)
    walk(root)
    return enabled


def dangling(root, enabled):
    """Enabled nodes whose phandle references point at a missing or disabled node."""
    phandles = audit.fdt_phandles(root)
    found = []
    for node in audit.fdt_walk(root):
        if node.path not in enabled:
            continue
        for prop, cells_name in REFERENCES:
            if prop not in node.props:
                continue
            cells, index = audit.be32s(node.props[prop]), 0
            while index < len(cells):
                target = phandles.get(cells[index])
                if target is None:
                    found.append(f'{node.path} {prop} -> missing phandle {cells[index]:#x}')
                    break
                if target.path not in enabled:
                    found.append(f'{node.path} {prop} -> disabled {target.path}')
                index += 1 + audit.one_be32(target.props.get(cells_name), 0)
    return sorted(set(found))


def changed(before, after):
    """Properties a patch added, removed or changed on nodes that exist both before and after."""
    found = []
    for path in sorted(set(before) & set(after)):
        old, new = before[path], after[path]
        for name in sorted(set(old) | set(new)):
            if name != 'phandle' and old.get(name) != new.get(name):
                found.append(f'{path} {name}')
    return found


def describe(dtb, warnings):
    root = audit.parse_dtb(dtb)
    enabled = enabled_nodes(root)
    props = {node.path: node.props for node in audit.fdt_walk(root)}
    return {'compatible': audit.node_compatibles(root), 'warnings': warnings, 'props': props,
            'model': audit.cstrings(root.props.get('model', b''))[0] if 'model' in root.props else '',
            'enabled': enabled, 'dangling': dangling(root, enabled)}


def report(tree, patches=PATCHES):
    tree = Path(tree)
    include, upstream_apple = tree / 'include', tree / APPLE
    with tempfile.TemporaryDirectory() as workspace:
        patched_apple = apply_patches(upstream_apple, patches, workspace)
        result = {'asahi_commit': ASAHI_COMMIT, 'patches': [patch.name for patch in patches], 'boards': {}}
        for board in boards(upstream_apple):
            before = describe(*compile_board(upstream_apple, include, board))
            after = describe(*compile_board(patched_apple, include, board))
            result['boards'][board] = {
                'model': after['model'],
                'compatible': after['compatible'],
                'warnings': after['warnings'],
                'enabled_by_patches': sorted(f'{path} {compatible}'.rstrip()
                                             for path, compatible in after['enabled'].items()
                                             if path not in before['enabled']),
                'disabled_by_patches': sorted(path for path in before['enabled'] if path not in after['enabled']),
                'changed_by_patches': changed(before['props'], after['props']),
                'dangling_upstream': before['dangling'],
                'dangling_added': sorted(set(after['dangling']) - set(before['dangling'])),
            }
        for board in boards(patched_apple):
            if board not in result['boards']:
                raise RuntimeError(f'patches add a board file t8132-{board}.dts')
    return result


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    print(json.dumps(report(args[0]), indent=1, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
