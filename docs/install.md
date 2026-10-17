# Install v0.1.0-alpha.1

This experimental, independent build supports only the **13-inch M4 MacBook Air
(2025), Mac16,12 / J713 / T8132**. It is not endorsed by Omarchy, Asahi, Gravity
or Apple. Read the repository warning and KNOWN-LIMITATIONS.md before proceeding.
Installation changes partitions and boot policy. Keep a backup and macOS recovery.

This is a **macOS-launched installer**, not an ISO to boot from a PC-style USB menu.
The installed desktop runs from the internal SSD; it does not need an attached
development computer or USB drive afterward.

## Download

Get `download-j713.command` from the **v0.1.0-alpha.1-update.4** GitHub release,
the latest installer. Its image starts the GPU on every 13-inch M4 Air,
including the base model's eight-core GPU. It ships the speaker filters in the
image, carries the stage 1 fix for #82 and connects to M4 updates by itself
(see [UPDATES.md](updates.md#installer-update-4)). No fresh installation from
this exact kit has been confirmed yet, so please report how yours goes. Update 3
remains available; with it, use `update.3` in place of `update.4` in the paths
below. In macOS Terminal, run the downloaded file:

```sh
bash "$HOME/Downloads/download-j713.command"
```

The release copy has the release URL and manifest checksum embedded. It downloads
the split image and source bundle, reassembles them and checks every file. Allow
at least 15 GB of free space for this step, plus space for the Apple recovery
download and the Linux installation. Do not use GitHub's automatic “Source code”
ZIP as an installer. Downloads become publicly accessible when the repository
becomes public; before then, GitHub authentication is required to acquire assets.

The resulting kit is at:
`~/Downloads/Omarchy-M4-v0.1.0-alpha.1-update.4/installer/`.
You may copy that complete directory to an exFAT USB drive. Keep its directory
layout intact. If using USB, use the **port closest to the hinge**: the
installed image turns on the front port only after its first M4 update
(`omarchy-m4-boot` 1-12). macOS may recognize both ports.

## Start in macOS

1. Connect power and the internet. Boot macOS normally and open Terminal locally.
   An administrator account is required; run from native ARM64 macOS, not Rosetta.
   The installer firmware profile requires macOS **26.5.1 or newer**. Updates 3
   and 4 already include the approved speaker filters and do not read macOS
   AudioDSP. The update 2 kit is narrower: it recognizes exact Apple AudioDSP and
   AID37 tuning files recorded in `owner-audio/bundle/manifest.json`. macOS
   26.5.1, 26.5.2 and 26.6.2 (25G83) are qualified there; other builds may be
   refused before any partition changes. Firmware, recovery and other installer
   dependencies still need qualification on a newer macOS build.
2. Change into the kit directory. For the default download location:

   ```sh
   cd "$HOME/Downloads/Omarchy-M4-v0.1.0-alpha.1-update.4/installer"
   bash Install.command --install
   ```

   If using a USB, use its actual directory under `/Volumes/` instead. Quotes are
   needed around paths containing spaces. `--verify-only` checks the kit without
   starting installation.
3. Wait for checksum verification. The large image and source archives can take
   time after the notices' `OK` lines. A returned shell prompt with no error means
   the command has finished; do not start a second copy while one is running.
4. Confirm that you have read these instructions. Choose whether to enable Google
   Widevine for protected movies. **No is the default.** No Widevine binary is in
   the download; consenting enables acquisition from Google after Linux starts.
5. Updates 3 and 4 include the speaker filters and proceed directly to administrator
   authentication. The update 2 kit instead generates them from the Mac's
   installed components and requires exact output hashes. If that preparation
   fails, stop: no partitioning has begun. Do not bypass its checks or
   substitute filters.
6. Enter your **macOS** password when requested. Terminal displays no characters
   while you type it. Sleep prevention stays active during the installer.

The launcher can also use an existing complete recovery download:
`bash Install.command --install --ipsw-dir "/path/to/recovery"`.
That directory must contain `UniversalMac_26.5.1_25F80_Restore.ipsw`.
Do not rename a different version to that filename.

## Allocate space and authorize startup

1. The installer uses expert mode for this experimental profile. If prompted,
   enable it. Select the experimental **Omarchy M4 Native** entry.
2. If needed, use the installer's resize flow to make room. Keep macOS and both
   recovery environments. Respect the displayed recommended minimum; minimum
   usable space varies with your files and macOS version. Do not copy disk-number
   deletion commands from someone else's machine.
3. Choose **Install into free space** when it is available. At the Linux allocation
   prompt, `max` uses the available gap. Name the OS **Omarchy M4**. Review the
   target and sizes before confirming changes.
4. Let recovery decoding, validation, partitioning, firmware collection and boot
   setup finish. Follow the installer's exact on-screen recovery instructions.
   Apple requires local owner authentication; a completely unattended install is
   not possible. Use your macOS account when the recovery terminal requests it.
5. Select **Omarchy M4** in startup options when instructed. Preserve macOS as a
   recovery option. Do not disconnect power or force a restart while installation
   is writing to disk.

## First Omarchy login

Create your own Linux username, full name and password. Git name is your desired
commit author name; hostname is your choice (for example `omarchy-m4-air`). These
are new Linux settings, not another person's saved account. Choose your timezone.

The October 12 failed candidate finished account setup but could not start its
desktop. Its progress screen therefore looked stuck even after provisioning
completed. The replacement removes the confirmed unrelated-device waits and a
30-second probe for another manufacturer's audio hardware. It also orders the
form after startup jobs and pauses kernel console printing while the form owns
the screen; diagnostics remain in the journal and console printing is restored
on success or failure.

If the desktop cannot start, use **Control + Option + F2** (also hold Fn if your
keyboard treats F2 as a brightness key). Sign in with the username and password
you just created. The replacement enables this independent recovery login and
records a short status file at `/var/log/omarchy-m4-boot-status.json`. Inspect:

```sh
sudo journalctl -b -u m4-gpu -u m4-video -u m4-j713-audio-register --no-pager
sudo journalctl -b -k --grep 'G16|asahi' --no-pager
sudo cat /tmp/m4-gpu-start.log
sudo cat /var/log/omarchy-provision-owner.log
```

On the v0.1.0-alpha.1 installers through update 2, `m4-gpu-start`'s own
messages are only in `/tmp/m4-gpu-start.log` until the Air is connected to M4
updates, and a restart clears that file.

Update 4's image carries the GPU module that starts the GPU on every 13-inch
M4 Air. The kits through update 3 have alpha.1's GPU module instead. It starts
the GPU only on an Air with the ten-core GPU whose GPU power limit is exactly
24800 mW. Two of the three ten-core Airs measured so far are lower
([#57](https://github.com/Kb2uka/Omarchy-M4-Native/issues/57)), and the
base-model Air's eight-core GPU is refused outright
([#118](https://github.com/Kb2uka/Omarchy-M4-Native/pull/118)); both reach
this login instead of the desktop.
From the recovery login, do the [last step](#last-step-connect-to-m4-updates-required)
now: `nmtui` works here, and so does the connect line. Then restart. The update
brings the fixed module, which also logs why it refuses a GPU.

A stationary bar does not prove disk writes have stopped. Do not erase the
installation or repeatedly force power off to diagnose a stalled handoff.

Once setup completes and the desktop works, eject and remove the USB. Earlier
private installations had working keyboard, trackpad, audio and video. The
replacement's attended installation is still pending; intermittent trackpad
AFE startup remains an open qualification item.

## Last step: connect to M4 updates (required)

The alpha.1 installer still makes **Shut Down restart the Air**, and it does not
yet receive M4 fixes. One step fixes both. First join Wi-Fi: open a terminal
and run `nmtui`. Then paste this one line, press Enter, and type your
password when asked:

```bash
curl -fsSL --retry 5 --retry-all-errors https://github.com/Kb2uka/Omarchy-M4-Native/releases/download/packages/m4-connect.sh | bash
```

The last lines it prints include
`"status": "connected; restart once to apply the update"`. Restart the
Air once. From then on, Shut Down turns it off, and `omarchy-m4-update` brings
new M4 fixes. [Details, messages and rollback](updates.md).

Updates 3 and 4 do this step on their own (installers through update 2 do not). Once your
account exists and the Air is online, a background service
(`m4-channel-connect.timer`) runs the same setup within about 20 minutes and
retries until it succeeds; restart once afterwards. Check it with
`systemctl status m4-channel-connect.service`. Running the line above yourself
is still fine and only speeds it up.

Widevine acquisition needs internet access and a closed browser; finish that
setup before expecting protected movies. Ordinary browser video and protected
movies may use different decoders.

Read `UPDATES.md` before accepting a system update. Until the step above is
done, read the shutdown note in `KNOWN-LIMITATIONS.md` before powering off.
