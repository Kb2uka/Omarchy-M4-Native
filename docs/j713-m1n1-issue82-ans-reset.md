# J713 #82: stage 1 freezes at the ANS reset

[Issue #82](https://github.com/Kb2uka/Omarchy-M4-Native/issues/82) is one
13-inch M4 Air that stops on the m1n1 logo at every boot. Two other M4 Airs
(the owner's and the #57 reporter's) boot the same loaders. The first theory,
a dockchannel TX FIFO that fills and is never drained, is wrong: the reporter's
counters show the FIFO never fills, and the bounded TX wait in
[j713-m1n1-dockchannel-tx.md](j713-m1n1-dockchannel-tx.md) did not stop the
freeze. This note records what is now known and the loaders that test the
remaining explanations.

## What is observed

All hardware results are from the reporter's Air, from photos of marker
builds and from counters handed to Linux. Addresses are in test 1's stage 1
(`54807eb5…`). The reporter's phase hooks in alpha.1's `nvme_shutdown()`
(0x23dbc–0x23de8) sit 0xc0 lower than the same calls in test 1.

- **Where it stops.** Layout-preserving markers on alpha.1's stage 1 show
  phase 3 (the first `pmgr_reset(die, "ANS")` in `nvme_shutdown()` was called,
  so `rtkit_sleep()` returned) and never phase 4 (that call returned), in
  eleven photos over several minutes. No reboot follows.
- **Dockchannel off, same layout:** alpha.1's stage 1 with only
  `dockchannel_uart_init()` returning -1 boots every time.
- **Test 1** (every dockchannel TX wait bounded by a timer) freezes the same
  way, twice.
- **Rebuilt with diagnostics** (layout changed), every build boots. That
  includes one whose dockchannel path runs test 1's code unchanged with all
  extras off.
- **Counters** from those builds: `DATA_TX_FREE` reads 0x800 at init and on
  every read through stage 1, never less. 0xdb1 bytes are written, 0xd80 of
  them before `rtkit_sleep()` returns. The other 49 bytes are exactly
  `pmgr: resetting device 0.ANS\n` (29) and `nvme: shutdown done\n` (20), so
  `pmgr_reset(die, "ANS2")` prints nothing on this machine.

## What the code does in that window

From the published stage 1 source and test 1's disassembly:

```text
0x23e7c  bl rtkit_sleep        ANS acks SLEEP, then its CPU RUN bit is cleared
0x23e90  bl pmgr_reset "ANS"   -> pmgr_reset_device():
0x26934    ldr  PMGR power-state register (must be active)
0x26954    bl   debug_printf   29 bytes to the dockchannel, nothing on screen
0x26964    str  set DEV_DISABLE
0x26974    str  set RESET
0x2697c    bl   udelay(10)
0x26988    str  clear RESET
0x26998    str  clear DEV_DISABLE
0x23ea0  bl pmgr_reset "ANS2"  no such device here; returns at once
```

- `RTKIT_SYSLOG` is commented out in `config.h`, so `rtkit_recv()` only acks
  ANS syslog messages and prints nothing for them. Console output does not
  slow the sleep handshake.
- On the boot CPU SError is unmasked. `exc_serr()` prints a register dump to
  the console and reboots through the watchdog, which resets this model (see
  [air-watchdog.md](air-watchdog.md)). A freeze with no reboot is therefore
  not a plain SError.
- Upstream m1n1 (AsahiLinux/m1n1 `94395ad`, 2026-10-13) has the same
  `nvme_shutdown()`, `pmgr_reset_device()` and dockchannel code.
- alpha.1's kernel (companion `kernel-controller`, `drivers/nvme/host/apple.c`
  around line 1122) resets the same ANS through PMGR on every boot, when it
  finds the ANS CPU stopped. It then boots the firmware. That reset comes long
  after m1n1 stopped the ANS, and it works on all three Airs.
- An M4 Linux tree we track avoids the PMGR reset of ANS altogether: its NVMe
  driver says "Post-M4 ANS cannot be reset through PMGR: the reset assert
  raises an SError" and restarts a cleanly stopped ANS without a reset.

