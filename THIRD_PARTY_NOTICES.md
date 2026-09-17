# Third-party notices and provenance

Original project contributions are credited to **KB2UKA**. This notice
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
