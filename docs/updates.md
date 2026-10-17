# Updates for the experimental M4 build

M4 kernel, devicetree and boot fixes ship as **signed packages** from this
project's `[omarchy-m4]` repository. Connect each Air once; after that, run
`omarchy-m4-update` to get them, and the boot object is updated for you.
Nothing is reinstalled. How it works: [package-channel.md](package-channel.md).

## Connect once

Every v0.1.0-alpha.1 Air needs this step once, including a fresh installation
and an Air that already has update 1. Join Wi-Fi first (`nmtui`). Open a
terminal, paste this one line, press Enter, and type your password when
asked:

```bash
curl -fsSL --retry 5 --retry-all-errors https://github.com/Kb2uka/Omarchy-M4-Native/releases/download/packages/m4-connect.sh | bash
```

`m4-connect.sh` downloads the setup script, retrying if Wi-Fi drops a lookup,
and refuses it unless its SHA256 matches the one written into `m4-connect.sh`
when both were published. It then runs the setup script once and deletes its
temporary files. It must print `omarchy-m4-setup.py: OK`. The last lines then
show one of:

- `"status": "connected; restart once to apply the update"`: restart
  the Air once. This happens on an Air without update 1; it now gets the
  shutdown fix.
- `"status": "connected; future M4 fixes arrive with omarchy-m4-update"`: done,
  nothing to restart. This is the normal result on an Air that has update 1.
