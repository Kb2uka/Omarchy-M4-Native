# Apple T8132 device-tree gap analysis

> Historical research record. For the final checkpoint and reproduction
> limits, read [the project README](../README.md). Raw machine captures and
> bench binaries referenced below are not part of the public tree.

## Scope and method

This analysis compares the checked-out `t8132.dtsi` baseline with the J713
IODeviceTree dump and with the existing T8112/T8122 device trees. The dump is
the hardware authority. The other SoC trees are used only to identify Linux DT
interfaces and compatible fallbacks that already have driver precedent.
Because `drivers/` is not present in this sparse checkout, the relevant
sources were fetched read-only from the checkout's pinned Asahi tag. The
terminal, driver-bound compatible in a chain used by the T8112 or T8122 trees
is the evidence for driver availability; the SoC-specific first entry is not
evidence of a driver match. The verified match tables used for this patch are:

- mailbox: `apple,asc-mailbox-v4`, `apple,t8015-asc-mailbox`,
  `apple,m3-mailbox-v2`;
- SART: `apple,t6000-sart`, `apple,t8103-sart`, `apple,t8015-sart`;
- NVMe: `apple,nvme-ans2`, `apple,t8103-nvme-ans2`,
  `apple,t8015-nvme-ans2`;
- SMC: `apple,smc`, `apple,t8103-smc`;
- SPI: `apple,t8103-spi`, `apple,spi`;
- DWC3 Apple glue: `apple,t8103-dwc3`;
- ATC PHY: `apple,t8103-atcphy`, `apple,t8122-atcphy`;
- DART: `apple,t8103-dart`, `apple,t8103-usb4-dart`,
  `apple,t8110-dart`, `apple,t6000-dart`;
- PCIe: `apple,t6020-pcie` in
  `drivers/pci/controller/pcie-apple.c:992-995`;
- SEP: `apple,sep`;
- SPMI controller: `apple,spmi` in
  `drivers/spmi/spmi-apple-controller.c:475-478`;
- SPMI NVMEM: `apple,spmi-nvmem` in
  `drivers/nvmem/apple-spmi-nvmem.c:60-62`.
- SN201202x USB-PD: `apple,sn201202x` in
  `drivers/usb/typec/tipd/spmi.c:155-158`;
- display crossbar mux: `apple,t8112-display-crossbar` in
  `drivers/mux/apple-display-crossbar.c:431-449`.
- RTKit helper: `apple,rtk-helper-asc4` in
  `drivers/soc/apple/rtkit-helper.c:133-136`;
- DockChannel controller: `apple,dockchannel` in
  `drivers/soc/apple/dockchannel.c:386-389`;
- DockChannel HID: `apple,dockchannel-hid` in
  `drivers/hid/dockchannel-hid/dockchannel-hid.c:1194-1197`.
- SIO DMA: `apple,sio` in `drivers/dma/apple-sio.c:908-912`.
- AOP: `apple,t6020-aop` in `drivers/soc/apple/aop.rs:972-981`;
- AOP audio: `apple,t6030-aop-audio` in
  `sound/soc/apple/aop_audio.rs:952-960`;
- AOP ALS: `apple,aop-als` in
  `drivers/iio/common/aop_sensors/aop_als.rs:102-107`;
- AOP LAS: `apple,aop-las` in
  `drivers/iio/common/aop_sensors/aop_las.rs:29-34`;
- ADMAC: `apple,t8122-admac` in `drivers/dma/apple-admac.c:965-970`.
- CS42L84 codec: `cirrus,cs42l84` in
  `sound/soc/codecs/cs42l84.c:1097-1100`;
- SN012776 amplifier: `ti,sn012776` in
  `sound/soc/codecs/tas2764.c:1061-1065`.

The USB entries were verified directly against the Asahi branch versions of
`drivers/usb/dwc3/dwc3-apple.c`, `drivers/phy/apple/atc.c`, and
`drivers/iommu/apple-dart.c`, fetched on 2026-08-31. The ATC source also shows
that all five named register resources are mapped unconditionally.
The SEP match was verified against `drivers/soc/apple/sep.rs` from the same
branch and date; its OF table directly matches `apple,sep`.
The SN201202x and display-crossbar matches were verified against
`drivers/usb/typec/tipd/spmi.c` and `drivers/mux/apple-display-crossbar.c`
from the same branch and date. The USB-PD binding was also checked at
`Documentation/devicetree/bindings/usb/apple,sn201202x.yaml`.
The PCIe terminal and its generation split were verified against
`drivers/pci/controller/pcie-apple.c`: the T602x register offsets are at
lines 134-184, named per-port and PHY resources are selected at lines
675-689, and the OF match table is at lines 992-995.
The MTP terminals and resource names were verified against the same pinned
Asahi source. The helper maps resources named `asc` and optional `sram` at
`drivers/soc/apple/rtkit-helper.c:94-103`. The DockChannel controller maps
`irq` at `drivers/soc/apple/dockchannel.c:332-363`; the `config`/`data`
mapping, the named `tx`/`rx` interrupts, and `apple,fifo-size` are consumed
by `dockchannel_init()` in `drivers/soc/apple/dockchannel.c:213-264` (run on
the child device), and the HID transport consumes `apple,helper-cpu` at
`drivers/hid/dockchannel-hid/dockchannel-hid.c:1157-1166`.
The SIO terminal and resource contract were verified in the same tree:
`drivers/dma/apple-sio.c:809-811` maps only resource index 0, and its OF table
matches `apple,sio` at lines 908-912. The supporting terminals are
`apple,asc-mailbox-v4` in `drivers/soc/apple/mailbox.c:444-450` and
`apple,t8110-dart` in `drivers/iommu/apple-dart.c:1627-1634`.
The AOP driver maps fixed-size `0x1e0000` AOP and `0x4000` ASC resources at
`drivers/soc/apple/aop.rs:63-66,995-999`; it recognizes announced services
named only `aop-audio`, `las`, and `als` at lines 753-774. The audio, ALS, and
LAS child drivers bind the terminal compatibles cited above. The ADMAC driver
requires `dma-channels` at lines 817-842 and maps only resource index 0 at
lines 861-864.

The terminal fallbacks selected below are `apple,asc-mailbox-v4`,
`apple,t6000-sart`, `apple,t8103-nvme-ans2`, `apple,t8103-smc`, and
`apple,t8103-spi`. The USB/ATC slice uses `apple,t8103-dwc3`,
`apple,t8122-atcphy`, and `apple,t8110-dart`. The SEP slice uses
`apple,asc-mailbox-v4`, `apple,t8110-dart`, and the directly matched
`apple,sep`. The SPMI/PMU slice uses the directly matched `apple,spmi` and
`apple,spmi-nvmem` terminals. The PCIe slice uses `apple,t6020-pcie` and
`apple,t8110-dart`. The MTP slice uses `apple,rtk-helper-asc4`,
`apple,asc-mailbox-v4`, `apple,t8110-dart`, `apple,dockchannel`, and
`apple,dockchannel-hid`; `apple,mtp` is retained from the T8112/T8122 chain
but is not itself the helper driver's match token. This does not by itself
prove that a changed T8132 hardware revision is compatible.
The SIO slice uses `apple,sio`, `apple,asc-mailbox-v4`, and
`apple,t8110-dart` as its three driver-bound terminals.
The AOP slice uses `apple,t6020-aop`, `apple,t6030-aop-audio`,
`apple,aop-als`, `apple,t8122-admac`, `apple,asc-mailbox-v4`, and
`apple,t8110-dart` as its driver-bound terminals. `apple,aop-las` is bound but
is not instantiated because the T8132 ADT has no LAS service child.

A future upstream submission also needs companion devicetree-binding updates:
the `apple,asc-mailbox`, `apple,smc`, `apple,sart`, `apple,nvme-ans2`, and
`apple,spi` YAML compatible enums do not yet contain T8132 entries and are
outside this sparse checkout. The SPMI controller and PMIC schemas
(`spmi/apple,spmi.yaml`, `nvmem/apple,spmi-nvmem.yaml`) need
`apple,t8132-spmi` and `apple,abbey-pmic` entries — note the controller
binding directs new SoCs to the `apple,t8103-spmi` fallback family, which
this tree now uses. The DWC3, ATC PHY, and DART bindings likewise
need T8132 enum updates before the USB/ATC slice is submitted upstream. SEP
binding coverage is also needed for its T8132-specific compatible.
The PCIe binding likewise needs `apple,t8132-pcie` and a three-port T602x
layout: its current T6020 conditional requires ten register names even though
this ADT has three ports and therefore eight Linux resources.
The SIO DMA schema also needs `apple,t8132-sio` added to its compatible enum.
The AOP, AOP child, ADMAC, mailbox, and DART schemas likewise need the new
T8132-specific first entries before an upstream submission.

The dump serializes integer cells little-endian. `arm-io` declares two address
and two size cells (lines 1292 and 1311). The first `ranges` tuple on line 1300
decodes as child `0x0`, parent `0x2_00000000`, size `0x3_a0000000`.
Consequently the applicable child addresses below are translated by adding
`0x2_00000000`. This is cross-checked by `uart0`: its dump register is
`0x1_ad200000` (lines 4984-4990), which becomes the existing Linux node
`serial@3ad200000`.

The USB complexes are exceptions covered by later identity tuples in the same
`ranges` property, not by the first tuple. Tuple 1 is child/parent
`0x4_00000000`, size `0x04074000`, and tuple 2 is child/parent
`0x4_08000000`, size `0x04074000`. They cover ports 0 and 1 respectively.
The decoded `reg` values equal the independently reported `IODeviceMemory`
addresses for each port, confirming that no additional offset is applied.

Interrupt byte strings are decoded as arrays of little-endian 32-bit values.
For example, ANS `<ec030000 eb030000 ee030000 ed030000 fa030000>` is
`1004, 1003, 1006, 1005, 1018`; `nvme-interrupt-idx = 4` selects 1018
(lines 4634-4654).

“Baseline” in the status column means the T8132 tree before this patch.

## Gap matrix

