#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Assemble the alpha release from the retained build-2 files on a Linux build host."""
import argparse
import copy
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
import tarfile
import uuid
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from m4_audio_package import image_path

VERSION = '0.1.0-alpha.1'
BASE_SUMS = '95c8125fdd054ebcabbfc490e34ced0ee1b2e5293a3980aba383c54623fe121d'
BASE_ROOT = 'b2beef5d2d1c1370e6d6911a3ff6f1d4ddc1e7c992a444f90283491dd8440836'
BASE_AUDIO = 'ffa147b80173ede85be61b8b1a5431edcd8d605a740962da2dfa65ba189dcc6a'
BASE_GPU_UNIT = 'f4e62aa8a1a5782e1c27928e06f72e64ecfb630fee621a585db80837fb1c0671'
BASE_AUDIO_REGISTER = 'aa3f9f8b4c6c08ce3e088442168d5e80d9a59aa1b0e6d1449f6943829ae1a65d'
BASE_AUDIO_UNIT = 'bfa2e740cc4ff8aa03246f97bcbf85f92528b11fea22100ad8a3c312c7cb0ddf'
BASE_VIDEO = '8e1607e29174987e90f0f1cd0361786f894b239ad54fba08067ce1da54ec2331'
BASE_VIDEO_START = 'c613d8a3b48021204538fc0dd766a523b2ddb4c0ed4d2a26bb21259ba285c85d'
POWEROFF_DTB = '67dc61458b03c0fad1156994ad85a85f9f8612cac2996eb684609b0455ebf547'
FIRS = ('j713-static-minimal-lane0-48000.wav', 'j713-static-minimal-lane1-48000.wav',
        'j713-static-minimal-lane2-48000.wav', 'j713-static-minimal-lane3-48000.wav')
