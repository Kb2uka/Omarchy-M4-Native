#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Scan every tracked regular file; attributes cannot hide content from antivirus."""
from pathlib import Path
import subprocess
import tempfile


def inventory(root):
    raw = subprocess.check_output(['git', 'ls-files', '--stage', '-z'], cwd=root)
    paths = []
    for record in raw.split(b'\0'):
        if not record:
            continue
        metadata, name = record.split(b'\t', 1)
        mode, _, stage = metadata.split()
        if mode not in (b'100644', b'100755') or stage != b'0':
            raise ValueError('Non-regular or unmerged tracked file requires review')
        if any(value in name for value in (b'\n', b'\r')):
            raise ValueError('Newline in tracked path is not supported')
        path = root / name.decode('utf-8')
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 100 * 1024**2:
            raise ValueError('Tracked file is missing, oversized or a symlink')
        with path.open('rb') as stream:
            if stream.read(43).startswith(b'version https://git-lfs.github.com/spec/v1'):
                raise ValueError('LFS payload must be materialized and reviewed before scanning')
        paths.append(str(path))
    if not paths:
        raise ValueError('No tracked files to scan')
    return paths


def scan(root):
    paths = inventory(root.resolve())
    with tempfile.TemporaryDirectory() as temp:
        listing = Path(temp) / 'files.txt'
        listing.write_text('\n'.join(paths) + '\n')
        subprocess.run(['clamscan', '--infected', '--alert-encrypted=yes',
                        '--alert-exceeds-max=yes', '--max-filesize=100M',
                        '--max-scansize=500M', '--max-recursion=20',
                        '--file-list=' + str(listing)], check=True)
    print(f'Antivirus completed for all {len(paths)} tracked files.')


if __name__ == '__main__':
    scan(Path.cwd())