| Functional block | J713 ADT node and evidence | T8132 DT status | Nearest driver-backed precedent | T8132 power domain | Verdict |
|---|---|---|---|---|---|
| AIC, PMGR, watchdog, GPIO, I2C, UART, FPWM | `arm-io`, `pmgr@80700000`, `uart0@AD200000`; mapping evidence at lines 1287-1312 and 4984-4996 | Present; I2C/UART/FPWM are disabled in the SoC file and selected by boards | Existing T8132 nodes already fall back to T8122/T8103 interfaces | Yes: existing `ps_aic`, GPIO, I2C, FPWM and UART domains | Present; no change |
| ANS protection/IOMMU | `ans@81600000` points to `sart-ans` through `iommu-parent` (lines 4632-4664); `sart-ans@85C50000` has three explicit register ranges, `sart,coastguard`, and SART version 3 (lines 4825-4836). There is no `dart-ans` node in the dump | Baseline absent | T8122 `apple,t8122-sart`, fallback `apple,t6000-sart` | Yes: `ps_ans` | Added SART with the bound T6000 fallback, whose v3 operations match the ADT. No ANS DART was added because this machine describes SART, not DART, for storage |
| ANS mailbox and NVMe | `ans@81600000`: wrapper/register list at line 4664, IRQs at line 4654, NVMe IRQ index at line 4634, queue depth 64 and linear SQ at lines 4638-4642. The NVMMU range decodes to `0x4_85cc0000/0x60000` and the M4 controller BAR to `0x4_c5cc0000` (ADT reg[9]); wrapper base is `0x4_81600000` | Baseline absent; now disabled in SoC DT and enabled only on J713 | T8122 `apple,t8122-asc-mailbox` + `apple,asc-mailbox-v4`; `apple,t8122-nvme-ans2` + `apple,t8103-nvme-ans2` (T8122 lines 1059-1095) | Yes: `ps_ans`, `ps_apcie_st`; both exist and the latter depends on `ps_ans` | Added. Mailbox is the ASC wrapper base plus the established `0x8000` mailbox window used by T8112/T8122; IRQs are the first four ADT entries. NVMe uses ADT IRQ 1018; since patch 0013 it binds only `apple,t8132-nvme-ans2` (M4 split NVMe/NVMMU windows — see the night-2 section), no T8103 fallback |
| SMC mailbox/core | `smc@8C600000`: IRQs at line 4844; wrapper/register list at line 4846. Firmware SRAM is `region-base = 0x3_8de00000`, size `0x100000`, at lines 4869-4873 | Baseline absent; now disabled in SoC DT and enabled only on J713 | T8122 `apple,t8122-smc` + `apple,t8103-smc`; T8122 ASC mailbox interface (T8122 lines 833-877) | Not required by the precedent | Added. Mailbox is the established ASC `+0x8000` window and uses decoded IRQs 562, 561, 564, 563 |
| SPMI controller and main PMU | `nub-spmi0@88714000` and `pmu-main@E` (lines 5809-5955); the controller tuple translates to `0x3_88714000`, and the PMU identifies Abbey, USID `0xe`, plus explicit NVMEM offsets | Added with an ADT-derived fixed NVMEM layout | T8112 SPMI/Stowe structure; driver-bound `apple,spmi` and `apple,spmi-nvmem` terminals | No explicit domain required by precedent | Added the `0x100` controller window, Abbey PMIC, `pm_setting`, `rtc_offset`, `fault_shadow`, and full ADT `socd` region. Controller interrupts are omitted as in T8112; the driver polls when its optional IRQ is absent |
| SMC services | Dump exposes the SMC and SMC PMU/charger clients (lines 4839-4941) | Baseline absent | T8112/T8122 `apple,smc-gpio`, `apple,smc-hwmon`, `apple,smc-rtc`, and `apple,smc-reboot` children | Not required by the precedent | Added GPIO and hwmon children, plus J713 laptop hwmon keys. RTC is now wired to the ADT-proven `rtc_offset@2100`. Reboot remains withheld because `shutdown_flag`, `boot_stage`, `boot_error_count`, and `panic_count` are not proven for Abbey; `pm_setting@2001` alone is insufficient. Battery/charger power supplies are instantiated by the SMC stack rather than separate DT children in the T8112/T8122 precedent |
| CPU frequency scaling | PMGR contains two clusters (`6, 4`), performance-domain data, voltage tables, and `perf-regs` (lines 4091-4157), but no discrete cpufreq node/register tuple | Absent | T8122 `apple,t8122-cluster-cpufreq`, fallback `apple,t8112-cluster-cpufreq` at `cpufreq@210e20000`/`@211e20000` | No explicit domain required by precedent | Blocked: the T8122 addresses cannot be assumed for T8132, and the dump does not expose an unambiguous pair of cpufreq MMIO ranges |
| Dockchannel UART | `dockchannel-uart@88128000`, IRQ 496, registers `0x3_88128000/0x10000` and `0x3_8810c000/0x1000` (lines 5022-5035) | Absent | Nearest is T8112/T8122 `apple,*-dockchannel`, fallback `apple,dockchannel`, but those nodes model the MTP FIFO/HID layout rather than this UART channel | No explicit domain required by precedent | Blocked: node exists and values are known, but no matching T8112/T8122 DT interface for the UART layout was found |
| MTP / keyboard / trackpad | `mtp@94600000`, `dockchannel-mtp@94B00000`, and `dart-mtp@94800000` (lines 5041-5058, 5184-5264) | Added as disabled SoC nodes; enabled only on J713 | T8112/T8122 MTP helper, ASC mailbox and DART, plus `apple,dockchannel` and `apple,dockchannel-hid` | No domain is required: the MTP ADT node explicitly has empty `power-gates` and `clock-gates` arrays (lines 5188 and 5203), the same self-gated shape followed by the T8112 cluster | Added. Both MTP and HID consumers use ADT-proven SID 0, and J713 supplies its firmware, reset GPIOs, keyboard properties and alias |
| SPI | `spi2@AD108000`, IRQ 1056, `0x3_ad108000/0x4000`, clock ID `0x1a4` (lines 1315-1335); `spi4@AD110000`, IRQ 1058, `0x3_ad110000/0x4000`, clock ID `0x1a6` (lines 1373-1389) | Added; both controllers are disabled | T8122 `apple,t8122-spi`, terminal fallback `apple,t8103-spi` | Yes: `ps_spi2`, `ps_spi4` | Added in the SPI slice with the ADT-backed 24 MHz `clkref`; no pinctrl properties because the dump does not identify CLK/MOSI/MISO pins. Children remain omitted: Mesa is SEP-gated with no driver, and DP855 has no driver |
| USB/ATC ports 0 and 1 | `atc-phy0@2A90000`, `dart-usb0@2F00000`, `usb-drd0@2280000` (lines 5418-5558); matching port 1 nodes at lines 5613-5754. J713's HPM controllers are SPMI `usbc,sn201202x,spmi` children (lines 6082-6179), not I2C CD321x devices | SoC units, role/orientation switching, connector endpoints and crossbars are described; DWC3 and all four DARTs remain disabled by default and are enabled for J713 | T8122 `apple,t8122-dwc3` + terminal `apple,t8103-dwc3`, `apple,t8122-dart` + terminal `apple,t8110-dart`, driver-bound `apple,t8122-atcphy`, `apple,t8112-display-crossbar`, and `apple,sn201202x` | Yes: `ps_atc0_usb`, `ps_atc1_usb`, `ps_nub_spmi_a0`, and the ATC parent domains | Complete for J713: `nub_spmi_a0`, HPM0/HPM1/HPM5, dual-role connector graphs, DWC3 controllers and all four USB DARTs are enabled; no unrelated board nodes are enabled |
| SIO cluster | `sio@ADE00000` and `dart-sio@AD060000` (lines 4533-4630); the SIO mapper is SID 0 and PMGR gate ID `0x123` is `SIO-CPU-V` | Added as three disabled SoC nodes; no board enables them | T8112 `apple,t8112-sio` + terminal `apple,sio`, ASC mailbox v4, and T8110 DART | Yes: ADT gate `0x123` maps to existing `ps_sio_cpu`, wired to SIO, mailbox, and DART | Added. SIO maps only the leading `0x4000`, leaving the `+0x8000` mailbox disjoint; the second ADT range is omitted because the driver has no second resource |
| Audio architecture / codecs | No MCA-style I2S controller exists. `iop-audio-controller` is `iop-audio,controller`/`AOPAudioService` (lines 7531-7538); `audio-hp` is `iop-adma-stream,leap` and selects leap-ns (lines 7540-7549), while `audio-speaker` selects base-ns (lines 7623-7632). The CS42L84 is at i2c1/`0x4b` (lines 1521-1545), and four SN012776 amps are at i2c3/`0x38,0x39,0x3b,0x3c` (lines 1574-1630) | The AOP and leap-ADMAC portion is DT-ready and added disabled in this slice; codec I2C children remain for a future board slice | Linux has AOP, AOP-audio, ADMAC, CS42L84 and TAS2764/SN012776-compatible codec drivers, but the pinned tree has no match or driver for `iop-audio,controller` | AOP itself has empty gate arrays; leap ADMAC's gate `0x156` is `AUDIO-LLT-V`, with no represented provider | BLOCKED ON DRIVER WORK: neither speaker nor headphone playback can be completed in DT until the `iop-audio,controller` transport exists. Codec children can be added with future board wiring once that transport is available |
| AOP / sensors / low-power audio | `aop@90600000` registers/IRQs/children (lines 3848-3987), `dart-aop@90F00000` and mappers (lines 3988-4080), plus `admac-leap-ns@93300000` (lines 7756-7779) | Added as four disabled SoC nodes; no board enables them | T8112/T8122 AOP mailbox/DART/ADMAC/AOP cluster; terminals are ASC mailbox v4, T8110 DART, T8122 ADMAC, T6020 AOP, T6030 AOP audio, and generic AOP ALS | AOP has empty clock/power gate arrays and DART has none; no AOP domain is required by precedent. ADMAC gate `0x156` has no provider in `t8132-pmgr.dtsi` | Added. AOP uses SID 0, leap ADMAC uses SID 10 and the ADT's 9 channels. Children map `aop-audio` and ALS; LAS, accel, gyro, and voice-trigger are omitted for lack of matching T8132 evidence/interface |
| SEP | `sep@AA600000` exposes `0x3_aa600000/0x88000`, a second `0x3_aa050000/0x60000` range, and IRQs 390, 389, 392, 391 (lines 4385-4412); `dart-sep@A8C80000` exposes `0x3_a8c80000/0x20000`, IRQ 399, and three SIDs (lines 4453-4500) | Added; the SEP consumer is disabled, while its DART and mailbox follow the precedent's status behavior | T8112 `apple,sep`, DART, and ASC mailbox. T8122 contains no SEP cluster; its DART nodes retain the same leading `0x4000` mapping policy | Yes: always-on `ps_sep`, though the SEP precedent has no `power-domains` property | Added with driver-bound terminals. The DART maps its leading `0x4000`; the SEP wrapper uses the full first ADT range. The second ranges and SID 1 MPM mapping are omitted because the Linux precedent consumes neither; the exclave manager and SID 2 are out of scope |
| Internal display / DCP | `dcp@AE00000`, `dart-dcp@A340000`, `dart-disp0@A300000` (lines 6238, 6285, 6332), with multiple external-display DARTs at lines 6504-6751 | Only bootloader simple-framebuffer exists; DCP absent | T8112 `apple,t8112-dcp` + `apple,dcp`, display pipe, ASC mailbox and DART nodes | Yes: `ps_disp_sys`, `ps_disp_fe`, `ps_disp_cpu`, external-display domains | Blocked: DCP has a large multi-register contract, firmware/carveouts, mailbox, DART and panel wiring; a partial node would not be useful |
| PCIe / Wi-Fi / Bluetooth | `apcie@B0000000` and its three ports (lines 1840-2125), plus the single `dart-apcie0@90000000` and three mappers (lines 2126-2186) | Added as disabled SoC nodes; no board enables them | T8122 root-complex shape, T602x register generation; terminal `apple,t6020-pcie`, with `apple,t8110-dart` for the DART | `ps_apcie_phy_sw`, whose existing dependencies cover ADT gates `ANS-V`, `APCIE-GP-V`, `APCIE-SYS-GP-V`, `APCIE-ST-V`, and `APCIE-SYS-ST-V` | Added the three-port RC, one shared DART, ADT-exact outbound windows, RID-to-SID map, IRQ/MSI geometry, port-0 PERST/CLKREQ pins, and no board enablement; PCIe-C/Thunderbolt complexes and PIODMA remain separate future units |
| ISP, ANE and media engines | `isp@8C000000`/`dart-isp@8E8C0000` (lines 7233, 7288); `ane@0`/`dart-ane@1800000` (lines 7346, 7373); JPEG/AVE/AVD and DARTs at lines 6845-7192 | Absent | T8112/T8122 ISP, ANE, JPEG/AVE/AVD and DART compatibles | Mixed: ANE/JPEG/AVD domains exist; no complete ISP domain set is exposed | Blocked/longer-term: each requires firmware, memory, mailbox and/or multi-DART data beyond a safe minimal node |

