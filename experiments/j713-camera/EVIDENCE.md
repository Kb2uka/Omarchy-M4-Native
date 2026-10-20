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
| DART R5 without symbol exports | **NOT RUN on hardware.** |

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
