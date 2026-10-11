# J713 trackpad startup retry

The experimental installer’s J713 driver already contains the qualified
[version-2 power requests](j713-input-power-v2.md). A later fresh install
still encountered `AFE: No HINT_L`, followed by a READY timeout. Both power
commands returned a positive successful response length. The matching
80,524-byte trackpad firmware was present. An ordinary reboot subsequently
reached `Touch MT ready`; that recovery does not establish the cause of the
initial AFE failure.

The source has a separate, reproducible recovery defect: `dchid_start_interface`
sets `starting`, but `dchid_open` never clears it after its READY wait expires.
Every later open then returns `-EINPROGRESS`. Close and stop do not clear it.
The patch here repairs that startup lifecycle and adds bounded recovery; it
does not claim to repair the underlying AFE condition.

## Scope and behavior

Apply `patches-kernel/j713-input/dockchannel-start-retry.patch` after the
version-2 power patch. The exact supplied prepared driver before this change
has SHA-256 `77ef81abc66a61d08818142895bc02bec2b01abe9461195c3c575799da91d1eb`.
The prepared kernel release is `7.1.9-m4-gpu-backport-20261008`.

Only `multi-touch` on a machine matching **both** `apple,j713` and
`apple,t8132`, with `dockchannel_hid.m4_power_v2=1`, uses the new path:

- A per-interface mutex serializes the complete startup and its terminal
  state. READY delivery remains independent of that sleeping mutex.
- An open makes at most three full startup attempts. Each sends the same
  firmware and existing OFF/ON phase requests, then uses the existing
  2,000 ms READY wait. Only that wait timing out permits another attempt.
  Firmware, GPIO and command errors return immediately, including a command
  returning `-ETIMEDOUT`. The existing 1,000 ms command timeouts remain.
- Every terminal result clears `starting`. Exhaustion returns `-ETIMEDOUT`
  without setting `open`; a subsequent open can try again. A completed READY
  skips further startup commands.
- READY is not reinitialized between attempts. A late READY at a timeout or
  after exhaustion is retained. READY identifies the interface, not an
  attempt; this patch adds no invented generation or cancellation protocol.
- The validated firmware copy and its immutable coherent DMA upload buffer
  are cached for that interface’s device lifetime. Repeated failed opens do
  not accumulate firmware/DMA allocations. The DMA buffer is neither freed
  nor rewritten while the coprocessor might still reference it. An allocation
  failure returns immediately and a later open can retry that allocation.

The legacy open path, other boards/interfaces, reset command encoding and
READY handler are unchanged. The bound is three attempts, not a hard six-second
wall-clock deadline: transport waits, firmware acquisition, mutex contention
and scheduling also take time. No startup delay, transport unbind, GPIO
sequence, firmware contents or device-tree change is introduced. In particular,
this transport’s platform remove callback contains `BUG_ON(1)`; unbinding it
is not a recovery procedure.

## Regression proof

Retain the unmodified prepared driver as `before.c`, apply the patch to an
isolated copy, then run on a leased Linux test host:

```sh
python3 tests/check_dockchannel_retry.py \
  --before before.c \
  --after kernel/drivers/hid/dockchannel-hid/dockchannel-hid.c
```

The checker compiles the actual interface structure, initialization, firmware
loading/upload, power request, startup and open functions. Host callbacks supply
firmware, allocation tracking, command responses and deterministic READY events;
two real pthreads exercise simultaneous opens. The old functions must fail the
retry and terminal-state assertions. Seven case groups cover timeout then READY,
three timeouts and later reopen, normal/already READY, negative command and
allocation errors, late READY, all opt-in guards, and serialized opens. Fifteen
mutations must fail assertions, including extra/unbounded attempts, omitted
state reset/guards, ignored command errors, buffer-cache omissions and removed
serialization. The restored source must pass again. Compiler errors and test
hangs do not count as successful mutation rejection.

These host checks do not emulate the MTP coprocessor, DMA coherence or the
kernel scheduler. A configuration-matched ARM64 kernel build and target boot,
physical trackpad input, keyboard preservation and repeated-start qualification
remain required before updating an installer’s pinned kernel. No native retry
success or fix for `No HINT_L` is claimed by the extracted-function tests.
