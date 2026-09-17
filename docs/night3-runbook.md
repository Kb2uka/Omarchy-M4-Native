# T8132 hardware night 3 — 2026-09-02 — mini-side runbook (READ THIS FIRST)

> Historical research record. For the final checkpoint and reproduction
> limits, read [the project README](../README.md). Raw machine captures and
> bench binaries referenced below are not part of the public tree.

Staged September 2, 2026; this session was **not executed**. The goal was
storage validation followed by SMC, MTP, and USB, one group at a time.
The following is a historical inventory and experimental plan, not a
complete public boot kit. Paths describe the original Mac proxy host.
Serial-derived device names have been replaced with placeholders.

## 0. Experimental method

- Use a backed-up target and a separate proxy host.
- Record a timestamped log and photograph each boot's final screen.
- Change one variable per comparison; keep the kernel fixed between DT rungs.
- Keep source revisions and artifact hashes with every observed result.
- Obtain current recovery instructions before changing boot security.

## 1. What is staged (verified 2026-09-02 from the Air)

| File | sha256 (first 16) | What |
|---|---|---|
| `Image-m4hacks-smc-nvme.gz` | `639319d43d047aa0` | THE kernel for tonight: asahi 7.1.9 @ 77cb8f24c + M4 boot hacks + SMC stack, DWC3/ATC-PHY, SN201202X SPMI PD, dockchannel HID, spi-hid + the nvme-apple split-NVMMU change (`omarchy/patches-kernel/nvme-apple-t8132-split-nvmmu.patch`). All built in, no modules needed. |
| `Image-m4hacks-smc.gz` | `7095204eb6f9db7c` | Same minus the NVMe change. Fallback only (see rung 1). |
| `Image-m4hacks.gz` | — | Night-1 kernel that reached the shell (no SMC/USB/PD drivers). Last-resort kernel control. |
| `initramfs.cpio.gz` | — | Busybox init: tries modprobes (all "no-load", drivers are built in — that is fine), prints `=== probe results ===` (dmesg grep), `=== block devices ===` (`/proc/partitions`), then `exec sh`. **The verdict prints on screen without a keyboard.** |
| `dtb-night3/r1-kernel-control.dtb` | `771727a36d58b05b` | USB, SMC, storage, MTP all OFF (= night-1 minimal enable set, built from the 0013 tree). |
| `dtb-night3/r2-storage-only.dtb` | `d789bf7f6d7661c1` | ONLY ans_mbox + sart + nvme (M4 split node) ON. **The question of the night.** |
| `dtb-night3/r2a-ansmbox-only.dtb` | `128dc15e2e69f6fc` | Fallback: only ans_mbox ON. |
| `dtb-night3/r2b-ansmbox-sart.dtb` | `6f7614a380a189f3` | Fallback: ans_mbox + sart ON, nvme OFF. |
| `dtb-night3/r3-storage-smc.dtb` | `de71c45bdea360cd` | storage + smc_mbox + smc. |
| `dtb-night3/r4-storage-smc-mtp.dtb` | `b1823ca9d6ead410` | + mtp, mtp_mbox, mtp_dart, mtp_dockchannel (keyboard/trackpad). |
| `dtb-night3/r5-all.dtb` | `95a6df2199077ff2` | Everything the J713 board file enables (+ dwc3_0/1 and their four DARTs). Byte-identical to `t8132-j713-night2.dtb`. |
| `t8132-j713-night2-nostorage.dtb` | `7ff1f1ed52000536` | The older night-2 "control" — has SMC+USB+MTP ON with storage OFF. Superseded by r1..r5; do not use unless a rung says so. |
| `air-adt.bin` | — | Live ADT from this Air (469352 B). Same file as `omarchy/recon/air-adt-j713.bin`. Parse offline with `omarchy/tools/adt.py`. |
| `tools/mkdtb.sh` | — | `tools/mkdtb.sh /abs/out.dtb [label ...]` builds the J713 DTB from `omarchy/linux-asahi` with the named labels forced to `status = "disabled"`. Validated today: no labels → identical to night2.dtb; `ans_mbox sart nvme` → identical to night2-nostorage.dtb. **Output path must be absolute.** |
| `tools/boot.sh` | — | `tools/boot.sh <dtb> <tag>` = the boot loop with logging and the reboot-probe armed (section 3). |
| `tools/reboot-probe.sh` | — | Console-free verdict: waits for m1n1's USB to vanish (handoff) then for it to come back (kernel completed boot and `panic=-1` fired when init exited). Night 1: reboot after ~205 s = success. `boot.sh` starts it for you. |
| `omarchy/` | — | Repo checkout, main @ **1824538** (historical private revision). `omarchy/linux-asahi/` = freeze 32be170d0 + patches 0011, 0012, 0013 (HEAD **c70dc9cfc**). Rebuilding `t8132-j713.dts` from it reproduces night2.dtb byte for byte (checked today). |
| `m1n1/` | — | Doug's m1n1 v1.6.1-22-gc9ef56f. `build/m1n1.bin` sha `f45046da2e1314e1` = the copy in the Air's `~/Downloads/m1n1.bin` (verified identical today). |

