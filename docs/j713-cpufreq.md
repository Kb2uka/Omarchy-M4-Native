# J713 CPU frequency scaling and core capacities

## Symptoms

- Cores never change speed. `/sys/devices/system/cpu/cpufreq` is empty, so the
  P cores sit at whatever clock iBoot leaves (1800 MHz) even when idle.
- The scheduler treats all ten cores as equal: every `cpu_capacity` reads 1024.
  At these clocks an E core does about a third of a P core's work, so latency
  work such as Hyprland's frame composition lands on E cores and video drops
  frames.
- The main-thread pin that worked around that (`50-m4-main-placement`) is
  inherited by everything Hyprland launches, so terminals and apps started
  from the desktop see 4 CPUs (`nproc` prints 4) instead of 10.

## Cause

The T8132 devicetree has no cluster performance controllers, OPP tables,
`performance-domains` or `capacity-dmips-mhz`. `apple-soc-cpufreq` is built
as a module in the shipped kernel but has nothing to bind to, and
`arch_topology` leaves every core at the default capacity.

Evidence from the owner's Air (2026-10-12):

- The ADT `pmgr` node lists the cluster DVFS blocks as `reg[5]`
  `0x2_10e20000` (E) and `reg[16]` `0x2_11e20000` (P).
- A read-only `/dev/mem` read of `DVFS_CMD` (+0x20) gives E `0x400101` and P
  `0x400104`: P-state 1 and 4. `DVFS_STATUS` (+0x50) reads E `0x21` and P
  `0x84`, which decode as current state 1/4 and target state 1/4 only in the
  T8112 layout (current state in bits 9:5, target in 4:0).
- The ADT `voltage-states1-sram`/`voltage-states5-sram` tables, with one-based
  state numbers, give E1 900 MHz and P1-P4 912, 1260, 1512 and 1800 MHz. The
  2026-10-07 bench run moved P 4→1→4 and E 1→2→1 with the stock driver and
  measured matching loop throughput.
- Pinned single-core throughput at those clocks (median of three runs per
  core, two cores per cluster):

  | Workload | E core (s) | P core (s) | E/P |
  | --- | --- | --- | --- |
  | Python integer loop | 0.361 | 0.102 | 0.282 |
  | zlib level 9 | 0.331 | 0.114 | 0.344 |
  | xz -6, one thread | 1.104 | 0.315 | 0.286 |
  | Python sort | 0.297 | 0.118 | 0.399 |
  | Geometric mean | | | 0.324 |

## Change

[`patches/j713-cpufreq.dtso`](../patches/j713-cpufreq.dtso) adds:

- `cpufreq@210e20000` and `cpufreq@211e20000`, compatible
  `apple,t8132-cluster-cpufreq` with the `apple,t8112-cluster-cpufreq`
  fallback, as upstream does for T8122.
- OPP tables that **stop at the state iBoot leaves each cluster in**: E at
  900 MHz only, P at 912, 1260, 1512 and 1800 MHz. No core can run faster or
  hotter than before. Linux sees no CPU die temperature on J713 yet, and
  upstream refuses higher states on later Apple SoCs without a CPU thermal
  policy, so the higher states wait for one.
- `capacity-dmips-mhz` of 1024 on P cores and 663 on E cores, so that at the
  table maxima the E capacity is 0.324 of P, as measured. The kernel then
  reports E cores at capacity 331.

[`tools/build-j713-cpufreq-dtb.py`](../tools/build-j713-cpufreq-dtb.py) applies
the overlay to the update 1 portable devicetree (SHA256 `e54c77a7…`) and
refuses a result that differs from the base in anything but those nodes and
properties. DTC 1.8.1 produced 48,148 bytes, SHA256
`033025bbdda3ce72b5eefe39d1d6d68209256fd43e4fd587c96ae1cf38978555`.
[`tools/m4-j713-update-boot-dtb.py`](../tools/m4-j713-update-boot-dtb.py) now
knows this devicetree. It replaces the alpha.1 or the update 1 devicetree inside
`boot.bin`, keeps `boot.bin.pre-cpufreq`, and never downgrades.

