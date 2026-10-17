# Licensing

Original project contributions are credited to **studioxradio aka kb2uka**.
This repository contains that original work alongside changes to third-party
source files. **There is no single permissive license for the entire tree.**
The following grants cover KB2UKA's contributions; they do not replace
upstream copyright notices or expand rights in third-party material.

Copyright (c) 2026 studioxradio aka kb2uka for original contributions.
Existing file-level notices using KB2UKA remain valid and are preserved.

## Scope

| Material | License |
| --- | --- |
| Original prose in `README.md`, `SPEC.md`, `docs/*.md`, `recon/README.md`, and the original explanatory text in this file and `THIRD_PARTY_NOTICES.md` | MIT |
| Original `tools/` and `tests/` files with MIT headers, plus `.gitignore`; upstream test fixtures retain their own notices | MIT |
| `patches/0001-*.patch` through `patches/0017-*.patch`: changes to Apple `.dts` / `.dtsi` files | GPL-2.0-or-later OR MIT |
| `recon/pmgr-j713-generated-from-live-adt.dtsi` | GPL-2.0-or-later OR MIT for the contributed description; upstream notices remain applicable to any incorporated source material |
| `patches-kernel/m4-boot-hacks-7.1.9.patch`: `arch/arm64/lib/delay.c` hunks | GPL-2.0-only |
| The same patch: `drivers/irqchip/irq-apple-aic.c` hunks | GPL-2.0-or-later |
| `patches-kernel/nvme-apple-t8132-split-nvmmu.patch`: `drivers/nvme/host/apple.c` hunks | GPL-2.0-only |
| The same patch: `Documentation/devicetree/bindings/nvme/apple,nvme-ans.yaml` hunks | GPL-2.0-only OR BSD-2-Clause |
| Original J713 packaging tools, tests and service templates, `audio/j713/README.md`, `audio/j713/policy/` routing/metadata configuration | MIT |
| `audio/j713/kernel/`, `audio/j713/bootstrap/`, `audio/j713/topology/` | Linux-derived GPL-2.0-only unless a file-level notice grants additional choices |
| `audio/j713/speakersafetyd/` | GPL-2.0-only application; vendored ALSA has its own Apache-2.0 OR MIT licenses |
| `patches-kernel/brcmfmac-bcm4388-rev6-c2-v116.patch`: Broadcom `fwil_types.h` and `pcie.c` hunks | ISC |
| `patches-kernel/j713-input/dockchannel-power-v2.patch`: Asahi DockChannel HID hunks | GPL-2.0-only OR MIT |
| `patches/installer/`: changes to the Gravity/Asahi installer and original added helpers/tests | MIT, retaining the upstream Asahi notice |
| `patches/omarchy/offline-node-arm64.patch`, `tests/fixtures/mise-work-v4.0.4.sh` | Omarchy MIT terms, retaining its upstream notice in `tests/fixtures/omarchy-LICENSE` |
| `audio/j713/headphones/kernel/` | GPL-2.0-only |
| `audio/j713/headphones/policy/` and `README.md`, `tools/m4-headphones.py`, `tools/build-j713-headphones.sh`, `packaging/omarchy-m4/omarchy-m4-headphones/`, `docs/j713-headphones.md` and the headphone tests | MIT |
| `display/evdi/build.sh`, `display/evdi/bench/evdi_probe.c`, `display/evdi/README.md`, `packaging/omarchy-m4/omarchy-m4-evdi/`, `docs/j713-external-displays.md` | MIT; the evdi binaries they build or package keep DisplayLink's GPL-2.0-only (module) and LGPL-2.1-or-later (library) terms |
| Full texts in `LICENSES/` | Reproduced license documents; their own terms apply |

The J713 headphone jack rows above are Copyright (c) 2026 Luthfi Hizb, under
the same terms.

An `OR` grants a choice between the listed alternatives for that material.
A patch containing multiple source files keeps the terms of each affected
file; an MIT option for a device tree does not make a Linux driver MIT.
The upstream spelling `GPL-2.0+ OR MIT` means
`GPL-2.0-or-later OR MIT`. Legacy kernel `GPL-2.0` denotes version 2 only.

The license texts are included here:

- [MIT](LICENSES/MIT.txt)
- [GNU GPL version 2 only](LICENSES/GPL-2.0-only.txt)
- [GNU GPL version 2 or later](LICENSES/GPL-2.0-or-later.txt)
- [GNU LGPL version 2.1 or later](LICENSES/LGPL-2.1-or-later.txt)
- [BSD 2-Clause](LICENSES/BSD-2-Clause.txt)
- [ISC](LICENSES/ISC.txt)
- [Gravity/Asahi installer MIT notice](LICENSES/Gravity-installer-MIT.txt)

For the MIT grant over original work, the project contributor is **studioxradio aka kb2uka** and
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

The raw ADT and IODeviceTree captures are withheld from the tracked source tree.
No blanket software-license grant is made over Apple firmware, macOS,
proprietary payloads, or hardware captures supplied separately. Factual
hardware observations in the research do not convey rights to those works.
All trademarks remain with their respective owners. The applicable license
texts contain the warranty and liability terms.

## J713 runtime assets

The approved native numerical filters are included under `audio/j713/filters/`
as static platform data. Their [provenance record](audio/j713/filters/README.md)
distinguishes those final coefficients from the Apple software and tuning inputs
used to reproduce them. The source-code licenses above do not grant rights in
those Apple inputs.

`audio/j713/qualified.json` keeps its historical hold for the earlier reference
files. Other corresponding-source requirements remain described in
[audio/j713/README.md](audio/j713/README.md).
