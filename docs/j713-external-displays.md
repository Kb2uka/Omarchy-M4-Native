# J713 external displays

A monitor plugged straight into a USB-C port, or through a Thunderbolt dock,
shows nothing on the M4 Air under Linux, and this project cannot fix that yet
([issue #58](https://github.com/Kb2uka/Omarchy-M4-Native/issues/58)). A
**USB display adapter** works instead: a DisplayLink adapter or dock, an older
DisplayLink USB 2.0 adapter, or a GUD display. `omarchy-m4-update` installs
what the kernel side needs.

| Connection | Works? | Why |
| --- | --- | --- |
| USB-C monitor (DisplayPort Alt Mode), USB-C to HDMI/DP cable or adapter | No | Needs Apple's display coprocessor (DCP), which Linux cannot drive on M4 yet |
| Thunderbolt dock or monitor | No; whether a dock's own USB ports work over plain USB is untested | Same: Thunderbolt carries DisplayPort from the same DCP |
| DisplayLink adapter or dock (DL-3xxx, DL-5xxx, DL-6xxx chips) | Yes, after the setup below | Runs over USB; `omarchy-m4-evdi` from `omarchy-m4-update` plus Synaptics' DisplayLinkManager |
| Older DisplayLink USB 2.0 adapters (DL-1x0, DL-1x5) | Yes, no setup | The kernel's `udl` driver |
| GUD USB displays | Yes, no setup | The kernel's `gud` driver |

**A plain USB-C to HDMI or DisplayPort cable or adapter does not work, and
neither do a Thunderbolt dock's display ports** (for example an Anker Prime
Thunderbolt 5 dock). They all carry DisplayPort from the DCP. For a second
screen, use an adapter or dock whose specifications say **DisplayLink**.

## Why USB-C and Thunderbolt monitors do not work

On Apple silicon every display, internal or external, is driven by a display
coprocessor (DCP) running Apple firmware. Linux talks to that firmware through
the `appledrm` driver, and the firmware's interface changes with each macOS
release. iBoot loads the firmware that matches the Air's own macOS
(mBoot-18000.120.36, macOS 26.5.1, on the owner's Air), and the driver speaks
none of it:

- `appledrm.ko` in the J713 kernel accepts only the DCP firmware interfaces of
  macOS 12.3, 13.3 and 13.5 (its strings list `12.3.0`, `13.3.0` and `13.5.0`
  and `DCP firmware-compat %s (FW: %s) is not supported`).
- m1n1 does not recognise the Air's firmware, so the live devicetree reports
  `/chosen/asahi,os-fw-version` as `unknown` and has no `dcp` or `dcpext` node.
  The internal panel is the bootloader's framebuffer (`simpledrm`, card0) and
  the GPU is render-only (card1).
- Upstream, Asahi Linux drives M1 and M2 through the macOS 13.5 interface and
  is bringing M3 up on the macOS 14.8.3 one; on M3 "the HDMI port ... is
  currently disabled" for lack of DCP support, and M4 support is still
  experimental
  ([Asahi Linux 7.2 progress report](https://asahilinux.org/2026/08/progress-report-7-2/)).
  An M4 cannot run a macOS older than 15, so it can never use the 13.5 firmware.

A devicetree node cannot change this; the T8132 notes already record the
external-display blocks (`dispext0/1`, `dcpext0/1` and their DARTs) as
ADT-only ([T8132 ADT audit](t8132-adt-audit.md)). Native external displays need
four pieces of new work, in this order:

1. The DCP firmware interface of the macOS version the Air boots, in
   `appledrm`, proved first on the internal panel (`dcp`, `disp0`).
2. The external display pipes (`dcpext0/1`, `dispext0/1`) and their DARTs.
3. DisplayPort Alt Mode on the USB-C ports: the ATC PHY's DisplayPort lanes
   (`phy-apple-dptx` matches only T8112 and T6020 today) and the display
   crossbar (`apple-display-crossbar`, which currently uses T8112 data).
4. Thunderbolt tunnelling (`apciec0/1`, `acio0/1`), which J713 does not enable.

## DisplayLink adapters and docks

DisplayLink hardware is a USB device. The compositor draws the monitor into a
virtual display (DisplayLink's `evdi` kernel module), and Synaptics'
DisplayLinkManager program reads the frames and sends them over USB. The M4's
GPU does the drawing; the DCP is not involved.

`omarchy-m4-evdi` (from `omarchy-m4-update`; `omarchy-m4-boot` 1-13 depends on
it) installs evdi 1.15.1 built for the J713 kernel, and the matching
`libevdi`. It installs the same files as Arch's `evdi` package and provides
`evdi`, so the `displaylink` package installs on top of it. Nothing loads evdi
until DisplayLinkManager starts.

DisplayLinkManager is closed-source Synaptics software under its own licence,
so the M4 channel cannot ship it. Install it from the AUR, which downloads it
from Synaptics:

```sh
omarchy-m4-update
yay -S displaylink
sudo systemctl enable --now displaylink.service
```

Then plug the adapter into either USB-C port. Its monitors appear in Hyprland
as `DVI-I-1`, `DVI-I-2` and so on. Omarchy's default monitor rule uses
`scale = "auto"`, which gave 2 on a 1080p screen in the test below; to set a
scale for one monitor, add a line to `~/.config/hypr/monitors.lua`:

```lua
hl.monitor({ output = "DVI-I-1", mode = "preferred", position = "auto", scale = 1 })
```

Not yet checked: a physical DisplayLink adapter on this Air,
DisplayLinkManager's aarch64 build against this `libevdi`, several monitors at
once, and suspend/resume. Reports are welcome on
[issue #58](https://github.com/Kb2uka/Omarchy-M4-Native/issues/58).

## What was tested (owner's Air, 2026-10-17)

`display/evdi/bench/evdi_probe` takes DisplayLinkManager's place: it plugs a
virtual 1920x1080 monitor into evdi and reads back each frame Hyprland draws.
The run used the channel's `evdi.ko` (`c5e65a2b…`) and `libevdi` (`0f1da52d…`)
from `/tmp`, on boot 1-12 and kernel `7.1.9-m4-gpu-backport-20261008`:

- `insmod` succeeded after `modprobe drm_ttm_helper`; evdi created `card2`.
- Hyprland 0.56.2 added the monitor while running: `DVI-I-1`,
  1920x1080@60, description `OMA EVDI PROBE`, workspace 2, bar and wallpaper
  drawn. The probe's last frame was 100% non-black and showed the Omarchy
  desktop.
- With a 60 fps `mpv` test pattern (`testsrc2`, 1280x720) on that monitor,
  every second from 8 to 21 delivered 60 frames, all 60 different. Hyprland
  used 0-2.3% of a core, the whole system stayed 89% idle, and mpv used 44%.
  The second in which mpv's window opened brought 97 changed frames.
- Unplugging (`remove_all`) removed the monitor cleanly; Hyprland kept running
  on the internal panel, and `rmmod evdi` succeeded.

This proves the Air's half of a DisplayLink display: the module loads on this
kernel, Hyprland lights the monitor and draws it at 60 Hz, and frames come out
of evdi. It does not prove the USB half, which is Synaptics' software and the
adapter.

## Building evdi

See [display/evdi/README.md](../display/evdi/README.md). Both builds are
reproducible, and `packaging/channel-source-joins.json` joins them to the
upstream `v1.15.1` archive and the build script.