Two userspace changes ship with it:

- **Compositor placement without the inherited mask.**
  `m4-session-placement.py --main-only apply` (the existing login hook) sees
  the measured capacities and sets Hyprland's main-thread `uclamp_min` to 1024
  with reset-on-fork instead of pinning its affinity. On devicetrees without
  measured capacities (alpha.1, update 1) it keeps the previous pin. See
  [desktop image](j713-desktop-image.md).
- **Speaker protection runs real-time.** `speakersafetyd-j713.service` now sets
  `CPUSchedulingPolicy=fifo` and `CPUSchedulingPriority=20`, within its
  existing `LimitRTPRIO=50`. The daemon must renew its lease within 10.7 ms of
  each 21.3 ms capture period. As a normal task it misses that under full CPU
  load and fails closed, leaving the speakers silent until it is restarted.
  The first boot with the new devicetree hit it at startup. See the table
  below.

## Results on the owner's Air

Boot with the new devicetree: `apple_soc_cpufreq` loads by itself and
`schedutil` drives it:

```text
policy0 cpus=0 7 8 9 drv=apple-cpufreq gov=schedutil min=912000 max=1800000 avail=[912000 1260000 1512000 1800000]
policy1 cpus=1 2 3 4 5 6 drv=apple-cpufreq gov=schedutil min=900000 max=900000 avail=[900000]
cpu0 cap=1024  cpu1 cap=331  cpu6 cap=331  cpu7 cap=1024  cpu9 cap=1024
```

Kernel error and warning counts were no higher than on the previous boot (175
versus 184 errors, 196 versus 207 warnings at `warning` priority and above),
and no new error line appeared.

Video and placement, using a 60 s 1080p30 H.264 clip in mpv, full screen and
muted, started through Hyprland like a user launch (`vaapi-copy`, `gpu-next`).
"P share" is the fraction of 20 ms samples with Hyprland's main thread on a P
core:

| Devicetree and placement | P share | Dropped frames / 1799 | `nproc` from a Hyprland launch |
| --- | --- | --- | --- |
| update 1, no pin | 19.9% | 84 | 10 |
| update 1, old main-thread pin | 100% | 2 | 4 |
| cpufreq, no hook (three runs) | 78.0%, 30.0%, 12.8% | 2, 4, 6 | 10 |
| cpufreq, clamp set by hand (three runs) | 99.4%, 99.8%, 99.6% | 0, 2, 2 | 10 |
| cpufreq, clamp set by the login hook (two runs) | 99.7%, 100% | 2, 0 | 10 |

During playback the P cluster moved between 912, 1260, 1512 and 1800 MHz. A
`sleep` launched through Hyprland while the clamp was set read
`uclamp_min 0`, no reset-on-fork flag and CPUs 0-9. Hyprland itself read
`uclamp_min 1024` with all ten CPUs.

Speaker protection started with all ten cores busy (`python3 -c "while True: pass"` ×10),
six or eight starts per row:

| Devicetree and scheduling | Starts that stayed active |
| --- | --- |
| update 1, normal task | 1 of 6 |
| cpufreq, normal task, `schedutil` | 1 of 4 |
| cpufreq, normal task, P held at 1800 MHz | 2 of 4 |
| cpufreq, `SCHED_FIFO` 20 | 6 of 6, then 6 of 6 again after reboot |

Without load, three restarts on the cpufreq devicetree all reached nominal
gain, on P cores at 912 MHz and on E cores. Six earlier boots on the older
devicetrees started cleanly. The first cpufreq boot, without the real-time
setting, failed with `J713 backlog before renewal`. The next cpufreq boot, with
the setting, started cleanly. One boot is not a boot-time qualification.