## What it means (inferred)

The freeze needs dockchannel output, a particular code layout, and the
PMGR reset of ANS within microseconds of the ANS CPU stopping. Two
explanations fit everything above:

- **A. Dockchannel access stalls.** A dockchannel register access issued in
  a short transient right after ANS sleeps never completes.
- **B. The reset races ANS.** Asserting the ANS reset at some delay after its
  CPU stops wedges the machine. The 29-byte print only matters because it sets
  that delay. With the dockchannel off the reset comes sooner; in Linux it
  comes much later; both work.

Jev (`jev-1.13.0`, advisory) put B ahead: "reset timing" 0.74, and 0.97 as
the best fit among five explanations. It gave the dockchannel stall 0.23,
layout alone 0.18, and the FIFO-full theory 0.05. All planted false
controls scored 0.06–0.22.

## The experiment loaders

`patches-m1n1/issue82-variants.py` turns test 1's stage 1 into three loaders.
Each edits test 1 in place, so no instruction moves. Each site must hold the
expected instruction, and each removed call must reach the expected function.
The version tag changes from `-dctx1` to `-i82v1`, `-i82v2` or `-i82v3`, so
`/proc/device-tree/chosen/asahi,m1n1-stage1-version` names the loader that
booted.

| Loader | Change | If A | If B |
|---|---|---|---|
| `v2` | `pmgr_reset_device()` keeps the print, skips the four PMGR stores | freezes | boots |
| `v1` | `nvme_shutdown()` skips both `pmgr_reset()` calls | likely boots | boots |
| `v3` | `pmgr_reset_device()` resets without printing first | boots | likely boots, as with the dockchannel off |

`v2` separates A from B. `v1` is the candidate fix in the layout that
freezes. ANS is still put to sleep and stopped, as upstream m1n1 does for an
ANS left running by iBoot (`nvme_ensure_shutdown()`), and Linux resets it
later. `v3` checks that the print inside the window is needed at all. Jev
chose `v2` first (0.83), rated the risk to a reporter who already recovers
through 1TR as low (0.74, medium 0.25), and rated `v1` as support for the
candidate fix at 0.77.

```sh
python3 patches-m1n1/issue82-variants.py m1n1-stage1-issue82-test1.bin out/
```

| Loader | SHA-256 |
|---|---|
| `m1n1-stage1-issue82-v1.bin` | `ac8e2c4aff14c4abce68a1a01c5ec401d7cdb7354602d5cac6dfb9c76219c798` |
| `m1n1-stage1-issue82-v2.bin` | `d75486df569a3be4b84603aa8c3fd24ebab703572d636c1b7a28afdd13000969` |
| `m1n1-stage1-issue82-v3.bin` | `e263faedcadb53fc8c817b864e4815baa7ba053b9091c4c37a8a9e6596aa9be1` |

Install each one as test 1 was installed: from 1TR with
`kmutil configure-boot … --raw --entry-point 2048 --lowest-virtual-address 0`,
with the installer's 64 KiB vars block appended. Stage 2 stays the reporter's
dockchannel-off build. A run passes when stage 2 starts and Linux shows the
variant's tag.

`tests/test_issue82_variants.py` runs in hosted CI. It checks that each
variant changes only its own instructions and the two tags, and that the
script refuses a different loader, a moved instruction, or a call to another
function.

## Not yet known

- Which of A and B is true. That is decided on the reporter's Air.
- Why only this Air hits the window. The two machines that boot have
  different system firmware (`mBoot-18000.120.36` and `mBoot-20457.1.29`
  against `mBoot-18000.161.10`), but one data point per firmware cannot
  separate firmware from unit-to-unit timing.
- A source fix. If `v1` boots and `v2` does too, the fix is to leave ANS
  asleep and stopped in stage 1's `nvme_shutdown()` without the PMGR reset,
  and let Linux reset it. That changes boot behaviour on every M4 install, so
  it ships only after the owner's Air and the reporter's Air both boot it.
