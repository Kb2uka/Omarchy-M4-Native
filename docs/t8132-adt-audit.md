# T8132 J713 live-ADT audit

> Historical research record. For the final checkpoint and reproduction
> limits, read [the project README](../README.md). Raw machine captures and
> bench binaries referenced below are not part of the public tree.

Date: 2026-09-01

Machine: Mac16,12 / J713 / T8132
Authority: `recon/air-adt-j713.bin`

## Executive summary

After patch 0013, the compiled `t8132-j713.dtb` has 48 address-correlated
`MATCH` rows, one `MATCH-OVERSIZE` row (the NVMe controller window, mapped
0x28000 wide where the ADT declares 0x10000 — proven by the M4 reference
sequence, not by the ADT), no `MISMATCH` row, and two `DT-ONLY` rows. One
boot-critical pre-fix mismatch is recorded below as `MISMATCH-FIXED`: the old
NVMe node mapped ANS reg[3] as the controller instead of the secure BAR in
reg[9]. The DT-only rows are the disabled SoC-level I2C0 and I2C4 controller
descriptions; neither controller exists in this J713 ADT, so they must remain
disabled on this board. There are 64 direct `/arm-io` `ADT-ONLY` nodes. They
are informational gaps, chiefly Thunderbolt/PCIe-C, display, media, audio
transport, and unused auxiliary controllers; a live node is not by itself a
complete Linux binding.

The PMGR table contains exactly 392 48-byte records. Its `ps-regs` property
is sixteen 12-byte records: little-endian `(reg_index, offset,
valid-device-mask)`. All 392 `group_and_offset` fields are zero, so
`psreg_idx` is the selector on this machine. Of 129 providers in
`t8132-pmgr.dtsi`, 112 match the live record's label, register offset,
represented parents, and always-on flag. Fourteen atc2/atc3 providers are
virtual-only on J713 and are already removed from the compiled board by patch
0011. The remaining three table differences are explicit Linux policy:
`aic`, `disp_sys`, and `disp_fe` have `apple,always-on` although the
ADT device flags do not assert bit 0x08.

Verdict meanings:

- `MATCH`: every Linux MMIO resource is equal to or contained in an
  ADT-described window, IRQ selection follows an explicit ADT value/selector,
  IOMMU stream IDs agree with the selected mapper/DART, and referenced PMGR
  providers exist on J713.
- `MATCH-OVERSIZE`: as `MATCH`, except one Linux resource is deliberately
  larger than the ADT window it starts in; the justification must be stated
  in the row and is external to the ADT.
- `SID-UNVERIFIED`: the node carries `iommus`, but the ADT offers no mapper
  or `sid` list to compare against; stream IDs are unverified, not confirmed.
  (No row currently carries this verdict.)
- `MISMATCH`: the live ADT directly contradicts the compiled DT.
- `DT-ONLY`: no live ADT MMIO window contains the DT resource.
- `ADT-ONLY`: a direct live `/arm-io` MMIO node has no Linux DT resource;
  this is a gap, not an instruction to add a partial node.

Containment is intentional for Linux interfaces that expose a driver-defined
subwindow of a larger Apple wrapper. It is not evidence that the subwindow's
role has been exercised on hardware.

## Storage cluster — ADT reconciliation

| Node / field | Linux DT value | Live ADT evidence | Verdict |
|---|---|---|---|
| `/arm-io/ans reg[0]` wrapper | `nvme` resource `ans = 0x4_81600000/0x4000` | `reg[0] = 0x2_81600000/0x88000`, translated by range 0 to `0x4_81600000/0x88000` | INHERITED-UNPROVEN: the 4 KiB CPU-control resource is the T8122/Linux driver interface and is contained in the ADT wrapper |
| `ans_mbox reg` | `0x4_81608000/0x4000` | contained at `reg[0] + 0x8000`; no independent ADT tuple | INHERITED-UNPROVEN: ascwrap-v6 and the T8122 mailbox convention prove the model, but the last hardware run did not exercise ANS |
| `ans reg[1]` | omitted | `0x2_81050000/0x4000`, translated to `0x4_81050000/0x4000`; role absent | MATCH: omission is deliberate; no Linux resource or evidenced role |
| mailbox IRQs and names | 1004/1003/1006/1005 as send-empty/send-not-empty/recv-empty/recv-not-empty | `interrupts = [1004,1003,1006,1005,1018]`; ascwrap-v6 role order | MATCH |
| `/arm-io/ans reg[9]` secure BAR | `nvme = 0x4_c5cc0000/0x28000` | empty `nvme-secure-bar`; `reg[9] = 0x2_c5cc0000/0x10000`, translated to `0x4_c5cc0000`; the proven M4 sequence accesses controller offsets through 0x24910 | MATCH-OVERSIZE (reference-proven; the ADT declares only 0x10000) |
| NVMe MMIO | three windows: `nvme = 0x4_c5cc0000/0x28000`, `nvmmu = 0x4_85cc0000/0x60000`, `ans = 0x4_81600000/0x4000` | ANS reg[9] is the controller secure BAR, reg[3] is NVMMU, and reg[0] is the ascwrap-v6 wrapper | MISMATCH-FIXED (M4 split window): patch 0013 replaces the old two-window node that incorrectly named reg[3] as `nvme` |
| NVMe IRQ | 1018 | `nvme-interrupt-idx = 4` selects ANS interrupt 1018 | MATCH |
| `reg-names` | `"nvme", "nvmmu", "ans"` | ADT supplies positions and `nvme-secure-bar`, while the split resource names are the T8132 driver contract | MATCH: names align patch 0013 with the split-NVMMU driver interface |
| `mboxes` | `<&ans_mbox>` | mailbox and NVMe functions are subinterfaces of the same `/arm-io/ans` ascwrap-v6 node | INHERITED-UNPROVEN: Linux binding structure |
| `apple,sart` | `<&sart>` | ANS `iommu-parent = <0xd1>`; phandle 0xd1 resolves exactly to `/arm-io/sart-ans`; there is no ANS DART | MATCH |
| SART registers | `0x4_85c50000/0xc000` | `sart-ans reg[0] = 0x2_85c50000/0xc000`; two additional windows are `0x4_85d44000/0x4000` and `0x4_85cc0000/0x4000` | MATCH: the driver maps only resource index 0 |
| SART layout | terminal compatible `apple,t6000-sart` | `sart-version = 3`; the driver maps `apple,t6000-sart` to v3 and `apple,t8103-sart` to v2 | MATCH |
| SART coastguard identity | Linux compatible chain models SART | ADT compatible is `sart,coastguard` | MATCH |
| ANS metadata | no Linux properties | `clock-ids = [0x15f,0x15e,0x160]`, `role = "ANS2"`, empty clock/power gate arrays | MATCH: recorded evidence; the current Linux binding has no corresponding properties |
| NVMe power domains | `<&ps_ans>, <&ps_apcie_st>`, names `ans`, `apcie0` | live PMGR has ANS at 0x538 and APCIE_ST at 0x540 with APCIE_ST parent ANS | INHERITED-UNPROVEN: provider values and ancestry are ADT-proven; attaching both to NVMe follows T8122 |
| NVMe reset | `resets = <&ps_ans>` | live ANS PMGR provider exists and exposes reset cells | INHERITED-UNPROVEN: reset relationship follows T8122 rather than an ADT reset property |

The ADT's `nvme-secure-bar` plus reg[9] pair is the static evidence that
contradicted the old storage slice. Moving the mailbox to ANS reg[1] remains
unsupported: reg[1] is a separate, unnamed 16 KiB window at wrapper-base
minus 0x5b0000, while the mailbox convention is inside reg[0] at +0x8000.

## PMGR reconciliation details

The physical power-state address rule used here is:

`translated pmgr reg[reg_index] base + ps-reg offset + (addr_offset << 3)`.

Linux child `reg` is the final two terms, relative to the selected PMGR
node. `ps_ans` is record ANS, `psreg_idx=5`, `addr_offset=7`:
`0x500 + 7*8 = 0x538`. `ps_apcie_st` uses the same table entry and
`addr_offset=8`: `0x540`; its parent ID 148 resolves to ANS. The complete
129-provider table below performs this same comparison for every source
provider.

