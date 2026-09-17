# T8132 first-boot — mini-side handoff (staged 2026-09-01 from the Air)

> Historical research record. For the final checkpoint and reproduction
> limits, read [the project README](../README.md). Raw machine captures and
> bench binaries referenced below are not part of the public tree.

The MacBook Air (Mac16,12 / J713 / T8132) is the TARGET — it reboots into
m1n1 and any session hosted on that machine stops. This Mac mini is the PROXY HOST and this
directory is everything the loop needs. Goal: first Linux console on T8132
via our ten landed DT slices, then walk the hardware-validation checklist.

## Files here

| File | What |
|---|---|
| `vmlinuz` | Prebuilt Fedora Asahi kernel 7.1.6-400.asahi.fc44, 16k pages. Console path built-in; NVMe/SMC/DART/etc are modules inside the initramfs. |
| `t8132-j713.dtb` | Our DT, compiled from linux-asahi 32be170d0 (all ten slices). |
| `initramfs.cpio.gz` | Busybox initramfs: loads all T8132 modules, dumps probe results + `/proc/partitions`, drops to a shell. |
| `m1n1/` | Clone from the Air (HEAD c9ef56f, clean). Built separately; not included in this repository. |
| `first-boot-runbook.md` | The original Air-side runbook, for reference. |

Deps already installed on this mini: `pyserial`, `construct` (pip3 --user,
system python3). Verified importable.

## Doug's manual part (before the loop)

1. Time Machine backup of the Air current.
2. `cd ~/omarchy-firstboot/m1n1 && make` (LLVM works on macOS).
3. On the Air: recoveryOS (hold power) → `bputil` reduced security →
   `kmutil configure-boot` to install m1n1 per the m1n1/Asahi developer docs.
   macOS stays selectable via the power-button boot picker.
4. Cable the Air's DFU-capable port to this mini; boot the Air → m1n1 waits
   in proxy mode on the cable.

## The historical boot loop

```sh
cd ~/omarchy-firstboot/m1n1/proxyclient
export M1N1DEVICE=/dev/cu.usbmodemYOUR_DEVICE1   # proxy iface; YOUR_DEVICE3 = HV virtual UART
mkdir -p ~/omarchy-firstboot/logs
script -q ~/omarchy-firstboot/logs/boot-$(date +%Y%m%d-%H%M%S).log \
  /usr/bin/python3 tools/linux.py -b "console=tty0 idle=nop loglevel=7" \
    ~/omarchy-firstboot/Image.gz \
    ~/omarchy-firstboot/t8132-j713.dtb
```

Command corrections learned on hardware 2026-09-01 (do not regress these):

- **`Image.gz`, never `vmlinuz`.** The Fedora `vmlinuz` is an EFI zboot PE
  (`MZ`+`zimg`, zstd payload at header offset 0x8, size at 0xc). m1n1 supports
  only none/gz/xz and detects by file suffix. Unwrap: extract exactly
  payload_size bytes from payload_offset, `zstd -d`, verify `ARMd` magic at
  0x38, gzip. (Running to EOF grabs trailing bytes -> "unsupported format".)
- **`/usr/bin/python3`** (3.9, has pyserial+construct). Bare `python3` is
  homebrew 3.14 and has neither.
- **Wrap in `script -q <log>`** — linux.py enters Miniterm ttymode which needs
  a real TTY; backgrounded it dies on termios (Errno 19/25).
- **`idle=nop` is mandatory on T8132** — M4 cores lose state on WFI (Asahi 7.2
  progress report). Kernels without the `idle=` early_param (anything <= 7.1.x
  release builds, incl. Fedora 7.1.6-400) die silently in the idle loop before
  any console. `console=tty0` needs simpledrm (late); the s5l UART earlycon is
  not physically reachable — so an early death is always a black screen.
- After kernel handoff m1n1's USB gadget dies and `/dev/cu.usbmodem*` vanish —
  that is normal, not a failure. Power-cycle returns the Air to proxy.

**Every attempt gets tee'd to a timestamped log. No exceptions** — each
observed IRQ/probe outcome retires a "precedent-inherited, unconfirmed on
hardware" item in the gap analysis.

Panic/hang → power-cycle the Air, it returns to m1n1 proxy, push again.
Iterate freely; kernels run from RAM, nothing touches the macOS volume.

