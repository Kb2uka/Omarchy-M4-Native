# Preparing a successor private J713 installer

The successor tools join explicit, verified inputs for the existing J713-only
installer. They do not collect firmware, download Widevine, operate an installed
machine, alter partitions, or authorize a release. Version `0.1.0-alpha.1`,
private build 2, remains subject to the owner's fresh-install checks.

`tools/prepare-j713-candidate.py` consumes the frozen first kit, the reviewed
audio default-policy successor package, the exact retry-corrected kernel, a
closed browser code bundle, and the exact shipped Chromium desktop. Its output
must be a new directory. Both component and integrated source commits are
checked against the actual supplied browser bytes. Assembly helper/source-overlay
bytes must also match the supplied integrated commit; an uncommitted substitute
cannot be labeled as that commit.

The browser bundle has `manifest.json` with `schema: 1`, `source_revision` (full
component commit), and `files`, mapping each destination to `sha256`, `size`, and
numeric `mode`. The only five destinations are:

- `/usr/local/bin/brave`, `/usr/local/bin/brave-m4-hardware`, and
  `/usr/local/bin/chromium`: identical reviewed launcher source, mode 0755.
- `/usr/lib/omarchy/setup-brave-widevine.py` and
  `/usr/lib/omarchy/bootstrap-brave-widevine.py`: reviewed source, mode 0644.

Files reside at `root/<destination>` beneath the bundle. Extra files, symlinks,
CDM binaries and browser profile content are refused. The bundle manifest is
independently pinned on the command line. The integrated commit may differ from
the component commit, but the five files must match both.

```sh
python3 tools/prepare-j713-candidate.py \
  --baseline-kit /private/baseline/kit \
  --audio-package /private/approved-audio \
  --audio-manifest-sha256 APPROVED_AUDIO_SHA256 \
  --browser-bundle /private/approved-browser \
  --browser-manifest-sha256 APPROVED_BROWSER_SHA256 \
  --kernel /private/approved/Image.gz \
  --chromium-desktop /private/shipped/chromium.desktop \
  --chromium-desktop-sha256 SHIPPED_DESKTOP_SHA256 \
  --source-revision INTEGRATED_FULL_COMMIT \
  --private-owner-consent --output /private/candidate2/kit
```

The audio join accepts only the pinned prior manifest, unchanged existing
payloads and the two protected-default policy additions. The video join retains
all prior payloads and derives replacement permissions from the pinned prior
manifest. The kernel prerequisite and installed manifest change together.
A customized or foreign file is never accepted merely because its destination
appears in the update map.

The prior pending image has the reviewed dedicated-launcher first-owner default.
After all component preflights pass, the successor changes only that known line
to `brave-browser.desktop`. The normalized full provisioner hash and ordinary
Brave desktop hash must match the retained image; customized or original stock
Chromium defaults refuse before any component writes. Reapplying is idempotent.
The ordinary icon and default shortcut therefore use the normal Brave profile,
while the optional dedicated launcher remains separate. The owner-free pending
image guard excludes existing installations from this transition. A Chromium
desktop override is generated under `/etc/skel/.local/share/applications` by
changing only the executable token in every original `Exec` line; names,
translations, actions and other fields are retained. The stock desktop remains
unchanged. A hash mismatch in the image's stock desktop refuses application.

`--private-owner-consent` is specific to the authorized owner's private kit.
It seeds three mode-0600 requests outside profiles, for `Brave-Browser`,
`Brave-Browser-M4`, and `Chromium`. It does not distribute a CDM or accept terms
for another person. On a later ordinary closed-browser launch the reviewed
helper may download and validate the component; offline, busy or failed setup
leaves the request pending. No marker is reseeded at each login. Public packaging
must obtain each owner's choice rather than adopt this private consent.

## Offline apply and final packaging

The generated `kit/prepare-image.py` retains the original loop-backed, pending,
owner-free ext4 checks. It validates desktop, audio, video and mpv before applying
any component. Audio's explicit previous-manifest argument enables only the
reviewed transition. Use its `verify`, `apply` and `check` commands as documented
by `--help`, with a pinned kit manifest and explicit loop backing image.
Do not use it against an installed root or add a live-apply exception.

`tools/package-j713-candidate.py` replaces the former private finalizer's
hardcoded source and payload hashes. It takes a new output directory, verified
baseline delivery, integrated source tree, checked unmounted image, pinned kit,
and two separately pinned coordinator receipts. It refuses an attached image,
old root identity, changed image, unqualified native receipt or missing image
gate. The native receipt is exactly:

```json
{
  "schema": 1,
  "kind": "j713-private-native-qualification",
  "kit_sha256": "KIT_SHA256",
  "source_revision": "INTEGRATED_FULL_COMMIT",
  "native_qualified": true,
  "passed": {"kernel-reboot-trackpad": true, "audio-clean-default": true},
  "browsers": {
    "brave": {"passed": true, "decoder": "ACTUAL_DECODER_NAME", "hardware_accelerated": true, "evidence_sha256": "64_HEX_DIGITS"},
    "chromium": {"passed": true, "decoder": "ACTUAL_DECODER_NAME", "hardware_accelerated": false, "evidence_sha256": "64_HEX_DIGITS"}
  }
}
```

The image receipt contains `schema: 1`, `kit_sha256`, `root_sha256`, `root_uuid`
and `passed`, a dictionary with exactly `offline-apply-check`, `owner-free`,
`locale-utf8`, `sudo`, `base-packages`, and `e2fsck` as keys. Each value must be
JSON `true`; a list, false, integer, or string result is refused. These receipts record actual coordinator
observations; generating a JSON file does not perform or substitute for the
checks. Both browser playback results are mandatory even if the overall native
approval is true. Each records the observed decoder, hardware-acceleration status
and a pinned evidence file. Software fallback can qualify when explicitly
observed; the final IMAGE/KIT metadata preserves that distinction. A pending or
failed result refuses final packaging. The
finalizer also reads ext4 identity/clean-check state and verifies the
archive's full CRC, extracted root SHA and each portable boot member. The
existing actual macOS-installer parser readback remains a separate final gate.

The finalizer creates a new OS ZIP and integration-source archive, updates
installer root UUID and boot pins, derives IMAGE/KIT source revisions from the
verified kit, and regenerates outer checksums. It preserves corresponding
baseline source archives/notices and adds the committed input-retry source
patch/docs and current notices inside the new kit source archive. It never
copies a device-personalized boot object or owner profile into the delivery.
The resulting manifest continues to say `fresh_install_qualified: false`.

Full image assembly, native behavior and a fresh-owner installation are separate
from the filesystem fixture tests. Do not describe a passing fixture or assembled
unqualified kit as a verified new installation.
