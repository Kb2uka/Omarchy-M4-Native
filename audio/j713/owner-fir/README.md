# Owner-local J713 audio filters

This tool prepares four J713 FIR files from the owner's installed
macOS AudioDSP component and AID37 tuning strip. The distributed bundle contains
the generator, two native helpers, and NumPy. It must not contain Apple presets,
tuning strips, AudioDSP, measured responses, or generated WAV files.

```sh
/path/to/bundled/python3.13 tools/m4-j713-fir-owner.py --output /absolute/new/owner-firs
```

The output directory must not exist. Its parent must exist. The command accepts
`--bundle /absolute/owner-fir` when the installer uses a different layout. With
no override, the bundle is `audio/j713/owner-fir` relative to the repository root
containing `tools/m4-j713-fir-owner.py`.

The command reads installed system files, creates temporary private files, and
renders AudioUnit responses into memory. It does not start an audio output unit,
access speaker hardware, change a volume setting, mount a disk, or invoke an
installer. It uses no network connection and does not require Xcode on the
owner's Mac. The native helpers are built in advance.

All input, extracted-preset, helper, dependency and output hashes are pinned in
`manifest.json`. Generation requires native arm64 macOS and the installer's
CPython 3.13.9 without optimization. The current installed system input hashes
were verified on macOS 26.5.1 (25F80) and 26.5.2 (25F84). A different input or output is a refusal,
not permission to use a substitute filter. No compatibility claim is made for
other macOS inputs.

Success creates exactly four `j713-static-minimal-laneN-48000.wav` files and
`receipt.json`. The installer must require success and independently verify the
four hashes **before offering partition changes**. The receipt describes asset
generation, not installation or hardware qualification. Temporary presets,
responses and extracted NumPy files are removed when the command exits normally
or reports an error. The release builder carries the native output pins into
the installer handoff and installed audio manifest. Speaker protection policy
is unchanged.

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

No C compilation or dependency download happens on the owner's Mac.

## Validation status and limits

The two helpers were compiled, their Mach-O architecture and dynamic library
dependencies inspected, and their bytes hashed. The dependency wheel and source
archive were downloaded from the pinned PyPI links and their hashes verified.
The build host's installed AID37 inputs and AudioDSP match the retained source
inputs. A second arm64 Mac has the same AudioDSP hash.

The complete owner-generation command succeeded on the J713 Air running macOS
26.5.1 and an arm64 Mac Studio running 26.5.2, using the bundled CPython 3.13.9,
helpers and NumPy 2.5.3. Both produced the exact same four output hashes.

The earlier reference combined native macOS responses using NumPy 2.5.3 on
x86_64 Linux. The ARM64 results differ at 57 of 10,240 float32 samples, with a
maximum absolute difference of 3.553e-15 and a per-filter sum of absolute
differences below 3.584e-15. Tap counts and peak coefficients are unchanged.
The old hashes remain in `reference_outputs`; `outputs` identifies the measured
native results. No numerical tolerance replaces the four exact hash checks.
See `docs/j713-owner-fir-native.md` in the repository or the included
`source/release-integration.tar.gz` archive.

This verifies local generation and its byte identities. Physical playback of
the rebuilt installer remains part of the maintainer's final installation.

The existing measured WAV assets have no established redistribution license.
This arrangement avoids including them in a download. It does not establish
their legal status, grant rights in Apple's software or data, or remove any
other corresponding-source obligation. Existing runtime protection and package
verification remain required.
