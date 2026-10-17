# J773s devicetree skeleton

The first devicetree for the Mac mini (M4 Pro, 2024), J773s, SoC T6040, is in
[`dts/t6040/`](../dts/t6040/). It is written for a first m1n1-chainloaded
boot to a framebuffer console, and nothing more. It has not booted.

## Sources

- The J773s ADT, from the macOS-side survey of 2026-10-17
  ([j773s-macos-survey.md](j773s-macos-survey.md)). macOS shows it as text
  (`ioreg -p IODeviceTree -l -w0`). `tools/ioreg-adt.py` rebuilds that text into
  a binary ADT, so this repository's `tools/adt.py` and `tools/adt-audit.py`
  node audit, and m1n1's `pmgr_adt2dt.py`, can read it. The rebuild is not
  byte-exact (see the tool's header). Three properties lost their binary layout
  in the text: `bluetooth.function-bootstrap_lock`, `pmgrtools.interrupts` and
  `nub-spmi0/btm.interrupts`. None of them is used here.
- [damsleth/wallace](https://github.com/damsleth/wallace) at `55f116d`: a T6040
  bring-up on the 14-inch MacBook Pro (J614s) whose devicetree was checked
  against that machine's ADT and booted. `t6040.dtsi` starts from its
  `dts/t6040.dtsi`; `t6040-j773s.dts` follows its `t6040-j614s.dts` and
  Asahi's `t8132-j773g.dts` (the M4 Mac mini).
- m1n1 at `c9ef56f` for how CPU nodes are matched and pruned.

## What is enabled

| Node | Why |
|---|---|
| 16 CPU nodes and `cpu-map` | m1n1 starts the cores and prunes the absent ones |
| timer, `clock-ref` | Required |
| AIC (`apple,t6040-aic3`, `apple,t8122-aic3`) | Interrupts |
| PMGR blocks and 213 power domains | Every other block needs them |
| Watchdog (`apple,t6040-wdt`, `apple,t8103-wdt`) | Restart works without SMC |
| `uart0` | `stdout-path`, as on every Asahi board; no console is expected on it |
| `framebuffer0` | Left `disabled` for m1n1 to fill in and enable |
| memory | Filled in by m1n1 (ADT `dram-base` 0x10000000000) |

Disabled, described from the ADT: ANS mailbox, SART and NVMe; SMC with its
GPIO, RTC and reboot children; the primary SPMI bus and its abbey PMU; four
USB DART pairs and four DWC3 controllers. Not described at all: PCIe (the
Wi-Fi/Bluetooth module and the Broadcom 57762 Ethernet), the DockChannel UART,
the Thunderbolt/ATC PHYs, DCP and display, GPU, audio and cpufreq.

The compiled DTB, with `dtc` 1.8.1 and the pinned Asahi `dt-bindings`
headers, gives 0 warnings (SHA-256 `60dff154…f694`). `dtbs_check` (schema
validation in a kernel tree) has not been run.

## Audit against the ADT

The node audit of `tools/adt-audit.py` (reg, interrupts, IOMMU stream IDs and
power domains of every DT node with MMIO) ran on the compiled DTB and the rebuilt
J773s ADT: **24 match, 1 mismatch**. The mismatch is the SMC SRAM window
(`0x50de70000`), which no ADT lists (below). The tool's PMGR section expects the
T8132 `ps-regs` layout, which T6040 lacks, so the domains were checked by
regenerating them instead.

## Differences from J614s

| Area | J773s ADT | J614s (wallace) | In the skeleton |
|---|---|---|---|
| CPUs | cpu-ids 0-3 E (reg 0-3), 4-7 P (reg 0x100-0x103), 10-13 P (reg 0x200-0x203); 12 cores | cpu-ids 0-3, 4-8, 10-14; 14 cores; wallace lists 15 nodes with one fused placeholder | All 16 positions: E 0x0-0x3, P 0x10100-0x10105, P 0x10200-0x10205 |
| L2 | E 0x400000, P 0x1000000 | Same | Same |
| PMGR | 213 domains | 214 domains | J773s set (below) |
| USB | `usb-drd0..3`, `dart-usb0..3`; drd3 at 0x39a280000, IRQ 1715; DART IRQ 1719 | Three of each | Four pairs |
| USB DART stream IDs | Each mapper: reg 1, sid `<1 14>` | `<0>` and `<1>` | `<1>`, `<14>`, as the M4 Air needed ([patch 0012](../patches/0012-fix-dts-t8132-use-ADT-proven-USB-DART-stream-IDs-1-1.patch)) |
| Keyboard and trackpad | No MTP node | MTP over DockChannel | Not described |
| Keyboard backlight | No fpwm; `pwm1` is something else | `fpwm0` | Not described |
| Display | `dispext0..2` and `lpdptx-phy0` (HDMI) behind `display-crossbar0`; `dp2hdmi-gpio0` | Internal `disp0` plus `dispext0..1` | Framebuffer domains `dispext0_cpu` and `dptx_phy` |
| DockChannel UART | `0x508828000`, IRQ 360 | ADT 360, measured at AIC 816 | Not described |
| PCIe | Port 0: `wlan-pcie,bcm4387` Wi-Fi and Bluetooth; port 2: `pci14e4,1682` (Broadcom 57762, 1 GbE) | BCM4388 Wi-Fi and Bluetooth, GL9755 SD reader; no Ethernet | Not described |

Equal on both boards, by ADT: AIC (0x502400000, size 0x1cc000, IACK +0x40000);
watchdog (0x50836c000, IRQ 827); `uart0` (0x429200000, IRQ 1559); ANS
(0x409600000), NVMMU (0x40dcc0000), NVMe (0x44dcc0000) and SART (0x40dc50000),
interrupts `[1531 1530 1533 1532 2583]`; SMC (0x50c600000), interrupts
`[997 996 999 998]`; `nub-spmi0` (0x509014000) with the abbey PMU at SID 0xe
and `info-pm_setting` 0x2001, `info-rtc_scrpad` 0x2100, `info-leg_scrpad`
0xf800.

### Power domains

`t6040-pmgr.dtsi` is m1n1's `pmgr_adt2dt.py` output for the rebuilt J773s ADT.
Of the domains both boards have, all 209 have the same block, offset, parents
and `apple,always-on` flags as wallace's J614s file. J773s lacks `disp_cpu`,
`disp_fe`, `disp_sys`, `isp_sys` and `ispsens0`, and adds `dispext2_cpu`,
`dispext2_fe`, `dispext2_sys` and `dptx_phy`. The generator gives
`dispext{0,1,2}_sys` a second parent, `dispext_rail_v`, a virtual rail with no
power-state register and so no node; that reference was removed, as in
wallace's file. The `pmgr1` block is 0x10000 long rather than J614s's 0x14000
because its highest domain is lower.

### CPUs

m1n1 stores the ADT CPUs by `cpu-id`, then walks the DT's CPU nodes in order
and pairs the Nth node with CPU N. A node whose CPU is not running is removed,
and current m1n1 removes its `cpu-map` entry too (`kboot.c`, `dt_set_cpus`). The
skeleton therefore lists the full die, at the `cpu-id` positions, and leaves
binning to m1n1: on J773s positions 8, 9, 14 and 15 are pruned. On J614s, with
the same SoC, wallace's m1n1 log shows the ADT-to-MPIDR mapping this relies on
(E 0x0-0x3, P 0x10100-0x10104 and 0x10200-0x10204, and
`FDT: CPU 9 is not alive, disabling...`). The J773s MPIDRs themselves have not
been read; a wrong one stops m1n1 with an MPIDR-mismatch message before Linux
starts.

## Not verifiable from the macOS dump

These need m1n1's binary ADT capture and its log, at the first recoveryOS
session:

- The CPU MPIDRs, and that m1n1 starts 12 cores.
- Which `dispext` drives HDMI at boot. `dispext0` carries the boot display's
  timing in the capture; the domains follow `t8132-j773g.dts`. If they are
  wrong, the screen goes dark when Linux turns off unused domains;
  `pd_ignore_unused` on the command line avoids that while it is checked.
- The SMC SRAM window. On J614s it was measured from an RTKit buffer request,
  not read from the ADT. SMC stays disabled.
- The DockChannel UART interrupt. J614s's ADT gave 360 and measured 816; J773s's
  ADT also gives 360.
- Which `usb-drdN` serves which physical port, and which is m1n1's tether port.
- Byte-exact properties: a text rebuild can change values that look like strings.

## Inherited risks

Both are T6040 problems on J614s, so J773s should be expected to hit them:

1. **More than one core faults.** With several cores running, a kernel
   page-copy fault kills processes (fail-stop; wallace found no data
   corruption). Its persistent system runs one core. The missing-barrier theory
   was ruled out; page lifetime or TLB invalidation are the open suspects. Plan
   the first boots with `nosmp` or `maxcpus=1`, as the M4 Air's first boots
   used `nosmp`.
2. **NVMe asserts at the first completion-queue wrap.** Linux on J614s trips
   an ANS firmware assert when the first I/O completion queue wraps; m1n1's raw
   reads survive several wraps. NVMe stays disabled here. It may share a cause
   with two M4 Air fixes, the split NVMe and NVMMU windows
   ([patch 0013](../patches/0013-fix-dts-t8132-split-M4-NVMe-and-NVMMU-windows.patch)) and the
   swapped ANS receive interrupts
   ([patch 0015](../patches/0015-fix-dts-t8132-ans-receive-irq-roles.patch)); Jev rated that
   0.67 as worth testing. Untested.

## Advisory grading (Jev)

`jev-latest`, two rounds, with false claims mixed in. Round 1 controls: MTP on
J773s 0.02, NVMe enabled 0.04, five-core P clusters 0.17. Round 2 added the
wallace MPIDR log; controls: NVMe enabled 0.03, E cores at 0x10000 0.04,
five-core P clusters 0.31 (higher than round 1, so round 2 is weaker).

| Claim | Round 1 | Round 2 |
|---|---|---|
| PMGR matches J614s for shared domains | 0.85 | — |
| USB stream IDs should be `<1 14>` | 0.88 | — |
| Framebuffer domains are unproven | 0.71 | — |
| Core addresses equal J614s | 0.59 | 0.50 |
| The 16-position CPU list brings up all 12 cores | 0.44 | 0.48 |
| Status: ready for a first controlled boot / needs the live ADT first | 0.50 / 0.49 | 0.48 / 0.51 |

Jev is advisory. Its doubt on the CPU list matches the open item above, and the
split on readiness is resolved by order: the first session runs m1n1 in proxy
mode, captures the binary ADT, re-runs this audit on it, and only then boots
Linux.
