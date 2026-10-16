# M4 boards

[`packaging/m4-boards.json`](../packaging/m4-boards.json) is the one place
that says which Apple M4 (T8132) Macs this project supports. Only the 13-inch
M4 Air (J713) is qualified. Every other M4 board is refused. M4 Pro and M4 Max
Macs use a different chip and are outside this port.

<!-- m4-boards:begin -->
| Board | Model identifiers | Mac | Support | Installer profile |
|---|---|---|---|---|
| J713 | Mac16,12 | MacBook Air (13-inch, M4, 2025) | qualified | 26.5.1, expert mode only |
| J715 | Mac16,13 | MacBook Air (15-inch, M4, 2025) | refused | none |
| J604 | Mac16,1 | MacBook Pro (14-inch, M4, 2024) | refused | none |
| J623 | Mac16,2 | iMac (24-inch, 2024, Two ports) | refused | none |
| J624 | Mac16,3 | iMac (24-inch, 2024, Four ports) | refused | none |
| J773g | Mac16,10 | Mac mini (M4, 2024) | refused | 26.6.2 |
<!-- m4-boards:end -->

The installer profile is the macOS stub version that the bundled
asahi-installer `DEVICES` table selects for that board. The Mac mini profile
comes from the upstream installer this project builds on. `Install.command`
still refuses every model except Mac16,12, so the release does not install on
a mini.

## What the board tests check

`tests/test_m4_boards.py` runs in the hosted Linux CI job and fails when:

- `Install.command` accepts a model that the table does not qualify, or
  refuses one that it does;
- the installer `DEVICES` table, or a qualified board's IPSW entry, disagrees
  with the table;
- a shipped manifest names an unqualified board;
- shipped code under `tools/`, `packaging/`, `audio/`, `gpu/` or the patch
  directories names a board compatible (`apple,j…`) that is not qualified;
- the GPU module's Air bootstrap gate (`gpu/j713/asahi/g16.rs`) admits a
  refused board or refuses a qualified one;
- a live-system board gate lets a real sibling M4 identity through, refuses it
  with a different message, or writes anything before refusing. The gates are
  `m4-boot-compose`, `m4-channel-setup`, `m4-headphones`, `m4-lid-lock`,
  `m4-session-placement`, `m4-j713-audio-ready`, `m4-j713-audio-register`,
  `m4-j713-owner-assets` (its fixed paths redirected into the fixture) and the
  board and SoC lines of `m4-video-start` (run with its own `fail()`). Each
  sibling is fed the `compatible` list m1n1 hands Linux on that Mac
  (`apple,<board>`, `apple,t8132`, `apple,arm-platform`). The gates that also
  check the SoC must refuse the J713 name on an M2 SoC;
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

A new board starts as `refused`. Moving it to `qualified` means changing the
table, `Install.command`, the installer profile and every gate the tests list
in the same pull request, with hardware evidence from that board.

Speaker support also selects a versioned profile for that board, including its
filter assets, routing, gains and protection settings. The static J713 files
replace a generator that was already specific to J713; they are not a default
for other M4 machines. Sharing a profile between boards requires evidence for
that reuse. The owner's current macOS AudioDSP need not become another platform
qualification dimension.