## Added DT slice

The patch adds only the J713 storage and SMC paths:

1. `smc_mbox` and `smc`, including `smc_gpio` and `smc_hwmon`.
2. `ans_mbox`, `sart`, and `nvme`.
3. Disabled-by-default SoC nodes, enabled in `t8132-j713.dts` only.
4. The shared laptop hwmon key descriptions for J713.

The ASC mailbox `+0x8000` split and the `0x4000` interface windows follow the
T8112/T8122 bound interface. Their containing ASC wrapper ranges and all IRQs
come from the J713 dump. The NVMe range, SART range, and SMC SRAM range and
sizes come directly from the dump. No address or IRQ was copied from another
SoC.

## T8132 SPMI/PMU slice

`nub-spmi0@88714000` exposes three ADT register tuples at lines 5809-5832.
The first is child `0x1_88714000/0x4000`, translated through `arm-io` to
Linux address `0x3_88714000`. Linux maps only its first `0x100` bytes, as in
the T8112 structure: the controller driver uses command/status registers at
`0x0..0x8` and, when an optional parent IRQ exists, mask/ack registers through
`0x80` (`drivers/spmi/spmi-apple-controller.c:30-38,71-89,400-465`). The two
additional ADT controller ranges at translated addresses `0x3_88704000` and
`0x3_88700000` are not requested by that interface. The ADT's 21 interrupt
specifiers span two interrupt parents, while the T8112 main controller has no
Linux interrupt property. This node follows that precedent; the driver makes
the parent IRQ optional and polls its receive FIFO when absent.

The child at lines 5834-5955 identifies `hw-name = "abbey"`, compatibles
`pmu,spmi` and `pmu,abbey`, primary USID `0xe`. Its Linux chain is therefore
`apple,abbey-pmic`, `apple,spmi-nvmem`, with the template's
`reg = <0xe SPMI_USID>`. The fixed layout contains only established Linux
cell types backed by explicit Abbey data:

- `pm_setting@2001/1`: ADT `info-pm_setting = 0x2001` at line 5856. This
  diverges from T8112 Stowe's `0xf801`.
- `rtc_offset@2100/6`: ADT `info-clock_offset = 0x2100/0x6` at line 5944.
  This diverges from T8112's `0xf900/0x6`. ADT `info-rtc = 0x2002` at line
  5950 is the separate RTC register, not the clock-offset cell; the adjacent
  `info-rtc_scrpad = 0x2100` corroborates the offset-cell base.
- `fault_shadow@867b/0x10`: ADT `info-fault_shadow` at line 5866. It is
  identical to T8112.
- `socd@8b00/0x1800`: ADT `info-scrpad_socd` at line 5903. The base matches
  T8112, but Abbey's explicit size is `0x1800`, not T8112's `0x400` cell.
  It lies within Abbey's `info-scrpad = 0x8000/0x2800` at line 5876.

Other Abbey-only offsets are recorded but do not have an established cell or
consumer in this slice: `info-leg_scrpad = 0xf800`,
`info-rtc_alarm = 0x2008` with control/event registers at `0x2000`,
`info-scrpad_lpm_ctrl = 0xa7dc`, and `info-scrpad_lpm_log = 0xa780`.
They are not substituted for any T8112 cell.

The RTC child is wired because `drivers/rtc/rtc-macsmc.c:105-108` requires
only the six-byte `rtc_offset` cell and Abbey explicitly supplies its offset
and size.
The reboot child is withheld. T8112 lists `shutdown_flag`, `boot_stage`,
`boot_error_count`, `panic_count`, and `pm_setting`; the current reboot
driver requests the first four names at
`drivers/power/reset/macsmc-reboot.c:24-29`.
Abbey proves only `pm_setting`. Its ADT has no properties for the T8112
`boot_stage@f701`, split-nibble counts at `f702`, `boot_error_stage@f703`, or
`shutdown_flag@f70f,3`, and those addresses are not PMIC-family-invariant in
the in-tree layouts: other families place the same cells under bases `0x6000`,
`0x9f00`, or `0xf800`. Reboot requires Abbey-specific evidence giving the
byte offset for `boot_stage`, the shared byte plus bit ranges for both counts,
and the byte offset plus bit for `shutdown_flag`. An explicit Abbey ADT
property set or an in-tree Abbey layout with those definitions would satisfy
that requirement; a Stowe or Chapel offset alone would not.

## T8132 SPI slice

The SPI slice adds disabled `spi2` and `spi4` controller nodes. Their terminal
compatible is the driver-bound `apple,t8103-spi`; neither controller is enabled
by a board file.

Clock selection is resolved from the T8132 ADT rather than from the split
T8103/T8112/T8122 precedent. SPI2 and SPI4 have clock IDs `0x1a4` and `0x1a6`
at lines 1330 and 1381. The ADT clock-ID convention indexes
`arm-io/clock-frequencies` as `clock-id - 0x100`; entries `0xa4` and `0xa6` in
the line 1307 array both decode to 24 MHz. Therefore both nodes use the
existing 24 MHz `clkref`, and no speculative 200 MHz clock is added. Mesa asks
for 8 MHz at line 1366, which is an exact divide-by-three result. This clock
choice still needs on-hardware confirmation when a usable child driver exists,
because the current slice deliberately leaves both controllers disabled.

No pin groups are added. SPI2's `function-spi_cs0` decodes to `null` at line
1331, consistent with controller-driven chip select but providing no pin
number. SPI4's entry at line 1376 identifies AP GPIO pin `0x4c` (76), but only
for chip select. The `gpio0` description at lines 3753-3772 gives the bank size
but no function map for either controller's CLK, MOSI, or MISO signals. A
future session needs a pin-function table or a live GPIO mux-register decode
for those three signals before it can add `spi2_pins` or `spi4_pins`.

The ADT child `mesa@0` is present at lines 1339-1370, but it is a SEP-gated
Touch ID device with no Linux driver. The `dp855@0` display-side child is
present at lines 1395-1414 and likewise has no Linux driver. Both children are
therefore future-blocked and intentionally absent from DT.

Review-tracked caveats on this slice (both latent while the controllers stay
disabled; confirm both before any board enables spi2/spi4):

1. The `ps_spi2`/`ps_spi4` power-domain references rest on the PMGR slice's
   register-map derivation, not on per-device ADT ground truth — the spi2/spi4
   ADT nodes carry no `power-gates` property (only `clock-gates` 0x5d/0x5f).
   The pmgr offsets (0x380/0x390, in sequence with ps_spi1/ps_spi3/ps_spi5)
   are internally consistent but need one on-hardware confirmation alongside
   the clock check.
2. `apple,t8132-spi` (like the other `apple,t8132-*` tokens in this tree) has
   no devicetree-binding schema entry yet; an upstream submission needs the
   companion `apple,spi.yaml` enum hunk or `dtbs_check` will flag the node.
   No runtime impact — the driver binds the `apple,t8103-spi` fallback.

## T8132 USB/ATC slice

Both ports pass the all-or-nothing criteria and are described as complete SoC
units. The DWC3 controllers and their two DART instances remain disabled in
the SoC file and are enabled only by J713. The ATC PHY and crossbar nodes have
no `status`, matching the precedent. J713 does not use I2C CD321x USB-PD
controllers: `hpm0@C`, `hpm1@A`, and `hpm5@8` are children of
`nub-spmi-a0@88908000`, each with compatible `usbc,sn201202x,spmi`
(lines 6082-6179). The Asahi branch ships an SPMI SN201202x driver whose match
table binds `apple,sn201202x` at `drivers/usb/typec/tipd/spmi.c:155-158`.
The in-tree `t8122-usbpd-spmi.dtsi` and `t603x-j514-j516.dtsi` users supply
the same HPM nodes and full `usb-c-connector` graphs.

The J713 slice adds `nub_spmi_a0`, HPM0/HPM1/HPM5, both connector graphs,
role/orientation/mode switching, and both display crossbars. The board includes
`t8132-usbpd-spmi.dtsi`, provides `atcphy0`/`atcphy1` aliases, and enables
`dwc3_0`, `dwc3_1`, and all four associated DARTs. HPM5 intentionally has no
connector, matching the T8122 template. The remaining controller instances
`nub-spmi-a1@88A08000` and `aop-spmi0@90914000`, their non-USB children, and
every unrelated J713 subsystem remain withheld.

### USB-PD interrupt ruling

The ADT gives each HPM three interrupt numbers and a parallel
`interrupt-type = <0 2 3>` array: HPM0 has 11, 17, 19 (lines 6112 and
6126-6127), HPM1 has 37, 43, 45 (lines 6142 and 6155-6156), and HPM5 has
63, 69, 71 (lines 6169 and 6180-6181). Those type values identify the entries
as `irq`, `sleep`, and `wake`; type 1, `select`, is the omitted event.

Linux cannot use only that three-entry subset. The SN201202x probe obtains all
four interrupts by name and returns an error if any lookup fails
(`drivers/usb/typec/tipd/spmi.c:184-195`). The binding likewise specifies four
entries in the order `irq`, `select`, `sleep`, `wake` and requires both
properties
(`Documentation/devicetree/bindings/usb/apple,sn201202x.yaml:26-47`). Both
in-tree templates fill the missing select event at `irq + 2`: HPM0
11/13/17/19, HPM1 37/39/43/45, and the matching HPM5 pattern 63/65/69/71.
J713 therefore uses those complete four-entry sets; only the select entries
13, 39, and 65 are inferred, and the other nine numbers are direct ADT data.

Although the ADT controller advertises `#interrupt-cells = <1>` at line 6101,
the Linux interface requires two cells. The controller IRQ translator rejects
any specifier whose cell count is not two and consumes the second cell as the
IRQ trigger type (`drivers/spmi/spmi-apple-controller.c:300-312`); the SPMI
binding also fixes `#interrupt-cells` at two
(`Documentation/devicetree/bindings/spmi/apple,spmi.yaml:40-47`). Therefore
`nub_spmi_a0` deliberately uses `<2>` and each HPM event is encoded as
`<line IRQ_TYPE_EDGE_RISING>`, as in T8122. The controller's ADT interrupt
list routes only its second entry through the AIC (lines 6085 and 6094); that
entry decodes to 520, so the Linux node uses AIC IRQ 520. This upstream IRQ is
required for the controller driver to instantiate its child IRQ domain
(`drivers/spmi/spmi-apple-controller.c:400-465`). The controller maps the
leading `0x100` bytes of the ADT's `0x3_88908000/0x4000` first range, following
the same window policy as the main T8132 SPMI controller and T8122 auxiliary
controller. Its compatible chain, address/size cells, interrupt-controller
role, upstream IRQ shape, power-domain attachment, and disabled default follow
`t8122.dtsi:819-830`; it attaches the existing `ps_nub_spmi_a0` from
`t8132-pmgr.dtsi:995-1002`.

