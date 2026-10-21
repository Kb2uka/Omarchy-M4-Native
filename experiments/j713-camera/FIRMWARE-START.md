# J713 ISP firmware mapping and start contract

**Status: design, revision 4, not executed.** Nothing in this file has
started the ISP processor. It defines what Linux must do before, during and
after a first bounded firmware start on the M4 Air (J713, T8132, ISP revision
`0x16b`), and the experiment ladder that gets there one proven step at a time.
Each rung has its own build, offline tests, blind review and arbitration
before it touches hardware.

## Evidence base and confidence

- **Native driver.** Static disassembly of the macOS H16 camera driver
  (`ISP_StartFirmware`, `mapFwCTRRRegion`, `ISP_Init`,
  `PerformPowerGatingDisabledReset`, `ISP_Suspend`, `ISP_ForceShutdown`,
  `power_on_hardware`, `power_off_hardware`, `DisableISPClocksAndPower`). It
  comes from a macOS kernelcache taken from another Mac on the bench, not this
  Air's macOS install. The driver selects revision `0x16b` from the ADT
  sensor-type cell. A version match between that kernelcache and this Air's
  macOS is **not verified**.
- **ADT and live reads on this Air.** The ADT gives `pre-loaded = 1`,
  `no-firmware-service`, firmware `segment-ranges`, DART `vm-base` and
  `vm-size`, and `dapf-instance-0`. The processor was halted for every live
  read-only observation:
  - RVBAR read `0x10000000001`.
  - Every DART TTBR read invalid, and no DART was locked.
  - All three DART instances report a 42-bit input and output size.
- **Exact Air kernel source.** The T8110 DART driver resets every stream to
  `TCR = 0` on probe. Bypass needs explicit TCR bits (`BYPASS_DART` bit 1,
  `BYPASS_DAPF` bit 2). The fault interrupt logs at a limited rate and clears
  the error, with no panic path.
- **Labels.** **Proven** means read directly from code or hardware.
  **Inferred** means reasoned, with the reason stated. Anything else is
  **unknown**. Nothing is copied from another chip without saying so.
- **Linux safety deviations.** Some choices are deliberately stricter than the
  native driver. They are marked "Linux safety choice".

## 1. Firmware image and the reference DATA snapshot

| Segment | Physical | ISP address | Size | Access |
| --- | --- | --- | --- | --- |
| TEXT | `0x10000d20000` | `0x10000000000` | `0xca4000` | read-only |
| DATA | `0x10001f30000` | `0x10000ca4000` | `0x748000` | read-write |
| Heap | allocated by Linux | `0x100013ec000` | `0x14000` | read-write |
| IPC surface | allocated by Linux | `0x10001400000` (Linux choice inside the native range) | `0x1c000` | read-write |

- **Placement (proven, ADT and FDT).** iBoot preloads both segments once per
  boot, below Linux `memory`. Linux never allocates them.
- **Window base (proven, `mapFwCTRRRegion`).** `0x10000000000` is the DART
  window base (`vm-base`). The ADT `segment-ranges` remap fields and the live
  RVBAR both use it. These are the fixed firmware ranges. Before release, the
  native driver also maps one zeroed `0x1c000` "IPC" surface at an
  allocator-chosen address inside `[0x10001400000, 0x10010000000)` (proven,
  `start`). No mapping is untagged.
- **Heap (proven).** `0x1400000` is the end of the fixed firmware window
  (TEXT, DATA, heap), not a heap base. The 80 KiB heap at window offset
  `0x13ec000` is page-granular memory the driver provides.
- **Heap location never sent (inferred).** The firmware is never told where
  the heap is.
- **Heap and IPC contents (Linux safety choice).** Linux zeroes both
  surfaces and cleans them to the point of coherency before mapping. The IPC
  surface is zeroed natively (proven); heap zero-fill is a Linux choice.
  After every rung, Linux verifies the surfaces before freeing them:
  - **The processor was never released:** they must still be all zero. A
    nonzero byte stops the ladder.
  - **The processor was released:** their contents are recorded and the pages
    are freed.

  Every start uses freshly zeroed surfaces.
- **Cache attributes.** Whether the ISP DART is cache-coherent on this Air is
  unknown. E2 records the attributes it maps with and reads them back from
  the page tables.
- **Host access to TEXT and DATA is uncached (proven, exact kernel).**
  `phys_mem_access_prot` in arm64 `mm/mmu.c` returns `pgprot_noncached` for
  any page outside the kernel's memory map, and both segments are outside it.
  The `/dev/mem` snapshot, hash and restore therefore cannot leave stale CPU
  cache lines over memory the ISP writes.
