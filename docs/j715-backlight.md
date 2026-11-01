# J715 native panel backlight

This adds a separate brightness-only DCP client for the 15-inch M4 MacBook Air,
J715 / T8132. J715 remains a **testing** board. It does not inherit J713
qualification, and this change does not qualify an installer, audio, suspend
or a new kernel.

## Observed hardware behavior

The native client and current-boot display handoff were activated on J715 with
kernel `7.1.9-m4-gpu-backport-20261008`. The backlight registered at 140 nits
with a maximum of 525. The tested module SHA-256 was
`054c6256e8255c1a62b4683f051d1c590faad76d39b40ba8761b01aecc2d98c6`.
This identifies the tested module, not a published package input.

The owner confirmed that F1 and F2 visibly dim and brighten the built-in
panel. Physical brightness key events 224 and 225 were captured separately.
After the J715-only video-group permission fix, ordinary-user native commands
read back 140 -> 116 -> 142 nits, then restored 140. These command readbacks
and the physical-key observations are separate captures; they are not one
timestamp-correlated trace.

The new packaged startup path is tested offline, not by a fresh channel
installation. There is no speaker or audio success claim here.

## Separate client and current-boot handoff

`display/j715/backlight/client.c` reuses the locked J713 AFK transport without
changing it. The J715 client checks its own board, profile identity, stream,
DMA geometry and reserved memory before explicit firmware activation. Probe
does not wake DCP. The native backlight becomes available only after protocol
validation and a successful initial brightness command.

`tools/build-j715-backlight-dt.py` verifies the unchanged base DTB hash and
adds only profile-bound DCP, DART and mailbox resources. It does not manufacture
a successful handoff marker. The loader patch validates the current boot's
ADT mappings, page tables, reserved spans and framebuffer before publishing
the memory regions and handoff marker Linux consumes.

The loader recipe preserves both SEP fixes: resolving the SEP alias after
FDT mutations, and avoiding the ROM random request against running sepOS on
T8132. These overlap the SEP work in PR #168. Keep the known-good fixes when
rebasing or deduplicating that PR; the display donor overlay predates them,
so the recipe applies them **after** the overlay.

## Build inputs and provenance

The display source lock names baseline
`6c6d2e4dd48b6306416c28686b5212b6bc4f77dc` and exact `display/j713` file
identities. Integration-only descendant commits are accepted; unrelated
history, committed changes anywhere in that selected tree, and changed locked
worktree bytes are rejected. Adding this integration in a commit therefore
does not invalidate its own source lock.

The builders require a non-synthetic qualified display profile and its hashed
evidence. `tests/fixtures/j715-display-synthetic.json` is deliberately rejected
by production commands. No private profile, firmware, boot image or evidence
bundle is part of this change.

The module recipe needs the matching prepared kernel build, including
`Module.symvers`, generated release header, configuration and `vmlinux`.
The loader recipe needs the locked companion source archive. Both use a native
arm64 builder with Ubuntu Clang 21.1.8; the loader also requires Rust 1.93.1.
The immutable builder image digest is supplied as `J715_BUILD_IMAGE_ID`.
The loader reproduces its known-good SEP-preserving stock image before
building separate hidden/shown J715 payloads. Build receipts bind real output
hashes to the inputs; they do not assert hardware success or byte-identical
reproduction of the new J715 outputs.

Maintainer build entry points, in an isolated builder with new output
directories, are:

```sh
bash tools/build-j715-backlight.sh MODULE_OUTPUT MATCHING_KERNEL_BUILD QUALIFIED_PROFILE
python3 -B tools/build-j715-backlight-dt.py \
  --base UNCHANGED_J715_DTB --profile QUALIFIED_PROFILE --output NEW_DTB
bash display/j715/build-backlight-loader.sh COMPANION_SOURCE LOADER_OUTPUT QUALIFIED_PROFILE
```

These are build interfaces, not owner installation instructions. Supply
task-local `TMPDIR`, `NODE_COMPILE_CACHE` and
`BUN_RUNTIME_TRANSPILER_CACHE_PATH`. The builders do not install or replace
the live boot image.

## Update-channel integration

The existing `omarchy-m4-backlight` package moves from `1-1` to `1-2`. It
retains the exact J713 module, helper, metadata and service, and adds:

- A separate `m4-j715-backlight` command and its validator/schema dependencies.
- `m4-j715-backlight.service`, enabled by the package hook only on J715 for the
  next boot.
  The unit requires J715 module, pins and profile files, and its `ExecCondition`
  requires J715 / T8132 while excluding J713. The owner then verifies the
  kernel/module/profile and current-boot handoff before activation. Its
  durable failure marker prevents automatic repeated attempts.
- The J715 key hook only after successful native startup. F1/F2 and the
  existing XF86 brightness actions use the J715 owner; external focused
  displays retain Omarchy's existing routing. Custom XF86 brightness
  configurations are preserved by the existing owner logic.
- A udev rule matching only `j715-dcp` on add/change. It changes only that
  device's `brightness` file to group `video`, mode `0664`. It grants no
  debugfs or other panel access.

The channel allowlist and package checker verify the added exact paths,
modes and source bytes. No new opt-in package or second owner install path
is added. `m4-j715-backlight --disable` holds future startup without unloading
the live display client; `--enable` clears the hold/failure marker for the
next boot after the owner has investigated the failure.

## External publication blocker

The recipe deliberately does **not** download or install an unpublished J715
module, DTB, loader, profile or evidence bundle. Without those qualified
assets the added service is skipped, and it does not rewrite brightness keys.
The tested module hash above is not a release URL or enough to fabricate a
package source entry.

Before this can deliver J715 activation through `omarchy-m4-update`, the
maintainer needs:

1. Published immutable module and hidden/shown loader inputs, sizes, hashes,
   build receipts and corresponding-source joins.
2. The matching qualified DTB/profile identities and a reviewed runtime
   evidence-distribution contract. The current runtime validator checks the
   profile's evidence files, including firmware evidence; private evidence
   must not be silently copied into a public package.
3. Pinned channel/boot-composer integration for those actual artifacts,
   followed by a fresh update and repeat-boot check on J715.

Those inputs are not invented here. The existing J713 boot composer and
installer qualification gates remain intact.
