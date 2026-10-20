# Retained audio and DockChannel transport hardening

[Issue 43](https://github.com/Kb2uka/Omarchy-M4-Native/issues/43) covers the
retained MCA arithmetic and DockChannel packet bounds. The MCA correction now
lives in the source used by the audio build. The kernel candidate recipe applies
the complete DockChannel correction after the historical input patches.
This resolves the source/build portion of the audit. Native qualification and
package transition are required before replacing qualified binaries; they are
not claimed here. The installed channel and historical binary identities remain
unchanged.

## MCA arithmetic

`audio/j713/kernel/mca.c` now evaluates the generic clock product as:

```c
bclk_ratio = (unsigned long)tdm_slot_width * tdm_slots;
```

Both operands were previously unsigned int, so the product could wrap before
assignment to unsigned long. The generic setter stores supplied counts and
widths without enforcing the physical bounds claimed in an earlier assessment.
Shipped generic callers use 32×2, 32×8 and 16×16. J713 returns through
`j713_fe_params` before this assignment; no deployed J713 overflow is demonstrated.

The synthetic boundary 65536×65536 reproduced zero before the fix and 4294967296
afterward. Tests now read the actual build source by default. `MCA_SOURCE`
selects an explicit baseline or mutant. The corrected source hash is
`834e9783a8bfc8619867abccae6afed877314a88c82eabd05d52d7e486c68e44`,
also recorded in `audio/j713/imported-source.sha256`.

`patches-kernel/j713-audio/mca-bclk-width.patch` records the earlier correction
against historical source; do not apply it again to current source.
`audio/j713/qualified.json` continues to identify historical binaries.
Changing source does not make a new module qualified.

## DockChannel bounds

The build recipe applies
`patches-kernel/retained-hardening/dockchannel-packet-bounds.patch` to driver
`e4eebbee612eee32bd51ce370d412afc8cf62a9a7e087a3797c2c29306cedfb4`.
That is the source after the startup-retry, macsmc locking and AFE patches
in [release-source.md](release-source.md). The historical `j713-input` patch
tree and its source join remain unchanged.

The correction:

- rejects every interface index outside the 16-entry table before indexing;
- requires the complete eight-byte subheader before ACK or work dispatch;
- rejects empty input reports on communication and ordinary interfaces;
- frees work with invalid inner lengths and rearms receive after allocation failure;
- rejects initialization names without a NUL inside the 16-byte field;
- bounds GPIO requests by their own initialization block length;
- rejects zero-length product names before indexing or logging their contents.

Tests compile the real checksum, receive, ACK, event, report and initialization
functions. Interface creation and hardware operations are host substitutes;
READY and GPIO command implementations remain mocked. This is not validation
of every driver function or of native firmware behavior. The immutable
baseline fixture retains its upstream license and hash.

## Observed evidence

All development tests ran on leased fleet hosts. The direct-source MCA suite
failed its boundary case before the fix and passed all three afterward.
The expanded DockChannel suite ran 37 cases: four failed before the
initialization fixes, including two sanitizer-detected reads beyond the worker
allocation; all 37 passed afterward. Positive cases cover reports, ACK,
interface 15, one-byte reports, valid initialization names/products, GPIO
requests, descriptors and terminators. All 22 source-join tests passed.

Fourteen packet mutants and three arithmetic mutants were rejected by these
tests. They independently remove the initialization guards, weaken packet
bounds, omit cleanup/rearm, reject valid reports, remove operand widening,
cast after multiplication, or substitute a constant product.

A full ARM64 baseline kernel and then the hardened kernel linked with
LLVM 21.1.8 and Rust 1.93.1. All five `audio/j713/kernel` modules also linked
with `J713_PROFILE=conservative-v1`, `KCFLAGS=-Werror` and pinned supplemental
sound exports. This initial candidate build was incremental after the full
baseline reconstruction; it is not yet proof of a clean run of the new recipe.

| Artifact | SHA-256 |
| --- | --- |
| Hardened Image.gz, 18,900,954 bytes | `a7a160b6686652d7ae93218593d015febf9cc7ab0ef838148e551116befeb05e` |
| Hardened MCA module, 79,152 bytes | `fb941df4d55c4f2093b4595e0392aaf97ffe3a250228518b18a4f70568bf7454` |
| Configuration, unchanged | `14b4437b2b195e912a2ab614d28761acd3dc79f886d4487964aa0912616a3a6e` |
| Both export tables, unchanged | `5e3c2624c6a991bae503336137ce15071e51ef043edcd20ba1ad19df4e23d67c` |
| Speaker group, reproduced unchanged | `aaad802eb136ff6cb915feaa123001b3eb848bc2f038d0a5b71c8e2ff35d2581` |

ADMAC, macaudio and TAS2764 also reproduced their qualified hashes. Only MCA
changed among these five modules. The speaker group source join names its
five unchanged build inputs individually at the original revision, alongside
the configured kernel and supplemental export archives. MCA implementation
is not linked into that module.

## Reproduce a candidate

Use a leased Linux build host with Python 3.12 or newer and the exact local
builder image. Supply the published kernel archive and supplemental exports:

```sh
python3 tools/build-j713-hardened.py \
  --kernel-archive /absolute/path/kernel-controller.tar.gz \
  --sound-exports /absolute/path/reference-sound-exports.symvers \
  --image sha256:b2069edb8a23761d9211559fe632f5464aebc4cdfa9558d0f4c93211890fd7b1 \
  --output /absolute/path/new-candidate
```

The recipe checks pinned inputs, creates a new output directory, patches copied
sources and builds without network access. Its receipt records artifact
identities and explicitly leaves native qualification false. It does not
install a kernel or update a qualified manifest.

## Native and delivery gate

A clean recipe run and independent review are still in progress. Before
replacing qualified binaries, native boot and repeat boot, keyboard/trackpad
input, protected audio, GPU and video checks remain required. Suspend is not
part of this qualification. An owner performs any restart.

Before channel promotion, the kernel needs a reviewed video-manifest and boot
transition; MCA needs a matching audio-manifest transition. Rollback must
preserve unrelated module pins. Existing package hashes and historical
qualification records must not be relabeled as new evidence. Owners continue
to receive updates through `omarchy-m4-update`. A successful scanner job or
these offline tests does not establish native acceptance.
