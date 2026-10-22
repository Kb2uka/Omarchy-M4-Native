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
against historical source. The recipe does not apply it to current source,
which already contains the correction.
`audio/j713/qualified.json` continues to identify historical binaries and is
left unchanged. Changing source does not make a new module qualified.

The candidate recipe copies only `kernel/` rows from
`audio/j713/imported-source.sha256`. Each listed path must be a relative name
under that directory. The recipe reads those regular files once, checks the
manifest digest, and writes those bytes into `output/mca`. It does not copy
other working-tree files. `patches-kernel/j713-audio/mca-bclk-width.patch`
remains in the tree as the historical record of the correction; the recipe
still does not apply it to current source. Before `output` is created, the
recipe probes `output`'s parent with a temporary `case-probe` file and refuses
the build when `CASE-PROBE` names that same file. Archive member names are
left unchanged.

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

The regression and mutation runs reported here ran on leased fleet hosts.
The direct-source MCA suite
failed its boundary case before the fix and passed all three afterward.
The expanded DockChannel suite ran 37 cases: four failed before the
initialization fixes, including two sanitizer-detected reads beyond the worker
allocation; all 37 passed afterward. Positive cases cover reports, ACK,
interface 15, one-byte reports, valid initialization names/products, GPIO
requests, descriptors and terminators. All 22 source-join tests passed.

Fourteen packet mutants and three arithmetic mutants were rejected by these
tests. They independently remove the initialization guards, weaken packet
bounds, omit cleanup/rearm, reject valid reports, remove operand widening,
cast after multiplication, or substitute a constant product. Six earlier
builder mutants were already caught. The closure baseline was 32 tests with
3 failures, and the case-probe test failed as well. The final fixes copy
only manifest-listed regular files after verifying their hashes, and they
refuse a case-insensitive output filesystem. On Homebase, Python 3.14, at
`91dabdf`, the builder suite is 35 tests and all pass. Five earlier review
mutants are caught again at that commit: removing the DER hash check, the
read-only export mount, the explicit entrypoint, duplicate-member rejection,
or extraction-through-symlink rejection. The explicit-entrypoint mutant
fails directly when the command lacks `--entrypoint`: the command-contract
assertion raises `ValueError`, which is the test failure rather than an
infrastructure failure. Three further closure mutants fail their assertions:
restoring `copytree` so an unlisted file is copied (the targeted copy test),
omitting the copy digest, and omitting the case-probe call. With those
fixes restored, the same 35 tests pass.

The final builder suite at `dc191cb` has 42 tests, all passing on Homebase.
A positive directory-alias test failed before removing the unnecessary
symlink-parent refusal and passed afterward. The destination filesystem still
must be case-sensitive. The success tests require a case-sensitive `TMPDIR`;
case-sensitive APFS is suitable, while case-insensitive destinations are
intentionally refused.

Five additional guard mutations fail behavioral assertions: accepting an empty
kernel manifest, accepting a backslash path, following a source-directory
symlink, following a symlinked audio root, and removing the nonblocking FIFO
open. The FIFO case fails within five seconds instead of hanging. Removing
the occupied-destination diagnostic is also caught, but that is diagnostic
coverage: `mkdir` still refuses the existing path before copying. It is not
an additional overwrite vulnerability. The restored 42-test suite passes.

The subsequent directory-alias refusal test increases the suite to 43 tests.
It simulates case-insensitive name lookup through a real directory alias,
checks refusal before output creation, and checks probe cleanup and unchanged
source pins. A mutant that returns early for an aliased parent passed the
previous 42 tests and fails this test. The restored 43 tests pass on Homebase.
The simulation is limited to the resolved destination directory: moving the
probe onto the host's default temporary filesystem is also caught. That
mutation passed before the simulation was scoped and fails afterward.
The builder implementation is unchanged from `dc191cb`.

A full ARM64 baseline kernel and then the hardened kernel linked with
LLVM 21.1.8 and Rust 1.93.1. All five `audio/j713/kernel` modules also linked
with `J713_PROFILE=conservative-v1`, `KCFLAGS=-Werror` and pinned supplemental
sound exports. An earlier incremental `Image.gz`
(`a7a160b6686652d7ae93218593d015febf9cc7ab0ef838148e551116befeb05e`,
18,900,954 bytes) is not the current recipe output. A later clean run that
still used the default certificate rule also produced a different kernel and
is not selected. The current recipe is the pinned public certificate build
below.

Two independent fresh builds at `8ee57be` of the pinned-certificate recipe
produced byte-identical `Image.gz`, all five audio modules, and the build
receipt. `cmp` and `diff` between those outputs reported no differences.
Those two builds are the earlier pinned-certificate recipe. A third clean
build of the `91dabdf` recipe, which adds the source-closure copy and
the case-probe check, completed on Homebase. Against one of the earlier
outputs (build A), `cmp` of `Image.gz`, `cmp` of the receipt, and `diff -qr`
of all five modules each exited 0. The image remains 18,900,939 bytes and the MCA module
remains 79,152 bytes, at the hashes below. The full 5,896-byte trust region,
starting at byte offset 42,950,656 in each decompressed `Image`, was verified
independently. Its SHA-256 is
`77f3aad25837130e2e41d6750019a0e526365eeda0bfd17f2c45bb7df28cc0cc`,
matching that region of the original qualified decompressed `Image`. This is a region
digest, separate from either complete kernel digest. The matched receipt still sets `native_qualified`
and `fresh_install_qualified` to false.

