# J715 protected speakers: qualification and default audio

The 15-inch M4 Air (J715, Mac16,13) plays its six built-in speakers through the
protected path after an ordinary reboot. This was observed on one owner's Air with
speaker ID 3, kernel `7.1.9-m4-gpu-backport-20261008` (#19), WirePlumber 0.5.18 and
the native build pinned by [the native port](j715-native-port.md). Speaker ID 0,
headphones and the jack, and long sustained playback are not qualified.

## Hardware observations

All replays used the private PipeWire graph, the protected sink
`audio_effect.j715-protected`, the unchanged protection daemon and its leases.

| Replay | Stream volume | Stimulus | DAC peak per lane | Heard |
| --- | --- | --- | --- | --- |
| 6, 7 | 0.10 | `device-added.oga` (0.22 s, -10.9 dBFS) | not recorded | no |
| 8 | 0.10 | `audio-test-signal.oga` (1.41 s, -17.6 dBFS) | about -52 dBFS | no |
| 9 | 1.0 | `audio-test-signal.oga` | -31.6 to -34.9 dBFS | yes |

In replays 7 to 9, return-value tracing showed TX period interrupts, `j713_transport_running`
returning 0 once both directions ran, and `audio_active` moving the amplifiers to ACTIVE.
After each replay the group stopped LOW with no owner. In the earlier silent replays the
path was working. The level was too low to hear: at 10% stream volume and graph
gain 0.336, the DAC peak was about -52 dBFS, about 0.2 mW into each speaker at the
daemon's 21 dBV amplifier gain. An early reading that TX never ran was wrong: it
counted interrupts and missed about a dozen TX periods hidden among the feedback
periods. No driver, transport, routing, calibration or protection change was needed.

Replay 8's monitor recording showed only lanes 0 and 1 because the recorder used a
two-channel map. An offline replay of the same graph on a null AUX0-5 sink, recorded
with an AUX map and `stream.dont-remix`, carried all six lanes at the levels above.

After qualification, an ordinary reboot started registration and protection
(both groups reached nominal gain and readiness was published). WirePlumber started after
readiness, and the protected sink was the only and default output. The owner heard
normal desktop playback from the built-in speakers.

## Enabling on a qualified owner unit

Start from a verified first-test manifest. Record the owner's evidence as JSON with
`run`, `result_sha256`, `owner_confirmation` and `scope`, then as root:

```sh
python3 -B /usr/lib/omarchy-m4/j715/audio/m4-j715-audio-qualify.py --evidence evidence.json
systemctl enable m4-j715-audio-register.service speakersafetyd-j715.service
install -Dm644 /usr/lib/omarchy-m4/j715/audio/session/80-j715-audio-ready.conf \
  /usr/lib/systemd/user/wireplumber.service.d/80-j715-audio-ready.conf
```

`m4-j715-audio-qualify.py` reads a fresh speaker-ID report and writes
`qualification.json`, bound to the payload identity and exact profile receipt. It
holds the registration lock, installs the pinned profile members (WirePlumber policy,
default-node hook, graph, FIRs and UCM) at their system paths and verifies them, and only
then replaces the first-test authorization in the manifest. Identical files are kept.
Any other existing file refuses before anything changes, and a later failure restores
the manifest and removes created files. An interrupted run leaves the manifest in the
first-test form, so it can simply be rerun.
It does not change units, modules, protection, calibration or gains.

The WirePlumber drop-in runs the session gate with `--wait 60`. The user manager
starts about four seconds after boot, so WirePlumber can start before the speaker
card registers and well before protection publishes readiness about 15 seconds later. Without the wait the gate
either refuses repeatedly until WirePlumber reaches its start limit, or admits the
session before the card exists. The wait returns as soon as readiness is published,
a hold exists, registration or protection has failed, or registration is not enabled.
It grants nothing: the unchanged gate then decides admission. The owner unit ran the
equivalent shell form of this wait.

## Rollback

To stop native audio without removing files, create the hold and reboot:

```sh
sudo mkdir -p /var/lib/omarchy-m4/j715/audio
sudo touch /var/lib/omarchy-m4/j715/audio/hold
```

With a hold, registration refuses and the session gate admits USB, Bluetooth and
other audio as before. To undo the change completely, disable both units, restore
the pre-qualification `manifest.json`, remove `qualification.json`, the installed
profile members and the drop-in, then reboot. After a protection failure, recovery
is a reboot, as on [the J713](j713-protection-startup.md).

## Offline checks

```sh
cd tests
python3 -B -m unittest discover -s . -p "test_*j715*.py"
```

Export `TMPDIR`, `NODE_COMPILE_CACHE`, `BUN_RUNTIME_TRANSPILER_CACHE_PATH`,
`PYTHONDONTWRITEBYTECODE=1` and `GIT_MASTER=1`. Fixture setup needs the pinned donor
checkout next to this repository. These synthetic fixtures check contracts, refusals
and rollback; they are not hardware evidence.
