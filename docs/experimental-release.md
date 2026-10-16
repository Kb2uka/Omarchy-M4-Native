# Experimental release qualification

This is an independent integration by **studioxradio aka kb2uka**.
The first experimental release is **v0.1.0-alpha.1**, based on **Omarchy 4.0.4**.
It is **experimental on M4 Air** and **very experimental on all other M4
machines**. Only the 13-inch M4 Air, Mac16,12 / J713 / T8132, has the tested
installer profile. No result here qualifies M4 Pro, M4 Max or another board.

The maintainer completed two private fresh-install trials on the Air. The second
reached login after a setup stall and restart; keyboard, trackpad, audio and video
were owner-confirmed. Normal shutdown still failed. Browser/codec identity was
not captured in that final report, so it does not prove hardware decode.

The published installer removes preset personal consent and prepares exact
speaker filters on each owner's Mac. The release assembler instead includes
those same approved filters as static data. **These packaging changes do not
have a fresh-install hardware test.** The maintainer initially waived further tests, then reinstated the
final attended installation before publication. That installation is pending,
including the candidate SMC poweroff device-tree correction and stay-off check.
The private trial is not evidence for unexecuted changes.

## Three different kinds of evidence

1. **Tracked source:** patches, helper code, tests and documentation in this
   repository. Historical notes refer to exact earlier revisions and may describe
   states that have since changed. Do not apply old bring-up workarounds blindly.
2. **Qualified native runtime:** selected payloads were installed and exercised
   on one Air. The protected audio stack completed automatic startup; compositor
   placement and ordinary browser/media-player paths have separate observations.
   These facts do not mean every corresponding component has been joined into a
   complete installer image or tested from a new owner's first login.
3. **Installer release:** a complete, hash-identified image and install path,
   including its sources, notices and owner-input path. Its manifest distinguishes
   the earlier native observations from the final untested packaging changes.

Do not silently promote a result from one category to another. The main branch
and open source-package changes must be reviewed at their actual revisions;
a native runtime may contain separately staged work that is not yet in either.

## Current runtime limits

The experimental video path qualifies H264 8-bit and VP9 Profile 0 hardware
output with the pinned stack. Unsupported codecs/profiles must retain software
fallback. A protected Widevine movie was observed using CPU decoding; universal
protected-video acceleration is not a release promise.

A normal 1080p60 browser window remains a known problematic case: one retained
comparison reported roughly 47% dropped frames versus 1 in 1,804 fullscreen.
Frame counters do not measure physical panel presentation. Keep viewport,
fullscreen state, source codec/frame rate, audio route and decoder identity with
any performance report. Good 1080p30 or local mpv playback is not proof that the
60fps issue disappeared.

Protected speaker playback depends on the exact codec/transport, feedback daemon,
lease enforcement and DSP assets. It is not equivalent to enabling an arbitrary
ALSA sink. Suspend/resume, automatic fault recovery, broad acoustic calibration,
battery life, camera and general USB peripheral coverage are not qualified by
the currently described runtime. Preserve software-only fallback where supplied;
do not imply every failure is recoverable.

## Retained local-player observations

The native reports below were inspected for these narrow claims. Reports remain
private because they contain session paths and process metadata; hashes identify
the retained evidence without publishing it.

| Observation | Boundary | Report SHA-256 |
| --- | --- | --- |
| Normal stock GIO/file-open entry | H264 media time 20.0→29.5s in a 10-second check, `vaapi-copy`, zero reported frame/decoder drops, PipeWire selected. The AV1 control reached EOF in software with one startup frame drop and zero decoder drops. Temporary user instrumentation was removed afterward. | `77be355adf9954759381131d5432fcb3c40b1ab5ac6096b3dcf2b73e43ecf7d5` |
| Explicit software and failed VA initialization controls | Both progressed in software and exited 0, reporting zero drops. These used null video/audio outputs, so they are fallback controls, not visible or audible playback proof. | `c408abdd7a3d69a21d8c85cd1dd5cc52cf1b7753bf174679763f18844d7490f6` |
| Earlier visible local playback | Two full 60.633s H264 plays with explicit VA environment/options, PipeWire output and zero reported frame/decoder drops. This separately established the playback path; it did not establish the later installed normal launcher entry. | `4ad93788ccbd2d8ad4718736cb3557de4762f78e7d2cce401479dcc04d7dbbcb` |

An earlier diagnostic launch set `MPV_HOME`, which intentionally suppresses the
global config directory in mpv 0.41. Its default software selection was not a
valid test of `/etc/mpv/mpv.conf` and was retained as an instrumentation failure.
No settings were changed to hide that result. Native audio selection is not
independent proof of acoustic output; the normal-launch diagnostic was muted.

## Candidate and distribution boundaries

A candidate needs a manifest identifying the boot payload, kernel/config/source,
DT, firmware acquisition path, modules, graphics/decoder libraries, audio assets,
userspace ABI, service/owner defaults and installer revision. Keep unqualified
runtime candidates separate from the accepted package.

Preserve the original notices for Asahi, Gravity, Omarchy and every other upstream
component. Original project credit is **studioxradio aka kb2uka**; this is not a
replacement for upstream authorship. When binaries are included, the applicable
license's corresponding-source and notice requirements still apply. A hash is
an identity check, not a redistribution license or a complete source bundle.

The [static J713 profile](../audio/j713/filters/README.md) contains the approved
final numerical filters and their reproduction provenance. Including those
files does not grant rights to distribute the Apple source inputs.
Apple firmware, macOS components, tuning strips, extracted presets, raw render
captures and Widevine remain excluded unless a separately established lawful
distribution path applies. Private assembly alone does not resolve those rights.
Keep raw device trees, personal browser state, account credentials, machine identities, build-host paths and unredacted native logs out
of installable artifacts and any public tree.

## Final packaging evidence boundary

The static profile was independently reproduced with every existing native
output hash intact. The release assembler verifies those files before changing
the root image. The host macOS AudioDSP is no longer part of installation, while
firmware and recovery prerequisites remain. Local reproduction and offline
packaging checks do not establish a successful fresh installation.

No new native installation, playback, reboot or test-suite result is claimed for
the final release packaging. Asset hashes and image readbacks identify what is
shipped; they do not substitute for those tests. The download is an experimental
candidate awaiting the maintainer's final install. It retains all board checks and
requires the owner's local Apple startup authorization.