PROFILES = ('Brave-Browser', 'Brave-Browser-M4', 'Chromium')


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def document(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def command(*args):
    subprocess.run(list(map(str, args)), check=True)


def source_revision(tree):
    status = subprocess.check_output(
        ['git', '-C', str(tree), 'status', '--porcelain', '--untracked-files=no'], text=True)
    require(not status, 'Release source has tracked changes; commit before assembly')
    return subprocess.check_output(
        ['git', '-C', str(tree), 'rev-parse', '--verify', 'HEAD'], text=True).strip()


def patch_main(data):
    text = data.decode('utf-8')
    changes = {
        'import system, osenum, stub, diskutil, osinstall, gravity_firmware, m1n1\n':
        'import system, osenum, stub, diskutil, osinstall, gravity_firmware, m1n1\nimport j713_owner_assets\n',
        '    def __init__(self, version):\n':
        '    def __init__(self, version):\n        self.owner_assets = j713_owner_assets.prepare_for_installer()\n',
        '        self.ins.collect_firmware(fw_pkg)\n':
        '        self.ins.collect_firmware(fw_pkg)\n        j713_owner_assets.add_to_firmware(fw_pkg, self.owner_assets)\n',
        '            self.ins.collect_firmware(pkg)\n':
        '            self.ins.collect_firmware(pkg)\n            j713_owner_assets.add_to_firmware(pkg, self.owner_assets)\n',
    }
    for old, new in changes.items():
        require(text.count(old) == 1, 'Installer source seam changed: ' + old.strip())
        text = text.replace(old, new)
    return text.encode('utf-8')


def repack_installer(source, destination, module, source_archive=False):
    entry = 'src/main.py' if source_archive else 'main.py'
    addition = 'src/j713_owner_assets.py' if source_archive else 'j713_owner_assets.py'
    replaced = 0
    names = set()
    with tarfile.open(source, 'r:gz') as old, tarfile.open(destination, 'x:gz') as new:
        for item in old:
            name = item.name.removeprefix('./')
            require(name not in names, 'Duplicate installer member')
            names.add(name)
            require(name != addition, 'Installer module already exists')
            if name == entry:
                require(item.isfile(), 'Installer entry is not regular')
                data = patch_main(old.extractfile(item).read())
                item = copy.copy(item)
                item.size = len(data)
                new.addfile(item, io.BytesIO(data))
                replaced += 1
            else:
                new.addfile(item, old.extractfile(item) if item.isfile() else None)
        require(replaced == 1, 'Missing installer entry point')
        item = tarfile.TarInfo(addition)
        item.mode, item.size = 0o644, len(module)
        new.addfile(item, io.BytesIO(module))


def clean_integration(source, destination):
    excluded = {'kit/packages/audio/root/usr/share/asahi-audio/j713/' + name for name in FIRS}
    excluded.update('kit/packages/video/root/etc/skel/.config/omarchy/pending/widevine-' + p + '.json'
                    for p in PROFILES)
    removed = set()
    with tarfile.open(source, 'r:gz') as old, tarfile.open(destination, 'x:gz') as new:
        for item in old:
            name = item.name.removeprefix('./')
            if name in excluded:
                require(item.isfile(), 'Unexpected excluded member type')
                removed.add(name)
            else:
                new.addfile(item, old.extractfile(item) if item.isfile() else None)
    require(removed == excluded, 'Private integration input set changed')


def fill_free_space(root):
    # An actual zero write replaces freed blocks. Sparse allocation would not.
    path = root / '.release-free-space'
    require(not path.exists(), 'Free-space filler already exists')
    try:
        with path.open('xb', buffering=0) as stream:
            block = bytes(4 * 1024 * 1024)
            try:
                while True:
                    stream.write(block)
            except OSError as error:
                require(error.errno == errno.ENOSPC, 'Unexpected free-space write error: ' + str(error))
            try:
                os.fsync(stream.fileno())
            except OSError as error:
                require(error.errno == errno.ENOSPC, 'Unexpected free-space flush error: ' + str(error))
    finally:
        path.unlink(missing_ok=True)
    os.sync()


def owner_audio_manifest(raw, previous_pin, profile):
    """Replace only the four measured FIR identities in the retained manifest."""
    require(hashlib.sha256(raw).hexdigest() == previous_pin,
            'Unexpected retained audio manifest')
    require(set(profile['outputs']) == set(FIRS) == set(profile['reference_outputs']),
            'Unexpected owner filter set')
    manifest = json.loads(raw)
    require(manifest['schema'] == 1 and manifest['board'] == 'apple,j713',
            'Unexpected retained audio manifest board/schema')
    for name in FIRS:
        previous, native = profile['reference_outputs'][name], profile['outputs'][name]
        key = 'usr/share/asahi-audio/j713/' + name
        require(manifest['files']['/' + key] == previous['sha256'] and
                manifest['file_metadata'][key] == {**previous, 'mode': 0o644},
                'Unexpected reference filter: ' + name)
        require(set(native) == {'size', 'sha256'} and
                type(native['size']) is int and native['size'] == previous['size'] and
                isinstance(native['sha256'], str) and
                re.fullmatch('[0-9a-f]{64}', native['sha256']),
                'Invalid native filter identity: ' + name)
        manifest['files']['/' + key] = native['sha256']
        manifest['file_metadata'][key]['sha256'] = native['sha256']
    return (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode()


def prepare_startup(root, tree, raw):
    """Carry startup repairs into the image and both runtime integrity maps."""
    gpu = image_path(root, 'etc/systemd/system/m4-gpu.service')
    require(gpu.is_file() and sha(gpu) == BASE_GPU_UNIT, 'Unknown retained graphics unit')
    manifest = json.loads(raw)
    replacements = {}
    for name, pin, source in (
            ('usr/lib/omarchy/m4-j713-audio-register.py', BASE_AUDIO_REGISTER,
             'tools/m4-j713-audio-register.py'),
            ('usr/lib/systemd/system/m4-j713-audio-register.service', BASE_AUDIO_UNIT,
             'packaging/j713-audio/m4-j713-audio-register.service')):
        path = image_path(root, name)
        require(path.is_file() and sha(path) == pin, 'Unknown retained audio registration file: ' + name)
        previous = dict(sha256=pin, size=path.stat().st_size, mode=0o644)
        require(manifest['files']['/' + name] == pin and
                manifest['file_metadata'][name] == previous and
                path.stat().st_mode & 0o777 == 0o644,
                'Retained audio registration identity maps disagree: ' + name)
        replacements[name] = image_path(tree, source).read_bytes()
    gpu_replacement = image_path(tree, 'packaging/j713-startup/m4-gpu.service').read_bytes()
    configure = runpy.run_path(str(tree / 'tools/configure-j713-first-boot.py'))['apply']
    configure(root, tree)
    owner_setup = runpy.run_path(str(tree / 'tools/configure-j713-owner-setup.py'))['configure']
    owner_setup(root, apply=True)
    gpu.write_bytes(gpu_replacement)
    gpu.chmod(0o644)
    for name, replacement in replacements.items():
        path = image_path(root, name)
        path.write_bytes(replacement)
        path.chmod(0o644)
        pin = hashlib.sha256(replacement).hexdigest()
        manifest['files']['/' + name] = pin
        manifest['file_metadata'][name] = dict(sha256=pin, size=len(replacement), mode=0o644)
    return (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode()


def prepare_decoder(root, tree):
    """Replace the retained decoder wait and carry its exact identity forward."""
    name = 'usr/local/bin/m4-video-start'
    path = image_path(root, name)
    metadata = image_path(root, 'etc/omarchy/j713-video-manifest.json')
    require(metadata.is_file() and sha(metadata) == BASE_VIDEO, 'Unknown retained video manifest')
    require(path.is_file() and sha(path) == BASE_VIDEO_START and
            path.stat().st_mode & 0o777 == 0o755, 'Unknown retained decoder startup helper')
    manifest = json.loads(metadata.read_bytes())
    require(manifest['files'][name] == dict(sha256=BASE_VIDEO_START, size=path.stat().st_size, mode=0o755),
            'Retained decoder identity map disagrees')
    replacement = image_path(tree, 'packaging/j713-startup/m4-video-start').read_bytes()
    path.write_bytes(replacement)
    path.chmod(0o755)
    manifest['files'][name] = dict(sha256=hashlib.sha256(replacement).hexdigest(),
                                 size=len(replacement), mode=0o755)
    document(metadata, manifest)
    return sha(metadata)


def prepare_root(source, image, root, tree, identity):
    require(sha(source) == BASE_ROOT, 'Retained root image changed')
    command('cp', '--reflink=auto', '--sparse=always', source, image)
    root.mkdir()
    mounted = False
    try:
        command('mount', '-o', 'loop,nodev,nosuid', image, root)
        mounted = True
        completion = root / 'etc/m4-install-complete'
        require(not completion.exists() and not completion.is_symlink(),
                'Retained root must not have completed first boot')
        manifest = json.loads((tree / 'audio/j713/owner-fir/manifest.json').read_text())
        audio_manifest = root / 'etc/omarchy/j713-audio-manifest.json'
        native_audio = owner_audio_manifest(audio_manifest.read_bytes(), BASE_AUDIO, manifest)
        native_audio = prepare_startup(root, tree, native_audio)
        video_pin = prepare_decoder(root, tree)
        for name in FIRS:
            path = root / 'usr/share/asahi-audio/j713' / name
            require(path.is_file() and not path.is_symlink() and
                    sha(path) == manifest['reference_outputs'][name]['sha256'], 'Unexpected original FIR')
            with path.open('r+b') as stream:
                stream.write(bytes(path.stat().st_size)); stream.flush(); os.fsync(stream.fileno())
            path.unlink()
        audio_manifest.write_bytes(native_audio)
        for profile in PROFILES:
            path = root / ('etc/skel/.config/omarchy/pending/widevine-' + profile + '.json')
            require(json.loads(path.read_bytes()) == {'schema': 1, 'profile': profile,
                                                     'consent': 'owner-requested'}, 'Unexpected preset choice')
            with path.open('r+b') as stream:
                stream.write(bytes(path.stat().st_size)); stream.flush(); os.fsync(stream.fileno())
            path.unlink()
        additions = {
            'usr/lib/omarchy/m4-j713-owner-assets.py': 'tools/m4-j713-owner-assets.py',
            'usr/lib/systemd/system/j713-owner-assets.service': 'packaging/j713-owner-assets.service',
            'usr/lib/systemd/system/m4-j713-audio-register.service.d/10-owner-assets.conf':
                'packaging/j713-owner-assets-audio.conf',
            'usr/lib/systemd/system/systemd-user-sessions.service.d/10-owner-assets.conf':
                'packaging/j713-owner-assets-session.conf',
            'usr/lib/systemd/system/omarchy-provision-owner.service.d/10-owner-assets.conf':
                'packaging/j713-owner-assets-audio.conf',
        }
        for target, source_name in additions.items():
            path = root / target
            require(not path.exists() and not path.is_symlink(), 'Root addition already present')
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(tree / source_name, path)
            path.chmod(0o644)
        link = root / 'etc/systemd/system/multi-user.target.wants/j713-owner-assets.service'
        require(not link.exists() and not link.is_symlink(), 'Owner service already enabled')
        link.symlink_to('/usr/lib/systemd/system/j713-owner-assets.service')
        (root / 'etc/m4-root-id').write_text(identity + '\n')
        fill_free_space(root)
        command('umount', root)
        mounted = False
    finally:
        if mounted:
            command('umount', root)
    command('tune2fs', '-U', identity, image)
    # The installer requires a clean ext4 image checked after its last write mount.
    command('e2fsck', '-f', '-p', image)
    return {'audio': hashlib.sha256(native_audio).hexdigest(), 'video': video_pin}


def assemble(args):
    require(sys.version_info >= (3, 12), 'Build requires Python 3.12 or newer')
    require(os.geteuid() == 0, 'Build on a Linux host through its scoped root job')
    tree, baseline = args.source_tree.resolve(), args.baseline.resolve()
    revision = source_revision(tree)
    require(args.output.is_absolute() and not args.output.exists() and
            not args.output.is_symlink(), 'A new absolute build output is required')
    require(shutil.disk_usage(args.output.parent).free > 40 * 1024**3,
            'Need 40 GiB free on the build host')
    require(sha(baseline / 'SHA256SUMS') == BASE_SUMS, 'Unknown retained delivery')
    for line in (baseline / 'SHA256SUMS').read_text().splitlines():
        pin, name = line.split('  ', 1)
        require(sha(baseline / name) == pin, 'Retained delivery mismatch: ' + name)
    require(sha(args.owner_bundle) == '4b59c1549e821d79ca4043ade37c02cdbbcc38e5a8eb5202f285b8eb2ff97e07',
            'Unknown owner helper bundle')
    require(sha(args.numpy_source) == 'df2d5874ff183595a4ba404edd04f6bd9b5505c1d7708573f6a6c17489a67563',
            'Unknown numerical dependency source')
    require(sha(args.cargo_source) == 'faf50f21db4b622900bd275441bc321d1a01933afb555e84d1446d38d6027a34',
            'Unknown locked dependency sources')
    require(sha(args.sound_exports) == '9f3e2cf8c83045e3a89b94ef7c8526c4dfb10b386d872669819cd8f4f35d4362',
            'Unknown supplemental sound exports')
    require(sha(args.reconstruction) == '47c144a606c13f218b1bd84d8a1b86d6d9ac281a58a04d0c191e0508221b1e7c',
            'Unknown source reconstruction guidance')
    require(args.poweroff_dtb.is_file() and not args.poweroff_dtb.is_symlink() and
            sha(args.poweroff_dtb) == POWEROFF_DTB, 'Unknown SMC poweroff device tree')
    owner_profile = json.loads((tree / 'audio/j713/owner-fir/manifest.json').read_bytes())
    handoff = runpy.run_path(str(tree / 'tools/m4-j713-owner-assets.py'))['FIRS']
    require(handoff == {name: (record['size'], record['sha256'])
                        for name, record in owner_profile['outputs'].items()},
            'Owner generation and installer handoff identities disagree')
    args.output.mkdir(mode=0o700)
    output = args.output / 'delivery'
    output.mkdir()
    image = args.output / 'root.ext4'
    identity = str(uuid.uuid4())
    manifest_pins = prepare_root(args.root_image.resolve(), image, args.output / 'mounted-root', tree, identity)
    config = json.loads((baseline / 'installer_data.json').read_bytes())
    old_image = json.loads((baseline / 'IMAGE.json').read_bytes())
    require(old_image['audio_manifest_sha256'] == BASE_AUDIO, 'Baseline audio identity changed')
    require(old_image['video_manifest_sha256'] == BASE_VIDEO, 'Baseline video identity changed')
    require(config['os_list'][0]['m4_boot'] == old_image['boot'], 'Baseline boot pins disagree')
    boot = copy.deepcopy(old_image['boot'])
    boot['dtb']['sha256'] = POWEROFF_DTB
    old_package = config['os_list'][0]['package']
    new_package = 'omarchy-j713-' + VERSION + '.zip'
    skipped = {'SHA256SUMS', 'KIT.json', 'IMAGE.json', 'installer_data.json', 'Install.command',
               'START-HERE.md', 'KNOWN-LIMITATIONS.md', 'fir-origin.json', 'installer-macos.tar.gz',
               'source/integration-inputs.tar.gz', 'source/installer-source.tar.gz',
               'source/README.md', 'os/' + old_package}
    for source in sorted(baseline.rglob('*')):
        if source.is_dir():
            continue
        name = source.relative_to(baseline).as_posix()
        require(not source.is_symlink(), 'Unexpected delivery link')
        if name not in skipped:
            target = output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            target.chmod(0o644)
    (output / 'os').mkdir(exist_ok=True)
    with zipfile.ZipFile(baseline / 'os' / old_package) as old, zipfile.ZipFile(
            output / 'os' / new_package, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as new:
        require(set(old.namelist()) == {'root.ext4', *(p['file'] for k, p in old_image['boot'].items()
                                                     if k != 'bootargs')}, 'Unexpected boot archive')
        new.write(image, 'root.ext4')
        for kind, entry in old_image['boot'].items():
            if kind == 'bootargs':
                continue
            data = old.read(entry['file'])
            require(hashlib.sha256(data).hexdigest() == entry['sha256'], 'Boot payload changed')
            if kind == 'dtb':
                data = args.poweroff_dtb.read_bytes()
            require(hashlib.sha256(data).hexdigest() == boot[kind]['sha256'],
                    'Release boot payload pin mismatch')
            new.writestr(entry['file'], data)
    module = (tree / 'tools/m4-j713-owner-assets.py').read_bytes()
    repack_installer(baseline / 'installer-macos.tar.gz', output / 'installer-macos.tar.gz', module)
    repack_installer(baseline / 'source/installer-source.tar.gz',
                     output / 'source/installer-source.tar.gz', module, True)
    clean_integration(baseline / 'source/integration-inputs.tar.gz', output / 'source/integration-inputs.tar.gz')
    owner = output / 'owner-audio'
    owner.mkdir()
    with tarfile.open(args.owner_bundle) as archive:
        archive.extractall(owner, filter='data')
    (owner / 'owner-fir').rename(owner / 'bundle')
    # Checkout formatting metadata stays in the source archive, not the runtime bundle.
    attributes = owner / 'bundle/.gitattributes'
    require(attributes.is_file() and not attributes.is_symlink(), 'Owner bundle metadata changed')
    attributes.unlink()
    # Retain the pinned native helpers/dependency, but carry this revision's
    # output identities and documentation into the runtime bundle as well.
    for name in ('manifest.json', 'README.md'):
        shutil.copyfile(tree / 'audio/j713/owner-fir' / name, owner / 'bundle' / name)
    shutil.copyfile(tree / 'tools/m4-j713-fir-owner.py', owner / 'm4-j713-fir-owner.py')
    shutil.copyfile(args.numpy_source, output / 'source/numpy-2.5.3.tar.gz')
    shutil.copyfile(args.cargo_source, output / 'source/j713-audio-cargo-sources.tar.gz')
    shutil.copyfile(args.sound_exports, output / 'source/reference-sound-exports.symvers')
    shutil.copyfile(args.reconstruction, output / 'source/j713-audio-reconstruction.tar')
    for source_name, destination in (
            ('packaging/Install.command', 'Install.command'),
            ('docs/install.md', 'START-HERE.md'),
            ('docs/release-limitations.md', 'KNOWN-LIMITATIONS.md'),
            ('docs/updates.md', 'UPDATES.md'),
            ('docs/j713-poweroff.md', 'source/j713-poweroff.md'),
            ('docs/release-source.md', 'source/README.md')):
        shutil.copyfile(tree / source_name, output / destination)
    # The current repository sources are a separate exact archive; private build
    # logs, machine credentials and workflow instructions never enter this archive.
    with tarfile.open(output / 'source/release-integration.tar.gz', 'x:gz') as archive:
        tracked = subprocess.check_output(['git', '-C', str(tree), 'ls-files', '-z']).split(b'\0')
        for raw in tracked:
            if not raw:
                continue
            name = raw.decode()
            if Path(name).name in ('AGENTS.md', 'CLAUDE.md'):
                continue
            archive.add(tree / name, arcname=name, recursive=False)
    config['os_list'][0]['package'] = new_package
    config['os_list'][0]['name'] = 'Omarchy M4 Native ' + VERSION + ' (experimental J713)'
    config['os_list'][0]['m4_boot'] = boot
    roots = [p for p in config['os_list'][0]['partitions'] if 'rootfs_uuid' in p]
    require(len(roots) == 1, 'Unexpected root layout')
    roots[0].update(rootfs_uuid=identity, size=str(image.stat().st_size) + 'B')
    document(output / 'installer_data.json', config)
    require(source_revision(tree) == revision, 'Release source revision changed during assembly')
    metadata = {
        'release_version': VERSION, 'private_only': False, 'source_revision': revision,
        'credit': 'studioxradio aka kb2uka', 'base_omarchy_version': '4.0.4',
        'boot': boot,
        'root': {'bytes': image.stat().st_size, 'sha256': sha(image), 'filesystem_uuid': identity},
        'audio_manifest_sha256': manifest_pins['audio'],
        'video_manifest_sha256': manifest_pins['video'],
        'fresh_install_qualified': False,
        'qualification': 'The preceding owner-input candidate failed fresh startup. This image includes startup and recovery corrections; a successful owner installation is still required before publication.',
        'owner_inputs': 'Speaker filters generated from installed macOS and hash checked before partitioning; Widevine requires each owner choice.',
        'baseline_root_sha256': BASE_ROOT,
        'poweroff': {'change': 'Enable existing SMC handler through its missing device-tree child',
                    'hardware_verified': False, 'issue': 24},
    }
    document(output / 'IMAGE.json', metadata)
    files = {}
    for path in sorted(output.rglob('*')):
        if path.is_file():
            require(not path.is_symlink(), 'Unexpected final delivery link')
            path.chmod(0o755 if path.name == 'Install.command' else 0o644)
            files[path.relative_to(output).as_posix()] = {'bytes': path.stat().st_size, 'sha256': sha(path)}
    kit = {key: metadata[key] for key in ('release_version', 'private_only', 'source_revision',
                                        'base_omarchy_version', 'credit', 'fresh_install_qualified', 'qualification')}
    kit['files'] = files
    document(output / 'KIT.json', kit)
    pins = {name: record['sha256'] for name, record in files.items()}
    pins['KIT.json'] = sha(output / 'KIT.json')
    (output / 'SHA256SUMS').write_text(''.join(pin + '  ' + name + '\n' for name, pin in sorted(pins.items())))
    document(args.output / 'assembly.json', {'kit_sha256': sha(output / 'KIT.json'),
                                           'checksums_sha256': sha(output / 'SHA256SUMS'),
                                           'source_revision': revision, 'root_uuid': identity})
    print('Release delivery assembled at ' + str(output), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source-tree', 'baseline', 'root-image', 'owner-bundle', 'numpy-source', 'cargo-source',
                 'sound-exports', 'reconstruction', 'poweroff-dtb', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    assemble(parser.parse_args())


if __name__ == '__main__':
    main()
