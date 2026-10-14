# M4 board devicetrees

Upstream Asahi Linux describes six M4 (T8132) Macs, each with a small board
file on top of shared chip files (`t8132.dtsi`, `t8132-jxxx.dtsi`). Most of
this project's devicetree patches (`patches/00*.patch`) edit those shared
files, so they change every M4 Mac's devicetree, not only the 13-inch Air's.

The hosted Linux CI job fetches the pinned Asahi tree (tag `asahi-7.1.9-1`,
`77cb8f24c`) with `tools/ci/fetch-asahi-dts.sh`. `tests/test_m4_board_dtbs.py`
then builds all six boards with and without the patches using
`tools/m4-board-dtbs.py`. It fails when:

- a patch no longer applies, or a board no longer compiles;
- a patch adds a reference (`iommus`, `phys`, `power-domains`, `mboxes`) from
  an enabled node to a missing or disabled node, on any board;
- the J713 build loses its single documented `simple_bus_reg` warning;
- what any board's devicetree turns on, turns off or warns about differs from
  the record in `tests/fixtures/m4-board-dtbs.json`.

A patch that changes another board therefore changes that record in the same
pull request, where a reviewer can see it. The test prints the new record when
it differs.

<!-- m4-board-dtbs:begin -->
| Board | Model | dtc warnings | Nodes the patches enable | Nodes the patches disable | Broken references added |
|---|---|---|---|---|---|
| J604 | Apple MacBook Pro (14-inch, M4, 2024) | 1 | 24 | 0 | 0 |
| J623 | Apple iMac (24-inch, 2x USB-C, M4, 2024) | 1 | 24 | 0 | 0 |
| J624 | Apple iMac (24-inch, 4x USB-C, M4, 2024) | 1 | 24 | 0 | 0 |
| J713 | Apple MacBook Air (13-inch, M4, 2025) | 1 | 80 | 14 | 0 |
| J715 | Apple MacBook Air (15-inch, M4, 2025) | 1 | 24 | 0 | 0 |
| J773G | Apple Mac mini (M4, 2024) | 1 | 24 | 0 | 0 |
<!-- m4-board-dtbs:end -->

On the five boards other than J713, the patches enable the same 24 nodes: the
SEP DART and mailbox, the SPMI bus with an `apple,abbey-pmic` PMIC and its
nvmem cells, two USB-C PHYs (ATC) with their display crossbars, and the PCIe
pin group. Those descriptions were checked on the J713 only.

Building is not booting. A board that compiles here has never been started
with this project's kernel. This check also does not compare its J713 build
with the devicetree that ships to the Air.

To run it elsewhere: `tools/ci/fetch-asahi-dts.sh <dir>`, then
`M4_ASAHI_TREE=<dir> python -m unittest discover -s tests -p test_m4_board_dtbs.py`,
or `tools/m4-board-dtbs.py <dir>` for the report alone.
