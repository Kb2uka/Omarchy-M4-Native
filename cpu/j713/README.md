# J713 CPU idle module (#65, #66)

`j713_cpuidle.c` lets idle M4 cores power down instead of spinning under
`idle=nop`. It is `drivers/cpuidle/cpuidle-apple.c`'s "CPU PD" state without
the `s3_5_c15_c5_0` writes that SError on M4; see
[docs/j713-cpu-idle.md](../../docs/j713-cpu-idle.md) for why and for the
measurements.

The shipped build is
`a2a7947a1d2260abcfc6eb10d7d63643ad2e191e73e93fedddf605116cdfd5a9` (12,680
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

It prints the module's `vermagic` (`7.1.9-m4-gpu-backport-20261008 SMP preempt
mod_unload aarch64`) and its SHA-256.

## Parameters

| Parameter | Default | Meaning |
| --- | --- | --- |
| `cpus` | `all` | CPUs the driver serves, as a cpulist; other CPUs keep `idle=nop` |
| `exit_latency_us` | `10` | Exit latency of the power-down state |
| `target_residency_us` | `100` | Shortest idle period the governor powers down for |

The package loads it with the defaults. `cpus=6` is how the first hardware
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
