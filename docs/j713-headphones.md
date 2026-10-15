# J713 headphone jack

## Symptom

Nothing plays through the 3.5 mm jack. Linux has no card for it: the J713
devicetree has no CS42L84 node, and the protected audio stack drives only the
speakers (MCA cluster 0). The [gap analysis](t8132-gap-analysis.md) expected
headphone playback to wait for an `iop-audio,controller` (AOP) transport.
Once playback worked (`omarchy-m4-headphones` 1-1), a headset's microphone
still had no capture device.

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
- `snd-soc-j713-headphones.ko` is the card and CPU DAI driver for cluster 2:
  playback and, from 1-2, capture from the headset mic. Every register value
  and every rule that lets the two directions share the cluster is in one
  header with no MMIO,
  [`j713_hp_plan.h`](../audio/j713/headphones/kernel/j713_hp_plan.h), and a
  host test checks every step, every staging and every prepare decision of it.
- The stock `snd-soc-cs42l84` driver binds to the new node. Jack detection
  comes from the codec.
- UCM `J713Headphones` exposes a `Headphones` device with `JackControl
  "Headphone Jack"`. WirePlumber names the sink "Headphones". A hook makes
  it the default output while a headset is plugged in, even over a sink the
  owner picked. On unplug the route becomes unavailable, and selection falls
  back as before: to the owner's choice or to the protected speakers.
- From 1-2, UCM also exposes a `Headset` device with `JackControl "Headset
  Mic Jack"`, WirePlumber names the source "Headset Microphone", and the same
  hook makes it the default input while a 4-pole headset is plugged in. A
  3-pole plug never turns the mic route on, so the input stays as it was.

**Sharing the MCA with the speakers.** The global block at `0x393600000` is
shared with `snd-soc-apple-mca`. The headphone driver writes only:

- `SYNC_OMUX_SEL(2)` (`+0x108`), in prepare;
- bit 2 of the staged enable registers (`+0x140`/`+0x144` OMUX,
  `+0x150`/`+0x154` RX and `+0x160`/`+0x164` TX), plus the aligned trigger
  (`+0x170`), in trigger.