## Success ladder for tonight

1. **Kernel banner + earlycon output** — validates address translation and
   AIC/UART DT. Already huge if reached.
2. **Initramfs probe dump** — `nvme` present in `/proc/partitions`
   (ANS mailbox + SART + NVMe) and `macsmc` probe lines (SMC mailbox).
3. Walk the checklist below in order.

## Hardware-validation checklist (priority order, with failure signatures)

1. **DWC3 `iommus` stream IDs.** DT wires `<&dart 0>, <&dart 1>` per
   precedent; the ADT hints the endpoint stream is SID 1
   (`mapper-usb0@1` / `mapper-usb1@1`, `sid = <1, 14>`). If the ADT view
   is literal: loud contained DART translation faults on **IRQ 1283/1345**
   at first USB DMA, dead ports, no boot impact. Fault seen ⇒ SID-1
   hypothesis wins ⇒ one-line `iommus` fix (`<&dart_0 1>`, second entry
   per the sid[1]=14 pairing).
2. **HPM `select` IRQ (= base irq+2)** — only non-ADT interrupt number.
   Wrong value = probe timeout, loud.
3. **atcphy crossbar probe-write** into the derived `+0x4c000` window
   (T8112 data, `n_ufp=4`) — harmless with no mux consumer; verify at
   display bring-up.
4. **ascwrap-v6 `+0x8000` mailbox windows + axi2af 0x8000 sizing** —
   standing items; SIO reserves only its leading `0x4000`, mailbox
   subwindow disjoint.
5. **AOP ADMAC IRQ output index (2, precedent-inherited)** — wrong slot =
   DMA completions never serviced, aop-audio stalls *silently*.
6. **MTP HID AFE/STM reset GPIOs (8/24 on J713, copied from J413/J613)** —
   wrong number fails only at interface start: dead keyboard/trackpad,
   no probe error.

Version-skew caveat: DTBs from pinned linux-asahi, kernel from the
2026-08-06 copr build. Binding is by compatible string; if a probe fails
oddly, suspect skew before suspecting the DT.

## Recovery

- Wedged boot chain → DFU + Apple Configurator on this mini → **Revive**
  (data-safe, iBoot only). **Never "Restore"** unless you mean to wipe.
- macOS always reachable via the power-button boot picker.

## After the session

Findings go back to the repo (`Kb2uka/Omarchy-M4-Native`, gap doc updates)
and the Obsidian vault. Record absolute dates.

## Fixing the DT and rebuilding (added 2026-09-01, second staging pass)

The full repo is at `~/omarchy-firstboot/omarchy/` (git checkout of
`Kb2uka/Omarchy-M4-Native`, main @ cf25805; its `m1n1/` is excluded — use
the sibling `~/omarchy-firstboot/m1n1/`). `linux-asahi/` inside it is the
pinned freeze `32be170d0`, clean tree, holding all ten slices. `dtc` 1.8.1
is installed via homebrew; clang is at `/usr/bin/clang`.

To change the DT (e.g. the checklist-item-1 one-line `iommus` fix) edit
`omarchy/linux-asahi/arch/arm64/boot/dts/apple/t8132*.dtsi|.dts`, then
rebuild from `omarchy/linux-asahi/`:

```sh
cd ~/omarchy-firstboot/omarchy/linux-asahi
clang -E -nostdinc -I include -undef -D__DTS__ -x assembler-with-cpp \
    arch/arm64/boot/dts/apple/t8132-j713.dts \
  | dtc -O dtb -o ~/omarchy-firstboot/t8132-j713.dtb -I dts -
```

Baseline: exactly one `simple_bus_reg` warning is expected (that is the
proven gate baseline; use clang -E, never the macOS `cpp` wrapper — it
mangles `-x`). Then re-run the boot loop with the fresh dtb.

Preserve device-tree changes as patches and record the source revision.

Initramfs changes: `omarchy/build/make-initramfs.sh` +
`busybox-aarch64-static` rebuild it offline if module changes are needed.

## Session 1 results (2026-09-01, first hardware night) — read before session 2

Full evidence trail: `logs/session-20260901-150214-recon.log` + timestamped boot logs.

