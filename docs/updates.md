# Updates for the experimental M4 build

**v0.1.0-alpha.1 has no coordinated M4 update channel.** Keep its working stack
until a release explicitly provides an upgrade path. The normal Omarchy update
command can upgrade distribution and desktop packages, but it does not currently
coordinate this project's custom kernel, boot payload, Mesa, decoder and audio
components. Compatibility after a general rolling update has not been qualified.

The image's package configuration does not pin these components as a coordinated
set. `HoldPkg` is not version pinning. Do not treat a successful update command
or a repository merge as proof that the resulting M4 stack works. Do not install
isolated older core libraries to undo an update.

Releases are published from `main`; contributions target `develop`. Pushing to
either branch does **not** update installed machines. The versioned installer
and its hashes remain tied to their release, rather than following moving main.

Future releases will state whether they offer an in-place update, its supported
starting versions and recovery steps, or require another installation. A signed
package channel and qualified Omarchy integration are open work. Until then,
follow release notes and keep a recoverable copy of your working installation.
No automatic M4 update command is promised by this first alpha.

## Shutdown fix for alpha.1

The issue #24 correction changes only the devicetree inside the installed boot
object, so alpha.1 machines can take it without reinstalling. It does not touch
the kernel, root filesystem or packages.

1. Download `j713-poweroff.dtb`
   (SHA256 `e54c77a760a5b696486f9d80c7c83ca4295d641113bb9e54d13e259863bf9b07`)
   and `tools/m4-j713-update-boot-dtb.py` from the release that provides them.
2. Inspect first; nothing is written:

   ```bash
   sudo python3 m4-j713-update-boot-dtb.py --dtb j713-poweroff.dtb
   ```

   It refuses unless `/boot/efi/omarchy/boot.bin` holds exactly one alpha.1
   poweroff devicetree followed by the initramfs payload, and unless the new
   file has the hash above. A machine that already has it reports
   `already updated`.
3. Apply, then restart once:

   ```bash
   sudo python3 m4-j713-update-boot-dtb.py --dtb j713-poweroff.dtb --apply
   ```

   The previous file is kept as `boot.bin.pre-shutdown-flag` beside it, and the
   receipt prints the rollback command.

**Rollback** if the Air does not boot: hold the power button to reach startup
options, start macOS, mount the `EFI - OMARC` partition, and copy
`omarchy/boot.bin.pre-shutdown-flag` over `omarchy/boot.bin`. From a working
Omarchy session, run the rollback command from the receipt instead.
