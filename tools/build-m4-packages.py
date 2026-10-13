#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Build the M4 channel packages with makepkg, unsigned, into a new directory.

Each package directory under packaging/omarchy-m4/ is copied to a scratch
tree together with the repository files its PKGBUILD names, then built for
aarch64. No package compiles code, so an x86_64 host builds them. Sources are
checked against the PKGBUILD hashes; SOURCE_DATE_EPOCH is the commit time.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / 'packaging/omarchy-m4'
# Repository files a PKGBUILD lists by bare name, staged next to it.
STAGED = {
    'omarchy-m4-boot': {'m4-boot-compose.py': 'tools/m4-boot-compose.py',
                        'm4-update-check.py': 'tools/m4-update-check.py',
                        'm4-channel-setup.py': 'tools/m4-channel-setup.py',
                        'omarchy-m4-update': 'tools/omarchy-m4-update',
                        'MIT.txt': 'LICENSES/MIT.txt',
                        'GPL-2.0-or-later.txt': 'LICENSES/GPL-2.0-or-later.txt'},
    'omarchy-m4-speaker': {'m4-speaker-module.py': 'tools/m4-speaker-module.py',
                           'MIT.txt': 'LICENSES/MIT.txt',
                           'GPL-2.0-only.txt': 'LICENSES/GPL-2.0-only.txt'},
}
MAKEPKG_CONF = '''\
CARCH="aarch64"
CHOST="aarch64-unknown-linux-gnu"
DLAGENTS=('https::/usr/bin/curl -qgfL --proto =https --proto-redir =https --retry 3 --retry-delay 5 -o %o %u')
VCSCLIENTS=()
BUILDENV=(!distcc !color !ccache !check !sign)
OPTIONS=(!strip docs !libtool !staticlibs emptydirs !zipman !purge !debug !lto)
INTEGRITY_CHECK=(sha256)
PACKAGER="KB2UKA <kb2uka80@gmail.com>"
COMPRESSZST=(zstd -c -T0 -12 -)
PKGEXT='.pkg.tar.zst'
SRCEXT='.src.tar.gz'
'''


def require(condition, message):
    if not condition:
        raise SystemExit('build-m4-packages: ' + message)


def commit_time():
    return subprocess.check_output(['git', '-C', str(ROOT), 'log', '-1', '--format=%ct', 'HEAD'],
                                   text=True).strip()


def build(output, sources=None):
    require(not output.exists() and not output.is_symlink(), 'Output must be a new directory')
    names = json.loads((PACKAGING / 'channel.json').read_text())['packages']
    output.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix='m4-packages-') as scratch:
        scratch = Path(scratch)
        config = scratch / 'makepkg.conf'
        config.write_text(MAKEPKG_CONF)
        env = dict(os.environ, PKGDEST=str(output), SRCDEST=str(sources or scratch / 'sources'),
                   BUILDDIR=str(scratch / 'build'), SOURCE_DATE_EPOCH=commit_time(),
                   LC_ALL='C', TZ='UTC')
        Path(env['SRCDEST']).mkdir(parents=True, exist_ok=True)
        for name in names:
            stage = scratch / 'stage' / name
            shutil.copytree(PACKAGING / name, stage)
            for staged, source in STAGED.get(name, {}).items():
                require(not (stage / staged).exists(), 'Staged file shadows a packaging file: ' + staged)
                shutil.copyfile(ROOT / source, stage / staged)
            subprocess.run(['makepkg', '--config', str(config), '--nodeps', '--noconfirm',
                            '--cleanbuild', '--clean', '--noprogressbar'],
                           cwd=stage, env=env, check=True)
    built = sorted(p.name for p in output.iterdir())
    require(len(built) == len(names), 'Unexpected build output: ' + ', '.join(built))
    print('\n'.join(built))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--out', type=Path, required=True, help='new directory for the packages')
    parser.add_argument('--sources', type=Path, help='download cache for remote sources')
    args = parser.parse_args()
    build(args.out.resolve(), args.sources)


if __name__ == '__main__':
    main()
