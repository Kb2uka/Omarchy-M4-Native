# J713 native Linux USB log transport — 2026-10-07

Native Linux on the M4 MacBook Air can now serve diagnostic logs to its USB
host from a RAM initramfs. The strongest tested USB1 configuration uses DART
stream IDs **0, 1**, not the **1, 14** mapping asserted by historical patch
0012. This is a bench result, not a working Omarchy installation or a general
M4 USB support claim. This note adds evidence only; it does not alter the
historical patches or publish the experimental kernel and initramfs.

## Target and scope

The hardware target is the 13-inch 2025 MacBook Air, Mac16,12 / J713 / T8132.
The coordinating host was a Linux iMac. OptiPlex built the diagnostic kernel;
the Mac mini compiled DT variants. Neither test host was the hardware target.
The connected Air port was USB1. The Air's captured boot banner identifies
its installed firmware as `m1n1 v1.6.1-22-gc9ef56f`; the corresponding
AsahiLinux/m1n1 source revision is `c9ef56f7356e93e6f6cac116c529d31c277ef6a6`.
That revision's `src/iodev.h` places `IODEV_USB0` at 4, so the proxy's
reported IODEV 5 identifies USB1. The Linux controller is
`/soc/usb@40a280000`, and its PD controller uses SPMI SID `0xa`. Earlier USB0
experiments exercised the unused port and do not establish active-port support.

The tested path uses an opt-in, J713-only diagnostic PD-role override when the
cable is present but the status lacks USB2/USB3 data flags. It adjusts the
local status interpretation to request device mode, without changing the PD
power-role registers. Normal cable removal remains recognized. This workaround
is experimental and is **required context** for the results below; a DT stream
change alone has not been demonstrated to bring up the link.

The kernel has `CONFIG_USB_ETH=y`, `CONFIG_USB_ETH_EEM=y` and no `g_serial`.
The standard Ethernet gadget enumerates as `1d6b:0102`; the Linux host binds
`cdc_eem`. A static BusyBox RAM image brings up `usb0` at `fe80::713:2/64` and
serves only diagnostic GET endpoints through an IPv6 wildcard listener on
port 8080. No disk is mounted and no installation is performed.

The host used a temporary in-memory NetworkManager profile with IPv4 disabled,
IPv6 link-local enabled, and no default route, restricted to the observed USB
interface and `cdc_eem` driver. A previous DHCP profile timed out after 45 seconds.
Fixing that host timeout was necessary for the controlled comparison, but did
not itself fix the SID 1/1 transport stalls.

## Controlled stream-ID comparison

These two RAM boots used the same kernel, initramfs, boot arguments and host
profile. Storage, SMC and MTP were disabled in both DTs. Only the first USB1
DART stream changed from 1 to 0; the second remained 1.

| Observation | SID 1/1 control, 04:14:33 UTC | SID 0/1, 04:19:08 UTC |
|---|---:|---:|
| Armed watchdog window | 90 s | 90 s |
| Successful dmesg responses | 1 | 36 |
| Successful state responses | 2 | 37 |
| Complete dmesg/state pairs | 1 | 36 |
| Total successful response bytes | 256,346 | 9,154,575 |
| First-to-last successful response span | 2.019 s | 72.701 s |
| Armed watchdog to returned proxy | 96.308 s | 96.491 s |
| Fresh proxy check; watchdog control | passed; 0 | passed; 0 |

The monitor requested `/dmesg` and `/state` every two seconds, using one-second
connection and three-second request timeouts. A complete pair means both
responses with the same poll index were saved. The initial state-only poll
must not be counted as a complete pair. The span measures successful response
timestamps, not uptime, throughput or a long-duration reliability test.

Earlier serial-gadget experiments provide a separate comparison: changing the
second USB1 DART SID from 14 to 1 allowed USB reset handling and descriptor
enumeration, but ACM SET_CONFIGURATION still timed out. Changing the first
SID to 0 did not resolve that ACM failure. The bulk EEM result does not prove
ACM, USB host mode, SuperSpeed, the other port, or other M4 boards.

Historical claims that the ADT SID array `<1,14>` proves the consumer mapping
are too strong. A mapper inventory does not establish which stream each Linux
DART instance must use for this operating mode. The hardware results supersede
that conclusion for the tested J713 USB1 EEM device path only. The historical
patch stack still contains the old mapping; this document is not its replacement.

## Artifact identity and reproduction limits

All SHA-256 hashes below identify preserved local bench artifacts. They are
not download links: the binaries and raw captures are not shipped in this tree.
A fresh clone alone cannot reproduce these boots.

