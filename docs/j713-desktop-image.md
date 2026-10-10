# J713 desktop image completion

The first installed image contained Omarchy 4.0.4 but omitted `sudo`, generated
UTF-8 locales and several general desktop packages. Its custom Hyprland
entrypoint also omitted the stock application bindings and first-login startup.
The installed package version alone did not establish a complete desktop.

The live Air repair restored the packaged Hyprland entrypoint with the M4
display/session overrides. The owner confirmed that the browser shortcut then
opened Chromium. Running the packaged first-login provisioner completed its
logged steps, enabled the shipped user services and created its normal
`first-run-user` marker. This does not establish that a new image's entire
first-login flow has been qualified.

A later login failed because global user-service diagnostic drop-ins wrote to
shared files in `/tmp`. The greeter created the D-Bus log with its own ownership;
the owner's D-Bus service then failed at `STDOUT` with permission denied.
Changing those six drop-ins to journal output restored login. The owner then
confirmed one normal repeat boot reaching the desktop with a working trackpad.

## Prepare an offline image

Use a separate writable copy of the ARM root. Retain the original and its
manifest until the replacement has completed native qualification.

1. Install the packaged `omarchy-base.packages` requirements and the supplemental
   names in [`m4-desktop.packages`](../tools/m4-desktop.packages), using the
   target's package manager and verified ARM packages. Keep the custom M4
   kernel, graphics libraries and firmware integration. Replace `jack2` with
   `pipewire-jack` in the same dependency-checked transaction. Do not remove
   dependency checks or refresh a package database for a partial upgrade.
2. Apply the defaults from the installed Omarchy package:

   ```sh
   python3 tools/configure-m4-desktop.py --root /path/to/candidate-root --apply
   python3 tools/configure-m4-desktop.py --root /path/to/candidate-root --check
   ```

   The tool requires the existing M4 root identity and pending owner-provisioning
   markers. Place the image beneath a directory: a filesystem root is refused,
   including a drive-letter or UNC share root on Windows. It checks every requirement in the installed package's core list,
   the supplemental packages, Omarchy, its settings and Hyprland before changing
   any configuration. Registered package providers are recognized, including
   `neovim` providing `nvim` and `dotnet-runtime-bin` providing `dotnet-runtime`.
   An empty list or a new version-constrained requirement fails for review.
   This presence check complements the separate `pacman -Dk` dependency check.
   It copies the
   full packaged Hyprland entrypoint and appends the experimental display/session
   settings, enables `en_US.UTF-8` in `locale.gen`, selects that locale, and
   installs Omarchy's packaged zram configuration. It creates journal output
   drop-ins for six user services, replacing the known RAM diagnostic settings
   where present; unexpected extra settings fail validation before any write.

   It also retains the full packaged shell layout and generates an `m4.agents`
   clone under `/etc/skel/.config/omarchy/plugins/`. It configures the agent widget
   to remain visible before the first account has usage data; fresh-owner native
   qualification remains pending. Account sign-in belongs to
   each owner; the image contains no account credentials or invented usage.
   The clone is derived from the installed package and retains its license.
   Markdown copies are limited to README, LICENSE, COPYING, NOTICE and CHANGELOG
   documents; other Markdown files are omitted as internal build guidance.
   Unexpected files already in the clone fail preflight, including files removed
   upstream since an earlier run. Start with a fresh candidate or review those
   files manually; the tool never deletes them. A user's clone does not update
   automatically with the package: updating existing owner clones needs a
   separate qualified migration. Rebuilding `/etc/skel` affects future owners.
   A changed upstream visibility contract requires review rather than silently
   producing a hidden widget. Existing user homes and `/usr/share/omarchy`
   remain unchanged. Text configuration is written as UTF-8 with Linux newlines.
3. Run `locale-gen` **inside the ARM candidate root**, natively or through the
   qualified ARM emulation harness. Verify `locale -a` includes `en_US.utf8`.
   Merely editing `locale.gen` does not generate the locale.
4. In the candidate, run `pacman -Dk` and check all base requirements using
   `pacman -T`. Check that `sudo -V` loads, custom graphics hashes still match,
   and owner provisioning remains pending. Remove build keyrings and machine
   identity before packaging; retain the existing image sanitation checks.
