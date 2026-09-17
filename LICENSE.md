# Licensing

This repository contains original work by KB2UKA and patches to third-party
source files. **There is no single permissive license for the entire tree.**
The following grants cover KB2UKA's contributions; they do not replace
upstream copyright notices or expand rights in third-party material.

Copyright (c) 2026 KB2UKA for original contributions.

## Scope

| Material | License |
| --- | --- |
| Original prose in `README.md`, `SPEC.md`, `docs/*.md`, `recon/README.md`, and the original explanatory text in this file and `THIRD_PARTY_NOTICES.md` | MIT |
| `tools/adt.py`, `tools/adt-audit.py`, `tools/boot.sh`, `tools/mkdtb.sh`, and `.gitignore` | MIT |
| `patches/0001-*.patch` through `patches/0013-*.patch`: changes to Apple `.dts` / `.dtsi` files | GPL-2.0-or-later OR MIT |
| `recon/pmgr-j713-generated-from-live-adt.dtsi` | GPL-2.0-or-later OR MIT for the contributed description; upstream notices remain applicable to any incorporated source material |
| `patches-kernel/m4-boot-hacks-7.1.9.patch`: `arch/arm64/lib/delay.c` hunks | GPL-2.0-only |
| The same patch: `drivers/irqchip/irq-apple-aic.c` hunks | GPL-2.0-or-later |
| `patches-kernel/nvme-apple-t8132-split-nvmmu.patch`: `drivers/nvme/host/apple.c` hunks | GPL-2.0-only |
| The same patch: `Documentation/devicetree/bindings/nvme/apple,nvme-ans.yaml` hunks | GPL-2.0-only OR BSD-2-Clause |
| Full texts in `LICENSES/` | Reproduced license documents; their own terms apply |

An `OR` grants a choice between the listed alternatives for that material.
A patch containing multiple source files keeps the terms of each affected
file; an MIT option for a device tree does not make a Linux driver MIT.
The upstream spelling `GPL-2.0+ OR MIT` means
`GPL-2.0-or-later OR MIT`. Legacy kernel `GPL-2.0` denotes version 2 only.

The license texts are included here:

- [MIT](LICENSES/MIT.txt)
- [GNU GPL version 2 only](LICENSES/GPL-2.0-only.txt)
- [GNU GPL version 2 or later](LICENSES/GPL-2.0-or-later.txt)
- [BSD 2-Clause](LICENSES/BSD-2-Clause.txt)

For the MIT grant over original work, the copyright holder is **KB2UKA** and
the year is **2026**. For KB2UKA's binding additions under BSD-2-Clause, use
the same holder and year. The generic templates in the license-text files
do not supersede actual third-party notices.

## Upstream material

Preserve existing source headers when applying or redistributing the patches.
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) records the relevant notices
and source links, including the MIT notice for m1n1 used as a reference for
the NVMe sequence. The repository contains patches, not complete copies of
Linux, m1n1, or Omarchy. Obtain those projects and their licenses separately.

If distributing binaries built from these changes, comply with the licenses
of the complete resulting work, including applicable corresponding-source
requirements. This patch repository alone is not the complete corresponding
source of the historical kernel build.

The raw ADT and IODeviceTree captures are withheld from the public tree.
No blanket software-license grant is made over Apple firmware, macOS,
proprietary payloads, or hardware captures supplied separately. Factual
hardware observations in the research do not convey rights to those works.
All trademarks remain with their respective owners. The applicable license
texts contain the warranty and liability terms.
