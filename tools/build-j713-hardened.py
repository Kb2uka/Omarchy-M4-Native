#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Build a candidate J713 kernel Image.gz and the five audio/j713/kernel modules.

This does not qualify a native install. Pins are constants; the CLI cannot
replace them. A full kernel make may rewrite Module.symvers; that drift is a
blocker and is not accepted.
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIO = ROOT / 'audio/j713'

KERNEL_ARCHIVE = '810339d85aa3171fb1a982b71d01c0bf4de816f81ebbe9149d0dd3268e98ce57'
SOUND_EXPORTS = '9f3e2cf8c83045e3a89b94ef7c8526c4dfb10b386d872669819cd8f4f35d4362'
BUILDER_IMAGE = 'b2069edb8a23761d9211559fe632f5464aebc4cdfa9558d0f4c93211890fd7b1'
KERNEL_RELEASE = '7.1.9-m4-gpu-backport-20261008'
DOCKCHANNEL = 'drivers/hid/dockchannel-hid/dockchannel-hid.c'
MACSMC = 'drivers/mfd/macsmc.c'
DOCKCHANNEL_BASE = '77ef81abc66a61d08818142895bc02bec2b01abe9461195c3c575799da91d1eb'
DOCKCHANNEL_HISTORICAL = 'e4eebbee612eee32bd51ce370d412afc8cf62a9a7e087a3797c2c29306cedfb4'
MACSMC_HISTORICAL = '0cbe1eaf11e473336bfc24d5036a4a15586f21c868e8fedd21aeb1ce8bcaa9d6'
HISTORICAL_PATCHES = (
    'patches-kernel/j713-input/dockchannel-start-retry.patch',
    'patches-kernel/j713-input/macsmc-key-info-lock.patch',
    'patches-kernel/j713-input/dockchannel-afe-action.patch',
)
HISTORICAL_PATCH_SHA256 = {
    'patches-kernel/j713-input/dockchannel-start-retry.patch':
        'f3b1cacd0f1704e60edbadba714ac052959356446e5e059ad345fddc1d501967',
    'patches-kernel/j713-input/macsmc-key-info-lock.patch':
        '47a09e2b804b156c8ee5e26a025980caca7a10c4f0c0ef0bcdd8baf8b3b912f4',
    'patches-kernel/j713-input/dockchannel-afe-action.patch':
        '8f9ab49462a5076b7345aa465cef99696f29cbf4d23ab5bfa629acfebee8ed89',
}
RETAINED_PATCH = 'patches-kernel/retained-hardening/dockchannel-packet-bounds.patch'
RETAINED_PATCH_SHA256 = '6f628efb429dfb4ff76f77db742dcbfd2848f3715b6936c9ab759cebe9d21583'
IMPORTED_MANIFEST = AUDIO / 'imported-source.sha256'
KBUILD_ENV = {
    'KBUILD_BUILD_TIMESTAMP': '2026-10-20',
    'KBUILD_BUILD_USER': 'KB2UKA',
    'KBUILD_BUILD_HOST': 'm4-build',
    'KBUILD_BUILD_VERSION': '19',
}
AUDIO_MODULES = (
    'snd-soc-j713-group.ko',
    'snd-soc-tas2764.ko',
    'snd-soc-macaudio.ko',
    'snd-soc-apple-mca.ko',
    'apple-admac.ko',
)
PROFILE = 'conservative-v1'


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def load_sha_map(path):
    rows = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line:
            digest, name = line.split(None, 1)
            rows[name] = digest
    return rows


KERNEL_INPUTS = load_sha_map(AUDIO / 'kernel-inputs.sha256')


def refuse(message):
    raise RuntimeError(message)


def resolve_patch(path):
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return ROOT / candidate


def require_image(image):
    if image != 'sha256:' + BUILDER_IMAGE:
        refuse('builder image must be sha256:' + BUILDER_IMAGE)


