# J713 camera notch: an opt-in full-panel desktop

The J713 panel is 2560x1664. The camera sits in a cutout at the top centre, and
by default the desktop uses only the 2560x1600 below it, so nothing is drawn
behind the camera ([issue #89](https://github.com/Kb2uka/Omarchy-M4-Native/issues/89)).
An owner can opt in to the whole panel, as on macOS: the desktop gains the
64 rows beside the cutout, fullscreen video reaches the top edge, and the
Omarchy bar moves its center widgets to the left and right of the camera.

```sh
sudo m4-notch --show    # from the next restart
sudo m4-notch --hide    # back to 2560x1600 from the next restart
m4-notch                # what this boot shows, and what the next boot will
```

The default does not change. An Air that never runs `--show` boots the same
loader bytes as before and its desktop files are never touched.

## Why it needs a bootloader change

On the Air, Linux is handed one framebuffer and `simpledrm` offers exactly one
mode, so no compositor or kernel setting can reach rows it was not given:

```text
/sys/firmware/devicetree/base/chosen/framebuffer@103e1c8c000
  width 2560  height 1600  stride 10240  format x2r10g10b10
/sys/class/drm/card0-Unknown-1/modes: 2560x1600
```

iBoot sets up the whole 2560x1664 panel ([first boot](first-boot-runbook.md):
`fb 2560x1664`). m1n1's stage 2, the loader at the start of
`/boot/efi/omarchy/boot.bin` that boots Linux, crops it in
`get_notchless_fb()` (`src/kboot.c`): when the ADT `/product` node has
`partially-occluded-display`, it moves the framebuffer base down by 64 rows and
hands Linux 2560x1600. The installed stage 2 carries that function's
`display: Hiding notch` message. m1n1 has no option to skip it. On M1-M3
Macs `apple_dcp.show_notch=1` asks the display driver for the full mode; J713
has no display driver yet, so that parameter does nothing here.

## The notch stage 2

[`display/j713/m1n1-show-notch.patch`](../display/j713/m1n1-show-notch.patch)
replaces the crop: the function still detects the notch and logs
`display: Showing notch, 2560x1664 (stock m1n1 hides 64 rows, 16:10)`, but
leaves the base and height as iBoot set them. Nothing else in m1n1 changes.
With the full height, m1n1 also skips the `apple,notch-height` note it writes
only for a cropped framebuffer.

[`display/j713/build-notch-loader.sh`](../display/j713/build-notch-loader.sh)
builds it from alpha.1's published stage-2 source
(`m1n1-stage2-source.tar.gz`, `c3945179…`) in `m4-gravity-builder:20261008`,
as root, the way alpha.1 was built (Rust embeds the `/root/.cargo` paths). It
first rebuilds the unpatched source and requires alpha.1's shipped stage 2,
`3a3f7a64…`, byte for byte. The notch stage 2 is then
`f2417a74d13838004847a6fbdf946b524005301b88b5a403b1e79cb36d0ec7ca`
(0x110000 bytes, tag `air-c9ef56f-gpu-stage2-20261008-notch1`), and two clean
builds gave the same bytes. It is pinned in the `package-inputs` release and in
`channel.json` (`notch_loader`).

`display/j713/m1n1-base/` holds the unmodified MIT `src/kboot.c`, m1n1's
`LICENSE`, its README (whose License section names every embedded part) and its
`3rdparty_licenses/`. `SHA256SUMS` pins them to alpha.1's published
`m1n1-stage2-files.json`. The boot package installs those notices under
`/usr/share/licenses/omarchy-m4-boot/m1n1/`.

## The switch

`omarchy-m4-boot` 1-12 delivers `m4-notch`, the notch stage 2 at
`/usr/lib/omarchy-m4/m1n1-stage2-notch.bin` and a login unit, through
`omarchy-m4-update` like every M4 update. Installing it changes nothing.

`sudo m4-notch --show`:

- refuses anywhere but a J713, and while a package transaction runs. While it
  writes it holds pacman's own database lock (`/var/lib/pacman/db.lck`), so the
  composer's pacman hook cannot rewrite `boot.bin` at the same time. If
  `m4-notch` is killed while writing, that lock file can be left behind; pacman
  and `m4-notch` then both refuse, and removing `/var/lib/pacman/db.lck`
  (with no pacman running) clears it, as pacman's own message says;
- parses `boot.bin` with `m4-boot-compose`'s own parser, so a file the composer
  does not recognise is refused;
- replaces the stage 2 (the first 0x110000 bytes) only when it is alpha.1's
  (`3a3f7a64…`) or the published #82 test build (`7bc78b91…`, `-dctx1`), and
  only with a staged file that matches the pin;
- checks the result keeps the boot arguments, kernel, devicetree and firmware
  payload byte for byte;
- keeps the whole old file as `/boot/efi/omarchy/boot.bin.pre-notch`, then
  writes a verified copy and renames it over `boot.bin` (the composer's writer).

`sudo m4-notch --hide` splices the stage 2 from `boot.bin.pre-notch` back in,
so a kernel or devicetree update installed while the notch was shown is kept,
then removes the backup. When the notch is already hidden it only removes a
leftover backup, for example after the macOS recovery copy below. Both directions take effect at the next restart and
leave `boot.bin` unchanged when it is already in the requested state.

`m4-boot-compose` carries the stage 2 byte for byte, so package updates keep
the owner's choice. Its own `--rollback` refuses a `boot.bin.previous` made
before a `--show` or `--hide`, because the loader differs; use `m4-notch`.

**If the Air does not start after `--show`:** start up from macOS, mount the
`EFI - OMARC` partition, and copy `omarchy/boot.bin.pre-notch` over
`omarchy/boot.bin`, as for the [update rollback](updates.md).

## The bar around the cutout

`omarchy-m4-notch.service` is a user unit enabled globally by the package. At
each login (`graphical-session.target`) it runs `m4-notch --desktop` as the
desktop user, which follows what this boot shows:

- **Whole panel (2560x1664):** in `~/.config/omarchy/shell.json` it inserts an
  `omarchy.spacer` right after the bar's center anchor (the clock by default)
  and makes the spacer the anchor. Omarchy pins the anchor to the exact centre
  and flanks the rest of the center list around it, so the widgets before it
  sit left of the camera and the ones after it right of it. The spacer is the
  cutout's width at the session's scale plus 8 px on each side (173 at
  Hyprland's automatic scale 2). In `~/.config/omarchy/shell.toml`, Omarchy's
  machine-level override, it sets `[bar] size-horizontal` so the bar is as tall
  as the 64-row strip (32 at scale 2 and the default font size), so the camera
  sits inside the bar as it does in the macOS menu bar.
- **Cropped (2560x1600):** it removes exactly what it added and restores the
  anchor. A `shell.json` it had to create from Omarchy's defaults is removed
  again if the owner did not change it.

Every entry and line it writes carries an `m4-notch` mark, and Omarchy keeps
those marks when it rewrites `shell.json` itself. It never changes an
unmarked `size-horizontal`, refuses a center list that already has a spacer
(that spacer would take the anchor) and then leaves the bar height alone too,
keeps each existing file's mode (new files are 0600), and changes nothing on a
cropped boot that has no marks.

![The Omarchy bar split around the cutout on a 2560x1664 output](images/j713-notch-bar.png)

Measured on the owner's Air, on a 2560x1664 Hyprland output beside the panel:
Hyprland's automatic scale is 2, so the panel is 1280x832 logical and needs no
`debug:disable_scale_checks`; the bar is 32 px tall and the spacer centred.
The red outline above is the cutout's size from AppKit, drawn on the capture.

### The cutout's size

The ADT has no cutout geometry. AppKit reports the J713's notch as
179x32 pt at the default 1470x956 (13-inch M4 Air) and an M2 Air's, the same
panel, as 209x38 pt at 1710x1112: both about 312x56 px on the panel, centred.
`m4-notch` uses 314 px.

## Limits

- **Keep the bar at the top.** Only the bar covers the strip. With the bar on
  another edge or hidden, windows can reach the rows behind the camera.
  Hyprland's `reserved` area is not used: it adds to the bar's own space and
  pushes the bar below the strip.
- The pointer can go behind the camera, as on macOS.
- Fullscreen windows use the whole panel, the point of the option.
- The early boot console also draws the top rows, so its first line may sit
  behind the camera.
- Text sizes from 9 to 20 px keep the bar at least as tall as the strip; the
  size is recomputed at each login.
