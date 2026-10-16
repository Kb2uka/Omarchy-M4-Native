# J713 native owner-filter reproduction

The first public-candidate installer refused during speaker-filter preparation
on the maintainer's Air. The installed macOS inputs, extracted presets, helper
binaries and NumPy wheel all passed their pinned hashes. The generated WAVs
failed the final identity check before privilege escalation or partitioning.

The reference had combined native AudioUnit responses with NumPy 2.5.3 on
x86_64 Linux. Running the retained programs with the shipped arm64 NumPy wheel
produced small floating-point differences. A complete native run on the J713
Air (macOS 26.5.1 / 25F80) and Mac Studio (26.5.2 / 25F84) produced the same
four native hashes. CPython was the installer's bundled 3.13.9 in both cases.

macOS 26.6.2 (25G83) ships a different AudioDSP binary (17,311,312 bytes,
`b8711ac5c284751b343a9b2ea23799107225f5aadb7dcbfb68be1134508f64a6`). On a second
13-inch M4 Air running that build, the unchanged pipeline with only the input
pin replaced produced the same four native hashes. The manifest therefore lists
it as a second qualified AudioDSP build; the outputs remain single-pinned.

| Lane | Taps | Different samples | Maximum absolute difference | Sum of absolute differences |
| --- | ---: | ---: | ---: | ---: |
| 0 | 4096 | 27 | 1.111e-16 | 3.972e-16 |
| 1 | 4096 | 22 | 1.111e-16 | 3.904e-16 |
| 2 | 1024 | 4 | 3.553e-15 | 3.584e-15 |
| 3 | 1024 | 4 | 3.553e-15 | 3.584e-15 |

Values in the table are rounded upward. File lengths, tap counts and peak
coefficients match. For a bounded input with absolute magnitude at most one,
the per-filter sum bounds the absolute change in the ideal linear convolution
output. This comparison does not establish physical speaker performance or
replace the final installed-machine check.

`audio/j713/owner-fir/manifest.json` retains the old identities in
`reference_outputs` and pins the native results in `outputs`. The helper still
requires exact input, preset, dependency, executable and all four output hashes.
It accepts no approximate match. The release assembler checks the same
native identities against the committed static filters.

The release builder verifies the retained audio manifest and original filter
identities, then changes the four filter hashes in its two file maps. It also
records the native profile's provenance in place of the obsolete FIR-only
distribution policy fields. The historical qualification record remains intact.
It installs that manifest in the root image and records its new digest in
`IMAGE.json`. The kernel modules, daemon, audio routing, amplifier configuration,
speaker protection, volume settings and other audio payloads are retained.
The original `audio/j713/qualified.json` remains historical evidence for the
earlier runtime; it is not silently relabeled as a new playback test.

## Static profile delivery

The four approved native WAVs are versioned in
[`audio/j713/filters`](../audio/j713/filters/README.md). On 2026-10-16 the
unchanged release pipeline, exact shipped helpers, CPython 3.13.9 and NumPy
2.5.3 reproduced every approved hash in a macOS 26.6.2 (25G83) VM on an M4 host.
These files are the existing profile, not a new macOS 27 response.

The release assembler installs them directly into the root image. Installation
no longer runs the current host's AudioDSP or transports generated WAVs through
its firmware package. The development generator retains its historical input
checks for reproduction. Runtime checks still enforce the exact installed
native hashes. Fresh installation and physical playback of a rebuilt kit
remain separate validation steps.