The ADT virtual bit is `flags & 0x10`. All atc2/atc3 source providers named
in patch 0011 have only virtual records on this J713. The compiled board
contains 115 PMGR providers after those fourteen deletions. `NUB_SPI0`
(the boot message spells it `nub_spio`) and `NUB_OCLA` are real records,
both have flag bit 0x08, offsets `pmgr_mini + 0x80` and `+0x88`, and both
are marked `apple,always-on` in DT. The boot-time complaint means firmware
found an ADT-declared always-on domain in the off state; it does not justify
removing the DT flag. No DT change is proposed unless a focused power-on probe
shows the Linux PMGR driver cannot safely bring one on.

The three `always-on` table mismatches (`aic`, `disp_sys`, `disp_fe`)
are not changed. AIC is core boot infrastructure. The display nodes already
say their flags are a Linux power-management workaround. The live ADT does not
prove that removing those safeguards is safe.

## HPM / Type-C PD over SPMI

`/arm-io/nub-spmi-a0` translates to `0x3_88908000/0x4000`; Linux maps the
driver-used leading 0x100 bytes. Its ADT interrupt list is
`[256,520,281,260,261,...]`; the parallel interrupt-parent list selects the
AIC for 520, which is the Linux parent IRQ. The Apple SPMI binding permits one
optional parent IRQ and requires two child interrupt cells. The controller
driver rejects child specifiers that are not two cells and consumes
`<hwirq trigger-type>`.

For HPM children, the first ADT `reg` cell is the 4-bit SPMI USID:
hpm0=0xc, hpm1=0xa, hpm5=0x8. The remaining ADT cells
(`3,0,4,0,0`) are Apple bus-private descriptor data, not the Linux
two-cell SPMI address. Linux therefore correctly emits
`<USID SPMI_USID>`, where `SPMI_USID` is binding value zero.

The ADT supplies interrupt types 0,2,3: primary IRQ, sleep completion, and
wake completion. It omits type 1, select completion. The Linux SN201202x
driver hard-requires all four names. Current values 13/39/65 are the T8122
template's `primary+2` inference; they are not live-ADT-confirmed. A probe
that reaches SN201202x bind and completes a logical register selection settles
them. A timeout in select completion, with the other IRQs functional, rejects
the inference.

Port association is directly cross-checked:

- hpm0 has `dock=0x168`, `port-number=1`, and
  `port-location="left-front"`; atc-phy0 and usb-drd0 carry
  `function-dock_parent=0x168` and port 1.
- hpm1 has `dock=0x169`, `port-number=2`, and
  `port-location="left-rear"`; atc-phy1 and usb-drd1 carry
  `function-dock_parent=0x169` and port 2.
- hpm5 has `dock=0x16a`, no port-location, empty
  `transports-supported`, and internal `port-type=0x11`. No J713 ATC PHY
  references dock 0x16a, so it correctly has no connector graph.

The reciprocal connector graph in `t8132-j713` maps hpm0 to atc0/DWC3-0 and
hpm1 to atc1/DWC3-1. `/arm-io/aop-spmi0` is a separate ADT-only controller
for Wi-Fi/Bluetooth power and NFC children; it is not a Type-C dependency and
needs no node for tonight's HPM path.

## Findings, ranked by boot impact

1. **The storage wedge had a boot-critical DT mismatch.** The empty
   `nvme-secure-bar` property and ANS reg[9] identify the M4 controller BAR;
   reg[3] is only NVMMU. Patch 0013 supplies the split three-window node and
   the matching driver patch programs the new IOQ base registers. The next
   discriminators are proxy-side `p.nvme_init()`, a new-kernel control boot,
   and then the full fixed storage boot.
2. **J713 depopulation is real and already fixed.** Fourteen atc2/atc3 PMGR
   providers are virtual-only in the live table; patch 0011 removes exactly
   those providers from J713.
3. **USB stream IDs are live-ADT-proven and already fixed.** Both DWC3
   consumers now use SIDs 1 and 14, matching the two DART instances; patch
   0012 covers both ports.
4. **HPM select IRQ is still inferred.** All nine ADT-published HPM IRQs,
   controller IRQ 520, USIDs, port numbers, and connector associations match.
   Only 13/39/65 need a live completion test.
5. **AOP ADMAC interrupt output slot 2 is inherited.** The ADT proves IRQ 478,
   the leap-ns 0x34000 window, SID 10, and nine channels, but carries no output
   slot. AOP audio DMA completion is the settling probe.
6. **MTP reset GPIOs 8/24 are inherited.** No property under `/arm-io/mtp`,
   `dockchannel-mtp`, `smc-gpio`, or the AP/AOP GPIO nodes names those
   reset lines. Keyboard/trackpad interface start is the settling probe.
7. **PCIe, SPI, and Type-C MMIO are internally consistent.** The PCIe root
   eight selected resources, IRQs 1231/1240/1249, DART IRQ 1232, WLAN SID 1,
   Bluetooth SID 2, and RID split 0x100/0x101 agree. SPI2/SPI4 addresses,
   IRQs, and live PMGR providers agree. The Type-C graph is reciprocal and
   matches dock IDs 0x168/0x169.
8. **ADT-only gaps stay informational.** High-impact omissions are
   Thunderbolt/PCIe-C, DCP/display, audio transport/base ADMAC, ISP/ANE/media,
   and auxiliary SPMI/PIODMA controllers. None is safe to add solely from an
   address match.

## Reproduction

From the project worktree root:

```sh
clang -E -nostdinc -I linux-asahi/include -undef -D__DTS__ \
  -x assembler-with-cpp \
  linux-asahi/arch/arm64/boot/dts/apple/t8132-j713.dts \
  | dtc -O dtb -o /tmp/t8132-j713.dtb -I dts -

python3 tools/adt-audit.py \
  recon/air-adt-j713.bin \
  /tmp/t8132-j713.dtb \
  --pmgr-source linux-asahi/arch/arm64/boot/dts/apple/t8132-pmgr.dtsi
```

## Full audit tables
## ADT `/arm-io` ranges

| index | child | parent | size | translation |
|---|---|---|---|---|
| 0 | 0x0 | 0x200000000 | 0x3a0000000 | +0x200000000 |
| 1 | 0x400000000 | 0x400000000 | 0x4074000 | identity |
| 2 | 0x408000000 | 0x408000000 | 0x4074000 | identity |
| 3 | 0x410000000 | 0x410000000 | 0x4074000 | identity |
| 4 | 0x418000000 | 0x418000000 | 0x4074000 | identity |
| 5 | 0x800000000 | 0x800000000 | 0x200000000 | identity |
| 6 | 0xc00000000 | 0xc00000000 | 0x200000000 | identity |
| 7 | 0x1000000000 | 0x1000000000 | 0x200000000 | identity |
| 8 | 0x1400000000 | 0x1400000000 | 0x200000000 | identity |
| 9 | 0xa00000000 | 0xa00000000 | 0x80000000 | identity |
| 10 | 0xe00000000 | 0xe00000000 | 0x80000000 | identity |
| 11 | 0x1200000000 | 0x1200000000 | 0x80000000 | identity |
| 12 | 0x1600000000 | 0x1600000000 | 0x80000000 | identity |
| 13 | 0x1c50000000 | 0x1c50000000 | 0x10000000 | identity |
| 14 | 0x1c60000000 | 0x1c60000000 | 0x10000000 | identity |
| 15 | 0x1c70000000 | 0x1c70000000 | 0x10000000 | identity |
| 16 | 0x1c80000000 | 0x1c80000000 | 0x10000000 | identity |
| 17 | 0x404000000 | 0x404000000 | 0x74000 | identity |
| 18 | 0x40c000000 | 0x40c000000 | 0x74000 | identity |
| 19 | 0x414000000 | 0x414000000 | 0x74000 | identity |
| 20 | 0x41c000000 | 0x41c000000 | 0x74000 | identity |
| 21 | 0x490000000 | 0x490000000 | 0x7060000 | identity |
| 22 | 0x1cb0000000 | 0x1cb0000000 | 0x10000000 | identity |
| 23 | 0xbc0000000 | 0xbc0000000 | 0x20000000 | identity |
| 24 | 0xb80000000 | 0xb80000000 | 0x40000000 | identity |
| 25 | 0xac0000000 | 0xac0000000 | 0x20000000 | identity |
| 26 | 0xa80000000 | 0xa80000000 | 0x40000000 | identity |

## DT node audit

