# Static J713 speaker profile

These four mono 48 kHz float32 FIR files are the approved native J713 profile,
not a new tuning. Their combined size is 41,184 bytes. Their exact sizes and
SHA-256 identities are recorded in the `outputs` field of
[`../owner-fir/manifest.json`](../owner-fir/manifest.json).

The release assembler verifies all four files before changing the root image,
then installs them under `/usr/share/asahi-audio/j713/`. Its audio manifest
records those same identities. Runtime integrity checks continue to require
the exact installed bytes.

## Reproduction

On 2026-10-16 the unchanged release generator reproduced all four approved
hashes in a macOS 26.6.2 (25G83) VM on an M4 host. It used the exact shipped
native helpers, CPython 3.13.9 and NumPy 2.5.3. The AudioDSP input matched the
existing qualified hash `b8711ac5c284751b343a9b2ea23799107225f5aadb7dcbfb68be1134508f64a6`.

The retained pipeline renders synthetic impulses through selected AudioUnit
stages, normalizes their responses, combines two high-pass stages with Tone3,
truncates the finite responses and serializes the coefficients as WAV files.
The source and complete input/dependency pins remain in `../owner-fir/`.
The original native reproduction evidence is described in
[`docs/j713-owner-fir-native.md`](../../../docs/j713-owner-fir-native.md).

This reproduction establishes the files' identities. It does not claim a new
playback test. Routing, graph gain, amplifier controls and protection settings
remain the existing J713 configuration. Other boards need their own qualified
profiles; a shared chassis alone does not enable one.

## Distribution policy proposal

Earlier releases withheld generated FIR files because redistribution rights
had not been established. This change proposes distributing the final
functional numerical profile instead of making every owner regenerate it.
These files contain no AudioDSP executable, serialized Apple preset, tuning
strip, recorded music or personal calibration data. They are nevertheless
derived from Apple's tuning through the documented measurement pipeline.

Generated output is not automatically owned by the software vendor or the
owner of the computer. Inclusion proposes a change to the project's previous
distribution policy; it is not a determination of the files' copyright status
or a grant of rights in Apple software or source data. That policy decision
must be resolved before promoting a release containing these assets.
