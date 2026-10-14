# J713 headphone jack

## Symptom

Nothing plays through the 3.5 mm jack. Linux has no card for it: the J713
devicetree has no CS42L84 node, and the protected audio stack drives only the
speakers (MCA cluster 0). The [gap analysis](t8132-gap-analysis.md) expected
headphone playback to wait for an `iop-audio,controller` (AOP) transport.

## What made it work

The jack does not need the AOP transport. The CS42L84 codec on i2c1, its I2S
port and the MCA cluster that feeds it are all reachable from Linux, the same
way the speaker stack reaches cluster 0. What was missing was the exact stream
macOS runs, and the AOP firmware describes it. Its codec-out records (`mck2`,
`ckg2`, `syn2`, `ms02`) give MCA cluster 2, a 3,072,000 Hz clock from a source
named `anco`, divider 1, a 64-bit frame and I2S port 2. Two findings in those
records turned noise into clean stereo:

1. **The right clock was already running.** The PMGR clock mux for cluster 2
   (`0x388240040`, `mck2`) has a 4-bit source select (bits 27:24). Source 10
   is an NCO that already runs at 3.072 MHz when Linux is up; Linux never
   programs it. Pointing `mck2` at it gives the bit clock macOS uses
   (48 kHz × 64), and the stock `snd-soc-cs42l84` driver has a PLL row for
   exactly that rate. Every earlier bring-up variant, on a 12 MHz clock with
   a 250-bit frame, buzzed and crackled.
2. **One bit garbled the left channel.** The speaker's sync generator sets
   bit 9 (frame invert) of `SYNC_GEN_CONFIG`. The codec stream does not. With
   the speaker's value the right channel played cleanly and the left was barely
   intelligible; with bit 9 clear, both channels are clean and in the right
   place.

The rest is the codec stream as macOS configures it:

| Part | Setting |
| --- | --- |
| Clock | `mck2` source 10 (3.072 MHz NCO); cluster 2 clock generator `0x100` (divisor 1) |
| Frame | sync generator `0x003` (bit 9 clear); high/low 31/31: a 64-bit, 50 % frame |
| Serializer | TX 2: two 32-bit slots, MSB first, bit start 1, normal clock edge |
| Routing | `SYNC_OMUX_SEL(2)` = 3, I2S port 2 (CS42L84 on i2c1, address `0x4b`) |
| DMA | base-ns ADMAC channel 4 (the same controller as the speakers) |
| Format | 48 kHz, S32_LE, 2 channels; the volume stops at 0 dB |

## Change

[`audio/j713/headphones/`](../audio/j713/headphones/README.md) adds a second
card, `J713Headphones`, beside the protected speaker card, which keeps running
unchanged:

- `j713_hp_dt.ko` applies an overlay once per boot: the CS42L84 on i2c1, the
  `AUDIO_MCA2_M` power domain and the card node. Its phandles are looked up
  by path, because the speaker overlays create some of the target nodes.
- `snd-soc-j713-headphones.ko` is the card and CPU DAI driver for cluster 2.
  Every register value is in one header with no MMIO,
  [`j713_hp_plan.h`](../audio/j713/headphones/kernel/j713_hp_plan.h), and a
  host test checks every step of it.
- The stock `snd-soc-cs42l84` driver binds to the new node. Jack detection
  comes from the codec.
- UCM `J713Headphones` exposes a `Headphones` device with `JackControl
  "Headphone Jack"`. WirePlumber names the sink "Headphones". A hook makes
  it the default output while a headset is plugged in, even over a sink the
  owner picked. On unplug the route becomes unavailable, and selection falls
  back as before: to the owner's choice or to the protected speakers.

**Sharing the MCA with the speakers.** The global block at `0x393600000` is
shared with `snd-soc-apple-mca`. The headphone driver writes only:

- `SYNC_OMUX_SEL(2)` (`+0x108`), in prepare;
- bit 2 of the staged enable registers (`+0x140`/`+0x144` and
  `+0x160`/`+0x164`), plus the aligned trigger (`+0x170`), in trigger.

It reads `TX_ACTIVE` (`+0x168`). It never touches `+0x100` (the speaker's
`SYNC_OMUX_SEL(0)`), any other bit, or the RX enables. Timing registers are
written only while cluster 2's clocks are off and `TX_ACTIVE` bit 2 is clear.
`kernel/test/plan_test.c` enforces this contract.

**Package.** `omarchy-m4-headphones` arrives with `omarchy-m4-update`, like
every M4 package (`omarchy-m4-boot` 1-7 depends on it). It installs:

- the two pinned modules under `/usr/lib/omarchy-m4/j713-headphones/`;
- `m4-headphones`;
- a drop-in, `ExecStartPost=-/usr/bin/m4-headphones --boot`, on
  `m4-j713-audio-register.service`;
- the UCM and WirePlumber files.