| verdict | DT node | ADT node | reg | IRQs DT vs ADT | SIDs DT vs ADT | power domains |
|---|---|---|---|---|---|---|
| MATCH | /soc/power-management@380700000 | /arm-io/pmgr | DT 0x380700000/0x14000; ADT 0x380700000/0xcc000, 0x388280000/0x80000, 0x380000000/0x7c000, 0x388200000/0x7c000, 0x388004000/0x1000, 0x210e20000/0x1268, 0x210f00000/0x40088, 0x210e40000/0xc010, 0x210e50000/0x9048, 0x210e70000/0xffe8, 0x210050000/0x9010, 0x210150000/0x9010, 0x210250000/0x9010, 0x210350000/0x9010, 0x210450000/0x9010, 0x210550000/0x9010, 0x211e20000/0x12e8, 0x211f00000/0x40088, 0x211e40000/0xc010, 0x211e50000/0x9048, 0x211e70000/0xffe8, 0x211050000/0x9018, 0x211150000/0x9018, 0x211250000/0x9018, 0x211350000/0x9018, 0x380280000/0x8000, 0x388700000/0x4000, 0x388100000/0xa8000, 0x50195c000/0x4000, 0x501960000/0x4000, 0x501974000/0x4000, 0x380208000/0x8000, 0x380700000/0x18000, 0x380728000/0x4000, 0x380724000/0x4000, 0x380764000/0x4000, 0x380780000/0x4000, 0x380760000/0x4000, 0x3882b8000/0x10000, 0x3882c8000/0x4000, 0x3882d0000/0x8000, 0x3803c0000/0x20000, 0x380730000/0x14000, 0x211000000/0xff4000, 0x501954000/0x4000, 0x380768000/0x4000, 0x38006f000/0x4000, 0x300e1c000/0x4000, 0x382000000/0x1000000 (contained) | - | - | - |
| MATCH | /soc/interrupt-controller@381180000 | /arm-io/aic | DT 0x381000000/0x1cc000, 0x381040000/0x4000; ADT 0x381000000/0x1cc000 (contained) | - | - | aic (live PMGR) |
| MATCH | /soc/power-management@388280000 | /arm-io/pmgr | DT 0x388280000/0x4000; ADT 0x380700000/0xcc000, 0x388280000/0x80000, 0x380000000/0x7c000, 0x388200000/0x7c000, 0x388004000/0x1000, 0x210e20000/0x1268, 0x210f00000/0x40088, 0x210e40000/0xc010, 0x210e50000/0x9048, 0x210e70000/0xffe8, 0x210050000/0x9010, 0x210150000/0x9010, 0x210250000/0x9010, 0x210350000/0x9010, 0x210450000/0x9010, 0x210550000/0x9010, 0x211e20000/0x12e8, 0x211f00000/0x40088, 0x211e40000/0xc010, 0x211e50000/0x9048, 0x211e70000/0xffe8, 0x211050000/0x9018, 0x211150000/0x9018, 0x211250000/0x9018, 0x211350000/0x9018, 0x380280000/0x8000, 0x388700000/0x4000, 0x388100000/0xa8000, 0x50195c000/0x4000, 0x501960000/0x4000, 0x501974000/0x4000, 0x380208000/0x8000, 0x380700000/0x18000, 0x380728000/0x4000, 0x380724000/0x4000, 0x380764000/0x4000, 0x380780000/0x4000, 0x380760000/0x4000, 0x3882b8000/0x10000, 0x3882c8000/0x4000, 0x3882d0000/0x8000, 0x3803c0000/0x20000, 0x380730000/0x14000, 0x211000000/0xff4000, 0x501954000/0x4000, 0x380768000/0x4000, 0x38006f000/0x4000, 0x300e1c000/0x4000, 0x382000000/0x1000000 (contained) | - | - | - |
| MATCH | /soc/watchdog@3882b0000 | /arm-io/wdt | DT 0x3882b0000/0x4000; ADT 0x3882b0000/0x4000, 0x3882bc224/0x4, 0x3882b8008/0x4, 0x3882b802c/0x4, 0x3882b8020/0x4 (contained) | [507] vs [507, 0] | - | - |
| MATCH | /soc/spmi@388714000 | /arm-io/nub-spmi0 | DT 0x388714000/0x100; ADT 0x388714000/0x4000, 0x388704000/0x4000, 0x388700000/0x4000 (contained) | - | - | - |
| MATCH | /soc/spmi@388908000 | /arm-io/nub-spmi-a0 | DT 0x388908000/0x100; ADT 0x388908000/0x4000, 0x388904000/0x4000, 0x388900000/0x4000 (contained) | [520] vs [256, 520, 281, 260, 261, 264, 265, 266, 268, 269, 279, 280, 282, 283, 284, 285, 272, 273, 262, 263, 267] | - | nub_spmi_a0 (live PMGR) |
| MATCH | /soc/smc@38c600000 | /arm-io/smc | DT 0x38c600000/0x4000, 0x38de00000/0x100000; ADT 0x38c600000/0x88000, 0x38c050000/0x4000, 0x38c810000/0x100 (contained) | - | - | - |
| MATCH | /soc/mbox@38c608000 | /arm-io/smc | DT 0x38c608000/0x4000; ADT 0x38c600000/0x88000, 0x38c050000/0x4000, 0x38c810000/0x100 (contained) | [562, 561, 564, 563] vs [562, 561, 564, 563] | - | - |
| MATCH | /soc/pinctrl@3881f0000 | /arm-io/nub-gpio | DT 0x3881f0000/0x4000; ADT 0x3881f0000/0x4000 (contained) | [499, 500, 501, 502, 503, 504, 505] vs [499, 500, 501, 502, 503, 504, 505] | - | nub_gpio (live PMGR) |
| MATCH | /soc/pinctrl@38c820000 | /arm-io/smc-gpio | DT 0x38c820000/0x4000; ADT 0x38c820000/0x4000 (contained) | [552, 553, 554, 555, 556, 557, 558] vs [552, 553, 554, 555, 556, 557, 558] | - | - |
| MATCH | /soc/mbox@390608000 | /arm-io/aop | DT 0x390608000/0x4000; ADT 0x390600000/0x88000, 0x390050000/0x4000, 0x390c00000/0x1e0000, 0x3882a8000/0x8, 0x39062c000/0x3c008 (contained) | [434, 433, 436, 435] vs [434, 433, 436, 435] | - | - |
| MATCH | /soc/iommu@390f00000 | /arm-io/dart-aop | DT 0x390f00000/0x4000; ADT 0x390f00000/0xc000, 0x390f10000/0x4000, 0x3882a4000/0x4000 (contained) | [457] vs [457] | - | - |
| MATCH | /soc/dma-controller@393300000 | /arm-io/admac-leap-ns | DT 0x393300000/0x34000; ADT 0x393300000/0x34000, 0x3882a8000/0x8 (contained) | [478] vs [478] | [10] vs [10] | - |
| MATCH | /soc/aop@390c00000 | /arm-io/aop | DT 0x390c00000/0x1e0000, 0x390600000/0x4000; ADT 0x390600000/0x88000, 0x390050000/0x4000, 0x390c00000/0x1e0000, 0x3882a8000/0x8, 0x39062c000/0x3c008 (contained) | - | [0] vs [0] | - |
| MATCH | /soc/pinctrl@390824000 | /arm-io/aop-gpio | DT 0x390824000/0x4000; ADT 0x390824000/0x4000 (contained) | [423, 424, 425, 426, 427, 428, 429] vs [423, 424, 425, 426, 427, 428, 429] | - | - |
| MATCH | /soc/mtp@394600000 | /arm-io/mtp | DT 0x394600000/0x4000, 0x394050000/0x4000; ADT 0x394600000/0x88000, 0x394050000/0x4000 (contained) | - | [0] vs [0] | - |
| MATCH | /soc/mbox@394608000 | /arm-io/mtp | DT 0x394608000/0x4000; ADT 0x394600000/0x88000, 0x394050000/0x4000 (contained) | [1114, 1113, 1116, 1115] vs [1114, 1113, 1116, 1115] | - | - |
| MATCH | /soc/iommu@394800000 | /arm-io/dart-mtp | DT 0x394800000/0x4000; ADT 0x394800000/0xc000, 0x394810000/0x4000, 0x3882a4000/0x4000 (contained) | [1091] vs [1091] | - | - |
| MATCH | /soc/fifo@394b14000 | /arm-io/dockchannel-mtp | DT 0x394b14000/0x1000; ADT 0x394b00000/0x1000, 0x394b14000/0x1000, 0x394b30000/0x1000, 0x394b34000/0x1000, 0x394b28000/0x1000, 0x394b2c000/0x1000 (contained) | [1093] vs [1093] | - | - |
| MATCH | /soc/fifo@394b14000/input@8000 | /arm-io/dockchannel-mtp | DT 0x394b30000/0x1000, 0x394b34000/0x1000, 0x394b28000/0x1000, 0x394b2c000/0x1000; ADT 0x394b00000/0x1000, 0x394b14000/0x1000, 0x394b30000/0x1000, 0x394b34000/0x1000, 0x394b28000/0x1000, 0x394b2c000/0x1000 (contained) | [2, 3] (local interrupt domain) | [0] vs [0] | - |
| MATCH | /soc/pinctrl@39a000000 | /arm-io/gpio0 | DT 0x39a000000/0x100000; ADT 0x39a000000/0x100000 (contained) | [303, 304, 305, 306, 307, 308, 309] vs [303, 304, 305, 306, 307, 308, 309] | - | gpio (live PMGR) |
| MATCH | /soc/iommu@3a8c80000 | /arm-io/dart-sep | DT 0x3a8c80000/0x4000; ADT 0x3a8c80000/0x20000, 0x38079c000/0x4000 (contained) | [399] vs [399] | - | - |
| MATCH | /soc/sep@3aa600000 | /arm-io/sep | DT 0x3aa600000/0x88000; ADT 0x3aa600000/0x88000, 0x3aa050000/0x60000 (contained) | - | [0] vs [0] | - |
| MATCH | /soc/mbox@3aa608000 | /arm-io/sep | DT 0x3aa608000/0x4000; ADT 0x3aa600000/0x88000, 0x3aa050000/0x60000 (contained) | [390, 389, 392, 391] vs [390, 389, 392, 391] | - | - |
| DT-ONLY | /soc/i2c@3ad010000 | - | 0x3ad010000/0x4000 | - | - | - |
| MATCH | /soc/i2c@3ad014000 | /arm-io/i2c1 | DT 0x3ad014000/0x4000; ADT 0x3ad014000/0x4000 (contained) | [1063] vs [1063] | - | i2c1 (live PMGR) |
| MATCH | /soc/i2c@3ad018000 | /arm-io/i2c2 | DT 0x3ad018000/0x4000; ADT 0x3ad018000/0x4000 (contained) | [1064] vs [1064] | - | i2c2 (live PMGR) |
| MATCH | /soc/i2c@3ad01c000 | /arm-io/i2c3 | DT 0x3ad01c000/0x4000; ADT 0x3ad01c000/0x4000 (contained) | [1065] vs [1065] | - | i2c3 (live PMGR) |
| DT-ONLY | /soc/i2c@3ad020000 | - | 0x3ad020000/0x4000 | - | - | - |
| MATCH | /soc/pwm@3ad044000 | /arm-io/fpwm1 | DT 0x3ad044000/0x4000; ADT 0x3ad044000/0x4000 (contained) | - | - | fpwm1 (live PMGR) |
| MATCH | /soc/iommu@3ad060000 | /arm-io/dart-sio | DT 0x3ad060000/0x4000; ADT 0x3ad060000/0xc000, 0x38079c000/0x4000 (contained) | [1074] vs [1074] | - | sio_cpu (live PMGR) |
| MATCH | /soc/spi@3ad108000 | /arm-io/spi2 | DT 0x3ad108000/0x4000; ADT 0x3ad108000/0x4000 (contained) | [1056] vs [1056] | - | spi2 (live PMGR) |
| MATCH | /soc/spi@3ad110000 | /arm-io/spi4 | DT 0x3ad110000/0x4000; ADT 0x3ad110000/0x4000 (contained) | [1058] vs [1058] | - | spi4 (live PMGR) |
| MATCH | /soc/serial@3ad200000 | /arm-io/uart0 | DT 0x3ad200000/0x1000; ADT 0x3ad200000/0x4000 (contained) | [1046] vs [1046] | - | uart0 (live PMGR) |
| MATCH | /soc/sio@3ade00000 | /arm-io/sio | DT 0x3ade00000/0x4000; ADT 0x3ade00000/0x88000, 0x3ad850000/0x4000 (contained) | - | [0] vs [0] | sio_cpu (live PMGR) |
| MATCH | /soc/mbox@3ade08000 | /arm-io/sio | DT 0x3ade08000/0x4000; ADT 0x3ade00000/0x88000, 0x3ad850000/0x4000 (contained) | [1079, 1078, 1081, 1080] vs [1079, 1078, 1081, 1080] | - | sio_cpu (live PMGR) |
| MATCH | /soc/usb@402280000 | /arm-io/usb-drd0 | DT 0x402280000/0xcd00, 0x40228cd00/0x3200; ADT 0x402280000/0x11800, 0x402200000/0x4000, 0x40228c000/0x5800, 0x402a84000/0x4000, 0x402800000/0x4000, 0x402a80000/0x4000, 0x402080000/0x4000, 0x40228d000/0x4000, 0x402980000/0x4000 (contained) | [1279] vs [1279, 1280, 1281, 1282] | [1, 14] vs [1, 14] | atc0_usb (live PMGR) |
| MATCH | /soc/iommu@402f00000 | /arm-io/dart-usb0 | DT 0x402f00000/0x4000; ADT 0x402f00000/0xc000, 0x402f80000/0xc000, 0x38079c000/0x4000, 0x3882a4000/0x4000 (contained) | [1283] vs [1283] | - | atc0_usb (live PMGR) |
| MATCH | /soc/iommu@402f80000 | /arm-io/dart-usb0 | DT 0x402f80000/0x4000; ADT 0x402f00000/0xc000, 0x402f80000/0xc000, 0x38079c000/0x4000, 0x3882a4000/0x4000 (contained) | [1283] vs [1283] | - | atc0_usb (live PMGR) |
| MATCH | /soc/phy@403000000 | /arm-io/atc-phy0 | DT 0x403000000/0x4c000, 0x403050000/0x8000, 0x400000000/0x8000, 0x402a90000/0x4000, 0x402a84000/0x4000; ADT 0x402a90000/0x4000, 0x402800000/0x4000, 0x403000000/0x58000, 0x400000000/0x1000000 (contained) | - | - | atc0_usb (live PMGR) |
| MATCH | /soc/mux@40304c000 | /arm-io/atc0-dpxbar | DT 0x40304c000/0x4000; ADT 0x40304c000/0x4000 (contained) | - | - | atc0_usb (live PMGR) |
| MATCH | /soc/usb@40a280000 | /arm-io/usb-drd1 | DT 0x40a280000/0xcd00, 0x40a28cd00/0x3200; ADT 0x40a280000/0x11800, 0x40a200000/0x4000, 0x40a28c000/0x5800, 0x40aa84000/0x4000, 0x40a800000/0x4000, 0x40aa80000/0x4000, 0x40a080000/0x4000, 0x40a28d000/0x4000, 0x40a980000/0x4000 (contained) | [1341] vs [1341, 1342, 1343, 1344] | [1, 14] vs [1, 14] | atc1_usb (live PMGR) |
| MATCH | /soc/iommu@40af00000 | /arm-io/dart-usb1 | DT 0x40af00000/0x4000; ADT 0x40af00000/0xc000, 0x40af80000/0xc000, 0x38079c000/0x4000, 0x3882a4000/0x4000 (contained) | [1345] vs [1345] | - | atc1_usb (live PMGR) |
| MATCH | /soc/iommu@40af80000 | /arm-io/dart-usb1 | DT 0x40af80000/0x4000; ADT 0x40af00000/0xc000, 0x40af80000/0xc000, 0x38079c000/0x4000, 0x3882a4000/0x4000 (contained) | [1345] vs [1345] | - | atc1_usb (live PMGR) |
| MATCH | /soc/phy@40b000000 | /arm-io/atc-phy1 | DT 0x40b000000/0x4c000, 0x40b050000/0x8000, 0x408000000/0x8000, 0x40aa90000/0x4000, 0x40aa84000/0x4000; ADT 0x40aa90000/0x4000, 0x40a800000/0x4000, 0x40b000000/0x58000, 0x408000000/0x1000000 (contained) | - | - | atc1_usb (live PMGR) |
| MATCH | /soc/mux@40b04c000 | /arm-io/atc1-dpxbar | DT 0x40b04c000/0x4000; ADT 0x40b04c000/0x4000 (contained) | - | - | atc1_usb (live PMGR) |
| MATCH | /soc/mbox@481608000 | /arm-io/ans | DT 0x481608000/0x4000; ADT 0x481600000/0x88000, 0x481050000/0x4000, 0x485cc0000/0x60000, 0x483000000/0x1000000, 0x485b90000/0xc000, 0x485d47c00/0x4000, 0x485d60000/0x8000, 0x485d70000/0x8000, 0x4c5cc0000/0x10000, 0x485c00000/0x4000, 0x485c20000/0x4000, 0x485b00000/0x30000 (contained) | [1004, 1003, 1006, 1005] vs [1004, 1003, 1006, 1005, 1018] | - | ans (live PMGR) |
| MATCH | /soc/sart@485c50000 | /arm-io/sart-ans | DT 0x485c50000/0xc000; ADT 0x485c50000/0xc000, 0x485d44000/0x4000, 0x485cc0000/0x4000 (contained) | - | - | ans (live PMGR) |
| MATCH-OVERSIZE | /soc/nvme@4c5cc0000 | /arm-io/ans | DT 0x4c5cc0000/0x28000, 0x485cc0000/0x60000, 0x481600000/0x4000; ADT 0x481600000/0x88000, 0x481050000/0x4000, 0x485cc0000/0x60000, 0x483000000/0x1000000, 0x485b90000/0xc000, 0x485d47c00/0x4000, 0x485d60000/0x8000, 0x485d70000/0x8000, 0x4c5cc0000/0x10000, 0x485c00000/0x4000, 0x485c20000/0x4000, 0x485b00000/0x30000; MATCH-OVERSIZE (oversized by design: reference accesses to 0x24910) | [1018] vs [1018] | - | ans,apcie_st (live PMGR) |
| MATCH | /soc/iommu@490000000 | /arm-io/dart-apcie0 | DT 0x490000000/0x4000; ADT 0x490000000/0x20000, 0x38079c000/0x4000 (contained) | [1232] vs [1232] | - | apcie_phy_sw (live PMGR) |
| MATCH | /soc/pcie@1cb0000000 | /arm-io/apcie | DT 0x1cb0000000/0x10000000, 0x494000000/0x4000, 0x490028000/0x8000, 0x491028000/0x8000, 0x492028000/0x8000, 0x497020000/0x4000, 0x497024000/0x4000, 0x497028000/0x4000; ADT 0x1cb0000000/0x10000000, 0x494000000/0x4000, 0x497000000/0x40000, 0x497040000/0x20000, 0x496000000/0x1000000, 0x495046200/0x4000, 0x495044000/0x4000, 0x490028000/0x8000, 0x49003c000/0x4000, 0x497020000/0x4000, 0x497048000/0x8000, 0x490024000/0x4000, 0x490000000/0xc000, 0x490048000/0x4000, 0x490044000/0x4000, 0x491028000/0x8000, 0x49103c000/0x4000, 0x497024000/0x4000, 0x497050000/0x8000, 0x491024000/0x4000, 0x491000000/0xc000, 0x491048000/0x4000, 0x491044000/0x4000, 0x492028000/0x8000, 0x49203c000/0x4000, 0x497028000/0x4000, 0x497058000/0x8000, 0x492024000/0x4000, 0x492000000/0xc000, 0x492048000/0x4000, 0x492044000/0x4000 (contained) | [1231, 1240, 1249] vs [1231, 1240, 1249] | - | apcie_phy_sw (live PMGR) |

