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

In Zeus Link this shows as a Mute button that stays on **▶ Unmute**: the
station engine is not muted and keeps sending audio, but the window's audio
output never starts.

WebKitGTK also plays sound from inside a bubblewrap sandbox. The sandbox sees
the system plugin directory (`/usr/lib/gstreamer-1.0`) and any directory named
in `GST_PLUGIN_PATH`, but not the user plugin directory
`~/.local/share/gstreamer-1.0/plugins`. Plugins installed there work for
`gst-launch-1.0` and other unsandboxed apps, and stay invisible to WebKitGTK
unless `GST_PLUGIN_PATH` names that directory.

## Image fix

[`m4-desktop.packages`](../tools/m4-desktop.packages) now lists
`gst-plugins-good`, so `configure-m4-desktop.py` refuses an image without it.
Images built after this change ship it.

## Existing installs

**Connected to M4 updates? You get this automatically.** The connect step
installs `omarchy-m4-gst-audio` (an Air connected before it existed gets it by
running the connect line once more), which puts the same two plugins in the system
plugin directory that every app and the WebKitGTK sandbox can see; restart the
app. If you ran the helper below earlier, it now reports `not needed`; run it
once with `--remove` to delete its own copies and session file. The helper
remains for an Air that is not connected.

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

Then log out and back in, and start the app again. What it does:

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
  unsandboxed user process, plus a receipt with their hashes. It then checks
  that GStreamer loads `autoaudiosink` and `pulsesink`.
- It writes `~/.config/environment.d/60-m4-webkit-audio.conf`, which adds that
  directory to `GST_PLUGIN_PATH` for the desktop session, so WebKitGTK's
  sandbox can see it. systemd reads the file when the user's service manager
  starts, which is at login unless lingering is enabled for the user
  (`loginctl show-user "$USER" -p Linger`); with lingering, restart the
  computer instead of logging out.
- On an install made before the session file existed, `--apply` adds only the
  session file, with no download or build.
- It does nothing when `gst-plugins-good` is installed, and leaves any plugin
  or session file it did not write untouched.

`--remove` deletes the two plugins, the receipt and the session file, and
refuses if either plugin changed since install. A session file with other
contents is not this tool's and is left in place. Run it after a full system update that installs
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

On 2026-10-13, the same Air with the plugins installed, running Zeus Link
2.0.48 with station engine 2.0.50:

| Check | Plugins only | With the session file |
| --- | --- | --- |
| Zeus Link Mute button | stays on **▶ Unmute** | **■ Mute** |
| `/proc/<WebKitWebProcess>/maps` | no `libgstautodetect.so`, no `libgstpulseaudio.so` | both loaded |
| Plugin directory inside the WebKit sandbox | `No such file or directory` | present |
| PipeWire clients | no WebKit client | `WebKitWebProcess` |
| PipeWire streams | none from Zeus Link | `WebKit` stream into `J713 protected speakers`, active |

The station engine reported `muted=false sending=true` in both columns. The
check stops at the protected sink: speaker protection was down during this
run, so nothing reached the speakers themselves.

For the login path, the systemd environment generator resolves the session
file to `GST_PLUGIN_PATH=/home/<user>/.local/share/gstreamer-1.0/plugins`.
The desktop session in this run predated the file, so the same value was set
in the running Hyprland session (`hyprctl eval 'hl.env(...)'`) before Zeus
Link was started the way the app menu starts it
(`uwsm-app -- zeus-link-launcher`, in an `app-*.scope`). Its WebKit process
received the value and loaded both plugins.
