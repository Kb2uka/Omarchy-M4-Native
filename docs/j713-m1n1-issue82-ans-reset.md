# J713 #82: stage 1 freezes at the ANS reset

[Issue #82](https://github.com/Kb2uka/Omarchy-M4-Native/issues/82): two
13-inch M4 Airs stop on the m1n1 logo at every boot. Both run system firmware
`mBoot-18000.161.10`. Two other M4 Airs (the owner's and the #57 reporter's)
boot the same loaders. The first theory,
a dockchannel TX FIFO that fills and is never drained, is wrong: the reporter's
counters show the FIFO never fills, and the bounded TX wait in
[j713-m1n1-dockchannel-tx.md](j713-m1n1-dockchannel-tx.md) did not stop the
freeze. The PMGR reset of ANS right after stage 1 stops it is the cause, and
[the fix](#the-fix) leaves an M4's ANS stopped without that reset.

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
0x23e7c  bl rtkit_sleep        ask ANS to SLEEP, wait for the ack, clear CPU RUN
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
  [air-watchdog.md](air-watchdog.md)). An SError whose report completes would
  therefore reboot, not freeze. One whose report stalls, for example on a
  dockchannel access, would look like this freeze, so SError is not excluded.
- `rtkit_sleep()` returns and stops the ANS CPU even if the SLEEP handshake
  failed (its `ret < 0` check never fires on a `bool`). Phase 3 shows only
  that it returned, not that ANS acknowledged SLEEP.
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

B fits more of the evidence: it explains why the same reset works in Linux,
and needs no stall in the console hardware. Neither is proven.

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
later. `v3` checks that the print inside the window is needed at all. Boot
`v2` first. The worst expected result of each is the same freeze, recovered
through 1TR as before.

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

## Results

A second Air with the same freeze (Mac16,12, `mBoot-18000.161.10`, a stock
update 2 install) ran the loaders in order on 2026-10-15:

- **`v2` booted** to Linux
  ([report](https://github.com/Kb2uka/Omarchy-M4-Native/issues/82#issuecomment-6195132271)).
  The 29-byte print still lands in the window, with no reset after it. A
  predicted a freeze, B a boot, so B is supported.
- **`v1` booted** unattended to the desktop
  ([report](https://github.com/Kb2uka/Omarchy-M4-Native/issues/82#issuecomment-6204117990)).
  `asahi,m1n1-stage1-version` read `-i82v1`, NVMe came up and root mounted.
  The kernel log showed only RTKit `PANICLOG not yet written` syslog lines and
  macsmc-reboot `Missing NVMEM cell` lines.
- `v3` was not run. It would only show whether the print is needed for the
  freeze, which no longer changes the fix.

The first reporter has not run the loaders.

## The fix

[`patches-m1n1/stage1-ans-no-reset.patch`](../patches-m1n1/stage1-ans-no-reset.patch)
changes alpha.1's `src/nvme.c` only. A new `nvme_reset_ans()` does nothing
on T8132 (the ADT has `nvme-secure-bar`), and resets `ANS` and `ANS2` as
before on older SoCs. `nvme_shutdown()` and `nvme_init()`'s error path call it
right after `rtkit_sleep()`, in place of the two direct `pmgr_reset()` calls.
ANS is still put to sleep and its CPU stopped.

- `nvme_ensure_shutdown()` still resets ANS unconditionally. Nothing calls it
  (its one call in `nvme_shutdown()` is commented out), and it runs without
  `nvme_init()`, so `nvme_type` would not be set there and the new check could
  not guard it. A build that starts calling it needs its own T8132 check.
- Stage 2 needs no change. It is built without `CHAINLOADING`, so the only
  `nvme_init()` it can reach is the proxy's `P_NVME_INIT` command from a debug
  host. On a normal boot its `nvme_shutdown()` returns at once, because NVMe
  was never initialized there.

Only the `nvme_shutdown()` path was run on hardware, as `v1`. The error path
runs only when ANS fails to boot in stage 1. It stops ANS the same way, so it
gets the same change rather than keeping a reset that can freeze the machine
instead of reporting the error.

Linux then finds the ANS CPU stopped. alpha.1's `apple-nvme`
(`kernel-controller`, `drivers/nvme/host/apple.c`, `apple_nvme_reset_work()`)
asserts and deasserts the PMGR reset only when the CPU's RUN bit is clear,
then boots the firmware. The owner's Air logs that sequence on every boot
(`M4TRACE assert reset` … `deassert reset returned`).

### Hosted test

`tests/test_m1n1_stage1_ans_reset.py` compiles the real `nvme.c`, as shipped
and patched, into `tests/m1n1_nvme_ans_fixture.c`. That fixture simulates the
ADT, the controller and RTKit, runs `nvme_init()` then `nvme_shutdown()`, and
prints the order of ANS boot, sleep, reset and release:

| Case | Shipped | Patched |
| --- | --- | --- |
| M4, boots | `boot sleep reset:0.ANS reset:0.ANS2 free` | `boot sleep free` |
| M4, ANS boot status fails | same | `boot sleep free` |
| older SoC, either case | same | unchanged |

Five mutants must each change one case. They cover the T8132 check, both call
sites, and the order of sleep and reset.

### Building it

```sh
patches-m1n1/build-test-loaders.sh source out ans1 stage1 \
    patches-m1n1/stage1-ans-no-reset.patch
```

As with every loader here, this runs as root in `m4-gravity-builder:20261008`,
with network access for the Rust crates (Rust embeds `/root/.cargo` paths, so
another user changes the bytes). It first rebuilds alpha.1's stage 1 and
requires `18cc4cd1…`.

| Stage 1 | Patches | SHA-256 |
| --- | --- | --- |
| `ans1` | this fix | `8c5a15736c19d0a3428e64f01292791fb54dd68e09f2f5304a7210d19c42adce` |
| `dctx1-ans1` | dockchannel bound, then this fix | `77881a4c673c08c58e270b0adfa923701ddc538a072f7b2e3825e5faaa2aaec8` |

Each hash was reproduced by a second, separate build. In both, the
disassembly of `nvme_shutdown()` loads the byte that `nvme_init()` sets from
the `nvme-secure-bar` lookup and, when it is set, branches from just after
`rtkit_sleep()` past both `pmgr_reset()` calls to `rtkit_free()`:

```text
ans1  0x23ddc  bl   rtkit_sleep
      0x23de4  ldrb w8, nvme_type
      0x23de8  tbnz w8, #0, 0x23e10      T8132: skip both resets
      0x23dfc  bl   pmgr_reset "ANS"
      0x23e0c  bl   pmgr_reset "ANS2"
      0x23e10  ...  bl rtkit_free
```

### Testing on an Air

`kmutil` takes a boot object, not a bare loader. The installer builds it in
`src/m1n1.py`: the loader, then the m1n1 variables as `key=value` lines and
four NUL bytes, padded with zeros to a multiple of 64 KiB. alpha.1's stage 1
(`18cc4cd1…`) and both fixed builds are `0x120000` bytes, already a multiple
of 64 KiB. So after alpha.1's loader, the rest of the boot object the
installer left on the Omarchy stub is exactly the variables block. The `cmp`
line checks that the stub starts with alpha.1's loader, using the
`stage1-stock-m1n1.bin` (`18cc4cd1…`) the build script writes, so no
checksum tool is needed in 1TR. The stub's file is
`<stub system volume>/Finish Installation.app/Contents/Resources/boot.bin`
(the file installer step 2 passes to `kmutil`). In 1TR, work in a folder on
a disk that survives a restart, such as the macOS user's Downloads; 1TR's
home folder does not, and the rollback copy must still be there after a
freeze:

```sh
res="<stub system volume>/Finish Installation.app/Contents/Resources"
cd "<persistent folder holding m1n1-stage1-ans1.bin and stage1-stock-m1n1.bin>"
cp "$res/boot.bin" boot.bin.alpha1                      # keep for rollback
head -c 1179648 boot.bin.alpha1 | cmp - stage1-stock-m1n1.bin   # must print nothing
tail -c +1179649 boot.bin.alpha1 > vars.bin
cat m1n1-stage1-ans1.bin vars.bin > boot.bin.ans1
kmutil configure-boot -c boot.bin.ans1 --raw --entry-point 2048 \
  --lowest-virtual-address 0 -v "<stub system volume>"
```

On the owner's Air (2026-10-16) the `head -c … | cmp -` line printed a
mismatch in recoveryOS although the stub's first 0x120000 bytes are alpha.1's
`18cc4cd1…` (checked afterwards in Linux). If it does, nothing has changed:
copy `boot.bin.alpha1` to a disk Linux can read (the `EFI - OMARC` partition
works), check and assemble `boot.bin.ans1` there with `sha256sum`, `head` and
`tail`, and run only the `kmutil` line in recoveryOS.

A run passes when the desktop comes up and
`/proc/device-tree/chosen/asahi,m1n1-stage1-version` reads the build's tag:
`air-c9ef56f-gpu-stage1-20261008-ans1` or
`air-c9ef56f-gpu-stage1-20261008-dctx1-ans1`.

**Rollback.** If the Air freezes on the logo, hold the power button to reach
1TR, which does not run stage 1, and run the same `kmutil` command on
`boot.bin.alpha1` in that folder, the stub's original boot object. Never pass
a bare loader to `kmutil`.

Neither build is byte-identical to `v1`. `v1` is test 1 with two
instructions removed, and test 1 was built from an earlier revision of the
dockchannel bound (`a20df82`), which rebuilds to `54807eb5…` exactly. Any
rebuild moves code, and on the reporter's Air a moved layout booted even
without a fix. So a source build cannot show on that Air that the fix works:
`v1` shows it. What a source build must show on hardware is that it does no
harm.

## Not yet known

- Why only these Airs hit the window. The two that freeze share
  `mBoot-18000.161.10`; the two that boot have `mBoot-18000.120.36` and
  `mBoot-20457.1.29`. Two Airs per group cannot separate firmware from
  unit-to-unit timing.
- Whether the fixed stage 1 boots on the owner's Air and on the first
  reporter's Air. It changes boot behaviour on every M4 install, so it ships
  only after it does. Stage 1 comes from the installer kit's `boot/m1n1.bin`
  and is installed from macOS, so the fix reaches installed Airs as an
  installer kit update and a 1TR `kmutil` step, not through
  `omarchy-m4-update`.