def require_pinned_inputs(archive, exports):
    if sha256_file(archive) != KERNEL_ARCHIVE:
        refuse('kernel archive does not match the pinned hash')
    if sha256_file(exports) != SOUND_EXPORTS:
        refuse('sound exports do not match the pinned hash')
    if set(HISTORICAL_PATCH_SHA256) != set(HISTORICAL_PATCHES):
        refuse('historical patch pin map does not match the patch list')
    for path in HISTORICAL_PATCHES:
        if sha256_file(resolve_patch(path)) != HISTORICAL_PATCH_SHA256[path]:
            refuse('historical patch hash mismatch: ' + str(path))
    retained = resolve_patch(RETAINED_PATCH)
    if sha256_file(retained) != RETAINED_PATCH_SHA256:
        refuse('retained patch hash mismatch: ' + str(RETAINED_PATCH))
    for name, pinned in load_sha_map(IMPORTED_MANIFEST).items():
        source = AUDIO / name
        if not source.is_file() or sha256_file(source) != pinned:
            refuse('imported source mismatch: ' + name)


def member_relative(name):
    text = str(name).replace('\\', '/')
    if text.startswith('/'):
        refuse('absolute archive member: ' + text)
    parts = [part for part in text.split('/') if part not in ('', '.')]
    if not parts or any(part == '..' for part in parts):
        refuse('archive member escapes the tree: ' + text)
    stripped = parts[1:]
    if any(part == '..' for part in stripped):
        refuse('archive member escapes the tree: ' + text)
    return Path(*stripped) if stripped else None


def plan_archive(archive):
    planned = []
    with tarfile.open(archive, 'r:gz') as packed:
        for member in packed.getmembers():
            if member.issym() or member.islnk():
                refuse('refusing archive link; symlinks and hard links are not extracted: ' + member.name)
            if not member.isdir() and not member.isfile():
                refuse('refusing special archive member: ' + member.name)
            planned.append((member.name, member.isdir(), member_relative(member.name)))
    return planned


def extract_archive(archive, kernel, planned):
    wanted = {name: (is_dir, relative) for name, is_dir, relative in planned}
    kernel.mkdir(parents=True)
    with tarfile.open(archive, 'r:gz') as packed:
        for member in packed.getmembers():
            is_dir, relative = wanted[member.name]
            if relative is None:
                continue
            target = kernel / relative
            if is_dir:
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = packed.extractfile(member)
            if source is None:
                refuse('archive member has no file body: ' + member.name)
            with source, open(target, 'wb') as handle:
                shutil.copyfileobj(source, handle)


def require_tree_pins(kernel):
    for name, pinned in KERNEL_INPUTS.items():
        path = kernel / name
        if not path.is_file() or sha256_file(path) != pinned:
            refuse('extracted kernel input mismatch: ' + name)
    driver = kernel / DOCKCHANNEL
    if sha256_file(driver) != DOCKCHANNEL_BASE:
        refuse('dockchannel base hash mismatch')
    release = (kernel / 'include/config/kernel.release').read_text().strip()
    if release != KERNEL_RELEASE:
        refuse('kernel release mismatch')


def apply_patch(kernel, patch):
    result = subprocess.run(
        ['patch', '-p1', '--fuzz=0', '--forward', '-i', str(patch)],
        cwd=kernel, check=False, text=True, capture_output=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or '').strip()
        refuse('patch failed: ' + str(patch) + (('\n' + detail) if detail else ''))


def require_historical_tree(kernel):
    if sha256_file(kernel / DOCKCHANNEL) != DOCKCHANNEL_HISTORICAL:
        refuse('dockchannel hash after historical patches does not match the pin')
    if sha256_file(kernel / MACSMC) != MACSMC_HISTORICAL:
        refuse('macsmc hash after historical patches does not match the pin')


def copy_audio_modules(output):
    source = AUDIO / 'kernel'
    destination = output / 'mca'
    shutil.copytree(source, destination)
    # The copied tree is already the widened MCA. Do not apply mca-bclk-width.patch.


def prepare(archive, exports, image, output):
    archive = Path(archive)
    exports = Path(exports)
    output = Path(output)
    require_image(image)
    require_pinned_inputs(archive, exports)
    if output.exists():
        refuse('output already exists: ' + str(output))
    planned = plan_archive(archive)
    output.mkdir()
    kernel = output / 'kernel'
    extract_archive(archive, kernel, planned)
    require_tree_pins(kernel)
    for path in HISTORICAL_PATCHES:
        apply_patch(kernel, resolve_patch(path))
    require_historical_tree(kernel)
    apply_patch(kernel, resolve_patch(RETAINED_PATCH))
    copy_audio_modules(output)


