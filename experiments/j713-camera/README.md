# DO NOT MERGE — J713 camera investigation checkpoint

Paused at the maintainer's request on 2026-10-20. **The internal camera does
not work yet.** No ISP firmware has been started and no camera frames have
been captured. This draft preserves the investigation for another session.

Target examined: Mac16,12 / J713, T8132 M4 Air, Omarchy 4.0.4,
`7.1.9-m4-gpu-backport-20261008`, 16 KiB pages. `/dev/video0` is the AVD
decoder, not evidence of a working camera.

| Directory | Checkpoint |
| --- | --- |
| `dartmap/` | Latest scratch mapping diagnostic, including the aligned embedded device tree, immutable module loader, and removal of exported overlay symbols. Latest build and offline regression checks pass; this last overlay fix has **not** had independent review or a live mapping attempt. |
| `rvbar/` | Register observation diagnostic. Its private qualified build completed one live RVBAR read and restored the recorded power state. |
| `foundation/` | Unfinished driver foundation derived from the Aurora ISP driver. Builds, but its T8132 dispatch deliberately returns `-EOPNOTSUPP` after guards. Never loaded. |

See [HANDOFF.md](HANDOFF.md) for the next step and unresolved work, and
[EVIDENCE.md](EVIDENCE.md) for the distinction between build, test, and live
results. The modules are development experiments. They are not included in
the update channel, installed at boot, or connected to an installer.

## Public checkpoint boundaries

Boot identity and the personalized ADT digest are replaced with
`REQUALIFY_BOOT_UUID` and `REQUALIFY_ADT_SHA256`. The launchers therefore
refuse a real boot by default. The RVBAR module's descriptive boot parameter
is also redacted; it is not a substitute for the launcher guard. Other
captured layout assumptions must be requalified before any later test.

The original qualified sources and complete evidence remain in the owner's
private `air-camera-20261020` session archive. This tree contains source and
redacted findings, not firmware, ADT/FDT captures, kernelcache, disassembly,
module binaries, credentials, or raw session logs. Private build hashes in
the evidence identify historical artifacts; they do not identify binaries
rebuilt from these redacted sources.

Do not load these modules directly or fill in the placeholders merely to
get past a refusal. A later session must re-establish the target state and
complete the pending review before performing another bounded experiment.

## Offline checks

Run on the designated remote Linux validation host under its test lock.
Use the exact target kernel tree and its device-tree compiler. No hardware
load is part of these commands.

```sh
make -C dartmap mock layout-test embed-test
./dartmap/j713_isp_probe_mock
make -C dartmap dtbo-contract-test DTC="$KDIR/scripts/dtc/dtc"
(cd dartmap && python3 -m unittest test_run_probe.py test_real_launcher_io.py)
make -C rvbar mock
./rvbar/j713_isp_probe_mock
(cd rvbar && python3 -m unittest test_run_probe.py test_real_launcher_io.py)
make -C foundation layout-test
```

`make dtbo` replaces the intentional `.inc` placeholder with generated
source. Do not commit that generated file or the build outputs.

External foundation builds also need the exact kernel's media dependency
exports. The target source archive's top-level `Module.symvers` alone is
insufficient. Supply the already-built `drivers/media/mc/Module.symvers`,
`drivers/media/v4l2-core/Module.symvers`, and
`drivers/media/common/videobuf2/Module.symvers` through
`KBUILD_EXTRA_SYMBOLS`; otherwise MODPOST reports unresolved media symbols.

## Provenance

The foundation starts from `omacom/linux-aurora` commit
`34c7dfe3d272a12de30586292a033d8f15b23070`,
`drivers/media/platform/apple/isp`, with experimental T8132 layout and
refusal-path changes. Upstream copyright and license notices are preserved;
see [COPYING](COPYING). The diagnostic sources identify their licenses with
SPDX headers. The foundation's existing KUnit test files are preserved but
were not executed in this investigation.

Earlier-chip work is relevant, particularly the
[M3 J613 camera work](https://github.com/omacom/linux-aurora/pull/119).
It is a reference for firmware and streaming integration, not proof of
M4 support. No working native M4 camera implementation was established in
the repositories examined.

Checkpoint maintained by KB2UKA.
