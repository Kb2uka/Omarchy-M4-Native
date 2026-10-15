# Source accompanying v0.1.0-alpha.1

The installer contains upstream license notices, full retained prepared kernel
and Mesa source trees, both m1n1 trees, patched installer source, audio sources,
build inputs and the current integration source. `SHA256SUMS` identifies the
outer files. Their authorship and licenses are preserved.

- `kernel-controller.tar.gz`, `kernel-decoder.tar.gz`, `mesa.tar.gz`: separate
  prepared trees. Preserve their generated headers, configuration and export
  tables. `ARCHIVES.md` describes exact historical exclusions and inventories.
- `m1n1-stage1-source.tar.gz`, `m1n1-stage2-source.tar.gz`: source identified by
  `BOOTLOADER-SOURCE.md` and the adjacent file inventories.
- `installer-source.tar.gz`: the shipped installer source, with the owner-input
  module and its calls before partitioning and during firmware packaging.
- `build-inputs-final.tar.gz`: retained recipes, patches and compiler/config
  inputs. It is preserved byte-for-byte from private build 2.
- `integration-inputs.tar.gz`: historical integration sources and manifests,
  with four generated speaker WAVs and three preset consent JSON files removed.
  It is **not** the original complete private assembly kit. Old manifests retain
  their original hashes to document required inputs; do not bypass their checks.
- `release-integration.tar.gz`: the current repository's integration code,
  including the release assembler, launchers and owner-input service.
- `j713-audio-cargo-sources.tar.gz`: all 36 locked registry dependencies, original
  `.crate` archives, Cargo vendor directories, licenses and inventory. The patched
  ALSA dependency is separately retained in the application source.
- `j713-audio-reconstruction.tar`: sanitized sysroot member inventory, retained
  Dockerfiles, compiler/dependency versions, supplemental exports and build joins.
- `numpy-2.5.3.tar.gz`: source for the bundled numerical dependency. Wheel notices
  and the local filter-generator source are also in `owner-audio/bundle/`.

Speaker filters are generated from each owner's installed macOS components;
neither those Apple components nor the resulting WAV files are distributed in
this kit. The generator requires exactly the output hashes already required by
the unchanged audio graph and protection manifest. The macOS installer inserts
the owner files into its locally collected firmware package; first Linux boot
installs regular WAV files before audio registration and first-user creation.
Widevine binaries are also excluded and acquired separately only after consent.

## Binaries outside packages

The installed root carries some programs, libraries and kernel modules that no
pacman package owns, such as the GPU module at `/opt/m4-gpu/asahi_m4.ko`.
`packaging/release-source-joins.json` lists each one by SHA-256 with the source
that built it. Each entry names archives in this `source/` directory, most of
them pinned by hash, and tracked paths in `release-integration.tar.gz`. A
tracked path may be pinned to the commit that built the binary. The assembler
then refuses unless that path is unchanged in the revision being assembled.
The package channel similarly requires every binary downloaded from the
`package-inputs` release to be joined to its pinned corresponding source in
`packaging/channel-source-joins.json` (see [package-channel.md](package-channel.md)).

`correspondence` records how the join was established:

- `rebuilt-identical`: a rebuild from the named sources reproduced the exact
  bytes.
- `unverified`: the source is the likeliest published one, matched by file name
  only.

Every GPL binary is `rebuilt-identical`; the 14 MIT userspace entries (Mesa,
the VA-API driver, the speaker daemon) remain `unverified`.

