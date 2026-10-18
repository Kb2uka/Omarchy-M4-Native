# M4 Mac mini (J773g) tester kit

The plain M4 Mac mini is a `testing` board ([m4-boards.md](m4-boards.md),
#136): testers may install it, but only from a kit built for it, and only with
an explicit opt-in. This page describes that kit. The J713 release and every
live board gate keep refusing the mini.

## What it is

`tools/derive-j773g-test-kit.py` derives the kit from the J713 installer
update 4 kit (`v0.1.0-alpha.1-update.4`, `KIT.json` `9f216fd0…`). It checks
that kit's pins and every file its `SHA256SUMS` lists. The kernel, initramfs,
loader, installer, firmware collection and source bundle are reused
unchanged. The tool changes only the following:

| Part | J713 update 4 | J773g tester kit |
|---|---|---|
| Boot devicetree | `boot/portable.dtb` (`e54c77a7…`) | `boot/j773g.dtb` (`c2a4f0cc…`, [j773g-boot-dtb.md](j773g-boot-dtb.md)) |
| Installer firmware | `26.5.1` (the `j713ap` profile) | `26.6.2` (the upstream `j773gap` profile, IPSW `UniversalMac_26.6.2_25G83_Restore.ipsw`) |
| OS name and package | "…(experimental J713)", `omarchy-j713-0.1.0-alpha.1.zip` | "…(J773g tester)", `omarchy-j773g-0.1.0-alpha.1-test.1.zip` |
| `Install.command` | Mac16,12 only | Mac16,10 only, with `--install --tester` and a typed `TEST` confirmation; Widevine off |
| `START-HERE.md` | J713 instructions | [packaging/j773g-test/START-HERE.md](../packaging/j773g-test/START-HERE.md) |
| SDDM (`/etc/sddm.conf.d/99-m4.conf`, `sddm.service.d/90-m4-staging.conf`) | Requires the J713 GPU unit and the project's GPU Mesa | Starts without the GPU unit and uses the system Mesa (`llvmpipe`) on the boot framebuffer |
| Start links removed | — | `m4-j713-audio-register`, `speakersafetyd-j713`, `m4-video`, `j713-owner-assets`, `m4-channel-connect.timer` |
| `KIT.json`, `IMAGE.json`, `SHA256SUMS` | — | Regenerated, with a `j773g_test` record of the source kit, revision and root changes |

### Why the desktop starts without the GPU

The J713 GPU startup (`m4-gpu-start`) refuses any board but `apple,j713`, and
SDDM required it, so a mini would stop at a text console. The project's GPU
Mesa (`/opt/m4-mesa`) has no software renderer, while the system Mesa
(26.2.4) has `llvmpipe`. The Air ran the Omarchy desktop through `llvmpipe` on
the boot framebuffer before its GPU worked
([j713-omarchy-desktop.md](j713-omarchy-desktop.md)). The tester kit does the
same.

### Why the J713 services are not started

Each removed service has a board gate that refuses a J773g: J713 speaker
setup, video decoding, Widevine setup, and the M4 update channel connection.
None of them has a failure action, so leaving them enabled would only log
failures. The update timer would retry every 20 minutes for ever. The root
filesystem keeps their files; only the start links go. The image's
filesystem UUID is unchanged, so the installer's root identity check still
holds.

## Build

On a Linux build host with `debugfs` and `e2fsck` (e2fsprogs), the J713
update 4 installer directory (with `os/omarchy-j713-0.1.0-alpha.1.zip`) and
the J773g devicetree:

```sh
python3 tools/derive-j773g-test-kit.py --kit UPDATE4/installer \
  --dtb j773g.dtb --output /absolute/new/j773g-installer --revision "$(git rev-parse HEAD)"
python3 tools/package-j713-download.py --delivery /absolute/new/j773g-installer \
  --kit-sha256 KIT_SHA256 --checksums-sha256 SUMS_SHA256 \
  --release-tag v0.1.0-alpha.1-j773g-test.1 --output /absolute/new/transport
```

The derivation builds in a staging directory next to the output and moves it
into place only when complete. The packager accepts `-j773g-test.N` release
tags for this kit.

## Not known yet

Nobody has booted this kit on a Mac mini. The first tester boot decides
whether the HDMI framebuffer, the software-rendered desktop, Ethernet and the
radios work.
