# omarchy-m4-gst-audio

`libgstautodetect.so` and `libgstpulseaudio.so` from GStreamer's
gst-plugins-good 1.28.7 (LGPL-2.1-or-later), built for the J713 alpha.1 image,
whose GStreamer 1.28.7-2 has no matching gst-plugins-good on the Arch Linux ARM
mirrors. They give WebKitGTK and other GStreamer apps an `autoaudiosink`.

Corresponding source: `gst-plugins-good-1.28.7.tar.xz` (SHA-256
`87256969c82cf3bc8574301f3e7044a90de0ac500a5a27d8ba38c4dde894dd8b`), from
https://gstreamer.freedesktop.org/src/gst-plugins-good/ and mirrored in the
`package-inputs` release of https://github.com/Kb2uka/Omarchy-M4-Native.
Build: `tools/m4-webkit-audio-plugins.py` in that repository (meson options
`-Dauto_features=disabled -Dautodetect=enabled -Dpulse=enabled`).