Node summary: DT-ONLY=2, MATCH=48, MATCH-OVERSIZE=1

## ADT-only MMIO nodes

| ADT node | translated registers |
|---|---|
| /arm-io/nub-spmi-a1 | 0x388a08000/0x4000, 0x388a04000/0x4000, 0x388a00000/0x4000 |
| /arm-io/aop-spmi0 | 0x390914000/0x4000, 0x390904000/0x4000, 0x390900000/0x4000 |
| /arm-io/apcie0-piodma | 0x490030000/0x4000, 0x490034000/0x4000, 0x490038000/0x4000 |
| /arm-io/acio-cpu0 | 0x401108000/0x4000, 0x401100000/0x4000 |
| /arm-io/apciec0 | 0x1c50000000/0x10000000, 0x404034000/0x4000, 0x404020000/0x8000, 0x404038000/0x4000, 0x404060000/0x4000, 0x400020000/0x4000, 0x40402c000/0x4000, 0x404028000/0x4000 |
| /arm-io/dart-apciec0 | 0x404000000/0x20000, 0x38079c000/0x4000 |
| /arm-io/apciec0-piodma | 0x404068000/0x4000, 0x40406c000/0x4000, 0x404070000/0x4000, 0x40403c000/0x4000 |
| /arm-io/acio0 | 0x401f00000/0x100000, 0x401db0000/0x30004, 0x401ac0000/0x4000, 0x401ac4000/0x4000, 0x401ac8000/0x4000, 0x401e44000/0x4000 |
| /arm-io/dart-acio0 | 0x401a80000/0x20000, 0x38079c000/0x4000 |
| /arm-io/atc0-dpin0 | 0x401e50000/0x4000 |
| /arm-io/atc0-dpin1 | 0x401e58000/0x4000 |
| /arm-io/acio-cpu1 | 0x409108000/0x4000, 0x409100000/0x4000 |
| /arm-io/apciec1 | 0x1c60000000/0x10000000, 0x40c034000/0x4000, 0x40c020000/0x8000, 0x40c038000/0x4000, 0x40c060000/0x4000, 0x408020000/0x4000, 0x40c02c000/0x4000, 0x40c028000/0x4000 |
| /arm-io/dart-apciec1 | 0x40c000000/0x20000, 0x38079c000/0x4000 |
| /arm-io/apciec1-piodma | 0x40c068000/0x4000, 0x40c06c000/0x4000, 0x40c070000/0x4000, 0x40c03c000/0x4000 |
| /arm-io/acio1 | 0x409f00000/0x100000, 0x409db0000/0x30004, 0x409ac0000/0x4000, 0x409ac4000/0x4000, 0x409ac8000/0x4000, 0x409e44000/0x4000 |
| /arm-io/dart-acio1 | 0x409a80000/0x20000, 0x38079c000/0x4000 |
| /arm-io/atc1-dpin0 | 0x409e50000/0x4000 |
| /arm-io/atc1-dpin1 | 0x409e58000/0x4000 |
| /arm-io/mcc | 0x380780000/0x4000, 0x260008000/0x8000, 0x261008000/0x8000, 0x262008000/0x8000, 0x263008000/0x8000, 0x264008000/0x8000, 0x265008000/0x8000, 0x220000000/0x2000000, 0x222000000/0x2000000, 0x3803c0000/0x20000, 0x380478000/0x149c |
| /arm-io/aic-timebase | 0x581180000/0x1000 |
| /arm-io/dwi | 0x380200000/0x4000 |
| /arm-io/pwm | 0x3ad040000/0x4000 |
| /arm-io/aes | 0x3ad00c000/0x4000, 0x3882dc000/0x8000, 0x388078000/0x4000 |
| /arm-io/pmp | 0x380e00000/0x88000, 0x380850000/0x4000, 0x380500000/0xc0000, 0x3803d0000/0x4000 |
| /arm-io/dart-pmp | 0x380300000/0xc000, 0x38079c000/0x4000 |
| /arm-io/aon-ptd | 0x38c840000/0x4000, 0x38c850000/0x4000, 0x38c858000/0x4000, 0x38c860000/0x4000 |
| /arm-io/dockchannel-uart | 0x388128000/0x10000, 0x38810c000/0x1000 |
| /arm-io/qspi | 0x3ad118000/0x4000, 0x3ad118800/0x4000 |
| /arm-io/nub-spmi1 | 0x388614000/0x4000, 0x388604000/0x4000, 0x388600000/0x4000 |
| /arm-io/disp0 | 0x308000000/0xc20000, 0x30a240000/0x10000, 0x30a3a8000/0x4000, 0x30a800000/0x800000, 0x382800000/0xbc000 |
| /arm-io/dcp0-expert | 0x3882b8044/0xc, 0x388030000/0x4 |
| /arm-io/dcp | 0x30ae00000/0x88000, 0x30a850000/0x4000, 0x30ae2c000/0x3c008 |
| /arm-io/dart-dcp | 0x30a340000/0x20000, 0x38079c000/0x4000 |
| /arm-io/dart-disp0 | 0x30a300000/0xc000, 0x30a314000/0x4000, 0x38079c000/0x4000 |
| /arm-io/dispext0 | 0x284000000/0xc20000, 0x286240000/0x10000, 0x2863a8000/0x4000, 0x286800000/0x800000, 0x382800000/0xbc000 |
| /arm-io/dcpext0-expert | 0x3882b8044/0xc, 0x388030000/0x4 |
| /arm-io/dcpext0 | 0x286e00000/0x88000, 0x286850000/0x4000, 0x286e2c000/0x3c008 |
| /arm-io/dart-dcpext0 | 0x286340000/0x20000, 0x38079c000/0x4000 |
| /arm-io/dart-dispext0 | 0x286300000/0xc000, 0x286314000/0x4000, 0x38079c000/0x4000 |
| /arm-io/dispext1 | 0x488000000/0xc20000, 0x48a240000/0x10000, 0x48a3a8000/0x4000, 0x48a800000/0x800000, 0x382800000/0xbc000 |
| /arm-io/dcpext1-expert | 0x3882b8044/0xc, 0x388030000/0x4 |
| /arm-io/dcpext1 | 0x48ae00000/0x88000, 0x48a850000/0x4000, 0x48ae2c000/0x3c008 |
| /arm-io/dart-dcpext1 | 0x48a340000/0x20000, 0x38079c000/0x4000 |
| /arm-io/dart-dispext1 | 0x48a300000/0xc000, 0x48a314000/0x4000, 0x38079c000/0x4000 |
| /arm-io/scaler0 | 0x505000000/0x38000, 0x505210000/0x4000, 0x505204000/0x4000, 0x504000000/0x8000 |
| /arm-io/dart-scaler | 0x505260000/0x20000, 0x38079c000/0x4000 |
| /arm-io/jpeg0 | 0x3a5000000/0x4000 |
| /arm-io/dart-jpeg0 | 0x3a5020000/0xc000, 0x38079c000/0x4000 |
| /arm-io/jpeg1 | 0x3a5060000/0x4000 |
| /arm-io/dart-jpeg1 | 0x3a5080000/0xc000, 0x38079c000/0x4000 |
| /arm-io/ave | 0x421100000/0x45c000, 0x421800000/0x800000, 0x421050000/0x4000, 0x380708000/0x4000, 0x420000000/0x1000000 |
| /arm-io/dart-ave | 0x421080000/0xc000, 0x4210a0000/0xc000, 0x421094000/0x4000, 0x4210b0000/0x4000, 0x38079c000/0x4000 |
| /arm-io/avd | 0x3a0000000/0x1404000 |
| /arm-io/dart-avd | 0x3a1020000/0x20000, 0x38079c000/0x4000 |
| /arm-io/apr | 0x281000000/0x4000, 0x280000000/0x1000000, 0x2810c0000/0x4000 |
| /arm-io/dart-apr | 0x281020000/0xc000, 0x38079c000/0x4000 |
| /arm-io/dart-isp | 0x48e8c0000/0xc000, 0x48e8e0000/0xc000, 0x48e900000/0xc000, 0x48e8f4000/0x4000, 0x48e914000/0x4000, 0x48e8d0000/0x4000, 0x38079c000/0x4000 |
| /arm-io/dart-ane | 0x501800000/0xc000, 0x501820000/0xc000, 0x501840000/0xc000, 0x501810000/0x4000, 0x38079c000/0x4000 |
| /arm-io/sgx | 0x300000000/0x4000000, 0x300d00000/0x180000 |
| /arm-io/gfx-asc | 0x302600000/0x88000, 0x302050000/0x8 |
| /arm-io/admac-base-ns | 0x393800000/0x34000, 0x3882a8000/0x8 |
| /arm-io/dbgCPU | 0x389700000/0x28000, 0x3895c0000/0x4000 |
| /arm-io/aft | 0x220180000/0xc000 |

