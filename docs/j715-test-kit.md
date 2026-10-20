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
| GPU module (`/opt/m4-gpu/asahi_m4.ko` and its line in `runtime.sha256`) | `de0337a7…`, J713 only | `55831cf4…`, built from `gpu/j713` with `apple,j715` in the Air board check |
| `/usr/local/bin/m4-gpu-start` | Refuses any board but `apple,j713` | Also accepts `apple,j715` |
| New-user session placement (`/etc/skel/.local/lib/omarchy/m4-session-placement.py`) | J713 only | `tools/m4-session-placement.py`, which admits the J715 |
| Start links removed | — | `m4-j713-audio-register`, `speakersafetyd-j713`, `m4-video`, `j713-owner-assets`, `m4-channel-connect.timer` |
| Drop-ins removed | — | `10-owner-assets.conf` of `omarchy-provision-owner.service` and of `systemd-user-sessions.service` |
| Sources added | — | `source/j715/`: the kernel patch, both installer patches, `g16.rs` and `j715-board.dtso` |
| `KIT.json`, `IMAGE.json`, `SHA256SUMS` | — | Regenerated, with a `j715_test` record of the source kit, revision and changes; `kernel_native_startup_qualified` is false |

### Why the kernel changes

The built-in keyboard and trackpad driver (`dockchannel-hid`) admits only
`apple,j713`. `patches-kernel/j715-input/dockchannel-hid-accept-j715.patch`
also admits `apple,j715`; nothing else in the kernel changes. The patched
kernel is build 18's sources (the kernel controller tree with the three
`patches-kernel/j713-input` patches update 4 used) plus that patch, built with
build 18's version, user, host and timestamp. Build 18's module signing key is
not public, so a rebuild differs from build 18 in its built-in certificate as
well. The modules in the root image are unsigned and the kernel does not
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
The kit removes both drop-ins as well.

## Build

On a Linux build host with `debugfs`, `e2fsck` (e2fsprogs) and `patch`, the
J713 update 4 installer directory (with `os/omarchy-j713-0.1.0-alpha.1.zip`),
the J715 devicetree, the J715 kernel and the J715 GPU module:

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
or fuzz, and the repacked installer archive is deterministic. The packager
accepts `-j715-test.N` release tags for this kit.

## Not known yet

One J715 has run this kernel's driver change, the devicetree and the GPU
module from an earlier unofficial kit. Nobody has installed this kit on a
J715. Speakers and headphones stay off until the J715 audio delivery is
decided on #142.
