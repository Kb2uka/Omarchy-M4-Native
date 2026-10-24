# T8132 SEP handoff: mailbox IRQ roles and m1n1

Three changes let a Linux SEP driver attach on a T8132 (M4) Mac. Without
them, loading a driver for the disabled `sep@3aa600000` node either freezes the
machine or gets no answer from the SEP. This repository ships no SEP driver and
keeps the SEP node disabled; the changes only make the handoff correct for a
driver that is loaded later.

Hardware validation is one 15-inch M4 MacBook Air (Mac16,13 / J715) on the
J715 tester kit kernel (7.1.9-m4-gpu-backport-20261008), with the out-of-tree
aurora-derived `apple_sep` driver loaded by hand. Nothing here was run on a J713
or a Mac mini.

## 1. SEP mailbox IRQ roles (patch 0018)

Patch 0004 copied the SEP mailbox interrupts in ADT order, `390 389 392 391`
for `send-empty`, `send-not-empty`, `recv-empty`, `recv-not-empty`. As patch
0015 found for the ANS mailbox, that order puts the line asserted while the
receive FIFO is empty (391) in the `recv-not-empty` slot. The ASC v4 mailbox has
no IRQ acknowledge, so as soon as a driver starts the channel the receive
handler polls an empty FIFO in hard IRQ context without end.

Observed on the J715: with the ADT order, loading the driver froze the machine
within seconds of mailbox start, with nothing persisted to the journal. An
earlier load that stopped before mailbox start did not freeze. With
`389 390 391 392` the same load starts the channel and later counts the SEP's
replies on 392. The SMC, MTP and ANS mailboxes already use the higher line of
the receive pair.

## 2. `dt_set_sep` alias lifetime (`patches-m1n1/sep-alias-lifetime.patch`)

`dt_set_sep` keeps the pointer returned by `fdt_get_alias(dt, "sep")`, then
calls `dt_device_add_mem_region`, which inserts `memory-region` into the FDT.
The insertion moves the aliases node's storage, so the later
`fdt_path_offset(dt, path)` reads stale bytes and fails. The patch resolves the
stable alias name `"sep"` each time, keeps the helper's negative return in an
`int` instead of a `size_t`, checks the two `fdt_setprop` calls, and logs each
handed-over item.

Offline: a host build of the archive's libfdt run on the exact failing DTB under
AddressSanitizer and UBSan failed with the borrowed pointer and succeeded with
the alias name on identical input. On hardware, the J715 boot image whose only
device-tree change added `/aliases/sep` did not boot with the unpatched stage 2;
with the patch it boots and the SEP node carries `memory-region`,
`local-policy-manifest` (2,889 bytes) and `iboot-manifest` (6,424 bytes).

## 3. No SEP ROM random request on T8132 (`patches-m1n1/t8132-skip-sep-rom-random.patch`)

`dt_set_rng_seed_sep` sends a ROM-endpoint (0xff) GETRAND to the SEP. On T8132
iBoot hands over a running sepOS (the ADT has `sepfw-booted = 1`). On every
logged J715 boot the request already failed (`SEP: couldn't get enough random
bytes for KASLR seed`) and the ADT seed was used. The SEP takes the request off
the mailbox and never answers, and from then on it reads nothing from the
mailbox.

Observed with a read-only snapshot of the mailbox control registers (FULL bit
16, EMPTY bit 17) taken by the driver around its warm registration:

| Stage 2 | A2I before send | A2I after send | A2I at timeout | SEP replies |
|---|---|---|---|---|
| unpatched | `0x0002cc01` (empty) | `0x0010cd01` (1 queued) | `0x0010cd01` (still queued) | none |
| patched | `0x0002ee01` (empty) | `0x0002ff01` (empty, consumed) | n/a | 12 endpoints in ~280 ms |

The I2A control stayed empty in the unpatched case, so this is not a receive
IRQ problem. With the patch the SEP advertised its endpoints, the driver
provisioned its keybag, and an SSH key held by the SEP signed through OpenSSH.
That held after a reboot with the driver loaded at boot. Linux still receives
the same ADT seed it received before.

## Building

The patches apply in this order to the alpha.1 stage 2 source:

```sh
patches-m1n1/build-test-loaders.sh source out sep1 stage2 \
    patches-m1n1/sep-alias-lifetime.patch \
    patches-m1n1/t8132-skip-sep-rom-random.patch
```

The hardware-tested stage 2 was built from the same pinned archive and the same
two patches, but natively on ARM64 (clang 21.1.8, rustc 1.93.1), not in the
x86-64 alpha.1 builder. It is source-equivalent, not byte-identical, to what
this recipe produces.

`display/j713/m1n1-base/src/kboot.c` carries the same two code paths. It is not
changed here because the J713 backlight loaders built from it are pinned by hash
and would need requalification on a J713.
