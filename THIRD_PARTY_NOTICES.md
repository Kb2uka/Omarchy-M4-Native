# Third-party notices and provenance

Original project contributions are credited to **studioxradio aka kb2uka**.
Existing file-level KB2UKA notices are preserved. This notice
preserves upstream attribution separately; it does not imply endorsement.

## Linux and Asahi device trees

The Apple T8132 source files used by the device-tree patches carry:

> Copyright The Asahi Linux Contributors

Their SPDX expression is `GPL-2.0+ OR MIT` (the modern equivalent is
`GPL-2.0-or-later OR MIT`). That expression applies to the affected
`t8132.dtsi`, `t8132-j713.dts`, and `t8132-jxxx.dtsi` source, and is retained
in the new `t8132-usbpd-spmi.dtsi` introduced by patch 0006.

References: [Asahi Linux tree](https://github.com/AsahiLinux/linux),
[T8132 source](https://github.com/AsahiLinux/linux/blob/asahi/arch/arm64/boot/dts/apple/t8132.dtsi).
These branch links move; the research's recorded source identifiers are in
the README and historical runbooks.

## Kernel code patched here

| Upstream source | Existing notice / license |
| --- | --- |
| `arch/arm64/lib/delay.c` | Copyright (C) 2012 ARM Limited; author Will Deacon; GPL-2.0-only. Its header describes the OpenRISC implementation as a basis. |
| `drivers/irqchip/irq-apple-aic.c` | Copyright The Asahi Linux Contributors; GPL-2.0-or-later. Its header also credits `irq-lpc32xx` (Copyright 2015–2016 Vladimir Zapolskiy) and `irq-bcm2836` (Copyright 2015 Broadcom). |
| `drivers/nvme/host/apple.c` | Copyright The Asahi Linux Contributors; GPL version 2 only. Its header credits the PCI driver (Copyright (c) 2011–2014, Intel Corporation) and RDMA host code (Copyright (c) 2015–2016 HGST, a Western Digital Company). |
| `Documentation/devicetree/bindings/nvme/apple,nvme-ans.yaml` | GPL-2.0 OR BSD-2-Clause. The upstream binding identifies Sven Peter as maintainer. |

Sources: [delay.c](https://github.com/AsahiLinux/linux/blob/asahi/arch/arm64/lib/delay.c),
[AIC](https://github.com/AsahiLinux/linux/blob/asahi/drivers/irqchip/irq-apple-aic.c),
[NVMe](https://github.com/AsahiLinux/linux/blob/asahi/drivers/nvme/host/apple.c),
[binding](https://github.com/AsahiLinux/linux/blob/asahi/Documentation/devicetree/bindings/nvme/apple,nvme-ans.yaml).
Keep the full existing headers in the patched source. See also the
[Linux licensing rules](https://docs.kernel.org/process/license-rules.html).

### AIC workaround provenance

The historical gap analysis explicitly credits **Yureka** and records commit
`7c74add40c06` for the AIC EL2 workaround used during this bring-up. That is
the provenance recorded by the original investigation; this handoff does not
claim that the idea originated here. The local patch also bypasses the AIC
timer mask/unmask paths. The affected AIC source remains GPL-2.0-or-later.
The abbreviated reference is preserved as historical attribution, not a
claim that its original upstream object is included or publicly resolvable
from this snapshot.

### Derived PMGR description

`recon/pmgr-j713-generated-from-live-adt.dtsi` is a hardware description
produced from the private target ADT and compared with the Asahi PMGR tree.
Its header credits KB2UKA's original contributions and includes an Asahi
contributor notice for the upstream device-tree foundation. This does not
claim that the underlying hardware data originated with either contributor.
The expression `GPL-2.0-or-later OR MIT` covers the contributed description,
not Apple's underlying firmware or privately retained raw capture. Existing
notices in patched upstream files remain unchanged. See the full scope in
[LICENSE.md](LICENSE.md).

## m1n1 reference

The offline NVMe investigation used the M4 sequence in
[m1n1 `src/nvme.c`](https://github.com/AsahiLinux/m1n1/blob/main/src/nvme.c)
as a reference (the research notes record `53f8ee9b`). The boot sessions
used a separately obtained m1n1 checkout. No m1n1 tree or binary is bundled;
`tools/adt.py` is a standalone parser, not a copied m1n1 module.

`patches-m1n1/base/src/` holds five unmodified MIT-licensed m1n1 files
(`dockchannel_uart.c`, `iodev.c`, `iodev.h`, `nvme.c`, `nvme.h`) from the
alpha.1 bootloader source, with that tree's `LICENSE` beside them in
`patches-m1n1/base/`. The tests compile them against host shims. See
[docs/j713-m1n1-dockchannel-tx.md](docs/j713-m1n1-dockchannel-tx.md) and
[docs/j713-m1n1-issue82-ans-reset.md](docs/j713-m1n1-issue82-ans-reset.md).

The upstream [MIT license](https://github.com/AsahiLinux/m1n1/blob/main/LICENSE)
is reproduced below to preserve its notice for any adapted reference material:

MIT License

Copyright The Asahi Linux Contributors

Permission is hereby granted, free of charge, to any person obtaining
a copy of this software and associated documentation files (the
"Software"), to deal in the Software without restriction, including
without limitation the rights to use, copy, modify, merge, publish,
distribute, sublicense, and/or sell copies of the Software, and to
permit persons to whom the Software is furnished to do so, subject to
the following conditions:

The above copyright notice and this permission notice shall be
included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

## External projects and artifacts

Omarchy, omarchy-mac, Fedora Asahi Remix, BusyBox, LLVM, and the related
projects named in the research are external dependencies or references.
Their source trees and built distributions are not shipped here. Their
respective licenses apply if you obtain or redistribute them.

The full license documents under `LICENSES/` were obtained from the
[SPDX license-list-data project](https://github.com/spdx/license-list-data/tree/main/text).

## Gravity and Asahi installer

The J713 recovery/radio and SSD-root patches target the
[Gravity Linux installer](https://github.com/GravityLinux/installer), based on
the Asahi installer. The recorded base is
`6420623289fe7544cb8589b658355e57f0290fcc`. Its MIT license retains
`Copyright (c) 2021 The Asahi Linux contributors`; the exact notice is included
in [LICENSES/Gravity-installer-MIT.txt](LICENSES/Gravity-installer-MIT.txt).
Gravity's M4 adaptation and the Asahi foundation are upstream work, not original
work of this project. Preserve additional notices in the actual inputs used
when assembling an installer.

Source: [pinned installer license](https://github.com/GravityLinux/installer/blob/6420623289fe7544cb8589b658355e57f0290fcc/LICENSE).

## Radio, input and Omarchy additions

The radio patch changes Broadcom `fwil_types.h` (Copyright (c) 2012 Broadcom
Corporation) and `pcie.c` (Copyright (c) 2014 Broadcom Corporation). Both source
headers specify ISC. The DockChannel HID source carries the Asahi contributor
notice and `GPL-2.0 OR MIT`. These terms apply to the corresponding patched
files, not a blanket MIT license over the kernel.

Pinned source: [fwil_types.h](https://github.com/AsahiLinux/linux/blob/77cb8f24c2381a8abb7272d7bbdec548d6426a8a/drivers/net/wireless/broadcom/brcm80211/brcmfmac/fwil_types.h),
[pcie.c](https://github.com/AsahiLinux/linux/blob/77cb8f24c2381a8abb7272d7bbdec548d6426a8a/drivers/net/wireless/broadcom/brcm80211/brcmfmac/pcie.c),
[DockChannel HID](https://github.com/AsahiLinux/linux/blob/77cb8f24c2381a8abb7272d7bbdec548d6426a8a/drivers/hid/dockchannel-hid/dockchannel-hid.c).

The Omarchy ARM Node patch and retained `mise-work` fixture derive from Omarchy,
whose MIT notice credits David Heinemeier Hansson. That full notice remains in
[tests/fixtures/omarchy-LICENSE](tests/fixtures/omarchy-LICENSE). Related
[omarchy-mac](https://github.com/omacom/omarchy-mac) integration is also credited
where used; it is not represented as this project's original implementation.

## Signed M4 packages

The `[omarchy-m4]` packages redistribute Linux kernel binaries and modules
(GPL-2.0-only) and a devicetree derived from Apple `.dts` sources
(GPL-2.0-or-later OR MIT). Their corresponding source is the kernel tree,
configuration and devicetree base published with v0.1.0-alpha.1 (see
[release-source.md](docs/release-source.md)), plus `patches-kernel/j713-input/`
and `patches/j713-smc-poweroff.dtso` in this repository. Each package carries
its license texts. See [package-channel.md](docs/package-channel.md).

### DisplayLink evdi

`omarchy-m4-evdi` redistributes binaries built from DisplayLink's
[evdi](https://github.com/DisplayLink/evdi) 1.15.1, unchanged: the `evdi`
kernel module (GPL-2.0-only; Copyright (c) 2013-2026 DisplayLink (UK) Ltd.,
with parts Copyright (C) 2012 Red Hat and parts based on `udlfb.c`, Copyright
(C) 2009 its respective authors) and `libevdi` (LGPL-2.1-or-later; Copyright
(c) 2015-2024 DisplayLink (UK) Ltd.). Each source file's own notice governs. The
corresponding source is the upstream `v1.15.1` archive, mirrored in the
`package-inputs` release, built with `display/evdi/build.sh`. The package
carries both license texts; the LGPL text is
[LICENSES/LGPL-2.1-or-later.txt](LICENSES/LGPL-2.1-or-later.txt), copied from
evdi's `library/LICENSE`. Synaptics' DisplayLinkManager is not redistributed.

## Additional runtime components

Native runtime candidates also use Gravity/Asahi kernel and decoder work, Mesa,
PipeWire, WirePlumber, ALSA, speakersafetyd, libva and mpv. Their complete source,
modifications, notices and binary provenance must accompany the corresponding
release as required by their licenses. Listing a dependency here does not mean
its complete source or binaries are already included in this checkout. Preserve
every upstream file-level notice when those components are joined.

The static J713 FIR assets are final numerical responses reproduced with the
retained measurement pipeline. Their [provenance record](audio/j713/filters/README.md)
identifies the inputs, approved output identities and the distinction between
final numerical filters and the source inputs. No project credit or source-code license grants
rights in Apple firmware, macOS, tuning files, Widevine or separately obtained
assets. See the [experimental release boundary](docs/experimental-release.md).
