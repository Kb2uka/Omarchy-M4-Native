# J713 SMP TrustCache reservation

On the M4 MacBook Air J713, Linux must not allocate Apple's TrustCache pages.
The observed bootloader reserves the adjacent Apple device tree (ADT) and its
own image but leaves the intervening TrustCache allocation available to Linux.
Secondary cores retain CTRR_D protection over both the ADT and TrustCache.

## Evidence

The failing four-core boot reached userspace, then reported sixteen CPU3 write
permission faults in `clear_page` at `DC ZVA`. The first fault was at physical
`0x10004080000`; the Linux PTE's read-only bit was clear. Every captured fault
fell inside the unreserved 384 KiB gap between the ADT and bootloader image.
Worker restoration and the scheduled SSD read check did not complete.

A later, same-boot register and ADT capture identified:

| Field | Value |
| --- | --- |
| ADT start / size | `0x10005ab0000` / `0x74000` |
| TrustCache start / size | `0x10005b24000` / `0x60000` |
| Boot image start | `0x10005b84000` |
| CTRR_D lower / upper | `0x10005ab0000` / `0x10005b83000` |
| Boot P-core C/D control values | `0` / `0` |
| Three sampled E-core C/D control values | `0x8000000000000001` / `0x8000000000000001` |

The last protected 4 KiB page ends exactly at the TrustCache allocation's end.
The protected bounds agree across the sampled cores; their control values
explain why primary-core-only placement could mask the missing reservation.
Register names and encodings follow upstream
[CTRR/CTXR documentation commit](https://github.com/AsahiLinux/m1n1/commit/442c7a28de0333c09badd96268d62c0eafe4550f).

Adding only a current-boot TrustCache memory reservation produced:

- Two short four-core passes with initial primary-core worker placement.
- One short four-core pass and one short ten-core pass with default placement.
- A 120-second ten-core run with default placement, 46 complete diagnostic
  snapshots, no captured Oops, and successful read-only SSD checks.
- Each CPU0–9 passed singleton affinity/readback checks and four rounds of
  4 MiB anonymous-memory initial-zero and pattern verification.
- Every boot returned through the watchdog, followed by an independent fresh
  proxy and watchdog-control-zero check.

The longer run used kernel SHA256
`0ce617a2b965c3620c75b91a6080e2f33c9e9dbd6a814392c94729b974124bac`,
initramfs SHA256
`61181a730448a2a7744b793199e4176877b032efedd879f4a38565d0d0a8a222`,
and DTB SHA256
`b85fcae07cfcaafebe4eaef1c3d7cf8aac261fcab56042c6cc462117be1173dd`.
The installed bootloader source was `c9ef56f7356e93e6f6cac116c529d31c277ef6a6`.
Its source and image were not changed. These are bounded diagnostic results,
not comprehensive stability or complete hardware qualification.

The automatic host wrapper subsequently passed the same 120-second ten-core
check: 46 complete snapshots, CPU0–9 memory passes, matching SSD reads, no
captured Oops, and fresh proxy recovery after 126.402 seconds. Its live FDT
contained the freshly measured `0x10004a70000 / 0x60000` reservation. The
generated DTB SHA256 was
`8ae02d9ef8bd5c2eb51fd6893ee419edf9740654d761bd18bd960cb5c29c84f8`;
the source DTB was
`89c86a62190045dd82e448d934f9bd6f393e64b62c267e6e1e4ffe7761e4cb82`.
An earlier wrapper attempt rejected the ADT parser's integer subclass before
watchdog arming. A regression reproduced that failure; the corrected range
check accepts integer subclasses while continuing to reject booleans.

## Reproducible host handoff

`tools/air_trustcache.py` wraps an existing watchdog loader. It patches only
that host process's DT preparation call; it does not edit the bootloader tree,
write CPU protection registers, arm the watchdog, or mount storage.

For example, with an existing validated `air_watchdog.py` runtime:

```sh
python3 tools/air_trustcache.py \
  --proxyclient /path/to/m1n1/proxyclient \
  --watchdog-runtime /path/to/air_watchdog.py \
  --output-dtb /path/to/new-boot-evidence.dtb -- \
  --device /dev/ttyACM0 --serial EXPECTED_AIR_SERIAL \
  --arm --seconds 120 \
  --kernel /path/to/Image.gz --dtb /path/to/unmodified-j713.dtb \
  --initramfs /path/to/initramfs.cpio.gz \
  --bootargs 'maxcpus=10 idle=nop panic=0 console=tty0'
```

The example's watchdog options explicitly request one bounded boot/reset;
use the device identity, kernel arguments and recovery procedure validated for
that bench. This repository's separate watchdog change supplies the runtime;
it is not bundled here. The wrapper is exercised with the pinned diagnostic
runtime; other runtime revisions need their own integration verification.

Immediately before the existing `kboot_prepare_dt` call, the wrapper checks
J713/T8132 identity, watchdog-disabled state, live ADT allocation adjacency and
RAM bounds, and live CTRR_D limits. It reserves only the live TrustCache extent,
preserves DT structure and strings, saves an exclusive evidence file, uploads
and reads back the modified DTB, then invokes the original preparation call.
The original runtime retains ownership of the final watchdog-protected boot.
A disagreement aborts before that handoff. An already complete reservation
is retained; partial overlaps are rejected.

**Addresses change on each Apple boot. Never reuse the example addresses or a
previous generated DTB.** Start with the source DTB and regenerate through the
live wrapper on every boot. A saved DTB is evidence, not a persistent installer
fix. This does not make the on-disk boot path SMP-safe by itself.

## Validation and limits

Offline checks run with `python3 tests/test_air_trustcache.py` on a fleet host.
They open neither serial devices nor network sockets. The actual Air, live FDT
reservation and watchdog recovery require separate hardware verification.

`idle=nop` remains in use. CPU/GPU clock range and DVFS, CPU thermal control,
accelerated graphics, Wi-Fi/Bluetooth, audio, suspend, physical input events,
installed rootfs and storage writes are not established by these tests.
