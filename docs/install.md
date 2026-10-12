# Install v0.1.0-alpha.1

This experimental, independent build supports only the **13-inch M4 MacBook Air
(2025), Mac16,12 / J713 / T8132**. It is not endorsed by Omarchy, Asahi, Gravity
or Apple. Read the repository warning and KNOWN-LIMITATIONS.md before proceeding.
Installation changes partitions and boot policy. Keep a backup and macOS recovery.

This is a **macOS-launched installer**, not an ISO to boot from a PC-style USB menu.
The installed desktop runs from the internal SSD; it does not need an attached
development computer or USB drive afterward.

## Download

Get `download-j713.command` from the **v0.1.0-alpha.1** GitHub release. In macOS
Terminal, run the downloaded file:

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
`~/Downloads/Omarchy-M4-v0.1.0-alpha.1/installer/`.
You may copy that complete directory to an exFAT USB drive. Keep its directory
layout intact. If using USB, use the **port closest to the hinge**: the current
Linux image leaves the front port disabled. macOS may recognize both ports.

## Start in macOS

1. Connect power and the internet. Boot macOS normally and open Terminal locally.
   An administrator account is required; run from native ARM64 macOS, not Rosetta.
   The installer firmware profile requires macOS **26.5.1 or newer**. Audio input
   compatibility is narrower: this release recognizes exact Apple AudioDSP and
   AID37 tuning files recorded in `owner-audio/bundle/manifest.json`. Different
   macOS builds may be refused before any partition changes.
2. Change into the kit directory. For the default download location:

   ```sh
   cd "$HOME/Downloads/Omarchy-M4-v0.1.0-alpha.1/installer"
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
5. Speaker filters are generated from the Mac's installed components, in memory,
   and must match the release's exact expected hashes. If preparation fails,
   stop: no partitioning has begun. Do not bypass the checks or substitute filters.
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

The prior attended build stalled near the end of “Setting up machine”; one
restart subsequently reached login. A stationary bar alone does not establish
that writes have stopped. Allow setup to finish and inspect activity/errors
before restarting. If login appears after a necessary restart, sign in rather
than erasing partitions or repeating installation. Report recurrence with logs.

Once setup completes and the desktop works, eject and remove the USB. The
keyboard, trackpad, audio and video worked on the maintainer's preceding private
build. The final owner-input packaging is undergoing review and awaits the maintainer's
final installation before publication. It remains an alpha with the limitations
described alongside it.

For Wi-Fi, open a terminal and run `nmtui`. Widevine acquisition needs internet
access and a closed browser; finish that setup before expecting protected movies.
Ordinary browser video and protected movies may use different decoders.

Read `UPDATES.md` before accepting a system update. Read the shutdown workaround
in `KNOWN-LIMITATIONS.md` before powering off.
