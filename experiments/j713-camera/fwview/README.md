# fwview — E2 firmware-view map (do not load)

Private, never-merged diagnostic for the J713 (T8132) camera ISP. This
directory is rung E2. It must not be loaded, and the `REQUALIFY_*`
placeholders must not be filled in, without its own review and
requalification on the target boot.

E2 powers the ISP the way R5 did, maps the iBoot-preloaded TEXT and DATA
regions plus fresh zeroed HEAP and IPC pages, checks the maps, reads the
DART2 page tables back, and tears the device back down. The processor
stays halted.

`--map-check` requires `--data-ref PATH`. Before `insmod` it reads DATA
(phys `0x10001f30000`, size `0x748000`) through a read-only `/dev/mem`
map, checks the qualified DATA hash, and writes that byte copy to `PATH`
mode `0600`. An existing file is accepted only when it is a regular
file, mode `0600`, owned by the caller, and byte-identical. The read
loops until the full size or EOF. Otherwise the launcher does not load,
and it does not save a mismatched image.
After the run it re-hashes TEXT and DATA. If the module is still loaded
with `map_count` or `retain` nonzero, it does not write `/dev/mem` and
exits nonzero. If reading the module parameters raises because a file
is missing, unreadable, or unparsable, or any `OSError` is raised after
`insmod` was attempted (closing the module fd, reading `/proc/modules`,
or a sysfs read), the mapped state is unknown. The launcher leaves
`/dev/mem` unwritten, hashes TEXT and DATA, prints both results
(`TEXT hash unchanged` or `TEXT hash changed`, and the same for DATA),
prints that `--cleanup-only` is the recovery step and that
`--restore-data` restores DATA from the reference, and exits nonzero.
No `result` file means the module did not publish parameters, and a
DATA restore is still allowed. If DATA changed and the module is known
to be unmapped, the launcher writes the saved DATA bytes back through
a shared read-write `/dev/mem` map of exactly that range. The map is
closed without `msync`: a shared `/dev/mem` map of non-RAM is uncached,
so the stores are already visible. The re-hash is the result. Exit 2 is
`DATA restored` when the hash matches, and `DATA restore failed` when
it does not. An error from the write still re-hashes when the map was
opened. It never writes TEXT and never writes any other range. A DATA
write, including `--restore-data`, is refused unless DATA and TEXT are
fully disjoint with no overflow (`data_phys + data_size <= text_phys`
or `text_phys + text_size <= data_phys`) and `data_size` is the
qualified size. Exit 0 is a clean load. Exit 1 is any other failure.

`--restore-data` is mutually exclusive with the other modes and
requires `--data-ref`. It checks the same boot, kernel, and target
identity as the other modes. It refuses unless the module is absent
from `/proc/modules`, or present with readable parameters showing
`map_count`, `retain`, and `cleanup_pending` all 0. It reads the
reference (regular file, mode `0600`, exact DATA size, looping until
the full size or EOF) and requires its sha256 to equal the qualified
DATA hash. It then hashes current DATA through `/dev/mem`. When that
matches it prints `DATA already matches` and exits 0. Otherwise it
writes the reference through the same exact-range path, re-hashes, and
prints `DATA restored` (exit 0) or `DATA restore failed` (exit 2). It
does not insmod and it never writes TEXT.

After `insmod` returns, if `/sys/module/j713_isp_fwview` published
parameters, the launcher prints `result`, `first_error`, `retain`,
`cleanup_pending`, `map_count`, `ladder_stop`, `text_prot`, `data_prot`,
`heap_prot`, `ipc_prot`, `lookup_count`, `readback_count`, and
`pte_walk_count`. The run fails unless the first six are all 0.
`text_prot` must not have the write bit (`2`). A nonzero `data_prot`,
`heap_prot`, or `ipc_prot` must have that bit. On a `--map-check` load
that publishes parameters, `lookup_count` must equal the number of
mapped 16 KiB pages (TEXT, DATA, HEAP, and IPC), `readback_count` must
equal the module's complete readback count, and `pte_walk_count` must
equal the samples per region times 4. A mismatch fails the run. The
launcher does not write `cleanup_only`. When `retain`,
`cleanup_pending`, or `map_count` is set, it prints that
`--cleanup-only` is the recovery step.

## What E2 does not do

- It does not release the processor.
- It does not write COPROC, the mailbox, GPIO, the fabric, `0xc00`,
  `0x1600040`, or `0x28a4080`.
- It does not do cache maintenance. `dcache_clean_poc` is not exported
  on this kernel, and the processor never reads the pages in E2.
