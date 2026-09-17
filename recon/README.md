# Hardware research inputs

The research used two raw captures from the J713 test machine: a macOS
IODeviceTree text dump and a live binary Apple Device Tree captured through
m1n1. Those originals are preserved privately and are **not included in the
public tree** because they contain serial numbers, platform/volume
identifiers, and other machine-specific data.

The committed `pmgr-j713-generated-from-live-adt.dtsi` is a derived research
artifact. It is not a replacement board tree or an instruction to enable
all of its domains. The audit documents differences between the generated
description and the kernel's PMGR source.

The published [audit](../docs/t8132-adt-audit.md) and
[gap analysis](../docs/t8132-gap-analysis.md) retain the hardware mappings
and findings. References in those historical notes to `iodevicetree-full.txt`
line numbers refer to the privately retained original, not a downloadable
file in this directory. References to `air-adt-j713.bin` denote that same
private capture under a neutral name.

To rerun the tools, supply your own locally captured ADT and a DTB compiled
from the relevant patched source. Expect board, firmware, and revision
differences. Without the original captures and matching build inputs, a
reader cannot independently reproduce every recorded comparison from this
repository alone. Redact machine identifiers before sharing new captures.
