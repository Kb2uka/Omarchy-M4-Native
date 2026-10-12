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

The component fragment uses native SPA-JSON assignment syntax and the unquoted
dependency `requires = [ policy.default-nodes ]`, matching the installed
WirePlumber 0.5.18 configuration. The initial JSON-form candidate passed the Lua
fixture but failed native component loading: the resolver treated the quote
marks in `["policy.default-nodes"]` as part of the feature name and exited with
CONFIG status78. `policy.default-nodes` itself is a real stock virtual component.
The Lua algorithm and its event ordering did not change in this correction.

`qualified.json` pins both source files. The assembler includes them together in
a new 29-file integration candidate; all 27 previous payload hashes and modes,
module notes, ABI inputs, service links and protection settings remain unchanged.
The candidate is a different package from the previously qualified 27-file
runtime. Its source receipt identifies the default-policy addition without
claiming native qualification.

For a cloned pending-owner image, `configure-m4-audio.py` accepts the explicit
`--previous-package-sha256` option alongside the new `--package-sha256` pin. The
installed old manifest must match its pin. The transition permits only these
two added files and a source-receipt update; existing payload metadata and every
other package field must remain exact. Ordinary apply/check still refuse changed
existing manifests. Customized installed files remain a conflict even with the
transition option. The manifest is replaced after both policy files, and a retry
accepts already installed exact additions. This option remains offline-only and
requires the usual board, DTB, kernel, ABI and pending-owner checks.

## Validation

On a leased Linux test host with Lua 5.4 available:

```sh
LUA=/path/to/lua python3 -m unittest discover -s tests -p 'test_j713_audio_default.py' -v
python3 tests/j713_audio_default_mutants.py --lua /path/to/lua
python3 -m unittest discover -s tests -p 'test_j713_audio_package_join.py' -v
python3 tests/j713_audio_package_join_mutants.py
python3 tests/check_j713_audio_config_load.py --runtime-root COPIED_RUNTIME \
  --qemu /usr/bin/qemu-aarch64 --output NEW_PRIVATE_TEST_DIRECTORY
```

The fixture executes unchanged WirePlumber 0.5.18 collection, selected/stored
preference, ranking and metadata-apply hooks. PipeWire objects, JSON and device
route availability are substituted; no PCM is opened. It covers clean owners,
existing choices/history, external ranking, unavailable preferences, absent
filters/ports/routes, legacy raw choices and unrelated names/categories. The
baseline without the new hook fails the clean-owner and raw-fallback assertions.

The separate configuration integration check runs actual WirePlumber 0.5.18,
its stock configuration and script engine against a private PipeWire socket.
Supply the installed runtime layout under `usr/`, including its binaries,
libraries, modules and data; omit `--qemu` on a matching native architecture.
Run only on a leased fleet host in an isolated container. Hardware discovery is
disabled. The quoted-dependency control must reproduce exit78, while the shipped
fragment must keep the real manager running and register the real hook. This
checks configuration resolution and hook loading; the separate Lua scenarios
check selection policy, and native testing still checks actual devices/defaults.

Actual WirePlumber component loading and a controlled fresh-selection check on
the Air are still required before claiming a native durable fix. The current
owner's successful playback must not be interrupted merely to run this test.
An installed manifest change also invalidates the digest in the readiness
record. With playback stopped and TX closed, re-run the existing root startup
qualification against the new manifest and publish a new record before starting
WirePlumber. Preserve the running protection daemon and its lease; do not unload
modules or restart protection to install a routing policy. A failed qualification
must leave WirePlumber stopped until the exact previous package and a fresh
previous-manifest attestation are restored or the failure is resolved.
