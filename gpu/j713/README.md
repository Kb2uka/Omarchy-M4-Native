# Air GPU module: per-chip power limit (test build, issue #57)

**Test build only.** This is not part of the M4 update channel and no update
installs it. Use it only when asked to test it on an issue.

## What it changes

In Air mode (`air_bootstrap_test=1`), the alpha.1 GPU module accepts only a
GPU power limit of exactly 24800 mW. The loader (m1n1) derives that limit per
chip, the same way macOS does: the top GPU voltage state times a 24800 mA
clamp. Only a 1000 mV top state gives exactly 24800 mW. An Air with a 980 mV
top state gets 24304 mW, and its probe fails with `-22` and no log line.

`asahi/g16.rs` accepts each Air's own derived limit up to 24800 mW and passes
it to the GPU firmware as its power ceiling, as before. Every other check is
unchanged: 10-core mask `0x3ff`, ten performance groups, 1470 MHz, board
`apple,j713` + `apple,t8132` and firmware `mBoot-18000.120.36`. A 24800 mW Air
takes exactly the alpha.1 path, apart from one extra log line. Every rejection
now logs its reason and the measured values:

```text
G16: Air platform rejected (maximum power): mask=0x3ff groups=10 max-frequency=1470 MHz max-power=24900 mW
G16: Air bootstrap refused: firmware-build mBoot-... or board not qualified
G16: Air platform accepted; max-power=24304 mW
```

`asahi/g16.rs` began as an exact copy of the file in the v0.1.0-alpha.1 Air GPU
source packet (`omarchy-j713-v0.1.0-alpha.1-gpu-air-source.tar.gz`); the git
history shows the change on its own.

## Build

`build-test-module.sh` runs in `m4-gravity-builder:20261008` with the job
directory at `/job`, the layout of the shipped build. It takes the alpha.1
`kernel-controller.tar.gz` (SHA-256 `810339d8…`), the extracted source packet,
this `g16.rs` and an output directory. It rebuilds alpha.1 first and stops
unless that gives the shipped `80cf3669…`, then builds the test module from the
same tree with only `g16.rs` replaced.

## Install and roll back

`m4-gpu-test-module` swaps `/opt/m4-gpu/asahi_m4.ko` and its line in
`/opt/m4-gpu/runtime.sha256`, keeping both alpha.1 files in
`/opt/m4-gpu/alpha1-backup/`:

```sh
sudo bash m4-gpu-test-module install asahi_m4.ko SHA256
sudo reboot
sudo bash m4-gpu-test-module rollback   # restores alpha.1; reboot again
```

It refuses a download with the wrong hash or kernel, an installed module or
hash file that is not alpha.1, and a second install over a test module.
