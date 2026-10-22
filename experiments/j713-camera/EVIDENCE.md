# Evidence through 2026-10-20

This is a redacted account of observations. Complete captures and the
original qualified source snapshots remain in the private session archive.
All development tests and builds ran on the remote validation host under
its host-wide test lock.

## Live results

| Experiment | Observed result |
| --- | --- |
| Earlier identity/register probe | Thirteen local power requests acknowledged; version `0x100001`; 37 register observations; cleanup and raw power-state comparison passed. |
| First DART scratch attempt | Rejected before provider creation: embedded FDT pointer was not eight-byte aligned. No mappings were made. Cleanup restored the baseline. |
| Sealed-loader validation | Both diagnostic variants loaded successfully from an immutable read-only memfd. Validate-only had zero power writes and zero ISP reads, then unloaded. It did read the PMGR snapshot. |
| RVBAR observation | `result=0 cleanup=0 verify=0 fail_stage=0 attempted=13 isp_reads=1 inspect_reads=37 rvbar_reads=1 version=0x100001 raw_rvbar=0x10000000001`. CPU control `0`, status `0x0330006a`. Unloaded; all 16 raw PMGR words and boot identity restored. |
| Aligned DART R4 attempt | Header accepted, then `symbols in overlay, but not in live tree`. Overlay apply returned `-22` with an allocated ID. Explicit cleanup removed it. Final `result=-52 first=-52 cleanup_error=0 pending=0 power_restored=1 retain=0 flags=0 map=0/0 cleanup=0 verify=0`. No providers or mappings remained; all 16 raw PMGR words and boot identity restored. |
| DART R5 without symbol exports (2026-10-21, after reboot and requalification) | Fresh check-only passed; the stale R5 launcher was refused for boot mismatch. Validate-only matched the earlier run. One `--map-check`: overlay applied (`ret=0`), all three temporary ISP DARTs bound (16K pages, AS 42), five 16 KiB scratch pages mapped, looked up and unmapped, SID0 TTBR readback identical on all three DARTs with TLB flush and error register `0`, overlay removed. Final `result=0 first=0 cleanup_error=0 pending=0 stage=7 fail=0 power_restored=1 retain=0 flags=0 map=0/5 lookup=5 readback=6 cleanup=0 verify=0 attempted=13 version=0x100001`. COPROC control stayed `0`, status `0x0330006a`, at every check. No fault or error lines in the kernel log. All 16 raw PMGR words and boot identity matched the pre-run baseline; module unloaded; no owned nodes, platform devices or IOMMU group remained. |

The R5 result proves Linux can program and tear down the ISP DART page tables from a software point of view. It is not a DMA transaction and does not establish the firmware address alias.

There is no successful ISP firmware boot, DMA transfer, sensor discovery,
camera frame, or camera application result in this checkpoint.

## Failures reproduced and corrected offline

1. **Embedded alignment:** the original module's DTBO symbol was at
   `.rodata+0x3d4`, address modulo eight equal to four. The exact target
   kernel's `fdt_check_header` returned `-19` for that layout. `_Alignas(8)`
   plus a pre-power guard moved the symbol to `.rodata+0x3d8`; the exact
   parser passed. Removing alignment failed the compiler-based regression.
2. **Module byte verification:** overwriting the original inode after
   hashing changed the bytes read through the old load descriptor. Both
   launchers failed that regression. They now hash and pass a sealed copy,
   reopen it read-only, and close the writable handle before insertion.
   Both pass the original regression, seal/access-mode checks, exact child
   process byte checks, mutation refusal, and descriptor cleanup checks.
   Removing the copy or a required seal is caught by tests.
3. **Overlay symbol exports:** the actual built DTBO contained
   `/__symbols__`; the live tree had none. The exact kernel's
   `init_overlay_changeset` rejects that combination. Removing `-@` keeps
   `/plugin/` local phandle fixups at offsets `0,8,16` while omitting
   exports and external fixups. The new contract rejects the old artifact
   and a Makefile mutant that restores `-@`. Offline merge preserves every
   original node/property and adds exactly the four intended nodes.

