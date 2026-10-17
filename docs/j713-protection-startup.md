# J713 speaker protection at startup

## Symptom

After some boots the Air has no speakers. Volume controls do nothing, and
PipeWire offers only "Dummy Output". The protection daemon logs:

```text
thread 'main' panicked at src/j713.rs:77:9:
J713 backlog before renewal
```

Protection fails closed by design and does not restart (see
[readiness](j713-audio-readiness.md)), so audio stays off until a reboot. The
same daemon can also panic later in a session, while something is playing,
with `J713 stale capture backlog/read`
([#64](https://github.com/Kb2uka/Omarchy-M4-Native/issues/64)); the cause of
that panic is still open. The recovery below covers both.

## Recovery

Reboot the Air. It is the only supported recovery after a protection panic.

1. Optionally save the log first:
   `journalctl -b -u speakersafetyd-j713 --no-pager > ~/speaker-protection.log`.
2. Reboot. Do not restart `speakersafetyd-j713`, unload or reload the audio
   modules, or switch the card profile to `off` to free the speakers.
3. After the reboot, if `systemctl is-active speakersafetyd-j713` prints
   `active` but PipeWire offers only "Dummy Output", restore the speaker
   profile (no `sudo`):

   ```bash
   pactl set-card-profile alsa_card.platform-sound HiFi
   ```

None of the in-session attempts work on the current release:

- Restarting protection while PipeWire holds the speaker output fails with
  `Unhealthy first-lease initialization` (#64 saw the group still
  `cancelled=1`). The readiness check also requires the output to be closed
  before the first lease, and PipeWire still holds it.
- Once protection has gone, PipeWire's next attempt to start the output fails
  the lease check and sets the transport's `failed` flag
  (`audio/j713/kernel/j713-mca.h`). After that, setting up the output fails with
  `I/O error (5)` and starting it with `Operation not permitted`. The driver
  never clears that flag, so it lasts until the next boot.
- Registration runs once per boot and never reloads a module or overlay, so
  the audio stack cannot be rebuilt in a running session either.
- WirePlumber remembers a card profile across reboots
  (`~/.local/state/wireplumber/default-profile`). Switching the card to `off`
  leaves the next boot with healthy protection but no speakers until `HiFi`
  is restored, which is why step 3 exists.

On the owner's Air (#64) these attempts were followed minutes later by a
system freeze whose cause is unknown.

## Cause

Each 21.3 ms capture period, the daemon models the speakers, applies any gain
change and renews its kernel lease. Renewal requires at most 512 frames
(10.7 ms) queued, so all of that work must finish within 10.7 ms of the read.

During the startup ramp (about 2 s from the warm thermal assumption to
nominal), both speaker groups change gain almost every period. A changed level
is written through `j713_audio_level()`, which used 7 I2C transfers per amp:

1. `amp_write()`: the write, then a read-back of the register.
2. `amp_read()`: a second read-back of the same register.
3. `verify_limits()`: reads of the page, gain, DVC and mode registers. These
   cover the written register a third time.

A traced failing boot (owner's Air, 2026-10-13) had only the daemon on the bus:
1928 transfers in the ramp, median 0.368 ms each, independent of CPU clock
(0.369 ms with the P cluster under `schedutil`, 0.368 ms held at 1800 MHz).
In its last 0.3 s it issued 460 writes and 424 reads across amps 0x38, 0x39,
0x3b and 0x3c (registers 0x00, 0x02, 0x03 and 0x1a). Four amp changes in one
period cost 4 × 7 × 0.368 ≈ 10.3 ms of bus time alone. With the model on top,
that exceeds the window, and whether a boot fails depends on how the ramp
steps line up.

A level read (`get`) holds the same operation lock for its own hardware
reads. Two processes reading every control in a loop (`amixer contents`)
made a normal start fail 5 of 5 times. Each daemon write then waited about
2.5 ms for the lock before its transfers.

## Change

`j713_audio_level()` stores the value with a single unverified write
(`amp_store()`), then runs `verify_limits()`. That reads the page, gain, DVC
and mode registers back against the stored values, so every register,
including the new one, is still checked after every change. If verification
fails, the group is revoked, as before. New in this change: the stored value
also goes back to the old one; the previous code committed the new value
before `verify_limits()` and kept it on failure. A mismatch on the written
register now reports `-EPROTO` (from `verify_limits()`) instead of `-EIO`;
either error revokes the group, and the daemon fails closed on any control
write error. A level change now takes 5 transfers instead of 7, and a read
takes 4 instead of 5. The worst-case period drops to 4 × 5 × 0.368 ≈ 7.4 ms of
bus time.

## Evidence

The unchanged source, built in the pinned container against the pinned kernel
inputs, reproduces the qualified `snd-soc-j713-group.ko` byte for byte
(`465a88aa…`, 52,088 bytes). The fixed module is `aaad802e…`, 52,000 bytes,
GNU build ID `9e792aa0…3c7c014b`, built with `-Werror`.

| Owner's Air, cpufreq devicetree | Boots with protection reaching nominal |
| --- | --- |
| Qualified module | 1 of 4 (three `backlog before renewal` panics) |
| Fixed module | 6 of 6 |

Every fixed boot loaded the new build ID, published `ready.json` and kept
the register service active. The six boots before the cpufreq devicetree
also started cleanly on the qualified module.

## Existing installs

Connected Airs get the fix from the M4 package channel: run
`omarchy-m4-update`, then restart. The `omarchy-m4-speaker` package stages the
module; at the next boot, before any audio module loads, `m4-speaker-module
--boot` (run by a drop-in on `m4-j713-audio-register.service`) writes the
module and then the manifest, changing only the six values that pin
`snd-soc-j713-group.ko`. It never swaps a loaded module, because the audio
readiness gate would fail for the rest of that session.

```bash
m4-speaker-module                  # state: stock, fixed or interrupted; changes nothing
sudo m4-speaker-module --rollback  # restore the alpha.1 module at the next boot
```

The alpha.1 module and manifest are kept in `/var/lib/omarchy-m4/speaker-module/`.
A rollback also sets a hold there, so the fix is not applied again until
`/var/lib/omarchy-m4/speaker-module/hold` is removed.

## Limits

- **Continuous control readers still starve protection.** With two loops
  reading every control without pause, protection still fails at start (0 of 5
  starts survive with either module). With ten CPU-bound loops and no readers,
  the qualified module survived 5 of 5 starts and the fixed one 4 of 5.
  Level reads go to the hardware
  under the same lock the daemon writes through. Serving reads from the stored
  values would remove that, and is a separate protection-stack change.
- **Requalification (2026-10-13, owner's Air):** readiness reached `ready` on
  two consecutive fresh boots with this module, and the owner played music at
  100% volume for about 4.5 minutes: clean, protection active throughout
  (`phase=3`), no faults and no gain limiting needed. A sustained run long
  enough to make the thermal model limit gain has not been recorded. The
  maintainer approved delivery through the package channel.
