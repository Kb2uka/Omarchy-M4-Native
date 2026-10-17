# Owner-local J713 audio filters

This development tool reproduces the four J713 FIR files from installed
macOS AudioDSP and AID37 tuning inputs. Installation uses the
[static approved profile](../filters/README.md) instead. The generator, native
helpers and NumPy are no longer installer dependencies. A local reproduction
bundle must not contain or publish Apple presets, tuning strips, AudioDSP or
raw render captures.

```sh
/path/to/bundled/python3.13 tools/m4-j713-fir-owner.py --output /absolute/new/owner-firs
```

The output directory must not exist. Its parent must exist. The command accepts
`--bundle /absolute/owner-fir` for a different reproduction-bundle layout. With
no override, the bundle is `audio/j713/owner-fir` relative to the repository root
containing `tools/m4-j713-fir-owner.py`.

The command reads installed system files, creates temporary private files, and
renders AudioUnit responses into memory. It does not start an audio output unit,
access speaker hardware, change a volume setting, mount a disk, or invoke an
installer. It uses no network connection and does not require Xcode on the
reproduction host. The native helpers are built in advance.

All input, extracted-preset, helper, dependency and output hashes are pinned in
`manifest.json`. Generation requires native arm64 macOS and the reproduction environment's
CPython 3.13.9 without optimization. Each installed system input lists its
qualified builds under `qualified`; the input must match exactly one entry by
size and hash. AudioDSP is qualified for macOS 26.5.1 (25F80) and 26.5.2 (25F84),
and separately for 26.6.2 (25G83), whose different binary rendered the same four
pinned outputs. Every qualified input must produce the single pinned output set.
A different input or output is a refusal, not permission to use a substitute
filter. No compatibility claim is made for other macOS inputs.

The generator passes the matched AudioDSP hash to `run-transfer.py` and
`run-memory.py` in `owner-inputs.json`, replacing the literal each retained
script originally asserted. Their mathematical operations are unchanged. It
rechecks every input against the same entry after generation and records the
matched entry and macOS version in `receipt.json`.

Success creates exactly four `j713-static-minimal-laneN-48000.wav` files and
`receipt.json`. The receipt describes reproduction, not installation or hardware
qualification. Temporary presets, responses and extracted NumPy files are
removed when the command exits normally or reports an error. The release
builder verifies the committed static filters against these native output pins
and carries their identities into the installed audio manifest.

## Assembly and source

`src/` retains the original seven research source files with MIT headers added;
their mathematical operations are unchanged. `historical-source-pins.json`
records the pre-header source hashes. The pipeline retains native transfer
rendering, high-pass isolation, impulse normalization, finite-tail selection,
NumPy FFT convolution, float32 rounding, and IEEE float WAV serialization.

Build the helpers on native arm64 macOS with Command Line Tools or Xcode:

```sh
bash audio/j713/owner-fir/build-helpers.sh /absolute/new/helper-output
```

Place `measure-memory` and `measure-transfer` in the bundle's `bin/` directory.
The build script records compiler, platform, dynamic dependencies and hashes.
The pinned build used Apple clang 21.0.0, macOS 26.5.2 and a macOS 15.0 deployment
target. Its only dynamic dependencies are the system AudioToolbox,
CoreFoundation and libSystem libraries. A rebuild whose helper hashes differ
requires a reviewed manifest update; no fallback ignores that mismatch.

Place the exact wheel named in `manifest.json` in `dependencies/`:

- CPython 3.13, arm64, macOS 14 or newer; NumPy 2.5.3.
- The wheel URL, byte count and SHA-256 are recorded in `manifest.json`.
- `numpy_source` records the corresponding upstream source archive.
- The wheel is unpacked privately without pip; its bundled license files stay
  intact. A copy of every license entry is retained in `licenses/` as well.

No C compilation or dependency download happens on the reproduction host.

## Validation status and limits

The two helpers were compiled, their Mach-O architecture and dynamic library
dependencies inspected, and their bytes hashed. The dependency wheel and source
archive were downloaded from the pinned PyPI links and their hashes verified.
The build host's installed AID37 inputs and AudioDSP match the retained source
inputs. A second arm64 Mac has the same AudioDSP hash.

The complete owner-generation command succeeded on the J713 Air running macOS
26.5.1 and an arm64 Mac Studio running 26.5.2, using the bundled CPython 3.13.9,
helpers and NumPy 2.5.3. Both produced the exact same four output hashes.
On a 13-inch M4 Air (Mac16,12) running macOS 26.6.2 (25G83), the same bundle
with that build's AudioDSP produced the same four output hashes.

The earlier reference combined native macOS responses using NumPy 2.5.3 on
x86_64 Linux. The ARM64 results differ at 57 of 10,240 float32 samples, with a
maximum absolute difference of 3.553e-15 and a per-filter sum of absolute
differences below 3.584e-15. Tap counts and peak coefficients are unchanged.
The old hashes remain in `reference_outputs`; `outputs` identifies the measured
native results. No numerical tolerance replaces the four exact hash checks.
See `docs/j713-owner-fir-native.md` in the repository or the included
`source/release-integration.tar.gz` archive.

This verifies local generation and its byte identities. Physical playback of
a rebuilt installer remains part of its attended installation check.

The static profile's [provenance record](../filters/README.md) distinguishes its
final numerical filters from the Apple inputs and raw intermediate captures. It does
not grant rights in Apple's software or tuning data. Existing runtime protection
and corresponding-source obligations remain in place.