`m4-headphones` loads the modules only after the speaker registration has
succeeded, and only the pinned files on the pinned kernel and board. A refusal
or failure never fails the registration. The Air has no boot menu, so the
tool also writes an attempt marker durably before it loads anything. If a boot
dies during the load, the next boot loads nothing and says so in the journal;
`sudo m4-headphones --start` retries in the running session.
`sudo m4-headphones --disable` is the off switch: it writes
`/var/lib/omarchy-m4/headphones/hold`, and nothing loads at boot until
`sudo m4-headphones --enable`. Five package
paths fall under protected prefixes. Each is an exact `protected_exceptions`
entry in `channel.json`: the drop-in, the two UCM files, the WirePlumber
fragment and its hook. None of them replaces or edits a speaker file.

## Evidence

All on one J713 (MacBook Air 13-inch M4), kernel
`7.1.9-m4-gpu-backport-20261008`, with the protected speaker stack running.
"Silent" checks played zeros at a low DAC level and read the stream position
(`hw_ptr`); 48,000 frames/s means the bit clock and frame are right.

| Check | Result |
| --- | --- |
| Rate, single silent checks | 47,950.4 to 48,002.6 frames/s |
| Recipe registers against the plan (debugfs, while playing) | 0 mismatches |
| Speaker's `SYNC_OMUX_SEL(0)` (`+0x100`) | 1 throughout |
| 50 start/stop cycles (2 s each) with the speaker TX running | 50/50; 47,988 to 48,007 frames/s; `TX_ACTIVE` `0x5` mid-run |
| 5 bind/unbind rounds with PipeWire running | 47,998 to 48,017 frames/s; clocks and power domain back to their boot values after each |
| 10 min through PipeWire: headphones play while the speaker sink opens and suspends | 80 speaker opens, 79 closes, 0 headphone stalls, 0 kernel warnings |
| Speaker health after every run (registration, protection, speaker PCM running) | passed |
| Listening, by the owner | clean stereo, left and right correct. Plug in switches to Headphones; unplug returns to the speakers; replugging works every time. |
| Package `omarchy-m4-headphones` 1-1, installed with pacman, then a power cycle | `m4-headphones --boot` loaded both modules after the registration; card up on the stock codec; 47,970.2 frames/s with 0 mismatches mid-run; `+0x100` = 1; speaker checks passed. Plugged in, the sink is `alsa_output.j713.Headphones` and the default while the configured default stays the protected sink. The owner's plug, play, unplug and replug pass: no dropouts. |

The two modules rebuild byte for byte from this source with the pinned kernel
inputs and the toolchain that reproduces the five qualified speaker modules
(see [Building](../audio/j713/headphones/README.md#building)):

| Module | SHA256 | GNU build ID |
| --- | --- | --- |
| `j713_hp_dt.ko` (10,656 bytes) | `9ba22f0eb586052606eb6e9d98fa9d05663c12e3197c1b41c4bdc833afe41f07` | `ac2e4f3c6e14e09e428b8764033e50ff08a909c4` |
| `snd-soc-j713-headphones.ko` (37,016 bytes) | `a73ef5655d453c1ebd424922b0b6141a77159c01cbe718d4c972be61ec5be87d` | `ef4e03b375177271a5c6b39d83919e797c6e4815` |

The modules loaded on the test Air have these hashes and build IDs.

## Existing installs

On an Air connected to M4 updates, run the update and restart once:

```bash
omarchy-m4-update
```

Then, with a headset plugged in, "Headphones" is the default output. Check with
`m4-headphones` (inspection only) and `wpctl status`.

**Switch it off:** `sudo m4-headphones --disable`, then restart. The package
stays installed and current, and the overlay stays applied until the restart.
`sudo m4-headphones --enable` and a restart switch it back on. The speakers are
not affected either way.

## Known issue

Once, during a restart of the owner's PipeWire with the speaker PCM open, the
headphone stream stopped advancing: `aplay` ended with an I/O error. At the same
moment the kernel logged `apple-admac 393800000.dma-controller: clearing unknown
global interrupt flag: 1`. The speakers were unaffected, and the next open
played normally. It did not happen again in 95 later speaker-close events,
79 of them in the 10-minute run above. The cause is unknown: the pinned
`admac.c` only acknowledges and logs that flag. Whether PipeWire recovers from
such a stall by itself is untested.

## Limits

- **Playback only.** The headset microphone needs capture on ADMAC channel 5.
  The pinned `admac.c` sends every split-channel RX through
  `j713_admac_rx_configure()`, which only accepts the speaker feedback channel
  (channel 1). The AOP's record for the jack mic: RX on cluster 2, DIN mask
  `0x4`, 32-bit, slot mask `0x1`, `BCLK_POL` set, the same clock and frame.
- **The NCO is used, not programmed.** The NCO block's address is not known,
  so the driver depends on source 10 already running. If it ever were not,
  `mck2` would not switch, and the driver refuses (`mck2: still pending`)
  instead of playing at a wrong rate.
- **One untested corner of the shared enables.** The staged enable registers
  were shown not to drop a request on a zero write. A register that ignores
  zero writes but replaces the staged mask on a non-zero one cannot be ruled
  out without writing a bit outside cluster 2, which this work did not do. The
  two drivers' start/stop sequences were never observed losing a request
  (the 50 cycles and the 10-minute run above).
- One machine, 48 kHz only. Suspend/resume and other M4 models are not
  qualified. A kernel other than the pinned release needs rebuilt modules; the
  tool refuses to load these modules on any other kernel.
