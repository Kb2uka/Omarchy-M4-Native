# J713 NVMe power-domain attachment

On 2026-10-07, changing the J713 NVMe consumer's second power domain from
`ps_apcie_st` to `ps_apcie_sys_st` allowed native Linux to initialize the
controller and discover partitions. A follow-up RAM boot performed two
successful direct reads of the first MiB, with identical hashes, while Linux
reported the block device read-only. This is an SSD-read milestone, not a
working Omarchy installation, filesystem-write test, or stability qualification.

## What changed

Patch 0016 adds one property to the existing J713 `&nvme` override:

```dts
power-domains = <&ps_ans>, <&ps_apcie_sys_st>;
```

The storage system domain at PMGR offset `0x548` depends on ANS (`0x538`) and
APCIE_ST (`0x540`). The old attachment held references to ANS and APCIE_ST;
the new attachment includes the storage system domain and its existing parent
chain. The driver's `dev_pm_domain_attach_by_id()` and `device_link_add()`
with `DL_FLAG_PM_RUNTIME | DL_FLAG_RPM_ACTIVE` consume these references.

The correction is board-local. Five other T8132 board DTBs compiled byte for
byte unchanged. No GPIO, mailbox interrupt, USB stream, memory mapping, driver,
filesystem or installer behavior is changed by this patch.

## Controlled hardware evidence

The target was the 13-inch 2025 M4 MacBook Air, Mac16,12 / J713 / T8132.
The coordinator was a Linux iMac; Mac mini compiled DTs and OptiPlex built
the experimental kernel. Both compared boots used the same kernel, RAM
initramfs, boot arguments, and native USB EEM diagnostic service. The DTs
were identical except for the second NVMe power-domain phandle.

| Boot | Old attachment, 04:36:11 UTC | Corrected attachment, 04:42:34 UTC |
|---|---|---|
| RTKit wake | firmware crash, FAR `0x49802c000`; fails `-62` | returns successfully |
| Controller initialization | not reached | returns successfully |
| Partitions | none | `nvme0n1: p1 p2 p3` |
| Watchdog arm to proxy return | 126.336 s | 126.382 s |
| Fresh proxy / watchdog control | passed / 0 | passed / 0 |

The corrected run reported:

```text
[    1.275014] nvme-apple 4c5cc0000.nvme: M4TRACE RTKit wake returned
[    1.275027] nvme-apple 4c5cc0000.nvme: M4TRACE ANS boot status accepted
[    1.303656] nvme-apple 4c5cc0000.nvme: M4TRACE finish controller init returned
[    1.417620]  nvme0n1: p1 p2 p3
```

A later reversal check used the same modular kernel, early-load RAM image
and unpaced arguments: restoring r11 at **05:35:50 UTC** again produced an
RTKit wake SError and no partitions; returning to r13 at **05:38:23 UTC**
restored successful wake, controller initialization and partition discovery.
This repeated old/new result reduces the possibility that the first success
was solely state carried across watchdog resets. The reverted run's report
included PC `0x636004` and FAR `0x49802c000`; those values do not identify a
precise faulting transaction. The successful reapplication logged firmware
rebuild activity; no corresponding rebuild syslog line was captured in the
failed reversion, which crashed during wake.

A second corrected boot at 04:45:39 UTC changed only the RAM initramfs to add
an automatic, bounded read probe. It set `/dev/nvme0n1` read-only and ran
the following two commands, once for each RAM output file:

```sh
dd if=/dev/nvme0n1 of=/tmp/readprobe-a bs=4096 count=256 iflag=direct
dd if=/dev/nvme0n1 of=/tmp/readprobe-b bs=4096 count=256 iflag=direct
```

`cmp` succeeded and the RAM files were deleted.
No storage device was used as an output, and no filesystem was mounted.

```text
read_only=1
size_bytes=500277792768
sector_size=4096
256+0 records in
256+0 records out
1048576 bytes (1.0MB) copied, 0.004442 seconds, 225.1MB/s
256+0 records in
256+0 records out
1048576 bytes (1.0MB) copied, 0.004190 seconds, 238.7MB/s
a077dfdab79b61fa37f502cc587e5c1e44c0e5a85b6fcc3cffa3664581b32a79  /tmp/readprobe-a
a077dfdab79b61fa37f502cc587e5c1e44c0e5a85b6fcc3cffa3664581b32a79  /tmp/readprobe-b
READPROBE_PASS direct 1048576 bytes twice; hashes equal
```

