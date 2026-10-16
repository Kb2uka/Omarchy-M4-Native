# J713 front USB-C port

Both USB-C ports on the M4 MacBook Air now work the same way. Before this
change only the port nearest the hinge worked; the front port (nearest the
user) had no USB controller in Linux.

## Symptoms

- A device plugged into the front port did nothing: no USB bus, no
  enumeration, no kernel messages.
- The same device worked in the hinge-side port.

## Cause

The Air has two identical USB-C ports, each with its own Synopsys DWC3
controller, ATC PHY, pair of DARTs (the IOMMUs in front of the controller's
DMA) and SPMI PD controller:

| Port | Controller | DARTs | PHY | PD controller |
|---|---|---|---|---|
| Front (USB0) | `usb@402280000` | `iommu@402f00000`, `iommu@402f80000` | `phy@403000000` | SPMI `0xc` |
| Hinge side (USB1) | `usb@40a280000` | `iommu@40af00000`, `iommu@40af80000` | `phy@40b000000` | SPMI `0xa` |

In the boost-state devicetree (`c4f036b8…`, `omarchy-m4-boot` 1-11) the two
ports differed in only two ways, read from the owner's Air on 2026-10-16:

- USB0's controller and both of its DARTs were `status = "disabled"`, so no
  platform device, and no xHCI host, existed for the front port.
- USB0 still had the DART stream IDs 1 and 14 from patch 0012's reading of the
  ADT mapper. USB1 uses streams 0 and 1 on its own DARTs; that is what made
  USB1 work at all ([USB EEM evidence](j713-usb-eem-evidence.md)), and it now
  runs a SuperSpeed drive.

Everything else was already in place for both ports: the same controller
properties, the connector graph from each PD controller to its controller and
PHY, both PHYs bound to `phy-apple-atc`, and identical ATC tunables, which
m1n1 copies into both PHY nodes. m1n1 disables a `usb@` node only when the ADT
has no matching `usb-drd`, and the ADT has both.

## Fix

[`patches/j713-usb0.dtso`](../patches/j713-usb0.dtso) turns on USB0's
controller and both DARTs and gives USB0 streams 0 and 1 on its own DARTs,
exactly as USB1 is set up. No other property changes: no forced role, no new
PHY or PD settings.

[`tools/build-j713-usb0-dtb.py`](../tools/build-j713-usb0-dtb.py) applies it
to the boost-state devicetree. It refuses any other base, checks that the base
has USB0 off with streams 1/14, USB1 on with streams 0/1, and the same
controller layout on both ports, and refuses a result that differs from the
base anywhere but those four properties. DTC 1.8.1 produced 50,696 bytes,
SHA-256 `352bfa74351ad7d12d993f752fe9c09d682511bca0d4a60177cda0d716d1d614`, on
two hosts (Arch `dtc` 1.8.1 and the `m4-dtc` builder image).

It ships in `omarchy-m4-boot` 1-12 through `omarchy-m4-update`. The composer
and `tools/m4-j713-update-boot-dtb.py` accept it only after the boost-state
devicetree.

## Hardware check (owner's Air, 2026-10-16)

The devicetree was put into `boot.bin` with `m4-j713-update-boot-dtb.py`
(backup `boot.bin.pre-usb0`), then the Air was restarted.

- Live tree after the restart: USB0's controller and both DARTs `okay`, with
  `iommus = <0x53 0>, <0x54 1>`. Both USB0 DARTs initialized (16 streams), the
  controller joined IOMMU group 1, and `dwc3-apple 402280000.usb` probed and
  waited for a cable, as USB1 does.
- Hinge-side port, same boot: a LaCie Mobile Drive (`059f:10c4`) enumerated at
  SuperSpeed (`speed=5000`) on `40a280000`, as before.
- Front port: the same drive enumerated on `402280000` at SuperSpeed
  (`speed=5000`) as UAS, and the 2 TB disk and both partitions appeared. A
  2 GiB direct read (`dd bs=4M count=512 iflag=direct`) took 17.7 s
  (about 120 MB/s, the drive's own rate) with no DART fault, xHCI error, UAS
  reset or I/O error.

## Undo

Before the next package update, `sudo m4-boot-compose --rollback --apply`, or
copy back the backup the updater kept:
`sudo cp /boot/efi/omarchy/boot.bin.pre-usb0 /boot/efi/omarchy/boot.bin && sync`.
If Linux does not start, the same copy works from macOS with the EFI partition
mounted.

## Limits

- J713 (13-inch M4 Air) is the only Air this project builds for. The 15-inch
  M4 Air and the other T8132 boards in `packaging/m4-boards.json` stay refused
  until they are qualified; when they are, their DTBs need the same two
  changes.
- Checked with one SuperSpeed mass-storage drive in one plug orientation.
  USB-C alternate modes (DisplayPort, Thunderbolt/USB4), charging through the
  front port and every peripheral are not qualified.
