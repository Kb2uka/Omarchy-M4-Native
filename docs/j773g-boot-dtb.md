# J773g boot devicetree

The plain M4 Mac mini (J773g, Mac16,10) is a `testing` board
([m4-boards.md](m4-boards.md), #136). Its first tester kit boots this
devicetree with the kernel the Air ships.

## How it is built

`tools/build-j773g-dtb.py` applies `patches/j773g-board.dtso` to the newest
J713 devicetree, `j713-usb0` (`352bfa74…`, [j713-front-usb.md](j713-front-usb.md)).
Both Macs use the T8132, so every SoC node and every SoC fix in that tree
carries over unchanged. That includes the NVMe windows, CPU frequency, idle
and boost, the temperature sensor, and poweroff through the Abbey PMU at
SPMI `0xe`. The mini's survey shows the same PMU (`pmu,abbey`) at the same
address.

The overlay changes only what the two boards wire differently. Gravity
Linux's `t8132-j773g.dts`, the devicetree its M4 mini alpha boots, is the
reference:

| Change | Why |
|---|---|
| `compatible` and `model` become `apple,j773g` / "Apple Mac mini (M4, 2024)" | The mini's identity, which the board gates read |
| Boot framebuffer power domains become `dispext0_fe` and `dptx_phy` | The mini's picture comes out of HDMI through the external display pipe; the J713 keeps the internal panel's `disp_cpu` on |
| Wi-Fi and Bluetooth `brcm,board-type` become `apple,sakhalin` | The mini's radio firmware (the installer already collects it for `j773g`) |
| PCIe port 2 enabled, with its reset pin (165), bus 2 and an `ethernet@0,0` child; a DART for it at `0x492000000` (interrupt 1250); `iommu-map` entry `0x200`; alias `ethernet0` | The BCM57762 Ethernet. m1n1 fills in its MAC address from the ADT through the alias. The new DART uses the port 0 DART's power domain, `apcie_phy_sw`, which also keeps `apcie_gp` (Gravity's choice) on |
| The J713 keyboard and trackpad (MTP, its mailbox, DART and DockChannel FIFO) and the keyboard backlight (PWM and `pwm-leds`) disabled | Not present on the mini |

The two rear USB-C ports nearest the HDMI port use the same USB-PD
controllers as the J713's two ports (SPMI `0xc` and `0xa`) and the same DWC3
controllers, so they work unchanged. Plug the keyboard and mouse in there.
The front ports (behind a hub on DWC3 2) and the third rear port (DWC3 3,
USB-PD at SPMI `0x8`) are not wired yet.

The builder pins the base hash. It checks every base node that the overlay
names by phandle number (the base has no `__symbols__`), and refuses any
merged tree that differs from the expected one in any other property, node,
memory reservation or boot CPU.

## Build

On a Linux host with `dtc` and `fdtoverlay`:

```sh
python3 tools/build-j773g-dtb.py --base j713-usb0.dtb --output j773g.dtb
```

`j713-usb0.dtb` is in the `package-inputs` release. With `dtc` 1.8.1 the
output is `c2a4f0cc0e28841bd5c3a1beb1342a2b6cfebfa670cf53db10b6bfec2c92332b`.

## Not known yet

Nobody has booted this devicetree. The first tester boot should report, from
`dmesg`:

- whether the HDMI framebuffer stays on past the point where unused power
  domains turn off;
- Ethernet link on port 2, and Wi-Fi and Bluetooth with the Sakhalin
  firmware;
- the CPU frequency, idle and temperature drivers, which were measured only
  on the Air.

Native HDMI through the DCP, the speaker, the front ports and the third rear
port need later work (see the J773g survey in #137). Gravity's devicetree also
reserves a `ramoops` buffer near the top of RAM to keep the console across a
hang. It is left out here until its placement is checked against this
project's m1n1 allocations.
