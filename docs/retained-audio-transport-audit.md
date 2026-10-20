# Retained audio and DockChannel transport audit

This is a source-hardening review against `develop`. It does not change a
current manifest, source hash, or source join, and it does not qualify a
kernel, an audio module, or an installed system. [Issue 43](https://github.com/Kb2uka/Omarchy-M4-Native/issues/43)
stays open. Delivery and native qualification are still pending, and CodeQL
alert 4 stays open. Nothing here suppresses that alert or closes the issue.

## Audio bclk product

In retained `audio/j713/kernel/mca.c`, `mca_fe_hw_params` assigns
`bclk_ratio = tdm_slot_width * tdm_slots`. Both operands are `unsigned int`
and the destination is `unsigned long`. On LP64 the multiply is evaluated in
32 bits before the widening assignment. The J713 path returns at the early
`j713_fe_params` call (line 756) and does not reach that assignment. The
generic setter `mca_fe_set_tdm_slot` stores the supplied slot count and slot
width and does not enforce a physical bound.

Shipped `macaudio_fe_init` callers use a 16 by 16 sense, or a width of 32
with slot counts 64/32 and 256/32. No shipped J713 overflow is demonstrated.
The synthetic pair `65536 * 65536` yields 0 on the unpatched baseline; the
unsigned-long product is 4294967296. The staged patch casts one operand
before the multiply:

`bclk_ratio = (unsigned long)tdm_slot_width * tdm_slots;`

`patches-kernel/j713-audio/mca-bclk-width.patch` is applied to a temporary
copy. Retained `mca.c` stays
`6ee2fade6fc237cedb211c4bd198891e10b4fabac947070dce9bc55307628846`.

## DockChannel packet bounds

`patches-kernel/retained-hardening/dockchannel-packet-bounds.patch` is staged
against the exact qualified driver
`e4eebbee612eee32bd51ce370d412afc8cf62a9a7e087a3797c2c29306cedfb4`.
In [release-source.md](release-source.md) that identity is the driver after
the archived startup-retry patch and the AFE action patch stack. Those
historical patches stay in `patches-kernel/j713-input`. The pending bounds
patch is deliberately outside that directory: the linux-m4-omarchy channel
payload pins the whole `patches-kernel/j713-input` tree at
`1cf949ef443fd8c6d8744a2e3ae8e7e3a270628a`, so a new file there would change
the binary source join. The pin, the assertion, and
`packaging/channel-source-joins.json` are unchanged. The audio bclk patch
already lives under `patches-kernel/j713-audio`, which is outside every
pinned tree. The minimal patch, on the receive path only:

- rejects interface 16 and 255 before any interface index;
- requires a complete 8-byte subheader before ACK or work;
- rejects a zero-length report payload;
- frees the worker when the inner length is invalid;
- rearms receive when allocation fails.

Interface 15, a normal report, ACK, and a comm READY message stay accepted.
The host fixture mocks the nested INIT, READY, and GPIO handlers, so this
review does not validate those implementations.

## Evidence

Hosted Linux CI, command `python -m unittest discover -s tests -p 'test_*.py' -v`:

- [run 39503135644](https://github.com/Kb2uka/Omarchy-M4-Native/actions/runs/39503135644)
  at `7a83141`: 1212 tests, the 8 new packet cases failed.
- [run 39503732964](https://github.com/Kb2uka/Omarchy-M4-Native/actions/runs/39503732964)
  at `13c6dab`: 1214 tests, those 8 plus the audio boundary case failed.
  The normal audio products passed.

On a leased Linux host the patched suite passed: 22 packet tests and 3 audio
tests. Valid
one-byte input reports are delivered unchanged; zero-length reports are
rejected on both communication and ordinary input interfaces.

Hosted CI [run 39510068277](https://github.com/Kb2uka/Omarchy-M4-Native/actions/runs/39510068277)
ran 1225 tests. The new packet and audio regressions passed. One existing
provenance test failed:
`test_channel_source_joins.ManifestTests.test_repository_channel_joins_pass_schema_and_match_pins`,
because the bounds patch was still inside the pinned `j713-input` tree
(`0d8e24036f31e1355a636e518a54496fff0f98ad` instead of
`1cf949ef443fd8c6d8744a2e3ae8e7e3a270628a`). That failure was corrected by moving the patch without changing its bytes.
[Run 39510535504](https://github.com/Kb2uka/Omarchy-M4-Native/actions/runs/39510535504)
then passed all 1225 tests. The final invalid-interface sweep adds one case;
its complete CI result is recorded on the pull request.

Eleven packet mutants were caught: drop the interface guard, allow 16, reject
15, drop the minimum header, allow a 7-byte header, drop the zero-payload
guard, drop the bad-length free, drop allocation-failure rearm, reject valid
one-byte reports, and restrict zero-length rejection to the communication
interface, and reject only invalid interfaces 16 and 255. The last three
survived the initial tests and failed after the boundary cases and the
complete invalid-interface sweep (16 through 255) were added. Three audio mutants were caught: remove the
widening, cast after the multiply, and hard-code the default product.

An ingress `< 8` to `<= 8` mutant survives: an eight-byte body has no
payload, so input reports and ACKs already reject it. Moving that rejection
earlier changes no accepted report, ACK completion, leak, or receive rearm
in these cases. This is an equivalent invalid-frame outcome, not evidence
that a valid boundary packet is accepted. The one-byte positive case above
checks the actual payload boundary.

Both patched ARM64 objects compiled: the DockChannel object
and `mca.o`. The DockChannel object used the pinned configuration
`14b4437b2b195e912a2ab614d28761acd3dc79f886d4487964aa0912616a3a6e`,
LLVM 21.1.8, and Rust 1.93.1. Both `make` levels left without an error.
That is an object compile. No full kernel build, link, native boot, audio,
video, or suspend check was run.

Baseline and mutant runs keep the retained files and set `DCHID_SOURCE` or
`MCA_SOURCE` so the patch is not applied. The same unittest command is the
normal remote suite. This note does not give an install or runtime path.
`omarchy-m4-update` stays the owner path, and only after a future native
qualification and package promotion.

## Boundaries still open

Two pre-existing sites in `dchid_handle_init` were inspected and are not
exercised by the current host stubs. A product-name block still evaluates
`product[blk->length - 1]` when `blk->length` is zero. The GPIO request check
compares `sizeof` of the request with the remaining message length, not with
`blk->length`, so a short block inside a longer message is not bounded by the
block length. These are source observations, not a demonstrated reachable
exploit. They are enough to refuse a claim that the whole transport is
validated. The bounded packet patch does not resolve them.

## Delivery gate

Before any promoted kernel or audio package: rebuild the full kernel and the
audio module, then preserve or refresh only the exact source and qualified
identities that correspond to that build. Do that only after attended native
checks of keyboard, trackpad, audio, and video, and ship through the signed
channel update. Do not restart or suspend the Air as part of this review.
Issue 43 and CodeQL alert 4 remain open until that resolution exists.
