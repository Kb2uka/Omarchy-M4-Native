# J773s (Mac mini, M4 Pro) — macOS-side survey

Read-only survey taken from the running macOS install on 2026-10-17, before any
recoveryOS step. Nothing was changed on the machine and no file was written to
it: every command streamed its output back over SSH. Raw captures (IODeviceTree
and IOService dumps, `system_profiler` JSON, disk layout) are kept privately
because they contain serial numbers and other machine identifiers; see
[recon/README.md](../recon/README.md).

Commands: `ioreg -p IODeviceTree -l -w0`, `ioreg -l -w0`,
`system_profiler -json` (hardware, displays, storage, NVMe, USB, Thunderbolt,
Wi-Fi, Bluetooth, audio, Ethernet, power), `diskutil list`, `diskutil apfs list`,
`csrutil status`.

## Identity

| Field | Value | Source |
|---|---|---|
| Model | `Mac16,11` | ADT `/model` |
| Target | `J773s` / `J773sAP` | ADT `/target-type`, `/compatible` |
| Platform | `t6040` | ADT `/platform-name` |
| chip-id | `0x6040` | ADT `/chosen/chip-id` |
| board-id | `0x02` | ADT `/chosen/board-id` |
| chip-revision | `0x11` | ADT `/arm-io/chip-revision` |
| SoC generation | `H16` | ADT `/arm-io/soc-generation` |
| arm-io compatible | `arm-io,t6041` | ADT `/arm-io` |
| Die count | 1 | ADT `/arm-io/die-count` |
| System firmware | `mBoot-18000.121.3` | ADT `/chosen/system-firmware-version` |
| macOS | 26.5.2 (25F84), kernel `RELEASE_ARM64_T6041` | `sw_vers`, IOKit build string |

The J713 Air is `T8132` running `mBoot-18000.120.36` (see
[t8132-adt-audit.md](t8132-adt-audit.md)). The mini is a different SoC, not a
T8132 board variant, so the J713 device tree does not carry over.

## Hardware seen from macOS

| Block | What macOS reports |
|---|---|
| CPU | 12 cores: 8 P (`cluster-type` P ×8) + 4 E |
| GPU | 16 cores, ADT `gpu,t6040`, 15 perf states (`gpu-num-perf-states`) |
| Memory | 24 GB |
| Storage | Apple SSD AP1024Z, 1 TB, firmware 2973.120 (ANS) |
| Ethernet | Broadcom 57762-A0 on PCIe (`AppleBCM5701Ethernet`), 1 GbE |
| Wi-Fi | `wlan-pcie,bcm4387` |
| Bluetooth | BCM_4388C2 over PCIe |
| USB / Thunderbolt | USB-C ports report up to 120 Gb/s (Thunderbolt 5); USB DRD `usb-drd,t6040` + `usb-drd,t8132`; ATC PHY `atc-phy,t6040` |
| PCIe | `apcie,t6040` (internal), `apciec,t6040` ×3 (Thunderbolt) |
| Display | DCP `dcp-expert-v1` ×3, HDMI output in use |
| Audio | built-in "Mac mini Speakers" plus HDMI |
| PMGR / MCC | `pmgr1,t6041`, `mcc,t6041` |
| SPMI PMIC | `pmu,spmi` / `pmu,abbey` |

The USB DRD node lists `usb-drd,t8132` as a fallback compatible, so the M4 Air's
USB work is the closest model for that block. Most other blocks are labelled
`t6040` or `t6041`.

## Upstream state (2026-10-17)

- **m1n1** (local checkout at `c9ef56f`) defines `T6040`/`T6041` in `src/soc.h`,
  names the M4 Pro "Brava Chop" P and E cores in `src/midr.h` and
  `src/chickens.c`, and has T6040 cases in `src/smp.c` and `src/pcie.c`
  (`apcie,t6040`). Whether it boots J773s end to end is untested.
- **Asahi kernel** (`AsahiLinux/linux`, branch `asahi`,
  `arch/arm64/boot/dts/apple/`): device trees exist for t600x, t602x, t603x,
  t8103, t8112, t8122 and t8132. There is **no t604x tree**.

## Install headroom

Internal APFS container `disk0s2` is 994.7 GB with 431 GB free. The Asahi-style
install shrinks it from macOS; the boot policy step still needs recoveryOS.

## Prior art

[damsleth/wallace](https://github.com/damsleth/wallace) (MIT; its devicetree
sources are GPL-2.0+ OR MIT) brings Linux up on the 14-inch M4 Pro MacBook Pro
(J614s, also T6040) from a kernel branch based on Asahi's `asahi-wip`. As of
its 2026-08-19 status it boots with cpufreq, Wi-Fi, Bluetooth, PCIe and a
framebuffer desktop. Two of its open blockers are SoC-level and are expected
risks here too: a fail-stop kernel fault when more than one core runs, and an
ANS firmware assert at Linux's first NVMe completion-queue wrap. The second is
worth testing against this project's T8132 fixes for the split NVMe and NVMMU
windows (patch 0013) and the ANS receive-mailbox interrupt roles (patch 0015).

## Display

The mini has no internal panel, and support for more than one monitor is
required. Every output goes through the DCP coprocessor (three `dcp-expert-v1`
nodes), so the mini shares the M4 Air's macOS 26 DCP firmware-interface gap
described in [#125](https://github.com/Kb2uka/Omarchy-M4-Native/pull/125).
Until that is solved, Linux can only use the framebuffer the boot firmware
leaves on a monitor that was connected at power-on.

## Next (needs no recoveryOS)

1. Done here: `packaging/m4-boards.json` holds more than one SoC and lists
   J773s as refused ([board table](m4-boards.md)).
2. Write a skeleton `t6040` / `t6040-j773s` devicetree from this ADT and the
   prior art above, and audit it against the ADT.
3. Build m1n1 for T6040 to have it ready for the first recoveryOS session.

The first recoveryOS step (reduced security and the m1n1 boot object) needs the
owner at the machine with a monitor and a wired keyboard.