## Existing installs

No release asset carries `j713-cpufreq.dtb` yet; publishing one is pending.
Until then, build it in a checkout of this repository on a Linux host with
DTC 1.8.1 (`dtc` and `fdtoverlay`). Use the `j713-poweroff.dtb` asset of
`v0.1.0-alpha.1-update.1` as the base:

```bash
curl -fL -O https://github.com/Kb2uka/Omarchy-M4-Native/releases/download/v0.1.0-alpha.1-update.1/j713-poweroff.dtb
sha256sum j713-poweroff.dtb  # must be e54c77a760a5b696486f9d80c7c83ca4295d641113bb9e54d13e259863bf9b07
python3 tools/build-j713-cpufreq-dtb.py --base j713-poweroff.dtb --output j713-cpufreq.dtb
sha256sum j713-cpufreq.dtb   # must be 033025bbdda3ce72b5eefe39d1d6d68209256fd43e4fd587c96ae1cf38978555
```

The builder checks the tree's structure, not its bytes. Another DTC version
can assign phandles or order nodes differently. The result then has a
different hash, and the updater refuses it as `Unknown replacement devicetree`.
Do not use such a file. All three parts are needed together. Apply the devicetree update without the
real-time protection setting and speaker protection can fail at boot.

```bash
# 1. Speaker protection (until the next image ships the updated unit):
sudo mkdir -p /etc/systemd/system/speakersafetyd-j713.service.d
printf '[Service]\nCPUSchedulingPolicy=fifo\nCPUSchedulingPriority=20\n' |
  sudo tee /etc/systemd/system/speakersafetyd-j713.service.d/50-realtime.conf
# 2. Placement helper used by the login hook:
install -m 0644 m4-session-placement.py ~/.local/lib/omarchy/m4-session-placement.py
# 3. Devicetree (inspect first; --apply writes, keeping boot.bin.pre-cpufreq):
sudo python3 m4-j713-update-boot-dtb.py --dtb j713-cpufreq.dtb
sudo python3 m4-j713-update-boot-dtb.py --dtb j713-cpufreq.dtb --apply
```

From `omarchy-m4-boot` 1-2 on, the M4 package channel recognizes this
devicetree and keeps it when a package update recomposes `boot.bin`; see
[package channel](package-channel.md). The published 1-1 composer does not know
it: it refuses and leaves `boot.bin` untouched. Join the channel on a cpufreq
machine only after 1-2 is published.

Then restart. If the post-boot hook was disabled (renamed to
`50-m4-main-placement.sample`), rename it back to enable the clamp. An old copy
of the helper refuses to pin on the new devicetree (`Scheduler capacity
changed`), so it can no longer cause the 4-CPU limit.

**Check after restart:**

```bash
cat /sys/devices/system/cpu/cpufreq/policy*/scaling_available_frequencies
cat /sys/devices/system/cpu/cpu*/cpu_capacity
systemctl is-active speakersafetyd-j713      # active
python3 ~/.local/lib/omarchy/m4-session-placement.py --main-only status
```

**Rollback:** `sudo cp /boot/efi/omarchy/boot.bin.pre-cpufreq /boot/efi/omarchy/boot.bin && sync`,
then restart. From macOS, copy the same file on the `EFI - OMARC` partition.

## Limits

- No CPU state above the handoff clock is offered: E stays at 900 MHz and P
  peaks at 1800 MHz, as before. Higher states need a CPU temperature source
  and a thermal policy first.
- CPU idle states are still absent (`cpuidle: none`, `idle=nop`).
- No energy model is provided, so energy-aware scheduling stays off.
  Placement uses capacity only.
- Battery life was not measured.
- `--main-only restore` puts the clamp back but leaves the compositor's
  reset-on-fork flag set; the kernel does not let a normal user clear it. For
  the nice-0 compositor it only affects the clamp its new children start with,
  which is the default.