### Role, graph, and crossbar precedent

The DWC3 properties are copied from T8122: `dr_mode = "otg"`,
`usb-role-switch`, and `role-switch-default-mode = "host"` appear at
`t8122.dtsi:1267-1269` and `t8122.dtsi:1320-1322`. The common T8122 board
template supplies the graph shape: HS connector endpoints and the DWC3-to-ATC
USB3 endpoints are at `t8122-jxxx.dtsi:79-120`; the Type-C lane and USB3 PHY
endpoints are at `t8122-jxxx.dtsi:122-163`. T8122's ATC nodes supply
`orientation-switch` and `mode-switch` at `t8122.dtsi:1309-1310` and
`t8122.dtsi:1362-1363`. J713 keeps the complete bidirectional graph while
placing the connector back-links in its USB-PD include, so T8132 boards that
do not include connector nodes still compile.

Each ADT ATC wrapper is `0x58000`: port 0 starts at `0x4_03000000` and port 1
at `0x4_0b000000` (lines 5429 and 5624). The established layout maps ATC core
at offset `0/0x4c000`, leaves a crossbar window at offset `0x4c000/0x4000`,
and maps LPDPTX at offset `0x50000/0x8000`. This yields
`0x4_0304c000/0x4000` and `0x4_0b04c000/0x4000`, both fully contained in their
ADT wrapper. The nodes match the T8112 shape at `t8112.dtsi:1865-1870` and
`t8112.dtsi:1924-1929`: one register range, `#mux-control-cells = <1>`, and
the corresponding ATC USB power domain. Their compatible chains begin with
`apple,t8132-display-crossbar` and terminate in the directly matched
`apple,t8112-display-crossbar`; the terminal appears in
`drivers/mux/apple-display-crossbar.c:431-449`.

### Address and interrupt evidence

The `arm-io` identity ranges decoded above cover the entire complexes. Port 0
uses `0x4_00000000..0x4_04073fff`; port 1 uses
`0x4_08000000..0x4_0c073fff`. As independent anchors, every decoded ADT `reg`
base below equals its decimal `IODeviceMemory` address:

- Port 0 ATC PHY has four ADT ranges at lines 5429 and 5432:
  `17224499200 = 0x4_02a90000/0x4000`,
  `17221812224 = 0x4_02800000/0x4000`,
  `17230200832 = 0x4_03000000/0x58000`, and
  `17179869184 = 0x4_00000000/0x1000000`.
- Port 1 has the same layout at lines 5624 and 5627, offset by `0x08000000`:
  `17358716928 = 0x4_0aa90000/0x4000`,
  `17356029952 = 0x4_0a800000/0x4000`,
  `17364418560 = 0x4_0b000000/0x58000`, and
  `17314086912 = 0x4_08000000/0x1000000`.
- DWC3 port 0 is `17216045056 = 0x4_02280000/0x11800`; port 1 is
  `17350262784 = 0x4_0a280000/0x11800` (lines 5540, 5552, 5735, 5747).
  The Linux `dwc3-core` `0xcd00` and `dwc3-apple` `0x3200` split is contained
  in each ADT range and matches T8122 exactly.
- DART port 0 exposes `0x4_02f00000/0xc000` and
  `0x4_02f80000/0xc000` at lines 5485 and 5505; port 1 exposes
  `0x4_0af00000/0xc000` and `0x4_0af80000/0xc000` at lines 5680 and
  5700. Each Linux instance maps the leading `0x4000`, as in T8122. Both ADT
  nodes identify themselves as `dart,t8110` (lines 5494 and 5689).

The four DWC3 interrupt cells decode to `1279, 1280, 1281, 1282` for port 0
(lines 5529 and 5558), and `1341, 1342, 1343, 1344` for port 1 (lines 5724
and 5754). T8122 selects the first of each four-entry DWC3 sequence, with the
DART interrupt immediately following the sequence, so the Linux nodes use
1279 and 1341. The DART cells independently decode to 1283 and 1345 (lines
5480/5497 and 5675/5692).

### ATC PHY named-resource proof

The ATC PHY driver maps all five names unconditionally: `core`, `lpdptx`,
`axi2af`, `usb2phy`, and `pipehandler`. None is optional. T8132's four ATC
ADT ranges are sufficient because one contains two named subregions, while the
pipehandler is carried in the associated DWC3 node:

1. The `0x58000` window is split into `core` at offset `0x0/0x4c000` and
   `lpdptx` at `0x50000/0x8000`. These are the exact T8122 boundaries.
   Both regions are contained in the ADT wrapper. Mapping the full window as
   only `core` was rejected because it would leave the driver's mandatory
   `lpdptx` resource absent; overlapping the full window with a second resource
   was also rejected.
2. `axi2af` uses the first `0x8000` of the 16 MiB window at
   `0x4_00000000` or `0x4_08000000`. The ADT's `tunable_ATCAXI2AF*`
   properties are at lines 5426, 5450, 5460, 5474 and their port 1
   counterparts at lines 5621, 5645, 5655, 5669. The size matches the T8122
   DT resource and covers the explicitly named T8132 tunables.
3. `usb2phy` is the first ATC range, `0x02a90000/0x4000` within each port.
   The ADT has explicitly named `tunable_USB2PHY_HOST` and
   `tunable_USB2PHY_DEV` properties at lines 5442-5444 and 5637-5639.
4. `pipehandler` is DWC3 ADT register index 3:
   `17224450048 = 0x4_02a84000/0x4000` and
   `17358667776 = 0x4_0aa84000/0x4000` (lines 5540/5552 and 5735/5747).
   Its offset and size match the T8122 named pipehandler resource.

A positional four-name mapping was rejected: it would incorrectly call the
second ATC range `lpdptx`, fail to split the combined core/LPDPTX range, and
omit the separately exposed pipehandler. The remaining ATC range at
`0x4_02800000/0x4000` or `0x4_0a800000/0x4000` has no proven Linux driver
name. It is not one of the five resources the current driver requests and is
therefore omitted rather than guessed.

### Remaining criteria and driver terminals

`ps_atc0_usb` and `ps_atc1_usb` exist in `t8132-pmgr.dtsi` and depend on
their corresponding `*_common` and `*_usb_aon` domains. Compatible chains
terminate in directly matched strings:

- DWC3: `apple,t8132-dwc3`, terminal `apple,t8103-dwc3`;
- DART: `apple,t8132-dart`, terminal `apple,t8110-dart`;
- ATC PHY: `apple,t8132-atcphy`, terminal `apple,t8122-atcphy`.

The verified driver tables are recorded in the method note. Runtime use
requires the board Type-C graph, so the SoC file keeps both DWC3 controllers
disabled and J713 alone enables them through the in-tree SN201202x template.

## T8132 PCIe/Wi-Fi slice

This slice describes only the main three-port `apcie@B0000000` complex and
its single DART. Both SoC nodes are disabled and no board file changes. The
two `apciec` complexes, both `acio` engines, and every `dart-acio` node at
lines 2263-3170 are the Thunderbolt/PCIe-C path; they are intentionally a
separate future unit rather than partial dependencies of the WLAN/BT complex.

### `arm-io` translation and outbound ranges

The complete little-endian `arm-io/ranges` byte string at line 1300 has four
additional identity tuples relevant here (tuple numbers are zero-based):

- tuple 21: child and parent `0x4_90000000`, size `0x07060000`;
- tuple 22: child and parent `0x1c_b0000000`, size `0x10000000`;
- tuple 23: child and parent `0xb_c0000000`, size `0x20000000`;
- tuple 24: child and parent `0xb_80000000`, size `0x40000000`.

Tuple 22, not the first `+0x2_00000000` translation and not a USB tuple,
covers the ECAM window. Its `0x1c_b0000000` base exactly equals ADT
`IODeviceMemory[0] = 123211874304` at line 1862. Tuple 21 covers every RC,
port, PHY, and DART register selected below; its inclusive end is
`0x4_9705ffff`. Tuples 23 and 24 justify the parent sides of the two raw
`apcie/ranges` entries at line 1863. Those entries decode without alteration
to:

- 64-bit prefetchable PCI child `0xb_c0000000` to parent
  `0xb_c0000000`, size `0x20000000`;
- 32-bit non-prefetchable PCI child `0x80000000` to parent
  `0xb_80000000`, size `0x40000000`.

### Register selection and driver generation

The ADT has 31 `apcie` register tuples at line 1874, independently mirrored
by 31 decimal `IODeviceMemory` entries at line 1862. The Linux interface uses
these eight, in driver-required name order:

| Linux name | ADT index | Address / size | Independent decimal address |
|---|---:|---|---:|
| `config` | 0 | `0x1c_b0000000 / 0x10000000` | 123211874304 |
| `rc` | 1 | `0x4_94000000 / 0x4000` | 19662897152 |
| `port0` | 7 | `0x4_90028000 / 0x8000` | 19595952128 |
| `port1` | 15 | `0x4_91028000 / 0x8000` | 19612729344 |
| `port2` | 23 | `0x4_92028000 / 0x8000` | 19629506560 |
| `phy0` | 9 | `0x4_97020000 / 0x4000` | 19713359872 |
| `phy1` | 17 | `0x4_97024000 / 0x4000` | 19713376256 |
| `phy2` | 25 | `0x4_97028000 / 0x4000` | 19713392640 |

The one-RC window, per-port `0x01000000` stride, and separate `0x4000` PHY
windows at a `0x4000` stride match the T602x/T8122 generation, not the older
T8103 layout where PHYs are derived from the RC resource. The current driver
uses named `portN` and `phyN` resources at
`drivers/pci/controller/pcie-apple.c:675-689`; its T602x data selects the
newer MSI-address, PERST, RID-to-SID, and MSI-map offsets at lines 134-184.
The compatible therefore starts with `apple,t8132-pcie` and terminates in the
direct match `apple,t6020-pcie` at lines 992-995.

`dart-apcie0` independently reports `19595788288 = 0x4_90000000/0x20000`
at line 2132 and the same first tuple at line 2156. Linux maps its leading
`0x4000`, following the T8122 DART precedent and the established T8110 driver
window policy. The ADT compatible is `dart,t8110` (line 2142), so the chain is
`apple,t8132-dart`, `apple,t8110-dart`; the latter is directly matched at
`drivers/iommu/apple-dart.c:1627-1632`.

### Interrupts, MSI, ports, and IOMMU mapping

The three little-endian root interrupt cells at lines 1851 and 1879 decode to
1231, 1240, and 1249, one per ADT port. The DART cell at lines 2129 and 2145
decodes to 1232. `msi-vector-offset = 0x5e2` and `#msi-vectors = 0x60` at
lines 1849 and 1855 give AIC IRQ 1506 and 96 edge-triggered vectors, so
`msi-ranges` is `<&aic AIC_IRQ 1506 IRQ_TYPE_EDGE_RISING 96>`. This is three
32-vector port allocations rather than T8122's four ports and 32-vector total
property. The raw bus range at line 1844 is 0 through 8.

