# SPDX-License-Identifier: MIT
"""Contract for tools/build-j713-hardened.py.

The tool builds a candidate kernel and the current audio/j713/kernel modules.
It does not qualify a native install. Import with importlib, as the other
release helpers do. Expected production API:

    KERNEL_ARCHIVE = '810339d85aa3171fb1a982b71d01c0bf4de816f81ebbe9149d0dd3268e98ce57'
    SOUND_EXPORTS = '9f3e2cf8c83045e3a89b94ef7c8526c4dfb10b386d872669819cd8f4f35d4362'
    BUILDER_IMAGE = 'b2069edb8a23761d9211559fe632f5464aebc4cdfa9558d0f4c93211890fd7b1'
    KERNEL_RELEASE = '7.1.9-m4-gpu-backport-20261008'
    KERNEL_INPUTS = {relative path: sha256}  # the six audio/j713/kernel-inputs.sha256 rows
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
    HISTORICAL_PATCH_SHA256 = {those paths: published sha256}
    RETAINED_PATCH = 'patches-kernel/retained-hardening/dockchannel-packet-bounds.patch'
    RETAINED_PATCH_SHA256 = sha256 of that file
    MODULE_CERTIFICATE = 'patches-kernel/retained-hardening/qualified-module-certificate.pem'
    MODULE_CERTIFICATE_SHA256 = sha256 of that public certificate
    MODULE_CERTIFICATE_DER_SHA256 = sha256 of its 1324-byte DER form
    IMPORTED_MANIFEST = ROOT / 'audio/j713/imported-source.sha256'
    KBUILD_ENV = {
        'KBUILD_BUILD_TIMESTAMP': '2026-10-20',
        'KBUILD_BUILD_USER': 'KB2UKA',
        'KBUILD_BUILD_HOST': 'm4-build',
        'KBUILD_BUILD_VERSION': '19',
    }
    AUDIO_MODULES = (
        'snd-soc-j713-group.ko', 'snd-soc-tas2764.ko', 'snd-soc-macaudio.ko',
        'snd-soc-apple-mca.ko', 'apple-admac.ko',
    )

    prepare(archive, exports, image, output) -> None
    build(archive, exports, image, output) -> receipt dict
    main(argv) -> None

There is no CLI flag that replaces a pin. main() accepts only:

    --kernel-archive ARCHIVE --sound-exports FILE --image sha256:HEX --output NEW_DIRECTORY

prepare() and build() accept the image only as 'sha256:' plus BUILDER_IMAGE.
Relative patch paths are resolved from the repo root; tests may substitute
absolute paths. An absolute archive member, a path that escapes the tree
after stripping one leading component, a hard link, an absolute symlink, or a
symlink whose target resolves outside the extracted kernel root is refused.
A relative symlink that stays inside that root is extracted as a symlink,
including a ../../../ target such as arch/arm64/tools/syscall_64.tbl. Regular
files keep their archive mode (0755 stays executable, 0644 does not). Special
members are refused.

Order, and nothing is written to output before the input checks:

1. Refuse an image that is not the pinned sha256 identity.
2. Hash the archive and supplemental exports, and hash the historical and
   retained patch files. Verify every imported-source.sha256 row against
   audio/j713. Require MODULE_CERTIFICATE to be a regular file, not a
   symlink, whose sha256 is MODULE_CERTIFICATE_SHA256. The certificate is
   public; it contains no private key and the build does not generate one.
   Refuse an output path that already exists. Preflight the archive, then
   probe output's parent: a temporary directory there gets a file named
   case-probe and the build stops when CASE-PROBE is the same file.
   Remove that probe before creating output. Successful prepare and build
   tests require a case-sensitive TMPDIR, the same rule production enforces
   on the destination parent. Case-insensitive APFS refuses extraction. A
   case-sensitive APFS volume is a suitable TMPDIR and runs those tests in
   the ordinary way.
3. Extract the gzip archive into output/kernel with one leading component
   removed. Verify KERNEL_INPUTS and DOCKCHANNEL_BASE.
4. Apply HISTORICAL_PATCHES with patch -p1 --fuzz=0 --forward, in order,
   then require DOCKCHANNEL_HISTORICAL and MACSMC_HISTORICAL.
5. Apply RETAINED_PATCH once, to that copied tree only.
6. Copy only the kernel/ rows of IMPORTED_MANIFEST into output/mca.
   Each source must be a regular non-symlink file whose bytes still match
   the manifest. The read opens with O_NONBLOCK so a FIFO that replaces a
   listed file after the precheck cannot block; fstat then refuses it.
   Unlisted working-tree files, including Kbuild, stale
   .ko/.o/.cmd, and unlisted .c, are not copied. Do not apply the bclk
   patch. Copy the pinned certificate to
   output/kernel/certs/qualified-module-certificate.pem.

build() calls prepare(), then one docker run. Host sides of -v are absolute
paths to the prepared kernel tree, the supplemental exports file, and
output/mca, including when archive, exports, and output were relative:

    docker run --rm --network none --pull never --entrypoint /bin/bash
      -e KBUILD_BUILD_*=...
      -v /ABS/output/kernel:/kernel
      -v /ABS/EXPORTS:/reference-sound-exports.symvers:ro
      -v /ABS/output/mca:/job
      sha256:BUILDER_IMAGE -c SCRIPT

SCRIPT starts with set -euo pipefail, then runs make Image.gz, then
make modules for M=/job. The first make exiting non-zero stops the script;
the module make does not run. Both invocations use
ARCH=arm64 LLVM=1 LOCALVERSION= and
CONFIG_MODULE_SIG_KEY=certs/qualified-module-certificate.pem. That
non-default key stops the kernel from generating a certificate. The module
make also passes KCFLAGS=-Werror, J713_PROFILE=conservative-v1, and
KBUILD_EXTRA_SYMBOLS=/reference-sound-exports.symvers. No daemon, Cargo,
or speakersafetyd build is invoked. The archived .config, including any
signature-verification settings, is left unchanged.

After docker, Module.symvers, vmlinux.symvers, .config, and
include/config/kernel.release must still match the archived inputs, and the
supplemental file must still match SOUND_EXPORTS. The real archive hashes
both symvers files as 5e3c2624c6a991bae503336137ce15071e51ef043edcd20ba1ad19df4e23d67c.
vmlinux.symvers is compared with that archived export table even when
Module.symvers is untouched. The real archive contains the file. A tree
without vmlinux.symvers is refused after prepare and before docker, and
does not write a receipt. Drift raises and does not write a receipt.
Success copies
arch/arm64/boot/Image.gz to output/Image.gz and each module to
output/modules/<name>.ko, and writes output/receipt.json. Omitting any one
of AUDIO_MODULES after a successful docker run, with Image.gz and the other
modules present, refuses that module and writes no receipt. The receipt is
also build()'s return value:

    schema 1, kind 'j713-hardened-kernel-candidate',
    native_qualified false, fresh_install_qualified false,
    image, kernel_archive, sound_exports,
    kernel {path, sha256, bytes}, modules {name: {sha256, bytes}},
    exports {module_symvers_before, module_symvers_after,
             vmlinux_symvers_before, vmlinux_symvers_after,
             supplemental_before, supplemental_after},
    kernel_release, config_sha256, profile 'conservative-v1',
    module_certificate_sha256 = MODULE_CERTIFICATE_SHA256,
    module_certificate_der_sha256 = MODULE_CERTIFICATE_DER_SHA256,
    make_overrides['CONFIG_MODULE_SIG_KEY'] =
        'certs/qualified-module-certificate.pem'

require_unchanged() refuses publication when the copied certificate is no
longer that pin, or when kernel/certs/signing_key.x509 is missing, a symlink,
or not the 1324-byte DER of that PEM (MODULE_CERTIFICATE_DER_SHA256). A docker
mutation of either file writes no receipt. The mock writes that DER with
ssl.PEM_cert_to_DER_cert before a mutate hook runs.

Patch and docker go through this module's subprocess.run.
"""
import errno
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import ssl
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'j713_hardened_build', ROOT / 'tools/build-j713-hardened.py')
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)

