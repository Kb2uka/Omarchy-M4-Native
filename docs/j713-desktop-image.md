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

   It derives `/etc/skel/.config/chromium-flags.conf` from the packaged Chromium
   options and disables `OnBeginFrameThrottleVideo`. Existing disabled features
   are retained in the same switch; a conflicting enable, duplicate feature
   switch or unsupported syntax fails before any write. The packaged file must
   use LF line endings and one unquoted option per nonblank, noncomment line,
   without CR or NUL bytes, embedded whitespace or backslash escapes. ASCII spaces
   may surround options and indent blank lines or `#` comments; other forms
   require review because the Chromium launcher shell-parses each line. Feature
   switches must use two dashes and comma-separated bare names (ASCII letters,
   digits and underscores); trial/group/parameter forms require review.
   Every input and rewritten output line, including comments, is limited to
   2,046 UTF-8 content bytes. With its LF it fits in one read of the installed
   launcher's 2,048-byte buffer, without a separate newline chunk. This
   affects future accounts created from the candidate. Existing owners and
   Brave settings are unchanged. See the measured limits below.

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
update path are not qualified. The separate [J713 audio source package](j713-audio.md)
records owner-confirmed speaker playback and automatic startup of an exact pinned
runtime; it does not qualify fresh-image installation, audio suspend/resume or a
general runtime release. Installing unrelated driver packages would not establish
those missing capabilities.

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

This corrects one startup failure. Local 60 fps playback still dropped frames
after the change. The installed
session was tested by relaunching the browser; a fresh native owner boot with
this image override remains unqualified.

### Chromium video timing

A later four-run A/B/B/A comparison on the installed J713 used Chromium
153.0.8010.36, the matching private M4 graphics libraries and the same local
60.633-second, 1080-by-1920, 30 fps H.264 clip without an audio track. The only
B change was `--disable-features=OnBeginFrameThrottleVideo`. The baseline runs
dropped 51 and 71 frames out of 1,815 each; the B runs dropped 1 and 5.
That is 122/3,630 versus 6/3,630, a 95.1% reduction in this browser counter.
Disabling the feature changed both frame-request cadence and the deadline
window; the experiment does not isolate their individual contributions.

The setting is retained in the installed owner's Chromium flags. A separate
normal Omarchy browser launch verified that the actual Chromium child loaded
it alongside the packaged options. Neither test proves fully smooth panel
output: callback gaps remain, and frame submission is not physical display
delivery. Unmuted playback, other codecs and browser versions, 60 fps and other
frame rates, battery use, thermals, and a fresh owner boot using the candidate
remain unqualified. The local fixture removes
internet buffering from this comparison; it does not rule out separate Wi-Fi
issues. Revalidate this default when changing the browser or graphics stack.

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

The image helper now stages the accepted **main-thread-only** startup policy in
`etc/skel/.config/omarchy/hooks/post-boot.d/50-m4-main-placement`. The packaged
Omarchy post-boot runner executes it in the owner's graphical session, after
startup configuration loads. Its sole command is:

```sh
exec /usr/bin/python3 "$HOME/.local/lib/omarchy/m4-session-placement.py" --main-only apply
```

The mode first reads each CPU's scheduler capacity. On a devicetree with the
measured core capacities from [CPU frequency scaling](j713-cpufreq.md) (P cores
1024, E cores equal and lower), it does not touch affinity at all: it sets the
Hyprland main thread's minimum utilization clamp (`uclamp_min`) to 1024 with
`SCHED_FLAG_RESET_ON_FORK`. The scheduler then keeps that thread on P cores and
the P cluster at full clock while it runs, and every process it launches starts
with the default clamp and all ten CPUs. Saved state uses the policy
`hyprland-main-uclamp-v1`; restore puts the original clamp back. Other
capacity layouts are refused. The rest of this section describes the affinity
policy used while every core reports capacity 1024 (the alpha.1 and update 1
devicetrees).

That policy derives logical CPU IDs from each online CPU's device-tree node,
requires J713/T8132 with four `apple,donan-p` and six `apple,donan-e` CPUs and
capacity 1024, and matches both the current Hyprland instance signature and
Wayland socket. The PID must be that owner's Hyprland main thread. The helper
explicitly writes only that thread's affinity; existing compositor worker
threads and browser processes retain their masks. It creates no browser drop-in
and does not reload the user manager.

This also sets the inherited default for threads and applications subsequently
created by that main thread. Directly launched terminal programs can therefore
use four eligible CPUs instead of ten, while applications started separately by
the user manager retain its mask. The earlier stock terminal measurement below
observed this inheritance. Restoring the compositor's mask does not restore
masks already inherited by its children. The startup policy accepts these
scheduling limits; it does not promise unrestricted terminal workloads, better
battery life, or improved performance for every launch path.

The helper stores boot/PID/start identity and the original mask in the owner's
runtime directory, separately from the older experiment. Repeat calls for the
same session are idempotent. Custom main affinity, modified saved state,
another live compositor's state, and ambiguous sessions are refused. Restore
acts only on the same live process and the tool's own mask. If a successful
affinity call reads back a different mask, the helper refuses to report success
and retains the original-mask record; it does not overwrite a mask it cannot
identify as its own. After correcting the underlying cpuset or CPU-hotplug
condition, end the graphical session and log in again: the new compositor lets
the helper discard the stale record and apply the policy to the new process.
As the owner:

