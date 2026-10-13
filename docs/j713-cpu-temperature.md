# J713 CPU die temperature

## Symptom

Linux on the J713 shows no CPU temperature. `macsmc_hwmon` exposes only the
battery hotspot, the charge regulator and the Wi-Fi module, and the only thermal
zone is the battery. Higher CPU clocks (#60) cannot be considered without a CPU
temperature, so [CPU frequency scaling](j713-cpufreq.md) stops at the clocks
iBoot hands over (E 900 MHz, P 1800 MHz).

## Cause

The SMC measures the CPU die, but the J713 devicetree lists none of those keys
for `macsmc_hwmon`. The driver reports only the `temperature-*` children of the
SMC `hwmon` node.

## Finding the keys

A read-only survey module on the owner's Air (kernel
`7.1.9-m4-gpu-backport-20261008`, built against the pinned kernel tree) listed
every SMC key with `apple_smc_get_key_by_index`/`apple_smc_get_key_info`, then
read every readable, non-function `T` key once a second. It never wrote a key.
The SMC holds 2,129 keys, 200 of them `T` keys.

Sampled through 20 s idle, 60 s with the four P cores busy, 60 s cooling, 60 s
with the six E cores busy and 40 s cooling (239 samples, at the handoff clocks):

- `TCMz` was within 0.5 C of the hottest `Tp`/`Te` key in every sample and
  never above it. It is the SMC's own CPU maximum.
- The `Tp` keys follow the P cluster and the `Te` keys the E cluster. Measured
  during the bounded runs below, starting each from its own baseline:

  | Load | Mean `Tp` rise | Mean `Te` rise |
  | --- | --- | --- |
  | Four P cores, 2088 MHz, 20 s | 8.0 C | 5.5 C |
  | Six E cores, 2172 MHz, 20 s | 3.6 C | 6.5 C |

- The SMC refreshes these keys about once a second: a 100 ms reader saw the
  same `TCMz` value for about ten consecutive reads.
- At idle the P die reads 50-68 C. The kernel still runs with `idle=nop`, so
  idle CPUs spin instead of sleeping.

## Change

[`patches/j713-cpu-temperature.dtso`](../patches/j713-cpu-temperature.dtso)
adds seven reporting-only channels to the SMC `hwmon` node. Nothing uses them
for control. The P and E keys are the six names upstream reads on T8140.

| Key | Label |
| --- | --- |
| `TCMz` | CPU Die Max |
| `Tp01`, `Tp05`, `Tp09`, `Tp0D` | P-cluster Temperature 1-4 |
| `Te05`, `Te0S` | E-cluster Temperature 1-2 |

[`tools/build-j713-cputemp-dtb.py`](../tools/build-j713-cputemp-dtb.py) applies
the overlay to the cpufreq devicetree (`033025bb…`) and refuses a result that
differs from it in anything but those seven nodes. Clocks, OPP tables and every
other property stay as they are. DTC 1.8.1 produced 48,724 bytes, SHA256
`08e8e719381f88a1e50d821c145dad5b2d97fd772609e13d7778e19795e9cad1`.

[`tools/m4-j713-update-boot-dtb.py`](../tools/m4-j713-update-boot-dtb.py)
accepts it only in place of the cpufreq devicetree, keeping
`boot.bin.pre-cputemp`. A machine that never applied the cpufreq update cannot
reach cpufreq through this file. The boot composer (`omarchy-m4-boot` 1-3)
knows it and keeps it when a package update recomposes `boot.bin`.

## Above the handoff clocks: bounded measurements (#60)

To learn whether the firmware limits the CPU by itself, a second bench module
asked one cluster for one higher P-state at a time. It refused to start unless
the cluster was at its handoff state (cpufreq pinned there with the
`userspace` governor) and `TCMz` was below the abort temperature. It sampled
`TCMz` and `DVFS_STATUS` every 100 ms, and restored the handoff state when
`TCMz` reached 92 C, when an SMC read failed, when its time limit ran out, or
when it was removed. Busy loops pinned to the cluster counted iterations, so
throughput shows the clock actually run.