PUBLISHED_ARCHIVE = '810339d85aa3171fb1a982b71d01c0bf4de816f81ebbe9149d0dd3268e98ce57'
PUBLISHED_EXPORTS = '9f3e2cf8c83045e3a89b94ef7c8526c4dfb10b386d872669819cd8f4f35d4362'
PUBLISHED_IMAGE = 'b2069edb8a23761d9211559fe632f5464aebc4cdfa9558d0f4c93211890fd7b1'
PUBLISHED_PATCHES = {
    'patches-kernel/j713-input/dockchannel-start-retry.patch':
        'f3b1cacd0f1704e60edbadba714ac052959356446e5e059ad345fddc1d501967',
    'patches-kernel/j713-input/macsmc-key-info-lock.patch':
        '47a09e2b804b156c8ee5e26a025980caca7a10c4f0c0ef0bcdd8baf8b3b912f4',
    'patches-kernel/j713-input/dockchannel-afe-action.patch':
        '8f9ab49462a5076b7345aa465cef99696f29cbf4d23ab5bfa629acfebee8ed89',
}
PINNED_DRIVER = ROOT / 'tests/fixtures/dockchannel/dockchannel-hid.c'
PINNED_DRIVER_SHA256 = 'e4eebbee612eee32bd51ce370d412afc8cf62a9a7e087a3797c2c29306cedfb4'
RETAINED_PATCH = ROOT / 'patches-kernel/retained-hardening/dockchannel-packet-bounds.patch'
KERNEL_RELEASE = '7.1.9-m4-gpu-backport-20261008'
MODULE_CERTIFICATE = 'patches-kernel/retained-hardening/qualified-module-certificate.pem'
MODULE_CERTIFICATE_SHA256 = 'db92215adb9de23e9bba226ca17332b27c3bff5981346163cc68a4239fcd40b5'
MODULE_CERTIFICATE_DER_SHA256 = '4b61a05d46fbbdd907dc03ea9b96ebdad0bec4dc11b3a7cf874243efeb1ae66a'
MODULE_SIG_KEY = 'certs/qualified-module-certificate.pem'
SIGNING_KEY_DER_BYTES = 1324
EXPECTED_ENV = {
    'KBUILD_BUILD_TIMESTAMP': '2026-10-20',
    'KBUILD_BUILD_USER': 'KB2UKA',
    'KBUILD_BUILD_HOST': 'm4-build',
    'KBUILD_BUILD_VERSION': '19',
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def text_digest(data):
    return hashlib.sha256(data).hexdigest()


class HardenedBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo_mca = digest(ROOT / 'audio/j713/kernel/mca.c')
        self.repo_retained = digest(RETAINED_PATCH)
        self.repo_driver = digest(PINNED_DRIVER)

    def write_archive(self, files, links=(), modes=None, trailing=()):
        """files maps paths below the stripped top-level directory to bytes.

        links is (member path, link target) for relative symlinks. modes maps
        a regular member path to its permission bits. trailing is extra
        (path, bytes) regular members written after links, so a path can repeat.
        """
        modes = modes or {}
        archive = self.base / ('archive-%d.tar.gz' % len(list(self.base.iterdir())))
        with tarfile.open(archive, 'w:gz') as packed:
            for name, data in files.items():
                info = tarfile.TarInfo('linux/' + name)
                info.size = len(data)
                info.mode = modes.get(name, 0o644)
                info.type = tarfile.REGTYPE
                packed.addfile(info, io.BytesIO(data))
            for name, target in links:
                info = tarfile.TarInfo('linux/' + name)
                info.type = tarfile.SYMTYPE
                info.linkname = target
                packed.addfile(info)
            for name, data in trailing:
                info = tarfile.TarInfo('linux/' + name)
                info.size = len(data)
                info.mode = modes.get(name, 0o644)
                info.type = tarfile.REGTYPE
                packed.addfile(info, io.BytesIO(data))
        return archive

    def kernel_files(self, dockchannel, macsmc=b'smc\n'):
        release = (KERNEL_RELEASE + '\n').encode()
        files = {
            '.config': b'config\n',
            'Module.symvers': b'exports\n',
            'vmlinux.symvers': b'exports\n',
            'include/generated/autoconf.h': b'autoconf\n',
            'include/config/auto.conf': b'auto\n',
            'include/generated/utsrelease.h': b'uts\n',
            'include/config/kernel.release': release,
            tool.DOCKCHANNEL: dockchannel,
            tool.MACSMC: macsmc,
        }
        return files

    def pins_for(self, files, archive, exports, after_historical=None):
        """after_historical is (dockchannel bytes, macsmc bytes) once the historical patches have applied.

        The archive pins stay the extracted bytes. Callers that apply the synthetic
        historical series must pass the post-patch bytes, because prepare requires
        those hashes after the series and before the retained patch.
        """
        dock_after, mac_after = after_historical or (files[tool.DOCKCHANNEL], files[tool.MACSMC])
        inputs = {name: text_digest(files[name]) for name in tool.KERNEL_INPUTS}
        return {
            'KERNEL_ARCHIVE': digest(archive),
            'SOUND_EXPORTS': digest(exports),
            'KERNEL_INPUTS': inputs,
            'DOCKCHANNEL_BASE': text_digest(files[tool.DOCKCHANNEL]),
            'DOCKCHANNEL_HISTORICAL': text_digest(dock_after),
            'MACSMC_HISTORICAL': text_digest(mac_after),
        }

    def patch_file(self, name, body):
        path = self.base / name
        path.write_text(body)
        return path

    def historical_patches(self):
        first = self.patch_file('hist1.patch', """--- a/drivers/hid/dockchannel-hid/dockchannel-hid.c
+++ b/drivers/hid/dockchannel-hid/dockchannel-hid.c
@@ -1 +1,2 @@
 alpha
+beta
""")
        second = self.patch_file('hist2.patch', """--- a/drivers/mfd/macsmc.c
+++ b/drivers/mfd/macsmc.c
@@ -1 +1,2 @@
 smc
+locked
""")
        third = self.patch_file('hist3.patch', """--- a/drivers/hid/dockchannel-hid/dockchannel-hid.c
+++ b/drivers/hid/dockchannel-hid/dockchannel-hid.c
@@ -1,2 +1,3 @@
 alpha
 beta
+gamma
""")
        retained = self.patch_file('bounds.patch', """--- a/drivers/hid/dockchannel-hid/dockchannel-hid.c
+++ b/drivers/hid/dockchannel-hid/dockchannel-hid.c
@@ -1,3 +1,4 @@
 alpha
 beta
 gamma
+bounds
""")
        paths = (str(first), str(second), str(third))
        return paths, {path: digest(Path(path)) for path in paths}, retained

    def apply_pins(self, overrides, historical=None, retained=None):
        historical = historical or ((), {})
        stack = [
            mock.patch.object(tool, 'KERNEL_ARCHIVE', overrides['KERNEL_ARCHIVE']),
            mock.patch.object(tool, 'SOUND_EXPORTS', overrides['SOUND_EXPORTS']),
            mock.patch.object(tool, 'KERNEL_INPUTS', overrides['KERNEL_INPUTS']),
            mock.patch.object(tool, 'DOCKCHANNEL_BASE', overrides['DOCKCHANNEL_BASE']),
            mock.patch.object(tool, 'DOCKCHANNEL_HISTORICAL', overrides['DOCKCHANNEL_HISTORICAL']),
            mock.patch.object(tool, 'MACSMC_HISTORICAL', overrides['MACSMC_HISTORICAL']),
            mock.patch.object(tool, 'HISTORICAL_PATCHES', historical[0]),
            mock.patch.object(tool, 'HISTORICAL_PATCH_SHA256', historical[1]),
        ]
        if retained is not None:
            stack.append(mock.patch.object(tool, 'RETAINED_PATCH', str(retained)))
            stack.append(mock.patch.object(tool, 'RETAINED_PATCH_SHA256', digest(retained)))
        for item in stack:
            item.start()
            self.addCleanup(item.stop)

    def image(self):
        return 'sha256:' + tool.BUILDER_IMAGE

    def test_default_pins_match_published_inputs_and_repo_artifacts(self):
        self.assertEqual(tool.KERNEL_ARCHIVE, PUBLISHED_ARCHIVE)
        self.assertEqual(tool.SOUND_EXPORTS, PUBLISHED_EXPORTS)
        self.assertEqual(tool.BUILDER_IMAGE, PUBLISHED_IMAGE)
        self.assertEqual(tool.KERNEL_RELEASE, KERNEL_RELEASE)
        self.assertEqual(tool.KBUILD_ENV, EXPECTED_ENV)
        self.assertEqual(tool.HISTORICAL_PATCHES, tuple(PUBLISHED_PATCHES))
        self.assertEqual(tool.HISTORICAL_PATCH_SHA256, PUBLISHED_PATCHES)
        for path, pin in PUBLISHED_PATCHES.items():
            self.assertEqual(digest(ROOT / path), pin)
        self.assertEqual(tool.RETAINED_PATCH, 'patches-kernel/retained-hardening/dockchannel-packet-bounds.patch')
        self.assertEqual(tool.RETAINED_PATCH_SHA256, digest(RETAINED_PATCH))
        self.assertEqual(tool.DOCKCHANNEL_BASE,
                         '77ef81abc66a61d08818142895bc02bec2b01abe9461195c3c575799da91d1eb')
        self.assertEqual(tool.DOCKCHANNEL_HISTORICAL, PINNED_DRIVER_SHA256)
        self.assertEqual(tool.MACSMC_HISTORICAL,
                         '0cbe1eaf11e473336bfc24d5036a4a15586f21c868e8fedd21aeb1ce8bcaa9d6')
        rows = (ROOT / 'audio/j713/kernel-inputs.sha256').read_text().splitlines()
        self.assertEqual(tool.KERNEL_INPUTS, {
            line.split()[1]: line.split()[0] for line in rows if line.strip()})
        manifest = (ROOT / 'audio/j713/imported-source.sha256').read_text().splitlines()
        pinned = {line.split()[1]: line.split()[0] for line in manifest if line.strip()}
        self.assertEqual(pinned['kernel/mca.c'], self.repo_mca)
        self.assertEqual(tool.AUDIO_MODULES, (
            'snd-soc-j713-group.ko', 'snd-soc-tas2764.ko', 'snd-soc-macaudio.ko',
            'snd-soc-apple-mca.ko', 'apple-admac.ko'))
        with self.assertRaises(SystemExit):
            tool.main(['--kernel-archive', 'a', '--sound-exports', 'b',
                       '--image', self.image(), '--output', 'c',
                       '--kernel-archive-sha256', 'ff' * 32])

    def test_prepare_applies_synthetic_patches_and_copies_current_mca(self):
        files = self.kernel_files(b'alpha\n')
        archive = self.write_archive(files)
        exports = self.base / 'exports'
        exports.write_bytes(b'supplemental\n')
        historical = self.historical_patches()
        self.apply_pins(self.pins_for(
            files, archive, exports, after_historical=(b'alpha\nbeta\ngamma\n', b'smc\nlocked\n')),
            historical[:2], historical[2])
        output = self.base / 'out'
        before = {path: digest(path) for path in (archive, exports, RETAINED_PATCH, PINNED_DRIVER)}
        tool.prepare(archive, exports, self.image(), output)
        driver = (output / 'kernel' / tool.DOCKCHANNEL).read_text()
        self.assertEqual(driver, 'alpha\nbeta\ngamma\nbounds\n')
        self.assertEqual((output / 'kernel' / tool.MACSMC).read_text(), 'smc\nlocked\n')
        source = ROOT / 'audio/j713/kernel'
        copied = output / 'mca'
        names = sorted(path.relative_to(source).as_posix() for path in source.rglob('*') if path.is_file())
        self.assertEqual(names, sorted(path.relative_to(copied).as_posix()
                                       for path in copied.rglob('*') if path.is_file()))
        self.assertEqual((copied / 'mca.c').read_bytes(), (source / 'mca.c').read_bytes())
        self.assertIn('bclk_ratio = (unsigned long)tdm_slot_width * tdm_slots;',
                      (copied / 'mca.c').read_text())
        for path, pin in before.items():
            self.assertEqual(digest(path), pin)
        self.assertFalse((output / 'receipt.json').exists())

    def test_wrong_pins_bad_image_and_collision_do_not_mutate_inputs(self):
        files = self.kernel_files(b'alpha\n')
        archive = self.write_archive(files)
        exports = self.base / 'exports'
        exports.write_bytes(b'supplemental\n')
        archive_pin = digest(archive)
        exports_pin = digest(exports)

        missing = self.base / 'missing-out'
        with self.assertRaises(RuntimeError):
            tool.prepare(archive, exports, self.image(), missing)
        self.assertFalse(missing.exists())
        self.assertEqual(digest(archive), archive_pin)

        historical = self.historical_patches()
        pinned = self.pins_for(files, archive, exports)
        self.apply_pins(pinned, historical[:2], historical[2])
        occupied = self.base / 'occupied'
        occupied.mkdir()
        (occupied / 'sentinel').write_text('keep')
        with self.assertRaises(RuntimeError):
            tool.prepare(archive, exports, self.image(), occupied)
        self.assertEqual((occupied / 'sentinel').read_text(), 'keep')
        self.assertFalse((occupied / 'kernel').exists())

        for image in ('m4-gravity-builder:20261008', PUBLISHED_IMAGE,
                      'sha256:' + 'ab' * 32, 'sha256:' + PUBLISHED_IMAGE.upper()):
            destination = self.base / ('image-' + str(len(image)))
            with self.subTest(image=image):
                with self.assertRaises(RuntimeError):
                    tool.prepare(archive, exports, image, destination)
                self.assertFalse(destination.exists())
        self.assertEqual(digest(exports), exports_pin)

    def test_unsafe_archive_members_are_refused(self):
        def pack(member):
            archive = self.base / ('unsafe-%s.tar.gz' % member.name.replace('/', '_'))
            with tarfile.open(archive, 'w:gz') as packed:
                if member.isfile():
                    payload = b'outside\n'
                    member.size = len(payload)
                    packed.addfile(member, io.BytesIO(payload))
                else:
                    packed.addfile(member)
            return archive

        cases = {
            'escaping-link': tarfile.TarInfo('linux/arch/arm64/tools/escape'),
            'absolute-link': tarfile.TarInfo('linux/link'),
            'hardlink': tarfile.TarInfo('linux/hard'),
            'escape': tarfile.TarInfo('linux/../../outside'),
            'absolute': tarfile.TarInfo('/etc/passwd'),
            'fifo': tarfile.TarInfo('linux/fifo'),
        }
        cases['escaping-link'].type = tarfile.SYMTYPE
        cases['escaping-link'].linkname = '../../../../outside'
        cases['absolute-link'].type = tarfile.SYMTYPE
        cases['absolute-link'].linkname = '/tmp/outside'
        cases['hardlink'].type = tarfile.LNKTYPE
        cases['hardlink'].linkname = 'linux/.config'
        cases['fifo'].type = tarfile.FIFOTYPE
        exports = self.base / 'exports'
        exports.write_bytes(b'supplemental\n')
        for name, member in cases.items():
            archive = pack(member)
            destination = self.base / name
            with self.subTest(member=name):
                with mock.patch.object(tool, 'KERNEL_ARCHIVE', digest(archive)), \
                     mock.patch.object(tool, 'SOUND_EXPORTS', digest(exports)):
                    with self.assertRaises(RuntimeError):
                        tool.prepare(archive, exports, self.image(), destination)
                self.assertFalse(destination.exists())
                self.assertFalse((self.base / 'outside').exists())

    def test_prepare_rejects_duplicate_regular_member_before_output(self):
        files = self.kernel_files(b'alpha\n')
        duplicate = files['.config']
        archive = self.write_archive(files, trailing=(('.config', duplicate),))
        with tarfile.open(archive, 'r:gz') as packed:
            configs = [member for member in packed.getmembers() if member.name == 'linux/.config']
        self.assertEqual(len(configs), 2)
        self.assertTrue(all(member.isfile() and not member.isdir() for member in configs))
        self.assertEqual([member.size for member in configs], [len(duplicate), len(duplicate)])
        exports = self.base / 'exports-duplicate'
        exports.write_bytes(b'supplemental\n')
        historical = self.historical_patches()
        self.apply_pins(self.pins_for(
            files, archive, exports, after_historical=(b'alpha\nbeta\ngamma\n', b'smc\nlocked\n')),
            historical[:2], historical[2])
        output = self.base / 'duplicate-member'
        with self.assertRaises(RuntimeError) as caught:
            tool.prepare(archive, exports, self.image(), output)
        self.assertIn('duplicate archive member', str(caught.exception))
        self.assertFalse(output.exists())

    def test_prepare_keeps_internal_symlinks_and_their_targets(self):
        files = self.kernel_files(b'alpha\n')
        files['scripts/syscall.tbl'] = b'syscall table\n'
        files['Documentation/process/changes.rst'] = b'changes\n'
        links = (
            ('arch/arm64/tools/syscall_64.tbl', '../../../scripts/syscall.tbl'),
            ('Documentation/Changes', 'process/changes.rst'),
        )
        archive = self.write_archive(files, links=links)
        exports = self.base / 'exports'
        exports.write_bytes(b'supplemental\n')
        historical = self.historical_patches()
        self.apply_pins(self.pins_for(
            files, archive, exports, after_historical=(b'alpha\nbeta\ngamma\n', b'smc\nlocked\n')),
            historical[:2], historical[2])
        output = self.base / 'linked'
        tool.prepare(archive, exports, self.image(), output)
        kernel = output / 'kernel'
        syscall = kernel / 'arch/arm64/tools/syscall_64.tbl'
        changes = kernel / 'Documentation/Changes'
        self.assertTrue(syscall.is_symlink())
        self.assertEqual(syscall.readlink().as_posix(), '../../../scripts/syscall.tbl')
        self.assertEqual(syscall.read_bytes(), b'syscall table\n')
        self.assertEqual((kernel / 'scripts/syscall.tbl').read_bytes(), b'syscall table\n')
        self.assertTrue(changes.is_symlink())
        self.assertEqual(changes.readlink().as_posix(), 'process/changes.rst')
        self.assertEqual(changes.read_bytes(), b'changes\n')

    def test_extract_refuses_file_through_internal_symlink_directory(self):
        """A symlink directory whose target stays inside the kernel root is kept.

        A later regular member that descends through that symlink is refused
        while extracting. data_filter accepts the member because realpath stays
        inside the destination; the file must not be written through the link.
        """
        files = self.kernel_files(b'alpha\n')
        planted = b'planted-through-link\n'
        archive = self.write_archive(
            files,
            links=(('certs', 'include/config'),),
            trailing=(('certs/planted', planted),),
        )
        kernel = self.base / 'through-symlink'
        with self.assertRaises(RuntimeError) as caught:
            tool.extract_archive(archive, kernel)
        self.assertIn('refusing to extract through an existing symlink', str(caught.exception))
        self.assertTrue((kernel / 'certs').is_symlink())
        self.assertEqual((kernel / 'certs').readlink().as_posix(), 'include/config')
        release = kernel / 'include/config/kernel.release'
        self.assertEqual(release.read_bytes(), (KERNEL_RELEASE + '\n').encode())
        self.assertFalse((kernel / 'include/config/planted').exists())
        self.assertNotIn(planted, release.read_bytes())

    def test_prepare_preserves_script_mode_and_ordinary_files(self):
        files = self.kernel_files(b'alpha\n')
        files['scripts/link-vmlinux.sh'] = b'#!/bin/sh\ntrue\n'
        modes = {'scripts/link-vmlinux.sh': 0o755, '.config': 0o644}
        archive = self.write_archive(files, modes=modes)
        exports = self.base / 'exports'
        exports.write_bytes(b'supplemental\n')
        historical = self.historical_patches()
        self.apply_pins(self.pins_for(
            files, archive, exports, after_historical=(b'alpha\nbeta\ngamma\n', b'smc\nlocked\n')),
            historical[:2], historical[2])
        output = self.base / 'modes'
        tool.prepare(archive, exports, self.image(), output)
        kernel = output / 'kernel'
        script = kernel / 'scripts/link-vmlinux.sh'
        self.assertEqual(stat.S_IMODE(script.stat().st_mode), 0o755)
        self.assertEqual(script.read_bytes(), b'#!/bin/sh\ntrue\n')
        self.assertEqual(stat.S_IMODE((kernel / '.config').stat().st_mode), 0o644)
        self.assertFalse((kernel / '.config').stat().st_mode & stat.S_IXUSR)

    def test_patch_failure_leaves_no_receipt(self):
        files = self.kernel_files(b'alpha\n')
        archive = self.write_archive(files)
        exports = self.base / 'exports'
        exports.write_bytes(b'supplemental\n')
        broken = self.patch_file('broken.patch', 'this is not a unified diff\n')
        retained = self.patch_file('unused.patch', 'neither is this\n')
        historical = ((str(broken),), {str(broken): digest(broken)})
        self.apply_pins(self.pins_for(files, archive, exports), historical, retained)
        output = self.base / 'patched'
        with self.assertRaises(Exception) as caught:
            tool.prepare(archive, exports, self.image(), output)
        self.assertNotIsInstance(caught.exception, AssertionError)
        self.assertFalse((output / 'receipt.json').exists())
        driver = output / 'kernel' / tool.DOCKCHANNEL
        if driver.exists():
            self.assertNotIn('bounds', driver.read_text())

    def test_imported_source_mismatch_is_rejected_before_output(self):
        files = self.kernel_files(b'alpha\n')
        archive = self.write_archive(files)
        exports = self.base / 'exports'
        exports.write_bytes(b'supplemental\n')
        manifest = self.base / 'imported-source.sha256'
        manifest.write_text('ff' * 32 + '  kernel/mca.c\n')
        historical = self.historical_patches()
        self.apply_pins(self.pins_for(files, archive, exports), historical[:2], historical[2])
        output = self.base / 'manifest-out'
        with mock.patch.object(tool, 'IMPORTED_MANIFEST', manifest):
            with self.assertRaises(RuntimeError):
                tool.prepare(archive, exports, self.image(), output)
        self.assertFalse(output.exists())

    def test_prepare_applies_retained_bounds_patch_to_pinned_driver(self):
        """The fixture is the post-historical driver. Historical patches are not reapplied."""
        self.assertEqual(self.repo_driver, PINNED_DRIVER_SHA256)
        macsmc = b'smc-historical\n'
        files = self.kernel_files(PINNED_DRIVER.read_bytes(), macsmc)
        archive = self.write_archive(files)
        exports = self.base / 'exports'
        exports.write_bytes(b'supplemental\n')
        overrides = self.pins_for(files, archive, exports)
        self.apply_pins(overrides, ((), {}))
        output = self.base / 'real-driver'
        tool.prepare(archive, exports, self.image(), output)
        patched = (output / 'kernel' / tool.DOCKCHANNEL).read_text()
        self.assertIn('Packet shorter than subheader', patched)
        self.assertIn('Received zero-length input report', patched)
        self.assertIn('goto cleanup;', patched)
        self.assertNotEqual(text_digest(patched.encode()), PINNED_DRIVER_SHA256)
        self.assertEqual(digest(PINNED_DRIVER), PINNED_DRIVER_SHA256)
        self.assertEqual(digest(RETAINED_PATCH), self.repo_retained)
        self.assertEqual((output / 'mca/mca.c').read_bytes(), (ROOT / 'audio/j713/kernel/mca.c').read_bytes())
        self.assertEqual(digest(ROOT / 'audio/j713/kernel/mca.c'), self.repo_mca)

    def docker_runner(self, mutate=None, fail=False):
        real = subprocess.run
        calls = []

        def run(argv, **kwargs):
            argv = [str(part) for part in argv]
            if argv and argv[0] == 'patch':
                return real(argv, **kwargs)
            if not argv or argv[0] != 'docker':
                raise AssertionError('unexpected command ' + repr(argv))
            calls.append(argv)
            if fail:
                return subprocess.CompletedProcess(argv, 1, '', 'build failed')
            mounts = {}
            index = 0
            while index < len(argv):
                if argv[index] == '-v':
                    host, guest = argv[index + 1].split(':', 1)
                    guest = guest.split(':', 1)[0]
                    mounts[guest] = Path(host)
                index += 1
            # Same path the kernel build writes: <kernel>/certs/signing_key.x509.
            der_path = mounts['/kernel'] / 'certs/signing_key.x509'
            der_path.parent.mkdir(parents=True, exist_ok=True)
            pem = (ROOT / MODULE_CERTIFICATE).read_bytes().decode('ascii')
            der_path.write_bytes(ssl.PEM_cert_to_DER_cert(pem))
            if mutate:
                mutate(mounts)
            else:
                image = mounts['/kernel'] / 'arch/arm64/boot/Image.gz'
                image.parent.mkdir(parents=True, exist_ok=True)
                image.write_bytes(b'candidate-image')
                for name in tool.AUDIO_MODULES:
                    (mounts['/job'] / name).write_bytes(b'module:' + name.encode())
            return subprocess.CompletedProcess(argv, 0, '', '')

        return calls, run

    def prepared_inputs(self, extra_files=None):
        files = self.kernel_files(b'alpha\n')
        if extra_files:
            files.update(extra_files)
        archive = self.write_archive(files)
        exports = self.base / 'exports'
        exports.write_bytes(b'supplemental\n')
        historical = self.historical_patches()
        self.apply_pins(self.pins_for(
            files, archive, exports, after_historical=(b'alpha\nbeta\ngamma\n', b'smc\nlocked\n')),
            historical[:2], historical[2])
        return archive, exports, files

    def test_build_records_artifact_hashes_and_immutable_docker_command(self):
        archive, exports, files = self.prepared_inputs()
        output = self.base / 'built'
        calls, run = self.docker_runner()
        with mock.patch.object(tool.subprocess, 'run', side_effect=run):
            receipt = tool.build(archive, exports, self.image(), output)
        self.assertEqual(len(calls), 1)
        command_args = calls[0]
        entrypoint = command_args.index('--entrypoint')
        self.assertEqual(command_args[entrypoint:entrypoint + 2], ['--entrypoint', '/bin/bash'])
        self.assertLess(entrypoint, command_args.index('-c'))
        volumes = [command_args[index + 1] for index, arg in enumerate(command_args) if arg == '-v']
        self.assertIn(
            str(exports.resolve()) + ':/reference-sound-exports.symvers:ro',
            volumes)
        command = ' '.join(command_args)
        self.assertIn('--network none', command)
        self.assertIn('--pull never', command)
        self.assertNotIn('--privileged', command)
        self.assertIn('sha256:' + PUBLISHED_IMAGE, command)
        self.assertNotIn('m4-gravity-builder', command)
        script = calls[0][-1]
        self.assertLess(script.index('Image.gz'), script.index('M=/job'))
        for token in ('ARCH=arm64', 'LLVM=1', 'LOCALVERSION=', 'KCFLAGS=-Werror',
                      'J713_PROFILE=conservative-v1',
                      'KBUILD_EXTRA_SYMBOLS=/reference-sound-exports.symvers'):
            self.assertIn(token, script)
        for key, value in EXPECTED_ENV.items():
            self.assertIn(key + '=' + value, command)
        self.assertNotIn('speakersafetyd', command)
        self.assertNotIn('cargo', command.lower())
        saved = json.loads((output / 'receipt.json').read_text())
        self.assertEqual(saved, receipt)
        self.assertEqual(receipt['schema'], 1)
        self.assertEqual(receipt['kind'], 'j713-hardened-kernel-candidate')
        self.assertFalse(receipt['native_qualified'])
        self.assertFalse(receipt['fresh_install_qualified'])
        self.assertEqual(receipt['profile'], 'conservative-v1')
        self.assertEqual(receipt['image'], 'sha256:' + PUBLISHED_IMAGE)
        self.assertEqual(receipt['kernel_archive'], digest(archive))
        self.assertEqual(receipt['sound_exports'], digest(exports))
        self.assertEqual(receipt['kernel_release'], KERNEL_RELEASE)
        self.assertEqual(receipt['config_sha256'], text_digest(files['.config']))
        image = output / 'Image.gz'
        self.assertEqual(receipt['kernel'], {
            'path': 'Image.gz', 'sha256': digest(image), 'bytes': image.stat().st_size})
        self.assertEqual(set(receipt['modules']), set(tool.AUDIO_MODULES))
        for name in tool.AUDIO_MODULES:
            module = output / 'modules' / name
            self.assertEqual(receipt['modules'][name], {
                'sha256': digest(module), 'bytes': module.stat().st_size})
        symvers = text_digest(files['Module.symvers'])
        vmlinux = text_digest(files['vmlinux.symvers'])
        self.assertEqual(receipt['exports'], {
            'module_symvers_before': symvers,
            'module_symvers_after': symvers,
            'vmlinux_symvers_before': vmlinux,
            'vmlinux_symvers_after': vmlinux,
            'supplemental_before': digest(exports),
            'supplemental_after': digest(exports),
        })
        self.assertEqual((output / 'kernel/include/config/kernel.release').read_text(),
                         KERNEL_RELEASE + '\n')
        self.assertEqual(digest(output / 'kernel/.config'), text_digest(files['.config']))

    def test_subprocess_build_failure_writes_no_receipt(self):
        archive, exports, _files = self.prepared_inputs()
        output = self.base / 'failed-build'
        _calls, run = self.docker_runner(fail=True)
        with mock.patch.object(tool.subprocess, 'run', side_effect=run):
            with self.assertRaises(Exception) as caught:
                tool.build(archive, exports, self.image(), output)
        self.assertNotIsInstance(caught.exception, AssertionError)
        self.assertFalse((output / 'receipt.json').exists())
        self.assertFalse((output / 'Image.gz').exists())

    def test_export_and_release_drift_are_rejected(self):
        # require_unchanged checks Module.symvers, then the KERNEL_INPUTS loop.
        # include/config/kernel.release is one of those pins, so its byte change
        # is reported as kernel input drift before the later release-string check.
        reasons = {
            'exports': 'Module.symvers changed during the kernel build',
            'config': 'kernel input drifted: .config',
            'release': 'kernel input drifted: include/config/kernel.release',
        }

        def complete_then(mutate_input):
            def mutate(mounts):
                kernel = mounts['/kernel']
                image = kernel / 'arch/arm64/boot/Image.gz'
                image.parent.mkdir(parents=True, exist_ok=True)
                image.write_bytes(b'candidate-image')
                for module in tool.AUDIO_MODULES:
                    (mounts['/job'] / module).write_bytes(b'module:' + module.encode())
                mutate_input(mounts)
            return mutate

        def cases():
            def exports(mounts):
                path = mounts['/kernel'] / 'Module.symvers'
                path.write_bytes(path.read_bytes() + b'drift\n')

            def release(mounts):
                (mounts['/kernel'] / 'include/config/kernel.release').write_text('other\n')

            def config(mounts):
                (mounts['/kernel'] / '.config').write_bytes(b'changed\n')

            return {'exports': exports, 'release': release, 'config': config}

        archive, exports, _files = self.prepared_inputs()
        export_before = digest(exports)
        for name, mutate_input in cases().items():
            output = self.base / ('drift-' + name)
            _calls, run = self.docker_runner(mutate=complete_then(mutate_input))
            with self.subTest(drift=name):
                with mock.patch.object(tool.subprocess, 'run', side_effect=run):
                    with self.assertRaises(RuntimeError) as caught:
                        tool.build(archive, exports, self.image(), output)
                self.assertIn(reasons[name], str(caught.exception))
                self.assertNotIn('kernel Image.gz was not produced', str(caught.exception))
                self.assertFalse((output / 'receipt.json').exists())
                self.assertEqual(digest(exports), export_before)

    def test_vmlinux_symvers_drift_is_rejected_without_receipt(self):
        # Real archive: vmlinux.symvers and Module.symvers are both
        # 5e3c2624c6a991bae503336137ce15071e51ef043edcd20ba1ad19df4e23d67c.
        # Read the published pin before prepared_inputs() replaces KERNEL_INPUTS.
        self.assertEqual(tool.KERNEL_INPUTS['Module.symvers'],
                         '5e3c2624c6a991bae503336137ce15071e51ef043edcd20ba1ad19df4e23d67c')
        table = b'exports\n'
        archive, exports, files = self.prepared_inputs(extra_files={'vmlinux.symvers': table})
        self.assertEqual(files['vmlinux.symvers'], files['Module.symvers'])

        def mutate(mounts):
            kernel = mounts['/kernel']
            (kernel / 'vmlinux.symvers').write_bytes(b'regenerated\n')
            image = kernel / 'arch/arm64/boot/Image.gz'
            image.parent.mkdir(parents=True, exist_ok=True)
            image.write_bytes(b'candidate-image')
            for name in tool.AUDIO_MODULES:
                (mounts['/job'] / name).write_bytes(b'module:' + name.encode())

        output = self.base / 'vmlinux-drift'
        _calls, run = self.docker_runner(mutate=mutate)
        with mock.patch.object(tool.subprocess, 'run', side_effect=run):
            with self.assertRaises(RuntimeError) as caught:
                tool.build(archive, exports, self.image(), output)
        self.assertIn('vmlinux.symvers changed during the kernel build', str(caught.exception))
        self.assertNotIn('kernel Image.gz was not produced', str(caught.exception))
        self.assertFalse((output / 'receipt.json').exists())
        self.assertFalse((output / 'Image.gz').exists())
        kernel = output / 'kernel'
        self.assertEqual((kernel / 'Module.symvers').read_bytes(), files['Module.symvers'])
        self.assertNotEqual((kernel / 'vmlinux.symvers').read_bytes(), files['vmlinux.symvers'])

    def test_build_changes_image_and_keeps_both_symvers_tables(self):
        table = b'exports\n'
        archive, exports, files = self.prepared_inputs(extra_files={'vmlinux.symvers': table})
        output = self.base / 'vmlinux-kept'
        _calls, run = self.docker_runner()
        with mock.patch.object(tool.subprocess, 'run', side_effect=run):
            receipt = tool.build(archive, exports, self.image(), output)
        kernel = output / 'kernel'
        self.assertEqual((output / 'Image.gz').read_bytes(), b'candidate-image')
        self.assertNotEqual((output / 'Image.gz').read_bytes(), table)
        self.assertEqual((kernel / 'Module.symvers').read_bytes(), table)
        self.assertEqual((kernel / 'vmlinux.symvers').read_bytes(), table)
        self.assertEqual(receipt['exports']['module_symvers_before'], text_digest(table))
        self.assertEqual(receipt['exports']['module_symvers_after'], text_digest(table))
        self.assertTrue((output / 'receipt.json').exists())

    def test_missing_vmlinux_symvers_is_rejected_before_docker(self):
        files = self.kernel_files(b'alpha\n')
        del files['vmlinux.symvers']
        archive = self.write_archive(files)
        exports = self.base / 'exports-missing-vmlinux'
        exports.write_bytes(b'supplemental\n')
        historical = self.historical_patches()
        self.apply_pins(self.pins_for(
            files, archive, exports, after_historical=(b'alpha\nbeta\ngamma\n', b'smc\nlocked\n')),
            historical[:2], historical[2])
        output = self.base / 'missing-vmlinux'
        calls, run = self.docker_runner()
        with mock.patch.object(tool.subprocess, 'run', side_effect=run):
            with self.assertRaises(RuntimeError) as caught:
                tool.build(archive, exports, self.image(), output)
        self.assertIn('vmlinux.symvers must be a regular file', str(caught.exception))
        self.assertEqual(calls, [])
        self.assertFalse((output / 'receipt.json').exists())

    def test_relative_inputs_use_absolute_docker_mounts(self):
        archive, exports, _files = self.prepared_inputs()
        output_name = 'relative-built'
        calls, run = self.docker_runner()
        cwd = os.getcwd()
        os.chdir(self.base)
        self.addCleanup(os.chdir, cwd)
        with mock.patch.object(tool.subprocess, 'run', side_effect=run):
            tool.build(
                os.path.relpath(archive, self.base),
                os.path.relpath(exports, self.base),
                self.image(),
                output_name)
        self.assertEqual(len(calls), 1)
        mounts = {}
        argv = calls[0]
        index = 0
        while index < len(argv):
            if argv[index] == '-v':
                host, guest = argv[index + 1].split(':', 1)
                guest = guest.split(':', 1)[0]
                mounts[guest] = host
            index += 1
        expected = {
            '/kernel': (self.base / output_name / 'kernel').resolve(),
            '/reference-sound-exports.symvers': exports.resolve(),
            '/job': (self.base / output_name / 'mca').resolve(),
        }
        self.assertEqual(set(expected), set(mounts))
        for guest, intended in expected.items():
            host = mounts[guest]
            self.assertTrue(os.path.isabs(host))
            self.assertEqual(Path(host).resolve(), intended)

    def certificate_path(self):
        return ROOT / MODULE_CERTIFICATE

    def test_committed_module_certificate_is_pinned_public_certificate(self):
        path = self.certificate_path()
        data = path.read_bytes()
        self.assertEqual(text_digest(data), MODULE_CERTIFICATE_SHA256)
        der = ssl.PEM_cert_to_DER_cert(data.decode('ascii'))
        self.assertEqual(len(der), SIGNING_KEY_DER_BYTES)
        self.assertEqual(text_digest(der), MODULE_CERTIFICATE_DER_SHA256)
        self.assertNotIn(b'PRIVATE KEY', data)
        self.assertIn(b'-----BEGIN CERTIFICATE-----', data)
        self.assertFalse(path.is_symlink())
        self.assertFalse(Path(MODULE_CERTIFICATE).is_absolute())
        self.assertEqual(getattr(tool, 'MODULE_CERTIFICATE', None), MODULE_CERTIFICATE)
        self.assertEqual(getattr(tool, 'MODULE_CERTIFICATE_SHA256', None), MODULE_CERTIFICATE_SHA256)
        self.assertEqual(getattr(tool, 'MODULE_CERTIFICATE_DER_SHA256', None), MODULE_CERTIFICATE_DER_SHA256)

    def test_build_overrides_module_sig_key_and_records_pinned_certificate(self):
        archive, exports, files = self.prepared_inputs()
        committed = self.certificate_path().read_bytes()
        output = self.base / 'signed-key'
        calls, run = self.docker_runner()
        with mock.patch.object(tool.subprocess, 'run', side_effect=run):
            receipt = tool.build(archive, exports, self.image(), output)
        script = calls[0][-1]
        make_lines = [line for line in script.splitlines() if 'make ' in line]
        self.assertEqual(len(make_lines), 2)
        override = 'CONFIG_MODULE_SIG_KEY=' + MODULE_SIG_KEY
        for line in make_lines:
            self.assertIn(override, line)
        copied = output / 'kernel/certs/qualified-module-certificate.pem'
        self.assertTrue(copied.is_file())
        self.assertFalse(copied.is_symlink())
        self.assertEqual(copied.read_bytes(), committed)
        self.assertEqual(receipt['module_certificate_sha256'], MODULE_CERTIFICATE_SHA256)
        self.assertEqual(receipt['module_certificate_der_sha256'], MODULE_CERTIFICATE_DER_SHA256)
        der = output / 'kernel/certs/signing_key.x509'
        self.assertTrue(der.is_file())
        self.assertFalse(der.is_symlink())
        self.assertEqual(der.stat().st_size, SIGNING_KEY_DER_BYTES)
        self.assertEqual(digest(der), MODULE_CERTIFICATE_DER_SHA256)
        self.assertEqual(receipt['make_overrides']['CONFIG_MODULE_SIG_KEY'], MODULE_SIG_KEY)
        self.assertEqual((output / 'kernel/.config').read_bytes(), files['.config'])
        self.assertEqual(self.certificate_path().read_bytes(), committed)

    def test_bad_module_certificate_is_rejected_before_output(self):
        files = self.kernel_files(b'alpha\n')
        archive = self.write_archive(files)
        exports = self.base / 'exports-cert'
        exports.write_bytes(b'supplemental\n')
        historical = self.historical_patches()
        self.apply_pins(self.pins_for(
            files, archive, exports, after_historical=(b'alpha\nbeta\ngamma\n', b'smc\nlocked\n')),
            historical[:2], historical[2])
        committed = self.certificate_path().read_bytes()
        tampered = self.base / 'tampered-cert.pem'
        tampered.write_bytes(committed + b'\n')
        missing = self.base / 'missing-cert.pem'
        linked = self.base / 'linked-cert.pem'
        linked.symlink_to(self.certificate_path())
        cases = {'tampered': tampered, 'missing': missing, 'symlink': linked}
        for name, path in cases.items():
            output = self.base / ('cert-reject-' + name)
            with self.subTest(certificate=name):
                with mock.patch.object(tool, 'MODULE_CERTIFICATE', str(path), create=True), \
                     mock.patch.object(tool, 'MODULE_CERTIFICATE_SHA256', MODULE_CERTIFICATE_SHA256, create=True):
                    with self.assertRaises(RuntimeError):
                        tool.prepare(archive, exports, self.image(), output)
                self.assertFalse(output.exists())
        self.assertEqual(self.certificate_path().read_bytes(), committed)

    def test_copied_module_certificate_mutation_writes_no_receipt(self):
        archive, exports, _files = self.prepared_inputs()
        committed = self.certificate_path().read_bytes()

        def mutate(mounts):
            kernel = mounts['/kernel']
            cert = kernel / 'certs/qualified-module-certificate.pem'
            cert.parent.mkdir(parents=True, exist_ok=True)
            cert.write_bytes(b'not-the-pinned-certificate\n')
            image = kernel / 'arch/arm64/boot/Image.gz'
            image.parent.mkdir(parents=True, exist_ok=True)
            image.write_bytes(b'candidate-image')
            for name in tool.AUDIO_MODULES:
                (mounts['/job'] / name).write_bytes(b'module:' + name.encode())

        output = self.base / 'cert-mutated'
        _calls, run = self.docker_runner(mutate=mutate)
        with mock.patch.object(tool.subprocess, 'run', side_effect=run):
            with self.assertRaises(RuntimeError):
                tool.build(archive, exports, self.image(), output)
        self.assertFalse((output / 'receipt.json').exists())
        self.assertFalse((output / 'Image.gz').exists())
        self.assertEqual(self.certificate_path().read_bytes(), committed)

    def test_missing_or_mutated_signing_der_writes_no_receipt(self):
        archive, exports, _files = self.prepared_inputs()

        def write_outputs(mounts):
            kernel = mounts['/kernel']
            image = kernel / 'arch/arm64/boot/Image.gz'
            image.parent.mkdir(parents=True, exist_ok=True)
            image.write_bytes(b'candidate-image')
            for name in tool.AUDIO_MODULES:
                (mounts['/job'] / name).write_bytes(b'module:' + name.encode())

        def missing(mounts):
            write_outputs(mounts)
            (mounts['/kernel'] / 'certs/signing_key.x509').unlink()

        def longer(mounts):
            write_outputs(mounts)
            der = mounts['/kernel'] / 'certs/signing_key.x509'
            der.write_bytes(der.read_bytes() + b'\x00')
            self.assertGreater(der.stat().st_size, SIGNING_KEY_DER_BYTES)

        def same_length(mounts):
            write_outputs(mounts)
            der = mounts['/kernel'] / 'certs/signing_key.x509'
            original = der.read_bytes()
            self.assertEqual(len(original), SIGNING_KEY_DER_BYTES)
            flipped = bytearray(original)
            flipped[10] ^= 0xff
            der.write_bytes(bytes(flipped))
            self.assertEqual(der.stat().st_size, SIGNING_KEY_DER_BYTES)
            self.assertNotEqual(digest(der), MODULE_CERTIFICATE_DER_SHA256)

        for name, mutate in {'missing': missing, 'longer': longer, 'same-length': same_length}.items():
            output = self.base / ('der-' + name)
            _calls, run = self.docker_runner(mutate=mutate)
            with self.subTest(der=name):
                with mock.patch.object(tool.subprocess, 'run', side_effect=run):
                    with self.assertRaises(RuntimeError):
                        tool.build(archive, exports, self.image(), output)
                self.assertFalse((output / 'receipt.json').exists())
                self.assertFalse((output / 'Image.gz').exists())

    def test_kernel_manifest_refuses_backslash_component(self):
        """A manifest name with one backslash is an unsafe relative path.

        The shipped manifest has no such row. kernel_manifest_entries refuses
        the literal component before any source file is opened.
        """
        literal = 'kernel/sub\\mca.c'
        self.assertEqual(literal.count('\\'), 1)
        manifest = self.base / 'backslash-imported-source.sha256'
        manifest.write_text('%s  %s\n' % (text_digest(b'backslash-boundary\n'), literal))
        loaded = manifest.read_text().split(None, 1)[1].strip()
        self.assertEqual(loaded, literal)
        with mock.patch.object(tool, 'IMPORTED_MANIFEST', manifest):
            with self.assertRaises(RuntimeError) as caught:
                tool.kernel_manifest_entries()
        self.assertEqual(
            str(caught.exception),
            'imported source path is not a safe relative path: ' + literal)

    def test_kernel_manifest_refuses_empty(self):
        """No kernel/ row is an empty imported kernel manifest.

        One manifest is empty. The other pins only README.md with a real
        digest. Only IMPORTED_MANIFEST is patched.
        """
        cases = {
            'empty': '',
            'readme': '%s  README.md\n' % text_digest(b'readme-only\n'),
        }
        for name, body in cases.items():
            with self.subTest(manifest=name):
                manifest = self.base / ('kernel-manifest-%s.sha256' % name)
                manifest.write_text(body)
                with mock.patch.object(tool, 'IMPORTED_MANIFEST', manifest):
                    with self.assertRaises(RuntimeError) as caught:
                        tool.kernel_manifest_entries()
                self.assertEqual(
                    str(caught.exception), 'imported kernel manifest is empty')

    def pinned_audio_fixture(self, symlink_mca=False):
        """Temporary AUDIO tree whose manifest pins only kernel/Makefile and kernel/mca.c.

        Kbuild, a stale module, object debris, and an unlisted .c sit beside
        those two files. require_pinned_inputs hashes listed rows only.
        """
        audio = self.base / ('audio-fixture-%s' % ('link' if symlink_mca else 'files'))
        kernel = audio / 'kernel'
        kernel.mkdir(parents=True)
        makefile = b'obj-m += mca.o\n'
        mca = b'int mca(void) { return 1; }\n'
        (kernel / 'Makefile').write_bytes(makefile)
        if symlink_mca:
            target = self.base / 'mca-link-target.c'
            target.write_bytes(mca)
            (kernel / 'mca.c').symlink_to(target)
        else:
            (kernel / 'mca.c').write_bytes(mca)
        extras = {
            'Kbuild': b'unpinned kbuild\n',
            'stale.ko': b'\x7fELF stale\n',
            'mca.o': b'stale object\n',
            'mca.o.cmd': b'cmd_mca.o\n',
            'unlisted.c': b'int unlisted(void) { return 0; }\n',
        }
        for name, data in extras.items():
            (kernel / name).write_bytes(data)
        pinned = {'kernel/Makefile': makefile, 'kernel/mca.c': mca}
        manifest = audio / 'imported-source.sha256'
        manifest.write_text(''.join(
            '%s  %s\n' % (text_digest(data), name) for name, data in pinned.items()))
        archive = self.write_archive(self.kernel_files(b'alpha\n'))
        exports = self.base / ('exports-' + audio.name)
        exports.write_bytes(b'supplemental\n')
        return audio, manifest, pinned, extras, archive, exports

    def audio_pins(self, audio, manifest, archive, exports):
        return (
            mock.patch.object(tool, 'AUDIO', audio),
            mock.patch.object(tool, 'IMPORTED_MANIFEST', manifest),
            mock.patch.object(tool, 'KERNEL_ARCHIVE', digest(archive)),
            mock.patch.object(tool, 'SOUND_EXPORTS', digest(exports)),
        )

    def test_copy_audio_modules_copies_only_manifest_kernel_files(self):
        audio, manifest, pinned, extras, archive, exports = self.pinned_audio_fixture()
        kernel = audio / 'kernel'
        before = {
            path.relative_to(kernel).as_posix(): path.read_bytes()
            for path in sorted(kernel.iterdir())
        }
        output = self.base / 'copied-mca'
        output.mkdir()
        pins = self.audio_pins(audio, manifest, archive, exports)
        with pins[0], pins[1], pins[2], pins[3]:
            tool.require_pinned_inputs(archive, exports)
            tool.copy_audio_modules(output)
        copied = output / 'mca'
        present = sorted(
            path.relative_to(copied).as_posix()
            for path in copied.rglob('*') if path.is_file() or path.is_symlink())
        self.assertEqual(present, ['Makefile', 'mca.c'])
        for name, data in pinned.items():
            path = copied / name.split('/', 1)[1]
            self.assertFalse(path.is_symlink())
            self.assertTrue(path.is_file())
            self.assertEqual(path.read_bytes(), data)
            self.assertEqual(digest(path), text_digest(data))
        for name in extras:
            self.assertFalse((copied / name).exists())
        for path in copied.rglob('*'):
            self.assertFalse(path.is_symlink())
        after = {
            path.relative_to(kernel).as_posix(): path.read_bytes()
            for path in sorted(kernel.iterdir())
        }
        self.assertEqual(after, before)

    def test_copy_audio_modules_refuses_existing_destination(self):
        """An existing output/mca is refused before any file is copied.

        The helper contract is RuntimeError 'audio module destination already
        exists'. A directory, a regular file, a symlink to a separate sentinel
        directory, and a dangling symlink keep their type, bytes, and link
        target. The outside sentinel stays put and the output tree gains no files.
        """
        audio, manifest, _pinned, _extras, _archive, _exports = self.pinned_audio_fixture()
        source = audio / 'kernel'

        def file_bytes(root):
            return {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in sorted(root.iterdir())
                if path.is_file() and not path.is_symlink()
            }

        def node_record(path):
            if path.is_symlink():
                return ('symlink', os.readlink(path))
            if path.is_file():
                return ('file', path.read_bytes())
            if path.is_dir():
                children = []
                for child in sorted(path.rglob('*'), key=lambda item: item.as_posix()):
                    relative = child.relative_to(path).as_posix()
                    if child.is_symlink():
                        children.append((relative, 'symlink', os.readlink(child)))
                    elif child.is_file():
                        children.append((relative, 'file', child.read_bytes()))
                    else:
                        children.append((relative, 'dir'))
                return ('dir', tuple(children))
            return ('missing',)

        outside = self.base / 'outside-sentinel'
        outside.write_bytes(b'outside-sentinel\n')
        cases = []

        directory_output = self.base / 'existing-directory'
        directory_output.mkdir()
        directory_dest = directory_output / 'mca'
        directory_dest.mkdir()
        (directory_dest / 'sentinel').write_bytes(b'directory-sentinel\n')
        cases.append(('directory', directory_output, []))

        file_output = self.base / 'existing-file'
        file_output.mkdir()
        (file_output / 'mca').write_bytes(b'file-sentinel\n')
        cases.append(('file', file_output, []))

        linked = self.base / 'linked-sentinel-dir'
        linked.mkdir()
        (linked / 'sentinel').write_bytes(b'linked-sentinel\n')
        link_output = self.base / 'existing-symlink'
        link_output.mkdir()
        (link_output / 'mca').symlink_to(linked)
        cases.append(('symlink', link_output, [linked]))

        dangling_target = self.base / 'dangling-mca-target'
        dangling_output = self.base / 'existing-dangling'
        dangling_output.mkdir()
        (dangling_output / 'mca').symlink_to(dangling_target)
        cases.append(('dangling', dangling_output, [dangling_target]))

        source_before = file_bytes(source)
        for kind, output, extras in cases:
            with self.subTest(kind=kind):
                watched = [output, outside, *extras]
                before = [node_record(path) for path in watched]
                with mock.patch.object(tool, 'AUDIO', audio), \
                     mock.patch.object(tool, 'IMPORTED_MANIFEST', manifest):
                    with self.assertRaises(RuntimeError) as caught:
                        tool.copy_audio_modules(output)
                self.assertEqual(
                    str(caught.exception),
                    'audio module destination already exists: ' + str(output / 'mca'))
                self.assertEqual([node_record(path) for path in watched], before)
                self.assertEqual(file_bytes(source), source_before)

    def test_copy_audio_modules_refuses_symlink_listed_source(self):
        audio, manifest, pinned, _extras, archive, exports = self.pinned_audio_fixture(
            symlink_mca=True)
        source = audio / 'kernel' / 'mca.c'
        self.assertTrue(source.is_symlink())
        self.assertEqual(source.read_bytes(), pinned['kernel/mca.c'])
        output = self.base / 'symlink-mca'
        output.mkdir()
        pins = self.audio_pins(audio, manifest, archive, exports)
        with pins[0], pins[1], pins[2], pins[3]:
            tool.require_pinned_inputs(archive, exports)
            with self.assertRaises(RuntimeError) as caught:
                tool.copy_audio_modules(output)
        self.assertIn('kernel/mca.c', str(caught.exception))
        self.assertIn('regular file', str(caught.exception))
        self.assertFalse((output / 'mca').exists())
        self.assertTrue(source.is_symlink())
        self.assertEqual(source.read_bytes(), pinned['kernel/mca.c'])

    def test_copy_audio_modules_refuses_symlink_source_ancestor(self):
        """audio/kernel as a directory symlink is refused when the leaves are regular.

        Makefile and mca.c stay regular files with their pinned hashes, so
        require_pinned_inputs accepts the fixture. copy_audio_modules then
        refuses a non-regular source, creates no output/mca, and leaves the
        directory symlink and source bytes unchanged.
        """
        audio, manifest, pinned, extras, archive, exports = self.pinned_audio_fixture()
        kernel = audio / 'kernel'
        real_kernel = self.base / 'real-kernel'
        kernel.rename(real_kernel)
        kernel.symlink_to(real_kernel)
        link_before = os.readlink(kernel)
        self.assertFalse(audio.is_symlink())
        self.assertEqual(next(iter(pinned)), 'kernel/Makefile')
        for name in ('Makefile', 'mca.c'):
            leaf = real_kernel / name
            self.assertFalse(leaf.is_symlink())
            self.assertTrue(leaf.is_file())
            self.assertEqual(leaf.read_bytes(), pinned['kernel/' + name])
            self.assertFalse((kernel / name).is_symlink())
            self.assertTrue((kernel / name).is_file())
        source_before = {
            name: (real_kernel / name).read_bytes()
            for name in ('Makefile', 'mca.c', *extras)
        }
        output = self.base / 'ancestor-symlink'
        output.mkdir()
        pins = self.audio_pins(audio, manifest, archive, exports)
        with pins[0], pins[1], pins[2], pins[3]:
            tool.require_pinned_inputs(archive, exports)
            with self.assertRaises(RuntimeError) as caught:
                tool.copy_audio_modules(output)
        self.assertEqual(
            str(caught.exception),
            'imported source is not a regular file: kernel/Makefile')
        self.assertFalse((output / 'mca').exists())
        self.assertFalse((output / 'mca').is_symlink())
        self.assertTrue(kernel.is_symlink())
        self.assertEqual(os.readlink(kernel), link_before)
        self.assertFalse(real_kernel.is_symlink())
        for name, data in source_before.items():
            leaf = real_kernel / name
            self.assertFalse(leaf.is_symlink())
            self.assertEqual(leaf.read_bytes(), data)

    def test_copy_audio_modules_refuses_symlink_audio_root(self):
        """A directory symlink used as AUDIO is refused before the path walk.

        The real kernel leaves stay regular files. require_pinned_inputs
        accepts their hashes. copy_audio_modules creates no output/mca and
        leaves the root symlink and source bytes unchanged.
        """
        audio, manifest, pinned, extras, archive, exports = self.pinned_audio_fixture()
        real_audio = self.base / 'real-audio-root'
        audio.rename(real_audio)
        linked = self.base / 'audio-root-link'
        linked.symlink_to(real_audio)
        manifest = real_audio / 'imported-source.sha256'
        link_before = os.readlink(linked)
        real_kernel = real_audio / 'kernel'
        self.assertTrue(linked.is_symlink())
        self.assertFalse(real_kernel.is_symlink())
        self.assertFalse((linked / 'kernel').is_symlink())
        self.assertTrue(real_kernel.is_dir())
        self.assertEqual(next(iter(pinned)), 'kernel/Makefile')
        for name in ('Makefile', 'mca.c'):
            leaf = real_kernel / name
            walked = linked / 'kernel' / name
            self.assertFalse(leaf.is_symlink())
            self.assertTrue(leaf.is_file())
            self.assertEqual(leaf.read_bytes(), pinned['kernel/' + name])
            self.assertFalse(walked.is_symlink())
            self.assertTrue(walked.is_file())
        source_before = {
            name: (real_kernel / name).read_bytes()
            for name in ('Makefile', 'mca.c', *extras)
        }
        output = self.base / 'audio-root-out'
        output.mkdir()
        pins = self.audio_pins(linked, manifest, archive, exports)
        with pins[0], pins[1], pins[2], pins[3]:
            tool.require_pinned_inputs(archive, exports)
            with self.assertRaises(RuntimeError) as caught:
                tool.copy_audio_modules(output)
        self.assertEqual(
            str(caught.exception),
            'imported source is not a regular file: kernel/Makefile')
        self.assertFalse((output / 'mca').exists())
        self.assertFalse((output / 'mca').is_symlink())
        self.assertTrue(linked.is_symlink())
        self.assertEqual(os.readlink(linked), link_before)
        self.assertFalse(real_audio.is_symlink())
        for name, data in source_before.items():
            leaf = real_kernel / name
            self.assertFalse(leaf.is_symlink())
            self.assertEqual(leaf.read_bytes(), data)

    def test_copy_audio_modules_refuses_source_changed_after_precheck(self):
        audio, manifest, pinned, _extras, archive, exports = self.pinned_audio_fixture()
        source = audio / 'kernel' / 'mca.c'
        output = self.base / 'drifted-mca'
        output.mkdir()
        pins = self.audio_pins(audio, manifest, archive, exports)
        with pins[0], pins[1], pins[2], pins[3]:
            tool.require_pinned_inputs(archive, exports)
            source.write_bytes(b'drifted after precheck\n')
            with self.assertRaises(RuntimeError) as caught:
                tool.copy_audio_modules(output)
        self.assertIn('imported source mismatch: kernel/mca.c', str(caught.exception))
        self.assertFalse((output / 'mca').exists())
        self.assertEqual(source.read_bytes(), b'drifted after precheck\n')
        self.assertEqual((audio / 'kernel' / 'Makefile').read_bytes(), pinned['kernel/Makefile'])

    def test_copy_audio_modules_refuses_fifo_replacing_listed_source(self):
        """A FIFO in place of a listed source is refused without blocking this test.

        The real copier runs in a child process. Missing O_NONBLOCK makes
        os.open wait on the FIFO; the parent bounds that wait at five seconds.
        require_pinned_inputs is not called: hashing the FIFO would block on
        its own. FIFO creation skips only this test.
        """
        if not hasattr(os, 'mkfifo'):
            self.skipTest('FIFO creation is unsupported')
        audio, manifest, pinned, _extras, _archive, _exports = self.pinned_audio_fixture()
        source = audio / 'kernel' / 'mca.c'
        self.assertEqual(source.read_bytes(), pinned['kernel/mca.c'])
        source.unlink()
        try:
            os.mkfifo(source)
        except OSError as exc:
            unsupported = set()
            for name in ('ENOTSUP', 'EOPNOTSUPP', 'ENOSYS'):
                value = getattr(errno, name, None)
                if value is not None:
                    unsupported.add(value)
            if exc.errno not in unsupported:
                raise
            self.skipTest('FIFO creation is unsupported')
        output = self.base / 'fifo-mca'
        output.mkdir()
        # 86 is the child status only when copy_audio_modules raised RuntimeError.
        child = (
            'import importlib.util\n'
            'import sys\n'
            'from pathlib import Path\n'
            'builder, audio, manifest, output = sys.argv[1:5]\n'
            'spec = importlib.util.spec_from_file_location(\n'
            "    'j713_hardened_build_child', builder)\n"
            'module = importlib.util.module_from_spec(spec)\n'
            'spec.loader.exec_module(module)\n'
            'module.AUDIO = Path(audio)\n'
            'module.IMPORTED_MANIFEST = Path(manifest)\n'
            'try:\n'
            '    module.copy_audio_modules(Path(output))\n'
            'except RuntimeError as refusal:\n'
            '    print(refusal, flush=True)\n'
            '    raise SystemExit(86)\n'
            'raise SystemExit(0)\n'
        )
        builder = ROOT / 'tools/build-j713-hardened.py'
        try:
            result = subprocess.run(
                [sys.executable, '-c', child, str(builder), str(audio), str(manifest), str(output)],
                timeout=5, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            self.fail('copy_audio_modules blocked on a FIFO')
        self.assertEqual(result.returncode, 86, result.stderr)
        self.assertEqual(
            result.stdout.strip(),
            'imported source is not a regular file: kernel/mca.c')
        self.assertFalse((output / 'mca').exists())
        self.assertFalse((output / 'mca').is_symlink())
        self.assertTrue(stat.S_ISFIFO(source.stat().st_mode))

    def test_prepare_refuses_case_insensitive_destination(self):
        """A parent that aliases CASE-PROBE to case-probe never receives output.

        Path.exists is patched only for that basename. A real filesystem
        prepare of the same inputs still succeeds, and neither path changes
        the pinned inputs.
        """
        archive, exports, _files = self.prepared_inputs()
        watched = (
            archive,
            exports,
            ROOT / 'audio/j713/imported-source.sha256',
            ROOT / 'audio/j713/kernel/mca.c',
            RETAINED_PATCH,
        )
        before = {path: digest(path) for path in watched}
        output = self.base / 'case-folded'
        real_exists = Path.exists

        def exists(path):
            if Path(path).name == 'CASE-PROBE':
                return True
            return real_exists(path)

        with mock.patch.object(Path, 'exists', exists):
            with self.assertRaises(RuntimeError) as caught:
                tool.prepare(archive, exports, self.image(), output)
        self.assertIn('case-sensitive', str(caught.exception))
        self.assertFalse(real_exists(output))
        leftovers = [
            path for path in self.base.rglob('*')
            if 'case-probe' in path.name.lower() or path.name.startswith('.j713-case-')
        ]
        self.assertEqual(leftovers, [])
        for path, pin in before.items():
            self.assertEqual(digest(path), pin)

        real_output = self.base / 'case-real'
        tool.prepare(archive, exports, self.image(), real_output)
        copied = real_output / 'mca' / 'mca.c'
        self.assertTrue(copied.is_file())
        self.assertFalse(copied.is_symlink())
        self.assertEqual(copied.read_bytes(), (ROOT / 'audio/j713/kernel/mca.c').read_bytes())
        for path, pin in before.items():
            self.assertEqual(digest(path), pin)

    def test_prepare_follows_directory_symlink_to_case_sensitive_parent(self):
        """A missing output is prepared through a directory-symlink parent.

        The immediate parent is a symlink to a real case-sensitive directory.
        prepare follows that alias, leaves the alias in place, writes the
        prepared tree in the real directory, and removes the case probe.
        """
        archive, exports, _files = self.prepared_inputs()
        real = self.base / 'real-case-parent'
        real.mkdir()
        marker = real / 'case-fold-marker'
        marker.write_bytes(b'marker')
        self.assertFalse((real / 'CASE-FOLD-MARKER').exists())
        marker.unlink()
        alias = self.base / 'alias-case-parent'
        alias.symlink_to(real.name)
        output = alias / 'prepared'
        self.assertTrue(alias.is_symlink())
        self.assertTrue(alias.is_dir())
        self.assertFalse(real.is_symlink())
        self.assertFalse(output.exists())
        self.assertFalse(output.is_symlink())
        self.assertEqual(os.readlink(alias), real.name)
        tool.prepare(archive, exports, self.image(), output)
        self.assertTrue(alias.is_symlink())
        self.assertTrue(alias.is_dir())
        self.assertFalse(real.is_symlink())
        self.assertEqual(os.readlink(alias), real.name)
        real_output = real / 'prepared'
        self.assertEqual(output.parent, alias)
        self.assertTrue(real_output.is_dir())
        self.assertFalse(real_output.is_symlink())
        self.assertFalse(output.is_symlink())
        self.assertEqual(output.resolve(), real_output.resolve())
        self.assertEqual(
            (real_output / 'kernel' / tool.DOCKCHANNEL).read_text(),
            'alpha\nbeta\ngamma\nbounds\n')
        self.assertEqual((real_output / 'kernel' / tool.MACSMC).read_text(), 'smc\nlocked\n')
        copied = real_output / 'mca' / 'mca.c'
        self.assertTrue(copied.is_file())
        self.assertFalse(copied.is_symlink())
        self.assertEqual(copied.read_bytes(), (ROOT / 'audio/j713/kernel/mca.c').read_bytes())
        certificate = real_output / 'kernel/certs/qualified-module-certificate.pem'
        self.assertTrue(certificate.is_file())
        self.assertFalse(certificate.is_symlink())
        self.assertEqual(certificate.read_bytes(), (ROOT / MODULE_CERTIFICATE).read_bytes())
        self.assertFalse((real_output / 'receipt.json').exists())
        leftovers = [
            path for path in self.base.rglob('*')
            if 'case-probe' in path.name.lower() or path.name.startswith('.j713-case-')
        ]
        self.assertEqual(leftovers, [])

    def test_prepare_refuses_case_insensitive_destination_through_directory_symlink(self):
        """The case probe runs through a symlinked directory output parent.

        An alias onto a simulated case-insensitive filesystem must refuse
        before creating output and clean up its probe. Path.exists reports
        CASE-PROBE only when its resolved parent chain stays inside the real
        directory or that directory symlink. A probe created anywhere else
        is not case-insensitive, so prepare must not refuse. A real
        filesystem prepare of the same inputs still succeeds, and neither
        path changes the pinned inputs.
        """
        archive, exports, _files = self.prepared_inputs()
        watched = (
            archive,
            exports,
            ROOT / 'audio/j713/imported-source.sha256',
            ROOT / 'audio/j713/kernel/mca.c',
            RETAINED_PATCH,
        )
        before = {path: digest(path) for path in watched}
        real = self.base / 'real-case-parent'
        real.mkdir()
        alias = self.base / 'alias-case-parent'
        alias.symlink_to(real.name)
        output = alias / 'prepared'
        self.assertTrue(alias.is_symlink())
        self.assertTrue(alias.is_dir())
        self.assertFalse(real.is_symlink())
        self.assertFalse(output.exists())
        self.assertFalse(output.is_symlink())
        self.assertEqual(os.readlink(alias), real.name)
        self.assertEqual(output.parent, alias)
        self.assertTrue(output.parent.is_symlink())
        real_exists = Path.exists
        destination_parents = []
        for root in (real, alias):
            try:
                destination_parents.append(Path(os.path.realpath(root)))
            except OSError:
                continue

        def within_destination(candidate):
            # realpath resolves the parent chain and does not re-enter Path.exists.
            # A missing CASE-PROBE leaf and a lexical '..' cannot fake containment.
            try:
                resolved = Path(os.path.realpath(candidate.parent))
            except OSError:
                return False
            for root in destination_parents:
                if resolved == root or root in resolved.parents:
                    return True
            return False

        def exists(path):
            candidate = Path(path)
            if candidate.name == 'CASE-PROBE' and within_destination(candidate):
                return True
            return real_exists(path)

        with mock.patch.object(Path, 'exists', exists):
            with self.assertRaises(RuntimeError) as caught:
                tool.prepare(archive, exports, self.image(), output)
        self.assertIn('case-sensitive', str(caught.exception))
        self.assertFalse(real_exists(output))
        self.assertFalse(real_exists(real / 'prepared'))
        self.assertTrue(alias.is_symlink())
        self.assertTrue(alias.is_dir())
        self.assertFalse(real.is_symlink())
        self.assertEqual(os.readlink(alias), real.name)
        leftovers = [
            path for path in self.base.rglob('*')
            if 'case-probe' in path.name.lower() or path.name.startswith('.j713-case-')
        ]
        self.assertEqual(leftovers, [])
        for path, pin in before.items():
            self.assertEqual(digest(path), pin)

        real_output = self.base / 'case-real'
        tool.prepare(archive, exports, self.image(), real_output)
        copied = real_output / 'mca' / 'mca.c'
        self.assertTrue(copied.is_file())
        self.assertFalse(copied.is_symlink())
        self.assertEqual(copied.read_bytes(), (ROOT / 'audio/j713/kernel/mca.c').read_bytes())
        for path, pin in before.items():
            self.assertEqual(digest(path), pin)

    def test_prepare_refuses_missing_and_regular_file_destination_parents(self):
        """A missing or regular-file parent is refused by the case probe itself.

        prepare can fail earlier in archive preflight when the parent is a
        regular file, so this calls require_case_sensitive_destination(parent).
        """
        regular = self.base / 'regular-parent'
        regular.write_bytes(b'keep-parent\n')
        cases = {
            'missing': self.base / 'missing-parent' / 'prepared',
            'file': regular / 'prepared',
        }
        for name, output in cases.items():
            with self.subTest(parent=name):
                parent = output.parent
                before = parent.read_bytes() if parent.is_file() else None
                with self.assertRaises(RuntimeError) as caught:
                    tool.require_case_sensitive_destination(parent)
                self.assertIn('destination parent is not a directory', str(caught.exception))
                self.assertIn(str(parent), str(caught.exception))
                self.assertFalse(output.exists())
                self.assertFalse(output.is_symlink())
                if before is None:
                    self.assertFalse(parent.exists())
                    self.assertFalse(parent.is_symlink())
                else:
                    self.assertTrue(parent.is_file())
                    self.assertFalse(parent.is_symlink())
                    self.assertEqual(parent.read_bytes(), before)
                leftovers = [
                    path for path in self.base.rglob('*')
                    if 'case-probe' in path.name.lower() or path.name.startswith('.j713-case-')
                ]
                self.assertEqual(leftovers, [])

    def test_sound_exports_mismatch_is_rejected_before_output(self):
        files = self.kernel_files(b'alpha\n')
        archive = self.write_archive(files)
        exports = self.base / 'exports-mismatch-input'
        exports.write_bytes(b'supplemental\n')
        before = exports.read_bytes()
        output = self.base / 'exports-mismatch-out'
        with mock.patch.object(tool, 'KERNEL_ARCHIVE', digest(archive)), \
             mock.patch.object(tool, 'SOUND_EXPORTS', 'ab' * 32):
            with self.assertRaises(RuntimeError) as caught:
                tool.prepare(archive, exports, self.image(), output)
        self.assertIn('sound exports do not match the pinned hash', str(caught.exception))
        self.assertFalse(output.exists())
        self.assertEqual(exports.read_bytes(), before)

    def test_each_patch_hash_mismatch_is_rejected_before_output(self):
        files = self.kernel_files(b'alpha\n')
        archive = self.write_archive(files)
        exports = self.base / 'exports-patch-mismatch'
        exports.write_bytes(b'supplemental\n')
        paths, hashes, retained = self.historical_patches()
        self.apply_pins(self.pins_for(
            files, archive, exports, after_historical=(b'alpha\nbeta\ngamma\n', b'smc\nlocked\n')),
            (paths, hashes), retained)
        cases = [(Path(path), 'historical patch hash mismatch') for path in paths]
        cases.append((retained, 'retained patch hash mismatch'))
        for path, message in cases:
            original = path.read_bytes()
            path.write_bytes(original + b'\nnot-the-pinned-patch\n')
            output = self.base / ('patch-mismatch-' + path.name)
            try:
                with self.subTest(patch=path.name):
                    with self.assertRaises(RuntimeError) as caught:
                        tool.prepare(archive, exports, self.image(), output)
                    self.assertIn(message, str(caught.exception))
                    self.assertIn(str(path), str(caught.exception))
                    self.assertFalse(output.exists())
            finally:
                path.write_bytes(original)

    def test_multi_root_archive_is_rejected_before_output(self):
        files = self.kernel_files(b'alpha\n')
        archive = self.base / 'multi-root.tar.gz'
        with tarfile.open(archive, 'w:gz') as packed:
            for name, data in files.items():
                info = tarfile.TarInfo('linux/' + name)
                info.size = len(data)
                info.mode = 0o644
                info.type = tarfile.REGTYPE
                packed.addfile(info, io.BytesIO(data))
            payload = b'other root\n'
            extra = tarfile.TarInfo('other-root/README')
            extra.size = len(payload)
            extra.mode = 0o644
            extra.type = tarfile.REGTYPE
            packed.addfile(extra, io.BytesIO(payload))
        exports = self.base / 'exports-multi-root'
        exports.write_bytes(b'supplemental\n')
        historical = self.historical_patches()
        self.apply_pins(self.pins_for(
            files, archive, exports, after_historical=(b'alpha\nbeta\ngamma\n', b'smc\nlocked\n')),
            historical[:2], historical[2])
        output = self.base / 'multi-root-out'
        with self.assertRaises(RuntimeError) as caught:
            tool.prepare(archive, exports, self.image(), output)
        self.assertIn('kernel archive does not have one top-level directory', str(caught.exception))
        self.assertFalse(output.exists())

    def test_supplemental_export_drift_writes_no_receipt(self):
        archive, exports, _files = self.prepared_inputs()
        before = exports.read_bytes()

        def mutate(mounts):
            supplemental = mounts['/reference-sound-exports.symvers']
            supplemental.write_bytes(before + b'drift\n')
            kernel = mounts['/kernel']
            image = kernel / 'arch/arm64/boot/Image.gz'
            image.parent.mkdir(parents=True, exist_ok=True)
            image.write_bytes(b'candidate-image')
            for name in tool.AUDIO_MODULES:
                (mounts['/job'] / name).write_bytes(b'module:' + name.encode())

        output = self.base / 'supplemental-drift'
        _calls, run = self.docker_runner(mutate=mutate)
        with mock.patch.object(tool.subprocess, 'run', side_effect=run):
            with self.assertRaises(RuntimeError) as caught:
                tool.build(archive, exports, self.image(), output)
        self.assertIn('supplemental sound exports changed during the build', str(caught.exception))
        self.assertFalse((output / 'receipt.json').exists())
        self.assertFalse((output / 'Image.gz').exists())
        self.assertEqual(exports.read_bytes(), before + b'drift\n')

    def test_missing_image_or_module_after_successful_docker_writes_no_receipt(self):
        archive, exports, _files = self.prepared_inputs()

        def write_modules(mounts, skip=None):
            for name in tool.AUDIO_MODULES:
                if name != skip:
                    (mounts['/job'] / name).write_bytes(b'module:' + name.encode())

        def missing_image(mounts):
            write_modules(mounts)

        def missing_module(mounts):
            image = mounts['/kernel'] / 'arch/arm64/boot/Image.gz'
            image.parent.mkdir(parents=True, exist_ok=True)
            image.write_bytes(b'candidate-image')
            write_modules(mounts, skip='apple-admac.ko')

        cases = {
            'image': (missing_image, 'kernel Image.gz was not produced'),
            'module': (missing_module, 'audio module was not produced: apple-admac.ko'),
        }
        for name, (mutate, message) in cases.items():
            output = self.base / ('missing-output-' + name)
            _calls, run = self.docker_runner(mutate=mutate)
            with self.subTest(missing=name):
                with mock.patch.object(tool.subprocess, 'run', side_effect=run):
                    with self.assertRaises(RuntimeError) as caught:
                        tool.build(archive, exports, self.image(), output)
                self.assertIn(message, str(caught.exception))
                self.assertFalse((output / 'receipt.json').exists())
                if name == 'image':
                    self.assertFalse((output / 'Image.gz').exists())
                else:
                    self.assertFalse((output / 'modules' / 'apple-admac.ko').exists())

    def test_each_omitted_audio_module_is_rejected_when_other_outputs_exist(self):
        archive, exports, _files = self.prepared_inputs()
        for skipped in tool.AUDIO_MODULES:
            def mutate(mounts, skipped=skipped):
                kernel = mounts['/kernel']
                image = kernel / 'arch/arm64/boot/Image.gz'
                image.parent.mkdir(parents=True, exist_ok=True)
                image.write_bytes(b'candidate-image')
                for name in tool.AUDIO_MODULES:
                    if name != skipped:
                        (mounts['/job'] / name).write_bytes(b'module:' + name.encode())

            output = self.base / ('missing-one-' + skipped)
            _calls, run = self.docker_runner(mutate=mutate)
            with self.subTest(skipped=skipped):
                with mock.patch.object(tool.subprocess, 'run', side_effect=run):
                    with self.assertRaises(RuntimeError) as caught:
                        tool.build(archive, exports, self.image(), output)
                self.assertIn('audio module was not produced: ' + skipped, str(caught.exception))
                self.assertNotIn('kernel Image.gz was not produced', str(caught.exception))
                self.assertFalse((output / 'receipt.json').exists())
                self.assertFalse((output / 'modules' / skipped).exists())
                job = output / 'mca'
                for name in tool.AUDIO_MODULES:
                    produced = job / name
                    if name == skipped:
                        self.assertFalse(produced.exists())
                    else:
                        self.assertTrue(produced.is_file())
                        self.assertEqual(produced.read_bytes(), b'module:' + name.encode())

    def test_docker_script_sets_errexit_nounset_and_pipefail(self):
        script = tool.docker_script()
        self.assertTrue(script.startswith('set -euo pipefail\n'))
        body = script.splitlines()
        self.assertEqual(body[0], 'set -euo pipefail')
        make_lines = [line for line in body[1:] if line.startswith('make ')]
        self.assertEqual(len(make_lines), 2)
        self.assertIn('Image.gz', make_lines[0])
        self.assertIn('M=/job', make_lines[1])

        # No pipeline in the script exercises pipefail. errexit is what stops
        # the second make. The stub's second invocation would succeed.
        bindir = self.base / 'stub-make'
        bindir.mkdir()
        log = self.base / 'make-invocations'
        stub = bindir / 'make'
        stub.write_text(
            '#!/bin/sh\n'
            'printf \'%s\\n\' "$*" >> "$STUB_MAKE_LOG"\n'
            'count=$(wc -l < "$STUB_MAKE_LOG")\n'
            'if [ "$count" -eq 1 ]; then\n'
            '  exit 9\n'
            'fi\n'
            'exit 0\n'
        )
        stub.chmod(0o755)
        env = os.environ.copy()
        env['PATH'] = str(bindir) + os.pathsep + env.get('PATH', '')
        env['STUB_MAKE_LOG'] = str(log)
        result = subprocess.run(
            ['/bin/bash', '-c', script], env=env, capture_output=True, text=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        invocations = log.read_text().splitlines()
        self.assertEqual(len(invocations), 1)
        self.assertEqual('make ' + invocations[0], make_lines[0])


if __name__ == '__main__':
    unittest.main()