```sh
python3 ~/.local/lib/omarchy/m4-session-placement.py --main-only status
python3 ~/.local/lib/omarchy/m4-session-placement.py --main-only restore
```

To opt out across subsequent logins, remove the managed `50-m4-main-placement`
hook and then run the main-only restore command. The offline installer refuses
an existing differing hook before writes and preserves unrelated custom hooks.
It still refuses the old automatic `50-m4-placement` hook; review/remove that
old hook explicitly instead of running both policies. Image defaults apply to
newly provisioned owners through `/etc/skel`; this command does not migrate an
existing home or modify the live machine. The new startup-hook entry point
requires a fresh native session qualification separately from its leased
fixture tests. Runtime main-only placement has improved the measured J713
video case, but this is not a universal smoothness or mixed-workload claim.

The older CLI behavior **without `--main-only`** remains a separate manual
experiment for compatibility. It also creates a runtime browser-service
CPUAffinity drop-in. It is not the accepted automatic startup policy, and the
new hook never invokes it. The following measurements and constraints describe
that earlier combined experiment.

This is a scheduling workaround, not CPU frequency control, hardware video
decoding, or a global scheduler repair. Existing browser singletons keep their
current masks; direct launches that bypass the stock browser service do not use
its drop-in. New threads and directly forked applications inherit the mask of
the thread that creates them. In the qualified stock terminal launch, both the
terminal and its child inherited the four performance CPUs. Affinity-aware job counts can
therefore use four CPUs instead of all ten, and terminal workloads share those
CPUs with the compositor.
Browser workers can independently widen their masks. These are explicit limits
of this opt-in experiment.

A matched placement-off then placement-on trial played the full local Comic-Con
clip while four independent C++ compilation jobs ran. Both arms completed and
restored the original desktop, with no buffering, corruption or new GPU fault.
Reported video drops rose from 32/1815 to 45/1815, while compilation throughput
rose from 4.544 to 6.556 compilations per second. Workers reported ten eligible
CPUs without placement and four with it; both arms used exactly four jobs.
This single pair demonstrates a workload trade-off, not improved mixed-load
video or representative full-project build performance. Order/cache effects and
thermal drift were not isolated. Browser callback timing is not physical-panel
cadence. Persistent placement was not retained after this test.

The helper saves the original main-thread mask with boot/PID/start identity,
refuses existing custom affinity or drop-in content, and can restore its own
state without touching a reused PID. It inspects the running user manager's
unit search paths and refuses other applicable CPU affinity assignments, even
ones currently hidden by a higher-priority file. Unreadable configuration stops
the operation before changes. It also refuses same-named drop-ins or masks
that could hide its runtime file, regardless of their contents. The legacy runtime
drop-in disappears at reboot; the new startup hook never reapplies that browser
policy. If the user manager survives
logout, its runtime drop-in can remain until explicit restore or reboot.
To try the legacy experiment on an image with the new startup hook, first opt
out of that hook and run `--main-only restore` as described above. Otherwise
the legacy command correctly refuses the already changed compositor mask.
Do not run both placement modes together. Then, as the desktop owner:

```sh
python3 ~/.local/lib/omarchy/m4-session-placement.py apply
python3 ~/.local/lib/omarchy/m4-session-placement.py status
python3 ~/.local/lib/omarchy/m4-session-placement.py restore
```

The legacy combined experiment is not promoted by the main-only hook. Native
qualification of the new hook and its exact installed bytes remains necessary;
do not describe source/fixture tests as a completed new-image boot test.

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
Repeated cold boots, quiet boot, safe updates, audio suspend/resume and native
first-owner qualification of the rebuilt candidate remain open. Internal speaker
playback is covered by the [separate pinned audio qualification](j713-audio.md).
None of these observations qualifies another Mac model or the M4 Pro.

Run the configuration regressions on a leased Linux test host using discovery
(the platform skip in the placement suite relies on this invocation):

```sh
python3 -m unittest discover -s tests -p test_m4_desktop_config.py
python3 -m unittest discover -s tests -p test_m4_session_placement.py
```

`restore` restores the saved compositor's main-thread mask and removes the
managed browser drop-in. It does not widen masks of other compositor threads
or applications started during placement; they keep their masks until they
exit or are explicitly changed. If this owner previously received the older
automatic hook, remove that hook before running `restore`:

```sh
rm ~/.config/omarchy/hooks/post-boot.d/50-m4-placement
python3 ~/.local/lib/omarchy/m4-session-placement.py restore
```

The image builder stages defaults in `/etc/skel`, without rewriting existing
owners' home directories. New owners receive the helper but no placement hook.

If the managed runtime drop-in was manually edited, restore preserves that file
and its saved identity record, reports the conflict, and restores its own main
thread when possible. Save or move the customized drop-in elsewhere, then run
`restore` again to finish cleanup. The saved record is deliberately retained
until that conflict is resolved; it is not a persistent file across reboot.
