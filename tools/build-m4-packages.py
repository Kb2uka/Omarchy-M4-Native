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
HEADPHONES = 'audio/j713/headphones/policy/usr/share/'
# Repository files a PKGBUILD lists by bare name, staged next to it.
STAGED = {
    'omarchy-m4-boot': {'m4-boot-compose.py': 'tools/m4-boot-compose.py',
                        'm4-update-check.py': 'tools/m4-update-check.py',
                        'm4-channel-setup.py': 'tools/m4-channel-setup.py',
                        'omarchy-m4-update': 'tools/omarchy-m4-update',
                        'm4-lid-lock.py': 'tools/m4-lid-lock.py',
                        'm4-notch.py': 'tools/m4-notch.py',
                        # Notices for the notch stage 2, an m1n1 build (display/j713).
                        'm1n1-LICENSE': 'display/j713/m1n1-base/LICENSE',
                        'm1n1-README.md': 'display/j713/m1n1-base/README.md',
                        'm1n1-LICENSE.BSD-2.libfdt': 'display/j713/m1n1-base/3rdparty_licenses/LICENSE.BSD-2.libfdt',
                        'm1n1-LICENSE.BSD-3.arm': 'display/j713/m1n1-base/3rdparty_licenses/LICENSE.BSD-3.arm',
                        'm1n1-LICENSE.BSD-3.dwc3': 'display/j713/m1n1-base/3rdparty_licenses/LICENSE.BSD-3.dwc3',
                        'm1n1-LICENSE.CC0': 'display/j713/m1n1-base/3rdparty_licenses/LICENSE.CC0',
                        'm1n1-LICENSE.GPL-2': 'display/j713/m1n1-base/3rdparty_licenses/LICENSE.GPL-2',
                        'm1n1-LICENSE.minlzma': 'display/j713/m1n1-base/3rdparty_licenses/LICENSE.minlzma',
                        'm1n1-LICENSE.OFL-1.1': 'display/j713/m1n1-base/3rdparty_licenses/LICENSE.OFL-1.1',
                        'm1n1-LICENSE.tinf': 'display/j713/m1n1-base/3rdparty_licenses/LICENSE.tinf',
                        'MIT.txt': 'LICENSES/MIT.txt',
                        'GPL-2.0-or-later.txt': 'LICENSES/GPL-2.0-or-later.txt'},
    'omarchy-m4-speaker': {'m4-speaker-module.py': 'tools/m4-speaker-module.py',
                           'MIT.txt': 'LICENSES/MIT.txt',
                           'GPL-2.0-only.txt': 'LICENSES/GPL-2.0-only.txt'},
    'omarchy-m4-gpu': {'m4-gpu-module.py': 'tools/m4-gpu-module.py',
                       'MIT.txt': 'LICENSES/MIT.txt',
                       'GPL-2.0-only.txt': 'LICENSES/GPL-2.0-only.txt'},
    'omarchy-m4-headphones': {'m4-headphones.py': 'tools/m4-headphones.py',
                              'J713Headphones.conf': HEADPHONES + 'alsa/ucm2/conf.d/J713Headphones/J713Headphones.conf',
                              'HiFi.conf': HEADPHONES + 'alsa/ucm2/conf.d/J713Headphones/HiFi.conf',
                              '83-j713-headphones.conf': HEADPHONES + 'wireplumber/wireplumber.conf.d/83-j713-headphones.conf',
                              'j713-headphones.lua': HEADPHONES + 'wireplumber/scripts/default-nodes/j713-headphones.lua',
                              'MIT.txt': 'LICENSES/MIT.txt',
                              'GPL-2.0-only.txt': 'LICENSES/GPL-2.0-only.txt'},
    'omarchy-m4-cpu': {'m4-cpu-idle.py': 'tools/m4-cpu-idle.py',
                       'MIT.txt': 'LICENSES/MIT.txt',
                       'GPL-2.0-only.txt': 'LICENSES/GPL-2.0-only.txt'},
    'omarchy-m4-evdi': {'GPL-2.0-only.txt': 'LICENSES/GPL-2.0-only.txt',
                        'LGPL-2.1-or-later.txt': 'LICENSES/LGPL-2.1-or-later.txt'},
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
