# J713 P-core boost to 4464 MHz with a predictive guard

Issue [#66](https://github.com/Kb2uka/Omarchy-M4-Native/issues/66) asks for
macOS-like CPU behaviour on the fanless Air: short bursts at the top clock,
cool the rest of the time. CPU idle, E cores to 2172 MHz and a CPU die thermal
zone came first ([`docs/j713-cpu-idle.md`](j713-cpu-idle.md)). This change
lets the P cores run every state up to P20 (4464 MHz), the top of the M4's
table, and the E cores up to E7 (2808 MHz), under a guard that predicts the
heat before it arrives.

## Symptoms

- The P cores stop at 1800 MHz (P4), the state iBoot leaves them in, about 40%
  of the chip's rated single-core clock.
- The E cores stop at 2172 MHz (E5).

## Cause

The higher states were held back on purpose (#55, #60), because the hardware
runs them without regard to heat:

- The P cluster runs any requested state within 0.1 ms. Its only limit is by
  the number of busy P cores (P17 with one, P16 with two, P15 with four), and
  it never steps down for heat: the #60 bench reached 114.8 C at P15 with no
  throttle.
- The CPU die (`TCMz`) gains up to about 58 C within two seconds of a higher
  state, while the SMC refreshes that reading about once a second. A policy
  that only reacts to the reading is always late: #60's 92 C abort was overrun
  by 3-23 C.

## Change

### Boost states behind a boost gate

[`patches/j713-pboost.dtso`](../patches/j713-pboost.dtso), built by
[`tools/build-j713-pboost-dtb.py`](../tools/build-j713-pboost-dtb.py) on the
E-cluster devicetree (`fdedc09f…`), adds:

- **P5-P20 and E6-E7** from the ADT tables, one-based as in
  `j713-cpufreq.dtso`. The owner's Air (`air-adt-20261006.bin`) lists:

  | Table | States (MHz) |
  | --- | --- |
  | `voltage-states5-sram` (P) | 912, 1260, 1512, 1800, 2088, 2352, 2616, 2868, 3096, 3300, 3468, 3624, 3756, 3876, 3936, 3984, 4044, 4104, 4416, 4464 |
  | `voltage-states1-sram` (E) | 900, 1080, 1476, 1860, 2172, 2496, 2808, 2892 |

  E8 (2892 MHz) is left out: the hardware runs it as E7. They are ordinary
  OPPs, not turbo ones, because the kernel takes each cluster's capacity and
  schedutil's frequency scale from the policy maximum when the policy is
  created. `capacity-dmips-mhz` stays as measured, so E cores now report 417
  against P at 1024.
- **A boost gate:** a passive trip at 20 C on `cpu-die-thermal`, below any
  reading, that pins the P cooling device to state 16, which is P4. Until
  something raises that trip, P runs no faster than before. The 95 C trip for
  both clusters stays.

DTC 1.8.1 produced 50,708 bytes, SHA-256
`c4f036b819ce757ce790b01779a3f7532eacd5cb1c1097fa20286d1a9c9ec6ca`. The builder
refuses any result that differs from the base beyond those nodes, checks the
base's tables, trips and the P policy CPU's phandle, and its tests build the
committed overlay with the real `dtc`.

### The guard: `j713_pguard`

[`cpu/j713/j713_pguard.c`](../cpu/j713/j713_pguard.c) holds a frequency-QoS
maximum on the P policy. It allows the highest P state whose predicted peak
stays at or below 100 C:

> peak = base + step[busy P cores][state] + E-cluster heat

- **Step table:** the measured `TCMz` rise from P4 to each state with one to
  four busy P cores, rounded up and filled upward between measured points
  ([`cpu/j713/j713_pguard_policy.h`](../cpu/j713/j713_pguard_policy.h)).
  `tests/test_j713_pguard.py` holds every measurement below and fails if the
  table is under one.
- **Fast path:** a deferrable 16 ms timer on each P core counts busy P and E
  cores from their idle time. When more cores turn busy, the cap drops within
  about 20 ms, long before the extra heat builds. An idle cluster runs no
  timer.
- **Slow path:** every 250 ms it reads `TCMz`. On each new reading it
  estimates the base, the temperature at P4: it subtracts the measured **fast
  heat** (below) of the fewest busy P cores and the lowest P state seen in the
  last 750 ms, and only when a P core was at least 90% busy through the
  reading's window. Otherwise it subtracts nothing. Only this path raises the
  cap.
- **Fast heat table:** the part of a state's rise that leaves the die within
  two seconds of the cluster returning to P4, measured for every state from P5
  up to the first that reached 95 C, on one to four busy P cores, twice
  ([Settled heat](#settled-heat-the-base-estimate)). It takes the lower run,
  rounded down, and is non-decreasing in state, so the base can only err
  high. The rest of a rise, 0-12 C of slow creep, is package heat that stays
  after the drop and is rightly part of the base. The fast heat leaves with a
  time constant of about 0.25 s, so looking back 750 ms is enough.
- **Fails closed:** the cap starts at P4, returns there on any SMC read error,
  and pins P4 from 103 C until the die is below 95 C. The guard refuses to
  load without the J713 idle driver (idle P cores would spin at the boosted
  clock), on any other CPU layout or table, or without the closed gate. It
  opens the gate itself, raising the trip to 150 C, only after its own cap is
  in place, so the gate is never open without the guard. It cannot be
  unloaded, and it writes no hardware register.

The math is a pure header that the tests compile on the host, including the
property that no state it allows predicts a peak over the limit for any base,
core count and E-cluster load.

### Loading it: `omarchy-m4-cpu` 1-3

`m4-cpu-idle --boot` loads the idle driver and then the pinned guard
(`07adfa65…`) inside the same one-strike window: a boot that dies while either
loads or settles makes the next boot load neither. A guard that is missing,
differs from its pin or fails to load leaves the gate closed and P at
1800 MHz, while CPU idle still loads. Without the boost devicetree, the guard
waits for it.

With boost on, the helper also sets `sched_util_clamp_min_rt_default` to 412.
schedutil runs a real-time task at that floor, by default the policy maximum.
Speaker protection is real-time and wakes a P core every few milliseconds, so
with the default the idle P cluster sat above 3 GHz half the time. 412 maps to
4464 MHz × 412 / 1024 = 1796 MHz, which resolves to P4: real-time tasks keep
the 1800 MHz they had before.

- `m4-cpu-idle` reports `boost`, `gate`, `guard_loaded` and `rt_floor`.
- `sudo m4-cpu-idle --boost off` closes the gate at once (P back to 1800 MHz),
  restores the real-time floor that boost replaced (the kernel default unless
  you set your own), and keeps the guard unloaded at every boot. `--boost on`
  reopens the gate at once if the guard is loaded, or at the next boot.

`omarchy-m4-boot` 1-11 carries the devicetree; the updater and composer accept
it only after the E-cluster devicetree, with backup `boot.bin.pre-pboost`.

## Results on the owner's Air

Kernel `7.1.9-m4-gpu-backport-20261008`, on AC, at the login screen. Bench
tools: [`cpu/j713/bench/guard_bench.py`](../cpu/j713/bench/guard_bench.py)
(busy loops pinned to chosen cores, `TCMz`, `PSTR`, clocks and the cap every
0.25 s, load killed at 105 C) and [`gate.sh`](../cpu/j713/bench/gate.sh).

### Heat steps (the guard's table)

`TCMz` rise 1.5-2 s after moving the P cluster from P4, with the idle driver
loaded. Runs marked * hit the bench's 95 C abort and are lower bounds.

| Busy P cores | Measured rises |
| --- | --- |
| 1 | P12 +23.7 C, P15 +31.2 C, P20 (ran P16/P17) +32.1 C |
| 2 | P10 +21.6 C, P12 +32.3 C*, P14 +44.9 C* |
| 3 | P8 +15.3 C, P10 +26.9 C, P12 +35.3 C* |
| 4 | P6 +8.7 C, P8 +17.7 C, P10 +30.3 C* |

### Settled heat (the base estimate)

[`cpu/j713/bench/settle.py`](../cpu/j713/bench/settle.py) loads one to four P
cores at P4 for 8 s, has
[`j713_pstate_bench`](../cpu/j713/bench/j713_pstate_bench.c) hold one state
for 15 s (95 C abort), then keeps the cores busy at P4 for 10 s;
[`settle_decode.py`](../cpu/j713/bench/settle_decode.py) reads the logs. Fast
heat is what is gone two seconds after the drop; the creep is what stays.
Two runs each (fast heat, C):

| Busy P cores | P5 | P6 | P7 | P8 | P9 | P10 | P11 | P12 | P14 | P15 | P17 | P20 (runs P16/P17) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 1.9, 2.2 | 3.6, 3.6 | 5.5, 5.5 | 7.1 | | 13.6, 14.0 | | 20.9, 20.1 | 26.9, 28.8 | 27.9, 27.4 | 28.3, 28.8 | 26.2, 28.8 |
| 2 | 3.1, 3.2 | 5.2, 5.0 | 6.6, 7.8 | 10.0, 11.1 | 14.0 | 18.9, 16.5 | 25.0* | 27.6*, 27.4* | | | | |
| 3 | 2.6, 3.1 | 5.9, 5.5 | 8.0 | 12.3, 10.8 | 16.7 | 21.4*, 21.2* | | | | | | |
| 4 | 3.1, 3.3 | 6.6, 6.1 | 8.7, 8.7 | 14.6, 11.3 | 18.8*, 18.6* | | | | | | | |

\* reached 95 C before 15 s. The old estimate subtracted 70% of the step
table: for one core at P12-P17 the fast heat is 83-90% of it, so the base read
about 4 C high and one core backed off early; for four cores at P7 it is 62%,
so the base read about 1 C low. One core held at P17 rose from about 61 C to
94-95 C in 15 s and was still creeping 0.3-0.5 C/s.

### Guarded runs

Clocks are the P cluster's after the first 5 s; loop rate is the bench's
per-core work (millions of loop iterations a second) against the earlier
70%-share build. Each run started at 53-62 C; loads ran 45 s (the join run
40 s, all ten cores 60 s, the long run 120 s).

| Load | Peak `TCMz` | P clock while loaded | Loop rate (earlier build) | Over 100 C, hot pin, SMC errors |
| --- | --- | --- | --- | --- |
| One P core | 95.9 C | 2.6-4.1 GHz, median 3.1 GHz | 49.4 (48.2) | none |
| One P core, 120 s | 95.9 C | ends at 2.9 GHz | 43.0 | none |
| Two P cores | 96.8 C | 2.4-3.3 GHz, median 3.1 GHz | 44.0 (41.8) | none |
| Four P cores | 97.6 C | 2.1-2.6 GHz, median 2.4 GHz | 36.8 (34.4) | none |
| One P core for 15 s, then three more at once | 96.1 C | 2.1-3.8 GHz, median 2.6 GHz | 26.6 (29.8) | none |
| Four P cores, 200 ms on / 200 ms off | 88.5 C | median 2.4 GHz | 23.7 (21.6) | none |
| All ten cores | 97.1 C | P 1.8-2.1 GHz, E 2808 MHz | 25.3 (26.8) | none |

The prediction holds where it is tightest: in an earlier build's four-core
run, four cores woke at once from a 60.9 C die into a cap of P11, predicted to
peak at 98.9 C, and peaked at 97.1 C.

- **The clocks are real.** A 100 ms log of requested against actual clock
  (`cpuinfo_cur_freq`, the cluster's `DVFS_STATUS`) over these runs matched in
  99.5% of 5,174 samples; the earlier build's log reached 4464 MHz.
- **Settled single-thread clock:** one core settles near 3.1 GHz. With the
  base now measured, what holds it there is heat: a P core held at 4 GHz takes
  the die past 94 C within 15 s and keeps creeping, and the guard keeps the
  predicted peak at or below 100 C (Limits).
- **Single-thread speed:** a fixed Python loop pinned to CPU 0 took 3.99 and
  4.19 s with the gate closed (1800 MHz) and 2.49 and 2.13 s with it open:
  1.7-2.0x faster.
- **Ten-minute all-core soak** (all ten cores, E to 2808): peak 95.4 C, settled
  at 91-93 C, 14-18.6 W. The guard held P at 1800 MHz for the E cluster's heat,
  and the 95 C trip held the die by stepping E between 2172 and 2808 MHz.

### Idle

On one boot, 90 s per setting, login screen:

| Setting | `PSTR` | `TCMz` |
| --- | --- | --- |
| Boost on, kernel real-time floor (1024) | 5.88-5.96 W | 62-63 C |
| Boost off | 5.25 W | 52.6 C |
| Boost on, real-time floor 412 | 5.24-5.25 W | 51.7-52.0 C |

The E states made no difference (5.25 W at E 2172 and at E 2808 with boost
off). With floor 413 the real-time clock resolves to 2088 MHz and the cluster
spends half the time there (5.31 W).

## Existing installs

**Connected to M4 updates? You get this automatically.** `omarchy-m4-update`
installs `omarchy-m4-boot` 1-11 and `omarchy-m4-cpu` 1-3; restart once. Check:

```bash
m4-cpu-idle          # "boost": "on", "gate": "open", "rt_floor": "412"
cat /proc/j713_pguard
cat /sys/devices/system/cpu/cpufreq/policy0/scaling_available_frequencies
```

**Boost off switch:** `sudo m4-cpu-idle --boost off` (takes effect at once).
**Devicetree rollback:** `sudo cp /boot/efi/omarchy/boot.bin.pre-pboost
/boot/efi/omarchy/boot.bin && sync`, then restart; a later package update
recomposes it.

## Limits

- Sustained single-thread work settles near 3.1 GHz, not 4464 MHz, and the
  limit is now heat rather than the estimate: with the measured base, one P
  core near 4 GHz reaches the 100 C prediction within seconds. More sustained
  speed would need a higher limit.
- The guard limit is 100 C predicted, with the 95 C trip for sustained load and
  103 C as the hard pin. These are the guard's defaults; it has module
  parameters within fixed bounds, but nothing sets them.
- The fast-heat table comes from one Air at 53-68 C starting temperatures, two
  runs per state.
- No energy model, so energy-aware scheduling stays off.
- Measured on AC; battery life was not measured. Suspend/resume is untested.
- One Air (owner's). Other M4 Airs are untested; the guard refuses any other
  CPU layout or table.