def docker_script():
    common = 'make -C /kernel ARCH=arm64 LLVM=1 LOCALVERSION= -j6'
    modules = (
        'make -C /kernel ARCH=arm64 LLVM=1 LOCALVERSION= KCFLAGS=-Werror '
        'J713_PROFILE=%s KBUILD_EXTRA_SYMBOLS=/reference-sound-exports.symvers '
        'M=/job -j6 modules' % PROFILE)
    return 'set -euo pipefail\n' + common + ' Image.gz\n' + modules + '\n'


def require_unchanged(kernel, exports, before_symvers, before_exports):
    after_symvers = sha256_file(kernel / 'Module.symvers')
    after_exports = sha256_file(exports)
    if after_symvers != before_symvers or after_symvers != KERNEL_INPUTS['Module.symvers']:
        refuse('Module.symvers changed during the kernel build; '
               'a regenerated export table does not replace the archived pin')
    if after_exports != before_exports or after_exports != SOUND_EXPORTS:
        refuse('supplemental sound exports changed during the build')
    for name, pinned in KERNEL_INPUTS.items():
        if sha256_file(kernel / name) != pinned:
            refuse('kernel input drifted: ' + name)
    release = (kernel / 'include/config/kernel.release').read_text().strip()
    if release != KERNEL_RELEASE:
        refuse('kernel release drifted')
    return after_symvers, after_exports


def publish(output, kernel, archive, exports, before_symvers, after_symvers, before_exports, after_exports):
    image_source = kernel / 'arch/arm64/boot/Image.gz'
    if not image_source.is_file():
        refuse('kernel Image.gz was not produced')
    published = output / 'Image.gz'
    shutil.copy2(image_source, published)
    modules = output / 'modules'
    modules.mkdir()
    module_rows = {}
    for name in AUDIO_MODULES:
        built = output / 'mca' / name
        if not built.is_file():
            refuse('audio module was not produced: ' + name)
        shutil.copy2(built, modules / name)
        copied = modules / name
        module_rows[name] = {'sha256': sha256_file(copied), 'bytes': copied.stat().st_size}
    receipt = {
        'schema': 1,
        'kind': 'j713-hardened-kernel-candidate',
        'native_qualified': False,
        'fresh_install_qualified': False,
        'image': 'sha256:' + BUILDER_IMAGE,
        'kernel_archive': sha256_file(archive),
        'sound_exports': sha256_file(exports),
        'kernel': {
            'path': 'Image.gz',
            'sha256': sha256_file(published),
            'bytes': published.stat().st_size,
        },
        'modules': module_rows,
        'exports': {
            'module_symvers_before': before_symvers,
            'module_symvers_after': after_symvers,
            'supplemental_before': before_exports,
            'supplemental_after': after_exports,
        },
        'kernel_release': KERNEL_RELEASE,
        'config_sha256': sha256_file(kernel / '.config'),
        'profile': PROFILE,
    }
    (output / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    return receipt


def build(archive, exports, image, output):
    prepare(archive, exports, image, output)
    output = Path(output)
    archive = Path(archive)
    exports = Path(exports)
    kernel = output / 'kernel'
    before_symvers = sha256_file(kernel / 'Module.symvers')
    before_exports = sha256_file(exports)
    command = [
        'docker', 'run', '--rm', '--network', 'none', '--pull', 'never',
        '--entrypoint', '/bin/bash',
    ]
    for key, value in KBUILD_ENV.items():
        command.extend(['-e', key + '=' + value])
    command.extend([
        '-v', str(kernel) + ':/kernel',
        '-v', str(exports) + ':/reference-sound-exports.symvers:ro',
        '-v', str(output / 'mca') + ':/job',
        'sha256:' + BUILDER_IMAGE,
        '-c', docker_script(),
    ])
    result = subprocess.run(command, check=False, text=True, capture_output=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or '').strip()
        refuse('kernel build failed' + ((': ' + detail) if detail else ''))
    after_symvers, after_exports = require_unchanged(kernel, exports, before_symvers, before_exports)
    return publish(output, kernel, archive, exports, before_symvers, after_symvers, before_exports, after_exports)


def main(argv):
    parser = argparse.ArgumentParser(description='Build a J713 hardened kernel candidate. Pins cannot be overridden.')
    parser.add_argument('--kernel-archive', required=True)
    parser.add_argument('--sound-exports', required=True)
    parser.add_argument('--image', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    try:
        build(args.kernel_archive, args.sound_exports, args.image, args.output)
    except RuntimeError as error:
        print('error: ' + str(error), file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main(sys.argv[1:])
