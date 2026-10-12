# J713 keyboard and trackpad physical input

On 2026-10-07 the J713/T8132 Air delivered physical keyboard and trackpad
events during a native Linux RAM boot. The operator confirmed normal trackpad
click feedback. This checkpoint preserves the interface power change and its
bounded evidence; it does not qualify desktop gestures, every key combination,
suspend/resume or long-term reliability.

## Failure and change

The matching J713 trackpad firmware uploaded successfully, but the existing
four-byte `0x40` interface power requests failed with `0xe00002c2`.
Opening `Apple MTP multi-touch` returned `Operation timed out`. Keyboard
enumeration and opening already worked; no physical input was attempted in
that baseline.

[Wallace's M4 Pro investigation](https://github.com/damsleth/wallace/blob/55f116dd0e0f28d1412c6be728a101f7d861c473/evidence/2026-08-04-t6040-trackpad-post-upload-reset-contract.md)
documents the same firmware generation accepting a version-2 power request.
The Air test adapts that wire contract: nine packed bytes containing command,
version, interface, state, phase, and a zero little-endian 32-bit status.
For each OFF state `0` and ON state `2`, send phase `0`, then phase `1`.
No M4 Pro register addresses are used.

The accompanying patch is opt-in through
`dockchannel_hid.m4_power_v2=1`, and only selects version 2 when the machine
matches both `apple,j713` and `apple,t8132`. Other cases retain the four-byte
request. The interface startup path now propagates failed OFF/ON commands
instead of ignoring them; this error handling also applies to the legacy
request path. Successful command responses may return a positive byte count.

The patch applies to kernel base
`77cb8f24c2381a8abb7272d7bbdec548d6426a8a`. The hardware-tested kernel also
contains the existing J713 bring-up and boot diagnostics. This patch alone
does not supply the board DTS, MTP reset pins, firmware, or boot dependencies.
See the [MTP reset pins](https://github.com/Kb2uka/Omarchy-M4-Native/pull/2)
and [live TrustCache reservation](https://github.com/Kb2uka/Omarchy-M4-Native/pull/7).

## Firmware provenance

The board DT uses `apple/tpmtfw-j713.bin` for its multi-touch subdevice.
The source is Apple's J713 Multitouch component from macOS 26.5.2 build
25F84, checked against its BuildManifest SHA-384 before conversion with the
Asahi firmware tooling. Its bytes also match the independently read staged
25G83 component. No proprietary firmware is included here.

| Artifact | Size | SHA-256 |
| --- | ---: | --- |
| J713 Multitouch IM4P | 111,535 | `f27e6714b952598ec525581029e33524d235a8e3c8d361fa6a142b5984dce53f` |
| Converted HIDF | 80,524 | `9fcbef0cff6a3b7733a4df626c9eba859b66c8e5053557924b8020a67ef48b17` |

The live MTP coprocessor reports `AppleMTPFirmwareMac-5340.61.4~438`, SDK
`25F63`. Firmware for a different board is not interchangeable.

## Captured hardware evidence

Both version-2 boots reached `Touch MT ready`. The first capture contained
zero physical events, with no confirmation that the operator had acted during
its recording window. It establishes startup only. The second synchronized
capture, starting at `20261007T183615Z`, produced:

```text
[15.880773] M4INPUT v2 power iface=1 state=0 bytes=9 result=9
[16.128887] touch_iface.c:424: Touch MT ready
[16.128956] M4INPUT v2 power iface=1 state=2 bytes=9 result=9

Apple MTP keyboard: 5,472 bytes / 228 Linux input records
abc123: five complete press/release pairs per key
Up, Down, Left, Right: two complete press/release pairs per key

Apple MTP multi-touch: 926,328 bytes / 38,597 input records
Changing X/Y coordinates, pressure and contact slots 0..2
BTN_LEFT: 18 complete press/release pairs
SYN_DROPPED: zero on both devices
Operator confirmation: normal click feedback

CPU_PASS cpu=0 through cpu=9; SMP_MEMORY_PASS
READPROBE_PASS direct 8388608 bytes twice at three offsets; hashes equal
WIFI_SCAN_RETURN=0; BT_UP_RETURN=0
No Oops, BUG, WARNING or kernel panic in captured final dmesg
Watchdog recovery: 126.405 seconds; fresh proxy verified, control=0
```

Raw records use the ARM64 Linux `input_event` layout: two signed 64-bit time
fields, unsigned 16-bit type/code and signed 32-bit value, 24 bytes total.
The retained keyboard capture hashes to
`763c26ae8a6e51fe5ca4442634d6389b2f54f461b6b5d7413ecb6b67a716c5ea`;
the trackpad capture hashes to
`90f5295ad2877bb3703951232e791d2d74cbf3f9d97b1ecd806ff6d5009cd277`.
These observations verify the tested physical keys, contacts and clicks.
Raw two-finger contacts do not by themselves verify desktop scrolling.

| Boot input | SHA-256 |
| --- | --- |
| Kernel Image.gz | `e1a5bd4a73904e0b41c0082a30e49067cb97ffc26c53826398a0bfc57d99aa1c` |
| Kernel configuration | `0dd7fdb75e953363c8db86bdec8425539dcad7c4039eb72bd16257126afdc295` |
| Complete experimental kernel diff | `659df378286b446d788decb83d42cd321a58944812d1dd0ffcf90837312552d5` |
| Capture initramfs | `59ee705dd983d9144e3d449b6d3a6d16761d6a55b215e0f02100f268f2b452c0` |
| r21 DTB before per-boot fixups | `6a216236144b0bbebe1ee137bb542621318bd81472e9b54d7d639a9d4f84a15c` |

`maxcpus=10 idle=nop` remained enabled. The Air SSD was read-only throughout;
no Air filesystem was mounted or changed.

## Reproducible packet check

Apply `patches-kernel/j713-input/dockchannel-power-v2.patch` to an isolated
copy of the pinned kernel, retaining the original driver as `before.c`.
Run this on a fleet test host with a C compiler:

```sh
python3 tests/check_dockchannel_power.py \
  --before before.c \
  --after kernel/drivers/hid/dockchannel-hid/dockchannel-hid.c
```

The checker compiles the actual extracted helper, with a recording transport
stub. The old helper fails the version-2 expectation; the new helper checks
default/board/chip guards, exact OFF/ON packets, positive success returns,
and both transport failure positions. Four mutations of version, phase,
packing and enable guard are rejected. Temporary executable fixtures are
removed. This is a helper contract check, not an end-to-end driver simulator;
hardware evidence above covers the successful real startup and input path.

A later fresh-install READY-timeout recovery defect is addressed separately by
[J713 trackpad startup retry](j713-input-startup-retry.md). The original power
request evidence above is unchanged.