It reads `TX_ACTIVE` (`+0x168`) and `RX_ACTIVE` (`+0x158`). It never touches
`+0x100` (the speaker's `SYNC_OMUX_SEL(0)`) or any other bit; the speaker
stages bit 0 of the same registers, for its playback and for its IV-sense
feedback. Timing registers are written only while cluster 2's clocks are off
and bit 2 of both status registers is clear. The one exception is the
serializer reset before a start, which toggles the serializer's sync select
with the clocks running, as the speaker driver does
([Two directions on one cluster](#two-directions-on-one-cluster)).
`kernel/test/plan_test.c` enforces this contract.

**Package.** `omarchy-m4-headphones` arrives with `omarchy-m4-update`, like
every M4 package (`omarchy-m4-boot` 1-7 depends on it). Version 1-2 adds the
headset mic to the same package. It installs:

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

## Headset microphone

### What made the mic work

The AOP firmware's record for the jack mic (`cirx`) describes the capture
side of the same stream: RX on cluster 2, DIN mask `0x4` (I2S port 2), 32-bit,
slot mask `0x1`, `BCLK_POL` set, on the clock and frame that playback already
runs. The speaker stack's own builder for its RX serializer,
`m4_t8132_build_rx()`, turns that record into the six writes for RX
serializer 2 (`+0x4200`). The DMA adapter's RX half needs no packing or pad.
ADMAC channel 5, the RX channel next to the headphones' channel 4, carries
the samples.

| Part | Setting |
| --- | --- |
| Serializer | RX 2: one 32-bit slot, bit delay 1, `BCLK_POL` set, MSB first: conf `0x0003b500`, slot masks `~0`/`~1` |
| Routing | I2S port 2 in (sources `0x4`), sync generator 2: the playback frame |
| DMA adapter 2 | RX field 0 (no packing, no pad), FIFO enable bit 4 |
| DMA | base-ns ADMAC channel 5: `BUS_WIDTH` low byte `0x02` (32-bit words, one-word frame), `FIFOCTL` `0x180` (the speaker feedback channel's value) |
| Format | 48 kHz, S32_LE, 1 channel, 24 significant bits (the low byte is 0) |
| Codec | stock `snd-soc-cs42l84`: `SDOUT1` from the ADC, preamp 0 dB, PGA as the volume, high-pass filter on |

### Channel 5 without changing apple-admac

The pinned `admac.c` sends every split-channel RX configuration to
`j713_admac_rx_configure()`, which accepts only the speaker's feedback channel
(channel 1). For channel 5 it refuses before it touches anything, so
`dmaengine_slave_config()` would fail the capture's `hw_params`. `apple-admac`
is one of the five qualified speaker modules; changing it would mean
requalifying the speaker stack. Instead the card driver:

- claims channel 5 through the devicetree (`dma-names = "tx", "rx"`) and
  refuses to probe unless that channel is number 5, the one whose registers
  it writes;
- skips `dmaengine_slave_config()` for capture;
- writes channel 5's `BUS_WIDTH` (bits 31:8 kept) and `FIFOCTL` itself in
  every capture prepare, where ALSA has stopped and synchronized the stream,
  and reads both back; a mismatch fails the prepare.

`apple-admac` never writes those two words for channel 5. Everything else on
the channel (SRAM carveout, `CONFIG`, `BURST`, the enables, rings and
interrupts) is its generic path, which already runs the speaker feedback.

### Two directions on one cluster

Playback and capture share the clocks, the frame, port 2 and `OMUX` 2. The
driver's callbacks run under one lock, and the rules are pure functions in
`j713_hp_plan.h` that `plan_test` checks exhaustively:

- `OMUX` 2 is set by whichever direction starts first and cleared by the last
  to stop. Each direction stages only its own enable.
- A prepare reprograms the cluster only while the other direction is neither
  running nor prepared on a clocked cluster. Otherwise it verifies the recipe
  instead, and refuses (`-EBUSY`) if a register differs.
- Each prepare resets its serializer, as the speaker driver does before every
  start, with one exception: a capture started while playback runs skips the
  RX reset.
- Closing one direction while the other is open never tears the cluster
  down.

The test runs (below) covered a TX serializer reset while capture runs (runs
B and C1: 30 playback starts during a 40 s capture) and a capture started
during playback without an RX reset (runs A and C2: 30 captures during
playback). An RX reset while TX runs was never tested, and the driver never
does it.

### Gain

The UCM device sets the preamp to 0 dB (of 0, +10 and +20 dB) and turns the
high-pass filter on. The PGA (-12 to 0 dB in 0.5 dB steps) is the capture
volume, so 100 % in PipeWire is 0 dB. With the +10 dB preamp, loud speech
clipped (183 full-scale samples in an 8-second talk test). At 0 dB, a video
call's automatic gain settled the PGA at -3.5 dB, which leaves headroom.

### Mic evidence (test build)

On the same J713 and kernel, with the protected speaker stack running, using
a test build of `snd-soc-j713-headphones` in place of the 1-1 module: the
same register recipe, plus test-only module parameters and a DMA channel
filter. For the last two rows, the Air's UCM had the `Headset` device of this
package and a WirePlumber rule gave the source the same name; the
default-input hook was not part of the test.

| Check | Result |
| --- | --- |
| Capture alone, 3 s, silent | 48,000 frames/s, no xrun, 0 recipe mismatches; `RX_ACTIVE` `0x5` (the speaker's bit 0 kept); channel 5 `BUS_WIDTH` `0xf02`, `FIFOCTL` `0x180`; back to the speaker-only values after the stop |
| Data | 24-bit samples in 32-bit words (low byte 0; bits 8 to 12 toggle half the time, so the alignment is exact); 0 spikes; about 21 ms of zeros at the start when playback is idle |
| Both start orders (runs A and B) | `TX_ACTIVE` and `RX_ACTIVE` bit 2 follow each stream; `OMUX` bit 2 stays on until the last stops; 48 kHz both ways; 0 mismatches; teardown back to the speaker-only values; no spikes in the capture at playback start or stop |
| 30 playback bursts during a 40 s capture (C1); 30 captures during 40 s of playback (C2) | 0 failures, 0 spikes, every capture full length; kernel log clean; speaker protection healthy |
| Sound settings | "Headset Microphone" is the default input (chosen by WirePlumber itself), 100 % is 0 dB, and PipeWire records real data |
| Video call, by the owner | Google Meet, from the Air's headset to a phone, both directions: clean |

### Mic evidence (1-2 package)

The package 1-2 built from this branch (modules `3f501c61…` and `2e743937…`,
`pacman -Qkk` 0 altered files) was installed with `pacman -U` on the same Air,
followed by a power cycle. It differs from the test build in these ways: no
module parameters (the tested values are built in), channel 5 claimed through
the devicetree, its two words written in every capture prepare instead of in
`hw_params`, `FIFOCTL` written as one word instead of two halves (the same final
value), the two-direction rules moved into the plan header, and the
default-input hook.

| Check | Result |
| --- | --- |
| Upgrade from 1-1 | `post_upgrade` printed the restart line; after the power cycle `m4-headphones --boot` loaded both modules (`state` `loaded`, `status` `current`); the card has a playback and a capture PCM |
| Sound settings | "Headphones" is the default output and "Headset Microphone" the default input |
| Both start orders (runs A and B) | Same as the test build: bit 2 of `TX_ACTIVE` and `RX_ACTIVE` follows each stream, `OMUX` bit 2 stays on until the last stops, 0 recipe and 0 channel 5 mismatches, teardown back to the speaker-only values |
| 30 playback bursts during a 40 s capture (C1); 30 captures during 40 s of playback (C2) | 0 failures, every capture full length, 0 spikes in about 54 s of capture; kernel log clean; speaker protection healthy |
| Owner checks | Music playback, a Google Meet call to a phone in both directions, and repeated unplug and replug during playback and during the call: all clean; output and input follow the plug |
| Logs afterwards | No new kernel warnings, no PipeWire or WirePlumber warnings, no speaker protection warnings |

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

The playback checks above ran on the 1-1 modules (`9ba22f0e…` and
`a73ef565…`, the hashes and build IDs loaded on the test Air). The 1-2
modules rebuild byte for byte from this source, in two separate builds, with
the pinned kernel inputs and the toolchain that reproduces the five qualified
speaker modules (see [Building](../audio/j713/headphones/README.md#building)):

| Module | SHA256 | GNU build ID |
| --- | --- | --- |
| `j713_hp_dt.ko` (10,656 bytes) | `3f501c6193aeddc62031395e899909bf352851278f829458998ca1c4dbbe6149` | `5f1e07754e61d2784388a0fbe6838ec9fad80018` |
| `snd-soc-j713-headphones.ko` (48,160 bytes) | `2e743937b4bd8315ec6dba6bef4bbfbf197d4bf6f8688761954579192df48244` | `acb0160796d6a2a07a7a799dd6162f328923f4ba` |

## Existing installs

On an Air connected to M4 updates, run the update and restart once:

```bash
omarchy-m4-update
```

An upgrade from 1-1 also says to restart once; until then the 1-1 card, playback
only, stays loaded. Then, with a headset plugged in, "Headphones" is the default
output, and with a 4-pole headset "Headset Microphone" the default input. Check
with `m4-headphones` (inspection only) and `wpctl status`.

**Switch it off:** `sudo m4-headphones --disable`, then restart. The package
stays installed and current, and the overlay stays applied until the restart.
`sudo m4-headphones --enable` and a restart switch it back on. The switch covers
the headset mic too. The speakers are not affected either way.

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

- **Headset mic format.** Mono, 48 kHz, S32_LE with 24 significant bits.
- **Headset mic gain.** The preamp is fixed at 0 dB, so the most analog gain is
  0 dB. Every enable of the UCM device sets it again, so an `alsamixer` change
  does not stick. A recorder without automatic gain was not measured at 0 dB
  (the talk test used +10 dB), so a quiet voice may need software gain. The
  wind-noise filter and the high-pass corner stay at the codec's defaults.
- **Headset mic noise.** A tone at about 129 Hz, with smaller peaks up to about
  1.6 kHz, shows in silence. Its source (the room or electrical) is unknown; it
  was not compared with macOS.
- **Headset mic start.** The first ~21-24 ms of a capture are zeros (the
  codec's ADC starting up). With the 1-2 driver this also happened for a
  capture started during playback, which skips the RX serializer reset
  ([Two directions on one cluster](#two-directions-on-one-cluster)).
- **The NCO is used, not programmed.** The NCO block's address is not known,
  so the driver depends on source 10 already running. If it ever were not,
  `mck2` would not switch, and the driver refuses (`mck2: still pending`)
  instead of playing at a wrong rate.
- **One untested corner of the shared enables.** The staged enable registers
  were shown not to drop a request on a zero write. A register that ignores
  zero writes but replaces the staged mask on a non-zero one cannot be ruled
  out without writing a bit outside cluster 2, which this work did not do.
  Under that behavior, a headphone write landing between the speaker's
  SET/CLR write and its trigger could drop the speaker's request. The two
  drivers' start/stop sequences were never observed losing a request (the
  50 cycles and the 10-minute run above). The headset mic extends this corner
  to the RX enables: the mic stages bit 2 of `RX_ENABLE_SET`/`_CLR`, and the
  speaker stages bit 0 of the same registers when its IV-sense feedback starts
  or stops. The mic tests did not cover that window: the speaker's feedback
  ran throughout them.
- One machine, 48 kHz only. Suspend/resume (playback and capture) and other M4
  models are not qualified. A kernel other than the pinned release needs
  rebuilt modules; the tool refuses to load these modules on any other kernel.
