# ⚠️ EXPERIMENTAL — USE AT YOUR OWN RISK

> [!WARNING]
> **This is experimental software with unfinished hardware support and known bugs.**
> Installing, updating or running it can cause **hardware damage, data loss or
> corruption, boot failures, and loss of access to macOS**. Keep backups and a
> working recovery path. You are responsible for your installation and recovery.
>
> **This is an independent community project. It is not an official release and
> is not sponsored, approved or endorsed by Omarchy / Basecamp, Asahi Linux,
> Gravity Linux, Apple, or the other upstream projects whose work it uses.**
> Upstream credits and project names do not imply endorsement or support for
> this build.
>
> **Provided “AS IS,” without warranty of any kind, express or implied. To the
> maximum extent permitted by applicable law, the maintainers and contributors
> are not liable for hardware damage, data loss, loss of use, recovery costs, or
> other damages arising from installing, updating or using this project.**
> The applicable [licenses](LICENSE.md) and
> [third-party notices](THIRD_PARTY_NOTICES.md) remain in effect.

![Experimental Omarchy M4 Air concept artwork](docs/images/omarchy-m4-native.png)

# Omarchy on Apple M4 — experimental

**Experimental on M4 Air. Very experimental on all other M4 machines.**

Only the **13-inch M4 MacBook Air (2025), Mac16,12 / J713 / T8132**, has been
hardware-tested and qualified for this project's installer profile. Other M4
Air models, Mac mini, iMac, MacBook Pro, M4 Pro and M4 Max are **not qualified**
by these results. “Very experimental” does not mean their installation works;
the J713 installer must refuse unsupported boards.

Project integration and original contributions by **studioxradio aka kb2uka**.
Built on the work of Asahi Linux, Gravity Linux, Omarchy and many other upstream
contributors. Their authorship and licenses remain theirs.

## Download and install