Offline `fdtoverlay` alone had missed the kernel's additional symbol-table
requirement. It is not presented as a successful kernel overlay application.

## Observed checks

- DART R3: exact target kernel compile/MODPOST, C mock, literal layout,
  two embedding tests, and 64 launcher/real-I/O tests passed.
- Sealed-loader candidates: 69 DART and 49 RVBAR tests passed, including
  the new real-I/O cases. Independent handoff checks passed on both.
- RVBAR C mock passed. Additional actual-helper and extracted kernel-read
  tests killed two guard mutants that the original mock alone missed;
  removing the RVBAR call was caught by the original mock.
- DART R5: new DTBO contract, two embedding tests, independent payload
  oracle, exact captured-tree merge, exact kernel compile/MODPOST, actual
  ELF embedding/alignment, and exact kernel FDT parser all passed. The old
  artifact was rejected and the restored-`-@` mutant was killed.
- Foundation: layout test and exact kernel build/MODPOST passed. Its
  retained KUnit tests were not run. The foundation was never loaded.

Historical private build hashes:

| Artifact | SHA-256 |
| --- | --- |
| DART R5 module, not loaded | `742a18129c58be1d766df40b9bb6eca4d066e218ad247a7e1dfd2754e855033c` |
| DART R5 DTBO, 1019 bytes | `ad8931f338d40d07797b21e2d515b7387b2704a0519857290480823884abce2b` |
| RVBAR R2 module, observed live | `868b76176f303563a327d5c2b53026f601275ef3c60b3814113d4d5e2a7454c6` |
| Inert foundation module, never loaded | `19b462a17682e14d26c3d6654c9bc34096d7e8dc8232dc7168cd4b77f2ad812e` |

## Review status

The exact redacted publication sources were checked separately off-host:
both C mocks, DART layout/embedding/DTBO contract, foundation layout, all
118 launcher tests, and all three kernel module builds passed. The first
foundation MODPOST attempt omitted media dependency symbol tables and
failed; the focused rerun with the exact dependency exports passed. No
redacted checkpoint module was loaded on hardware.

Two independent reviews and arbitration accepted the alignment and sealed
loader changes for bounded experiments. Remaining low-impact notes include
an asynchronous return-boundary descriptor cleanup window, early error-stage
reporting, conservative node-name refusal, and uneven inherited test
coverage. These are retained as follow-up work, not silently marked fixed.

**The R5 symbol-export delta has not completed that review cycle and has not
been loaded.** The final redacted publication checkpoint is also not being
presented as merge-ready or as a fully reviewed camera driver.

## 2026-10-21 continuation

- **Requalification after reboot.** m1n1 placed the ADT at a different physical address on this boot. The private launcher's
  boot identity, ADT address, ADT digest and reserved-memory path were requalified. Only those four launcher constants changed;
  the module bytes did not. A parsed diff against the previous qualified ADT showed no property change in the ISP, ISP DART,
  ISP mappers or PMGR nodes. Only per-boot entropy, timestamps, memory-map entries and other coprocessors' segment ranges changed.
  The live FDT differed only in boot-relocation fields; `/soc` and the AIC phandle were unchanged.
- **Review.** Two blind delta reviews of R5 plus the requalified launcher (Gemini 3.8 Flash High, DeepSeek v4 pro max): both
  APPROVE, with no blocker or major finding. Both found the same test-strength gaps: the DTBO contract does not enforce an exact
  master-node property set and does not refuse an extra fragment. The arbiter checked the exact staged DTBO: one fragment, master
  properties exactly `compatible` and `iommus`, so the run was unaffected. Both gaps are now closed (see below). Jev
  arbitration: one bounded run (0.97); planted false controls rejected (0.07, 0.05, 0.34).
- **Tests.** The 69 launcher tests passed on a remote Linux host under the host lease.
- **Not performed.** Firmware start, handshake, sensor discovery, frames, foundation KUnit, and hosted CI.