**Install path (historical; stock macOS was restored afterward):** 1TR → `bputil -n` (Permissive —
NOT "reduced"; `-n`, never `-p` which is --password) → `kmutil configure-boot
-c m1n1.bin --raw --entry-point 2048 --lowest-virtual-address 0 -v "/Volumes/
Macintosh HD"` (m1n1.**bin** on macOS >=12.1; the .macho path in older notes is
pre-12.1 only). Verified: coih populated, Security Mode Permissive, CHIP 0x8132,
BORD 0x2C. FileVault: `diskutil apfs unlockVolume disk3s5` first. Volume Group
<YOUR-VOLUME-GROUP-UUID>. At that point, the Air booted to m1n1 on each startup
(plain m1n1 does not chainload macOS); undo = 1TR `bputil -f`. DFU port on the
2025+ Air = the USB-C **farther from MagSafe** (Apple 120694; opposite of
M1-M3). m1n1 v1.6.1-22-gc9ef56f built on the mini (brew llvm+lld, rustup +
aarch64-unknown-none-softfloat target).

**m1n1 runs on T8132.** Banner evidence: AIC3 2048/4096 IRQs 1 die; pmgr 392
devices; fb 2560x1664; `dart /arm-io/dart-usb0 @ 0x402f80000 is a t8110` (and
usb1 @ 0x40af80000); t8132 PCIe controller 0 initialized; enumerates 2 ACM
devices in ~24 s. `cpufreq: Chip 0x8132 is unsupported` + `MCC: Failed to get
mcc count!` are non-fatal m1n1 gaps.

**Why every 7.1.6 boot was a black screen:** kernel predates the
`idle=<wfi|yield|nop>` early_param (Yureka, arm64-idle-param, in linux-next;
in the asahi branch at `arch/arm64/kernel/idle.c`). WFI on M4 loses core state
→ silent death before simpledrm. PROVEN NOT our DT: Fedora's own upstream
skeletal t8132-j713.dtb fails identically (differential test). Also ruled out:
AIC3 binding (apple,t8122-aic3 IS in the kernel), fb node status (m1n1 deletes
it, kboot.c:237), PA/VA width, fbcon deferred takeover, earlycon.

**m1n1 hypervisor does NOT work on T8132**: run_guest.py loads the guest, then
m1n1 crashes at EL2 in `hv_start+0x194` (ESR EC=0, cluster-1 core) and
self-reboots. Virtual-UART-over-USB console is unavailable until upstream
fixes it. New gap-analysis item.

**Staged initramfs is broken**: 2655 modules but zero of the T8132 stack
(apple-dart, nvme-apple, apple-sart, pinctrl-apple-gpio, i2c-apple,
apple-dockchannel, apple-admac, spi-apple) — they are in NEITHER staged RPM's
extraction. Session-2 kernel builds them **built-in**, so boot WITHOUT an
initramfs.

**Replacement kernel** (in progress at session end): asahi branch 7.1.9
(HEAD 77cb8f24c) on the OptiPlex at `~/t8132-build/` — Fedora config base,
RUST/SEP/DRM_ASAHI off (no bindgen), BTF/debug-info off, 16K pages, T8132
stack =y, `make ARCH=arm64 LLVM=1 -j12 Image.gz dtbs`. Artifacts →
`arch/arm64/boot/Image.gz` + `dts/apple/t8132-j713.dtb`. Revert plan:
`rm -rf ~/t8132-build`; `pacman -Rns lld bc`; package snapshot at
`~/pkgs-before-t8132.txt` (178 pkgs).

**Hardware evidence from the live ADT** (`air-adt.bin` here, 469352 B, loads
offline via proxyclient `m1n1.adt.load_adt`):
- Checklist #1 RESOLVED: dart-usb0/1 mapper reg = **1**, sid array = <1, 14>,
  IRQs 1283/1345 confirmed. Our `iommus = <&dart 0>` is wrong on hardware —
  apply the one-line SID-1 fix before the next slice test.
- Checklist #2 premise WRONG: no i2c hpmBusManager nodes exist. HPM is
  `/arm-io/nub-spmi-a0/hpm0`, compatible `usbc,sn201202x,spmi`, interrupts
  11/17/19, int-parent 261.
- AOP `/arm-io/aop`: ascwrap-v6, IRQs 434/433/436/435, wrapper 0x190600000/0x88000.
- MTP `/arm-io/mtp`: ascwrap-v6, IRQs 1114/1113/1116/1115, 0x194600000/0x88000.
