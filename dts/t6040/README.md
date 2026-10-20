# T6040 devicetree sources (Mac mini M4 Pro, J773s)

Bring-up devicetree for the Mac mini (M4 Pro, 2024): board J773s, model
`Mac16,11`, SoC T6040. The upstream Asahi tree has no T6040 description, so
these are standalone sources rather than patches against it. When Asahi gains
one, the board moves to a patch series under `patches/` like the T8132 work.

| File | Contents |
|---|---|
| `t6040.dtsi` | The SoC: all 16 CPU positions, timer, AIC, PMGR blocks, watchdog, UART, and disabled ANS/NVMe, SMC, SPMI and USB nodes |
| `t6040-pmgr.dtsi` | 213 power domains, generated from the J773s ADT |
| `t6040-j773s.dts` | The board: compatible, model, chosen/framebuffer, memory |

The SoC file starts from `dts/t6040.dtsi` in
[damsleth/wallace](https://github.com/damsleth/wallace) (commit `55f116d`), the
J614s MacBook Pro bring-up. Every value was re-checked against the J773s ADT.
What changed, what matched and what could not be checked are in
[docs/j773s-dt-skeleton.md](../../docs/j773s-dt-skeleton.md).

## Building

The sources include `dt-bindings` headers from the pinned Asahi tree:

```bash
tools/ci/fetch-asahi-dts.sh /tmp/asahi
cpp -nostdinc -undef -D__DTS__ -x assembler-with-cpp \
    -I /tmp/asahi/include -I dts/t6040 dts/t6040/t6040-j773s.dts |
  dtc -I dts -O dtb -o t6040-j773s.dtb -
```

## Checking against a Mac

`tools/ioreg-adt.py` rebuilds a binary ADT from macOS's
`ioreg -p IODeviceTree -l -w0` text, so the audit runs before anything is
installed. The dump holds serial numbers; keep it and the rebuilt ADT private.
Once m1n1 runs on the Mac, use its ADT capture instead.

These directories are not under the shipped-code board check in
`tests/test_m4_boards.py`, and nothing in the installer or the update channel
uses them. J773s stays refused until it has hardware evidence.
