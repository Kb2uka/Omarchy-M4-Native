# J713 ANS mailbox interrupt correction

Patch 0015 corrects both ANS mailbox interrupt pairs. The original DT assigned
send-empty/send-not-empty to IRQ1004/1003 and receive-empty/receive-not-empty
to IRQ1006/1005. The corrected roles are IRQ1003/1004 and IRQ1005/1006.
The patch edits the SoC include and therefore affects every including T8132
board. Hardware validation has been performed only on J713. Other mailboxes
are outside this patch's scope.

## Evidence from the Air, 2026-10-06 Eastern

The ADT lists `1004,1003,1006,1005,1018` for ANS but does not label those
entries with the Linux mailbox role names. The earlier audit's `MATCH` for
role ordering was an assumption, not a hardware validation.

Read-only proxy observations after reset:

```text
ANS A2I control @ 0x481608110 = 0x2dd01
ANS I2A control @ 0x481608114 = 0x28801
Both FIFO control registers have EMPTY (bit17) set.
AIC IRQ1003 asserted; IRQ1004 clear; IRQ1005 asserted; IRQ1006 clear.
```

The AIC hardware-state address was derived from the live AIC3 ADT properties
and capability register: base `0x381000000`, configuration offset `0x10000`,
4096 maximum IRQs, hardware-state offset `0x14800`. No event/acknowledge
register was read and no mailbox message was consumed in this observation.

Diagnostic Linux checkpoints showed the receive handler reading an empty
FIFO and exiting its poll function, while the code after enabling the
selected receive interrupt was not observed. This supports repeated empty
receive interrupts as the startup stall mechanism. The controlled DT change
then made the previously unreachable code execute.

The checkpoint channel uses a host-armed 90-second watchdog. A selected
checkpoint shortens it once to 15 seconds; an early reached checkpoint returns
the USB proxy in about 22 seconds, while the absent control returns in about 96.
Early positive and absent negative controls were calibrated, and every boot
ended with a fresh responding proxy and watchdog control 0. Timing is evidence
of checkpoint reachability; it is not a full console log or a return-value check.

| Checkpoint | Original receive pair | Corrected receive pair |
|---|---:|---:|
| After receive IRQ enable, diagnostic kernel A | 96.403 s | 22.079 s |
| After receive IRQ enable, diagnostic kernel B | 96.326 s | 22.067 s |
| RTKit initialization returned, kernel A | not tested | 21.904 s |
| ANS boot-ready status accepted, kernel A | not tested | 21.891 s |

Kernel A SHA256: `59ea7308a1f939c497840aa1caa04e7711d884d16b1665c84cd3dc250685de36`.
Kernel B SHA256: `53b4af7deffaf2ea0bffce61665210b3ab7d9b466ec05305342fb5f53c6c4fb7`.
Both are diagnostic variants of the preserved 7.1.9 bring-up kernel.

The initramfs stayed fixed at SHA256
`5d60e6c45d6ce81768f731f127aab69304d981476c02b6fcb31a74401925c479`.
Boot arguments were `nosmp idle=nop panic=0 console=tty0 fbcon=nodefer
loglevel=4 initcall_debug m4trace_hit=N`, where N selected the checkpoint.
No macOS volume was mounted or written.

## Send pair follow-up

The same idle capture identifies IRQ1003 as the asserted send-empty line.
The driver enables that line when a non-atomic send finds the FIFO full,
then waits for an empty notification. Leaving it assigned to the opposite
condition could defeat that wait. A full-FIFO send was not forced or observed,
so that failure scenario is a code-derived risk, not a captured failure.

Changing only the send pair in the receive-corrected DT reached controller
initialization (checkpoint 520, 22.059 s). Create Submission Queue success
(checkpoint 712) still was not reached (96.342 s). Both returned a responsive
proxy with watchdog control 0. Kernel SHA256 was
`efdd1cb51dfd18765e86127cd03cff32815ef4a9804821ef5cd119af1727b197`,
with NVMe hardware monitoring disabled. The send correction is therefore
not sufficient to resolve the remaining storage failure. With the same kernel
and the receive-only image, Create Completion Queue success (checkpoint 711) was reached in
22.058 s, but Create Submission Queue success was not reached (96.297 s).
The receive-only image was not separately tested at checkpoint 520 in that
comparison. The both-pairs image was not separately tested at checkpoint 711
or at the Create Submission Queue timeout checkpoint. Its remaining stop is
therefore bracketed between controller initialization and Create Submission
Queue success; the exact command timeout was captured only with the
receive-only image.

## Reproduction and regression check

Apply patch 0015 after the existing storage DT patches and rebuild the J713
DTB with the same enabled devices. The current patch source rebuild reproduces
the original and both-pairs-corrected storage images byte for byte. The
receive-only image is retained as a historical comparator. Relative to it,
the both-pairs image swaps only send-empty/send-not-empty IRQ1004/1003 to
IRQ1003/1004. The image hashes are:

| Image | SHA256 |
|---|---|
| Original r2 | `d789bf7f6d7661c122fd16b446f3861a08c01918b2239ef8ab3c9e57f7f4a3f7` |
| Historical receive-only correction | `cea68eaecc142279951a3140078f9641a4dcfba7b8ecc033d9de19df813ef299` |
| Both IRQ pairs corrected | `24952485f10cc6fb4e713b76836edc7bc53e2362e528fec2734a9f20044ac932` |

Run the offline comparison:

```sh
python3 tests/check_ans_receive_irq.py original.dtb candidate.dtb
python3 tests/check_ans_receive_irq_guards.py original.dtb candidate.dtb
```

This comparison is specific to the isolated patch: pass the original image
and the candidate built with both pairs corrected. It deliberately rejects
an already-corrected baseline or additional unrelated DT changes. It is not
a general validator for every later DT variant. The checker requires J713,
T8132 mailbox and AIC3, named three-cell level interrupts, the captured original
and corrected IRQ identities, and unchanged other nodes/properties. It prints
both input SHA256 hashes. It does not infer semantics from ADT list order.

The general `adt-audit.py` IRQ set comparison is unchanged and cannot validate
mailbox role order: its `MATCH` only confirms membership. Use this focused
comparison for the captured ANS correction.

## Remaining work

On the historical receive-only image, controller identification succeeds.
With hardware monitoring enabled, a Get Log Page command times out during
hardware-monitor initialization. With hardware monitoring disabled,
controller initialization succeeds but Create Submission Queue times out.
For the current both-pairs image, the observed checkpoint bracket is given in
the send-pair follow-up above.
Earlier receive-only diagnostic checkpoints observed command submission,
an NVMe interrupt and an admin completion. These observations do not establish a functioning block
device or successful media reads. Storage, keyboard, GPU and desktop completion
are not claimed.

The [Asahi ASC hardware description](https://asahilinux.org/docs/hw/soc/asc/)
describes the lowest mailbox IRQ as send-empty and the highest as
receive-not-empty on earlier hardware. That supports this experiment's direction;
the J713 observations and controlled boot comparison provide its target-specific
evidence. The SoC-wide change is tested only on J713; other T8132 boards remain unverified.
Do not copy these IRQ numbers to other coprocessors.