| Request | Cores loaded | Ran (current state) | Throughput vs handoff | `TCMz` | Stopped by |
| --- | --- | --- | --- | --- | --- |
| P5, 2088 MHz | 4 P | P5 | 1.166 (clock ratio 1.160) | 74 → 83 C, still rising slowly at 20 s | 20 s limit |
| P8, 2868 MHz | 4 P | P8 | 1.596 (1.593) | 78 → 91 C at the first refresh (0.3 s), 95.3 C at 1.3 s | 92 C abort |
| P12, 3624 MHz | 1 P | P12 | too short to measure | 73.5 → 105.5 C at the first refresh (0.5 s) | 92 C abort |
| P16, 3984 MHz | 1 P | **P15**, 3936 MHz | too short to measure | 75 → 114.8 C at the first refresh (0.4 s) | 92 C abort |
| P20, 4464 MHz | 1 P | **P15**, 3936 MHz | too short to measure | 75.4 → 112.0 C at the first refresh (0.3 s) | 92 C abort |
| E3, 1476 MHz | 6 E | E3 | 1.642 (1.640) | 69.6 → 70.3 C, flat | 20 s limit |
| E5, 2172 MHz | 6 E | E5 | 2.420 (2.413) | 69.3 → 73.3 C, flat after 7 s | 20 s limit |
| E8, 2892 MHz | 1 E | **E7**, 2808 MHz | 3.131 (E7 3.120) | 70.6 → 78.8 C at 8 s | 8 s limit |

Every run returned to the handoff state (`DVFS_CMD` `…0104` for P, `…0101`
for E). Afterwards the kernel log held no new warning or thermal message, speaker
protection stayed active, and `TCMz` was back at 71 C.

What this shows:

- **The requested states are real.** P5, P8, P12, E3 and E5 ran as asked by
  `DVFS_STATUS`; throughput confirmed it for P5, P8, E3 and E5 and matched E7
  when E8 was asked. The ADT voltage tables iBoot leaves are usable.
- **Something caps the top states.** P16 and P20 were accepted as the target
  (`DVFS_STATUS` bits 4:0) but the cluster ran P15. E8 ran E7. These runs
  cannot tell a fixed limit from a power limit or a fast hardware thermal
  limiter; they lasted under half a second for P16 and P20.
- **Nothing stepped the clock down further as the die heated.** Up to P15 the
  current state never fell below what the cap allowed, even at 114.8 C, until
  Linux restored the handoff state. A Linux policy cannot count on the
  firmware to bring the temperature back down.
- **The P die heats faster than the sensor reports.** From P8 up, the first
  refresh after the change was already 13-40 C hotter. With a refresh about
  once a second, a Linux policy only learns of the heat after it has happened;
  the 92 C abort was overrun by 3-23 C. Upstream describes the same behavior on
  the fanless T8140 Air and drives its policy from the raw reading.
- **The E cluster is mild.** Six E cores at 2172 MHz settled near 73 C within
  20 s. That is cooler than the P die at the handoff clock.

These were bursts of at most 20 s on one machine. They do not qualify any
clock for sustained use.

## Building

On a Linux host with DTC 1.8.1 (`dtc` and `fdtoverlay`; Arch Linux `dtc`
1:1.8.1-1 was used), from the `j713-poweroff.dtb` asset of
`v0.1.0-alpha.1-update.1`:

```bash
python3 tools/build-j713-cpufreq-dtb.py --base j713-poweroff.dtb --output j713-cpufreq.dtb
sha256sum j713-cpufreq.dtb   # must be 033025bbdda3ce72b5eefe39d1d6d68209256fd43e4fd587c96ae1cf38978555
python3 tools/build-j713-cputemp-dtb.py --base j713-cpufreq.dtb --output j713-cputemp.dtb
sha256sum j713-cputemp.dtb   # must be 08e8e719381f88a1e50d821c145dad5b2d97fd772609e13d7778e19795e9cad1
```

Another DTC version can lay the tree out differently; the updater then refuses
the file as `Unknown replacement devicetree`.

## Existing installs

**Connected to M4 updates? You get this automatically** with `omarchy-m4-boot`
1-4 through `omarchy update`; restart once. The steps below are for an Air that
is not connected.

Only on a machine already running the cpufreq devicetree (see
[CPU frequency scaling](j713-cpufreq.md)); on any other devicetree the updater
refuses:

```bash
sudo python3 m4-j713-update-boot-dtb.py --dtb j713-cputemp.dtb           # inspect
sudo python3 m4-j713-update-boot-dtb.py --dtb j713-cputemp.dtb --apply   # keeps boot.bin.pre-cputemp
```

Restart, then:

```bash
grep -H . /sys/class/hwmon/hwmon*/temp*_label | grep -E 'CPU|cluster'
```

**Rollback:** `sudo cp /boot/efi/omarchy/boot.bin.pre-cputemp /boot/efi/omarchy/boot.bin && sync`,
then restart.

## Limits

- Reporting only. No thermal zone, trip point or frequency cap uses these
  channels, and the clocks are unchanged.
- Full clocks (#60) still need a Linux CPU thermal policy. In these runs the
  firmware did not bring the temperature down by itself, and the die moved
  tens of degrees between readings at P8 and above.
- `TCMz` is the SMC's maximum; the four `Tp` and two `Te` channels are samples
  of their clusters, not every sensor.
- Key names follow the SMC key groups and the upstream T8140 choice. Apple
  does not document them.
