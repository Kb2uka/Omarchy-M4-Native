# J713 protected audio source package

This source package assembles the exact J713 audio stack qualified on the
custom `7.1.9-m4-gpu-backport-20261008` ARM64 kernel. It is an explicit,
fail-closed addition to a pending ext4 owner image. It does not alter a running
system, change the kernel, or handle arbitrary Apple models.

The installed path is bootstrap providers → speaker group and codec topology →
explicit MCA binding → protected card → protection daemon → readiness → desktop
routing. The registration service never starts a PCM. The protection service and
ordinary WirePlumber gate are included in this checkout; their contract is
documented in [the readiness guide](../../docs/j713-audio-readiness.md).
No automatic unload, reconfiguration or retry follows partial registration.

## Contents and provenance

- `kernel/`: the five joined GPL kernel modules, including the corrected card
  remove path, frame-reset serialization and activation admission. The transport
  is fixed to 48 kHz, four S32 playback channels and eight S16 feedback channels.
- `bootstrap/`: first overlay loader, including runtime provider checks. Stage 4
  reports the loader's completed population work; registration separately checks
  all PMGR, DART, I2C and ADMAC driver bindings.
- `topology/`: second overlay loader and DT source for the reusable supplier,
  four codec clients and sound card. Existing MCA lookup uses its exact OF path;
  it does not attempt to give an existing node a new overlay phandle.
- `speakersafetyd/`: the modified speakersafetyd 3.0.0 application source under
  GPL-2.0-only, locked dependency
  graph, and vendored ALSA 0.11.0 typed Integer64 fix. Feedback uses period 1024,
  buffer 2048, maximum queued frames 512 and the existing 50 ms age/lease limit.
  Two 512-frame thermal steps retain the more attenuating result. The temporary
  fixed 30 dB test attenuation is absent; normal thermal attenuation remains.
- `qualified.json`: exact runtime hashes, module GNU notes, kernel identity,
  two qualified base DTB hashes, userspace ABI inputs and readiness source pins.
  These are artifact identities, not private machine or boot identities.