- **2026-10-21 integrity check (read-only, `/dev/mem`).** TEXT on the current
  boot is byte-identical to the previous boot's capture. DATA differs in
  exactly two 8-byte words. That they are per-boot values written by iBoot is
  inferred, not proven. No experiment had written DATA on this boot.
- **Reference snapshot rule.** Before the first rung on a boot that maps
  DATA read-write, E2 included, the launcher saves one reference copy of DATA
  and its hash. That reference is never replaced on the same boot.
  - **Rungs that cannot run firmware (E2).** After detach, verify DATA still
    hashes to the reference. A mismatch means something wrote DATA while the
    processor was halted. Restore the reference, stop the ladder, and report.
  - **Rungs that can run firmware (E3 and later).** After the DART is
    detached and power is verified off, restore the reference and verify its
    hash.
  - **A second start on the same boot**, if any, uses the same reference.

## 2. DART contract

- **Streams.**
  - The native driver activates one mapper, stream ID 0 (proven). Stream ID
    12 is never mapped (proven).
  - Programming stream 0 identically on all three instances (`LLT`, `BLK`,
    `RT`) is inferred and is a Linux choice.
- **DMA window.** Linux sets it from the ADT: `apple,dma-range` base
  `0x10000000000`, size `0xe0000000`.
  - On the exact Air kernel this selects four-level tables and keeps address
    bit 40 (proven by kernel arithmetic). E2 must read back TCR `0x9`
    (translate plus four-level).
  - The R5 scratch window `[0, 2^36)` used three levels and cannot represent
    the firmware addresses.
- **RVBAR.** Never written. The native driver only reads it and checks mask
  `0x3fffffff800`, and a mismatch there is only logged (proven). Linux aborts
  on a mismatch (Linux safety choice).
- **Stream 15 and DAPF (gate before E3).**
  - The ADT marks stream 15 `bypass-15`, and gives 13 DAPF ranges for
    instance `0x48e8d0000`.
  - The macOS DART driver programs that DAPF at its own start. Whether the
    ISP firmware's register accesses travel on stream 15 is inferred only.
  - Linux leaves stream 15 at `TCR = 0`, which per the kernel source enables
    neither translation nor bypass.
  - Linux never sets `BYPASS_DAPF` on stream 15, because that would bypass the
    filter.
  - E2b (section 5) reads stream 15's TCR, the DAPF registers and the PROTECT
    word read-only before any start.
  - E3 runs with stream 15 left disabled. Traffic the firmware sends on
    stream 15 then faults (inferred from the TCR bit semantics) instead of
    reaching other hardware.
  - E3 counts DART fault interrupts and halts on any (section 3, step 7).
  - Seeing no stream 15 fault before stage 1 does not show that DAPF is
    unnecessary at later stages. Each later rung (E4) has its own stream 15
    and DAPF readiness gate. Programming DAPF from the ADT is a separate rung,
    entered on positive evidence of need.

## 3. Start sequence (native order, revision `0x16b`)

All offsets are relative to the ISP base `0x48c000000` unless named as PMGR.
GPIOn is `0x29241d0 + 4*n`. Every poll has the stated bound. A bound that
expires takes the abort path (section 4), never a retry.

1. **Power on.** The native driver powers three provider domains (ISP-SYS,
   ISPSENS0, ISPSENS1) through its provider. It powers the 13 local registers
   directly, with the sensor domains placed between `0x4018` and `0x4020`
   (proven).

   On this Air, Linux's PMGR driver owns the three provider domains. A live
   read before any run found ISP_SYS (`PMGR+0x530`) at `0x23ff` and both
   sensor domains (`+0x188`, `+0x190`) at `0x040002ff`, all active. They are
   among the 16 tracked words.

   **Linux safety choice:** the experiment never writes the provider domains.
   It requires all three active before power-on, and verifies them unchanged
   after every run. The native sensor-domain step is therefore a no-op here
   (inferred). Linux performs:
   1. Precondition: ISP_SYS, ISPSENS0 and ISPSENS1 read active. Otherwise abort
      with nothing written.
   2. Local registers `PMGR 0x380704000 + 0x4000` to `+0x4060`, ascending:
      write `0xf`, then poll `(v & 0xff) == 0xff`, each bounded at 50 ms
      (proven native value and poll). The R5 probe wrote `0x3ff`. Bits 8 and
      9 are clear-on-write status flags in the Linux PMGR driver (inferred
      harmless). The contract uses `0xf`.
   3. Re-check ISP_SYS, `(v & 0xff) == 0xff`, bounded at 5 ms.
   4. Write `0x28a4080 = 1` (proven native write; meaning unknown; nothing
      clears it at native power-off).
