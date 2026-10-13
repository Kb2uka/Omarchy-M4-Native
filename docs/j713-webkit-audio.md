# Sound for WebKitGTK and GStreamer apps

## Symptom and cause

Apps that play sound through GStreamer are silent on alpha.1, even though the
speakers work for everything else. That covers every WebKitGTK app, including
Zeus Link's window. PipeWire never sees an output stream from them.

The alpha.1 image installs `gst-plugin-pipewire`, but not `gst-plugins-good`.
WebKitGTK builds its audio output from GStreamer's `autoaudiosink`, which
`gst-plugins-good` provides (with `pulsesink`, the sink it picks on a
PipeWire desktop). Without it the pipeline cannot be built:

```text
$ gst-launch-1.0 audiotestsrc num-buffers=1 wave=silence ! autoaudiosink
WARNING: erroneous pipeline: no element "autoaudiosink"
```

`webkitgtk-6.0` lists `gst-plugins-good` only as an optional dependency, so
pacman does not pull it in.

## Image fix

[`m4-desktop.packages`](../tools/m4-desktop.packages) now lists
`gst-plugins-good`, so `configure-m4-desktop.py` refuses an image without it.
Images built after this change ship it.

## Existing installs

Installing the package on alpha.1 does not work as-is. alpha.1 carries
GStreamer 1.28.7-2, and the Arch Linux ARM mirrors only carry the matching
`gst-plugins-good` until the next GStreamer release lands. After that,
`pacman -S gst-plugins-good` fails with a 404, and refreshing the package
lists to get the newer build is a partial upgrade: GStreamer 1.28.8 was built
against newer glib2 and libsoup3 than alpha.1 has. A full system update is
not qualified on this build ([updates](updates.md)).

`tools/m4-webkit-audio-plugins.py` builds only the two plugins that are
needed, `autodetect` and `pulse`, from upstream source that matches the
installed GStreamer. It installs them for the current user:

```bash
python3 m4-webkit-audio-plugins.py            # inspect, changes nothing
python3 m4-webkit-audio-plugins.py --apply    # build and install, about 30 s
```

Then close and reopen the app. What it does:

- It runs as the desktop user and refuses sudo. It changes no system files
  and no packages.
- It downloads `gst-plugins-good-<version>.tar.xz` from
  gstreamer.freedesktop.org and refuses it unless the SHA256 matches the
  pinned value for that version. Only 1.28.7 (alpha.1) is pinned. Any other
  version is refused, with advice to update the whole system instead.
- It builds with `meson` and `ninja` installed in a temporary virtual
  environment from hash-pinned wheels (`pip --require-hashes`). `gcc` and the
  GStreamer and PulseAudio headers come from the existing install
  (`base-devel` is part of the image).
- It writes `libgstautodetect.so` and `libgstpulseaudio.so` to
  `~/.local/share/gstreamer-1.0/plugins`, which GStreamer scans for every
  user process, plus a receipt with their hashes. It then checks that
  GStreamer loads `autoaudiosink` and `pulsesink`.
- It does nothing when `gst-plugins-good` is installed, and leaves any plugin
  file it did not write untouched.

`--remove` deletes the two plugins and the receipt, and refuses if either
file changed since install. Run it after a full system update that installs
`gst-plugins-good`; the inspect output says so when that applies.

## Verification on the owner's Air

On 2026-10-12, on the owner's alpha.1 Air with GStreamer 1.28.7-2:

| Check | Before | After `--apply` |
| --- | --- | --- |
| `gst-inspect-1.0 --exists autoaudiosink` | exit 1 | exit 0 |
| `gst-inspect-1.0 pulsesink` | `No such element or plugin` | found, version 1.28.7 |
| `audiotestsrc ! autoaudiosink` | `no element "autoaudiosink"` | stream linked to `audio_effect.j713-protected` |
| Second `--apply` | | `already installed`, no download |

The build took 23 seconds. The test stream was silence, and its links in
`pw-link -l` show it entering the protected speaker chain
(`audio_effect.j713-protected`, then `alsa_output.j713.RawSpeakers`), so app
sound passes through speaker protection like everything else.
