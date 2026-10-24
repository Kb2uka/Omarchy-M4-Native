# J713 sleep power domains (issue #92)

## Symptom and cause

On the 13-inch M4 Air (Mac16,12 / J713) with the `afb025a8…` backlight
devicetree, a `pm_test=platform` suspend logged `PS gfx: Failed to reach power
state 0xf` on resume and a DCP `DPTXController` FIFO error during suspend, and
about a minute later the idle blank left the panel black with the backlight on
([s2idle findings](https://github.com/Kb2uka/Omarchy-M4-Native/pull/162)).
A bench module that held the three domains below on removed all three
symptoms.

During system suspend, genpd powers off every PMGR domain that is not marked
always-on. Three of them must stay powered for the GPU and DCP to survive:

| Node | Label | Consumers |
| --- | --- | --- |
| `/soc/power-management@380700000/power-controller@250` | `gfx` | `302600000.gpu`, `302608000.mailbox` |
| `/soc/power-management@380700000/power-controller@10000` | `disp_cpu` | framebuffer, `dcp-backlight`, its DART and mailbox |
| `/soc/power-management@380700000/power-controller@3c8` | `dptx_phy` | framebuffer |

`drivers/pmdomain/apple/pmgr-pwrstate.c` sets `GENPD_FLAG_ALWAYS_ON` for a
domain with `apple,always-on`, and `genpd_sync_power_off` skips such domains in
system suspend. No kernel change is needed.

## Change

`tools/build-j713-sleep-domains-dt.py` generates an overlay with one
`target-path` fragment per domain, each adding only `apple,always-on;`. It
applies the overlay to the backlight devicetree with `dtc` and `fdtoverlay`:

```bash
python3 tools/build-j713-sleep-domains-dt.py \
  --base /absolute/path/to/j713-backlight.dtb \
  --output /absolute/new/path/j713-sleep-domains.dtb
```

The builder accepts only base SHA256
`afb025a8f1c946d164fe962f20b2e9d5cd987cfc668985eee62e41767722b2aa`. It refuses
an existing output, and a base whose three nodes lack the expected labels or
are already always-on. It verifies that the result equals the base plus an
empty `apple,always-on` property on exactly those three nodes. Every other
node, property, memory reservation and the boot CPU must be unchanged.

DTC 1.8.1 produced 51,516 bytes, SHA256
`38d5ae5f5cf689d1ca82b3a3721ecd5d0985a623285d8eb23ef76a5d22671982`.
`dtc -I dtb -O dts -s` of the result differs from the base by exactly three
added `apple,always-on;` lines.

**This devicetree is not yet in the boot chain.** It is not listed in
`tools/m4-boot-compose.py`, the package channel or any PKGBUILD. Building and
publishing it, and adding it to the composer and the devicetree update chain,
are maintainer packaging steps.

## Hardware results

MacBook Air 13-inch M4 (J713), kernel `7.1.9-m4-gpu-backport-20261008`, on
2026-10-24. The `38d5ae5f…` devicetree was spliced into `boot.bin` in place of
`afb025a8…`, with the loader, bootargs, kernel and initramfs byte-identical:

- The Air booted normally. `pm_genpd_summary` reported all three domains on.
- `pm_test=platform` through `systemctl suspend` returned without the
  `PS gfx` power-state failure or the DCP FIFO error.
- A real s2idle cycle of 36 seconds, woken by the power button, succeeded;
  `/sys/power/suspend_stats` recorded no failure.
- After it the display blanked and woke through DPMS. The GPU rendered
  `es2gears_wayland` on `Apple M4 (G16G B1)` at about 21 FPS, the same as
  before sleep. Speaker protection restarted (with the coordinator from #171).

## Limits

- The GPU mailbox keeps waking the system while asleep, roughly 350 interrupts
  per second (a rough estimate).
- Bluetooth logs `hci0 Opcode 0x0c01` and `0x0c24` failures on resume.
- The idle and sleep power cost of keeping `gfx` powered is unmeasured.
- The screen stays lit during sleep unless something blanks it first.
- Only one real s2idle cycle has been recorded; repeated cycles, lid-close
  sleep and battery drain are not verified.
