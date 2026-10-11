# J713 protected speaker default

On a clean owner session the protected speaker filter can exist while the
ordinary default output remains unusable. Explicitly choosing
`audio_effect.j713-protected` restored volume controls and audible playback on
the fresh J713 installation. That repair must not depend on distributing an
existing owner's WirePlumber state.

WirePlumber 0.5.18's software-DSP hook hides `alsa_output.j713.RawSpeakers` from
ordinary clients but retains its own access. Its default-node rescan can still
include that raw parent. The protected graph has no session priority, while the
raw ALSA node can win the normal ranking. The regression reproduces this
mechanism using the actual upstream hooks and synthetic priority values; the
original live raw priority/default metadata was not captured before repair.

## Policy

The two new files below form one unit and must be included together in the
reviewed audio package and its file/hash manifest:

- `audio/j713/policy/etc/wireplumber/wireplumber.conf.d/82-j713-protected-default.conf`
  installs at `/etc/wireplumber/wireplumber.conf.d/82-j713-protected-default.conf`.
- `audio/j713/policy/usr/share/wireplumber/scripts/default-nodes/j713-protected.lua`
  installs at `/usr/share/wireplumber/scripts/default-nodes/j713-protected.lua`.

The required component runs before upstream default selection, removing only the
exact J713 raw `Audio/Sink` from audio-sink candidates. Existing configured and
stored choices keep their normal precedence. When only the protected speakers
are available they become the normal default. Available USB, Bluetooth and other
outputs retain upstream ranking and owner preference behavior. A disconnected
preferred output can fall back normally without overwriting its saved choice.

The hook does not select the raw node, fabricate a sink, write user defaults,
unmute audio, change volume or run a timer. If the protected graph is missing or
not yet eligible, it supplies no raw-speaker fallback. Legacy saved selections
of the hidden J713 raw parent are ineligible too. Other boards and capture/video
defaults are unchanged. The existing protection-readiness gate and raw-node
permission hiding remain necessary; this default policy is not an access-control
or speaker-protection replacement.

`qualified.json` and the previously qualified 27-file runtime are unchanged by
this source-only addition. The assembler will not include these files until the
installer integration refreshes its exact qualified input and runtime manifest.
Do not append untracked files to an installed manifest and describe it as the
previously qualified package.

## Validation

On a leased Linux test host with Lua 5.4 available:

```sh
LUA=/path/to/lua python3 -m unittest discover -s tests -p 'test_j713_audio_default.py' -v
python3 tests/j713_audio_default_mutants.py --lua /path/to/lua
```

The fixture executes unchanged WirePlumber 0.5.18 collection, selected/stored
preference, ranking and metadata-apply hooks. PipeWire objects, JSON and device
route availability are substituted; no PCM is opened. It covers clean owners,
existing choices/history, external ranking, unavailable preferences, absent
filters/ports/routes, legacy raw choices and unrelated names/categories. The
baseline without the new hook fails the clean-owner and raw-fallback assertions.

Actual WirePlumber component loading and a controlled fresh-selection check on
the Air are still required before claiming a native durable fix. The current
owner's successful playback must not be interrupted merely to run this test.
