# M4 IO queue size correction — 2026-10-06

The preserved split-window driver mapped the M4 register resources separately
and wrote the extra IO queue base addresses. It still wrote the legacy pending
command count to controller offset `0x1210` early in reset.

[Yureka Lilian's September 6 v2 patch](https://lists.openwall.net/linux-kernel/2026/09/06/915)
documents that M4 uses this offset for zero-based IO queue sizes; the queue
creation commands' size fields are ignored. This patch follows that ordering and size encoding. It retains the existing
`lo_hi_writeq` queue-base writes (two 32-bit stores, as in m1n1's
`write64_lo_hi`); upstream v2 uses `writeq` instead. Access width is not changed
by this correction:

| Hardware path, depth 64 | Before | Corrected |
| --- | --- | --- |
| M1–M3 | `0x00400040` before queue creation | unchanged |
| M4 | `0x00400040` before queue creation | `0x003f003f` after CQ/SQ base writes |

In this patch, `has_separate_nvmmu` identifies the T8132 hardware entry and also
gates its extra queue-register programming. Future hardware entries must verify
both properties before reusing it. The upstream patch uses a separate
`needs_ioq_register` flag in its different resource-mapping implementation.

## Reproduce the code-level check

Use an isolated kernel worktree at Asahi `asahi` commit
`77cb8f24c2381a8abb7272d7bbdec548d6426a8a`. Apply
`patches-kernel/nvme-apple-t8132-split-nvmmu.patch`, then run the check.
Maintainer development runs use a remote fleet host and its shared lease:

```sh
python3 tests/check_nvme_queue_writes.py /path/to/kernel/drivers/nvme/host/apple.c
```

The check extracts and compiles the actual queue setup blocks and referenced
register constants from the driver. Mock MMIO records values and ordering. It
requires one size write, both correct DMA base addresses before the M4 size
write, and the unchanged legacy path. It also consumes the actual hardware
profiles and T8132 compatible mapping, checks NVMMU queue-base/invalidation
window routing, and checks the existing status-read address. Those address
checks preserve the source contract; they do not prove the unresolved hardware
status offset. It needs Python 3 and a C compiler.

The old patch failed with M4 `value=0x00400040 phase=0 bases_before=0`.
The correction passed with M4 `value=0x003f003f phase=1 bases_before=2`.
The original before/after checks ran on a macOS test host.

## Hardware status

This verifies emitted register programming, not firmware behavior, physical bus
access, or successful storage I/O. The corrected ARM64 kernel compiled successfully on OptiPlex with Clang/LLD
22.1.8. Its minimal-control boot on J713 reached the initramfs shell, confirmed by
a panel photo showing the candidate version and `/ #` prompt. Storage-only
boots still stop before the initramfs. Mailbox-only and mailbox-plus-SART
controls reach the shell; this narrows the unresolved path to work triggered
by the NVMe consumer, not to a proved failing instruction. Detailed tracing
shows mailbox/SART binding but no demonstrated Linux storage I/O.

The old kernel remains preserved. The candidate records its source revision,
patches, full configuration, compiler version and artifact hash. The recovered
Fedora configuration was a seed, not the exact missing historical config.

Keep the remaining uncertainties explicit: mailbox/power initialization, the
controller mapping beyond the ADT length, and the NVMMU status offset are not
resolved by this change. Require panel/console evidence for each boot. Do not
mount or write the macOS filesystem from the experimental kernel.

Candidate `Image-m4hacks-smc-nvme-ioq-20261006.gz`:

```text
SHA-256 49614fdc6c60aba68b10b80436abc06f5d5d1c1156e8f1f1ece0dbb9c9360067
.config 8e3f6192144df141fae4df7be411339e83b0512b628296f4e9e4d870d4b2f096
```

The candidate combines the existing M4 boot workarounds with the corrected NVMe
patch. Its configuration uses 16 KiB pages, built-in storage/SMC/input/USB drivers,
and embedded kernel configuration for later provenance recovery. Rust, SEP and
Asahi GPU support are disabled for this bring-up candidate; this is not the final
hardware-accelerated desktop kernel.