Boot args, fixed for every Linux boot tonight (exactly what reached the shell
on night 1): `nosmp idle=nop panic=-1 console=tty0 fbcon=nodefer loglevel=7`.

## 2. Pre-flight on the mini (do this BEFORE Doug touches the Air)

```sh
cd ~/omarchy-firstboot
git -C omarchy log --oneline -1                 # expect 1824538
git -C omarchy/linux-asahi log --oneline -1     # expect c70dc9cfc ... split M4 NVMe and NVMMU windows
shasum -a 256 Image-m4hacks-smc-nvme.gz dtb-night3/*.dtb | cut -c1-16,64-   # compare with the table above
/usr/bin/python3 -c 'import serial, construct; print("ok")'   # MUST be /usr/bin/python3, not bare python3
ls tools/                                         # boot.sh mkdtb.sh reboot-probe.sh
ls /dev/cu.usbmodem* 2>/dev/null || echo "no proxy yet (expected until the Air is in m1n1)"
mkdir -p logs
```

If anything above is off, STOP and fix it before the Air goes down; nothing
in the ladder is worth running against unverified inputs.

## 3. Target-side prerequisites and manual steps

For an operator-led test session, verify each prerequisite in order.

1. `m1n1.bin` is already in the Air's `~/Downloads` (1114112 B, sha
   `f45046da…`). If it is gone: AirDrop `~/omarchy-firstboot/m1n1/build/m1n1.bin`
   from this mini (AirDrop discovery on both machines; plain http downloads
   are blocked by Chrome).
2. **Full shutdown.** Then hold the Touch ID / power key continuously through
   "Loading startup options" — that is **1TR**. A tap, or waking via lid,
   just boots macOS and any other recovery is "not paired".
3. Menu bar → Utilities → Terminal (not a button in the four-icon window).
4. `diskutil apfs unlockVolume disk3s5` (FileVault; needed to reach Downloads).
5. `bputil -n` — Permissive. **`-n`, never `-p` (that is --password).**
   Owner credentials prompt is normal.
6. `ls /Volumes` to see where the unlocked Data volume mounted, then `cd`
   into `<that volume>/Users/kb2uka_mac_air/Downloads` (night 1 ran kmutil
   from the directory holding m1n1.bin), then
   `kmutil configure-boot -c m1n1.bin --raw --entry-point 2048 --lowest-virtual-address 0 -v "/Volumes/Macintosh HD"`
   (`kmutil -v` = volume PATH; `bputil -v` means something else — never mix).
7. `bputil -d` — check: `coih` is a hash (not `absent`), Security Mode
   Permissive, `CHIP: 0x8132`, `BORD: 0x2C`, Pairing Status Paired.