- `omarchy-m4-setup: refused: …`: the reason follows. If the download or
  installation failed, `pacman.conf` is put back as it was. In every case your
  Air keeps booting exactly as before. Fix the stated cause (for example free
  space on the EFI partition) and run the block again, or report it in
  [issue #49](https://github.com/Kb2uka/Omarchy-M4-Native/issues/49).

### Step by step instead

To download and look at each file before anything runs, use this block. It
does the same as the line above. Run it in one terminal; line 2 sets `base` for
the lines after it.

```bash
cd ~ && rm -rf m4-updates && mkdir m4-updates && cd m4-updates &&
base=https://github.com/Kb2uka/Omarchy-M4-Native/releases/download/packages &&
curl -fL -O "$base/omarchy-m4-setup.py" -O "$base/SETUP-SHA256SUMS" &&
sha256sum -c SETUP-SHA256SUMS &&
sudo python3 omarchy-m4-setup.py --apply
```

The first three lines only download; `sha256sum -c` must print
`omarchy-m4-setup.py: OK`. Running `sudo python3 omarchy-m4-setup.py` without
`--apply` shows what it would do and changes nothing.

What it does:

- trusts the channel's signing key
  (`578DF78177CF6DF29B888B8C9404F3CC90797A50`);
- adds `/etc/pacman.d/omarchy-m4.conf` and one `Include` line above `[core]`
  in `/etc/pacman.conf`, keeping the old file as `/etc/pacman.conf.pre-omarchy-m4`;
- installs `omarchy-m4-keyring`, `omarchy-m4-boot` and `linux-m4-omarchy`.

It refreshes only the M4 repository's database, so it is **not** a system
upgrade. The kernel it installs is the one your Air already boots. It never
touches the browser, Widevine, video, graphics, speaker or audio files, the
boot options that the trackpad needs, or your Apple firmware.

## Receiving fixes

A connected Air checks the M4 repository once a day (read-only, nothing is
installed) and shows a **"M4 update available"** desktop notification that
lists the new M4 package versions. You get one notification per new release.
The check starts right after the connect step (otherwise at your next login).
This is separate from Omarchy's own update indicator, which only watches the
`omarchy` package. Check by hand at any time with `omarchy-m4-update-check`.

Run **`omarchy-m4-update`** and type your password. It refreshes only the M4
repository and installs or upgrades only the M4 packages, including any new
one, the same way the connect step does; nothing else on the system changes.
(`omarchy-m4-boot` 1-5 adds the command. On an Air with an older boot package,
run the connect line once instead.) `omarchy update` also installs M4 fixes,
but it updates the whole system as well, which is not qualified on this
hardware (below). When an M4 fix is included, pacman shows
`Updating the M4 boot object (boot.bin)...` followed by
`"status": "updated; takes effect at next boot"`. Restart once. If nothing in
the boot object changed, it reports `"status": "current"`.

The composer only installs kernels and devicetrees this project has qualified
for the installed audio and video stacks. Anything else is refused with
`m4-boot-compose: refused: … boot.bin was not changed.`, and the Air keeps
booting what it booted before. After fixing the stated cause, finish the update
with `sudo m4-boot-compose --apply` (or run the connect block again).

Pushing to `main` or `develop` does not update installed machines; only
packages published to the channel do. The rest of a general rolling update,
from Arch Linux ARM and Omarchy, is still **not qualified** on this hardware.
Do not install isolated older core libraries to undo an update.
`omarchy refresh pacman` and Omarchy channel switching replace `/etc/pacman.conf`
with x86 defaults and are not supported on M4. If one was run, run the connect
step again.

## Check and roll back

See the boot object's state at any time (nothing is written):

```bash
sudo m4-boot-compose
```

Undo the last boot object change, then restart:

```bash
sudo m4-boot-compose --rollback --apply
```

The previous file is kept as `/boot/efi/omarchy/boot.bin.previous`. **If the
Air does not boot:** hold the power button to reach startup options and start
macOS. Mount the `EFI - OMARC` partition and copy `omarchy/boot.bin.previous`
over `omarchy/boot.bin`. A rollback lasts until the next M4 package update
composes the boot object again; report the problem in issue #49 before
updating again.

## Installer update 4

`v0.1.0-alpha.1-update.4` is the **latest installer kit**, for **new
installations only**. No fresh installation from this exact kit has been
confirmed yet; update 3 remains available. An Air that is already installed
needs nothing new from it: `omarchy-m4-update` brings the same GPU module as
`omarchy-m4-gpu` 1-3, and the stage 1 loader is update 3's.

- **The GPU starts on every 13-inch M4 Air**
  ([#57](https://github.com/Kb2uka/Omarchy-M4-Native/issues/57),
  [#118](https://github.com/Kb2uka/Omarchy-M4-Native/pull/118)). The image
  runs GPU module `de0337a7…` in place of alpha.1's `80cf3669…`. It accepts
  each Air's own GPU power limit up to 24800 mW and the base model's
  eight-core GPU, so a fresh install reaches the desktop on its first boot
  without first connecting to M4 updates. `sudo m4-gpu-module --rollback`
  still restores alpha.1's module.
- Everything else is as in update 3.

The kit lands in `~/Downloads/Omarchy-M4-v0.1.0-alpha.1-update.4/installer/`;
use that directory in the installation guide.

## Installer update 3

`v0.1.0-alpha.1-update.3` was the previous installer kit, for **new
installations only**; update 4 replaces it. An Air that is already installed
needs nothing from it: everything it adds to the system also arrives through
`omarchy-m4-update`, except the stage 1 loader below.

- **Speaker filters ship in the image**
  ([#111](https://github.com/Kb2uka/Omarchy-M4-Native/pull/111)). The kit no
  longer reads the Mac's AudioDSP before partitioning, so a macOS build the
  filter step did not know (such as macOS 27 betas) is no longer refused there.
  Other installer dependencies are not yet qualified on macOS 27.
- **m1n1 stage 1 no longer resets ANS on an M4**
  ([#82](https://github.com/Kb2uka/Omarchy-M4-Native/issues/82),
  [#110](https://github.com/Kb2uka/Omarchy-M4-Native/pull/110)), which stopped
  some Airs on the m1n1 logo at every boot. An installed Air gets this loader
  only through the 1TR `kmutil` step in
  [j713-m1n1-issue82-ans-reset.md](j713-m1n1-issue82-ans-reset.md).
- **First boot connects to M4 updates by itself**
  ([#74](https://github.com/Kb2uka/Omarchy-M4-Native/pull/74)), so the connect
  step below is not needed, and the image carries the GPU start journal fix
  ([#108](https://github.com/Kb2uka/Omarchy-M4-Native/pull/108)) and the
  shutdown devicetree of update 1.

The kit lands in `~/Downloads/Omarchy-M4-v0.1.0-alpha.1-update.3/installer/`;
use that directory in place of update 2's in the installation guide.

## Installer update 2 (macOS 26.6.2)

`v0.1.0-alpha.1-update.2` is a refreshed installer kit for **new installations
only**. The original alpha.1 kit stops before any partition change on macOS
26.6.2 (25G83), because that release ships a different AudioDSP build than the
speaker-filter step knew. Update 2 qualifies it
([#83](https://github.com/Kb2uka/Omarchy-M4-Native/pull/83)). The system image
is the same alpha.1 image, byte for byte. An Air that is already installed needs
nothing from it.

## Shutdown fix for alpha.1

**Connecting to M4 updates (above) already applies this fix**; the composer
produces the same boot object, byte for byte. This standalone update remains for
anyone who prefers not to connect. **What it fixes:** Shut Down restarting the
Air ([issue #24](https://github.com/Kb2uka/Omarchy-M4-Native/issues/24)).
**Reinstall needed:** no.

### Quick version

Open a terminal on the Air, paste this whole block, press Enter, and type your
password when asked:

```bash
cd ~ && rm -rf m4-update-1 && mkdir m4-update-1 && cd m4-update-1 &&
base=https://github.com/Kb2uka/Omarchy-M4-Native/releases/download/v0.1.0-alpha.1-update.1 &&
curl -fL -O "$base/j713-poweroff.dtb" -O "$base/m4-j713-update-boot-dtb.py" -O "$base/SHA256SUMS" &&
sha256sum -c SHA256SUMS &&
sudo python3 m4-j713-update-boot-dtb.py --dtb j713-poweroff.dtb --apply
```

- `"status": "updated; takes effect at next boot"`: restart the Air. Done.
- `"status": "already updated"`: you already have it.
- `FAILED` or any error: stop. Nothing is written unless every check passes. Please
  [open an issue](https://github.com/Kb2uka/Omarchy-M4-Native/issues).

### What it does

Update 1 (`v0.1.0-alpha.1-update.1`) changes only the devicetree inside
`/boot/efi/omarchy/boot.bin`. It does not touch the kernel, root filesystem,
packages or your files.

`sha256sum -c` checks both downloads against the release checksums. The
devicetree's SHA256 is
`e54c77a760a5b696486f9d80c7c83ca4295d641113bb9e54d13e259863bf9b07`. The updater
refuses unless `boot.bin` holds exactly one alpha.1 poweroff devicetree followed
by the initramfs payload, and unless the new file has that hash. It keeps the
previous file as `boot.bin.pre-shutdown-flag` and prints the rollback command.
To look without changing anything, run the last line without `--apply`.

After the restart, `journalctl -k -b | grep macsmc-reboot` should show only
`Handling reboot and poweroff requests via SMC`, with no `Missing NVMEM cell`
lines.

**Rollback** if the Air does not boot: hold the power button to reach startup
options, start macOS, mount the `EFI - OMARC` partition, and copy
`omarchy/boot.bin.pre-shutdown-flag` over `omarchy/boot.bin`. From a working
Omarchy session, run the rollback command from the receipt instead.