ADT-only summary: 64

## PMGR decode

Devices: 392 records x 48 bytes; virtual records: 262; group_and_offset nonzero: 0.

| psreg | reg index | offset | valid mask |
|---|---|---|---|
| 0 | 0 | 0x0 | 0xfff |
| 1 | 0 | 0x100 | 0x3887ff9e |
| 2 | 0 | 0x200 | 0x7fffe760 |
| 3 | 0 | 0x300 | 0xffffc003 |
| 4 | 0 | 0x400 | 0x7d2643ff |
| 5 | 0 | 0x500 | 0x1f7c0 |
| 6 | 0 | 0xc00 | 0x1 |
| 7 | 0 | 0x4000 | 0x0 |
| 8 | 0 | 0x8000 | 0x1f |
| 9 | 0 | 0xc000 | 0xf |
| 10 | 0 | 0x10000 | 0x1 |
| 11 | 1 | 0x0 | 0x3f77801 |
| 12 | 1 | 0x4000 | 0x0 |
| 13 | 1 | 0x4100 | 0x0 |
| 14 | 1 | 0x8000 | 0x0 |
| 15 | 1 | 0xc000 | 0x0 |

| verdict | source | DT label | DT offset | ADT device | ADT reg:offset | DT parents | ADT parents | DT AON | ADT AON | reason |
|---|---|---|---|---|---|---|---|---|---|---|
| MATCH | ps_msg | msg | 0x108 | MSG | 0:0x108 | - | - | no | no | - |
| MISMATCH | ps_aic | aic | 0x110 | AIC | 0:0x110 | - | - | yes | no | always-on |
| MATCH | ps_dwi | dwi | 0x118 | DWI | 0:0x118 | - | - | no | no | - |
| MATCH | ps_gpio | gpio | 0x120 | GPIO | 0:0x120 | - | - | no | no | - |
| MATCH | ps_pmc | pmc | 0x138 | PMC | 0:0x138 | - | - | no | no | - |
| MATCH | ps_pms_fpwm0 | pms_fpwm0 | 0x140 | PMS_FPWM0 | 0:0x140 | - | - | no | no | - |
| MATCH | ps_pms_fpwm1 | pms_fpwm1 | 0x148 | PMS_FPWM1 | 0:0x148 | - | - | no | no | - |
| MATCH | ps_pms_fpwm2 | pms_fpwm2 | 0x150 | PMS_FPWM2 | 0:0x150 | - | - | no | no | - |
| MATCH | ps_pms_fpwm3 | pms_fpwm3 | 0x158 | PMS_FPWM3 | 0:0x158 | - | - | no | no | - |
| MATCH | ps_pms_fpwm4 | pms_fpwm4 | 0x160 | PMS_FPWM4 | 0:0x160 | - | - | no | no | - |
| MATCH | ps_pms_c1ppt | pms_c1ppt | 0x168 | PMS_C1PPT | 0:0x168 | - | - | no | no | - |
| MATCH | ps_soc_rc | soc_rc | 0x170 | SOC_RC | 0:0x170 | - | - | no | no | - |
| MATCH | ps_soc_dpe | soc_dpe | 0x178 | SOC_DPE | 0:0x178 | - | - | yes | yes | - |
| MATCH | ps_pmgr_soc_ocla | pmgr_soc_ocla | 0x180 | PMGR_SOC_OCLA | 0:0x180 | - | - | no | no | - |
| MATCH | ps_ap_tmm | ap_tmm | 0x1b8 | AP_TMM | 0:0x1b8 | - | - | no | no | - |
| MATCH | ps_dispext0_sys | dispext0_sys | 0x1d8 | DISPEXT0_SYS | 0:0x1d8 | - | - | no | no | - |
| MATCH | ps_dispext0_fe | dispext0_fe | 0x1e0 | DISPEXT0_FE | 0:0x1e0 | ps_dispext0_sys | dispext0_sys | no | no | - |
| MATCH | ps_dispext0_cpu | dispext0_cpu | 0x1e8 | DISPEXT0_CPU | 0:0x1e8 | ps_dispext0_fe | dispext0_fe | no | no | - |
| MATCH | ps_scodec | scodec | 0x228 | SCODEC | 0:0x228 | - | - | no | no | - |
| MATCH | ps_scodec_streaming | scodec_streaming | 0x230 | SCODEC_STREAMING | 0:0x230 | ps_scodec | scodec | no | no | - |
| MISMATCH | ps_disp_sys | disp_sys | 0x240 | DISP_SYS | 0:0x240 | - | - | yes | no | always-on |
| MISMATCH | ps_disp_fe | disp_fe | 0x248 | DISP_FE | 0:0x248 | ps_disp_sys | disp_sys | yes | no | always-on |
| MATCH | ps_gfx | gfx | 0x250 | GFX | 0:0x250 | - | - | no | no | - |
| MATCH | ps_sio_cpu | sio_cpu | 0x268 | SIO_CPU | 0:0x268 | - | - | no | no | - |
| MATCH | ps_fpwm0 | fpwm0 | 0x270 | FPWM0 | 0:0x270 | - | - | no | no | - |
| MATCH | ps_fpwm1 | fpwm1 | 0x278 | FPWM1 | 0:0x278 | - | - | no | no | - |
| MATCH | ps_fpwm2 | fpwm2 | 0x280 | FPWM2 | 0:0x280 | - | - | no | no | - |
| MATCH | ps_i2c0 | i2c0 | 0x288 | I2C0 | 0:0x288 | - | - | no | no | - |
| MATCH | ps_i2c1 | i2c1 | 0x290 | I2C1 | 0:0x290 | - | - | no | no | - |
| MATCH | ps_i2c2 | i2c2 | 0x298 | I2C2 | 0:0x298 | - | - | no | no | - |
| MATCH | ps_i2c3 | i2c3 | 0x2a0 | I2C3 | 0:0x2a0 | - | - | no | no | - |
| MATCH | ps_i2c4 | i2c4 | 0x2a8 | I2C4 | 0:0x2a8 | - | - | no | no | - |
| MATCH | ps_i2c5 | i2c5 | 0x2b0 | I2C5 | 0:0x2b0 | - | - | no | no | - |
| MATCH | ps_i2c6 | i2c6 | 0x2b8 | I2C6 | 0:0x2b8 | - | - | no | no | - |
| MATCH | ps_i2c7 | i2c7 | 0x2c0 | I2C7 | 0:0x2c0 | - | - | no | no | - |
| MATCH | ps_i2c8 | i2c8 | 0x2c8 | I2C8 | 0:0x2c8 | - | - | no | no | - |
| MATCH | ps_spi_p | spi_p | 0x2d0 | SPI_P | 0:0x2d0 | - | - | yes | yes | - |
| MATCH | ps_uart_p | uart_p | 0x2d8 | UART_P | 0:0x2d8 | - | - | yes | yes | - |
| MATCH | ps_dpa_p | dpa_p | 0x2e0 | DPA_P | 0:0x2e0 | - | - | yes | yes | - |
| MATCH | ps_aes | aes | 0x2e8 | AES | 0:0x2e8 | - | - | no | no | - |
| MATCH | ps_spi0 | spi0 | 0x2f0 | SPI0 | 0:0x2f0 | ps_spi_p | spi_p | no | no | - |
| MATCH | ps_afiaft | afiaft | 0x300 | AFIAFT | 0:0x300 | - | - | no | no | - |
| MATCH | ps_venc_sys | venc_sys | 0x308 | VENC_SYS | 0:0x308 | - | - | no | no | - |
| MATCH | ps_spi1 | spi1 | 0x370 | SPI1 | 0:0x370 | ps_spi_p | spi_p | no | no | - |
| MATCH | ps_atc0_common | atc0_common | 0x378 | ATC0_COMMON | 0:0x378 | - | - | no | no | - |
| MATCH | ps_spi2 | spi2 | 0x380 | SPI2 | 0:0x380 | ps_spi_p | spi_p | no | no | - |
| MATCH | ps_spi3 | spi3 | 0x388 | SPI3 | 0:0x388 | ps_spi_p | spi_p | no | no | - |
| MATCH | ps_spi4 | spi4 | 0x390 | SPI4 | 0:0x390 | ps_spi_p | spi_p | no | no | - |
| MATCH | ps_trace_fab | trace_fab | 0x398 | TRACE_FAB | 0:0x398 | - | - | no | no | - |
| MATCH | ps_atc0_pcie | atc0_pcie | 0x3a0 | ATC0_PCIE | 0:0x3a0 | ps_atc0_common | atc0_common | no | no | - |
| MATCH | ps_atc0_cio | atc0_cio | 0x3a8 | ATC0_CIO | 0:0x3a8 | ps_atc0_common | atc0_common | no | no | - |
| MATCH | ps_atc0_cio_pcie | atc0_cio_pcie | 0x3b0 | ATC0_CIO_PCIE | 0:0x3b0 | ps_atc0_cio | atc0_cio | no | no | - |
| MATCH | ps_spi5 | spi5 | 0x3b8 | SPI5 | 0:0x3b8 | ps_spi_p | spi_p | no | no | - |
| MATCH | ps_qspi | qspi | 0x3c0 | QSPI | 0:0x3c0 | ps_spi_p | spi_p | no | no | - |
| MATCH | ps_dptx_phy | dptx_phy | 0x3c8 | DPTX_PHY | 0:0x3c8 | - | - | no | no | - |
| MATCH | ps_uart_n | uart_n | 0x3d0 | UART_N | 0:0x3d0 | ps_uart_p | uart_p | no | no | - |
| MATCH | ps_uart0 | uart0 | 0x3d8 | UART0 | 0:0x3d8 | ps_uart_p | uart_p | no | no | - |
| MATCH | ps_uart1 | uart1 | 0x3e0 | UART1 | 0:0x3e0 | ps_uart_p | uart_p | no | no | - |
| MATCH | ps_uart2 | uart2 | 0x3e8 | UART2 | 0:0x3e8 | ps_uart_p | uart_p | no | no | - |
| MATCH | ps_uart3 | uart3 | 0x3f0 | UART3 | 0:0x3f0 | ps_uart_p | uart_p | no | no | - |
| MATCH | ps_uart4 | uart4 | 0x3f8 | UART4 | 0:0x3f8 | ps_uart_p | uart_p | no | no | - |
| MATCH | ps_uart5 | uart5 | 0x400 | UART5 | 0:0x400 | ps_uart_p | uart_p | no | no | - |
| MATCH | ps_uart6 | uart6 | 0x408 | UART6 | 0:0x408 | ps_uart_p | uart_p | no | no | - |
| MATCH | ps_dpa0 | dpa0 | 0x410 | DPA0 | 0:0x410 | ps_dpa_p | dpa_p | no | no | - |
| MATCH | ps_dpa1 | dpa1 | 0x418 | DPA1 | 0:0x418 | ps_dpa_p | dpa_p | no | no | - |
| MATCH | ps_atc0_cio_usb | atc0_cio_usb | 0x420 | ATC0_CIO_USB | 0:0x420 | ps_atc0_cio | atc0_cio | no | no | - |
| MATCH | ps_atc1_common | atc1_common | 0x428 | ATC1_COMMON | 0:0x428 | - | - | no | no | - |
| MATCH | ps_atc1_pcie | atc1_pcie | 0x430 | ATC1_PCIE | 0:0x430 | ps_atc1_common | atc1_common | no | no | - |
| MATCH | ps_atc1_cio | atc1_cio | 0x438 | ATC1_CIO | 0:0x438 | ps_atc1_common | atc1_common | no | no | - |
| MATCH | ps_atc1_cio_pcie | atc1_cio_pcie | 0x440 | ATC1_CIO_PCIE | 0:0x440 | ps_atc1_cio | atc1_cio | no | no | - |
| MATCH | ps_atc1_cio_usb | atc1_cio_usb | 0x448 | ATC1_CIO_USB | 0:0x448 | ps_atc1_cio | atc1_cio | no | no | - |
| MISMATCH | ps_atc2_common | atc2_common | 0x450 | ATC2_COMMON | - | - | - | no | no | virtual-only |
| MISMATCH | ps_atc2_pcie | atc2_pcie | 0x458 | ATC2_PCIE | - | ps_atc2_common | - | no | no | virtual-only |
| MISMATCH | ps_atc2_cio | atc2_cio | 0x460 | ATC2_CIO | - | ps_atc2_common | - | no | no | virtual-only |
| MISMATCH | ps_atc2_cio_pcie | atc2_cio_pcie | 0x468 | ATC2_CIO_PCIE | - | ps_atc2_cio | - | no | no | virtual-only |
| MATCH | ps_dpa2 | dpa2 | 0x470 | DPA2 | 0:0x470 | ps_dpa_p | dpa_p | no | no | - |
| MISMATCH | ps_atc2_cio_usb | atc2_cio_usb | 0x478 | ATC2_CIO_USB | - | ps_atc2_cio | - | no | no | virtual-only |
| MISMATCH | ps_atc3_common | atc3_common | 0x480 | ATC3_COMMON | - | - | - | no | no | virtual-only |
| MATCH | ps_pmp | pmp | 0x488 | PMP | 0:0x488 | - | - | yes | yes | - |
| MATCH | ps_pms_sram | pms_sram | 0x490 | PMS_SRAM | 0:0x490 | - | - | yes | yes | - |
| MISMATCH | ps_atc3_pcie | atc3_pcie | 0x498 | ATC3_PCIE | - | ps_atc3_common | - | no | no | virtual-only |
| MISMATCH | ps_atc3_cio | atc3_cio | 0x4a0 | ATC3_CIO | - | ps_atc3_common | - | no | no | virtual-only |
| MATCH | ps_dpa3 | dpa3 | 0x4a8 | DPA3 | 0:0x4a8 | ps_dpa_p | dpa_p | no | no | - |
| MISMATCH | ps_atc3_cio_pcie | atc3_cio_pcie | 0x4b0 | ATC3_CIO_PCIE | - | ps_atc3_cio | - | no | no | virtual-only |
| MISMATCH | ps_atc3_cio_usb | atc3_cio_usb | 0x4b8 | ATC3_CIO_USB | - | ps_atc3_cio | - | no | no | virtual-only |
| MATCH | ps_dpa4 | dpa4 | 0x4c0 | DPA4 | 0:0x4c0 | ps_dpa_p | dpa_p | no | no | - |
| MATCH | ps_apcie_gp | apcie_gp | 0x4d0 | APCIE_GP | 0:0x4d0 | - | - | no | no | - |
| MATCH | ps_apcie_sys_gp | apcie_sys_gp | 0x4d8 | APCIE_SYS_GP | 0:0x4d8 | ps_apcie_gp | apcie_gp | no | no | - |
| MATCH | ps_dispext1_sys | dispext1_sys | 0x4e0 | DISPEXT1_SYS | 0:0x4e0 | - | - | no | no | - |
| MATCH | ps_dispext1_fe | dispext1_fe | 0x4e8 | DISPEXT1_FE | 0:0x4e8 | ps_dispext1_sys | dispext1_sys | no | no | - |
| MATCH | ps_dispext1_cpu | dispext1_cpu | 0x4f0 | DISPEXT1_CPU | 0:0x4f0 | ps_dispext1_fe | dispext1_fe | no | no | - |
| MATCH | ps_ans | ans | 0x538 | ANS | 0:0x538 | - | - | no | no | - |
| MATCH | ps_apcie_st | apcie_st | 0x540 | APCIE_ST | 0:0x540 | ps_ans | ans | no | no | - |
| MATCH | ps_apcie_sys_st | apcie_sys_st | 0x548 | APCIE_SYS_ST | 0:0x548 | ps_ans,ps_apcie_st | ans,apcie_st | no | no | - |
| MATCH | ps_apcie_phy_sw | apcie_phy_sw | 0x550 | APCIE_PHY_SW | 0:0x550 | ps_apcie_sys_st,ps_apcie_sys_gp | apcie_sys_gp,apcie_sys_st | no | no | - |
| MATCH | ps_msr | msr | 0x560 | MSR | 0:0x560 | - | - | no | no | - |
| MATCH | ps_msr_ase_core | msr_ase_core | 0x568 | MSR_ASE_CORE | 0:0x568 | ps_msr | msr | no | no | - |
| MATCH | ps_ane_sys | ane_sys | 0x570 | ANE_SYS | 0:0x570 | - | - | no | no | - |
| MATCH | ps_jpg | jpg | 0x578 | JPG | 0:0x578 | - | - | no | no | - |
| MATCH | ps_avd_sys | avd_sys | 0x580 | AVD_SYS | 0:0x580 | - | - | no | no | - |
| MATCH | ps_sep | sep | 0xc00 | SEP | 0:0xc00 | - | - | yes | yes | - |
| MATCH | ps_venc_dma | venc_dma | 0x8000 | VENC_DMA | 0:0x8000 | ps_venc_sys | venc_sys | no | no | - |
| MATCH | ps_venc_pipe4 | venc_pipe4 | 0x8008 | VENC_PIPE4 | 0:0x8008 | ps_venc_dma | venc_dma | no | no | - |
| MATCH | ps_venc_pipe5 | venc_pipe5 | 0x8010 | VENC_PIPE5 | 0:0x8010 | ps_venc_dma | venc_dma | no | no | - |
| MATCH | ps_venc_me0 | venc_me0 | 0x8018 | VENC_ME0 | 0:0x8018 | ps_venc_dma | venc_dma | no | no | - |
| MATCH | ps_venc_me1 | venc_me1 | 0x8020 | VENC_ME1 | 0:0x8020 | ps_venc_me0 | venc_me0 | no | no | - |
| MATCH | ps_ane_mpm | ane_mpm | 0xc000 | ANE_MPM | 0:0xc000 | ps_ane_sys | ane_sys | no | no | - |
| MATCH | ps_ane_cpu | ane_cpu | 0xc008 | ANE_CPU | 0:0xc008 | ps_ane_sys | ane_sys | no | no | - |
| MATCH | ps_ane_td | ane_td | 0xc010 | ANE_TD | 0:0xc010 | ps_ane_sys | ane_sys | no | no | - |
| MATCH | ps_ane_base | ane_base | 0xc018 | ANE_BASE | 0:0xc018 | ps_ane_td | ane_td | no | no | - |
| MATCH | ps_disp_cpu | disp_cpu | 0x10000 | DISP_CPU | 0:0x10000 | ps_disp_fe | disp_fe | no | no | - |
| MATCH | ps_debug_gated | debug_gated | 0x0 | DEBUG_GATED | 1:0x0 | - | - | yes | yes | - |
| MATCH | ps_nub_spmi0 | nub_spmi0 | 0x58 | NUB_SPMI0 | 1:0x58 | - | - | yes | yes | - |
| MATCH | ps_nub_spmi1 | nub_spmi1 | 0x60 | NUB_SPMI1 | 1:0x60 | - | - | yes | yes | - |
| MATCH | ps_nub_spmi_a0 | nub_spmi_a0 | 0x68 | NUB_SPMI_A0 | 1:0x68 | - | - | yes | yes | - |
| MATCH | ps_nub_spmi_a1 | nub_spmi_a1 | 0x70 | NUB_SPMI_A1 | 1:0x70 | - | - | yes | yes | - |
| MATCH | ps_nub_spi0 | nub_spi0 | 0x80 | NUB_SPI0 | 1:0x80 | - | - | yes | yes | - |
| MATCH | ps_nub_ocla | nub_ocla | 0x88 | NUB_OCLA | 1:0x88 | - | - | yes | yes | - |
| MATCH | ps_nub_gpio | nub_gpio | 0x90 | NUB_GPIO | 1:0x90 | - | - | yes | yes | - |
| MATCH | ps_nub_sram | nub_sram | 0xa0 | NUB_SRAM | 1:0xa0 | - | - | yes | yes | - |
| MATCH | ps_debug_switch | debug_switch | 0xa8 | DEBUG_SWITCH | 1:0xa8 | - | - | yes | yes | - |
| MATCH | ps_atc0_usb_aon | atc0_usb_aon | 0xb0 | ATC0_USB_AON | 1:0xb0 | - | - | no | no | - |
| MATCH | ps_atc1_usb_aon | atc1_usb_aon | 0xb8 | ATC1_USB_AON | 1:0xb8 | - | - | no | no | - |
| MATCH | ps_atc0_usb | atc0_usb | 0xc0 | ATC0_USB | 1:0xc0 | ps_atc0_common,ps_atc0_usb_aon | atc0_common,atc0_usb_aon | no | no | - |
| MATCH | ps_atc1_usb | atc1_usb | 0xc8 | ATC1_USB | 1:0xc8 | ps_atc1_common,ps_atc1_usb_aon | atc1_common,atc1_usb_aon | no | no | - |
| MISMATCH | ps_atc2_usb_aon | atc2_usb_aon | 0xd0 | ATC2_USB_AON | - | - | - | no | no | virtual-only |
| MISMATCH | ps_atc3_usb_aon | atc3_usb_aon | 0xd8 | ATC3_USB_AON | - | - | - | no | no | virtual-only |
| MISMATCH | ps_atc2_usb | atc2_usb | 0xe0 | ATC2_USB | - | ps_atc2_common,ps_atc2_usb_aon | - | no | no | virtual-only |
| MISMATCH | ps_atc3_usb | atc3_usb | 0xe8 | ATC3_USB | - | ps_atc3_common,ps_atc3_usb_aon | - | no | no | virtual-only |

PMGR provider summary: MATCH=112, MISMATCH=17

## HPM/SPMI decode

| HPM | USID | ADT interrupts | ADT types | location | port-number |
|---|---|---|---|---|---|
| hpm0 | 0xc | [11, 17, 19] | [0, 2, 3] | left-front | 1 |
| hpm1 | 0xa | [37, 43, 45] | [0, 2, 3] | left-rear | 2 |
| hpm5 | 0x8 | [63, 69, 71] | [0, 2, 3] | internal | 1 |