There is one DART for the entire three-port complex, unlike T8122's one DART
per port. Its children prove WLAN SID 1, Bluetooth SID 2, and PIODMA SID
`0x11` at lines 2160-2184. The enumerated endpoint `reg` properties at lines
2064 and 2116 decode to RIDs `0x100` (WLAN function 0) and `0x101` (Bluetooth
function 1). The Linux map is therefore:

```
iommu-map = <0x100 &pcie0_dart 1 1>,
            <0x101 &pcie0_dart 2 1>;
iommu-map-mask = <0xffff>;
```

The full 16-bit mask is required to preserve the function bit; T8122's
`0xff00` mask would collapse both functions onto one SID. SID `0x11` is not
included because it belongs to the internal PIODMA engine, not a PCI RID.

ADT `#ports = 3` proves port nodes 0 through 2, while only bridge 0 is
instantiated at lines 1882-2125. Ports 1 and 2 therefore remain explicitly
disabled. The whole root complex and the DART are also disabled, so this
slice provides no board enablement.

### Pins, power, and withheld PIODMA

Bridge 0 does prove its pins. `function-clkreq` at lines 1890 and 1985 names
AP GPIO controller phandle `0xa3`, pin 160, function 2; `function-perst` at
lines 1898 and 1961 names the same controller, pin 163, function 0. Phandle
`0xa3` is `gpio0` at lines 3753-3772. The DT therefore adds a one-pin CLKREQ
pinctrl group and port-0 active-low reset GPIO. There are no bridge children
or corresponding function descriptors for ports 1 and 2, so no pins are
invented for them. An ADT bridge node or a live mux decode naming their
CLKREQ/PERST pins would be sufficient future evidence.

The root `power-gates` list at line 1865 is `0x17b..0x17f, 0x97`. Decoding
the PMGR `devices` table at line 4144 identifies these as `ANS-V`,
`APCIE-GP-V`, `APCIE-SYS-GP-V`, `APCIE-ST-V`, `APCIE-SYS-ST-V`, and
`APCIE_PHY_SW`. The existing `ps_apcie_phy_sw` leaf at
`t8132-pmgr.dtsi:816-822` depends on both `ps_apcie_sys_st` and
`ps_apcie_sys_gp`; their parents cover `ps_ans`, `ps_apcie_st`, and
`ps_apcie_gp`. Using that leaf therefore represents the complete ADT gate
set through one binding-compliant power domain. The DART shares it, following
the T8122 policy of attaching the DART and RC to the same PCIe domain.

`apcie0-piodma@90030000` is real: its three `0x4000` ranges begin at
`0x4_90030000`, its IRQ is 1233, and it uses SID `0x11` (lines 2187-2202).
It is withheld because neither the T8122 PCIe complex nor the current Apple
PCIe driver describes a PCIe PIODMA node or a bound `pcie-apiodma,t6020`
interface. A driver with an OF match and documented register contract, plus
an in-tree PCIe precedent showing its relationship to the host bridge, would
make it safe to add. The Thunderbolt PIODMA and ACIO nodes are not substitutes
and remain part of their own future unit.

## T8132 SEP slice

The SEP slice adds `sep_dart`, the disabled `sep` consumer, and `sep_mbox`.
The consumer prepends `apple,t8132-sep` to the directly matched `apple,sep`
token used by T8112. The DART and mailbox chains likewise begin with
T8132-specific compatibles and terminate in `apple,t8110-dart` and
`apple,asc-mailbox-v4` respectively. The current Asahi branch's SEP OF table
matches `apple,sep` in `drivers/soc/apple/sep.rs` at line 332.

The ADT's first SEP register tuple decodes from little-endian cells to child
`0x1_aa600000/0x88000`. Applying the `arm-io` translation gives
`0x3_aa600000/0x88000`, exactly matching the independently reported
`IODeviceMemory` address 15743320064 and length 557056. The second tuple is
`0x1_aa050000/0x60000`, translated to `0x3_aa050000/0x60000`. It is omitted:
the Linux SEP interface has one wrapper resource and the T8112 precedent does
not map the analogous extra range.

The mailbox subwindow at `0x3_aa608000/0x4000` is the precedent-defined ASC
interface at wrapper offset `0x8000`; it is contained in the first ADT range
but is not independently named by the dump. The four ADT interrupt cells
decode in role order to 390, 389, 392, and 391 for `send-empty`,
`send-not-empty`, `recv-empty`, and `recv-not-empty`. This is the same role
ordering established by the existing T8132 SMC and ANS mailbox nodes.

The DART's first tuple decodes and translates to
`0x3_a8c80000/0x20000`, matching `IODeviceMemory` address 15716581376 and
length 131072. Linux maps the leading `0x4000` register window, as the T8112
SEP DART and the newer T8122 DART nodes do. T8122 has no SEP cluster in either
this checkout or the checked branch, so it supplies no SEP-specific window or
power-domain choice. The DART's second `0x3_8079c000/0x4000` range is omitted,
matching the established DART interface. Its interrupt cell decodes to 399.

The SEP ADT node references SID 0 (`mapper-sep`) and SID 1
(`mapper-sep-mpm`), while the DART also exposes SID 2 for the exclave manager.
The Linux SEP driver and T8112 node consume only SID 0, so this slice keeps
`iommus = <&sep_dart 0>`. It does not describe the MPM mapper separately, and
the undriven exclave manager remains out of scope. Although `ps_sep` exists
and is always on, no `power-domains` property is added because neither the
T8112 SEP cluster nor the T8122 DART policy supplies one.

Hardware-validation checklist (series-wide, in priority order — items note
the slice that armed them):