8. Reboot. The Air shows the m1n1 logo/banner and waits in proxy mode.
9. USB-C cable from the Air's port **FARTHER from MagSafe** (2025 Air DFU
   port; opposite of M1–M3) to this mini. Within ~24 s:
   `ls /dev/cu.usbmodem*` shows `…YOUR_DEVICE1` (proxy) and `…YOUR_DEVICE3` (HV
   UART — unusable, m1n1's hypervisor is broken on T8132, don't retry it).

After ANY kernel boot, m1n1's USB gadget dies and those devices vanish —
normal. A power-cycle (hold power 10 s, press again) returns the Air to m1n1
proxy. Kernels run from RAM; nothing touches macOS.

## 4. The boot wrapper

`tools/boot.sh <dtb-path> <tag>` does exactly this (read it once):

```sh
cd ~/omarchy-firstboot/m1n1/proxyclient
export M1N1DEVICE=/dev/cu.usbmodemYOUR_DEVICE1
~/omarchy-firstboot/tools/reboot-probe.sh &            # console-free verdict, own log
script -q ~/omarchy-firstboot/logs/boot-<tag>-<stamp>.log \
  /usr/bin/python3 tools/linux.py \
    -b "nosmp idle=nop panic=-1 console=tty0 fbcon=nodefer loglevel=7" \
    ~/omarchy-firstboot/Image-m4hacks-smc-nvme.gz <dtb> \
    ~/omarchy-firstboot/initramfs.cpio.gz
```

Known-normal noise at the end of every boot log: Miniterm dies with
`OSError: [Errno 6] Device not configured` / `SerialException: read failed`
when m1n1's USB disappears at handoff. That is the handoff, not a failure.
`script -q` is mandatory (linux.py needs a real TTY; backgrounded it dies on
termios).

## 5. The ladder — in this order, one boot per rung, log + photo every time

Record per boot: rung, dtb sha, wall time of handoff, whether the reboot-probe
fired and after how many seconds, the last visible screen (photo), and every
`apple-*`/`nvme`/`smc`/`dart`/`sart`/`rtkit`/`dockchannel`/`hid`/`dwc3`/
`sn201202x`/`tps` line with its IRQ number. Append findings to section 8 of
this file as you go — do not wait for the end.

### Rung 0 — proxy-side NVMe probe (no Linux, ~30 s)

```sh
cd ~/omarchy-firstboot/m1n1/proxyclient
M1N1DEVICE=/dev/cu.usbmodemYOUR_DEVICE1 script -q ~/omarchy-firstboot/logs/proxy-nvme-$(date +%Y%m%d-%H%M%S).log /usr/bin/python3 tools/shell.py
```
At the `>>>` prompt: `p.nvme_init()` — then `p.nvme_shutdown()`, then `quit()`.

- **Returns 1 / True:** m1n1's own M4-aware NVMe path (the `NVME_T8132`
  reference our patch copies) brings up ANS on this Air. Mailbox, SART and
  pmgr wiring are proven; anything that fails later is the Linux driver or
  DT. Proceed.
- **Returns 0 / hangs / the Air reboots:** the split-window theory is
  incomplete at the bare-metal level. Record exactly what printed. Still run
  rung 1 (it is independent), then rung 2 once — but treat a rung-2 wedge as
  expected and go straight to the fallbacks 2a/2b.

Power-cycle the Air back to proxy if the shell left it in a bad state.

### Rung 1 — kernel control: `dtb-night3/r1-kernel-control.dtb`

`tools/boot.sh ~/omarchy-firstboot/dtb-night3/r1-kernel-control.dtb r1-control`

Same enable set that reached the shell on night 1, but with tonight's kernel
(new built-in drivers). Expected: kernel banner, simpledrm console, the
initramfs banner, `=== block devices ===` with NO nvme, `/ #`, and the
reboot-probe firing after roughly 200 s.

