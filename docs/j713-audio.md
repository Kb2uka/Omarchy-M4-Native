# J713 protected audio: source and qualification

This checkout contains the installer, registration, readiness and protected-audio
sources used by the qualified J713/T8132 runtime. It targets the ARM64 kernel
`7.1.9-m4-gpu-backport-20261008` and the exact userspace ABI pinned in
[`qualified.json`](../audio/j713/qualified.json). It is an opt-in addition to a
pending offline ext4 owner image, not a generic audio package or a live updater.

Use the [source package guide](../audio/j713/README.md) for build inputs, local
assembly and image apply/check. The [readiness guide](j713-audio-readiness.md)
describes the system-service and user-WirePlumber ordering. Both components are
present here; package assembly uses `--readiness-source .` from the repository
root. The assembler checks all supplied assets before creating its output.

## Qualified runtime

The reference runtime manifest is
`65ddf60ccf257971bcc6b611e5a9a242a6b0f79906ddb88a3a128fae360dc8c5`.
Its 27 regular payload files include seven modules, the protection daemon and
profile, graph/FIR assets, UCM/WirePlumber policy, and startup helpers/units. The
four FIR WAVs and compiled binaries are supplied separately for local assembly;
they are not files in this source tree. The package manifest also declares the
two system-service enable links created by the image installer.

The corrected registration helper waits for actual deferred provider bindings
after overlay population, then binds MCA explicitly if needed. The readiness
helper accepts the bounded pre-lease MUTE startup interval without confusing its
configuration deadline with the later 50 ms lease. These are distinct from
permission to produce output: the kernel group still enforces the current
generation, control owner, feedback lease and hardware mode/level checks.

The daemon uses eight S16 feedback channels at 48 kHz, a 1024-frame period and
2048-frame ring. Its maximum queued backlog remains 512 frames. Each period runs
two ordered 512-frame thermal-model steps and applies the more attenuating result.
The userspace sample-age check runs before the synchronous renewal ioctl; it
does not measure time spent inside that ioctl. The kernel independently checks
lease expiry. The service's `LimitRTPRIO=50` permits requesting real-time priority;
it does not assign such a scheduling policy to this daemon.

## Native evidence and limits

The following observations were retained on 2026-10-10. Raw reports stay outside
the source package because they also contain machine/session metadata.

| Evidence | Observation | Retained report SHA-256 |
| --- | --- | --- |
| Automatic startup, 2026-10-10 | All 27 installed payload files and seven loaded module notes matched the reference manifest; registration completed, protection started automatically, readiness observed 15.074 seconds, and desktop video/audio worked without manual audio startup. | `62cb07c3f4584d6e09dd68ab2cdb25f81b292fc9fa550af46b323bbe29dcca8a` |
| Earlier sustained observation of the same audio binaries | The 1200.154-second observer completed with no reported errors and the same protection invocation/generation. Its 1127 media samples included 866 Playing, 165 Paused and 96 Stopped; ten ordinary transport stops were traced with no trace overruns/dropped events. | Archive `c01baecda80c1874aa7d0be6058276ae8d07722c4e615e3a08f7e3f6f0903c2f` |

The sustained observation included manual video changes; it is not a claim of
20 minutes of continuous playback. One successful automatic normal reboot does
not establish arbitrary reboot reliability. The offline image apply/check path
has filesystem-fixture coverage and was not used to create that native install.
Fresh-image installation on another machine, acoustic calibration, broad load
coverage and suspend/resume remain unqualified. The separate experimental sleep
coordinator, PM changes and resume daemon are not included in this runtime.

Faults remain latched and the protection service retains `Restart=no`. This
package does not promise automatic recovery after thermal faults, capture XRUNs,
lease loss or a changed ABI. Offline image apply/check refuses a mismatched
kernel, libasound, payload or customized destination. Runtime registration and
readiness pin the kernel and installed payloads; they do not re-attest the shared
library ABI after a system update. Such updates require separate qualification.

## Reproduce the source checks

Run these commands in a disposable checkout on a leased development-test host:

```sh
python3 -B -m unittest discover -s tests -p test_j713_audio_installer.py -v
python3 -B -m unittest discover -s tests -p test_m4_j713_audio_ready.py -v
python3 -B tests/j713_audio_mutants.py
python3 -B tests/j713_audio_readiness_mutants.py
git diff --check
```

The installer suite has 31 tests and the readiness suite has 35. The mutation
scripts temporarily change one source guard at a time and restore it; they must
not run in a shared checkout. Fixtures exercise actual helper/CLI paths while
mocking system-manager, clock, ownership or hardware boundaries. They do not
load modules, open native audio, or prove physical speaker behavior. The captured
PCM fixture has a substituted process identity and contains no user media data.

## Source distribution boundary

Original packaging, tests and policy text are MIT-licensed. Kernel-derived
sources retain their file-level GPL notices; the modified speakersafetyd 3.0.0
source is GPL-2.0-only, with the vendored ALSA crate's Apache-2.0/MIT notices
preserved. See [LICENSE.md](../LICENSE.md) for scope.

This source is sufficient to inspect the changed seams and assemble already-held
qualified inputs. A complete external rebuild still requires the exact configured
kernel/export table, ARM64 sysroot, offline Cargo cache and immutable compiler
images described in the build guide. The historical modified daemon's exact
upstream commit has not been independently reconstructed; its supplied source
snapshot is pinned instead. Complete corresponding source and toolchain inputs
must be addressed before distributing resulting GPL binaries.

The historical assembly tool retains its local-only distribution hold. The
release assembler uses the [static native profile](../audio/j713/filters/README.md),
whose numerical files reproduce the already approved hashes. Its provenance record distinguishes final numerical coefficients from source
inputs; the source-code license is not a grant of rights in Apple software or
tuning data. No Apple executable,
tuning strip, raw hardware capture, sysroot, compiled module or daemon executable
is included here. Other build-provenance and corresponding-source requirements
remain independent of the speaker-profile delivery change.
