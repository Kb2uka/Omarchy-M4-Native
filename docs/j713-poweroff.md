# J713 SMC poweroff handler

## Symptom and cause (issue #24)

Builds without an SMC reboot child had no poweroff handler, so Linux halted
instead of powering off and the Air appeared to hang. v0.1.0-alpha.1 added the
child, `/soc/smc@38c600000/reboot { compatible = "apple,smc-reboot"; }`, with
no NVMEM cells. On that build, choosing Shut Down restarts the Air.

The installed alpha.1 kernel logs, on every boot:

```text
macsmc-reboot macsmc-reboot: Missing NVMEM cell shutdown_flag (-2)
macsmc-reboot macsmc-reboot: Missing NVMEM cell boot_stage (-2)
macsmc-reboot macsmc-reboot: Missing NVMEM cell boot_error_count (-2)
macsmc-reboot macsmc-reboot: Missing NVMEM cell panic_count (-2)
macsmc-reboot macsmc-reboot: Handling reboot and poweroff requests via SMC
```

`drivers/power/reset/macsmc-reboot.c` (identical in the retained 7.1.9 backport
and Asahi) writes `MBSE=offw`, sets `boot_stage` to 0 and `shutdown_flag` to 1,
then sends `MBSE=off1`. Its comments say `off1` "needs PMU bit set to stay on"
and, for a missing flag, "the device will just reboot". With no cells the two
NVMEM writes are skipped silently, so only `offw` and `off1` reach the SMC.

## Abbey shutdown-state cells

The J713 PMU is Abbey (`pmu,abbey`, USID `0xe`). Its ADT declares
`info-leg_scrpad = 0xf800`, `info-pm_setting = 0x2001` and
`info-rtc_scrpad = 0x2100`. That triple is exactly mainline T6030 Chapel's
layout (`arch/arm64/boot/dts/apple/t6030.dtsi`), whose state cells sit at the
legacy scratchpad base:

| Cell | Address | Bits |
| --- | --- | --- |
| `boot_stage` | `0xf801` | byte |
| `boot_error_count` | `0xf802` | 0..3 |
| `panic_count` | `0xf802` | 4..7 |
| `shutdown_flag` | `0xf80f` | 3 |

Every in-tree family places the same cells at `leg_scrpad + 1/+2/+0xf`. For
T8103 (`0x9f00`) and T6000 (`0x6000`), public ADTs show `info-leg_scrpad` equal
to the DT base. m1n1 clears the counter byte at `leg_scrpad + 2` on the primary
PMU. A read-only dump of the Air's PMU at `0xf800` on alpha.1 returned
`20 30 02 30 00 …`: `boot_stage` 0x30 (kernel started), two recorded boot
errors, error stage 0x30, and `shutdown_flag` clear. That is consistent with
unclean shutdowns on this layout. It is inference from consistent evidence, not
Apple documentation.

## Change

`patches/j713-smc-poweroff.dtso` adds the four cells to the Abbey fixed NVMEM
layout and wires them to the reboot child in the driver's order and names, as
T6030 does. It adds no driver code, SMC command, kernel option or kernel
rebuild. The driver now also writes `boot_stage` 0x30 at probe and clears
non-zero error counters, as on other Apple machines. All writes land in
Abbey's legacy scratchpad.

## Exact portable build

On a Linux host with `dtc` and `fdtoverlay`:

```bash
python3 tools/build-j713-poweroff-dtb.py \
  --base /absolute/path/to/qualified-portable.dtb \
  --output /absolute/new/path/j713-poweroff.dtb
```

The builder accepts only base SHA256
`3ba58a528c3c1f07bbde6704231689a8a9eba1b4a3cc9a73f62654544b11e5e3`. It refuses
an existing output. It verifies that the result equals the base plus exactly the
reboot child and the four cells: the expected `reg`/`bits`, fresh phandles, and
`nvmem-cells` resolving to those cells in order. Memory reservations and the
boot CPU must be unchanged.

DTC 1.8.1 produced 46,823 bytes, SHA256
`e54c77a760a5b696486f9d80c7c83ca4295d641113bb9e54d13e259863bf9b07`. The
decompiled diff against the base contains only the reboot child and the four
cell nodes. `tools/prepare-j713-release.py` pins this hash for the next release.

## Installed alpha.1 machines

`tools/m4-j713-update-boot-dtb.py` replaces only this devicetree inside
`/boot/efi/omarchy/boot.bin`; see [updates](updates.md#shutdown-fix-for-alpha1).
m1n1 and the gravity loader advance past a devicetree by its own `totalsize`
(`src/payload.c` `load_fdt`), so the larger tree needs no padding.

## Owner verification

1. Boot once after the update. `journalctl -k -b | grep macsmc-reboot` should
   show only `Handling reboot and poweroff requests via SMC`, with no
   `Missing NVMEM cell` lines. `/sys/bus/nvmem/devices/spmi_nvmem0/cells/` should
   list the four new cells.
2. Choose Shut Down. The Air should stay off.
3. Power on and confirm a normal boot.
4. Choose Restart and confirm it returns.

Owner result, 2026-10-12, on the owner's Air after the in-place update:

- **Step 1 passed.** The boot logged only `Handling reboot and poweroff
  requests via SMC`, and the four cells appeared in sysfs.
- **Driver probe cleared the error counter.** The two recorded boot errors
  were cleared (`0xf802` went from `0x02` to `0x00`).
- **Steps 2 and 3 passed.** The journal shows
  `poweroff requested … System is powering down`. The owner confirmed the Air
  stayed off, and the next power-on booted normally.
- **Restart with the cells active** (step 4) and the power-button long press
  were not separately recorded in that session.