- Passes → the kernel is sound; every later failure is the DT group added.
- Fails (black screen, no reboot within 6 min) → the new built-ins broke the
  boot even with their nodes disabled. Re-run once with `Image-m4hacks-smc.gz`
  (edit the kernel path in `boot.sh` or pass it by hand). If that passes, the
  NVMe driver change itself is the regression — STOP the ladder, record, and
  the rest of the night is USB/MTP/SMC on the `-smc` kernel (rungs 3–5 with
  storage labels added to the disabled list via `mkdtb.sh`). If both fail, run
  `Image-m4hacks.gz` + the same dtb to confirm the night-1 baseline still
  boots, then stop: it is a build problem for the OptiPlex, not a DT problem.

### Rung 2 — storage only: `dtb-night3/r2-storage-only.dtb`  ← THE TEST

`tools/boot.sh ~/omarchy-firstboot/dtb-night3/r2-storage-only.dtb r2-storage`

Expected on screen: `apple-sart`/`apple-mailbox` lines, RTKit boot of ANS
(`nvme-apple … RTKit`), `nvme nvme0: …` and **`nvme0n1` in
`=== block devices ===`** (plus partitions `nvme0n1p1…`). Then `/ #` and the
probe reboot ~200 s later. If seen: photograph it, and write the result into
section 8 immediately — this is the M4 NVMe layout proven on hardware.

Failure signatures and what each means:

- **(a) Silent wedge at ~0.27 s again, black or frozen at the same place as
  night 1, no probe reboot:** the "nvme" window is still wrong. Do NOT guess
  offsets; go to fallbacks 2a/2b to find which of the three nodes wedges.
- **(b) `ANS did not boot` / boot-status timeout / RTKit timeout:** the
  controller window is right (we got far enough to talk) but the mailbox or
  firmware handshake is off. Record IRQ numbers printed. Fallback 2a tells
  whether the mailbox alone registers cleanly.
- **(c) NVMe probes, but I/O errors, or an ANS crashlog mentioning
  `I/O SQ : 0x0`:** the IOQ base writes (controller +0x1200/+0x1208 after IO
  queue creation) did not land — reset-path bug in the kernel patch. Storage
  layout is proven; this is a kernel fix for the OptiPlex.
- **Repeated `NVMMU TCB invalidation failed` warnings but device present:**
  watch item — Linux reads TCB status at NVMMU +0x28120, m1n1 reads +0x29120.
  Record it; do not change it tonight.

Fallbacks, only if rung 2 did not reach the shell:

- **2a** `dtb-night3/r2a-ansmbox-only.dtb` — shell with no nvme = the ASC
  mailbox provider (+0x8000 window, IRQs from the ADT) registers cleanly; a
  wedge here isolates `ps_ans` power-up or the mailbox window itself.
- **2b** `dtb-night3/r2b-ansmbox-sart.dtb` — shell + SART init lines = SART
  is fine and the failure is inside the NVMe consumer (windows, IOQ writes,
  secure-BAR overmap). A new wedge here isolates SART.

If the rung-2 boot dies before ANY driver line, repeat it once with
`initcall_debug` appended to the boot args (edit the `-b` string in
`boot.sh` for that one boot, then put it back).

### Rung 3 — + SMC: `dtb-night3/r3-storage-smc.dtb`

Only after rung 2 passed (or, if storage is parked, build the equivalent with
`mkdtb.sh … ans_mbox sart nvme` added to the disabled list).

