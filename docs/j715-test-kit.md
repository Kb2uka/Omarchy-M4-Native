# 15-inch M4 MacBook Air (J715) tester kit

The 15-inch M4 Air (Mac16,13) is a `testing` board ([m4-boards.md](m4-boards.md),
#142): testers may install it, but only from a kit built for it, and only with
an explicit opt-in. This page describes that kit. The J713 release and every
live board gate keep refusing the J715.

## What it is

`tools/derive-j715-test-kit.py` derives the kit from the J713 installer
update 4 kit (`v0.1.0-alpha.1-update.4`, `KIT.json` `9f216fd0…`), the same
base as the [M4 Mac mini tester kit](j773g-test-kit.md). It checks that kit's
pins and every file its `SHA256SUMS` lists. The initramfs, loader, root
filesystem identity, firmware IPSW and source bundle are reused unchanged.
The tool changes only the following:

| Part | J713 update 4 | J715 tester kit |
|---|---|---|
| Boot devicetree | `boot/portable.dtb` (`e54c77a7…`) | `boot/j715.dtb` (`84a19aba…`, [j715-boot-dtb.md](j715-boot-dtb.md)) |
| Kernel | build 18 (`c3704cfd…`) | build 18 with `patches-kernel/j715-input` (`291c4b95…`, below) |
| macOS installer | `j713ap` profile only | `packaging/j715-test/installer-j715.patch` adds the `j715ap` profile on firmware 26.5.1 and its `kos` Wi-Fi and Bluetooth firmware; four files change |
| OS name and package | "…(experimental J713)", `omarchy-j713-0.1.0-alpha.1.zip` | "…(J715 tester)", `omarchy-j715-0.1.0-alpha.1-test.1.zip` |
| `Install.command` | Mac16,12 only | Mac16,13 only, with `--install --tester` and a typed `TEST` confirmation; Widevine off |
| `START-HERE.md`, `KNOWN-LIMITATIONS.md`, `UPDATES.md` | J713 instructions, limits and update channel | The J715 versions in [packaging/j715-test](../packaging/j715-test/) |
| GPU module (`/opt/m4-gpu/asahi_m4.ko` and its line in `runtime.sha256`) | `de0337a7…`, J713 only | `55831cf4…`, built from `gpu/j713` with `packaging/j715-test/g16-j715.patch` (`apple,j715` in the Air board check) |
| `/usr/local/bin/m4-gpu-start` | Refuses any board but `apple,j713` | Also accepts `apple,j715` |
| New-user session placement (`/etc/skel/.local/lib/omarchy/m4-session-placement.py`) | `21c8ebb8…` (84a23c4), J713 only | develop's later `tools/m4-session-placement.py` (`4f535a3c…`, 594b36a, which adds the scheduler boost policy) with `packaging/j715-test/m4-session-placement-j715.patch`, which admits the J715. The J715 ran this version |
| Start links removed | — | `m4-j713-audio-register`, `speakersafetyd-j713`, `m4-video`, `j713-owner-assets`, `m4-channel-connect.timer` |
| Drop-ins removed | — | `10-owner-assets.conf` of `omarchy-provision-owner.service` and of `systemd-user-sessions.service` |
| Sources added | — | `source/j715/`: the kernel patch, both installer patches, both gate patches, the patched `g16.rs` and `j715-board.dtso` |
| `KIT.json`, `IMAGE.json`, `SHA256SUMS` | — | Regenerated, with a `j715_test` record of the source kit, revision and changes; `kernel_native_startup_qualified` is false |

### Why the gates are patched in the kit

`docs/m4-boards.md` keeps every live gate refusing a `testing` board, so
`gpu/j713/asahi/g16.rs` and `tools/m4-session-placement.py` still refuse the
J715 in the repository. The kit applies its own two patches to pinned copies
of them and checks the results. The GPU module was built from the patched
`g16.rs`, which the kit carries as `source/j715/g16.rs`.

### Why the kernel changes

The built-in keyboard and trackpad driver (`dockchannel-hid`) admits only
`apple,j713`. `patches-kernel/j715-input/dockchannel-hid-accept-j715.patch`
also admits `apple,j715`; nothing else in the kernel changes. The patched
kernel is build 18's sources (the kernel controller tree with the three
`patches-kernel/j713-input` patches update 4 used) plus that patch, built with
build 18's version, user, host and timestamp. Build 18's module signing key is
not public, so the kit kernel differs from build 18 in its built-in certificate
as well. The modules in the root image are unsigned and the kernel does not
enforce signatures (`CONFIG_MODULE_SIG_FORCE` is off), so they load either way.

The rebuild was checked without the J715 patch first. Its uncompressed `Image`
is the same size as build 18's and differs in 1091 bytes: 1071 in
`system_certificate_list` and the 20-byte GNU build ID that follows from it.
To get there the rebuild matches build 18's toolchain and environment:

- Ubuntu clang, LLD and LLVM 21.1.8 (`21.1.8-6ubuntu1`) and Ubuntu's Rust 1.93
  at its packaged path (`/usr/lib/rust-1.93`, with `rust-src` at
  `/usr/src/rustc-1.93.1`), because Rust metadata records absolute paths;
- the tree at `/job/kernel-backport`, with the archived x86-64 host tools and
  Rust libraries removed so they are rebuilt;
- `ARCH=arm64 LLVM=1 LOCALVERSION= KBUILD_BUILD_VERSION=18
  KBUILD_BUILD_USER=ubuntu KBUILD_BUILD_HOST=aa4fae7caf7b`;