These short-read timings are raw command output, not a performance benchmark.
The read-probe boot returned to a verified proxy with watchdog control zero
in 126.408 seconds. Linux read-only mode does not prohibit the SSD firmware's
own internal maintenance; the logs included firmware parity-rebuild messages.

## Artifact hashes and limits

| Local bench artifact | SHA-256 |
|---|---|
| Kernel `eem-slowtrace` | `56a911a8316303b5e7463c12c853d12105ef9ec2f0224078cbf40add99648e85` |
| RAM initramfs, original early module load | `6f4455da4110e7301192d31276d14971fdd80bbef2426a3c81f2755cb5c01499` |
| RAM initramfs, direct read probe | `2427cc80de11847be5fd58ffe05ddf7c7aa7e24721b9fac096d0525b11a31870` |
| Original bench DT, r11 | `63e72ae46bdcebd9f14238f69e963a75a0834797541269024d0f3b5a082c621d` |
| Corrected bench DT, r13 | `513c1feb72a0913fb5d0d46e9103b56b9c89869e3dd14b84cdc238b80a0f2d73` |

The kernel includes private diagnostic changes on Asahi revision
`77cb8f24c2381a8abb7272d7bbdec548d6426a8a`. Diagnostic pacing was disabled.
The boot used `nosmp idle=nop`, a 120-second one-shot watchdog and a RAM-only
root. The storage driver was a matching module loaded one second after init.
The bench also required the separately tested ANS mailbox corrections, shared
controller/NVMMU mapping and experimental USB transport. Binaries and raw
captures are preserved locally and are not distributed in this repository.
A fresh clone with patch 0016 alone cannot reproduce the complete bench.

Setting `pd_ignore_unused` or making only the ANS domain always-on did not
fix the preceding wake failure. The controlled attachment change is evidence
for the missing storage-system reference, but does not identify the precise
firmware instruction or physical access that produced the SError. The older
built-in-driver path was retested at 05:07:56 UTC using kernel SHA-256
`363e814b57bf122c2a33dfabace48ec42efe66736806f853c19addb8da5b34bb`,
r13 and the read-probe initramfs. Controller initialization and partition
scan completed; both direct first-MiB reads matched. Its proxy returned with
watchdog control zero after 126.539 seconds. The initramfs's redundant module
load failed because that older built-in kernel lacked the diagnostic export;
the built-in driver had already initialized storage successfully.

## Validation

`tests/check_j713_nvme_power.py` resolves compiled phandles to provider paths,
checks both old and corrected ordered attachments and their parent chain,
and rejects every unrelated property or node-inventory change. The expected providers use paths, not hard-coded numeric phandle IDs;
the final unchanged-property comparison still requires unchanged provider IDs.
The guard runner exercises malformed provider references, reversed/extra
domains, wrong board, incompatible providers, altered ancestry and unrelated
changes. Run both against source-built original and candidate J713 DTBs:

```sh
python3 tests/check_j713_nvme_power_guards.py before-j713.dtb after-j713.dtb
python3 -O tests/check_j713_nvme_power_guards.py before-j713.dtb after-j713.dtb
```

To reproduce the compiled fixtures, start from the preserved historical DT
source at `c70dc9cfc`, after patches 0011–0013. That source snapshot is local
bench material, not guaranteed reachable from this repository's remote.
From its root, compile the original, apply this repository's patch 0016, then
repeat the same compile command with `after-j713.dtb` as the output:

```sh
clang -E -nostdinc -I include -I arch/arm64/boot/dts/apple -undef -D__DTS__ \
  -x assembler-with-cpp arch/arm64/boot/dts/apple/t8132-j713.dts | \
  dtc -O dtb -o before-j713.dtb -I dts -
git apply /path/to/this/repository/patches/0016-fix-dts-j713-attach-nvme-storage-system-domain.patch
```

The source build used those steps. Its full-board original/candidate hashes are
`95a6df2199077ff2340fa15916ce354682576fd8d728140376d69ea4b45ca03e` and
`a89a464319feb1d40bf132bd0d6bd81f47f626a2f38b0ff13f52529904a682bd`.
Those full-board DTBs were compiled and compared, **not booted**. The hardware
runs used the bounded, reduced-device bench DTs r11/r13 listed above.
