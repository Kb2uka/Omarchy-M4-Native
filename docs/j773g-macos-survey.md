# J773g (Mac mini, M4) — macOS-side survey

Read-only survey of a plain M4 Mac mini, posted by the reporter of
[#136](https://github.com/Kb2uka/Omarchy-M4-Native/issues/136) on 2026-10-18.
The reporter ran the survey commands in macOS and attached the output. Nothing
was installed on the machine. The raw files are kept privately because they
contain serial numbers and other machine identifiers; see
[recon/README.md](../recon/README.md). This page keeps only hardware facts.

Commands: `sysctl hw.model`, `sw_vers`, `ioreg -p IODeviceTree -l -w0`,
`ioreg -l -w0`, `system_profiler -json`, `diskutil list`,
`diskutil apfs list`, `csrutil status`.

## Identity

| Field | Value | Source |
|---|---|---|
| Model | `Mac16,10` | `sysctl hw.model`, ADT `/model` |
| Target | `J773g` / `J773gAP` | ADT `/target-type`, `/compatible` |
| Platform | `t8132` | ADT `/platform-name`, IOKit build `RELEASE_ARM64_T8132` |
| arm-io compatible | `arm-io,t8132` | ADT `/arm-io` |
| chip-revision | `0x11` | ADT `/arm-io/chip-revision` |
| SoC generation | `H16` | ADT `/arm-io/soc-generation` |
| Die count | 1 | ADT `/arm-io/die-count` |
| System firmware | `20457.1.29` | `system_profiler` boot ROM and OS loader version |
| macOS | 27.0.1 (26A434) | `sw_vers` |

This is the same T8132 SoC as the 13-inch M4 Air (J713), not the M4 Pro mini
(J773s, T6040) in [j773s-macos-survey.md](j773s-macos-survey.md). The J773s
devicetree and m1n1 work does not apply to it. `/chosen` (chip-id, board-id,
the iBoot version string) is not in this capture; see below.

## Hardware seen from macOS

| Block | What macOS reports |
|---|---|
| CPU | 10 cores: 4 P + 6 E (ADT `cluster-type`) |
| GPU | 10 cores, `gpu,t8132` |
| Memory | 16 GB |
| Storage | Apple SSD AP1024Z, 1 TB, firmware 241.0.12 |
| Ethernet | Broadcom 57762-A0 on PCIe (`AppleBCM5701Ethernet`), 1 GbE |
| Bluetooth | BCM_4388C2 over PCIe |
| USB / Thunderbolt | 3 Thunderbolt/USB4 receptacles at 40 Gb/s; 4 `usb-drd,t8132`, 4 `atc-phy,t8132`, 3 `apciec,t8132` |
| PCIe | `apcie,t8132` (internal: Wi-Fi, Bluetooth, Ethernet) |
| Display | 3 DCPs (`dcp0`, `dcpext0`, `dcpext1`, all `dcp-expert-v1`), `disp0`/`dispext0`/`dispext1` `t8132`, `dptx-phy,t8122`, `atc-dpxbar,t602x`; HDMI monitor in use |
| Audio | built-in "Mac mini Speakers" plus HDMI audio |
| Security | SIP enabled (`csrutil status`), FileVault on (`diskutil apfs list`) |

Install headroom: the internal APFS container `disk0s2` is 994.7 GB with
821 GB not allocated (`diskutil list`, `diskutil apfs list`).

## How the device tree was recovered

The IODeviceTree dump came back with only the root entry (13 lines), so it
cannot be turned into an ADT. The IOService dump (`ioreg -l -w0`) is complete
and carries the same device-tree nubs, with IOKit driver objects between them.
Dropping the driver objects and keeping their device-tree children rebuilds
the tree.

On the M4 Pro mini, where both dumps exist, this rebuild was checked against
the IODeviceTree rebuild from `tools/ioreg-adt.py` (#128): 364 nodes in common
and none with a different property. The IOService rebuild lacks `/chosen`,
`/aliases` and a few firmware sub-nubs (SEP and AOP endpoints), and lists the
CPU nodes at the root. None of those feed the MMIO audit below. `ioreg-adt.py`
itself accepts only IODeviceTree dumps today; IOService input is a follow-up
once #128 merges.

The reference remains the binary ADT that m1n1 captures at boot. That ADT also
comes from the installed stub's firmware, not from macOS 27.0.1, so values can
differ.

## Upstream state (2026-10-18)

Gravity Linux, whose installer and kernel this project builds on, targets this
exact board. Its upstream installer supported only `j773gap` (stub 26.6.2,
kept in this project's `DEVICES` table, see
[j713-installer-profile.md](j713-installer-profile.md)). Its
[early alpha for the M4 Mac mini](https://gravitylinux.org/blog/early-alpha-m4-mac-mini/)
(2026-09-18, a Fedora remix) reports DCP and GPU working (OpenGL ES 3.0 and
OpenGL 3.3) and HDMI output. It lists as not working: suspend, display output
over USB-C, Thunderbolt/USB4, reliable shutdown and reboot, normal power use,
and full speaker volume. The 13-inch Air shared most of these. This project
has since fixed shutdown ([j713-poweroff.md](j713-poweroff.md)) and speaker
protection on the Air; suspend, idle power (#92), Thunderbolt and USB-C
displays are still open there too
([release-limitations.md](release-limitations.md)).

Gravity's kernel (GravityLinux/linux, branch `gravity-m4`) carries the J773g
work. Revision `af73769`, the revision [audio/j713/README.md](../audio/j713/README.md)
names as kernel upstream, contains:

| Commit | Change |
|---|---|
| `e4be873` | DCP: recognize the 26.6.2 firmware compatibility version |
| `3c08d26` | DCP: on T8132, require firmware version 26.6.2 for DCP and DCPEXT |
| `1c37460` | J773g framebuffer on the display FE power domain |
| `bab8e96`, `9cec671`, `497a835` | T8132 audio controllers, kernel-protected J773g speaker playback, J773g speaker enabled |

The kernel this project ships does not. Its `appledrm.ko`
(`7.1.9-m4-gpu-backport-20261008`, from the alpha.1 root) lists only the
`12.3.0`, `13.3.0` and `13.5.0` DCP interfaces and has no `26.6` string; the
`"26.6.2"` comparison is exactly what `e4be873` adds. The shipped kernel was
built from the alpha.1 `kernel-controller` tree
([release-source.md](release-source.md)), not from `af73769`.

Native DCP on T8132 therefore needs both Gravity's DCP driver and the 26.6.2
stub. The Air has neither (#127). A J773g that should get HDMI through the DCP
needs a kernel built from Gravity's tree, which this project does not ship yet.

## Devicetree audit

`tools/adt-audit.py` compared the rebuilt ADT with `t8132-j773g.dtb` built
from `af73769` (`dtc` 1.8.1, SHA-256 `cf988d8c…`): 54 `MATCH`, 7 `MISMATCH`,
11 `DT-ONLY`. None contradicts a register address.

| Rows | Finding |
|---|---|
| 4 × `usb@…` `MISMATCH` | The DT lists one of the ADT's four interrupts and DART streams 0/1 where the ADT says 1/14. On the Air, hardware proved 0/1 right and the ADT's 1/14 wrong ([j713-front-usb.md](j713-front-usb.md)). Not a defect. |
| `iommu@491000000` `MISMATCH` | The second PCIe port's DART. This ADT has `dart-apcie0` and `dart-apcie2` but no `dart-apcie1`, so the address only lands inside the `apcie` window. Disabled in the DT, so harmless. |
| `iommu@390f00000`, `dma-controller@393800000` `MISMATCH` | Register windows and interrupts match. Their power domains point at the PMGR records `AUDIO_A` and `AUDIO_BASE_NS_A`, which this ADT marks virtual (flags `0x30`, virtual bit `0x10`); the audit matches only physical providers. Gravity's J773g speaker uses these nodes. Check their power-up in the first boot log. |
| `nvme@4c5cc0000` `DT-ONLY` | Mapped 0x60000 wide at the controller's secure BAR, where the ADT declares 0x10000 and the Air maps 0x28000 (`T8132_NVME_DT_WINDOW`). Over-mapped, not misplaced. |
| `sart@485c50000` `DT-ONLY` | 0x10000 wide; the ADT's `sart-ans` is 0xc000 at the same address. |
| `spmi@388a08000` `DT-ONLY`, enabled | `nub_spmi_a1` comes from `t8132.dtsi` with no status. This capture has no `nub-spmi-a1` node, but `NUB_SPMI_A1` is in the PMGR table. Check its probe in the first boot log. |
| `i2s@393600000` `DT-ONLY`, enabled | `&mca`, which Gravity's protected J773g speaker (`apple,j773g-speaker`, amplifier `ti,sn012776` on `i2c3`) uses. No ADT node sits at that address in this capture. Check its probe in the first boot log. |
| 7 other `DT-ONLY` | Disabled: the MTP keyboard and trackpad controller, its mailbox, its DART, its FIFO and the FIFO's input node, `i2c@3ad020000` and `pwm@3ad044000`. |

The audit compares addresses, interrupts, DART streams and power domains. It
does not show that a node works. Gravity's alpha booting this board is the
evidence that this devicetree starts, with Gravity's kernel.

## What stands between this board and an installer

Checked on `develop` at `55f6cb0`. The release and the update channel are
built around one board today.

**Boot devicetree.** `boot.bin` carries exactly one devicetree. On update,
`tools/m4-boot-compose.py` swaps in `usr/lib/omarchy-m4/j713.dtb` and refuses
any board other than `apple,j713` (`BOARD`) and any devicetree outside the
J713 hash lineage (`KNOWN_DTBS`). No J773g devicetree ships, and
`linux-m4-omarchy` carries none (`packaging/omarchy-m4/channel.json`). The
six J713 devicetree builders (`tools/build-j713-*-dtb.py`: poweroff, cpufreq,
cputemp, E-cluster idle, P-core boost, front USB) each pin a J713 base hash
and J713 phandles. Their SoC-level content (cpufreq, CPU idle, temperature)
should carry over; the poweroff target (the Abbey PMU), the P-boost thermal
policy (tuned for a fanless Air) and the USB port map need checking on the
mini.

**Release image.** `tools/prepare-j713-release.py` assembles the installer
from one retained J713 root (`BASE_ROOT`). `packaging/Install.command`
accepts only `Mac16,12` and requires the 26.5.1 IPSW. The bundled installer
already has the mini's profile (`j773gap`, stub 26.6.2, its IPSW entry and
recovery key; mini radios select Sakhalin firmware), so the installer side
needs the least work.

**Gates.** Every live gate refuses the mini today. They fall into three
groups:

- SoC-level, which should admit it: `m4-session-placement` (CPU layout),
  `m4-video-start`, `m4-cpu-idle` and `m4-j713-owner-assets` (Widevine).
- Board-aware, which need a J773g path: `m4-boot-compose` (devicetree
  lineage) and `m4-channel-setup` (which packages to install).
- J713 hardware, which should stay closed: `m4-lid-lock`, `m4-notch`,
  `m4-j713-audio-ready`, `m4-j713-audio-register` and `m4-headphones` (the
  mini's headphone codec is unverified).
`packaging/m4-boards.json` allows only `qualified` and `refused`, and
`tests/test_m4_boards.py` checks the gates listed in
[m4-boards.md](m4-boards.md) against those two lists. `m4-cpu-idle` and
`m4-notch` are not in that list: their own tests check the refusal with a
made-up board (`apple,j613` in `tests/test_m4_cpu_idle.py` and
`tests/test_m4_notch.py`), not with every real sibling identity.

**GPU.** `gpu/j713/asahi/g16.rs` admits any board on firmware
`mBoot-18000.161.10`; the Air path needs `mBoot-18000.120.36` and
`apple,j713`. Whether the mini's 26.6.2 stub reports `161.10` is not yet
known.

**Display.** With the shipped kernel the mini can only use the framebuffer
iBoot leaves on an HDMI monitor connected at power-on. Native HDMI and more
than one monitor need Gravity's DCP driver.

**Speaker.** The J713 speaker profile does not apply. Keep the speaker off
until the mini's amplifier (`ti,sn012776` on `i2c3`) has its own profile.

## Plan

1. **This survey.** Record the board and the audit (this page).
2. **A `testing` board status.** Today a board is either `qualified`, with
   boot evidence from that board, or `refused` ([m4-boards.md](m4-boards.md)).
   A board that remote testers may install before it has that evidence is a
   policy change; the maintainer approved it on 2026-10-18. The J713 release
   and every live gate keep refusing a testing board: only a kit built for
   that board, run with the tester's explicit opt-in, admits it.
3. **A J773g boot devicetree**, built from the same tree and SoC patches as
   the J713 one and audited the same way, with its own lineage in
   `m4-boot-compose`. Gravity's `t8132-j773g.dts` supplies the board wiring
   (framebuffer power domain, ports, speaker pins), not the SoC nodes: the
   shipped kernel's NVMe driver expects the split NVMe and NVMMU windows of
   patch 0013, which Gravity's tree maps differently.
4. **A mini tester kit**: the shipped kernel, the 26.6.2 stub, framebuffer
   HDMI, speaker off, no J713-only packages, behind the tester opt-in. Here the
   gates change for testing boards, and the board tests check each one: the
   SoC-level gates admit the mini, the board-aware gates take its J773g path
   (its devicetree lineage and package set), and the J713-hardware gates keep
   refusing it. `m4-cpu-idle` and `m4-notch` join the gate list in
   [m4-boards.md](m4-boards.md) and its tests then.
5. **First boot by the tester**, then SoC fixes in the Air's order: poweroff,
   CPU idle and boost, GPU, video decode.
6. **Kernel move** to Gravity's DCP work, for native HDMI on the mini and
   native displays on the Air (#127). This touches the Air too, so it is its
   own project.

A board becomes `qualified` only with boot evidence from that board
([m4-boards.md](m4-boards.md)).
