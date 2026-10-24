# J715 inactive channel payload

This assembler prepares files, not an installation. No J715 hardware success
exists yet. Internal speakers require a protected exact-ID owner qualification;
native panel F1/F2 additionally require genuine N5 panel, protocol, firmware and
handoff evidence. Offline compiler fixtures are not qualified binaries.

From the source checkout, with the five required subprocess environment
variables exported:

```sh
python3 -B tools/package-j715-native.py \
  --audio-build /absolute/audio-build/build-receipt.json \
  --donor-source /absolute/verified-donor --output /absolute/new-stage
python3 -B -m unittest discover -s tests -p 'test_j715_native_package.py' -v
```

The N2 compilation receipt must retain its original sibling `payload/j715/`
and `source/audio-build-receipt.json`. Preparation alone is refused. Original
source bytes, the single MCA patch, integration sources, both model identity
and runtime ABI, kernel inputs, immutable builder identities and all eight
binary sizes/hashes are checked. The donor checkout is verified against N1's
commit/tree/blob/mode/hash closure, including licenses.

Both N3 candidates are reconstructed from that verified donor, never from
an externally edited candidate receipt. Their artificial offline ID reports
only select candidate filenames; no GPIO is read and no current boot is
authorized. Both original FIR sets and owner-FIR source inputs are retained.
Canonical graph and filter filenames remain unchanged inside each candidate.
Neither candidate is copied onto active policy paths.

## Explicit native build backend

`tools/build-j715-native-audio.py` is a separate BUILD-ONLY backend. It emits a
schema1 receipt with `backend=native-prepared-external`, not Docker image IDs or
container transcripts. The Docker entrypoint and donor/J713 contracts retain
their original checks. N6 accepts either original Docker receipts or the explicit
native backend; unknown backends and relabeled commands are refused.

The native backend has its own reviewed kernel and runtime ABI contract in
`audio/j715/native-build-contract.json`. Matching `uname -r` alone is insufficient:
native target pins and build version must match that contract. The old donor
inventory remains specific to the original Docker branch. The native ABI records
both ALSA links directly to `libasound.so.2.0.0`, the actual target libraries and
loader, rather than silently substituting J713's `qualified.json`.
The reviewed native target is kernel #19, GNU build ID
`14abe5081b1dd9a206fe7442dd45c12467a79ed4`. The prepared tree must retain its
matching `vmlinux` and `include/generated/utsversion.h`; the builder checks the
ELF build ID and build version as well as the six pins before and after building.

After supplying a matching prepared kernel with native `fixdep` and `modpost`,
explicit extracted native tool roots, the signed pinned Arch ARM Rust package
and a complete offline Cargo cache, an owner can run:

```sh
python3 -B tools/build-j715-native-audio.py \
  --donor /absolute/verified-donor \
  --kernel /absolute/matching-prepared-kernel \
  --exports /absolute/reference-sound-exports.symvers \
  --llvm /absolute/llvm-runtime/usr --rust /absolute/native-rust/usr \
  --rust-package /absolute/pinned-rust.pkg.tar.xz \
  --rust-signature /absolute/pinned-rust.pkg.tar.xz.sig \
  --rust-keyring /absolute/reviewed-archarm.gpg \
  --sysroot / --cargo /absolute/offline-cargo-home \
  --output /absolute/new-native-audio-output
```

Export `TMPDIR`, `NODE_COMPILE_CACHE`, `BUN_RUNTIME_TRANSPILER_CACHE_PATH`,
`PYTHONDONTWRITEBYTECODE=1` and `GIT_MASTER=1`. `TMPDIR` must be tmpfs. There is no
fetch, install, kernel preparation, activation or Docker fallback operation.
Rust's explicit library root is included in every subprocess environment.
The CLI verifies package contents/signature, pinned executables, all six native
kernel inputs, supplemental exports, development inputs and the locked Cargo
cache before compiler invocation; offline graph resolution precedes output.

Two clean object/target passes reuse one owned scratch pathname so build paths
do not introduce false differences. All eight complete output files are compared.
The receipt retains actual argv and results, compiler hashes/versions/library
closure, ELF build IDs and module vermagic, model identity, relative runtime ABI
pins, both pass inventories and linker evidence. Only the small prepared source
closure and receipt, payload and evidence persist; temporary object/target/cache
trees are removed on success or failure. `repeatable=true` is separate from
`rebuilt_identical=false` and `hardware_qualified=false`: neither release
equivalence nor physical speaker behavior follows from this build.

