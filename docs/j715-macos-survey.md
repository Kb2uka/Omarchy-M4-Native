# J715 (MacBook Air 15-inch, M4) — macOS-side survey

Read-only survey of a 15-inch M4 MacBook Air (Mac16,13), made on the owner's
machine from 2026-10-15 to 2026-10-19 for
[#142](https://github.com/Kb2uka/Omarchy-M4-Native/issues/142). Nothing in this
page needed anything installed on the machine. The raw dumps are kept privately
because they contain serial numbers, MAC addresses and other machine
identifiers; see
[recon/README.md](../recon/README.md). This page keeps only hardware facts.

Commands: `sysctl hw.model`, `sw_vers`, `ioreg -p IODeviceTree -l -w0` and
`-a -l`, `ioreg -l -w0`, `system_profiler`, `diskutil list`. The board's ADT
template (`Firmware/all_flash/DeviceTree.j715ap.im4p` and `.j713ap.im4p`) was
read from Apple's `UniversalMac_27.0.1_26A434_Restore.ipsw` with HTTP range
requests; the J715 template is byte-identical to the one in this Mac's
Preboot volume.

## Identity

| Field | Value | Source |
|---|---|---|
| Model | `Mac16,13` | `sysctl hw.model`, ADT `/model` |
| Target | `J715` / `J715AP` | ADT `/target-type`, `/compatible` |
| Platform | `t8132` | ADT `/platform-name` |
| arm-io compatible | `arm-io,t8132` | ADT `/arm-io` |
| chip-revision | `0x11` | ADT `/arm-io/chip-revision` |
| SoC generation | `H16` | ADT `/arm-io/soc-generation` |
| Die count | 1 | ADT `/arm-io/die-count` |
| System firmware | `20457.1.29` | `system_profiler` boot ROM and OS loader version |
| macOS | 27.0.1 (26A434) | `sw_vers` |

The same T8132 SoC and the same chip revision as the 13-inch Air (J713).

## Hardware seen from macOS

| Block | What macOS reports |
|---|---|
| CPU | 10 cores: 4 P (`apple,everest`) + 6 E (`apple,sawtooth`) |
| GPU | 10 cores |
| Memory | 32 GB |
| Storage | Apple SSD AP1024Z, 1 TB, firmware 241.0.12 |
| Wi-Fi / Bluetooth | BCM4388 C2 over PCIe, module `kos` (J713: `garden`) |
| Display | Internal 2880 × 1864 panel |
| Speakers | Six `sn012776` amplifiers on `i2c3` (J713: four), acoustic ID 38 (J713: 37) |
| Headphones | `cs42l84` at `i2c1` 0x4b, as on the J713 |

## Devicetree audit against the J713

Compared with the J713 alpha.1 `portable.dtb` and, for audio, CPU and PMU
values, with the J713 ADT template from the same IPSW.

**SoC.** 81 of the 85 J713 `/soc` register windows sit inside a J715 ADT
node. The four exceptions are the ones the J713 audit already lists (SMC SRAM,
the two absent `i2c` controllers, the over-mapped NVMe window). The SoC-level
description carries over unchanged.

**Board wiring that differs.**

| Item | J713 | J715 |
|---|---|---|
| `compatible` / `model` | `apple,j713` | `apple,j715`, "Apple MacBook Air (15-inch, M4, 2025)" |
| Wi-Fi and Bluetooth `brcm,board-type` | `apple,garden` | `apple,kos` |
| Trackpad firmware name | `apple/tpmtfw-j713.bin` | `apple/tpmtfw-j715.bin` |
| Speaker amplifiers | 0x38, 0x3b, 0x39, 0x3c | 0x38, 0x3b, 0x39, 0x3c, 0x3a, 0x3d (the J713 order is a prefix) |

Trackpad resets (SMC `gP1c` / `gP1d`), WLAN power (`gP13`), WLAN PERST
(`gpio0` 0xa3), the speaker reset (`gpio0` pin 15), the headphone codec and the
front USB port's controller, DARTs and streams (the same ADT 1/14 that
[j713-front-usb.md](j713-front-usb.md) corrects to 0/1) match the J713.

**PMU.** The Abbey PMU at SPMI `0xe` has the J713's `pm-setting@2001` and
`rtc-offset@2100`, and the scratchpad at `0xf800` reads back with the t6030
layout (`0xf801` = 0x30 after boot), as on the J713.