- It does not use the section 4.2 canonical restore path. Teardown is
  the sequence R5 proved: unmap, detach, drop the overlay, then the 13
  local PMGR registers off. The module's existing restore path still
  checks the 16 PMGR words. The launcher still checks the boot identity.

## How E2 differs from R5

R5 (`dartmap/`) mapped five scratch pages in a DMA window that started
at IOVA 0 and used three-level tables. E2 is a different module and a
different overlay so the two cannot be confused:

| | R5 | E2 |
| --- | --- | --- |
| Module | `j713_isp_dartmap` | `j713_isp_fwview` |
| Master node | `j713-isp-dart-map-test` | `j713-isp-fwview-test` |
| DMA window | `[0, 2^36)` | `0x10000000000` size `0xe0000000` |
| Tables while mapped | three-level | TCR exactly `0x9` (four-level) |
| Mappings | five scratch pages | TEXT, DATA, HEAP, IPC |

TEXT and DATA are iBoot-preloaded physical memory. Linux does not
allocate or free them. HEAP (5 pages) and IPC (7 pages) are fresh
zeroed pages. Idle SID0 and SID12 TCR before the overlay is still
exactly `0x1`, the value R5 read. The four-level check applies only
after the map.

Before attach, E2 reads GPIO1, the four fabric counters, SID15 TCR,
PROTECT, and DART ERROR on each instance. One premise line records
GPIO1, the four fabric words, `sid15_tcr[3]`, `dart_protect[3]`, and
`dart_error[3]`. A nonzero fabric counter, or any nonzero DART ERROR
word, aborts before any map and still runs the normal teardown. GPIO1,
SID15 TCR, and PROTECT are recorded and not judged. After the software
lookup, and before unmap, readback walks SID0's TTBR. The three DART
instances must hold the same TTBR value. The first DART ERROR read is
checked before the TLB poll: any nonzero word returns `-EIO`. The later
ERROR read still rejects a set flag bit. The table address is decoded
the way the T8110 driver writes it: valid is bit 0, the address field
starts at bit 2, and the address is in 16KiB pages. Each table address,
including every next-level table, must pass `pfn_valid` before
`phys_to_virt`, or the walk returns `J713_DM_ERR_PTE`. Table entries
are read through the kernel linear map. The walk does not write.
The first, middle, and last 16KiB page of TEXT,
DATA, HEAP, and IPC must each be a valid leaf whose output address is
that page. `NO_READ` and `NO_CACHE` are clear on every sample. `NO_WRITE`
is set on TEXT and clear on DATA, HEAP, and IPC. The low 32 bits of those
leaves are `text_pte_bits`, `data_pte_bits`, `heap_pte_bits`, and
`ipc_pte_bits` (three each). `pte_walk_count` counts the leaves read. A
mismatch fails the run and still uses the normal teardown.

`iommu_map` is called with `J713_DM_TEXT_PROT` for TEXT (read and cache,
value 5) and `J713_DM_RW_PROT` for DATA, HEAP, and IPC (read, write, and
cache, value 7). Those bits are `IOMMU_READ` (1), `IOMMU_WRITE` (2), and
`IOMMU_CACHE` (4). `op_map` returns an error if the TEXT value ever has
the write bit.

After detach, and before the HEAP and IPC pages are freed, every byte of
those pages must still be zero. A nonzero byte is a ladder stop: the
pages are still freed and the gates still go off. The post-detach heap
and IPC zero check reads those pages through cacheable kernel mappings,
so it can only detect stray writes if the ISP DART is cache-coherent.
Coherence is unknown, and it is a gate before E3.

The flock path stays `/run/lock/j713-isp-probe.lock` so this probe
excludes the other ISP probes.

## Offline checks

Run on the designated remote Linux validation host under its test lock.
Use the exact target kernel tree and its device-tree compiler. No
hardware load is part of these commands.

```sh
make -C fwview mock layout-test embed-test
./fwview/j713_isp_probe_mock
make -C fwview dtbo-contract-test DTC="$KDIR/scripts/dtc/dtc"
(cd fwview && python3 -m unittest test_run_probe.py test_real_launcher_io.py)
```

`make dtbo` replaces the intentional `j713_isp_fwview_dtbo.inc`
placeholder with generated source. Do not commit that generated file
or the build outputs. The public tree's `REQUALIFY_BOOT_UUID`,
`REQUALIFY_ADT_SHA256`, `REQUALIFY_TEXT_SHA256`, and
`REQUALIFY_DATA_SHA256` placeholders make the launcher refuse a real
boot.
