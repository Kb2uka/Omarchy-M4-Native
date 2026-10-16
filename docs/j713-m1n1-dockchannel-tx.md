# J713 m1n1 dockchannel TX bound

m1n1's dockchannel console waits for TX space with no limit. This patch bounds
that wait. It was written for
[issue #82](https://github.com/Kb2uka/Omarchy-M4-Native/issues/82), but it
does not fix #82: the reporter's Air still freezes with it, and their counters
show its FIFO never fills. The #82 freeze is inside the ANS reset call in
`nvme_shutdown()`; see [j713-m1n1-issue82-ans-reset.md](j713-m1n1-issue82-ans-reset.md).
What is left here is hardening for a FIFO that stops draining. That state has
not been seen on any Air.

## The unbounded wait

m1n1 uses the dockchannel UART as a second console. Its byte writer waits for
TX space with no limit:

```c
while (read32(uart_base + DATA_TX_FREE) == 0)
    ;
```

`iodev_console_write()` checks `DATA_TX_FREE > 0` once, then hands the whole
pending line to that writer. If nothing drained the FIFO, the first line
longer than the space left would spin forever. The console writes the UART,
then the dockchannel, then the framebuffer, so the stuck line would never reach
the screen. Upstream m1n1 still has the same loop.

## The patch

`patches-m1n1/dockchannel-tx-bound.patch` changes only
`src/dockchannel_uart.c`. Every wait for TX space now has a limit:

- Until anything has been seen draining the FIFO, the writer waits at most
  100 ms (`TX_TIMEOUT_US`). A consumer is seen when space comes back during a
  wait, or when there is more space than the writer's own writes left.
- Once a consumer has been seen, the limit is 2 s (`TX_CONSUMER_TIMEOUT_US`),
  so a debug host using m1n1's proxy over the dockchannel can pause briefly
  without losing reply bytes.
- On timeout the writer latches a stall and returns. While stalled and still
  full, later bytes are dropped at once, so a stall costs one wait, not one per
  byte. The first byte that finds space clears the stall.

An undrained unit would pay one 100 ms wait per stage and boot. A FIFO that
drains within the limit behaves exactly as before. A consumer that
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

- `undrained` (37 bytes free, never drained, 200 lines): the
  shipped source hangs with nothing on screen. The patched source puts every
  line on the screen, keeps the 37 bytes that fit, and waits 100 ms in total.
- `slow_drain` (a host drains one byte per 200 µs), `fast_host_pause` (a
  host keeps up, then pauses 150 ms) and `slow_host_pause` (a host slower than
  the writes, then pauses 150 ms): every byte arrives in order, unpatched and
  patched.
- `recover` (stall, then a host starts draining): everything written after
  the host attaches arrives whole.
- `consumer_vanishes` (a host drains, then stops for good): one 2 s wait, and
  every line still reaches the screen.
- `secondary` (a secondary CPU prints before the MMU is up, after a stall): no
  wait.

Twelve mutants must each fail a case. They cover the timeout, the stall latch,
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

```sh
patches-m1n1/build-test-loaders.sh source out dctx1 "stage1 stage2" \
    patches-m1n1/dockchannel-tx-bound.patch
```

## Testing on an installed Air

Stage 2 is replaced from Linux with `patches-m1n1/m4-m1n1-test-stage2.py`:

```sh
sudo python3 m4-m1n1-test-stage2.py install m1n1-stage2-issue82-test1.bin SHA256
sudo reboot
sudo python3 m4-m1n1-test-stage2.py status
```

It replaces only the first 0x110000 bytes of `boot.bin`, and only when they
are alpha.1's stage 2 or the #82 reporter's dockchannel-off build. Beside
`boot.bin` it keeps the old bytes as `stage2.pre-issue82.bin` and a receipt
naming both loaders as `stage2.issue82.json`. `rollback` writes the old bytes
back, keeping any devicetree update made in between, and only while stage 2 is
still the installed test build. It also finishes an interrupted install or
rollback.

Stage 1 is installed from macOS recoveryOS (1TR) with the command installer
step 2 uses, run against the test stage 1 instead of the kit's `boot/m1n1.bin`.
The file must be a boot object: the loader with the installer's m1n1 variables
appended, as prepared in
[j713-m1n1-issue82-ans-reset.md](j713-m1n1-issue82-ans-reset.md#testing-on-an-air)
(`cat m1n1-stage1-issue82-test1.bin vars.bin > boot.bin.test1`):

```sh
kmutil configure-boot -c boot.bin.test1 --raw --entry-point 2048 \
  --lowest-virtual-address 0 -v <Omarchy stub system volume>
```

`status` reports the running loaders from
`/proc/device-tree/chosen/asahi,m1n1-stage{1,2}-version`.

## Limits

- Stage 1 is installed by macOS (`kmutil configure-boot`, as installer step 2
  does), so it cannot be updated from Linux. Stage 2 is the start of the
  ESP's `omarchy/boot.bin`.
- These loaders are test builds. They are not in the installer or the update
  channel. On the #82 Air, test 1's stage 1 froze like alpha.1's; the owner's
  Air boots test 1's stage 2 with no new kernel errors.
