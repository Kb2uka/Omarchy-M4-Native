#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Install authenticated recovery consoles and boot status in a pending offline M4 image."""
import argparse
import json
import os
from pathlib import Path
import stat


FILES = {
    'usr/lib/omarchy/m4-boot-status.py': 'tools/m4-boot-status.py',
    'usr/lib/systemd/system/m4-boot-status.service': 'packaging/j713-first-boot/m4-boot-status.service',
    'etc/systemd/system/m4-gpu.service.d/95-m4-boot-status.conf': 'packaging/j713-first-boot/graphics-failure.conf',
    'etc/systemd/system/sddm.service.d/95-m4-boot-status.conf': 'packaging/j713-first-boot/graphics-failure.conf',
    'etc/systemd/system/omarchy-provision-owner.service.d/95-m4-console.conf': 'packaging/j713-first-boot/owner-console.conf',
}
LINKS = {
    'etc/systemd/system/autovt@.service': '/usr/lib/systemd/system/getty@.service',
    'etc/systemd/system/getty.target.wants/getty@tty2.service': '/usr/lib/systemd/system/getty@.service',
    'etc/systemd/system/graphical.target.wants/m4-boot-status.service': '/usr/lib/systemd/system/m4-boot-status.service',
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def image_path(root, relative, allow_leaf_link=False):
    """Validate every component before any directory or payload is written."""
    path = root
    parts = Path(relative).parts
    require(parts and not Path(relative).is_absolute() and '..' not in parts, 'Invalid relative image path')
    for index, part in enumerate(parts):
        path /= part
        leaf = index == len(parts) - 1
        require(not path.is_symlink() or (leaf and allow_leaf_link), 'Symlink in image path: ' + relative)
        if not leaf and path.exists():
            require(path.is_dir(), 'Non-directory image parent: ' + relative)
    return path


def plan(root, tree):
    root, tree = Path(root).absolute(), Path(tree).resolve(strict=True)
    require(not root.is_symlink(), 'Symlink image root')
    root = root.resolve(strict=True)
    require(root.is_dir() and root != Path(root.anchor), 'An offline image directory is required')
    for marker in ('etc/m4-root-id', 'var/lib/omarchy/provisioning/pending'):
        require(image_path(root, marker).is_file(), 'Not a pending M4 owner image: ' + marker)

    template = image_path(root, 'usr/lib/systemd/system/getty@.service')
    require(template.is_file(), 'Missing packaged getty template')
    lines = [line.strip() for line in template.read_text(encoding='utf-8').splitlines()]
    require([line for line in lines if line.startswith('ExecStart=')] ==
            ['ExecStart=-/usr/bin/agetty --noreset --noclear - ${TERM}'] and
            'Alias=autovt@.service' in lines, 'Unexpected packaged getty command or alias')
    # A per-instance replacement or drop-in could turn a normal getty into an
    # automatic root login. Unknown console customizations need explicit review.
    for base in ('etc/systemd/system', 'usr/lib/systemd/system'):
        for unit in ('getty@.service', 'getty@tty2.service', 'autovt@.service', 'autovt@tty2.service'):
            if unit == 'autovt@.service':
                if base == 'usr/lib/systemd/system':
                    alias = image_path(root, base + '/' + unit, allow_leaf_link=True)
                    require(not (alias.exists() or alias.is_symlink()) or
                            (alias.is_symlink() and os.readlink(alias) in
                             ('getty@.service', '/usr/lib/systemd/system/getty@.service')),
                            'Unexpected packaged automatic console alias')
            elif not (base == 'usr/lib/systemd/system' and unit == 'getty@.service'):
                path = image_path(root, base + '/' + unit, allow_leaf_link=True)
                require(not path.exists() and not path.is_symlink(), 'Unexpected getty override: ' + str(path))
            dropin = image_path(root, base + '/' + unit + '.d')
            require(not dropin.exists() or (dropin.is_dir() and not any(dropin.iterdir())),
                    'Unexpected getty drop-in: ' + str(dropin))

    files, links = {}, {}
    for target, source in FILES.items():
        source_path = image_path(tree, source)
        require(source_path.is_file(), 'Missing first-boot source: ' + source)
        content = source_path.read_bytes()
        path = image_path(root, target)
        if path.exists():
            require(path.is_file() and path.read_bytes() == content and
                    stat.S_IMODE(path.stat().st_mode) == 0o644,
                    'Existing first-boot file differs: ' + target)
        files[path] = content
    for target, destination in LINKS.items():
        path = image_path(root, target, allow_leaf_link=True)
        if path.exists() or path.is_symlink():
            require(path.is_symlink() and os.readlink(path) == destination,
                    'Existing first-boot link differs: ' + target)
        links[path] = destination
    return files, links


def apply(root, tree):
    files, links = plan(root, tree)
    # All source files, destinations and parent components have passed preflight.
    for path, content in files.items():
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as stream:
                stream.write(content)
            path.chmod(0o644)
    for path, destination in links.items():
        if not path.is_symlink():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.symlink_to(destination)
    return {'files': len(files), 'links': len(links), 'scope': 'Offline image only'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--tree', type=Path, default=Path(__file__).resolve().parents[1])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if args.apply:
        print(json.dumps(apply(args.root, args.tree)))
    else:
        files, links = plan(args.root, args.tree)
        require(all(path.is_file() and path.read_bytes() == raw for path, raw in files.items()) and
                all(path.is_symlink() and os.readlink(path) == target for path, target in links.items()),
                'First-boot configuration is incomplete')
        print(json.dumps({'status': 'checked'}))