- **GPU module:** the published `kernel-controller.tar.gz` with the four Air GPU
  files from `omarchy-j713-v0.1.0-alpha.1-gpu-air-source.tar.gz` reproduces it
  byte for byte ([issue 77](https://github.com/Kb2uka/Omarchy-M4-Native/issues/77)).
  That packet is attached to the v0.1.0-alpha.1 release; alpha.1's source packet
  originally omitted those four files.
- **Seven audio modules:** `audio/j713/kernel`, `bootstrap` and `topology` at
  the v0.1.0-alpha.1 tag (`ee98743a`), built against `kernel-controller.tar.gz`
  with `reference-sound-exports.symvers`, `J713_PROFILE=conservative-v1` and
  `KCFLAGS=-Werror`. Later revisions build a different `snd-soc-j713-group.ko`
  (the #61 fix), so these joins are pinned to that commit.
- **Two video modules:** the module sources and `recipe/BUILD.md` in
  `build-inputs-final.tar.gz`. `apple-avd.ko` builds against
  `kernel-decoder.tar.gz`, and `avd-controller.ko` against
  `kernel-controller.tar.gz`. The AVD driver copy inside the decoder tree is
  not the shipped source. `recipe/decoder-prepared-to-qualified.patch` records
  the difference.

All nine were rebuilt on 2026-10-14 with read-only kernel trees and the
recorded builder images (clang 21.1.8).

`tools/prepare-j713-release.py` scans the finished root and refuses to assemble
a release in any of these cases:

- an unpackaged ELF file or kernel module is not listed, or its hash differs;
- a listed binary is no longer present;
- a named source is missing from the kit or has changed (checked before any
  output is written, and again on the finished kit);
- a commit-pinned tree differs in the revision being assembled.

It also refuses `unverified` entries, unless `--allow-unverified-source` is
given to reassemble alpha.1 itself. `IMAGE.json` then lists those entries in
`source_correspondence_unverified`. Archives that are not part of the retained
delivery, like the GPU packet, are supplied with `--join-source`.

## Kernel and audio build joins

The final private-build-2 kernel adds the startup-retry patch to the archived
controller tree. In a new extracted controller tree, apply
`patches-kernel/j713-input/dockchannel-start-retry.patch` from the current
integration source with `patch -p1 < /path/to/dockchannel-start-retry.patch`.
The prepared driver's original SHA-256 is
`77ef81abc66a61d08818142895bc02bec2b01abe9461195c3c575799da91d1eb`;
after this patch it is
`3e779d232c2aab7263af15bc2fb9f77fa9532dec7e64a4de0b71ec61c1d8bc46`.
Keep the supplied configuration (SHA-256
`14b4437b2b195e912a2ab614d28761acd3dc79f886d4487964aa0912616a3a6e`)
and use Clang/LLD 21.1.8 with Rust 1.93.1:

```sh
make ARCH=arm64 LLVM=1 LOCALVERSION= -j8 Image.gz
```

Do not apply the patch a second time to an already patched tree. The retained
build was incremental; the archives omit compiled objects, so rebuilding from
them entails more work. Build timestamps can change the output hash. The final
installed Image.gz hash is in IMAGE.json; a byte-identical rebuild is not claimed.

### Optional J713 AFE action kernel

The release assembler retains that kernel when neither `--kernel` nor
`--kernel-receipt` is supplied. Supplying either requires both. This optional
path accepts only the exact AFE action build pinned in
`tools/prepare-j713-release.py`, together with its build and native startup
receipt. It does not qualify another kernel or observe the target itself.

After the startup-retry patch, apply
`patches-kernel/j713-input/macsmc-key-info-lock.patch` and
`patches-kernel/j713-input/dockchannel-afe-action.patch`. The resulting source
identities are:

- `drivers/hid/dockchannel-hid/dockchannel-hid.c`:
  `e4eebbee612eee32bd51ce370d412afc8cf62a9a7e087a3797c2c29306cedfb4`
- `drivers/mfd/macsmc.c`:
  `0cbe1eaf11e473336bfc24d5036a4a15586f21c868e8fedd21aeb1ce8bcaa9d6`

The retained build used the configuration above, the same compiler versions,
and this command:

```sh
make ARCH=arm64 LLVM=1 LOCALVERSION= KBUILD_BUILD_VERSION=18 -j8 Image.gz
```

Its compressed image is 18,901,266 bytes, SHA-256
`c3704cfd2afb7760e73f0b9939a68864a30bd916fcd478bd960e538373123dc9`.
The release remains `7.1.9-m4-gpu-backport-20261008`; all 16,412 export rows and
the module ABI are retained. This records the build identity, not native
acceptance. A later rebuilt image requires its own reviewed pins and evidence.

The receipt is a closed JSON object with these fields; additional fields and
duplicate keys at any depth are rejected so private transcripts and machine
identities do not enter the kit:

| Field | Required value |
| --- | --- |
| `schema`, `kind` | `1`, `"j713-afe-action-kernel"` |
| `board`, `soc` | `"apple,j713"`, `"apple,t8132"` |
| `source_revision` | Full commit of the clean integration checkout being assembled |
| `kernel` | Exact `sha256` and integer `bytes` from `KERNEL_IDENTITY.kernel` |
| `build` | Exact object from `KERNEL_IDENTITY.build`, including release, configuration, both driver hashes, both export-table hashes, integer `export_rows` and `exports_unchanged: true` |
| `patches` | Exact path-to-SHA-256 map from `KERNEL_PATCHES`; each patch must also match the committed checkout |
| `native_startup_qualified` | `true` only after every objective check below has been observed |
| `native_checks` | Exactly the eight boolean checks below, all `true` |
| `evidence_sha256` | SHA-256 of the retained, redacted native startup evidence |
| `fresh_install_qualified` | `false` |
| `physical_acceptance` | `"not_performed"` |

The checks are `boot`, `touch_mt_ready`, `keyboard_registered`,
`trackpad_registered`, `gpu_service_integrity`, `video_service_integrity`,
`audio_service_integrity`, and `repeat_boot`. The service checks cover successful
startup and their existing integrity/readiness guards. Device registration does
not mean physical pointer movement, click feel, heard audio or browser visual
acceptance. Those checks and an attended fresh installation remain pending.
An incomplete receipt must retain false checks and is rejected by assembly;
neither a successful compile nor offline tests authorize changing them to true.
The assembler validates the receipt's declared identity and scope; the recorded
native observations remain the maintainer's responsibility.

Use the ordinary release command with these two additional arguments:

```sh
  --kernel /job/qualified/Image.gz \
  --kernel-receipt /job/qualified/j713-kernel-receipt.json
```

The assembler snapshots the verified kernel and receipt before creating an
image. It replaces only the portable kernel, records its hash in the video
manifest and both boot descriptions, and appends
`dockchannel_hid.m4_afe_action=1` alongside the retained power-v2 option. The
installer's Mac16,12 restriction and the driver's J713/T8132/multi-touch guards
remain in place. GPU, audio, video, browser payloads, loader, DTB and base
initramfs retain the ordinary release path. The installer still collects each
owner's firmware; a private composed boot object must not be distributed.

The three patches and `docs/j713-afe-native-action.md` must be committed in that
revision so `source/release-integration.tar.gz` contains the corresponding
source. `source/j713-kernel-receipt.json` preserves the exact receipt, whose hash
is recorded in `IMAGE.json`. Historical prepared kernel archives and build
inputs remain unchanged; this patch chain describes their join to the new
image. `fresh_install_qualified` remains false in image and kit metadata.

For the audio build helper, copy `vendor-registry` from the Cargo packet into
`audio/j713/speakersafetyd/vendor-registry`. Supply a new Cargo home containing:

```toml
[source.crates-io]
replace-with = "vendored-sources"
[source.vendored-sources]
directory = "/job/source/vendor-registry"
```

`tools/build-j713-audio.sh` copies this application tree and Cargo home into those
container paths and uses `--locked --offline`. The original patched ALSA tree,
lock and source fingerprints must remain intact. The supplemental sound export
table is supplied as `reference-sound-exports.symvers`.

The historical ARM64 sysroot came from glibc `2.43+r22+g8362e8ce10b2-2`, alsa-lib
`1.2.16.1-1` and gcc-libs `16.1.1+r12+g301eb08fa2c5-1`. Historical toolchains used
Rust/Cargo 1.93.1, LLVM/Clang 21.1.8 and GCC 15.2.0. Reconstruct those external
toolchain/sysroot inputs using the retained recipes and package versions; local
Docker image hashes alone are not public download locations. This source packet
does not claim a fully self-contained or byte-identical clean rebuild. The
owner-input pipeline passed on native ARM64 macOS 26.5.1 and 26.5.2 with matching
output hashes. Reconstructed kernel and userspace build environments have not
been qualified as a complete clean rebuild.

The assembler `tools/prepare-j713-release.py` records how the retained private
root was copied, owner-specific payloads removed, freed blocks overwritten,
new service installed and the existing SMC poweroff handler enabled through the
device-tree child described in [j713-poweroff.md](j713-poweroff.md).
The four native filter identities replace the historical x86_64 calculation's
identities in the installed audio manifest. The audio registration helper drops
unrelated global device waits, and both integrity maps record its new identity.
Other audio payloads and protection settings are retained. The GPU service now
waits only for its scanout device. Offline configurators install the independent
recovery console, setup console controls, a bounded desktop-failure report and
the arm64 guard for an unrelated Framework audio probe. See [j713-owner-fir-native.md](j713-owner-fir-native.md).
Without the optional kernel pair, kernel bytes are preserved. Initramfs and
loader bytes are preserved in both paths; the DTB checksum is updated in both
the image metadata and installer definition. Its pinned private base
is a historical build input, not a public download promise. The distributed
source and installer are versioned separately from future main-branch changes.
Assembly requires Python 3.12 or newer and a clean, isolated checkout at the
intended release revision. Tracked changes are rejected before assembly, and
the checkout must still be clean at the same revision when metadata is written.
