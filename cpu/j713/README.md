# J713 CPU idle module and P-cluster guard (#65, #66)

`j713_cpuidle.c` lets idle M4 cores power down instead of spinning under
`idle=nop`. It is `drivers/cpuidle/cpuidle-apple.c`'s "CPU PD" state without
the `s3_5_c15_c5_0` writes that SError on M4; see
[docs/j713-cpu-idle.md](../../docs/j713-cpu-idle.md) for why and for the
measurements.

`j713_pguard.c` caps the P cluster from a prediction of the CPU die's peak and
opens the boost devicetree's gate; its policy math is `j713_pguard_policy.h`,
which `tests/test_j713_pguard.py` compiles on the host. See
[docs/j713-cpu-boost.md](../../docs/j713-cpu-boost.md).

The shipped builds are `j713_cpuidle.ko`
`a2a7947a1d2260abcfc6eb10d7d63643ad2e191e73e93fedddf605116cdfd5a9` (12,680
bytes) and `j713_pguard.ko`
`91fe247e7e83d30f62f35bcf64ddbcb42faa3a1c109881d5aefcb64dc7e5e691` (39,696
bytes), pinned in `packaging/omarchy-m4/channel.json` and staged by the
`omarchy-m4-cpu` package.

## Build

`build-module.sh` runs in `m4-gravity-builder:20261008` (clang 21.1.8) with the
job directory at `/job`, the layout of `gpu/j713/build-test-module.sh`. It takes
the alpha.1 `kernel-controller.tar.gz` (SHA-256 `810339d8…`), this directory
and an output directory:

```sh
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD/job:/job" -v "$PWD:/w" \
  m4-gravity-builder:20261008 bash /w/cpu/j713/build-module.sh \
  /w/kernel-controller.tar.gz /w/cpu/j713 /w/out
```

It prints both modules' `vermagic` (`7.1.9-m4-gpu-backport-20261008 SMP
preempt mod_unload aarch64`) and their SHA-256.

## Parameters

| Parameter | Default | Meaning |
| --- | --- | --- |
| `cpus` | `all` | CPUs the driver serves, as a cpulist; other CPUs keep `idle=nop` |
| `exit_latency_us` | `10` | Exit latency of the power-down state |
| `target_residency_us` | `100` | Shortest idle period the governor powers down for |

`j713_pguard` takes `limit_c` (100, predicted peak allowed, 60-105),
`hot_c` (103, reading that pins P4, at most 110), `release_c` (95, must be
below `hot_c`) and `period_ms` (250, 50-1000); it refuses values outside those
bounds.

The package loads both with the defaults. `cpus=6` is how the first hardware
test confined it to one E core.

## Bench tools

`bench/` holds what produced the numbers in the doc. None of it is packaged.

- `j713_smc_read.c` (with `Kbuild`): a read-only module; `/proc/j713_smc_read`
  reads the SMC keys named in its `keys` parameter (default `TCMz` and the power
  keys `PSTR`, `PDTR`, `PPBR`, `PHPC`, `PHPS`, `PHPM`, `PHPB`). Build it like
  the idle module.
- `sample.py SECONDS LABEL`: idle sampling of `TCMz`, `PSTR`, clocks and busy %.
- `verify.py SECONDS`: the self-checking workload that idles thousands of times.
- `soak.py SECONDS CPUS ABORT_C OUT.csv`: all-core soak; stops the load at
  `ABORT_C`.
- `estates.py` (root): pins the E cluster to each offered state and checks the
  clock and throughput.
- `zone_test.py TRIP_C SECONDS OUT.csv` (root): lowers the thermal zone's trip,
  loads all cores, and always restores the 95 C trip.
- `errdiff.sh BASE NEW`: kernel warning and error kinds that appear only in
  boot NEW, compared with boot BASE (journalctl offsets), numbers masked.
- `guard_bench.py LABEL CPUS SECONDS [KILL_C [JOIN_S [DUTY]]]` (root): busy
  loops pinned to CPUS with `j713_pguard` in charge; samples `TCMz`, `PSTR`,
  clocks and the guard's cap every 0.25 s and kills the load at `KILL_C`.
  `JOIN_S` starts all but the first CPU late; `DUTY` (`ON_MS/OFF_MS`) makes it
  bursty ([J713 P-core boost](../../docs/j713-cpu-boost.md)).
- `gate.sh open|close` (root): raises or restores the boost-gate trip by hand,
  for comparing a run against the 1800 MHz cap.
