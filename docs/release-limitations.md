# Known limitations — v0.1.0-alpha.1

- **Experimental J713 only.** Mac16,12 / 13-inch M4 Air is the only supported
  installer profile. M4 Pro, Max, mini, iMac and other Air boards are not qualified.
- **Shut Down restarts the Air until it is connected to M4 updates.** alpha.1
  enables the SMC poweroff driver without the PMU shutdown-state cells, so the
  firmware starts the Air again. Connecting to M4 updates (UPDATES.md) applies
  the fix in place, with no reinstall, as does the standalone update 1 verified
  on the maintainer's Air:
  https://github.com/Kb2uka/Omarchy-M4-Native/releases/tag/v0.1.0-alpha.1-update.1
  (issue #24). Without it, only holding the power button keeps the Air off, and
  a long press can still end in a restart depending on its timing.
- **Replacement first boot still needs qualification.** The October 12 candidate
  finished account setup but failed graphics and audio on global device waits;
  the recovery console alias was also missing. The replacement corrects those
  confirmed paths, restores an independent text login, protects setup from
  kernel console messages, and skips an unrelated 30-second audio probe.
  Its actual fresh installation and desktop handoff are still pending.
- **Trackpad startup is intermittent.** The same retained kernel and matching
  firmware have reached `Touch MT ready` on an earlier installation, but the
  failed candidate repeatedly exhausted its bounded retries with `AFE: No HINT_L`.
  Startup dependency changes do not establish a fix for that hardware condition.
  Keep a USB or paired Bluetooth pointing device available for qualification.
  Code review and checksums do not prove successful installation on another Mac.
- **Speaker-filter preparation depends on the kit.** Updates 3 and 4 include the
  approved filters and do not read macOS AudioDSP. The update 2 kit has an
  `owner-audio/` folder. Its generator refuses unknown Apple AudioDSP or
  tuning hashes and unequal filter output before disk changes; macOS 26.5.1,
  26.5.2 and 26.6.2 produced identical approved outputs. Minimum macOS version
  alone does not guarantee a match. If your kit has no `owner-audio/` folder,
  those same approved filters are already included and it does not read the host
  AudioDSP. Other installer dependencies still need qualification on newer macOS.
  Fresh installation and physical playback of the rebuilt kit remain unverified.
- **Video acceleration is selective.** Brave and mpv have used hardware H264
  8-bit / VP9 Profile 0 decoding with the pinned stack. The shipped Chromium build
  and some protected movies use CPU decoding while the desktop uses GPU rendering.
  Other codecs retain software fallback. Universal hardware decoding is not claimed.
- **1080p60 windowed playback remains problematic.** An earlier comparison saw
  many more dropped frames in a normal browser window than fullscreen. Smooth
  30 fps playback does not establish that this issue is fixed.
- **USB:** in alpha.1 only the hinge-side port works. `omarchy-m4-boot` 1-12,
  from `omarchy-m4-update`, turns on the front port as well
  ([J713 front USB-C port](j713-front-usb.md)).
- **External displays are unsupported.** Thunderbolt and USB-C DisplayPort
  Alt Mode do not provide monitor output or hotplug on J713. The required DCP
  and PCIe-C/Thunderbolt display paths are not part of this release. USB
  display adapters (DisplayLink, GUD) are the exception: `omarchy-m4-evdi`
  from `omarchy-m4-update` supplies DisplayLink's kernel side, and Synaptics'
  DisplayLinkManager comes from the AUR
  ([J713 external displays](j713-external-displays.md)). No physical adapter
  has been qualified yet.
- **Incomplete hardware qualification:** camera, suspend/resume, battery life,
  Bluetooth audio, arbitrary peripherals and fault recovery are not qualified.
  Speaker protection remains enabled; do not substitute unqualified filters or
  disable the protection services. If protection stops and the speakers go
  silent, reboot; restarting it in the same session does not work. If the
  reboot leaves only "Dummy Output", run
  `pactl set-card-profile alsa_card.platform-sound HiFi`
  ([recovery](https://github.com/Kb2uka/Omarchy-M4-Native/blob/main/docs/j713-protection-startup.md#recovery)).
- **M4 updates need one connect step.** Read UPDATES.md. After it, M4 kernel
  and boot fixes arrive as signed packages through `omarchy update`. Ordinary
  rolling Arch Linux ARM and Omarchy updates remain unqualified on this hardware.

This independent integration is not endorsed by Omarchy, Basecamp, Asahi Linux,
Gravity Linux or Apple. It is supplied AS IS, without warranty. Installation or
use may cause data loss, boot failure or hardware damage. The repository's full
disclaimer and the upstream licenses remain applicable.