| Artifact | SHA-256 |
|---|---|
| Diagnostic kernel `eem-slowtrace` | `56a911a8316303b5e7463c12c853d12105ef9ec2f0224078cbf40add99648e85` |
| RAM initramfs `eem-v3` | `e61d4d41208036805529a7c138ebc04e35819a1b01c2a997703bc2f6ea7b87d2` |
| Control DT `r8-usb1-second-sid1` | `ee55c2b25063f35ef10fae18cfa6ca147f45494b6ed116c9ddf5ddc2721306dc` |
| SID 0/1 DT `r9-usb1-sids01` | `2103fc088b034752d52c1430db79dbe17611f9b8d90ba71acb41c6a9d9bf5dbf` |

The kernel source base was Asahi Linux
`77cb8f24c2381a8abb7272d7bbdec548d6426a8a`, with private diagnostic changes.
The image name includes slow-trace support, but its pacing option was **not**
enabled in the controlled stream comparison. Both boots used:

```text
m4bench_usb2_device=1 g_ether.use_eem=1
 g_ether.dev_addr=02:4d:34:00:00:02 g_ether.host_addr=02:4d:34:00:00:01
 g_ether.iSerialNumber=M4-BENCH-EEM nosmp idle=nop panic=0 console=tty0
 fbcon=nodefer loglevel=4 initcall_debug m4trace_hit=999
```

These are a single boot-argument string, wrapped for readability. Marker 999
was an unselected sentinel; the full watchdog window was intended. Earlier
120-second runs with named markers cannot establish checkpoint absence:
the marker implementation accepted only the exact 90-second armed value.
Those results were excluded from checkpoint conclusions.

## Storage status through 04:36 UTC

A subsequent SID 0/1 DT enabled ANS mailbox, SART and NVMe, corrected both
mailbox interrupt pairs, and extended the shared controller/NVMMU mapping.
With the NVMe driver loaded from the RAM image, the USB service remained usable
after the storage firmware crashed. The paced run started at **04:24:28 UTC**:
50 complete snapshot pairs were captured across 100.696 seconds; the returned
proxy passed its watchdog-zero check after 126.340 seconds. Its armed window
was 120 seconds, so its pair count is not directly comparable to the 90-second
stream-ID runs. The monitor used a 120-second wall-time loop, not a fixed poll
count.

These three storage runs (04:24:28, 04:27:36 and 04:36:11 UTC) used DT `r11-usb1-sids01-ans-alias`, SHA-256
`63e72ae46bdcebd9f14238f69e963a75a0834797541269024d0f3b5a082c621d`,
and RAM initramfs `eem-storage-early`, SHA-256
`6f4455da4110e7301192d31276d14971fdd80bbef2426a3c81f2755cb5c01499`.
The kernel was unchanged. Relative to the USB comparison's argument string,
`initcall_debug` was removed and `log_buf_len=1M m4bench_slowtrace=1` added.
Marker 999 remained the unselected sentinel.

Representative native log lines from the 04:24:28 UTC paced run:

```text
[    7.505756] nvme-apple 4c5cc0000.nvme: M4TRACE RTKit wake begin
[    7.714843] nvme-apple 4c5cc0000.nvme: RTKit: co-processor has crashed
```

The firmware SError report contained EL1h, ESR `0xbe000000`, FAR `0x49802c000`, and
PC `0xb7a004`. Wake failed before queue setup, and Linux reported reset failure
`-62`. This is distinct from the earlier built-in-driver run that reached
Identify/Create CQ and timed out at Create SQ. It must not be labeled the same
failure merely because both prevent storage use.

The 04:27:36 UTC run added `pd_ignore_unused`; the 04:36:11 UTC run
instead removed `m4bench_slowtrace=1`. Both captured native RTKit wake-failure
logs over USB. Their reports again contained FAR `0x49802c000`, with different
PC values and crash timestamps. The reported FAR is not treated as a precise
faulting transaction or proof that the crashes were identical. A missing DART fault interrupt does not
prove absence of a DMA problem, and the last line received over USB does not
prove the CPU stopped there. As of this recorded status, no explicit media-read probe, working keyboard/trackpad,
accelerated desktop, audio or installed Omarchy are established by this record.

Later storage-read results are recorded separately in [a draft power-domain record at b73349f](https://github.com/Kb2uka/Omarchy-M4-Native/blob/b73349f3478522b45cd916bde42c5cc19d663bcb/docs/j713-nvme-power-domain.md). Track revisions and review status in [PR #6](https://github.com/Kb2uka/Omarchy-M4-Native/pull/6).
