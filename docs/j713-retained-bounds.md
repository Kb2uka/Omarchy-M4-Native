# J713 retained bounds qualification

This records the installed-Air validation of the kernel and MCA artifacts built
by the [retained source recipe](retained-audio-transport-audit.md). Source
corrections alone do not update an installed machine. The boot and speaker
packages stage the new artifacts, and their helpers coordinate the protected
manifests and recovery state through `omarchy-m4-update`.

## Artifact identities

| Artifact | Identity |
| --- | --- |
| Kernel release | `7.1.9-m4-gpu-backport-20261008`, build 19 |
| Image.gz, 18,900,939 bytes | `ca1fff571cf1b9d8c0b07b2e97ba0a5402f93c1b7a2d54ea2c07e24a8b70f9d0` |
| MCA, 79,152 bytes | `fb941df4d55c4f2093b4595e0392aaf97ffe3a250228518b18a4f70568bf7454` |

The decompressed Image contains the release above and
`#19 SMP PREEMPT_DYNAMIC 2026-10-20`, matching the running-kernel captures.
The base kernel package remains unchanged. Four other audio modules reproduce
their historical qualified hashes; MCA is the changed audio module.

## Installed Air observations

The normal updater installed a private signed candidate on an existing J713
Air, including its real ALPM boot hook. The first attempt exposed a deployed
audio manifest with valid JSON but no final newline. The helper refused that
format and retained working stock MCA. Removing the unnecessary formatting
restriction allowed the corrected candidate to load hardened MCA while
preserving the other component pins.

The successful candidate used boot package `1-16` and speaker package `1-3`.
The following observations describe those packages; final recovery-helper
qualification is still pending.

| Check | Observed result |
| --- | --- |
| Upgrade | Kernel build 19 and hardened MCA loaded; fresh audio readiness passed |
| Rollback | Build 18 and stock MCA restored; MCA held |
| Rollback preservation | Complete boot object and all seven captured audio module hashes matched baseline |
| Rollback manifests | Parsed audio/video objects matched baseline; audio JSON was reserialized |
| Reapply | Build 19 and hardened MCA loaded; fresh phase-2 readiness in 1.032337215 seconds |
| Unchanged repeat boot | Same boot segments and audio module hashes; fresh phase-2 readiness in 1.029779771 seconds |
| Hardware video | All 120 decoded frame hashes matched baseline on upgrade, rollback and repeat boot |
| Protected playback | Three seconds through the protected PipeWire sink at 5% volume completed with exit 0 |

GPU, video, audio registration, speaker protection and backlight were active
on the successful candidate boots. CPU-idle was inactive in baseline and the
reapply capture, and active in the repeat capture; this is not a claim that
every service was identical across boots. Its installed package was unchanged.

The four non-kernel boot segments remained identical to baseline. Owner boot
arguments, firmware initramfs and shown-notch loader were preserved. Kernel
logs showed DockChannel keyboard and multitouch initialization and device
registration. Login used automated input injection, so it does not establish
physical keyboard or trackpad operation.

## Scope and remaining delivery checks

Candidate build receipts deliberately leave `native_qualified` and
`fresh_install_qualified` false: they describe a build, not a live hardware
qualification registry. Historical qualification records are unchanged.

Final combined packages, persistent boot rollback holds, exact source joins
and final independent review remain delivery prerequisites. These observations
do not claim a production channel promotion. Physical typing, physical
trackpad use, human listening, suspend and fresh installation were not tested.
Automated enumeration and playback do not establish those results.

The signed sandbox package test separately checks dependency installation,
unchanged base-package payload, idempotence and signer rejection. It uses a
synthetic older setup request and skips ALPM scriptlets. The native run above
supplies the separate real-hook evidence; neither test substitutes for the
other.

Revision `5c8d3148d91ba9a00d5af829f99ad9251eaed959` passed
[1,312 Linux tests in 82.252 seconds](https://github.com/Kb2uka/Omarchy-M4-Native/actions/runs/39528346296)
and [package CI](https://github.com/Kb2uka/Omarchy-M4-Native/actions/runs/39528348315).
That integration revision is distinct from the packages used in the native
observations and from the final combined delivery revision.
