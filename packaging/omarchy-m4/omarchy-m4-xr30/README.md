# J715 XR30 session selection

Package integration installs `tools/m4-xr30.py` as executable
`/usr/bin/m4-xr30`, and `90-m4-xr30.conf` as
`/usr/lib/systemd/user/wayland-wm@hyprland.desktop.service.d/90-m4-xr30.conf`.
The separately pinned GL-only Mesa prefix lives at `/usr/lib/omarchy-m4/mesa`.
No install or upgrade hook may restart a compositor. The private experimental
per-user `95-xr30.conf` is not a package file; its local migration is separate.

At the next session start, the wrapper requires the exact DT compatible token
`apple,j715`, one connected DRM connector whose device is bound to
`simple-framebuffer`, and that device's `chosen` DT node with compatible token
`simple-framebuffer` and format `x2r10g10b10`. Disabled nodes do not qualify.
The prefix must contain `lib/`, `lib/gbm/`, `lib/dri/` and
`share/glvnd/egl_vendor.d/50_mesa.json`. Missing or unreadable qualification
data, missing prefix contents, or a global hold preserve the incoming
environment and launch the ordinary compositor.

Qualified sessions prepend the private `lib/` to `LD_LIBRARY_PATH` and select
the private GBM backend, DRI drivers and EGL vendor manifest with
`GBM_BACKENDS_PATH`, `LIBGL_DRIVERS_PATH` and
`__EGL_VENDOR_LIBRARY_FILENAMES`. Selection applies to `start-hyprland` and
its inherited process environment; this helper does not change the systemd
user manager's environment. It does not spoof GL versions, select Vulkan
drivers, alter `/opt/m4-mesa`, or change GPU checksum files.

Eligible sessions also retain `ASAHI_MESA_DEBUG=nocompress`, preserving any
other debug flags. The source-pinned candidate crashed while preparing a color
target without that flag, then passed the same full-panel 32-buffer test with
the flag. Compressed color-target rendering is not qualified.

The package supplies XR30 capability, not a monitor policy. The owner's
existing monitor rules determine bit depth, mode, scale, position, transform
and all other behavior. For example, an owner choosing 10-bit output can add
`bitdepth = 10` to their existing Lua `hl.monitor({...})` table. The package
does not add that setting, create another monitor rule, or contact a running
compositor. An existing 8-bit choice remains 8-bit.

## Hyprland 0.57 format quirk

Library selection alone does not fix the confirmed J715 simpledrm regression:
the default `quirks:skip_non_kms_dmabuf_formats=true` leaves only opaque
advertised formats, and Firefox fails EGLConfig selection. A persistent Lua
setting of `false` in the loaded `looknfeel.lua`, with full `true` then `false`
configuration reloads, restored alpha EGL configs in the running test session.
A fresh session rendered Firefox and the bar and completed a desktop capture.
Extended capture and shell rendering still have separate unresolved failures;
this is not full graphics-stack qualification.

Release 4 delivers the quirk automatically through `omarchy-m4-update`.
The package install/upgrade hook mirrors the lid/backlight migrations: it
drops to each login owner's UID and groups before accessing configuration.
On a qualified J715 native XR30 simpledrm panel, it appends an idempotent block
to an existing regular `looknfeel.lua` explicitly loaded by the owner's
Omarchy `hyprland.lua`. Missing, symlinked or unrecognized configurations are
left alone; existing owner quirk settings in either file are preserved.
The block gates the option on the running Hyprland version (0.57 or later),
so an older or unknown version does not receive a nonexistent option, even
after a downgrade. No restart or explicit reload is issued; automatic config
reloads remain possible. A refusal reports its reason without failing the
upgrade. Check the result at the next graphical login.
See [qualification and remaining gates](../../../docs/j715-xr30.md#hyprland-057-format-quirk).
The compiler/probe checks do not qualify Hyprland 0.57 client compatibility.

## Controls

`m4-xr30 --status` (also the default) reports selection for a new login, not
whether the current session uses XR30. The wrapper logs the same decision to
the session journal before executing `/usr/bin/start-hyprland`.

As root, `m4-xr30 --disable` creates `/var/lib/omarchy-m4/xr30/hold`;
`m4-xr30 --enable` removes it. Both operations are idempotent and take effect
at the next login. They do not stop or reconfigure the current session.
Enabling removes the hold, but never bypasses hardware or prefix checks.

Build packages on a native aarch64 host. Local deterministic validation is:

```sh
python3 -m unittest discover -s tests -p test_m4_xr30.py -v
```

Hardware qualification still needs a new-login check of loaded libraries,
the requested output format, unchanged monitor behavior, and disable/enable
across logins. No hardware result is implied by the unit tests.