**[v0.1.0-alpha.1 release and installer](https://github.com/Kb2uka/Omarchy-M4-Native/releases/tag/v0.1.0-alpha.1)**,
based on **Omarchy 4.0.4**. Download the release's `download-j713.command` and
run it from macOS Terminal:

```sh
bash "$HOME/Downloads/download-j713.command"
```

The downloader reassembles the installer and verifies the release checksums.
Then follow the **[step-by-step installation guide](docs/install.md)**. This
installer starts in macOS; it is not a bootable PC ISO. GitHub's automatic
“Source code” ZIP is not the installer. Release downloads require repository
access while this repository is private.

Two attended private installations preceded this release. On the second,
keyboard, trackpad, audio and video worked after a setup stall and restart.
The final delivery changes owner-local speaker-filter preparation and browser
consent, preserving the working kernel and driver payloads. **That final
packaging has not been hardware retested; further tests were waived by the
maintainer.** See the [qualification record](docs/experimental-release.md) and
[known limitations](docs/release-limitations.md).

**Read the [update policy](docs/updates.md) before accepting system updates.**
There is no coordinated M4 update channel in this alpha. A push to `main` does
not update installed machines.

## Known shutdown issue

**Normal shutdown can hang without powering off the Air.** The owner reproduced
this on the fresh **v0.1.0-alpha.1 private build 2** installation: Omarchy closes
and shutdown messages appear, but the machine stays powered on. The same symptom
was reported on the earlier installation. Follow
[issue #24](https://github.com/Kb2uka/Omarchy-M4-Native/issues/24).

**Owner-observed workaround on this Air:** save your work, choose **Shut Down**
in Omarchy, wait until shutdown has stalled, then hold the power button to turn
the machine off. Holding the power button while the Omarchy desktop is still
running instead made the Air loop and start back up. The order matters for the
reported workaround; it is a forced power-off, not a normal shutdown fix.

The cause and a reliable shutdown fix remain unverified. Working login,
trackpad, audio and video do not establish working shutdown.

## What has been observed on the test Air

As of October 11, 2026, with the pinned experimental runtime:

| Area | Observed result and limit |
| --- | --- |
| Installed desktop | Two private installer trials reached the desktop; the second required a restart after setup stalled. The final owner-input packaging and general updates remain unqualified. |
| Keyboard, trackpad, Wi-Fi | Used in the native desktop. These observations do not qualify every peripheral or power state. |
| Bluetooth | A paired mouse reconnected after an observed reboot. General Bluetooth audio and every device are not qualified. |
| Graphics | Experimental Apple M4 GPU rendering is active. Scanout still uses simpledrm; this is not complete display-controller support or proof of physical panel timing. |
| Speakers | Protected internal-speaker playback and automatic startup have worked on the test Air. Broad acoustic calibration, all failure conditions and suspend/resume remain unqualified. |
| Browser video | H264 8-bit and VP9 Profile 0 hardware decoding have been observed with the qualified decoder/graphics stack. Ordinary 1080p30 playback works in the tested cases. This is not hardware support for every codec or protected video. |
| Normal media-player launch | A 10-second normal mpv file-open check used `vaapi-copy` with zero reported frame/decoder drops; an AV1 control reached EOF in software with one startup frame drop. `vaapi-copy` still uses the hardware decoder, with a copy into CPU-accessible memory afterward. |
| Protected movies | A tested Widevine movie used CPU decoding and played. Hardware decoding is not promised for protected content, and no license or DRM restrictions are bypassed. |
| Known video issue | A tested 1080p60 browser video dropped roughly 47% of frames in a normal window; the fullscreen comparison reported 1 drop in 1,804 frames. This geometry-dependent problem remains open. Fullscreen is a tested discriminator, not a universal fix. |
| Camera, suspend/resume, battery life, broad USB-host support | Not qualified for release. |

These are observations from one machine and specific builds, not a statement
about current upstream Asahi support or every M4 system. Good decoder counters
do not by themselves establish physical smoothness, color accuracy or audibility.
The current desktop integration restricts only the compositor's main thread to
the measured performance-core set; browser/decoder worker threads remain
unrestricted. Do not substitute the older broad session-placement experiment.

## Release scope

The download includes the installer, image, checksums, source archives and
upstream notices. It excludes Apple components, generated speaker-filter WAVs,
Widevine binaries and preset personal consent. Speaker filters are prepared
from the owner's installed macOS components and must match the expected hashes
before partitioning begins. Unrecognized inputs are refused.

Shutdown, the front USB port, camera, suspend/resume, broad hardware coverage
and coordinated updates remain open work. This alpha is not a daily-driver,
safe-update or all-M4 compatibility promise. See the
[source package guide](docs/release-source.md) for source/build boundaries.

## Source and historical research

| Path | Purpose |
| --- | --- |
| [Experimental release boundary](docs/experimental-release.md) | Current qualification, provenance and publication limits |
| [Desktop image preparation](docs/j713-desktop-image.md) | Existing offline root/owner configuration, including qualified main-thread startup placement |
| [J713 installer profile](docs/j713-installer-profile.md) | Recovery/firmware/board checks for the experimental installer profile |
| [SSD root preparation](docs/j713-ssd-root-preparation.md) | Root identity, boot payload and owner provisioning work |
| [GPU and normal-root checkpoint](docs/j713-gpu-normal-root.md) | Historical October 8 RAM-root evidence |
| [Hardware gap analysis](docs/t8132-gap-analysis.md), [ADT audit](docs/t8132-adt-audit.md) | Source-derived mappings, uncertainties and historical tests |
| [patches/](patches/), [patches-kernel/](patches-kernel/) | Changes against specific historical bases; not a patch set for any current kernel |
| [tools/](tools/), [tests/](tests/) | Source helpers and offline regression checks |
| [recon/](recon/), [SPEC.md](SPEC.md) | Capture availability and the original roadmap, not current support promises |

Read the date and stated limits in older notes. Their failed attempts and
bring-up workarounds are retained for provenance; they are not current installer
instructions. Raw hardware captures, browser profiles, credentials and native
session logs remain outside the source tree. A clone is not a complete boot kit.

## Credits and licensing

**studioxradio aka kb2uka** maintains this integration and contributed the
project-specific research, fixes and packaging. Existing copyright notices using
KB2UKA are retained. The project does not claim upstream work as original.

- [Asahi Linux](https://asahilinux.org/) and [m1n1](https://github.com/AsahiLinux/m1n1): Apple Silicon research, boot and driver foundations.
- [Gravity Linux](https://github.com/GravityLinux): M4 kernel/installer groundwork used by this experiment, building on Asahi's work.
- [Omarchy](https://github.com/basecamp/omarchy) and [omarchy-mac](https://github.com/omacom/omarchy-mac): desktop and integration work.
- Linux, Mesa, PipeWire, WirePlumber, ALSA, speakersafetyd, mpv and the other
  dependencies retain their respective contributors' copyrights and licenses.
  The historical AIC investigation also records Yureka's M4 work.

This is an independent experiment, not an official release or endorsement by
Apple or any upstream project. The artwork is concept art, not a system screenshot.
See [LICENSE.md](LICENSE.md), [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and
[LICENSES/](LICENSES/) for the mixed-license scope. No project license grants
rights to Apple firmware, macOS, Widevine, trademarks, unlicensed captures or
separately obtained assets.
