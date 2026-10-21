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

The initial successful candidate used boot package `1-16` and speaker package
`1-3`. Final packages `1-18` and `1-4`, built from
`0bcf42915012521ac7b40e3d3ba5735304c40b9b`, were subsequently installed through
the same normal updater and real ALPM hook. Their archive SHA-256 values are
`632a8f4a981488e7dc9c815e73b5e43f4df7be4bcd564541a64cf0beb608dd8e`
(boot) and `cdc127fd16e463e86ce78db41dc79aa219f994549460a4e16cb73b7fa37e9e2b`
(speaker). The final campaign repeated rollback, held update, explicit reapply
and an unchanged repeat boot on 2026-10-21:

| Check | Observed result |
| --- | --- |
| Upgrade | Kernel build 19 and hardened MCA loaded; fresh audio readiness passed |
| Rollback | Build 18 and stock MCA restored; MCA held |
| Rollback preservation | Complete boot object and all seven captured audio module hashes matched baseline |
| Rollback manifests | Parsed audio/video objects matched baseline; audio JSON was reserialized |
| Held update | Normal updater returned success with no package transaction and no restart needed; original boot hash and both holds remained |
| Explicit reapply | Build 19 and hardened MCA loaded; fresh phase-2 readiness in 1.031329135 seconds |
| Unchanged repeat boot | Same boot segments, manifests and audio module hashes; fresh phase-2 readiness in 1.031961279 seconds |
| Hardware video | All 120 decoded frame hashes matched baseline on final reapply and repeat; the earlier campaign also covered rollback |
| Protected playback | Three seconds through the protected PipeWire sink at 5% volume completed with exit 0 |
| Public-channel handoff | Original channel and pacman configuration restored; ordinary update retained boot 1-18/speaker 1-4, installed nothing and reported no restart needed |
| Marker creation | Newly created MCA rollback and hold files were empty and mode 0600 |

GPU, video, audio registration, speaker protection and backlight were active
on the successful candidate boots. CPU-idle was inactive in baseline and final
reapply/repeat captures, and active in the final rollback capture; this is not
a claim that every service was identical across boots. Its installed package
remained `1-3`. All eight packages outside boot and speaker retained the
production package bytes and installed versions.

The final rollback boot was `ccd9ddfe-c143-416e-aaa4-8c45140f0ac5`, reapply was
`45eef097-f160-46f2-9065-eb7d1b0f7062`, and repeat was
`e6283eeb-982f-4108-a9c9-eee244400f71`. Rollback restored the complete baseline
boot object `d03bc6d9fcd125c3b9624f369697a08d36570731c6419960d68534246f8279ed`;
reapply and repeat used
`d1f6db4dc30e7757c3ba0e9cc830ff41573ae56ca8b0121695f682e227b84038`.
The kernel hold was released only by `m4-boot-compose --resume-updates --apply`;
the independent MCA hold was removed before reboot, without swapping live audio.

The four non-kernel boot segments remained identical to baseline. Owner boot
arguments, firmware initramfs and shown-notch loader were preserved. Kernel
logs showed DockChannel keyboard and multitouch initialization and device
registration. Login used automated input injection, so it does not establish
physical keyboard or trackpad operation.

## Scope and verification

Candidate build receipts deliberately leave `native_qualified` and
`fresh_install_qualified` false: they describe a build, not a live hardware
qualification registry. Historical qualification records are unchanged.

The source joins name merged source revision
`24920ee126207d8a5595f1297892384cef77776a`; its recipe inputs match the four
clean builds recorded in the source audit. These observations do not claim
a production channel promotion. Physical typing, physical
trackpad use, human listening, suspend and fresh installation were not tested.
Automated enumeration and playback do not establish those results.

The signed sandbox package test separately checks dependency installation,
unchanged base-package payload, idempotence and signer rejection. It uses a
synthetic older setup request and skips ALPM scriptlets. The native run above
supplies the separate real-hook evidence; neither test substitutes for the
other.

The package revision `0bcf42915012521ac7b40e3d3ba5735304c40b9b` passed
[1,368 Linux tests in 81.066 seconds](https://github.com/Kb2uka/Omarchy-M4-Native/actions/runs/39541326985),
[package CI](https://github.com/Kb2uka/Omarchy-M4-Native/actions/runs/39541326954)
and [all four CodeQL language analyses](https://github.com/Kb2uka/Omarchy-M4-Native/actions/runs/39541327047).
The original arithmetic alert is fixed on develop; the marker-permissions
finding is fixed in the rollout analysis. Neither was dismissed or suppressed.
The signed package sandbox additionally demonstrates retaining newer installed
packages while still applying a legitimate upgrade. It does not substitute for
the native public-channel handoff above.
