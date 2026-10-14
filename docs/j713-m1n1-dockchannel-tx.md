# J713 m1n1 dockchannel console hang

[Issue #82](https://github.com/Kb2uka/Omarchy-M4-Native/issues/82): on one
13-inch M4 Air (iBoot1 `mBoot-18000.161.10`), alpha.1 stopped on the m1n1
logo every boot, with nothing on screen. The reporter's marker builds showed
stage 1 freezing at a console `printf`. With `dockchannel_uart_init()` made
to return -1, the same Air booted alpha.1 unattended. The reporter's
"original" hashes are alpha.1's shipped loaders (stage 1 `18cc4cd1…`,
stage 2 `3a3f7a64…`).

## Cause

m1n1 uses the dockchannel UART as a second console. Its byte writer waits for
TX space with no limit:

```c
while (read32(uart_base + DATA_TX_FREE) == 0)
    ;
```

`iodev_console_write()` checks `DATA_TX_FREE > 0` once, then hands the whole
pending line to that writer. If nothing drains the FIFO, the first line longer
than the space left spins forever. The console writes the UART, then the
dockchannel, then the framebuffer, so the stuck line never reaches the screen.

The owner's Air (`mBoot-18000.120.36`) and the #57 reporter's Air
(`mBoot-20457.1.29`) reach Linux, so firmware version alone does not predict
the hang. Why the FIFO drains on those units and not on this one is not known.
Upstream m1n1 still has the same loop.

## Fix

`patches-m1n1/dockchannel-tx-bound.patch` changes only
`src/dockchannel_uart.c`. Every wait for TX space now has a limit:

- Until anything has been seen draining the FIFO, the writer waits at most
  100 ms (`TX_TIMEOUT_US`). It tracks how much space its own writes leave.
  More space than that means a consumer drained the FIFO.
- Once a consumer has been seen, the limit is 2 s (`TX_CONSUMER_TIMEOUT_US`),
  so a debug host using m1n1's proxy over the dockchannel can pause briefly
  without losing reply bytes.
- On timeout the writer latches a stall and returns. While stalled and still
  full, later bytes are dropped at once, so a stall costs one wait, not one per
  byte. The first byte that finds space clears the stall.

An undrained unit like the #82 Air pays one 100 ms wait per stage and boots. A
FIFO that drains within the limit behaves exactly as before. A consumer that
stops for good costs one 2 s wait, then output is dropped. The hardware UART,
framebuffer, USB proxy and dockchannel reads are unchanged.

`patches-m1n1/base/` holds the three unmodified MIT files the test compiles
(`dockchannel_uart.c`, `iodev.c`, `iodev.h`). `SHA256SUMS` pins them to the
per-file hashes in alpha.1's published `m1n1-stage1-files.json` and
`m1n1-stage2-files.json`, which agree for these files.

## Proof

`tests/test_m1n1_dockchannel_tx.py` runs in the hosted CI suite. It needs `cc`
and `patch` on `PATH`. It compiles m1n1's real `iodev.c` and
`dockchannel_uart.c` into `tests/m1n1_dockchannel_tx_fixture.c`, with m1n1's
warning flags, and drives the console as m1n1 does against a simulated 4 KiB
FIFO and clock:

- `undrained` (the #82 state: 37 bytes free, never drained, 200 lines): the
  shipped source hangs with nothing on screen. The patched source puts every
  line on the screen, keeps the 37 bytes that fit, and waits 100 ms in total.
- `slow_drain` (a host drains one byte per 200 µs) and `fast_host_pause` (a
  host keeps up, then pauses 150 ms): every byte arrives in order, unpatched
  and patched.
- `recover` (stall, then a host starts draining): everything written after
  the host attaches arrives whole.
- `consumer_vanishes` (a host drains, then stops for good): one 2 s wait, and
  every line still reaches the screen.
- `secondary` (a secondary CPU prints before the MMU is up, after a stall): no
  wait.

Eleven mutants must each fail a case. They cover the timeout, the stall latch,
consumer detection and both limits.

The host test does not emulate the hardware FIFO or iBoot's handoff state.
Hardware qualification is below.

## Building the test loaders

`patches-m1n1/build-test-loaders.sh` runs in `m4-gravity-builder:20261008`
with the job directory at `/job`. It takes the alpha.1 companion's `source/`
directory. For each stage it first rebuilds the unpatched source and requires
alpha.1's shipped hash, then builds the patched loader with the version tag
`air-c9ef56f-gpu-<stage>-20261008-dctx1`. Linux shows that tag in
`/proc/device-tree/chosen/asahi,m1n1-stage{1,2}-version`, which tells you
which loader booted.

## Limits

- Stage 1 is installed by macOS (`kmutil configure-boot`, as installer step 2
  does), so it cannot be updated from Linux. Stage 2 is the start of the
  ESP's `omarchy/boot.bin`.
- These loaders are test builds. They are not in the installer or the update
  channel until the #82 reporter's Air and the owner's Air both boot with them.