2. **Reset, only if GPIO1 reads `0xfeedbabe`** (proven). That value means the
   previous stop did not cut power. The reset:
   1. writes EDPRCR `0x1010310 = 2` and waits for bit 1 to clear (100 ms);
   2. waits 50 ms;
   3. writes `0xffffffff` to fabric `0x748`, `0x848`, `0x948`, `0xa50` and to
      the IRQ masks `0x1600a00..0x1600a0c`;
   4. reads `0x1600818` and `0x160081c` eight times;
   5. waits 50 ms.

   A fresh boot is expected to read GPIO1 = 0 and skip it (inferred; E2 records the value).
3. **Map** TEXT, DATA and the zeroed heap on stream 0 (section 2), and verify
   every page by lookup, plus the TCR and TTBR readback.
4. **Boot mode.** Write 0 to GPIO0..5 and GPIO7, then GPIO6 = 1 ("legacy
   mode").
5. **Premise checks before release (Linux safety choice).** Re-read RVBAR and
   expect `0x10000000001`. Expect GPIO1 = 0 (just written), the DART error
   registers clear, and the reference DATA snapshot taken. Any failure
   aborts.
6. **Release.** COPROC control `0x1600044 = 0x10`. The native driver does not
   poll status (proven).
7. **Stage 1.** Poll GPIO7 for `0x8042006` every 10 ms, bounded at 10 s.
   - **DART fault monitor.** The kernel DART driver requests IRQ 387 as
     shared, logs each fault and clears the error register. Polling that
     register would race the clear.
   - Before release, the module adds its own shared handler on IRQ 387. The
     handler only counts invocations and returns `IRQ_NONE`, so the kernel
     driver still handles and logs each fault.
   - Precondition: IRQ 387 belongs only to `dart-isp` in the ADT, and the run
     checks that no other device holds the line. Every invocation is then an
     ISP DART event.
   - Any increase of that count during the wait aborts at once through the
     post-release path (section 4.3). The bound also takes that path.
   - The launcher saves the kernel log lines from the ISP DART devices
     (`translation fault ... stream ...`) for the report.
8. **Read the firmware's requests:**
   - GPIO0: channel count, at most 32.
   - GPIO1 + 1: IPC queue size.
   - GPIO2 bit 1: boot-argument layout, `0x290` if set, `0x190` if clear.
   - GPIO3: heap size.
   - GPIO4: must be 0.
   - GPIO5: log format.

   An unreadable register, GPIO0 > 32, or GPIO4 ≠ 0 aborts through
   section 4.3 after the values are recorded. **E3 stops here.**
9. **Stage 2 (E4).** In a zeroed `0x1c000` IPC buffer, the queue sits at
   offset 0 and the boot-argument block at `roundup(GPIO1+1, 0x40)`
   (inferred from the native allocator). The fields below are proven unless
   marked:

   | Offset | Value |
   | --- | --- |
   | `+0x00` | Store traced to the TEXT physical base `0x10000d20000` by one analysis, meaning unknown in another. **Must be reconciled before E4** |
   | `+0x08` | Queue address |
   | `+0x10` | `0x1400000` |
   | `+0x18` | `0x0ec00000` |
   | `+0x20` / `+0x28` | Extra heap address and size: `max(GPIO3, 0x4000)` bytes, none if GPIO3 is 0 |
   | `+0x30` | Sensor type `0x17` |
   | `+0x38` / `+0x40` / `+0x48` | Log buffer. 0 selects interrupt logging |
   | `+0x50` | Queue size |
   | `+0x68` | `0x40` |
   | `+0x70` | 0x200-byte descriptor tail, zeroed. `+0xb0 = 1` when the optional driver pointer is null, and no NVM override slots (both inferred for Linux, depending on native object state) |

   In the `0x190` layout the tail is 0x100 bytes at `+0x6c`. Then a full
   `dsb(st)`, matching the native barrier, not the weaker `dma_wmb()`. Write the block address to GPIO0 (low 32 bits) and
   GPIO1 (high 32 bits), then GPIO7 = `0xf7fbdff9`. Wait for GPIO7 =
   `0x8042006`, bounded at 10 s.
10. **Channel table.** Read it at GPIO0 | GPIO1<<32. The address is unmasked
    and must fall inside a mapped range, otherwise abort. Seven channels are
    required.
11. **Stage 3.** GPIO3 = `0x8042006`. Wait for GPIO3 = 0, bounded at 10 s.
12. **Interrupts.** `0x2924008 = 0xf`, `0x2924018 = 0x70000000`,
    `0x2924038 = 0x02c00000`, `0x292495c = 8`.

The secure-camera (exclave) path is not taken on this Air. It is proven
that `ISP_StartFirmware` has no exclave step. That this Air takes the
non-exclave path is inferred from the ADT. It needs `exclave-assigned` or an `isp-exclave-proxy` node; this ADT
has neither, and `/product` reports `has-exclaves = 0`.

## 4. Stop contract

The native driver never relies on a COPROC control write of 0 to stop the
processor, and whether such a write alone halts it is unknown. The contract
does not use it.

### 4.1 Firmware park (E4 and later, only after stage 3)

Native `ISP_Suspend`, proven order and conditions. **Gate before E4:**
resolve every unknown named here, or document why it is skipped.
1. Command `0x1d`, sent only if the native state at `this+0x15d` is not 4. It
   returns a suspend-flag address and value, which are polled on success
   (step 7).
2. Optional command `0x13`, only when the native work list is not empty.
   Name unknown.
3. Per channel: stop (`0x101`) and buffer return (`0x104`).
4. `CISP_CMD_STOP` (opcode 1), only on the path where the native flag
   `this+0x14a` is clear.
5. ANE endpoint control message 4 (if the ANE endpoint applies; unknown).
6. GPIO0 = `0xf7fbdff9`, then the final command, `POWER_DOWN` (`0x10`) or
   `SUSPEND` (`0x21`), selected by `this+0x14a` (unknown). Wait for
   GPIO0 = `0x08042006`, bounded at 1 s.
7. On success, poll the suspend flag from step 1 (200 ms). Then wait for
   `0x1600048 & 3` nonzero (idle), bounded at 200 ms. Then `DisableCamPMU`
   (contents not decoded).
8. On failure: `DisableCamPMU`, wait 100 ms, write the overrides
   `0x292485c = 0xffffffff` and `0x2924858 = 0x80008000`, and mark the
   shutdown dirty.

### 4.2 Canonical restore, after power-on (E2c, and E3 aborts before release)

E2 does not use this path. E2 uses the teardown R5 proved on hardware:
1. detach stream 0 and read back TCR and TTBR;
2. write the 13 local registers off;
3. verify the 16 PMGR words and the boot identity.

E2 writes no mailbox, GPIO, fabric or COPROC register.

**Written-word ledger.** Every ISP-block word this path or section 3 writes
is read before its first write and recorded:
- the mailbox IRQ enables, `0x292495c`, GPIO0..7;
- `0xc00`, the fabric registers and `0x1600040`;
- `0x28a4080`, and the overrides `0x292485c` and `0x2924858`.

After the power-off, the rung powers the 13 local registers on once more,
reads the ledger words back, and powers them off again. A word that does not
return to its recorded value is restored by an explicit write if its meaning
allows it. Otherwise it stops the ladder for a human decision. This is how
E2c proves whether the local power cut resets the ISP block.

Each wait is bounded. A bound that expires marks the run dirty and is
recorded, and the remaining steps still run (Linux safety choice). Native
`power_off_hardware` order (proven): DART first, then the fabric drain, then
power.
1. Clear the mailbox IRQ enables `0x2924008`, `0x2924018`, `0x2924038` and
   `0x292495c`. Write GPIO1 = `0xfeedbabe`.
2. Detach stream 0: unmap every range, flush the TLB, then read back on all
   three instances that SID 0's TCR is 0 and its TTBR invalid. Read the error
   registers.
3. Drain the fabric, native `ResetTlimits`:
   1. wait for `0x74c`, `0x84c`, `0x94c` and `0xa54` to read 0, 100 ms each;
   2. clear bits 24..25 of `0xc00`;
   3. write 0 to `0x748`, `0x848`, `0x948` and `0xa50`;
   4. wait for the four counters again.
4. `0x1600040 = 0x2000000`, then wait for bit 25 (50 ms).
5. Write 0 to the 13 local registers in descending order, and poll
   `(v & 0x8ff) == 0` on each (50 ms).
6. Verify the 16 tracked PMGR words against the pre-run baseline. That
   includes the three provider domains, which must be unchanged and active.
   Verify the boot identity.
7. Apply the DATA rule from section 1.

### 4.3 Post-release path (E3 and later, any exit after COPROC release)

This covers a stage-1 timeout, a DART fault, a failed park or any other
abort. A powered-off ISP clears the step 1 marker; if it survives, the next
start resets first (section 3, step 2).
1. Record the cause and mark the run dirty unless the park succeeded.
2. If the firmware did not park (E3 always, since it never reaches IPC),
   the native failure order:
   1. `DisableCamPMU`. **Gate before E3:** decode it, or prove it unnecessary
      when no sensor was started.
   2. Wait 100 ms.
   3. Write the failure overrides `0x292485c = 0xffffffff` and
      `0x2924858 = 0x80008000` (ledger words).
   4. Wait up to 200 ms for `0x1600048 & 3` nonzero, and record the result.
   5. Native `ISP_ForceShutdown` is a cooperative request (GPIO5 =
      `0x08042008`, a doorbell, GPIO6 acknowledgement within 5 s). Its
      doorbell index is unknown, so E3 does not use it.
3. Then section 4.2, steps 1 to 7. Power-gating an ISP processor that did not
   acknowledge is the native failure behaviour: `power_off_hardware` runs
   after a failed suspend with the shutdown marked dirty (proven).

### Outcome rules (all paths)

- A run that ends dirty keeps the module loaded with power off, for
  inspection.
- If power cannot be verified off, nothing further is attempted, and the
  state is reported for a human decision.
- No reboot, suspend or power-off of the machine is part of any rung.
- **Unknown:** whether preloaded firmware restarts after a stop on M4 (E5).

## 5. Experiment ladder

| Rung | What it does | Processor | Proves |
| --- | --- | --- | --- |
| E1 | Hash TEXT and DATA through `/dev/mem` | not powered | Current-boot reference established: TEXT byte-identical across boots; DATA carries per-boot words (done 2026-10-21) |
| E2 | Steps, in order: (1) reference DATA snapshot; (2) power on as R5; (3) record the read-only state: GPIO1, the fabric counters `0x74c`, `0x84c`, `0x94c`, `0xa54` (must read 0 before attach), and stream 15 TCR, PROTECT and error on all three DARTs; (4) R5 map extended: window `0x10000000000 + 0xe0000000`, TEXT read-only, DATA read-write, zeroed heap and IPC surface on stream 0 of all three DARTs; (5) per-page lookup, TCR `0x9`, TTBR and page-table attribute readback; (6) the R5-proven teardown; (7) TEXT and DATA hashes against the reference, heap and IPC still zero | halted | Linux can build the firmware's pre-release view without anything writing it |
| E2c | Section 3 steps 1 to 5 exactly (power-on `0xf`, `0x28a4080`, the reset branch only if GPIO1 = `0xfeedbabe`, boot-mode GPIOs, premise checks) with the written-word ledger, then section 4.2. No COPROC write | halted | The pre-release writes behave, and the local power cut resets what it should |
| E3 | Section 3 steps 1 to 8 with the fault monitor, then section 4.3. No boot arguments | runs to stage 1 | The firmware boots under Linux mappings and states its requests |
| E4 | Stages 2 and 3, channel table, section 4.1 park, then section 4.2 | full boot | IPC handshake and a clean park |
| E5 | Restore the reference DATA, fresh surfaces, start again, park | full boot | Restart within one boot |

**Gates before E3:**
- E2 and E2c done and reviewed.
- `DisableCamPMU` decoded or proven unnecessary.
- `0x28a4080` and the override words resolved by the E2c ledger.
- The IRQ 387 sharing precondition checked.
- The stream 15 and DAPF state from E2 reviewed.

## 6. Open questions

1. Whether stream 15 and DAPF are needed before stage 1 or 2 (E2b, E3).
2. The meaning of `0x28a4080`, `0xc00` bits 24..25 and `0x1600040` bit 25.
3. Live GPIO0..5 after wake and the stage-2 buffer sizes (E3).
4. The park unknowns of section 4.1 and the boot-argument `+0x00` field (before E4).
5. Whether the ISP DART is cache-coherent (E2 records attributes).
6. Whether the ANE endpoint steps apply to this Air.
7. Whether this Air's own macOS camera driver matches the decoded copy.
8. Firmware restart within one boot (E5).
