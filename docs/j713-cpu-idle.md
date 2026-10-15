# J713 CPU idle, E-cluster clocks and a CPU thermal zone

Issue [#66](https://github.com/Kb2uka/Omarchy-M4-Native/issues/66) asks for
macOS-like CPU behaviour on the fanless Air: short bursts at high clocks, cool
the rest of the time. Its first step is CPU idle
([#65](https://github.com/Kb2uka/Omarchy-M4-Native/issues/65)). This change
delivers that step, raises the E-cluster clocks, and adds a CPU die thermal zone
as a safety net. P-core boost above 1800 MHz is the next step:
[J713 P-core boost](j713-cpu-boost.md).

## Symptoms

- Idle cores never stop. The kernel boots with `idle=nop`, there is no CPU idle
  driver (`cpuidle: none`), and an idle core spins at its cluster's clock. The
  idle Air draws about 9.6 W (SMC `PSTR`) and its CPU die reads 68 C.
- Because the four P cores share one clock, a single busy P core at a higher
  state also runs the three spinning P cores at that state. In the #60 bench,
  one core at P12 (3624 MHz) took the die from 73.5 to 105.5 C in half a second.
- The E cores stop at the 900 MHz iBoot handoff state, about a third of a P
  core's work.

## Cause

On M1 to M3 a plain `wfi` only clock-gates the core; it powers down only when
the kernel sets the ok-to-power-down bits in `s3_5_c15_c5_0` (CYC_OVRD), and
`drivers/cpuidle/cpuidle-apple.c`'s "CPU PD" state saves what that loses first.
On M4 iBoot locks those bits: writing the register raises an SError, and every
`wfi` powers the core down and zeroes its general registers ([Asahi Linux 7.2
progress report](https://asahilinux.org/2026/08/progress-report-7-2/), [The
forgetful CPU](https://yuka.dev/blog-2026-10-02-linux-m4.html)). A kernel `wfi`
that does not expect this crashes, so the boot uses `idle=nop`. `cpuidle-apple`
leaves T8132 out because its state writes the locked register.

## Change

### CPU idle module

[`cpu/j713/j713_cpuidle.c`](../cpu/j713/j713_cpuidle.c) is `cpuidle-apple`'s
"CPU PD" sequence without the register writes:

1. `cpu_pm_enter()` saves FP/SIMD and the other per-CPU state its notifiers own.
2. x18-x30 go on the stack; x0-x17 are caller-saved, so the call already gives
   them up.
3. `wfi` repeats until `ISR_EL1` shows a pending interrupt. The core is powered
   down meanwhile.
4. The registers come back from the stack, then `cpu_pm_exit()`.

State 0 returns at once, which is exactly what `idle=nop` does. It is the safe
state and the fallback for any CPU-latency request tighter than state 1's
10 µs. `idle=nop` stays on the command line for early boot. Nothing in the
module writes a system or MMIO register. It loads only on `apple,t8132` and
cannot be unloaded while registered (the kernel pins an idle driver's module).
It ships out of tree, built on the alpha.1 kernel tree by
[`cpu/j713/build-module.sh`](../cpu/j713/build-module.sh), so the kernel package
does not change.

### Loading it: `omarchy-m4-cpu`

`m4-cpu-idle.service` runs `m4-cpu-idle --boot` once the root filesystem is
writable (`Type=exec`, so it never holds up the desktop). The helper loads only
the pinned module (`a2a7947a…`), only on the pinned kernel and board, and only
while no other idle driver is registered. It follows the headphone package's
one-strike rule:

- An attempt marker is written durably before the module loads and removed
  after a 120 s settle period in which the cores must power down through the
  driver. A lost-state fault shows within milliseconds of a core's first
  power-down, so a boot that dies while loading or settling leaves the marker,
  and the next boot loads nothing. `sudo m4-cpu-idle --start` retries in the
  running session.
- `sudo m4-cpu-idle --disable` is the off switch (hold file
  `/var/lib/omarchy-m4/cpu-idle/hold`); `--enable` turns it back on. A loaded
  driver stays until the next restart.
- `m4-cpu-idle` with no option reports the state.

### E-cluster states and the CPU thermal zone

[`patches/j713-ecluster-thermal.dtso`](../patches/j713-ecluster-thermal.dtso),
built by [`tools/build-j713-ecluster-dtb.py`](../tools/build-j713-ecluster-dtb.py)
on the CPU temperature devicetree (`08e8e719…`):

- **E states E2-E5:** 1080, 1476, 1860 and 2172 MHz, from the ADT
  `voltage-states1-sram` table with one-based state numbers. The E cluster now
  scales 900-2172 MHz. `capacity-dmips-mhz` stays 663, because the work per MHz
  does not change (below), so the kernel reports E cores at capacity 800 against
  P at 1024. The P table is unchanged.
- **CPU die thermal zone `cpu-die-thermal`:** reads `TCMz` (CPU Die Max,
  temperature channel 6 of the SMC hwmon node), with a passive trip at 95 C
  (5 C hysteresis) that steps both clusters down through their cpufreq cooling
  devices. Both polling delays are 1 s, the SMC's refresh rate. There is no
  critical trip: a short burst can read above 95 C for a second, and that must
  never shut the Air down.

The builder refuses any result that differs from the base beyond those nodes,
checks that channel 6 is `TCMz` and that phandles `0x07` and `0x01` are the P
and E policy CPUs. DTC 1.8.1 (Arch `dtc` 1:1.8.1-1) produced 49,616 bytes,
SHA-256 `fdedc09f2fe8ed681a61208589024e01fb5efa77beebbd0eb4ad267c08e77f7c`.
`omarchy-m4-boot` 1-10 carries it; the updater and composer accept it only after
the CPU temperature devicetree (backup `boot.bin.pre-ecluster`).

## Results on the owner's Air

Air on AC power, so power is the SMC's system total `PSTR`, not battery drain.
Kernel `7.1.9-m4-gpu-backport-20261008`, at the login screen, 0.2-0.4% CPU busy.

### Idle

| Idle, 2 min | `idle=nop` | Idle module, all 10 cores |
| --- | --- | --- |
| System power `PSTR` | 9.65 W | **5.47 W** |
| CPU die `TCMz` | 68.8 C | **54.5 C**, still falling |
| P / E cluster sensors | 57-58 C | 44-45 C |

With the module on the six E cores only, idle power was 8.93 W against 9.66 W:
most of the saving is the P cores.

### Correctness

The module was widened one stage at a time, each after a restart:

| Stage | Power-down entries | Self-checked rounds | Wrong results |
| --- | --- | --- | --- |
| CPU 6 only | 9,100 during the run | 15,629 | 0 |
| E cores 1-6 | 7,142-13,373 per core | 70,789 | 0 |
| All 10 cores | 9,592-201,389 per core | 143,206 | 0 |

The self-checking workload ([`cpu/j713/bench/verify.py`](../cpu/j713/bench/verify.py)) computes
SHA-256, integer and floating-point sums with known answers between 0.2-2 ms
sleeps, so the core it runs on powers down thousands of times. The kernel log
had no new warning or error line at any stage.

### Single-core boost with idle on

The #60 bounded bench (`j713_pstate_bench`, 92 C abort), one P core loaded and
the other three idle:

| Request | `idle=nop` (#60) | Idle module | Throughput |
| --- | --- | --- | --- |
| P8, 2868 MHz | 4 cores: 91 C at 0.3 s | +9 C step, 72.3 C at 20 s | 1.60x |
| P12, 3624 MHz | 105.5 C at 0.5 s | +22 C step, 90.8 C at 20 s | 2.02x |
| P15, 3936 MHz | 114.8 C (as P16) | +29 C step to 93.1 C at 0.5 s | 2.20x |

With the idle cores powered down, a boost is an immediate temperature step that
depends on the state, followed by a slow creep (0.15 C/s at P8, 0.35 C/s at
P12). That makes a predictive P-state cap possible, which is the next change.

### E states and the all-core soak

Every E state ran as requested (`cpuinfo_cur_freq` read back the state asked
for). Throughput on one E core relative to 900 MHz was 1.138x at 1080, 1.486x at
1476, 1.951x at 1860 and 2.319x at 2172 MHz.

All ten cores busy for 10 minutes (one busy loop pinned to each), with the
idle module loaded and `schedutil` choosing the clocks:

| Time | `TCMz` | `PSTR` | P | E | Cooling state |
| --- | --- | --- | --- | --- | --- |
| 1 min | 73.1 C | 11.66 W | 1800 MHz | 2172 MHz | 0 |
| 5 min | 77.2 C | 11.78 W | 1800 MHz | 2172 MHz | 0 |
| 10 min | 78.6 C (peak) | 11.83 W | 1800 MHz | 2172 MHz | 0 |

The die settled 16 C below the trip and nothing throttled. With E cores at
0.78 of a P core instead of 0.32, the ten cores do about 47% more work than at
the handoff clocks (8.7 P-core equivalents against 5.9).

### Thermal zone

To show the zone acts, its trip was lowered to 70 C at run time
(`trip_point_0_temp`) with all ten cores busy, then restored to 95 C. It began
throttling 1.5 s after the die crossed 70 C and held it at 68.4-71.3 C for
150 s, stepping P between 912 and 1512 MHz and E between 1080 and 1860 MHz. Five
seconds after the 95 C trip was restored, both cooling states were back at 0.
The script is [`cpu/j713/bench/zone_test.py`](../cpu/j713/bench/zone_test.py).

## Existing installs

**Connected to M4 updates? You get this automatically.** `omarchy-m4-update`
installs `omarchy-m4-boot` 1-10 and `omarchy-m4-cpu` 1-1; restart once. Check:

```bash
m4-cpu-idle                                          # "state": "loaded"
cat /sys/devices/system/cpu/cpuidle/current_driver   # j713_idle
cat /sys/devices/system/cpu/cpufreq/policy1/scaling_available_frequencies
cat /sys/class/thermal/thermal_zone*/type            # cpu-die-thermal
```

**Off switch:** `sudo m4-cpu-idle --disable`, then restart.
**Devicetree rollback:** `sudo cp /boot/efi/omarchy/boot.bin.pre-ecluster
/boot/efi/omarchy/boot.bin && sync`, then restart; a later package update
recomposes it.

## Limits

- P cores still stop at 1800 MHz here. The guarded P boost is
  [J713 P-core boost](j713-cpu-boost.md).
- No energy model, so energy-aware scheduling stays off.
- Power was measured on AC with the SMC's `PSTR`; battery life was not measured.
- The thermal zone reacts at the SMC's one-second refresh. It protects against
  sustained heat, not against a one-second burst.
- One Air (owner's, 1000 mV GPU). Other M4 Airs are untested.
