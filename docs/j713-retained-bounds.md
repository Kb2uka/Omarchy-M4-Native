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

## Earlier installed Air observations

The normal updater installed a private signed candidate on an existing J713
Air, including its real ALPM boot hook. The first attempt exposed a deployed
audio manifest with valid JSON but no final newline. The helper refused that
format and retained working stock MCA. Removing the unnecessary formatting
restriction allowed the corrected candidate to load hardened MCA while
preserving the other component pins.

The initial successful candidate used boot package `1-16` and speaker package
`1-3`. Packages `1-18` and `1-4`, built from
`0bcf42915012521ac7b40e3d3ba5735304c40b9b`, were then installed through
the same normal updater and real ALPM hook. Their archive SHA-256 values are
`632a8f4a981488e7dc9c815e73b5e43f4df7be4bcd564541a64cf0beb608dd8e`
(boot) and `cdc127fd16e463e86ce78db41dc79aa219f994549460a4e16cb73b7fa37e9e2b`
(speaker). That campaign repeated rollback, held update, explicit reapply
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

That rollback boot was `ccd9ddfe-c143-416e-aaa4-8c45140f0ac5`, reapply was
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

## Final helper package qualification

Boot `1-19` and speaker `1-5`, built from
`3a8ea0a26ee7500211c9adb34be3f61d0ead52a3`, were installed through
`omarchy-m4-update` and its real ALPM hook on 2026-10-21. Their archive hashes
are `2a30de5ffc84bb803efdab27397fa9152fd5898fd545ad959659eeba8cf2c4f1`
and `24fe08b074e6f345a26ab4fc3c01ec2ad901f10947c8593f1bae1596e62af58f`,
respectively. These revisions contain the final recovery helpers; their kernel
and module artifacts are the same ones identified above. The transaction
upgraded only boot and speaker. All eight other installed package versions,
including CPU `1-3`, stayed unchanged.

| Check | Observed result |
| --- | --- |
| Rollback boot | `fe5b8abd-a837-48e8-a689-d140ce2fbdd5`; build 18, stock MCA loaded and held |
| Rollback preservation | Exact baseline boot object, all five segments and all seven audio module hashes; whole parsed audio manifest equal to the original backup |
| Held candidate update | Exit 0, empty package transaction, no restart needed, baseline boot and holds retained |
| Explicit reapply | `0afe1d2a-2d3c-4fe9-874c-9b4708e97754`; build 19 and fixed MCA; fresh phase-2 readiness in 1.032126593 seconds |
| Repeat boot | `c3d13695-75af-4aa0-ab4e-2b0a5a737fcc`; same fixed boot, manifests and module hashes; fresh phase-2 readiness in 1.031683264 seconds |
| Hardware video | All 120 frame records matched baseline on rollback, reapply and repeat |
| Backup permissions | Both stock backup halves remained root-owned mode 0600; an ordinary-user read of the module backup was denied, while held/current inspection returned the same report as root |
| Protected playback | Three seconds through the protected PipeWire sink at 5% volume, exit 0 |
| Public-channel handoff | Original configuration and matching database pair restored; ordinary update exited 0, installed nothing, requested no restart and retained `1-19`/`1-5` |

The five core GPU, video, audio-registration, speaker-protection and backlight
services were active on all three boots. CPU-idle was inactive on all three,
matching the original baseline. The non-kernel boot segments stayed identical
to baseline. Rollback's audio JSON was reserialized; the comparison checked
the complete parsed document against a backup whose bytes matched the original
baseline hash. Reapply and repeat matched the earlier fixed capture byte for
byte, including both protected manifests.

The temporary repository and its signing key were removed after the public
handoff. Verified recovery backups remain on the Air. Login used injected
input, and the physical-input and listening limitations below still apply.

## Scope and verification

Candidate build receipts deliberately leave `native_qualified` and
`fresh_install_qualified` false: they describe a build, not a live hardware
qualification registry. Historical qualification records are unchanged.

The source joins name merged source revision
`24920ee126207d8a5595f1297892384cef77776a`; its recipe inputs match final
clean build D recorded in the source audit. All four clean builds produced
identical artifacts. These observations do not claim
a production channel promotion. Physical typing, physical
trackpad use, human listening, suspend and fresh installation were not tested.
Automated enumeration and playback do not establish those results.
Before public-channel promotion, complete manual built-in keyboard and trackpad
acceptance, listening, suspend/resume and fresh-install qualification. This
develop-branch validation does not satisfy those remaining release checks.

The signed sandbox package test separately checks dependency installation,
unchanged base-package payload, idempotence and signer rejection. It uses a
synthetic older setup request and skips ALPM scriptlets. The native run above
supplies the separate real-hook evidence; neither test substitutes for the
other.

The earlier package revision `0bcf42915012521ac7b40e3d3ba5735304c40b9b` passed
[1,368 Linux tests in 81.066 seconds](https://github.com/Kb2uka/Omarchy-M4-Native/actions/runs/39541326985),
[package CI](https://github.com/Kb2uka/Omarchy-M4-Native/actions/runs/39541326954)
and [all four CodeQL language analyses](https://github.com/Kb2uka/Omarchy-M4-Native/actions/runs/39541327047).
The original arithmetic alert is fixed on develop; the marker-permissions
finding is fixed in the rollout analysis. Neither was dismissed or suppressed.
The signed package sandbox additionally demonstrates retaining newer installed
packages while still applying a legitimate upgrade. It does not substitute for
the native public-channel handoff above.

The final packaged helper revision `3a8ea0a` passed
[1,386 Linux tests in 81.918 seconds](https://github.com/Kb2uka/Omarchy-M4-Native/actions/runs/39549213413),
[package CI](https://github.com/Kb2uka/Omarchy-M4-Native/actions/runs/39549213268)
and [all four CodeQL analyses](https://github.com/Kb2uka/Omarchy-M4-Native/actions/runs/39549213362).
Its separate ten-package build, strict archive checks and signed sandbox
installation also passed. Later test and documentation changes do not alter
the packaged helper bytes or the native identities recorded here.
