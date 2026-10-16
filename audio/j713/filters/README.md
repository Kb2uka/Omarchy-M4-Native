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

## Distribution and provenance

The repository includes these final functional numerical filters as platform
profile data. They contain no AudioDSP executable, serialized Apple preset,
tuning strip, recorded music or personal calibration data. Their response is
derived from Apple's tuning through the documented measurement pipeline.

This distinction is the basis for distributing the profile separately from its
source inputs. It does not assert that generated output is automatically owned
by the software vendor or the owner of the computer, determine its copyright
status, or grant rights in Apple software or source data. Apple inputs and raw
intermediate captures remain outside the distributed profile.
