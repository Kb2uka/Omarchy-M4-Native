# J713 AFE native action candidate

This is an opt-in private candidate, not a qualified installer fix. The preceding
GPIO OUTPUT experiment failed native startup and was rolled back. The restored
kernel had owner-confirmed trackpad movement and click; repeated boot
and fresh-install reliability are not established by that recovery.

## Observed contract

The J713 platform describes `function-afe-reset` through the normal SMC PMU,
key `gP1c`. Its function template is `0x00010000`, with the argument substituted
in the low 16 bits. The native CBOR path registers firmware, then requests
power OFF (0) followed by ON (2). Each transition sends Will, performs the
configured host action, then sends Has with the host result. OFF uses argument
1 and waits 50 ms; ON uses argument 0 without a further delay.

These are function arguments, not independently measured pin levels. Native
ACTION and the generic GPIO OUTPUT command are different operations. The
candidate reproduces the documented caller's bytes rather than assuming those
operations have equivalent electrical effects. It does not establish the
meaning of the action template's bits inside SMC firmware.

The same native named-resource command 3 calls argument 1, waits 50 ms, then
calls argument 0, stopping after a failed operation. Its descriptor starts at
the event byte and has a minimum length of 20 bytes: interface at byte 1,
little-endian 16-bit resource ID at byte 2 and 32-bit command at byte 4. The
candidate checks those full-width fields before its new action path. Other
interfaces retain the existing legacy packet handling and pulse.

## Scope and preparation

Apply `patches-kernel/j713-input/macsmc-key-info-lock.patch` and
`patches-kernel/j713-input/dockchannel-afe-action.patch` after the existing
startup-retry patch. The source driver before the action patch is SHA-256
`3e779d232c2aab7263af15bc2fb9f77fa9532dec7e64a4de0b71ec61c1d8bc46`.

The new readonly parameter `dockchannel_hid.m4_afe_action=1` defaults off. It
requires the existing version-2 power option, both J713 and T8132 compatibility,
and the multi-touch interface. An unreachable SMC core rejects this opt-in path.
No installer or default boot parameter is changed by adding the patch.

Before firmware upload, preparation validates the transport's normal SMC GPIO
provider, pin 28 and retained ACTIVE_LOW flag as board identity. It reserves the
descriptor with GPIOD_ASIS, obtains the matching physical SMC parent, retains its
device reference and adds a managed supplier link. The physical parent must
include `apple,t8132-smc` in its compatible list. The live J713 device tree
contains `apple,t8132-smc` followed by `apple,t8103-smc`; it does not contain
the generic `apple,smc` string. A prior candidate rejected that real parent
before querying key metadata or sending any action. The regression fixture
now models the actual NUL-separated property and reproduces that rejection.
The descriptor is shared
with a later exact `afe-reset` request; other resource names are rejected.
No GPIO output or direction change is made during preparation.

The native function obtains write length from key metadata. Before any action,
the candidate therefore requires the actual key to report four bytes and write
permission. It rejects mismatches and API errors. The companion core patch
keeps the metadata command and shared-memory copy under the same existing SMC
mutex, following the core's read/write pattern. Without it, another command
can overwrite the returned metadata before it is copied.

The host writes little-endian `0x00010001` for OFF and `0x00010000` for ON.
The AFE mutex serializes preparation, power work and the named-resource pulse;
it is not held across DockChannel command/acknowledgement waits. A failed Will
causes no action. Host failures reach Has as generic IOReturnError, and the
original Linux error is returned rather than advancing the startup sequence.

## Qualification

Extracted-source checks are implementation evidence. The build must retain the
existing kernel configuration, module ABI and symbol exports. Native acceptance
separately requires actual Touch MT ready, pointer movement, normal click,
keyboard preservation and repeat boot. Graphics, video, speaker startup and
existing browser behavior must also be checked after the kernel change.

Preserve the exact working boot object before a native trial. Do not unbind the
transport: the retained driver's remove path contains BUG_ON. Neither a zero
SMC return nor passing offline tests qualifies a new installer. Keep firmware,
loader, device tree, initramfs and unrelated userspace unchanged during this
trial. Only a qualified candidate may advance installer kernel and integrity
pins, followed by exact packaged-payload and USB readback verification.
