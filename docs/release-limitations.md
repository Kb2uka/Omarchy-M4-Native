# Known limitations — v0.1.0-alpha.1

- **Experimental J713 only.** Mac16,12 / 13-inch M4 Air is the only supported
  installer profile. M4 Pro, Max, mini, iMac and other Air boards are not qualified.
- **Shutdown can hang.** The desktop closes and shutdown messages appear, but the
  Air may remain powered on. On the maintainer's Air, the workaround was: save
  work, choose Shut Down, wait for the stall, then hold power. Holding power while
  the desktop was still running instead caused a restart loop. This forced
  power-off is not a fix or proof that writes are complete. See
  https://github.com/Kb2uka/Omarchy-M4-Native/issues/24.
  The final candidate adds the missing device-tree child for the existing SMC
  poweroff handler. Its physical shutdown and stay-off behavior await the
  maintainer's check; the issue remains open.
- **First setup may stall near completion.** One attended private installation
  reached login after a restart. Allow for ongoing writes before considering
  recovery; the progress bar alone is insufficient evidence.
- **Final packaging not hardware retested.** Two attended private installations
  preceded this release; the final build changes owner-local audio preparation,
  consent, delivery packaging and the SMC poweroff device-tree child. The
  maintainer's final installation is pending.
  Code review and checksums do not prove successful installation on another Mac.
- **macOS audio inputs are pinned.** The local generator refuses unknown Apple
  AudioDSP/tuning hashes or unequal filter output before disk changes. Minimum
  macOS version alone does not guarantee those files match. The ARM64 filter
  generation pipeline passed end to end on macOS 26.5.1 and 26.5.2 with identical
  output hashes. The rebuilt installer's physical audio check remains pending.
- **Video acceleration is selective.** Brave and mpv have used hardware H264
  8-bit / VP9 Profile 0 decoding with the pinned stack. The shipped Chromium build
  and some protected movies use CPU decoding while the desktop uses GPU rendering.
  Other codecs retain software fallback. Universal hardware decoding is not claimed.
- **1080p60 windowed playback remains problematic.** An earlier comparison saw
  many more dropped frames in a normal browser window than fullscreen. Smooth
  30 fps playback does not establish that this issue is fixed.
- **USB:** use the hinge-side port. The front USB0 controller remains disabled.
- **Incomplete hardware qualification:** camera, suspend/resume, battery life,
  Bluetooth audio, arbitrary peripherals and fault recovery are not qualified.
  Speaker protection remains enabled; do not substitute unqualified filters or
  disable the protection services.
- **Updates are not coordinated yet.** Read UPDATES.md. Ordinary rolling updates
  can replace parts of the experimental stack; this alpha has no supported M4
  upgrade channel or qualified in-place upgrade path.

This independent integration is not endorsed by Omarchy, Basecamp, Asahi Linux,
Gravity Linux or Apple. It is supplied AS IS, without warranty. Installation or
use may cause data loss, boot failure or hardware damage. The repository's full
disclaimer and the upstream licenses remain applicable.
