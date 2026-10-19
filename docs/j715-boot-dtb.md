# J715 boot devicetree

The 15-inch M4 Air (J715, Mac16,13) is a `testing` board
([m4-boards.md](m4-boards.md), #142). Its tester kit boots this devicetree with
the kernel the 13-inch Air ships.

## How it is built

`tools/build-j715-dtb.py` applies `patches/j715-board.dtso` to the newest J713
devicetree, `j713-usb0` (`352bfa74…`, [j713-front-usb.md](j713-front-usb.md)),
as [j773g-boot-dtb.md](j773g-boot-dtb.md) does for the Mac mini. Both Airs use
the T8132 with the same register map, and the J715 ADT wires the trackpad, the
radios' power and reset, the speaker reset, the headphone codec and both USB-C
ports like the J713's ([j715-macos-survey.md](j715-macos-survey.md)). Every SoC
node and SoC fix in the J713 tree carries over unchanged: the NVMe windows, CPU
frequency, idle and the boost gate, the CPU temperature sensors and thermal
zone, poweroff through the Abbey PMU at SPMI `0xe`, and the front USB port.

The overlay changes only what the two Airs differ in:

| Change | Why |
|---|---|
| `compatible` and `model` become `apple,j715` / "Apple MacBook Air (15-inch, M4, 2025)" | The board identity, which the board gates read |
| Wi-Fi and Bluetooth `brcm,board-type` become `apple,kos` | The J715's radio module (the `j715ap` installer profile collects its firmware) |
| The trackpad's `firmware-name` becomes `apple/tpmtfw-j715.bin` | The firmware m1n1 hands over for the J715 trackpad |

The overlay names every node by path, so it depends on no base phandle. The
builder pins the base hash, checks the three J713 values it replaces, and
refuses any merged tree that differs from the expected one in any other
property, node, memory reservation or boot CPU.

## Build

On a host with `dtc` and `fdtoverlay`:

```sh
python3 tools/build-j715-dtb.py --base j713-usb0.dtb --output j715.dtb
```

`j713-usb0.dtb` is in the `package-inputs` release. With `dtc` 1.8.1 the
output is `84a19aba63365c5b07a6f53780c0b48f0d054e9bf0c787ef285e7c9cf401f066`.

## Hardware check

On one J715 (the 26.5.1 stub of the `j715ap` profile, the alpha.1 kernel),
this exact devicetree (`84a19aba…`) was swapped into `boot.bin` with nothing
else changed and booted on 2026-10-19 (#142). The root was the unofficial J715
kit's, which also carries J715 speaker, GPU and CPU idle work that this
repository does not ship yet.

| Check | Observed |
|---|---|
| Identity | `compatible`, `model`, `brcm,board-type` (`apple,kos`) and the trackpad `firmware-name` read back as the J715 values |
| Trackpad, Wi-Fi, Bluetooth | Worked |
| Front USB-C | A USB SSD worked under the `402280000` xHCI, no DART faults |
| Poweroff | `systemctl poweroff` powered the machine off |
| cpufreq | P policy 912–4464 MHz with `scaling_max_freq` 1800 MHz (the boost gate's cooling state 16 of 19); E policy 900–2808 MHz |
| E cluster at 2808 MHz | Six E cores loaded for 600 s: 72 °C at 30 s, 77 °C at 3 min, then 78–79 °C with one 82 °C reading. The 95 °C passive trip was not reached and the E cooling state stayed 0 |
| P cluster | 1800 MHz at most during the load |

## Not known yet

- In the first boot of the check, `policy0/stats/time_in_state` showed one tick
  (about 10 ms) at 4464 MHz; a second boot stayed at or below 1800 MHz. The boost
  gate is a trip of the `cpu-die-thermal` zone, which exists only once
  `macsmc_hwmon` binds (about 2 s into boot on this machine), so until then the P
  policy's maximum is 4464 MHz and schedutil can pick it during boot. The J713
  boot devicetree should have the same window (not checked on a J713). It is a
  short early-boot excursion at boot temperatures, but the gate does not hold P
  at 1800 MHz from the first moment
  ([#145](https://github.com/Kb2uka/Omarchy-M4-Native/issues/145)).
- The speaker, GPU and CPU idle need later work for the J715 in this repository.
