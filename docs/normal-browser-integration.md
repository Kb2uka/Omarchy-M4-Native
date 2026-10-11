# Browser startup on J713

The private installer includes Brave and Chromium. Brave's ordinary launch path
requests the qualified M4 video stack when its current-boot readiness and device
checks pass. Software decoding remains available for unsupported media and when
the hardware stack is unavailable. The shipped Arch Linux ARM Chromium
153.0.8010.36 build disables VA-API and enables a different V4L2 backend. It uses
software video decoding with GPU rendering; forcing its direct V4L2 decoder
failed the native playback test. VA-API flags cannot enable an omitted backend.
A future hardware-capable Chromium package requires separate qualification.
Hardware decoding is a playback-path result; a GPU-rendered desktop alone does
not prove it.

Install `tools/brave-m4-hardware.py` as `/usr/local/bin/brave`,
`/usr/local/bin/chromium`, and `/usr/local/bin/brave-m4-hardware`. The first two
retain the browser's normal profile and explicit profile arguments. The last
retains the existing separate Brave-Browser-M4 profile. Chromium continues through
its packaged launcher; system and owner feature flags are merged before the
decoder switches are supplied. Browser-specific libraries are scoped to the
launched process.

The packaged Chromium desktop entry uses an absolute `/usr/bin/chromium` command.
A fresh-owner desktop entry therefore must be derived from that exact packaged
file, changing its three Exec commands to `/usr/local/bin/chromium`. Install the
result in the pending image's `/etc/skel/.local/share/applications/` so both XDG
launchers and Omarchy's browser shortcut find it. Do not overwrite a customized
existing owner's desktop entry. Brave's stock `Exec=brave` resolves through
`/usr/local/bin` in the verified session PATH.

Protected-video setup is separate from hardware decoding. Install
`setup-brave-widevine.py` and `bootstrap-brave-widevine.py` together under
`/usr/lib/omarchy/`. The bootstrap runs only for a matching prior owner-consent
request, before the selected browser starts, and leaves an incomplete request
pending for a later closed-browser launch. Download failures do not prevent
ordinary browser use. See [the component setup contract](brave-widevine.md).
The private owner's authorization must not be treated as consent from future
public-installer users. Component binaries and browser profiles are excluded
from the installer.

Validation includes both desktop/shortcut entry points, default and explicit
profiles, system/owner flags, hardware fallback, first-owner component setup,
actual EME support and native playback. A replacement installer remains private
until the attended clean-install check passes.