**CPU.** `arm-io/pmgr` `reg[5]` = 0x2_10e20000 and `reg[16]` = 0x2_11e20000
(the E and P cluster DVFS bases). `voltage-states1-sram` (E) is 900, 1080,
1476, 1860, 2172, 2496, 2808, 2892 MHz and `voltage-states5-sram` (P) is the
same 20 states as the J713, 912 to 4464 MHz. The SMC has `TCMz`, `Tp01`,
`Tp05`, `Tp09`, `Tp0D`, `Te05` and `Te0S` (read-only key query).

**GPU.** The bring-up power value the GPU module checks,
`apple,m4-max-power-mw`, is 24552 on this unit against 24800 on the J713 unit
`g16.rs` was qualified on. m1n1 derives it from the top GPU state's voltage
(990 mV here, 1000 mV there), so it varies per chip, not per board.

**Speaker data (ADT).** `amp-tx-zd-config` is the J713's eight bytes per amp
for amps 0 and 1 and zero for amps 2–5; `speaker-config` extends the J713's
V/I slot pattern to six amps; the MCA `audio-speaker@201` keeps BCLK ratio 250
and 48 kHz with TX mask `0x3f` (6 × 32 bit) and RX mask `0xfff`
(12 × 16 bit); `bop-config` and `amp-dcblocker-config` are identical. macOS
selects `AID38/DSP/Strips/builtin_speaker_out_general_3.austrip` for this
unit's speaker ID 3; its graph feeds woofer 2 the same signal as woofer 1.

## Hardware observations

These are observations from this one J715 running Omarchy from an
unofficial kit derived from the J713 v0.1.0-alpha.1 kit with the changes above
(not this repository's release). They show what the next steps need to
re-prove from this repository's own sources.

| Area | Observed |
|---|---|
| Boot | Boots to the desktop with the J715 board devicetree and the alpha.1 kernel (dockchannel HID gate extended to `apple,j715`). Repeat boots and a fresh install worked. |
| Wi-Fi / Bluetooth | Work with the `kos` firmware the installer collects. |
| GPU | Works after the `g16.rs` board gate admitted `apple,j715` and the power gate accepted this unit's 24552 mW. |
| Keyboard, trackpad, battery | Work. Brightness keys do nothing (no backlight device, as on the J713 alpha.1). |
| Speakers | Six amplifiers play protected through a six-lane FIR from the AID38 `_3` strip, with the J713 speaker stack adapted to six amps and `i2c3` at 400 kHz. |
| Poweroff | Powers off with the PMU scratchpad cells; reboots without them, as on the J713 before its fix. |
| Front USB-C | Works with the J713 front-port fix. |
| CPU | cpufreq, CPU temperature, the E-cluster states and CPU idle work with the J713 overlays and module unchanged. A ten-core, ten-minute load reached 86 °C (CPU Die Max) on battery at 1800 / 2172 MHz, warmer than the J713's 78.6 °C. |
| Suspend | s2idle resumes, but the internal display stays black unless `dptx_phy` and `disp_cpu` stay on; USB-C and Bluetooth need rebinding after resume. |

## What stands between this board and an installer

Checked on `develop` at `75a725d`.

- **Board table.** `J715` is `refused` with no installer profile.
- **Boot devicetree.** As for the J773g ([j773g-boot-dtb.md](j773g-boot-dtb.md)),
  a J715 devicetree can be built by applying a small board overlay
  (`compatible`, `model`, `brcm,board-type`, trackpad firmware name) to the
  newest J713 devicetree. `m4-boot-compose` needs a J715 lineage.
- **Installer.** A `j715ap` profile (stub 26.5.1, the J713's IPSW) and the
  `kos` Wi-Fi and Bluetooth firmware maps.
- **GPU.** `g16.rs` admits only `apple,j713` and pins 24800 mW.
- **Speakers.** A versioned J715 profile ([m4-boards.md](m4-boards.md)): six
  amps, AID38 filters, protection settings and routing.
- **CPU boost.** The P-boost guard's tables were measured in the J713 chassis;
  the J715 runs warmer, so the boost should stay off until it is measured on a
  J715.

## Plan

1. **This survey.**
2. **`testing` status** with the `j715ap` installer profile and the `kos`
   firmware maps; the board tests check every gate against it.
3. **A J715 boot devicetree** built from the J713 one, with its own lineage.
4. **GPU**, then **speakers** (a J715 profile), each with hardware evidence.
5. **A tester kit**, then `qualified` with boot evidence from this board.