- `make usr/` with `KBUILD_BUILD_TIMESTAMP="2026-10-07 12:23:00 UTC"` (the
  built-in initramfs dates from the first build of that tree), then
  `make Image.gz` with no `KBUILD_BUILD_TIMESTAMP` and `date` printing
  `Mon Oct 12 20:24:06 UTC 2026`: build 18's `version.o` carries an empty
  timestamp and only `version-timestamp.o` carries the date.

### Rebuilding the kit kernel

`tools/build-j715-kernel.sh` rebuilds the kit kernel to the bytes `KERNEL` pins
(`291c4b95…`, 18,901,137 bytes) from public sources only:

```sh
tools/build-j715-kernel.sh kernel-controller.tar.gz
```

Run it as an ordinary user in build 18's environment above, with no
`/job/kernel-backport` yet. In `m4-gravity-builder:20261008`, add `patch` first
(`apt-get install patch`); the script stops with that message without it, and
needs nothing else outside the image. It checks
`kernel-controller.tar.gz` (`810339d8…`, the alpha.1 source archive) and the
configuration (`14b4437b…`), applies the three `j713-input` patches and the J715
patch, checks both changed drivers (`dockchannel-hid.c` `6fea03db…`,
`macsmc.c` `0cbe1eaf…`), builds as above and checks the result.

The kit kernel's built-in module certificate is published as
`patches-kernel/j715-input/build-certificate.pem` (`bf669f2b…`, DER
`7a296122…`), without its private key. The script puts it at
`certs/signing_key.pem` with kbuild's saved command for that file, so the build
extracts it instead of generating a new key. This configuration signs no
modules at build time (`CONFIG_MODULE_SIG_ALL` is off), so the private key is
never needed. The key was generated for this build alone and deleted on
2026-10-22, between the two rebuilds below. The certificate grants nothing either way:
module signatures are not enforced (`CONFIG_MODULE_SIG_FORCE` is off), lockdown
is not forced (`CONFIG_LOCK_DOWN_KERNEL_FORCE_NONE`), and kexec signatures are
not forced, as in build 18.

From a fresh tree, the script reproduced `291c4b95…` byte for byte twice: with
the recipe as of `6ffbb1f`, and, after the key was deleted, as of `36a4e73`, from
the published certificate alone. Its uncompressed `Image` equals the kit
kernel's. The #155 review rebuilt it the
same way in `m4-gravity-builder:20261008` without network access. The kernel's
source join is `j715-Image-291c4b95.gz` under `kit_binaries` in
`packaging/channel-source-joins.json`.

### Why the J713 services are not started

Each removed service has a board gate that refuses a J715: J713 speaker setup
(the J715 has six amplifiers and needs its own protected profile), video
decoding, Widevine setup, and the M4 update channel connection. None of them
has a failure action, so leaving them enabled would only log failures. The
update timer would retry every 20 minutes for ever. The root filesystem keeps
their files; only the start links go. The image's filesystem UUID is
unchanged, so the installer's root identity check still holds.

The owner options service (`j713-owner-assets`) is also pulled in without
its start link: first-boot user setup `Requires` it and `Permit User Sessions`
`Wants` it, through two `10-owner-assets.conf` drop-ins. On the first J715
install with only the start link gone, the service refused the board, the user
setup failed on the dependency, and SDDM came up with no account to log in to.
The kit removes both drop-ins as well, after checking their sha256 as the
J773g tester kit does, and keeps the WirePlumber audio gate.

## Build

On a Linux build host with `debugfs`, `e2fsck` (e2fsprogs) and `patch`, the
J713 update 4 installer directory (with `os/omarchy-j713-0.1.0-alpha.1.zip`),
the J715 devicetree, the J715 kernel (`tools/build-j715-kernel.sh`, above) and
the J715 GPU module:

```sh
python3 tools/derive-j715-test-kit.py --kit UPDATE4/installer \
  --dtb j715.dtb --kernel Image.gz --gpu-module asahi_m4-55831cf4.ko \
  --output /absolute/new/j715-installer --revision "$(git rev-parse HEAD)"
python3 tools/package-j713-download.py --delivery /absolute/new/j715-installer \
  --kit-sha256 KIT_SHA256 --checksums-sha256 SUMS_SHA256 \
  --release-tag v0.1.0-alpha.1-j715-test.1 --output /absolute/new/transport
```

The derivation builds in a staging directory next to the output and moves it
into place only when complete. The installer patch must apply without offset
or fuzz, and the repacked installer archive is deterministic. Two derivations
from the same inputs and revision give the same bytes (#163): debugfs runs with
`E2FSPROGS_FAKE_TIME`, so every inode, directory and the superblock it writes
carry `REPRODUCIBLE_TIME` (update 4's OS ZIP date, 2026-10-17 12:01:16) instead
of the clock, and the tool refuses a debugfs that ignores it; the OS ZIP members
carry the same date. The packager accepts `-j715-test.N` release tags for this
kit.

## Not known yet

One J715 has installed this kit (KIT.json `6d6f8f7f…`, from an earlier
revision of this branch). The kit derived now differs from it only in its
documents and the two gate patches in `source/j715/`: its root has the same
files, modes, links and contents. First-boot user
setup, the desktop with GPU acceleration, the built-in keyboard and trackpad,
Wi-Fi with the `kos` firmware, both USB-C ports, restart and shut down worked,
Bluetooth started, and no system unit failed. WirePlumber does not start: update 4's
`/etc/systemd/user/wireplumber.service.d/80-j713-audio-ready.conf` runs the
J713 speaker readiness gate, which refuses the J715, so Bluetooth and USB
audio are off too. The kit keeps that gate. Speakers and headphones stay off
until the J715 audio delivery is decided on #142.