1. DWC3 `iommus` stream IDs. The DT wires `<&dart 0>, <&dart 1>` per the
   T8103/T8112/T8122 precedent, but the T8132 ADT attests `sid = <1, 14>`
   on `dart-usb0`/`dart-usb1` (lines 5493, 5688) with a single
   `mapper-usb0@1`/`mapper-usb1@1` (unit-address = SID value, proven by
   `mapper-sep@0/1/2` vs `dart-sep` sids and `mapper-apcie0-piodma@11`),
   and `usb-drd0/1` attach via that SID-1 mapper. Whether precedent SoCs'
   ADTs share this structure (making 0/1 a macOS mapper-view abstraction
   that works) cannot be resolved without their dumps. Failure mode if the
   ADT view is literal: loud, contained DART translation faults (IRQ
   1283/1345) on first USB DMA, dead ports, no boot impact. Candidate fix
   is a one-line `iommus` change (`<&dart_0 1>`, second entry per the
   sid[1]=14 pairing). Decide on first hardware boot. Update from the
   PCIe slice review: the ADT proves mapper `reg` IS the hardware stream
   ID (`pci-bridge0` `apcie-piodma-sid = <0x11>` matches
   `mapper-apcie0-piodma@11`, and the dart `sid` array there holds the
   ports' PIODMA streams, not endpoint streams). Applied to USB, the
   single `mapper-usb0@1` tilts the answer toward stream 1 for the DWC3
   endpoint; the open question is only how that maps across the two DART
   instances.
2. The `select = irq+2` fourth HPM interrupt (only non-ADT interrupt
   number; wrong value = probe timeout, loud).
3. The atcphy crossbar nodes probe-write one tunable into the derived
   (non-ADT) `+0x4c000` window with T8112 data (`n_ufp=4`); harmless with
   no mux consumer, verify with display bring-up.
4. The ascwrap-v6 `+0x8000` mailbox windows and axi2af 0x8000 sizing
   (standing items from earlier slices). SIO follows the MTP correction and
   reserves only its leading `0x4000`, so its `+0x8000` mailbox is disjoint.
5. The AOP ADMAC interrupt output index (2, precedent-inherited from the
   T8112/T8122 AOP ADMACs — the ADT lists one IRQ with no slot evidence,
   and the index selects live status registers: a wrong slot means DMA
   completions are never serviced and aop-audio stalls silently). Armed
   by the AOP slice.
6. The MTP HID AFE/STM reset GPIOs (8/24 on J713, copied from the J413/
   J613 laptop precedents — the J713 ADT exposes no GPIO evidence). A
   wrong number fails only at interface start (dead keyboard/trackpad,
   no probe error).

Enable-time caveats, recorded for whichever board slice flips the SEP node on:
the SEP driver's probe requires a reserved-memory region named `sepfw`
(resolved by name), which this node does not yet reference — the shipped
T8103/T8112 nodes are identical, so the region is expected to arrive with
board/bootloader wiring; and first hardware bring-up should confirm whether
SEP firmware traffic ever uses the SID 1 MPM stream omitted here.

## T8132 MTP slice

The MTP slice adds the disabled `mtp`, `mtp_mbox`, `mtp_dart`, and
`mtp_dockchannel` SoC nodes, with the DockChannel HID transport as the FIFO
child. J713 enables the four parent units and supplies the board-specific HID
description. All four SoC compatible chains begin with `apple,t8132-*`;
their driver-bound terminals are listed above.

The first `arm-io` range tuple at line 1300 spans child addresses from zero
through `0x3_9fffffff` and adds `0x2_00000000`. It therefore covers every
MTP child address. The MTP `reg` bytes at line 5204 decode to child
`0x1_94600000/0x88000` and `0x1_94050000/0x4000`, translated to Linux
`0x3_94600000/0x88000` (`asc`) and `0x3_94050000/0x4000` (`sram`). These
agree with the two `IODeviceMemory` entries at line 5190. The DT `asc`
window is deliberately SHRUNK to the leading `0x4000` of the `0x88000` ADT
wrapper, exactly as T8112 and T8122 do: the helper driver reserves its
`asc` resource and touches only `asc + 0x44`, while the mailbox driver
separately reserves the `+0x8000` subwindow — a full-width `asc` would
contain the mailbox and fail probe with `-EBUSY` (the mailbox reserves
first under fw_devlink ordering).

The mailbox is the established ASC interface subwindow at wrapper offset
`+0x8000`, `0x3_94608000/0x4000`. It is a precedent-derived window contained
by the first ADT range, rather than an independently named ADT tuple. The ADT
interrupt cells at lines 5186 and 5198 decode, in the series' mailbox role
order, to 1114, 1113, 1116, and 1115 for `send-empty`, `send-not-empty`,
`recv-empty`, and `recv-not-empty` respectively.

The DART `reg` property at line 5253 has three tuples. They translate to
`0x3_94800000/0xc000`, `0x3_94810000/0x4000`, and
`0x3_882a4000/0x4000`, matching the three `IODeviceMemory` entries at line
5231. Linux maps only the leading `0x4000` of the first tuple, as the T8112
MTP DART and the established T8132 DART policy do; both trailing ranges are
outside that interface and are omitted. The DART interrupt cell at lines
5229 and 5243 decodes to 1091.

The MTP and HID nodes deliberately use `iommus = <&mtp_dart 0>`, diverging
from T8112/T8122's SID 1. T8132's DART advertises `sid = <0>` at line 5239,
and its only child is `mapper-mtp@0` with `reg = <0>` at lines 5257-5263.
The mapper-reg-is-stream-ID proof recorded in hardware-validation item 1
therefore makes SID 0 the ADT-grounded choice; copying SID 1 would address a
stream the T8132 MTP mapper does not describe.

The DockChannel `reg` bytes at line 5047 contain six translated windows:

| Linux window | Linux role | Evidence and disposition |
|---|---|---|
| `0x3_94b00000/0x1000` | Unbound base window | First ADT tuple; omitted because neither the T8112 DT interface nor the Linux drivers name it |
| `0x3_94b14000/0x1000` | `irq` | Second ADT tuple; the controller maps the resource by this name |
| `0x3_94b30000/0x1000` | `config` | Third ADT tuple; child offset `0x8000` from the FIFO range base |
| `0x3_94b34000/0x1000` | `data` | Fourth ADT tuple; child offset `0xc000` |
| `0x3_94b28000/0x1000` | `rmt-config` | Fifth ADT tuple; child offset `0x0000`; carried per T8112/T8122 precedent — no driver consumes the rmt-* windows at this tag |
| `0x3_94b2c000/0x1000` | `rmt-data` | Sixth ADT tuple; child offset `0x4000` |

The translations agree with all six `IODeviceMemory` entries at line 5054.
Four separate `ranges` tuples retain the ADT's real `0x1000` apertures and do
not claim the gaps between them. The T8112/T8122 offset pattern establishes
the four child roles, while the driver confirms that local `config` and
`data` are the actively mapped FIFO resources. The parent interrupt at lines
5046 and 5049 decodes to 1093; the HID child uses the precedent's
DockChannel-local TX/RX interrupts 2 and 3. Its FIFO size is the decoded ADT
value `0x800` from line 5052.

No `power-domains` property is present. The MTP ADT node explicitly reports
empty `clock-gates` and `power-gates` arrays at lines 5188 and 5203, resolving
the former gap-matrix blocker: it has the same self-gated dependency shape as
the T8112 MTP cluster and the already described SEP consumer. The disabled
SoC defaults still ensure that only an opting-in board starts the stack.

J713 follows both laptop precedents (`t8112-j413.dts:224-257` and
`t8122-j613.dts:158-195`): it enables all four units, assigns AFE and STM
reset GPIOs 8 and 24 from `smc_gpio`, declares the `multi-touch`, `keyboard`,
`stm`, `actuator`, and `tp_accel` interfaces, and sets HID country code and
keyboard layout ID to zero. Its firmware name follows the per-board precedent
as `apple/tpmtfw-j713.bin`. The root `keyboard = &keyboard` alias follows
T8112 J413 lines 21-25 so the bootloader can attach layout/calibration data;
the DockChannel HID driver consumes `firmware-name` at lines 407-455 and the
two keyboard values at lines 669-685.

The separate `mtp-aop-mux` at ADT lines 5266-5272 has compatible
`hid-transport,mux`, for which the pinned Linux tree has no matching driver.
It is not part of the T8112/T8122 Linux MTP interface and remains out of scope.

## T8132 SIO slice

The SIO slice adds only the disabled `sio_dart`, `sio`, and `sio_mbox` SoC
nodes. No board file is changed. Their compatible chains begin with the
T8132-specific token and terminate in the driver matches cited above. The SIO
DMA driver consumes `dma-channels`, mailbox and IOMMU links and maps only MMIO
resource index 0 (`drivers/dma/apple-sio.c:784-820`); it has no named or second
resource.

The first `arm-io` range translates every SIO child address here by
`+0x2_00000000`. The SIO `reg` bytes at ADT line 4557 therefore decode as:

| ADT child tuple | Linux disposition | Source cross-check |
|---|---|---|
| `0x1_ade00000/0x88000` | `sio` maps only `0x3_ade00000/0x4000`; `sio_mbox` separately maps the precedent-defined `0x3_ade08000/0x4000` subwindow | `IODeviceMemory` address 15802040320, length 557056 at line 4540 |
| `0x1_ad850000/0x4000` | Translates to `0x3_ad850000/0x4000` and is omitted: T8112 gives SIO no second or `sram` resource and the driver has no interface for it | `IODeviceMemory` address 15796076544, length 16384 at line 4540 |

The leading SIO mapping deliberately stops at `0x4000`, keeping it disjoint
from the mailbox at wrapper offset `+0x8000`. The pinned T8112 DTS itself still
declares a legacy `0x8000` SIO resource at `t8112.dtsi:947-956`; it does not
actually provide the claimed `0x4000` precedent. This slice applies the MTP
resource-reservation lesson instead: both T8112/T8122 MTP helpers map only the
leading `0x4000`, and the SIO driver touches the base resource (notably
`base + 0x44`) while the mailbox driver independently reserves its subwindow.
Using the full ADT wrapper would make the two platform resources overlap.

The mailbox subwindow is not a separate ADT tuple. It is the established ASC
v4 interface derived at `0x3_ade00000 + 0x8000`, with size `0x4000`. The four
little-endian interrupt cells at lines 4535 and 4550 decode in ADT role order
to 1079, 1078, 1081, and 1080, wired respectively as `send-empty`,
`send-not-empty`, `recv-empty`, and `recv-not-empty`.

The DART's first tuple at ADT line 4608 is
`0x1_ad060000/0xc000`, translated to `0x3_ad060000/0xc000`; the independent
`IODeviceMemory` entry at line 4589 reports address 15787753472 and length
49152. Linux maps its leading `0x4000`, following the T8112 DART and the
series-wide DART policy. The extra `0x1_8079c000/0x4000` tuple translates to
`0x3_8079c000/0x4000` and is omitted per that interface. IRQ `<0x432>` at
lines 4587 and 4600 decodes to 1074.

SID wiring follows the ADT rather than copying a consumer number. `dart-sio`
advertises SIDs 0 and 1 at line 4596. Its children are `mapper-sio@0` with
`reg = <0>` (lines 4612-4620) and `mapper-aes@1` with `reg = <1>` (lines
4622-4630); `sio` points to the former through `iommu-parent = <0xcd>` at line
4544. The mapper-reg-is-stream-ID rule therefore yields
`iommus = <&sio_dart 0>`.

Both `clock-gates` and `power-gates` on SIO contain ID `0x123` at ADT lines
4537 and 4556. The PMGR `devices` table at line 4144 pairs record `0x123` with
the name `SIO-CPU-V`; this is the domain represented by `ps_sio_cpu` at
`t8132-pmgr.dtsi:202-208`. Following the T8112 cluster shape, that domain is
wired to SIO, mailbox, and DART. T8112's separate `resets = <&ps_sio>` is not
copied because T8132 has no `ps_sio` provider and the ADT proves only the
`SIO-CPU-V` gate; inventing a reset relationship would not be evidence-backed.

T8132's audio architecture is not the MCA-style path described by the
T8112/T8122 SoC trees. The complete ADT has no MCA/I2S controller node.
Instead, `iop-audio-controller` is compatible `iop-audio,controller` and names
`AOPAudioService` at lines 7531-7538. Its headphone path is
`iop-adma-stream,leap`: `audio-hp` selects leap-ns through phandle `0x15c` at
lines 7540-7549. The speaker path is `iop-adma-stream,mca`, but its
`dma-parent` and power-switch provider are phandle `0x15b` (lines 7623-7632),
the base-ns ADMAC. The two ADMAC tuples decode and translate as follows:

| ADMAC | Main Linux window | IRQ | Extra tuple | IOMMU evidence |
|---|---|---:|---|---|
| base-ns (`admac,t8132`) | `0x3_93800000/0x34000` from child `0x1_93800000/0x34000` | 475 (`0x1db`) | child `0x1_882a8000/0x8` → `0x3_882a8000/0x8` | `iommu-parent = <0xb2>` selects AOP-DART `mapper-admac-base-ns@8` |
| leap-ns (`admac,t8132`) | `0x3_93300000/0x34000` from child `0x1_93300000/0x34000` | 478 (`0x1de`) | child `0x1_882a8000/0x8` → `0x3_882a8000/0x8` | `iommu-parent = <0xb4>` selects AOP-DART `mapper-admac-leap-ns@a` |

These values come from ADT lines 7726-7779; the mapper SIDs are at lines
4044-4071. The proposed `0x800` extra-range size does not match the evidence:
both raw `reg` properties end in little-endian 64-bit size `0x8`, and both
independent `IODeviceMemory` entries report length 8 at lines 7736 and 7766.
T8112 has one 24-channel ADMAC at
`0x2_38200000/0x34000`, connected to SIO-DART SID 2 and consumed by MCA
(`t8112.dtsi:959-1047`). T8132 instead has 13-channel base and 9-channel leap
instances on AOP-DART SIDs 8 and 10. This slice instantiates leap-ns only,
because it is the ADT-selected DMA engine for the driver-supported AOP audio
child. It follows both AOP precedents by mapping only the main `0x34000`
resource: the ADMAC binding permits one register resource and the driver maps
only index 0 (`drivers/dma/apple-admac.c:861-864`). The shared auxiliary
`0x3_882a8000/0x8` tuple therefore remains omitted.

The codec silicon is already Linux-supported and DT-describable: the CS42L84
headset codec is the i2c1 child at address `0x4b` (ADT lines 1521-1545), and
the four `audio-control,sn012776` speaker amps are i2c3 children at `0x38`,
`0x39`, `0x3b`, and `0x3c` (lines 1574-1630). They are not added yet because
the transport between those endpoints and the AOP is missing. An exhaustive
search of the pinned Linux tree finds no driver or OF match for
`iop-audio,controller`. Consequently speaker and headphone playback are
**BLOCKED ON DRIVER WORK, not DT**. The AOP cluster and leap ADMAC are DT-ready
in this slice; the codec I2C children can join a future board slice once an
`iop-audio,controller` transport driver exists.

SIO enable-time notes (from review): the apple,sio driver's probe hard-
requires an `apple,sio-firmware-params` property that no in-tree Apple DT
carries — the bootloader injects it at boot (see the t8112-j473 comment);
any board flipping `sio` on must ensure that injection path exists.
`dma-channels = <128>` is copied from T8103/T8112 (the ADT carries no
count) and equals the driver's own NCHANNELS_MAX cap, so it cannot overrun
driver state. The dart/mbox providers are deliberately disabled with the
consumer (series policy, unlike T8112's default-on providers): a board
enabling `sio` must enable all three together. The `apple,t8132-sio`,
`apple,t8132-dart`, and `apple,t8132-asc-mailbox` first entries join the
standing binding-enum list.

## T8132 AOP slice

The AOP slice adds the disabled `aop_mbox`, `aop_dart`, `aop_admac`, and
`aop` SoC nodes. It follows the complete T8112/T8122 cluster shape at
`t8112.dtsi:1502-1562` and `t8122.dtsi:899-959`, while deriving every T8132
address, interrupt, stream ID, and channel count from the ADT. No board file is
changed.

The five little-endian `aop@90600000` register tuples at ADT line 3871 decode
through the `arm-io` `+0x2_00000000` translation as follows:

| ADT child tuple | Linux disposition | Source cross-check |
|---|---|---|
| `0x1_90600000/0x88000` | The AOP `asc` resource maps only the leading `0x3_90600000/0x4000`; `aop_mbox` separately maps `0x3_90608000/0x4000` | `IODeviceMemory` address 15307112448, length 557056 at line 3855 |
| `0x1_90050000/0x4000` | Omitted; the driver accepts only the fixed AOP and ASC resources | Address 15301148672, length 16384 at line 3855 |
| `0x1_90c00000/0x1e0000` | First AOP resource, `0x3_90c00000/0x1e0000` | Address 15313403904, length 1966080 at line 3855 |
| `0x1_882a8000/0x8` | Omitted auxiliary power-switch register; no AOP driver resource represents it | Address 15169388544, length 8 at line 3855 |
| `0x1_9062c000/0x3c008` | Omitted; no third AOP driver resource exists | Address 15307292672, length 245768 at line 3855 |

This intentionally applies the MTP/SIO resource-reservation rule instead of
copying the T8112/T8122 AOP wrapper width. The AOP driver's `ASC_MMIO_SIZE` is
`0x4000` and it maps resource indices 0 and 1 at
`drivers/soc/apple/aop.rs:63-66,995-999`. Reserving all `0x88000` as `asc`
would overlap the independent mailbox at wrapper offset `+0x8000`; stopping at
`0x4000` leaves the two resources disjoint. The driver's required resource
order also explains why the `0x1e0000` AOP/SRAM window is listed first even
though its address is higher.

The four interrupt cells at ADT lines 3850 and 3865 decode in established ASC
mailbox role order to 434, 433, 436, and 435. They are wired respectively as
`send-empty`, `send-not-empty`, `recv-empty`, and `recv-not-empty`. AOP's
`compatible = "iop,ascwrap-v6"` at line 3864 establishes the wrapper
generation; Linux terminates the mailbox compatible chain in the bound
`apple,asc-mailbox-v4` interface.

The AOP DART's three tuples at line 4019 are
`0x1_90f00000/0xc000`, `0x1_90f10000/0x4000`, and
`0x1_882a4000/0x4000`; their `IODeviceMemory` counterparts are at line 4003.
Linux maps only the translated leading `0x3_90f00000/0x4000`, following both
AOP precedents and the series DART policy, and omits the second/auxiliary
tuples. IRQ `<c9010000>` at lines 3995 and 4023 is 457. The ADT compatible is
`dart,t8110` at line 4012, matching the terminal
`apple,t8110-dart` at `drivers/iommu/apple-dart.c:1627-1634`.

Stream wiring follows mapper `reg`, not mapper ordinal. The DART advertises
non-secure SIDs 0, 8, and 10 at line 3994 and exclave SIDs 1, 9, and 11 at line
4008. Its mapper children are AOP `@0`/`reg = <0>` (lines 4026-4033), exclave
AOP `@1`/`reg = <1>` (lines 4035-4042), base-ns `@8`/`reg = <8>` (lines
4044-4052), base-s `@9`/`reg = <9>` (lines 4054-4061), leap-ns `@A`/
`reg = <10>` (lines 4063-4071), and leap-s `@B`/`reg = <11>` (lines
4073-4080). AOP's `iommu-parent = <0xb0>` at line 3859 therefore yields SID 0;
leap-ns's parent `<0xb4>` at line 7768 yields SID 10.

`admac-leap-ns@93300000` supplies main window
`0x1_93300000/0x34000`, auxiliary `0x1_882a8000/0x8`, IRQ 478, compatible
`admac,t8132`, and `#dma-channels = 9` at ADT lines 7756-7779. The main window
translates to `0x3_93300000/0x34000`; the auxiliary tuple is omitted because
both T8112/T8122 AOP ADMAC precedents and the binding expose one register
resource, and the driver maps only index 0. The sole usable IRQ occupies output
2 in the four-entry `interrupts-extended` array, matching both precedents.
Unlike their hard-coded 16 channels, this node keeps the ADT's 9: the driver
allocates channel state from `dma-channels` at
`drivers/dma/apple-admac.c:817-842`, and the AOP audio child consumes valid
channel 1. The ADT's `clock-gates = <0x156>` at line 7760 maps to
`AUDIO-LLT-V` in the PMGR `devices` table at line 4144. There is no
corresponding provider in `t8132-pmgr.dtsi`, no ADT `power-gates` property, and
neither AOP ADMAC precedent wires a domain, so this slice does not invent one.

The AOP firmware subtree contains accel (lines 3908-3924), gyro (lines
3926-3943), ALS (lines 3945-3957), `aop-audio` (lines 3959-3971), and
`aop-voicetrigger` (lines 3973-3985). Linux AOP registers announced services
only for `aop-audio`, `las`, and `als` at `drivers/soc/apple/aop.rs:753-774`.
Accordingly this slice maps the evidenced `aop-audio` service to the precedent
audio child and ALS to the precedent ALS child. It adds no LAS child because
the T8132 ADT has no LAS service. Accel/gyro have no corresponding
T8112/T8122 child nodes, and voice-trigger has no Linux service mapping, so all
three remain omitted.

The separate `aop-spmi0@90914000` exposes three `0x4000` windows and 21
interrupt entries at ADT lines 1690-1713, with `spmi-wifibt@e` and
`stockholm-spmi@9` consumers at lines 1715-1742. Neither the T8112 nor T8122
Linux device tree represents an AOP SPMI controller, and the pinned tree has no
Linux consumers for those two children. This unprecedented, consumer-less
controller is deliberately omitted from the AOP slice.

AOP and its DART have empty/no gate lists (`clock-gates` and `power-gates` are
empty at lines 3853 and 3870; the DART properties at lines 3988-4023 contain no
gate), matching the no-domain precedent. All four cluster nodes are disabled
by default, including the mailbox and DART providers. Any future board that
enables `aop` must enable `aop_mbox`, `aop_dart`, and `aop_admac` together;
enabling only the consumer violates the provider/consumer discipline already
recorded for SIO. The new T8132-first compatible entries remain binding-enum
work for any upstream submission.

cpufreq ruling (2026-08-31 DEFINE, recorded so no one re-derives it): the
DVFM register bases ARE ADT-derivable — pmgr's reg list carries
`0x1_10e20000/0x1268` (E) and `0x1_11e20000/0x12e8` (P), the exact T8112
pattern, translating to `0x3_10e20000`/`0x3_11e20000`. The pmgr node's
`voltage-states1`/`voltage-states5` pairs decode as (frequency-field,
millivolts) with millivolts confirmed, but the frequency-field unit/
conversion rule is NOT provable from on-machine evidence (period-vs-tick
ambiguity; no precedent ADT exists in this repo and the bootloader
project's decoder is out of scope for this pipeline). The cpufreq driver
requires DT OPP tables, so the slice is BLOCKED ON EVIDENCE — do not ship
guessed frequencies. What would unblock it: a documented voltage-states
format from a source this pipeline may use, or first-boot DVFM register
readback.

## Ranked next contributions

Completed and moved out of the ranking: J713 Type-C now has nub-spmi-a0,
HPM0/HPM1/HPM5 (`apple,sn201202x`), connector graphs, SoC
role/orientation/mode switching, crossbars, and board USB enablement. The main
three-port PCIe/WLAN/BT root complex and its single DART are now described as
disabled SoC units; board enablement remains intentionally separate. The SIO
DMA cluster and the AOP/mailbox/DART/leap-ADMAC cluster are also described as
disabled SoC units with no board enablement.

1. Thunderbolt/PCIe-C as one unit: `apciec0/1`, `acio0/1`, their DARTs,
   PIODMA engines, and Type-C dependencies.
2. Audio transport driver work for `iop-audio,controller`, followed by codec
   I2C children and board sound wiring; the AOP/leap-ADMAC DT portion is done.
3. DCP/display after all firmware, carveout, mailbox and DART dependencies are mapped.
4. ISP, ANE and media accelerators.
5. cpufreq only after the T8132 cluster control registers are independently identified.

---

# HARDWARE NIGHT 1 RESULTS — 2026-09-01 (J713, first boots on silicon)

Full evidence: `omarchy-firstboot/logs/session-20260901-150214-recon.log` (mini) and
the boot-*.log files beside it. Live ADT: `recon/air-adt-j713.bin`
(loads with m1n1 proxyclient `m1n1.adt.load_adt`). Photos IMG_1312–1322 on the mini.

## Headline

**Linux 7.1.9 (own build) boots to an interactive busybox shell on J713** —
banner `Linux version 7.1.9+ (kb2uka@optiplex)` + `Machine model: Apple MacBook
Air (13-inch, M4, 2025)` rendered by simpledrm/fbcon on the internal panel —
with the storage cluster disabled (see blocker below). AIC3 initializes:
`2048/4096 IRQs (1/2 dies), 7 FIQs, 32 vIPIs`. ttySAC0 (s5l) probes at IRQ 54.

## Boot requirements discovered (all mandatory on T8132)

1. Kernel must carry the `idle=<wfi|yield|nop>` early_param (asahi branch
   `arch/arm64/kernel/idle.c`; NOT in any Fedora release kernel ≤ 7.1.6-400)
   and be booted with **`idle=nop`** — M4 cores lose state on WFI.
2. `arch/arm64/lib/delay.c` WFxT path (`wfit`/`wfet`) must be disabled — M4
   advertises WFxT and dies on `wfit`; `idle=nop` does not cover delay loops.
3. The AIC driver's EL2 writes in `aic_init_cpu` (`SYS_IMP_APL_VM_TMR_FIQ_ENA_EL2`,
   `SYS_ICH_HCR_EL2`) must be skipped (= Yureka hack 7c74add40c06), plus the
   VM_TMR mask-path writes in our asahi-branch driver. Without this the boot
   dies inside irqchip init: silent black screen, unable even to panic.
   Kernel-side hacks: `patches-kernel/m4-boot-hacks-7.1.9.patch` (applies to
   asahi branch @ 77cb8f24c; built on the OptiPlex at `~/t8132-build/linux`).
4. Boot args that work: `nosmp idle=nop console=tty0 fbcon=nodefer loglevel=7`
   (SMP untested beyond nosmp — Yureka: unstable even with the patches).

## Checklist outcomes

1. **USB DART stream IDs — RESOLVED (ADT), fix committed as patch 0012.**
   dart-usb0/1: mapper child reg = **1**, sid = <1, 14>, IRQs **1283/1345
   confirmed**, dart-id 10/11, compatible dart,t8110 confirmed by both m1n1
   and Linux (`apple-dart ... 16 streams ... initialized` on screen).
   `iommus` now `<&dartX_0 1>, <&dartX_1 14>`. NOT yet exercised (USB blocked
   behind item 4 wedge + dwc3 =m in test kernel).
2. **HPM select IRQ — PREMISE WRONG.** No i2c hpmBusManager nodes exist in the
   ADT. HPM is `/arm-io/nub-spmi-a0/hpm0`, compatible `usbc,sn201202x,spmi`,
   interrupts **11, 17, 19**, interrupt-parent 261. Re-derive the select-IRQ
   story against SPMI.
3. atcphy crossbar probe-write — untested (display bring-up scope).
4. **ascwrap +0x8000 mailbox windows — HARDWARE-IMPLICATED AS THE BOOT BLOCKER.**
   Bisect result: with ans_mbox+sart+nvme enabled the boot wedges silently at
   ~0.27s (after crypto initcalls); with them disabled it reaches the shell.
   No ANS/SART/NVMe probe line ever prints. Prime suspect: the ANS mailbox
   window derivation. **Next session: re-derive from the captured ADT**
   (`/arm-io/ans` reg list) and re-test. This blocks success-ladder item 2.
5. AOP — ADT ground truth captured: `/arm-io/aop` iop,ascwrap-v6, IRQs
   434/433/436/435, reg[0] 0x190600000/0x88000, reg[1] 0x190050000/0x4000,
   reg[2] 0x190C00000/0x1E0000, reg[3] 0x1882A8000/0x8, reg[4] 0x19062C000/0x3C008.
   ADMAC output-index still unconfirmed (no admac node at the probed path).
6. MTP — `/arm-io/mtp` iop,ascwrap-v6, IRQs 1114/1113/1116/1115,
   reg 0x194600000/0x88000 + 0x194050000/0x4000. HID GPIOs unconfirmed.

## New findings (not on the checklist)

- **atc2/atc3 power domains cannot power on J713** and wedge the boot when
  their subtree registers children (patch 0011 deletes them at board level,
  t6022-j475d precedent). m1n1 pmgr banner: 392 devices, 1 die.
- pmgr: `always-on domain nub_spio is not on at boot` + `nub_ocla` — review
  those two always-on markings against the ADT.
- **m1n1 v1.6.1-22-gc9ef56f hypervisor does NOT start on T8132**: EL2 crash at
  `hv_start+0x194` (ESR EC=0, cluster-1 core 0), self-reboots. No
  virtual-UART console on M4 until upstream m1n1 fixes it.
- m1n1 gaps (non-fatal): `cpufreq: Chip 0x8132 is unsupported`,
  `MCC: Failed to get mcc count!`.
- PCIe slice (patch 0007): m1n1 initializes controller 0, and Linux ran with the nodes
  present in the full-DT boot without wedging before 0.27s — deeper probing
  unobserved (blocked behind item 4 in log order? unconfirmed).
- Fedora kernel-16k packaging: the T8132 driver set (apple-dart, nvme-apple,
  sart, pinctrl, i2c-apple, dockchannel, admac, spi) is =m but present in
  NEITHER kernel-16k-core nor kernel-16k-modules extractions we hold —
  build them built-in (current test kernel does) and boot without initramfs,
  or with the busybox initramfs purely for the shell.

## Next session, in order

1. Re-derive the ANS mailbox/asc window from `recon/air-adt-j713-*.bin`;
   fix the storage slice; rebuild dtb; boot → expect nvme0n1 in
   /proc/partitions (ladder item 2a).
2. Re-enable smc/mtp/usb groups stepwise on top (each was disabled during
   bisect for isolation, none yet individually cleared).
3. SMC driver: not in the test kernel (config symbol mismatch — find the
   asahi branch's actual macsmc Kconfig names) → ladder item 2b.
4. Keep the OptiPlex tree (`~/t8132-build/linux`, asahi @ 77cb8f24c + hacks
   patch) — incremental rebuilds are ~3 min. Revert plan if ever needed:
   `rm -rf ~/t8132-build; pacman -Rns lld bc` (snapshot ~/pkgs-before-t8132.txt).

---

# NIGHT-2 OFFLINE ADT RECONCILIATION — 2026-09-01 (J713)

This section supersedes the night-1 “re-derive” action. The live ADT was
parsed directly with `tools/adt.py` and the compiled J713 DTB was audited with
`tools/adt-audit.py`. Full evidence and the 49-row MMIO comparison are in
`docs/t8132-adt-audit.md`.

## Storage cluster — result

The silent 0.27 s wedge now has a concrete, ADT-proven candidate cause —
a hypothesis until the rung-3 boot below confirms it. The live `/arm-io/ans`
node contains the empty `nvme-secure-bar` property and thirteen register
tuples. On this M4 layout, `reg[3] = 0x2_85cc0000/0x60000` (Linux
`0x4_85cc0000`) is the NVMMU block, while `reg[9] =
0x2_c5cc0000/0x10000` (Linux `0x4_c5cc0000`) is the NVMe controller secure
BAR. The old DT named `reg[3]` as `"nvme"`, so the first controller access
landed in NVMMU registers. That is consistent with a silent failure before
any NVMe probe message; it is not yet shown to be the only cause (the
`ps_ans` sequencing and the mailbox interface remain unexercised on this
machine).

Patch 0013 moves `"nvme"` to `reg[9]`, adds `reg[3]` as a separate
`"nvmmu"` resource, preserves the ANS control resource, and changes the
compatible to the single string `apple,t8132-nvme-ans2` — the generic
`apple,nvme-ans2` fallback is deliberately dropped so that a kernel without
the split-window driver does not bind the node (it would map NVMMU registers
past the 0x28000 controller window and fault); the DT and kernel change
travel together. The matching
driver change is `patches-kernel/nvme-apple-t8132-split-nvmmu.patch`: it maps
the named NVMMU resource and writes the IO SQ/CQ DMA bases to controller
offsets 0x1200/0x1208 after queue creation. The Linux controller resource is
intentionally `0x28000`, larger than the ADT's `0x10000`, because the proven
M4 sequence reaches controller offsets through 0x24910.

The rest of the reconciliation is unchanged: IRQ index 4 selects 1018;
mailbox IRQs are 1004/1003/1006/1005; `iommu-parent=0xd1` resolves to the v3
`sart-ans`; and live PMGR proves `ps_ans@538`, `ps_apcie_st@540`, and their
parent chain. Still unproven on this J713 are successful mailbox operation at
wrapper `+0x8000`, the new IOQ base writes reaching the controller, and the
kernel's intentional mapping beyond the ADT-declared secure-BAR length.

## Night-2 storage bisect plan

Use the new driver kernel, identical boot arguments, and the same non-storage
DT state for every Linux boot. Capture a timestamped log and photograph the
last framebuffer line for each.

1. **Proxy reference probe:** run `p.nvme_init()` before Linux. Success proves
   the reference storage sequence can bring up this exact ANS/NVMMU/NVMe
   instance. A hang or error is a pre-Linux result and must be preserved before
   changing the boot ladder.
2. **Control boot, storage off:** boot the new kernel with `ans_mbox`, `sart`,
   and `nvme` disabled. Expected signature: the known busybox shell and no
   NVMe device. Failure invalidates the new-kernel control and stops the test.
3. **Full fixed storage:** enable `ans_mbox`, `sart`, and the patch-0013 NVMe
   node together. Expected signature: Apple SART/NVMe/RTKit probe output and
   `nvme0n1` in `/proc/partitions`. Successful IO proves mailbox operation,
   split resource mapping, IOQ base programming, and the 0x28000 mapping in
   one hardware result.

Only if rung 3 still wedges, use the isolation fallbacks:

4. **Fallback mbox-only:** enable only `ans_mbox`. A shell with no NVMe clears
   mailbox provider registration; a wedge isolates `ps_ans` activation or the
   `+0x8000` mailbox interface.
5. **Fallback mbox+sart:** enable `ans_mbox` and `sart`, leaving `nvme` off. A
   shell plus SART initialization clears the v3 SART provider. A new wedge at
   this rung isolates SART setup; success shifts the remaining failure to the
   NVMe consumer, IOQ writes, or secure-BAR overmap.

If the full fixed rung dies before any driver line, repeat it once with
`initcall_debug` and targeted dynamic debug for Apple PMGR, mailbox, SART, and
NVMe built in. Keep the fixed addresses unchanged between fallback rungs.

## HPM / Type-C PD over SPMI — corrected result

J713 HPM is not I2C. `/arm-io/nub-spmi-a0` is `aapl,spmi` with first window
`0x1_88908000/0x4000` (Linux `0x3_88908000`, leading `0x100` mapped) and
parent AIC IRQ 520. The other two 16 KiB windows are not consumed by the
Linux controller interface. The ADT publishes one-cell child IRQs, but the
Linux Apple SPMI binding and IRQ translator require two cells:
`<hwirq IRQ_TYPE_EDGE_RISING>`.

The first HPM `reg` cell is the SPMI USID: hpm0=0xc, hpm1=0xa, hpm5=0x8.
The rest of the six-cell ADT descriptor is Apple-private bus metadata; it is
not copied into Linux's standardized two-cell address. Linux correctly uses
`<USID SPMI_USID>`, where `SPMI_USID` is zero.

ADT interrupt types 0/2/3 are primary/sleep/wake:

| HPM | ADT IRQs | Linux IRQs | Evidence status |
|---|---|---|---|
| hpm0 | 11, 17, 19 | 11, **13**, 17, 19 | select 13 is T8122 `irq+2` precedent |
| hpm1 | 37, 43, 45 | 37, **39**, 43, 45 | select 39 is T8122 `irq+2` precedent |
| hpm5 | 63, 69, 71 | 63, **65**, 69, 71 | select 65 is T8122 `irq+2` precedent |

The SN201202x driver requests all four names and cannot probe with only the
three ADT-published events. The bold select values remain
**INHERITED-UNPROVEN**. A successful logical-register selection completion is
the hardware proof; a select timeout rejects the inference.

Port mapping is direct rather than positional guesswork. hpm0 `dock=0x168`,
port 1, left-front matches atc-phy0/usb-drd0
`function-dock_parent=0x168`. hpm1 `dock=0x169`, port 2, left-rear matches
atc-phy1/usb-drd1. hpm5 has dock 0x16a, no location, empty transports, and
internal port type 0x11; no ATC PHY references it, so it remains instantiated
without a connector. The current J713 include and reciprocal Type-C graph are
correct. `/arm-io/aop-spmi0` serves Wi-Fi/Bluetooth power and NFC consumers;
it is not required for HPM and remains an informational ADT-only gap.

## Remaining precedent-derived hardware checks

1. ANS mailbox `+0x8000`, NVMe 4 KiB `ans` control resource, and dual PMGR
   attach/reset ordering: settle with the storage ladder above.
2. HPM select IRQs 13/39/65: settle at SN201202x register selection.
3. AOP ADMAC output slot 2: the ADT proves IRQ 478 but no slot; settle with an
   AOP audio DMA completion.
4. MTP AFE/STM reset GPIOs 8/24: no live MTP, DockChannel, SMC-GPIO, AP-GPIO,
   or AOP-GPIO property names them; settle at keyboard/trackpad interface
   start.
5. NVMMU TCB invalidation status: the Linux driver reads
   `APPLE_NVMMU_TCB_STAT` at NVMMU +0x28120 (inherited from M1), while the
   proven M4 bare-metal reference reads its status word at +0x29120. Every
   other NVMMU offset agrees. The read is warning-path only (it never gates
   I/O), so the offset is left alone tonight; if rung 3 shows repeated
   "NVMMU TCB invalidation failed" warnings, or silently loses invalidations,
   check this offset first.
6. `NUB_SPI0`/`NUB_OCLA` are ADT always-on and correctly marked in DT. The
   boot warning reports an unexpected initial hardware state, not a DT flag
   mismatch; use a focused PMGR state transition probe before proposing any
   change.