`payload-receipt.json` inventories every staged installable file, with absolute
future destination, mode, byte size and SHA-256. The receipt itself is metadata,
not an installed file. Its schema is `packaging/j715-native/payload.schema.json`.
`channel-paths.json` lists individual external ownership exceptions and future
selected-policy paths. Namespace roots are not permissions to overwrite a
system tree. Modules stay under `/usr/lib/omarchy-m4/j715/audio/`, never the
global module tree. Units are inert files: no scriptlet, enablement link,
daemon reload, service start, boot mutation or updater is emitted.

The user-session readiness drop-in stays in the immutable audio payload under
`session/`. The installed user recovery configuration is untouched. Future
ID-selected WirePlumber, UCM and FIR paths are listed individually; no broad
`etc/`, asahi-audio or systemd exception is requested. The exact model path is
`/usr/lib/omarchy-m4/j715/audio/config/apple/j715.conf`.

## Optional display closure

Audio-only preparation explicitly records display as `omitted`, not brightness
success. To request display, add all three inputs:

```sh
  --display-build /absolute/module-output/module.json \
  --display-loader /absolute/loader-output/loader-receipt.json \
  --display-base /absolute/unchanged-j715.dtb
```

These are the existing separate N5 module and loader receipts, not a new build
API. Each output directory must retain its original `source-receipt.json`,
`profile.json`, evidence and binary outputs. Missing evidence or a synthetic
profile fails the entire requested assembly before creating output. The
loader closure includes reproduced stock and both notch variants. Module and
audio kernels must agree. The base DTB is checked against the qualified
profile; the existing N5 serializer generates the inactive DT candidate.

Everything lives under `/usr/lib/omarchy-m4/j715/backlight/`. Runtime helpers
retain the `tools/` and `display/j715/` relative schema layout. The staged
backlight owner has exactly three old immutable path prefixes adapted to this
namespace; the original source is not edited. The staged inventory hashes the
adapted bytes. Managed brightness bindings invoke this namespaced Python
frontend by absolute path, rather than depending on an absent command alias.
No firmware/DMA/panel values are substituted or inferred.

## Later private owner gate and public channel

This assembly deliberately emits no N4 activation manifest or qualification.
A private, separately authorized owner gate must bind actual compiled ELF
identities, runtime ABI, the full immutable manifest and a fresh helper report
to the current boot and exact speaker ID. ID 0 needs its own physical
qualification. A running daemon or passing offline suite is not audible sound.
Display requires actual panel/handoff qualification and native key observations.
The gate must test the staged path adaptation before runtime deployment.

First qualification uses a separate authorization, not a fabricated successful
hardware receipt. Its activation manifest sets `qualification` explicitly to
null and names `first_qualification_authorization` at the exact namespaced
path in the runtime contract. The root-owned, pinned authorization has
`kind=first-qualification-authorization`, `status=authorized`,
`scope=private-protected-test` and `hardware_qualified=false`. It binds the
payload, exact speaker ID/profile, current boot, a future monotonic deadline
and a separately pinned private protocol. Completed qualification retains
the original receipt form; the two forms are mutually exclusive.

The private owner must first approve the inventory and concrete bounded
protocol, prove ordinary-session discovery isolation, and arm and verify
stop supervision. No authorization is issued by this assembler. Its expiry
blocks admission, publication and direct health gating, but does not itself
stop an already-running daemon or playback. The approved supervisor must
execute cutoff and invalidation on deadline, abort or controller loss.
The kernel's independent lease protection remains required.

Registration, daemon startup and root readiness retain their existing
ordering and checks during the isolated first test. The ordinary no-argument
session helper refuses this authorization even after root readiness; only
the approved isolated route may invoke the direct protection-health gate.
Keep the existing desktop recovery override intact. Physical observations
are recorded separately, and a later accepted qualification needs a new
pinned manifest and fresh boot, not a relabeling of the live test generation.
Neither form changes N2's build/ABI requirements or public promotion gates.

Only after those gates may the channel owner publish corresponding source and
append real binary joins using the existing archive/SHA-256 join form. Existing
J713 joins, manifests, sources and recovery paths stay unchanged. Promotion is
through `omarchy-m4-update`, with maintainer review and exact individual path
ownership; this work creates no guessed URL/hash, PKGBUILD, public deployment
script or alternate install command. Output must be new, outside source/build
inputs and outside live system trees. No install or activation option exists.
