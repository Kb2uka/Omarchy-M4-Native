# J713 first-boot recovery corrections

The October 12 owner installation completed provisioning at 09:27:45 local time.
Four captured boots failed graphics and speaker startup. These observations
come from persistent journals read from the failed installation while macOS was
running; no failed Linux filesystem write was needed to retrieve them.

## Confirmed failure paths

- Graphics exited in its pre-start control command, a global ten-second
  `udevadm settle`. Trackpad initialization was still timing out. The video
  service then explicitly reported that the desktop GPU was not ready.
- Speaker registration raised `RuntimeError` at its first global
  `udevadm settle --timeout=10`; it never reached the targeted provider checks.
- logind reported `autovt@tty2.service not found` when recovery was attempted.
- The Framework AMD audio helper occupied exactly 30 seconds in the owner log
  on this Apple arm64 machine. Finalization subsequently completed successfully.
- Setup began at 9 seconds while trackpad diagnostics continued until 24 seconds.
  Concurrent console output can displace interactive prompts. No Gum renderer
  incompatibility or between-prompt resize was demonstrated.

## Replacement behavior

The GPU pre-start waits for initialized `/dev/dri/card0`, the existing simpledrm
scanout device. The startup helper then inserts the GPU module and checks its
render node, board, kernel, file integrity, firmware handshake and Apple M4
renderer. Waiting for that render node before insertion would prevent startup.
Audio
registration uses its existing exact provider/module/readiness checks without
waiting for unrelated device events. Its explicit service ordering now names
event submission. The shipped boot does not request the global settle service;
if another package requests it, systemd's normal `sysinit.target` ordering still
delays ordinary services. This repair does not bypass that global dependency.
Decoder startup also waits for its own
device nodes rather than the global queue. Protection and one-attempt failure
latching are unchanged. The release assembler updates both audio integrity maps
and the decoder helper identity in the video manifest.

An authenticated tty2 is enabled independently of graphics. A small bounded
status report and a text-mode-only notice cover failed desktop handoff without
writing over active setup, a text login or a graphical session. The owner form
runs after the hardware startup jobs; kernel console printing is temporarily
suppressed and restored on success or failure, while the journal retains logs.
The unrelated Framework probe is skipped on aarch64 only.

## Evidence and limits

Fail-before and pass-after regressions cover the global device wait, missing
console alias, console controls and arm64 audio probe. Release integration tests
exercise the real offline configurators and check both runtime integrity maps.
The kernel, input firmware and GPU/video binaries are retained.

The earlier qualified kernel also had an intermittent `AFE: No HINT_L` trackpad
failure. The current failed boots exhausted retries using that same kernel and
matching firmware. These startup repairs do not claim to fix it. An attended
fresh installation, physical trackpad input, playback, restart and shutdown are
required before publication; offline results are not hardware qualification.
