# M4 package channel

Installed M4 machines receive kernel, devicetree and boot-object fixes as signed
pacman packages from the `[omarchy-m4]` repository, through the normal
`omarchy update`, without reinstalling
([issue #49](https://github.com/Kb2uka/Omarchy-M4-Native/issues/49)). Owners
connect once; see [updates.md](updates.md). This page is for maintainers and
reviewers.

## What is in the channel

| Package | Installs | Version at launch |
| --- | --- | --- |
| `omarchy-m4-keyring` | The channel's public signing key for `pacman-key` | `20261012-1` |
| `omarchy-m4-boot` | `m4-boot-compose`, its pacman hook, the J713 devicetree, the daily update notifier and, from 1-4, the real-time speaker protection drop-in | `1-1`; `1-4` adds CPU frequency scaling and CPU temperature; `1-12` adds the opt-in notch switch and turns on the front USB-C port; `1-13` brings `omarchy-m4-evdi` |
| `linux-m4-omarchy` | The J713 kernel image and its modules | `7.1.9.m4.20261008-2` |
| `omarchy-m4-gst-audio` | GStreamer's `autodetect` and `pulse` sinks in `/usr/lib/gstreamer-1.0`, for WebKitGTK and GStreamer apps | `1.28.7-1` |
| `omarchy-m4-headphones` | The headphone jack card: two pinned modules, `m4-headphones`, its registration drop-in, UCM and WirePlumber files | `1-1`; `1-2` adds the headset microphone |
| `omarchy-m4-cpu` | The CPU idle driver: one pinned module, `m4-cpu-idle` and its boot unit | `1-1` |
| `omarchy-m4-evdi` | DisplayLink's evdi module and `libevdi`, for USB display adapters | `1.15.1-1` |

### omarchy-m4-boot 1-4 and omarchy-m4-gst-audio 1.28.7-1

- **Devicetree:** `08e8e719…` (`j713-cpu-temperature`): update 1 plus [CPU
  frequency scaling](j713-cpufreq.md) and [CPU die
  temperature](j713-cpu-temperature.md). It was rebuilt byte for byte with DTC
  1.8.1 and is pinned in the `package-inputs` release. The composer moves an Air
  on alpha.1 or update 1 to it, and keeps it on an Air that already has it.
- **Real-time speaker protection:**
  `/usr/lib/systemd/system/speakersafetyd-j713.service.d/50-realtime.conf`
  (`SCHED_FIFO` 20). The CPU frequency devicetree needs it, or speaker
  protection can fail at boot. It is the one file a channel package may install
  under a protected prefix (`protected_exceptions` in `channel.json`).
- **GStreamer sinks:** alpha.1 has GStreamer 1.28.7-2 but no matching
  `gst-plugins-good` on the mirrors, so WebKitGTK apps had no `autoaudiosink`.
  `omarchy-m4-gst-audio` installs only `libgstautodetect.so` and
  `libgstpulseaudio.so`. They are the files `tools/m4-webkit-audio-plugins.py`
  built on the owner's Air (hashes pinned in `channel.json`), and the upstream
  source is mirrored in `package-inputs`. Installing them system-wide lets the
  WebKitGTK sandbox see them without `GST_PLUGIN_PATH`. The package conflicts
  with `gst-plugins-good`, so a later full install of that package replaces it.
  Nothing depends on it, so that replacement is never blocked. The connect step
  installs it; an Air connected before 1.28.7-1 was published gets it by running
  the connect line once more.
- **Not in the channel:** the core-placement helper (per user, under `~/.local`;
  an old copy refuses to pin on this devicetree).

### omarchy-m4-boot 1-5 and omarchy-m4-speaker 1-1

- **`omarchy-m4-update`** (in `omarchy-m4-boot` 1-5, with a copy of the setup
  script at `/usr/lib/omarchy-m4/m4-channel-setup.py`) runs the setup script
  with `--apply`. It refreshes only `[omarchy-m4]` and installs or upgrades only
  channel packages, never the rest of the system. The update notifier now says
  to run it.
- **`omarchy-m4-speaker`** delivers the speaker protection startup fix
  ([#61](https://github.com/Kb2uka/Omarchy-M4-Native/pull/61)). The audio modules
  stay manifest-managed outside pacman, so the package does not overwrite them.
  It stages the fixed `snd-soc-j713-group.ko` (`aaad802e…`) under
  `/usr/lib/omarchy-m4/`. A drop-in on `m4-j713-audio-register.service`
  (`ExecStartPre=-`, the second `protected_exceptions` file) then runs
  `m4-speaker-module --boot` before any audio module loads. It swaps the module
  and exactly the six manifest values that pin it, only from the stock alpha.1
  state, or to finish a swap a power cut interrupted. Everything else is
  refused and left untouched, and a refusal never stops registration. Details:
  [speaker protection startup](j713-protection-startup.md#existing-installs).

### omarchy-m4-boot 1-6 and omarchy-m4-gpu 1-1

- **`omarchy-m4-gpu`** delivers the Air GPU power-limit fix
  ([#57](https://github.com/Kb2uka/Omarchy-M4-Native/issues/57),
  [#80](https://github.com/Kb2uka/Omarchy-M4-Native/pull/80)). alpha.1's module
  required a GPU power limit of exactly 24800 mW, which only a 1000 mV top GPU
  state gives, so other Airs got no GPU and no desktop. The fixed module
  (`3cf47fa7…`, 390,752 bytes, built from [`gpu/j713/`](../gpu/j713/README.md)
  on the alpha.1 kernel tree) accepts each Air's own derived limit up to 24800
  mW. It booted on a 1000 mV and a 980 mV Air. It is pinned in the
  `package-inputs` release.
- The GPU module stays under `/opt/m4-gpu`, outside pacman, so the package does
  not overwrite it. It stages the module under `/usr/lib/omarchy-m4/gpu/`. A
  drop-in on `m4-gpu.service` (`ExecStartPre=-`, the third
  `protected_exceptions` file) runs `m4-gpu-module --boot` before
  `m4-gpu-start` checks `/opt/m4-gpu/runtime.sha256` and loads the module. It
  swaps the module and exactly its one line in `runtime.sha256`, only from the
  stock alpha.1 state (`80cf3669…`), or to finish a swap a power cut
  interrupted. An Air on the #57 test build is already current. Anything else,
  such as a hand-patched module, is refused and left untouched, and a refusal
  never stops graphics startup. `sudo m4-gpu-module --rollback` restores alpha.1
  at the next boot and holds it there.
- **`omarchy-m4-boot` 1-6** depends on `omarchy-m4-gpu`. An Air's installed
  setup script (from 1-5) does not list the new package, so this dependency is
  what makes its first `omarchy-m4-update` run install it. Pacman then prints
  that the fix applies at the next restart; the setup script from 1-6 also
  reports the restart.

At launch the channel delivers exactly what a fully updated alpha.1 Air already
runs:

- **Kernel:** build 18, `c3704cfd…` (18,901,266 bytes), release
  `7.1.9-m4-gpu-backport-20261008`. This is the kernel alpha.1's boot object
  actually boots, with the trackpad AFE fix.
- **Devicetree:** update 1's `e54c77a7…`, with the shutdown fix.
- **Modules:** the alpha.1 package's 5,790 module, license and configuration
  files, byte-identical. They were checked against that package's own recorded
  SHA-256, size and mode.

The installed alpha.1 package `linux-m4-omarchy 7.1.9.m4.20261008-1` carries an
older `Image.gz` (`69b92b65…`) than the one in `boot.bin`. Version `-2` corrects
the package to match what boots. Nothing ever rebuilds `boot.bin` from the `-1`
file.

### omarchy-m4-headphones 1-1 and omarchy-m4-boot 1-7

- **Playback through the headphone jack** ([J713 headphone jack](j713-headphones.md)).
  Like every channel package, it arrives with `omarchy-m4-update`; there is no
  separate install step.
- **`omarchy-m4-boot` 1-7** depends on `omarchy-m4-headphones`, for the same
  reason 1-6 depends on `omarchy-m4-gpu`: an Air's installed setup script does
  not list the new package, so this dependency is what makes its first
  `omarchy-m4-update` run install it. The setup script from 1-7 lists it and
  reports that a restart starts the card.
- **Off switch:** `sudo m4-headphones --disable` keeps the package installed
  and current but stops the modules loading from the next boot;
  `sudo m4-headphones --enable` switches it back on. The hold file is
  `/var/lib/omarchy-m4/headphones/hold`, like the speaker and GPU holds.
- **Modules:** `j713_hp_dt.ko` (`9ba22f0e…`) and `snd-soc-j713-headphones.ko`
  (`a73ef565…`), built from `audio/j713/headphones/kernel/` with
  `tools/build-j713-headphones.sh` and pinned in `package-inputs` and
  `headphone_modules`. They are staged under `/usr/lib/omarchy-m4/` and never
  enter the manifest-managed audio module directory.
- **Loading:** a drop-in on `m4-j713-audio-register.service`
  (`ExecStartPost=-`) runs `m4-headphones --boot` after the speaker
  registration succeeds. A refusal or failure never fails the registration,
  and a boot that dies while loading stops the next boot from trying again.
- **Protected exceptions:** the drop-in, the two UCM files of the new
  `J713Headphones` card, the WirePlumber fragment `83-j713-headphones.conf`
  and its hook. All five are new files; none replaces a speaker or ALSA file.

### omarchy-m4-keyring 20261012-2 and omarchy-m4-boot 1-8

- **User-level pacman verifies the channel database** (issue #71). alpha.1
  leaves `/etc/pacman.d/gnupg` readable by root only, so with
  `DatabaseRequired` every `pacman` run as the desktop user printed keyring
  errors. The directory becomes `755` and `pubring.gpg`, `pubring.kbx` and
  `trustdb.gpg` become `644`, as on stock Arch. Private keys, revocation
  certificates and agent sockets keep their modes, and symlinks are never
  followed.
- **`omarchy-m4-keyring` 20261012-2** applies the modes from its install
  scriptlet, so an Air that is already connected is fixed by its next
  `omarchy-m4-update` run.
- **`omarchy-m4-boot` 1-8** ships the setup script that applies the same modes
  when a new install connects.

### omarchy-m4-boot 1-9

- **Lid close locks the screen on installed Airs** ([#84](https://github.com/Kb2uka/Omarchy-M4-Native/issues/84)).
  The image fix (#85) reached only new images; this delivers it through
  `omarchy-m4-update`. The install script runs `m4-lid-lock.py`, which writes
  the lock screen's PAM file when it is missing and appends the two lid binds
  once to each user's Omarchy configuration, as that user. Details:
  [J713 desktop image](j713-desktop-image.md#lid-close-and-the-lock-screen).
- The binds take effect at the next login. A refusal never fails the update.

### omarchy-m4-cpu 1-1 and omarchy-m4-boot 1-10

- **CPU idle** ([J713 CPU idle](j713-cpu-idle.md), #65): idle cores power down
  instead of spinning under `idle=nop`. Idle system power on the owner's Air
  fell from 9.65 W to 5.47 W.
- **Module:** `j713_cpuidle.ko` (`a2a7947a…`), built from `cpu/j713/` with
  `cpu/j713/build-module.sh` and pinned in `package-inputs` and
  `cpu_idle_module`. It is staged under `/usr/lib/omarchy-m4/cpu/`; the kernel
  package does not change.
- **Loading:** `m4-cpu-idle.service` (the package's one `protected_exceptions`
  file, enabled by its install scriptlet) runs `m4-cpu-idle --boot`. A boot that
  dies while loading or in the 120 s settle period stops the next boot from
  trying again; `sudo m4-cpu-idle --disable` is the off switch (hold file
  `/var/lib/omarchy-m4/cpu-idle/hold`).
- **`omarchy-m4-boot` 1-10** depends on `omarchy-m4-cpu`, as 1-6 and 1-7 do for
  the GPU and headphone packages, and carries devicetree `fdedc09f…`
  (`j713-ecluster-thermal`): E-cluster states up to 2172 MHz and a CPU die
  thermal zone with a 95 C passive trip. The composer and updater accept it only
  after the CPU temperature devicetree.

### omarchy-m4-cpu 1-2 and omarchy-m4-boot 1-11

- **P-core boost** ([J713 P-core boost](j713-cpu-boost.md), #66): P cores run
  up to 4464 MHz and E cores up to 2808 MHz, with P capped by a predictive guard
  that keeps the CPU die's predicted peak at or below 105 C, and raises the
  zone's sustained trip from 95 C to 100 C while it runs.
- **Module:** `j713_pguard.ko` (`91fe247e…`), built with the idle module by
  `cpu/j713/build-module.sh` and pinned in `package-inputs` and
  `cpu_guard_module`. `m4-cpu-idle` loads it inside the idle driver's
  one-strike window, and with boost on sets the real-time uclamp floor to
  1800 MHz. `sudo m4-cpu-idle --boost off` closes the boost gate at once
  (hold file `/var/lib/omarchy-m4/cpu-idle/boost-hold`).
- **`omarchy-m4-boot` 1-11** carries devicetree `c4f036b8…` (`j713-pboost`):
  the boost states with P held at 1800 MHz by a boost gate on the CPU die zone
  until the guard opens it. The composer and updater accept it only after the
  E-cluster devicetree.

### omarchy-m4-cpu 1-3

- **Limits at macOS parity:** a 105 C predicted peak, a 108 C hard pin, and
  the zone's sustained trip raised to 100 C while the guard runs.
- **Measured heat model** ([Settled heat](j713-cpu-boost.md#settled-heat-the-base-estimate),
  #66): the guard's base estimate subtracts the measured fast heat of each P
  state instead of 70% of its predicted step, and looks back 750-1000 ms for the
  lowest state. Guard `j713_pguard.ko` `91fe247e…`, pinned in
  `package-inputs` and `cpu_guard_module`.

### omarchy-m4-headphones 1-2

- **The headset microphone on the headphone jack**
  ([J713 headphone jack](j713-headphones.md#headset-microphone)). It is part of
  the same package, so `omarchy-m4-update` brings it as an ordinary upgrade:
  no new package, no setup-script or boot-package change (`omarchy-m4-boot`
  1-7 already depends on `omarchy-m4-headphones`).
- **Restart notice on upgrade.** The install script gains `post_upgrade`, so
  the upgrade prints "Restart once to start the headphone jack and headset
  microphone". `omarchy-m4-update` reports a needed restart only while the card
  module is not loaded, so on an Air running 1-1 this line is the notice.
- **Off switch:** unchanged. `sudo m4-headphones --disable` switches the mic
  off together with the headphones.
- **Modules:** `j713_hp_dt.ko` (`3f501c61…`; its card node now also names ADMAC
  channel 5, `rx`) and `snd-soc-j713-headphones.ko` (`2e743937…`), built with
  `tools/build-j713-headphones.sh` and pinned in `package-inputs` and
  `headphone_modules`, as for 1-1. `apple-admac` and every other speaker
  module stay as qualified.
- **Protected exceptions:** the same five paths. The reasons for the
  WirePlumber fragment and its hook now name the headset mic source too.

### omarchy-m4-gpu 1-2

- **A failed GPU start can be read from the journal**
  ([#82](https://github.com/Kb2uka/Omarchy-M4-Native/issues/82)). alpha.1's
  `m4-gpu.service` appends `m4-gpu-start`'s output to `/tmp/m4-gpu-start.log`,
  which a restart clears, so the "desktop could not start" notice's
  `journalctl -u m4-gpu.service` showed nothing from the script. The drop-in now
  also sets `StandardOutput=journal` and `StandardError=journal`.
- **The report names the GPU values the module's Air gate checks:** core mask,
  power limit and firmware build, read from the GPU's devicetree node. Each
  boot's `m4-gpu-module --boot` report, and `m4-gpu-module` run by hand, carries
  them under `gpu`. A missing or malformed value is `null`. Nothing else in the
  tool changed: the same states, swaps and refusals.
- The module is unchanged (`3cf47fa7…`). Like every channel package, 1-2 arrives
  with `omarchy-m4-update` and takes effect at the next boot.

### omarchy-m4-boot 1-12

- **Opt-in full-panel desktop** ([J713 camera notch](j713-notch.md),
  [#89](https://github.com/Kb2uka/Omarchy-M4-Native/issues/89)):
  `sudo m4-notch --show` makes the next boot use all 2560x1664 pixels, with the
  Omarchy bar's center widgets split left and right of the camera.
  `sudo m4-notch --hide` goes back. Installing the package changes nothing.
- **Notch stage 2:** `m1n1-stage2-notch.bin` (`f2417a74…`, 0x110000 bytes),
  alpha.1's stage 2 rebuilt byte for byte plus `display/j713/m1n1-show-notch.patch`,
  built by `display/j713/build-notch-loader.sh` and pinned in `package-inputs`
  and `notch_loader`. Only `m4-notch --show` writes it into `boot.bin`; the
  composer then carries it like any loader. m1n1's license notices are installed
  under `/usr/share/licenses/omarchy-m4-boot/m1n1/`.
- **Login unit:** `omarchy-m4-notch.service` (user, enabled globally by the
  install script, like the update notifier) runs `m4-notch --desktop`, which
  fits the bar to what the boot shows and changes nothing on a cropped boot
  without its marks.
- **Front USB-C port** ([J713 front USB-C port](j713-front-usb.md)): devicetree
  `352bfa74…` (`j713-usb0`) turns on the front port's controller and both of
  its DARTs, with the hinge-side port's stream IDs, so both ports work the
  same. Built by `tools/build-j713-usb0-dtb.py` on the boost-state devicetree
  and pinned in `package-inputs`. The composer and updater accept it only after
  the boost-state devicetree.

### omarchy-m4-gpu 1-3

- **GPU and desktop on the eight-core GPU**
  ([#118](https://github.com/Kb2uka/Omarchy-M4-Native/pull/118), by Jeff
  Hollis). The base-model Air's GPU has the same ten core positions with two
  fused off. The #57 module accepted only the ten-core mask `0x3ff`, so an
  eight-core Air (one reports `0x3bd`) got no GPU and no desktop. The module
  (`de0337a7…`, 390,816 bytes, built from [`gpu/j713/`](../gpu/j713/README.md)
  on the alpha.1 kernel tree and pinned in `package-inputs`) accepts any mask
  within `0x3ff` with eight or ten cores. Every other check is unchanged. The
  mask never reaches the GPU firmware, only Mesa, so a ten-core Air runs exactly
  as before.
- **`m4-gpu-module`** moves an Air from #57's module (`3cf47fa7…`, 1-1 and
  1-2, state `previous`) to the new one at the next boot, the same way it moves
  one from alpha.1. Its swap still writes the module before the pin, and a
  module ahead of its pin is completed the next boot, including one that 1-1 or
  1-2 left. A pin ahead of its module, or any other module, is refused and left
  untouched. `sudo m4-gpu-module --rollback` still restores alpha.1.
- The setup script's restart notice (`gpu_pending` in `m4-channel-setup.py`,
  shipped in `omarchy-m4-boot`) counts only a swap from alpha.1, so an Air
  moving from 1-2 is told to restart by pacman's install message instead.

### omarchy-m4-evdi 1.15.1-1 and omarchy-m4-boot 1-13

- **USB external displays** ([J713 external displays](j713-external-displays.md),
  #58): DisplayLink's evdi 1.15.1, the kernel half of DisplayLink adapters and
  docks. USB-C and Thunderbolt monitors still need DCP support and do not work.
- **Files:** `evdi.ko` (`c5e65a2b…`) in
  `/usr/lib/modules/7.1.9-m4-gpu-backport-20261008/updates/`, where pacman's
  depmod hook indexes it, and `libevdi` (`0f1da52d…`) as `/usr/lib/libevdi.so`,
  the layout of Arch's `evdi` package. Both are built by `display/evdi/build.sh`
  from the upstream `v1.15.1` archive (mirrored in `package-inputs`) and pinned
  in `evdi_module` and `evdi_library`.
- **Relations:** `provides=evdi=1.15.1` and conflicts with `evdi`, `evdi-dkms`
  and `evdi-git`, so the AUR `displaylink` package (which needs `evdi<1.16`)
  installs on top. Nothing loads evdi until its service runs `modprobe evdi`;
  no restart is needed.
- **Not shipped:** Synaptics' DisplayLinkManager is closed source; owners
  install it from the AUR.
- **`omarchy-m4-boot` 1-13** depends on `omarchy-m4-evdi`, as 1-6, 1-7 and 1-10
  do for the GPU, headphone and CPU packages. Nothing else in it changes.

## Connecting new installs

The release root builder (`tools/prepare-j713-release.py`) adds
`/usr/lib/omarchy/m4-channel-connect.py` (a copy of `tools/m4-channel-setup.py`;
`/usr/lib/omarchy-m4/` belongs to the channel packages) and enables
`m4-channel-connect.timer`. Its service runs the setup script with
`--apply` once the owner's account exists
(`ConditionPathExists=!/var/lib/omarchy/provisioning/pending`). The timer
fires every 20 minutes of wall-clock time until the script succeeds, because
Wi-Fi usually arrives after first login. Success writes
`/var/lib/omarchy-m4/connected` and disables the timer. The service has no
timeout of its own, so it is never killed in the middle of its rollback. A
failed attempt leaves `pacman.conf` and `boot.bin` as they were; the channel's
signing key, imported before the network step, stays trusted. Setup takes an
exclusive lock, so this service and a manual connect or `omarchy-m4-update` run
one after the other.

## The boot object and its composer

`/boot/efi/omarchy/boot.bin` is unique to each machine. It is the m1n1 loader,
one `chosen.bootargs=` line, the gzip kernel, the devicetree, and an
`m1n1_initramfs` payload. That payload holds the base initramfs plus the owner's
Apple firmware, collected by the installer on that Mac. The firmware cannot be
redistributed and must never be regenerated by a package.

`m4-boot-compose` therefore **splices**; it never rebuilds:

1. It parses the current file strictly. Exactly one layout must consume the
   whole file, and decoy text inside the loader is rejected.
2. It requires the current kernel and devicetree to be in the known alpha.1
   lineage (kernel `c3704cfd…`; devicetree `67dc6145…` alpha.1, `e54c77a7…`
   update 1, `033025bb…` with [CPU frequency scaling](j713-cpufreq.md),
   `08e8e719…`, which adds [CPU die temperature](j713-cpu-temperature.md), or
   `fdedc09f…`, which adds [E-cluster states and a CPU thermal zone](j713-cpu-idle.md)).
   Anything else, such as a development boot object, is refused and left
   untouched. A current devicetree later in that lineage than the packaged one
   is kept, so an owner-applied devicetree update is never reverted.
3. It replaces only the kernel and devicetree segments with the packaged files.
   Those must also be known, and the kernel must be one complete gzip stream.
   The loader, the boot arguments (including the trackpad options) and the
   firmware payload are carried over byte for byte, then checked again in the
   result.
4. **Protected stack:** it refuses any kernel the installed video stack
   (`/etc/omarchy/j713-video-manifest.json`: `boot_kernel_sha256` and
   `kernel_release`) or audio stack (`kernel_release`) is not qualified for. It
   also refuses when that kernel's module directory is missing.
5. It checks free space on the EFI partition. It saves the current file as
   `boot.bin.previous`, then writes a verified temporary copy and renames it
   over `boot.bin`. An identical result writes nothing.

Every failed check leaves `boot.bin` unchanged; a pacman hook cannot roll back
package files, so the old boot object simply keeps booting. The hook
(`90-omarchy-m4-boot.hook`, PostTransaction) runs after any transaction that
installs or upgrades the composer, the kernel image or the devicetree.

Equivalence on hardware: run on the Air against its own alpha.1 `boot.bin`
(`54273bb8…`), the composer produced `deb79273…`, byte-identical to the
update 1 result already verified to boot and shut down correctly. Rollback
restored `54273bb8…`.

## Update notification

Omarchy's own update indicator only watches the `omarchy` package, so it never
announces M4 fixes. `omarchy-m4-boot` therefore ships `omarchy-m4-update-check`
and a per-user systemd timer, enabled globally by the package's install script.
It runs ten minutes after login and daily after that. It downloads the
channel's package list over HTTPS from the server in
`/etc/pacman.d/omarchy-m4.conf`, compares it with the installed M4 packages
(`pacman -Q`, `vercmp`), and shows one `notify-send` notification per new set
of versions: "M4 update available … Run omarchy-m4-update, then restart." It is
unprivileged and read-only, and it never offers a package that is not
installed. Offline or unconnected machines stay quiet. Signatures are still
checked by pacman when the update is installed.

## What the channel never touches

Package contents are checked on every PR and before every publish
([check-m4-packages.py](../tools/check-m4-packages.py)). Each file must be on
its package's allowlist in [channel.json](../packaging/omarchy-m4/channel.json)
and outside the protected list. Packages cannot install anything under
`/etc`, `/opt` (graphics, decoder and Mesa overlays), `/usr/local` (browser
wrappers), Widevine state, `/usr/lib/omarchy` (audio modules and helpers),
the speaker and ALSA/WirePlumber configuration, firmware, or system service
units. The only user units are the notifier's own two files. The boot object
is written only by the composer.

Install scripts never install files there either. Their tools change state
outside the package only in known states and refuse anything else: the
speaker and GPU tools swap only their own module and its pin, and from
`omarchy-m4-boot` 1-9 `m4-lid-lock.py` writes the lock screen's PAM file when
it is missing and appends the lid binds once to each user's Omarchy
configuration, running as that user.

These protected components stay manifest-managed outside pacman, as alpha.1
installed them. pacman refuses to install over files it does not own, and
Omarchy's conflict handler only clears files for the `omarchy` packages
themselves. The graphics overlays link only to stable sonames (`libdrm`,
`libexpat`, `libz`, `libzstd`; observed with `ldd` on the Air), so a general
Mesa or LLVM update does not replace them.

`linux-m4-omarchy` conflicts with `linux-aarch64` and provides it, so a general
Arch Linux ARM update cannot pull the stock kernel over the M4 one. The
repository is searched before `[core]`.

## Shipping a fix

- **Devicetree:** add the new hash to `KNOWN_DTBS` in `tools/m4-boot-compose.py`,
  point the boot package at the new file, bump `pkgver` and update the pin in
  `channel.json`. Ship the hardware evidence with the PR.
- **Kernel:** a kernel with a different image or release also changes what the
  video, GPU and audio stacks were qualified against. Add it to
  `KNOWN_KERNELS` only together with a requalified protected stack whose
  manifests name it. Until then the composer refuses it on every machine.
  Publishing a new module release removes the old module directory on upgrade,
  so CI only accepts the release pinned in `channel.json`.
- **Composer or packaging only:** bump `pkgrel`.

A published package file is never replaced. CI fails a change that alters a
package's contents without a new version.

## Repository, signing and publishing

- **Hosting:** assets of the GitHub release `packages` (pre-release), served at
  `https://github.com/Kb2uka/Omarchy-M4-Native/releases/download/packages`.
  Inputs that are not built from this repository live, hash-pinned, in the
  `package-inputs` release. That covers the kernel payload; the devicetree
  comes from the update 1 release.
- **Key:** ed25519 `578DF78177CF6DF29B888B8C9404F3CC90797A50`, used only for
  this repository. Packages and the database are signed; machines use
  `SigLevel = Required DatabaseRequired` for it.
- **CI:** [packages.yml](../.github/workflows/packages.yml) builds the
  packages with makepkg on a standard hosted runner. It runs on every PR and
  branch push, checks policy, then installs them with real pacman into a
  sandboxed alpha.1-style root. There, a throwaway key signs a local copy, the
  setup script connects the root twice, and protected stand-ins and `boot.bin`
  must stay byte-identical. pacman must also refuse a database signed by an
  untrusted key.
- **Publishing:** only the `publish` job, only on `main`, inside the
  `package-publish` environment (deployments restricted to `main`), receives
  the private key as `PACKAGE_SIGNING_KEY`. It keeps already-published package
  files and verifies their signatures, then signs and uploads new ones. It
  repairs an interrupted earlier run by signing an unsigned published package.
  The signed databases go up last, whenever they are missing, unsigned or out
  of date. Everything is then
  downloaded anonymously and verified again. The
  [workflow policy](../tools/ci/check-workflows.py) allows a secret or write
  access nowhere else.

### Maintainer setup (once)

```sh
# Environment limited to main, then the key, read from the maintainer's offline copy.
gh api -X PUT repos/Kb2uka/Omarchy-M4-Native/environments/package-publish \
  -F 'deployment_branch_policy[protected_branches]=false' \
  -F 'deployment_branch_policy[custom_branch_policies]=true'
gh api -X POST repos/Kb2uka/Omarchy-M4-Native/environments/package-publish/deployment-branch-policies \
  -f name=main -f type=branch
GNUPGHOME=~/.config/omarchy-m4/signing-gnupg gpg --armor --export-secret-keys 578DF78177CF6DF29B888B8C9404F3CC90797A50 |
  gh secret set PACKAGE_SIGNING_KEY --env package-publish
```

Keep an offline copy of the private key and its revocation certificate. To
rotate it, publish a keyring package that adds the new key and revokes the old
one. Sign it with the old key, then switch the fingerprint in
`channel.json` and `tools/m4-channel-setup.py`.

## Corresponding source

Every compiled binary downloaded from the `package-inputs` release is joined to
its pinned corresponding source and its rebuild correspondence in
[`packaging/channel-source-joins.json`](../packaging/channel-source-joins.json).
`tools/check-m4-packages.py` enforces in CI, with no override, that every
package version not yet published has a `rebuilt-identical` join for each
binary it downloads. A version already published byte for byte is on Airs and
is not uploaded again, so it passes; any new version of it needs the rebuild
first. Joins stay `unverified` until a byte-identical rebuild is recorded:

- **Kernel (`linux-m4-omarchy`):** The kernel package's build identity is recorded
  from the kernel tree and configuration published with
  [v0.1.0-alpha.1](https://github.com/Kb2uka/Omarchy-M4-Native/releases/tag/v0.1.0-alpha.1)
  (`source/kernel-controller.tar.gz`), plus the J713 input patches in
  `patches-kernel/j713-input/` in this repository; as with the release kernel, a
  byte-identical rebuild is not claimed.
- **Devicetrees (`omarchy-m4-boot`):** The devicetree's base source is the alpha.1
  portable devicetree plus [`j713-smc-poweroff.dtso`](../patches/j713-smc-poweroff.dtso),
  followed in order by the cpu-temperature (`patches/j713-cpu-temperature.dtso`),
  ecluster (`patches/j713-ecluster-thermal.dtso`), and pboost
  (`patches/j713-pboost.dtso`) overlays built with DTC 1.8.1 and their respective
  builder tools (`tools/build-j713-cputemp-dtb.py`, `tools/build-j713-ecluster-dtb.py`,
  `tools/build-j713-pboost-dtb.py`).
- **Speaker protection module (`omarchy-m4-speaker`):** `snd-soc-j713-group.ko`
  (`aaad802e…`), built from `audio/j713/kernel/` on the alpha.1
  kernel-controller tree with `reference-sound-exports.symvers`; an identical
  rebuild is not yet recorded.
- **Air GPU module (`omarchy-m4-gpu`):** `asahi_m4.ko` (`3cf47fa7…`), built from
  `gpu/j713/asahi/g16.rs` on the alpha.1 kernel tree with
  `gpu/j713/build-test-module.sh`. Rebuilt byte for byte (`rebuilt-identical`),
  after the same run reproduced alpha.1's module.
- **Headphone jack modules (`omarchy-m4-headphones`):** `j713_hp_dt.ko` and
  `snd-soc-j713-headphones.ko`, built from `audio/j713/headphones/kernel/` on
  the alpha.1 kernel-controller tree with `tools/build-j713-headphones.sh`.
- **CPU idle and boost guard (`omarchy-m4-cpu`):** `j713_cpuidle.ko` (`a2a7947a…`)
  and `j713_pguard.ko` (`91fe247e…`), built from `cpu/j713/` on the alpha.1
  kernel tree with `cpu/j713/build-module.sh`.
- **Notch stage 2 (`omarchy-m4-boot`):** `m1n1-stage2-notch.bin` (`f2417a74…`),
  alpha.1's `source/m1n1-stage2-source.tar.gz` plus `display/j713/m1n1-show-notch.patch`,
  built by `display/j713/build-notch-loader.sh`. Rebuilt byte for byte
  (`rebuilt-identical`).
- **USB display support (`omarchy-m4-evdi`):** `evdi.ko` (`c5e65a2b…`), built
  on the alpha.1 kernel tree, and `libevdi` (`0f1da52d…`), built for aarch64,
  both from DisplayLink's unchanged `evdi-1.15.1.tar.gz` (`b120df30…`, mirrored
  in `package-inputs`) with `display/evdi/build.sh`. Each was rebuilt byte for
  byte (`rebuilt-identical`).
- **GStreamer audio sinks (`omarchy-m4-gst-audio`):** `libgstautodetect.so` and
  `libgstpulseaudio.so`, built by `tools/m4-webkit-audio-plugins.py` from
  `gst-plugins-good-1.28.7.tar.xz` (mirrored in `package-inputs`).

## Limits

- The channel's first version moves no machine to a different kernel. On an
  alpha.1 install that skipped update 1, it applies the same devicetree change
  as update 1. A fully updated Air sees `boot.bin` unchanged.
- Mesa, the decoder, audio and Widevine pieces are protected, not packaged.
  Packaging them needs their own requalification and ownership migration.
- `omarchy refresh pacman` and channel switching replace `/etc/pacman.conf`
  with x86 Omarchy defaults; they are not supported on M4. Rerun the setup
  script afterwards.
- Hardware acceptance of a changed `boot.bin` delivered by `pacman` itself (a
  restart after a real composer write) is recorded in issue #49.