Expected new lines: `macsmc` / `apple-smc` probe, SMC mailbox on IRQs
562/561/564/563 (first hardware evidence for slice 1's SMC half), `smc-gpio`,
`macsmc-hwmon`. Failure = wedge → the SMC ASC +0x8000 mailbox window or SRAM
region; record and move on with SMC disabled again.

### Rung 4 — + MTP keyboard/trackpad: `dtb-night3/r4-storage-smc-mtp.dtb`

Expected: `apple-dockchannel`, `dockchannel-hid`, MTP RTKit boot, and — the
real test — **the keyboard works at the `/ #` prompt.** Have Doug type
`cat /proc/partitions` and `dmesg | grep -iE 'nvme|smc|dart|hid|mtp'` and
photograph it. Dead keyboard with no probe error = the AFE/STM reset GPIOs
8/24 (copied from J413/J613, unconfirmed on J713) — checklist item 6. Record;
do not guess new GPIO numbers tonight.

Note MTP needs SMC GPIOs, so rung 4 assumes rung 3 passed.

### Rung 5 — everything: `dtb-night3/r5-all.dtb`

Adds dwc3_0/dwc3_1 and their four DARTs (SID <1,14>, ADT-proven by patch
0012, never yet exercised). Expected: `apple-dart` probes on the two USB DARTs
(`0x402f80000`, `0x40af80000`), `dwc3` probes, `sn201202x` / `tps6598x`
Type-C PD lines over SPMI. Watch for **DART translation faults on IRQ
1283/1345** at first USB DMA — if they appear, the SID pairing is still wrong
(record which SID the fault names). If the HPM driver times out, that is the
precedent-only "select" IRQ (13/39/65) — record.

Beyond rung 5 (PCIe/Wi-Fi) needs a DT edit (no board file enables `pcie` yet)
— not tonight unless everything above passed with time to spare.

## 6. Building a different DTB mid-session

Labels you can disable: `dwc3_0 dwc3_0_dart_0 dwc3_0_dart_1 dwc3_1
dwc3_1_dart_0 dwc3_1_dart_1 smc_mbox smc ans_mbox sart nvme mtp mtp_mbox
mtp_dart mtp_dockchannel`. Example:

```sh
~/omarchy-firstboot/tools/mkdtb.sh ~/omarchy-firstboot/dtb-night3/custom.dtb ans_mbox sart nvme dwc3_0 dwc3_1
```

Exactly ONE `simple_bus_reg` warning is the proven baseline (the script hides
it). Any other dtc output = stop and read it. If you must edit the DT source
(`omarchy/linux-asahi/arch/arm64/boot/dts/apple/t8132*.dts*`), commit it in
`linux-asahi/` and mirror it into `omarchy/patches/` as 0014+ at session end.
The DT must say only what the ADT (`air-adt.bin`) proves; use
`omarchy/tools/adt.py` to look things up, never another SoC's numbers.

## 7. If a KERNEL rebuild is needed (unlikely tonight)

The tree is on the OptiPlex, `kb2uka@optiplex:~/t8132-build/linux` (asahi @
77cb8f24c + M4 hacks + the NVMe patch applied, configured; incremental build
≈ 3 min: `make ARCH=arm64 LLVM=1 -j12 Image.gz`). From this mini, plain SSH
to it failed today with "Host key verification failed" — accept the host key
interactively from the GUI Terminal first. **Long builds there MUST run
under tmux** (a nohup job died when the SSH session dropped). Copy the result
here as a NEW filename (never overwrite a staged kernel) and record its sha.

## 8. Findings log — append as you go (absolute dates and times)

(empty at staging — fill in per boot: `2026-09-02 HH:MM rung N dtb sha …`)

## 9. End of session

1. Doug: 1TR → `bputil -f` (Full Security; the Air is his daily machine
   again). Confirm it boots macOS.
2. You: make sure every boot log and proxy log is in `logs/`; move Doug's
   photos into `logs/photos-20260902/` if he AirDrops them.
3. Write "Night 3 results" as a new section at the END of
   `omarchy/docs/t8132-gap-analysis.md` (headline, per-rung outcome, every
   IRQ/probe fact, checklist verdicts, ranked next steps) and mirror this
   file's section 8 into `omarchy/docs/first-boot-runbook.md`.
4. Preserve source changes, hashes, redacted logs, and observations together.
   Keep generated binaries out of the patch repository.

## 10. Recovery

- Wedged boot chain → DFU + Apple Configurator on this mini → **Revive**
  (data-safe). Never Restore.
- macOS is always reachable from the power-button boot picker / 1TR
  `bputil -f`.
- The proxy devices vanishing after a boot is normal; only a device that
  never reappears after a power-cycle is a problem.