A fourth fresh build used `dc191cb3e7dc935cb5f4e13a5b44f2a835a6a331`,
the final builder implementation with directory-alias compatibility. Its
`Image.gz`, receipt and five modules again compare byte-for-byte equal to
build A. The complete trust region also matches the original qualified
kernel. Thus the hashes below are reproduced by the final implementation,
not inferred from the earlier builds.

| Artifact | SHA-256 |
| --- | --- |
| Current recipe Image.gz, 18,900,939 bytes | `ca1fff571cf1b9d8c0b07b2e97ba0a5402f93c1b7a2d54ea2c07e24a8b70f9d0` |
| Hardened MCA module, 79,152 bytes | `fb941df4d55c4f2093b4595e0392aaf97ffe3a250228518b18a4f70568bf7454` |
| Built-in trust region, 5,896 bytes | `77f3aad25837130e2e41d6750019a0e526365eeda0bfd17f2c45bb7df28cc0cc` |
| Configuration, unchanged | `14b4437b2b195e912a2ab614d28761acd3dc79f886d4487964aa0912616a3a6e` |
| Both export tables, unchanged | `5e3c2624c6a991bae503336137ce15071e51ef043edcd20ba1ad19df4e23d67c` |
| Speaker group, reproduced unchanged | `aaad802eb136ff6cb915feaa123001b3eb848bc2f038d0a5b71c8e2ff35d2581` |

The 5,896-byte trust region is in the decompressed `Image`, starting at
byte offset 42,950,656. Decompress `Image.gz` before reading that region.
The full region was verified independently. Its hash matches the original
qualified Air `Image` and is unchanged. That match is a public embedded
region, not a signature and not possession of a private key. ADMAC, macaudio,
TAS2764 and the speaker group reproduced their historical qualified hashes.
MCA is the only one of the five audio modules that differs from the historical
qualified module; its candidate hash and size are unchanged from the earlier
hardened candidate. This source change does not modify package source joins.
The historical speaker-group join remains unchanged here. MCA implementation
is not linked into that module.

The recipe now pins the public certificate recovered from qualified kernel
`c3704cfd2afb7760e73f0b9939a68864a30bd916fcd478bd960e538373123dc9`.
Its PEM hash is `db92215adb9de23e9bba226ca17332b27c3bff5981346163cc68a4239fcd40b5`;
its 1,324-byte DER hash is
`4b61a05d46fbbdd907dc03ea9b96ebdad0bec4dc11b3a7cf874243efeb1ae66a`.
Only the public certificate is included. Both make commands explicitly select
`CONFIG_MODULE_SIG_KEY=certs/qualified-module-certificate.pem`, which bypasses
the default key-generation rule. The archived configuration and its signature
verification policy remain unchanged. Automatic module signing remains disabled;
these outputs are not claimed to be signed. The recipe checks the copied PEM
and generated DER before writing a receipt and records the make override.
The two builds at `8ee57be` are that pinned-certificate recipe. The
`91dabdf` build uses the same certificate pin and adds the source-closure
copy and case-probe check. Both its receipt and the final `dc191cb` receipt
match earlier build A, including the same PEM and DER hashes.
No private key is in the tree or the
receipt, and these outputs are not claimed to be signed.

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

[Pull request 156](https://github.com/Kb2uka/Omarchy-M4-Native/pull/156)
changes the source and the build only. It does not publish a channel.
Native testing and package migration are in the dependent
[pull request 157](https://github.com/Kb2uka/Omarchy-M4-Native/pull/157).
On the installed J713, an initial upgrade, rollback, reapply, and repeat
boot completed with this same image and MCA module
(`ca1fff571cf1b9d8c0b07b2e97ba0a5402f93c1b7a2d54ea2c07e24a8b70f9d0`,
18,900,939 bytes;
`fb941df4d55c4f2093b4595e0392aaf97ffe3a250228518b18a4f70568bf7454`,
79,152 bytes). The evidence for that session is in the companion pull
request. The native acceptance requirements remain boot and repeat boot,
keyboard and trackpad input, protected audio, GPU, and video checks. PR 157
owns those checks and the migration evidence. Boot and repeat boot completed;
fresh protected-audio readiness, GPU state, and hardware-decoded video
comparisons passed. Instrumented playback through the protected PipeWire sink
completed for three seconds at 5% volume with exit 0; no human listening is
claimed. Device enumeration and automated login are recorded separately
from physical keyboard and trackpad acceptance, which remains unperformed
for channel promotion. These source changes do not waive that gate.
Closing issue 43 also requires the companion migration and recovery evidence. Build receipts keep `native_qualified` and
`fresh_install_qualified` false because the builder does not qualify
hardware. A fresh install, suspend, human listening, and physical typing
were not performed.

Hosted CI for source commit `dc191cb3e7dc935cb5f4e13a5b44f2a835a6a331`
passed [1,283 Linux tests in 80.058 seconds](https://github.com/Kb2uka/Omarchy-M4-Native/actions/runs/39533130154).
The Rust, macOS and policy jobs on that run passed.
[CodeQL](https://github.com/Kb2uka/Omarchy-M4-Native/actions/runs/39533130213),
[security checks](https://github.com/Kb2uka/Omarchy-M4-Native/actions/runs/39533130159)
and [package checks](https://github.com/Kb2uka/Omarchy-M4-Native/actions/runs/39533130165)
also passed at that source commit. Later documentation-only changes are
outside these revision-specific results.

Before channel promotion, the kernel needs a reviewed video-manifest and boot
transition; MCA needs a matching audio-manifest transition. Rollback must
preserve unrelated module pins. Existing package hashes and historical
qualification records must not be relabeled as new evidence. The production
update channel has not shipped this candidate. Owners continue
to receive updates through `omarchy-m4-update`. A successful scanner job or
these offline tests does not establish native acceptance.
