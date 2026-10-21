# Resume the camera investigation

**DO NOT MERGE.** Work is paused. The camera is not functional. On
2026-10-21 the R5 scratch DART mapping experiment ran once and met every
acceptance criterion below (see EVIDENCE.md). The next work is firmware
mapping and start design, which has not begun.

## State at pause

- Both diagnostic modules were absent after the last live experiment.
- All four temporary overlay nodes were absent.
- All 16 tracked raw PMGR values and the boot identity matched their
  recorded baseline after each experiment.
- No firmware start, sensor programming, camera streaming, reboot,
  shutdown, or suspend was performed.
- The final source in `dartmap/` is the private R5 candidate, with identity
  fields redacted for this checkpoint. `rvbar/` is the private R2 candidate.
- The R5 module was built on the remote validation host and was **not
  loaded on the Air**. No further hardware attempt is scheduled.

## Immediate next step

1. The R5 review, requalification and one bounded mapping run are done
   (2026-10-21). Do not repeat the mapping run without a reason.
2. Close the two open contract-test gaps: exact master-node property set
   and refusal of any extra fragment or root child.
3. Requalify every private launcher constant after any boot or kernel
   change. The ADT physical address moves between boots.
4. Design, review and arbitrate a firmware mapping and start contract
   before any processor start (see "Work still required").

The acceptance criteria the 2026-10-21 run met are listed below.
For a clean mapping result expect `result`, `first_error`, `cleanup_error`,
`cleanup`, `verify`, and `fail_stage` all zero; `attempted=13`,
`version=0x100001`, `map_total=5`, `lookup_count=5`, `readback_count=6`,
`map_count=0`, `flags=0`, `retain=0`, `cleanup_pending=0`, and
`power_restored=1`. The 2026-10-21 run observed exactly these values. Validate-only has no power writes and normally reports
`power_restored=0`.

## What the experiments establish

The RVBAR observation is `0x10000000001` at physical register
`0x48d050000`. COPROC control was zero and status was `0x0330006a`.
Bit 0 of RVBAR was set; the address field was `0x10000000000`.
This is a retained register observation, not proof that firmware can fetch
instructions through a newly configured mapping. Do not replace it with a
guessed CPU physical firmware address.

The scratch diagnostic allocates five fresh zeroed 16 KiB pages at IOVAs
`0x13ec000..0x1400000`. It uses three temporary ISP DART providers and SID0.
Its explicit Linux DMA window is `[0, 2^36)`, selecting three page-table
levels on this kernel; hardware IAS/OAS read as 42 bits. Software address
lookup and register readback do not prove a DMA transaction or the
firmware's high address alias.

The RVBAR probe records COPROC state but does not add a CPU-halt predicate
or revalidate every PMGR word immediately before its final read. The DART
experiment has its own halt guards. Do not conflate those contracts.

## Work still required after scratch mapping

- Establish the M4 firmware mapping and address-tag contract using the
  observed reset vector and earlier-chip references; keep the foundation's
  unknown-address-mask refusal until justified.
- Establish a bounded firmware start, handshake, and reliable shutdown
  sequence before starting the processor. A control-register write alone
  is not established as a complete firmware shutdown.
- Resolve sensor identity, matching setfile, and streaming ABI. The ADT
  selector used for the SoC layout is not a sensor identifier.
- Integrate the real power, IRQ, mailbox, DMA lifetime, and firmware paths.
  The foundation's broad register claims and generic power handling still
  require reconciliation with the measured layout.
- Capture real camera frames and validate normal camera applications,
  cleanup, and repeated use before proposing package-channel integration.

Do not install the experiments or relax the T8132 refusal merely because
the foundation compiles. The checkpoint is intentionally unfinished.
