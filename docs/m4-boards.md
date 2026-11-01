# M4 boards

[`packaging/m4-boards.json`](../packaging/m4-boards.json) is the one place
that says which Apple M4-family Macs this project supports. Only the 13-inch
M4 Air (J713) is qualified. The plain M4 Mac mini (J773g,
[#136](https://github.com/Kb2uka/Omarchy-M4-Native/issues/136)) and the
15-inch M4 Air (J715, [#142](https://github.com/Kb2uka/Omarchy-M4-Native/issues/142))
are in testing. Every other board is refused.

Each board names its chip. The M4 boards use the T8132; the M4 Pro Mac mini
(J773s) uses the T6040, a different chip, and its port is starting from the
[macOS survey](j773s-macos-survey.md). The M4 Pro and M4 Max MacBook Pros are
listed under `unlisted`: no board entry yet, and `Install.command` refuses
them like every other unqualified model.

<!-- m4-boards:begin -->
| Board | Chip | Model identifiers | Mac | Support | Installer profile |
|---|---|---|---|---|---|
| J713 | T8132 | Mac16,12 | MacBook Air (13-inch, M4, 2025) | qualified | 26.5.1, expert mode only |
| J715 | T8132 | Mac16,13 | MacBook Air (15-inch, M4, 2025) | testing | 26.5.1, expert mode only |
| J604 | T8132 | Mac16,1 | MacBook Pro (14-inch, M4, 2024) | refused | none |
| J623 | T8132 | Mac16,2 | iMac (24-inch, 2024, Two ports) | refused | none |
| J624 | T8132 | Mac16,3 | iMac (24-inch, 2024, Four ports) | refused | none |
| J773g | T8132 | Mac16,10 | Mac mini (M4, 2024) | testing | 26.6.2 |
| J773s | T6040 | Mac16,11 | Mac mini (M4 Pro, 2024) | refused | none |
<!-- m4-boards:end -->

The installer profile is the macOS stub version that the bundled
asahi-installer `DEVICES` table selects for that board. The Mac mini profile
comes from the upstream installer this project builds on. The 15-inch Air uses
the J713's 26.5.1 recovery image and the `kos` radio firmware
([j713-installer-profile.md](j713-installer-profile.md)). `Install.command`
still refuses every model except Mac16,12, so the release does not install on
a mini.

## Support status

- `qualified`: the release installs on it and every gate admits it. Moving a
  board here needs boot evidence from that board.
- `testing`: a board testers may install before it is qualified, from a kit
  built for that board and only with the tester's explicit opt-in. It needs an
  installer profile. The J713 release, its `Install.command` and every live
  full-update, boot, audio and module gates refuse it like a refused board;
  the tester kit is what admits it. The bounded J715 graphics update route
  below is an explicit exception, not a qualification.
- `refused`: nothing installs or runs on it.

## J715 graphics-only tester updates

An already installed J715 tester can enroll through the existing channel setup
tool and then run `omarchy-m4-update`, as described in [updates.md](updates.md).
This route requires the exact `apple,j715` / `apple,t8132` identity, the tester
base package `linux-m4-omarchy 7.1.9.m4.20261008-1`, the running release
`7.1.9-m4-gpu-backport-20261008`, and coherent inherited stack manifests.
The kit deliberately retains the J713-named audio/video manifests: each must
still identify schema 1, `apple,j713`, `apple,t8132` and that kernel release.
These checks establish the inherited tester environment, not the identity or
qualification of a locally changed boot image.

The only install/upgrade targets are `omarchy-m4-keyring`,
`omarchy-m4-update` and `omarchy-m4-xr30`. Before installing, setup resolves
their dependency closure and refuses any package outside that set, a different
repository, conflicts or replacements. Required distribution libraries must
already be installed. A J715 with `omarchy-m4-boot` installed is refused rather
than upgraded or repaired. No J713 boot composer, audio helper, module helper
or notifier is called, including after an installation failure. The boot
object, kernel, module and audio state are not update targets.

This exception leaves J715 `testing`, preserves J713's full update behavior
and changes no installer, boot, audio or module board guard. Graphics apply
at the next graphical login, not by composing J713 boot objects.

## What the board tests check

`tests/test_m4_boards.py` runs in the hosted Linux CI job and fails when:

- `Install.command` accepts a model that the table does not qualify, or
  refuses one that it does (a testing model is refused);
- a testing board has no installer profile;
- the installer `DEVICES` table, or a qualified board's IPSW entry, disagrees
  with the table;
- a shipped manifest names an unqualified board;
- shipped code under `tools/`, `packaging/`, `audio/`, `gpu/` or the patch
  directories names a board compatible (`apple,j…`) that is neither qualified
  nor testing;
- the GPU module's Air bootstrap gate (`gpu/j713/asahi/g16.rs`) admits an
  unqualified board or refuses a qualified one;
- a live-system board gate lets a real sibling M4 identity through, refuses it
  with a different message, or writes anything before refusing. The gates are
  `m4-boot-compose`, the default full-update `m4-channel-setup` preflight,
  `m4-headphones`, `m4-lid-lock`,
  `m4-session-placement`, `m4-j713-audio-ready`, `m4-j713-audio-register`,
  `m4-j713-owner-assets` (its fixed paths redirected into the fixture) and the
  board and SoC lines of `m4-video-start` (run with its own `fail()`). Each
  sibling is fed the `compatible` list m1n1 hands Linux on that Mac
  (`apple,<board>`, `apple,<chip>`, `apple,arm-platform`), so the J773s
  identity also carries a different SoC string. The gates that also
  check the SoC must refuse the J713 name on an M2 SoC;
- the separate J715 graphics preflight admits a sibling, near-match,
  unterminated or mixed-board identity. Channel setup tests cover its exact
  tester prerequisites, bounded dependency transaction and protected-state
  preservation on success and failure;
- a gate that reads a devicetree file lets a sibling M4 identity, or the J713
  name on an M2 SoC, through: the J713 devicetree builders
  (`build-j713-cpufreq-dtb.py`, `build-j713-cputemp-dtb.py`,
  `build-j713-poweroff-dtb.py`, with their base hash pinned to the sibling so
  the board check itself must refuse, before compiling or writing), the audio
  package's base-DTB check (`tests/test_j713_audio_installer.py`) and the video
  package's boot-DTB check (`tests/test_j713_candidate.py`).

`tests/m4_board_identities.py` gives every one of these tests the same table
and identities.

Passing these tests shows that the gates refuse other M4 Macs. It does not
show that anything works on them. Only a report from the real machine can.

Not covered yet: the kernel C audio gates, which hosted CI does not compile.
`m4-video-start` takes its lock and clears its runtime state under `/run`
before its board check, so on another Mac those runtime writes would happen
before the refusal; the test runs only its board and SoC lines.

## Adding a board

A new board starts as `refused`. It can move to `testing` once it has an
installer profile and a survey of the real machine, so that a tester kit can be
built for it. Moving it to `qualified` means changing the table,
`Install.command`, the installer profile and every gate the tests list in the
same pull request, with hardware evidence from that board.

Speaker support also selects a versioned profile for that board, including its
filter assets, routing, gains and protection settings. The static J713 files
replace a generator that was already specific to J713; they are not a default
for other M4 machines. Sharing a profile between boards requires evidence for
that reuse. The owner's current macOS AudioDSP need not become another platform
qualification dimension.