Kernel upstream: [GravityLinux/linux](https://github.com/GravityLinux/linux),
revision `af73769bdb04fe428a9118e22995ccfd8a30ad45`. The full configured kernel
build and its external sound export table are build inputs, not included here.
The six checked kernel input hashes are in `kernel-inputs.sha256`. Module
sources retain their upstream file-level notices. Packaging removes trailing whitespace only from kernel/DT text. Exact Rust
source formatting is retained because Cargo output fingerprints changed when it
was normalized. Four path-specific Git whitespace attributes mark those imports;
the stronger complete source hash check still applies.
The module Makefile uses `$(srctree)/drivers/dma` instead of a private
container mount path; it resolves to the same directory in the reference build.

Daemon upstream: [AsahiLinux/speakersafetyd](https://github.com/AsahiLinux/speakersafetyd).
The imported modified source is pinned by `imported-source.sha256` (paths
relative to this directory); an exact
upstream commit for this source snapshot has not yet been independently
reconstructed. Do not describe the snapshot as an unmodified upstream release.
Vendored ALSA retains its Apache-2.0/MIT licenses and crate provenance. Other
Cargo dependencies are pinned in `Cargo.lock` and remain external build inputs.

## Build and assemble

Run development builds and tests on a leased Linux build host. `build-j713-audio.sh`
requires an existing exact configured kernel, exact supplemental sound exports,
the qualified ARM64 sysroot, an offline Cargo cache and two explicit immutable
container image IDs. It checks the kernel inputs before and after compilation,
uses strict external-module modpost, and does not modify configuration or install
outputs. It builds all seven modules and the ARM64 daemon into a new directory.

```sh
bash tools/build-j713-audio.sh KERNEL_BUILD SOUND_EXPORTS SYSROOT CARGO_HOME \
  KERNEL_IMAGE_SHA256 DAEMON_IMAGE_SHA256 NEW_OUTPUT
```

Container image construction and a complete corresponding source bundle for the
historical custom kernel/toolchain are still external prerequisites. A successful
compilation alone does not authorize different binary hashes. Compare outputs
with `qualified.json`; a changed output requires its own review and qualification.
The recipe records toolchain image IDs and preserves the Rust linker map. A
fresh leased build of this packaged source reproduced all seven qualified module
hashes and the daemon hash byte for byte; this does not distribute the external
container images or complete historical kernel build inputs.

Assemble locally supplied qualified modules, daemon and FIR files in an input
directory mirroring final absolute paths, without their leading `/`. Text policy
and the profile are taken from their pinned source files here. No files are
downloaded. The readiness helper and both service templates are included here;
run the following command from this repository's root. The assembler replaces
only the old manual protection service
with the pinned readiness service; all audio/DSP payload pins stay exact.

```sh
python3 tools/package-j713-audio.py --inputs QUALIFIED_INPUTS \
  --readiness-source . --output NEW_PACKAGE --local-only
```

The historical assembler above retains its `--local-only` distribution hold.
The release assembler verifies the approved native files tracked in
[`filters/`](filters/README.md) and installs them directly in the root image.
Their provenance record distinguishes the final numerical profile from its
source inputs. The historical `qualified.json` remains unchanged evidence for
the earlier profile.

No Apple executable, tuning strip, extracted preset, raw render capture, private
user path or native log is included. The routing, UCM and WirePlumber text in
`policy/` remains original MIT-licensed configuration.

## Pending image apply/check

Run apply/check as root. The target must be an explicitly mounted ext4 image containing both
`etc/m4-root-id` and `var/lib/omarchy/provisioning/pending`. The helper rejects
live `/`, symlink traversal, mismatched board/DTB/kernel/userspace ABI, incomplete
packages, conflicting startup hooks and customized destinations. It validates
all destinations before the first write. Existing identical files are retained;
updates with different bytes require a separately reviewed migration. The one
supported transition adds the two protected-default policy files from the
27-file package without changing any prior payload. It requires the previous
manifest's exact pin through `--previous-package-sha256`, as documented in
[the protected-default guide](../../docs/j713-audio-default.md).

```sh
sudo python3 tools/configure-m4-audio.py --root MOUNTED_IMAGE --package NEW_PACKAGE \
  --package-sha256 MANIFEST_SHA256 --board apple,j713 \
  --kernel-release 7.1.9-m4-gpu-backport-20261008 --base-dtb EXACT_BASE_DTB --apply
# Use the same arguments with --check for a read-only verification.
```

Apply installs regular files, the root-owned manifest, and two system service
wanted-by links in the offline image. It does not run systemctl, load a module,
open audio, restart a session or change the host's state. At boot the one-attempt
record is retained under `/run/omarchy/j713-audio-registration`. A partial
failure stays failed for that boot; the helper has no cleanup or retry option.

## Qualification boundaries

The exact runtime manifest
`65ddf60ccf257971bcc6b611e5a9a242a6b0f79906ddb88a3a128fae360dc8c5`
completed automatic registration, protection startup and desktop discovery on a
normal reboot of the qualified J713 installation. All 27 installed payload files
and seven loaded module notes matched. Readiness observed 15.074 seconds before
publication, and subsequent video/audio playback was confirmed. The earlier
runtime completed a 20-minute mixed session with manual video changes; this is
not a 20-minute continuous-playback claim.

These observations do not establish fresh-image installation on another machine,
arbitrary repeat-boot reliability or suspend support. The image apply/check path
has filesystem-fixture coverage; it was not used to create that native
installation. See [qualification and source scope](../../docs/j713-audio.md)
for evidence identities and remaining distribution requirements. Population and
readiness fixtures model selected system boundaries and do not replace native
registration evidence.

Suspend/resume remains unqualified for this pinned supplier. A separate PM
correction is intentionally not folded into these runtime hashes. Automatic
recovery after lease loss, a changed kernel/libasound, arbitrary existing audio
configuration, firmware updates, Limine/snapshot integration and ordinary rolling
updates are not handled by this prototype. Exact ABI refusal makes such a change
visible instead of silently loading an unqualified stack. Current Omarchy package
parity is a separate installer audit, not a claim made by this audio package.

## Clean-owner default policy

The separate [protected-default policy](../../docs/j713-audio-default.md) excludes
the hidden raw speaker parent from normal default selection while preserving
existing output choices. Its source and regression tests are included here; it
is now included by the assembler in a 29-file integration candidate. The 27-file
qualified manifest above remains the historical native evidence, not evidence
for the new policy. Native component loading and selection with no saved output
remain required before this candidate is described as a durable fresh-install
fix.