5. Qualify a fresh owner's native graphical session. Verify that owner creation
   copies the skel configuration, all personal `hypr.*` modules resolve, and
   provisioning grants the owner normal wheel/sudo access. Check the visible
   agent widget before account sign-in, default shortcuts, first-run completion,
   UTF-8 wizard input, notifications and persistent services. Check `zramctl`,
   `swapon --show`, `systemctl --failed` and `systemctl --user --failed`.
   Do not mark provisioning complete in the image itself.

The M4 block at the end of `~/.config/hypr/hyprland.lua` sets a fallback monitor
scale, software cursor, scrolling and diagnostic logging after the personal
modules. It also registers the `hyprland.start` hook that finalizes the UWSM
environment and starts the session services. Change these M4 settings in that
final block; the scalar settings override earlier assignments. A named monitor
rule can still take precedence over the fallback monitor rule. Other packaged
and personal settings retain their normal loading order.

The configuration tool performs no package installation, network operation,
locale compilation or power action. `--check` is read-only; `--apply` is for
an offline build candidate, not an installed user's customized desktop.
Symlinks in configuration paths are rejected to prevent writes outside the
candidate. This is a build step, not a complete installer or an update policy.

## Remaining hardware qualification

The October 9 package audit found all 147 entries in Omarchy 4.0.4's core list
satisfied on the repaired Air. The M4 supplement also includes the requested
`htop`; Omarchy's core list ships `btop`. The separate
[`omarchy-other.packages`](https://github.com/basecamp/omarchy/blob/v4.0.4/install/omarchy-other.packages)
list supplies the ISO mirror with conditional and optional packages. It is not
a list to install in full on every machine: it includes mutually exclusive
GPU drivers and hardware support for other vendors.

The upstream ISO's
[installation phases](https://github.com/omacom/omarchy-iso/blob/522b92a8d387681ff5713ae2e2624cbe2b16785a/configs/airootfs/usr/share/omarchy-iso/orchestrator/phases_impl.py)
install core packages, early owner defaults and hardware-selected applications;
Tailscale is conditional on an autoinstall key. This port still differs in its
custom M4 kernel/boot chain and ext4 root. Snapper/Limine rollback, the normal
update path and internal-audio support are not qualified. Installing unrelated
driver packages would not establish those missing capabilities.

The installed desktop created EGL contexts reporting Apple M4 (G16G B1) for
Wayland, GBM and the device platform. That demonstrates driver selection and
context creation, not every graphics workload.

The image helper also installs `etc/xdg/uwsm/env.d/90-m4-shader-cache` to enable
Mesa's on-disk shader cache. UWSM loads this system configuration after the
earlier bring-up environment in `/usr/share/uwsm`, using the installed session's
`XDG_CONFIG_DIRS=/etc/xdg`; owner configuration can
override it later. On this Air, `MESA_SHADER_CACHE_DISABLE=true` reproducibly
crashed Chromium's GPU process on a sandbox-denied `sched_setscheduler` call.
With the same private M4 driver, `ASAHI_MESA_DEBUG=nocompress` and cache disabling
set to `false`, both Chromium and Brave created a WebGL2 context reporting
Apple M4 and read back the expected pixel without GL errors. The sandbox
remained enabled. Browser launches must inherit the matching M4 library paths;
starting from a sparse SSH environment selected incompatible system libraries.

This corrects one startup failure, not the remaining video performance problem.
Local 60 fps playback still dropped frames after the change. The installed
session was tested by relaunching the browser; a fresh native owner boot with
this image override remains unqualified.

For an existing installation using the same M4 driver and UWSM layout, install
the override directly; the offline image helper intentionally refuses a live
root:

```bash
sudo install -d -m 0755 /etc/xdg /etc/xdg/uwsm /etc/xdg/uwsm/env.d
printf '%s\n' 'export MESA_SHADER_CACHE_DISABLE=false' | sudo tee /etc/xdg/uwsm/env.d/90-m4-shader-cache >/dev/null
sudo chmod 0644 /etc/xdg/uwsm/env.d/90-m4-shader-cache
```

Save work, log out and log back in to reload the session environment. Verify
`systemctl --user show-environment | grep '^MESA_SHADER_CACHE_DISABLE='`
reports `MESA_SHADER_CACHE_DISABLE=false`. Later
UWSM configuration, including owner overrides, still takes precedence. On the
development Air the value was changed in its existing custom, unowned
`/usr/share/uwsm/env.d/90-m4`, exported to the user service manager and tested
with relaunched browsers. That live repair did not test a fresh login using
the new image file. On POSIX build hosts the helper creates the new cache
directories as 0755 and its file as 0644 regardless of umask; it rejects an
existing parent without public read/traverse access before writing anything.
Windows fixture tests cover content only, not Linux permission semantics.

## Experimental session placement

The current J713 kernel reports the same scheduler capacity for its four
performance and six efficiency CPUs. In a local 1080p60 crossover, restricting
the existing compositor's main thread to the performance CPUs reduced reported
drops from 178/1196 to 29/1196; restoring its original mask produced 154/1196.
This implicates scheduling without relying on Internet buffering. Later full
Comic-Con playback completed with zero reported drops in two controlled browser
placement trials. These counters do not measure uniform physical-panel cadence.
An actual stock Chromium launch later reported 27/1815 drops for Comic-Con and
0/1196 for the 1080p60 fixture, with no buffering events. Its diagnostic bridge
failed during shutdown after recording both clips, so the overall qualification
did not pass. Smooth playback remains unfinished.

The image helper now stages `m4-session-placement.py` and an owner `post-boot.d`
hook. The packaged Omarchy startup invokes this hook after loading its startup
configuration. The helper derives logical performance CPU IDs from each online
CPU's device-tree node, requires the qualified J713/T8132 topology and unchanged
capacity values, and identifies the owner's actual compositor through the
current Hyprland instance. It changes only that main thread. A runtime user
drop-in applies the same CPU preference to future `omarchy-browser-*.service`
launches, keeping the packaged launcher intact. Unsupported hardware fails in
the hook without adding a condition that could block browser launches.

This is a scheduling workaround, not CPU frequency control, hardware video
decoding, or a global scheduler repair. Existing browser singletons keep their
current masks; direct launches that bypass the stock browser service do not use
its drop-in. New compositor threads and directly forked applications can inherit
the main thread's mask. Browser workers can independently widen theirs. Those
limits must remain visible when evaluating general desktop performance.

The helper saves the original main-thread mask with boot/PID/start identity,
refuses existing custom affinity or drop-in content, and can restore its own
state without touching a reused PID. It inspects the running user manager's
unit search paths and refuses other applicable CPU affinity assignments, even
ones currently hidden by a higher-priority file. Unreadable configuration stops
the operation before changes. The runtime drop-in disappears at reboot;
the hook rediscovers topology on the next login. On an installed candidate:

```sh
python3 ~/.local/lib/omarchy/m4-session-placement.py status
python3 ~/.local/lib/omarchy/m4-session-placement.py restore
```

The new helper and image integration are undergoing qualification. A fresh
owner boot and everyday application launches are not yet verified. Do not
describe these staged files as a finished video fix or a qualified new image.

The successful repeat boot logged `Touch MT ready` at 2.293 seconds; Hyprland
listed the multi-touch device and the owner confirmed physical operation.
Earlier boots failed AFE startup with `No HINT_L`. A temporary delayed-start
experiment did not fix that failure and also blocked the GPU service's udev
settle prerequisite. That rule was removed and is absent from image defaults.
One successful repeat boot does not establish reliable cold boot or explain the
earlier AFE failure.

The running kernel has zram and zstd support, and the candidate contains their
modules, but swap activation still needs the native check above. The power
daemon currently uses a placeholder driver, and the kernel exposes no CPU
frequency policies; hardware power/performance profiles remain unfinished.
Internal audio, repeated cold boots, quiet boot, safe updates and native
first-owner qualification of the rebuilt candidate remain open. None of these
observations qualifies another Mac model or the M4 Pro.

Run the configuration regressions on a leased test host:

```sh
python3 -m unittest discover -s tests -p test_m4_desktop_config.py
```