## 2026-10-21 contract-test gap closure (offline only)

- **Change.** `test_dtbo_contract.py` requires the exact node-path set, the exact property names on every node, an empty
  reserve map, and no duplicate node or property in the blob. The new `test_dtbo_contract_mutants.py` compiles DTS mutants with
  the same dtc (master `status`, extra `fragment@1`, stray root child, root/fragment/overlay properties, master child,
  `/memreserve/`, an unresolved external label, and `dtc -@`). It injects extra properties into all four generated
  `__local_fixups__` nodes of the real compiled blob. It also checks hand-built duplicate blobs and every `DTC` refusal.
  `make dtbo` deletes the old DTBO and requires a non-empty new one before embedding. A `DTC` must be an executable regular
  file, resolved to an absolute path with no PATH lookup. `all` and `modules` are unchanged; no C, DTS or launcher source
  changed.
- **Before/after.** On the remote builder with the exact kernel dtc 1.7.2 under the test lock, the new suite fails 13 checks against
  the previous contract, including both reported survivors. With the change, the contract passes on a DTBO byte-identical to
  the live R5 artifact (`ad8931…`, 1019 bytes), and the suite runs 16 tests with no failure. The embed tests (2) and launcher
  tests (69) also pass.
- **Mutation check.** Removing or weakening each new guard one at a time (14 mutants) is caught each time by a targeted test.
  A no-op `DTC` and a directory `DTC` are refused.
- **Review.** Independent blind review rounds with arbitration until no finding remained open. No hardware was touched.

## 2026-10-21 firmware start design, E1 and E2

- **Design.** `FIRMWARE-START.md` records the native start, park and
  power-off order for ISP revision `0x16b`, taken from static analysis of
  the macOS camera driver. It also records the firmware's DART view and a
  five-rung ladder with halt rules. Three blind design reviews were
  arbitrated before any code.
- **E1 (read-only, `/dev/mem`).**
  - The preloaded firmware's TEXT segment is byte-identical across two boots.
  - DATA differs in exactly two 8-byte words, consistent with per-boot
    values (inferred).
- **E2 offline (`fwview/`).**
  - Exact-kernel module build passes. The mock, layout and DTBO contract
    tests pass.
  - 134 launcher tests pass. 15 guard mutants were each killed by a targeted
    test.
  - Three blind code-review rounds found no forbidden register write, no
    processor release and no free of preloaded memory.
  - Bugs fixed before hardware: the DATA restore could not have worked on
    `/dev/mem` (a file-size length check, and `msync` rejected); the
    launcher trusted the `insmod` exit code; the writable-TEXT regression was
    untested.
- **E2 live, one map-check (same boot as the R5 run).**
  - Check-only and validate passed.
  - The map-check powered the 13 local domains (version `0x100001`, COPROC
    halted) and then **failed closed at the DART baseline** (`-71`,
    stage 21), before any overlay, provider, domain or mapping.
  - Power was restored. All 16 tracked PMGR words, the boot identity, the
    absent owned nodes and the unloaded module match the pre-run baseline.
  - Cause: on dart0, stream 0 still had translation enabled with a valid page
    table pointer (TCR `0x1`, TTBR `0x1006ab89`), and stream 12 was reset to
    0. R5 earlier on that boot had programmed a different TTBR
    (`0x1005c8f5`) and never read the DART back after teardown, so the
    writer of the current value is not established.
  - DART register state persists across the local power gating. The leftover
    translation is dormant while the ISP is gated and halted.
- **Round-5 response.**
  - A read-only `--dart-survey` mode that records TCR and TTBR for streams
    0..15 and the globals on all three DARTs.
  - For the next map-check: the baseline becomes record-only, and an
    explicit post-detach SID0 quiesce (TCR 0, TTBR 0, TLB flush, readback)
    runs before the overlay is removed.
  - Offline tests pass. Blind review and hardware runs are pending.

