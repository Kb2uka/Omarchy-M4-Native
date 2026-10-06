# J713 MTP reset pin correction

The original J713 input patch copied reset GPIOs 8 and 24 from earlier
MacBooks. The Air's ADT actually names these reset functions under
`/arm-io/dockchannel-mtp/mtp-transport`, one level below the transport.

| ADT child | Function | SMC key | Linux GPIO offset |
| --- | --- | --- | --- |
| `multi-touch` | `function-afe-reset` | `gP1c` | 28 |
| `stm` | `function-stm-reset` | `gP1d` | 29 |

Both functions reference the ADT phandle resolving to
`/arm-io/smc/iop-smc-nub/smc-pmu`. Their function name is `pKW4`. The preserved
capture and the 2026-10-06 live capture agree. Linux's `gpio-macsmc.c` maps
`apple,smc-gpio` to the `gP` prefix and encodes the GPIO offset as hexadecimal;
the old offsets select `gP08` and `gP18`, which are different keys.

Apply patch 0014 after patches 0001–0013 and rebuild the J713 DTB. Keep the
original staged DTBs for comparison. Do not use the uncorrected input-enabled
`r4-storage-smc-mtp.dtb` or `r5-all.dtb` for further bring-up.

Run the regression check with the captured ADT and the actual compiled DTB.

```sh
python3 tests/check_mtp_reset.py air-adt.bin candidate.dtb
```

For maintainer development, run on a remote fleet host through its lease:

```sh
~/.local/bin/with-fleet-test-lock python3 tests/check_mtp_reset.py air-adt.bin candidate.dtb
```

The check reuses the repository's ADT/FDT parsers and derives the pin offsets
from the ADT keys. It checks both GPIO providers and both offsets, rather than
matching source text. It accepts zero padding in a proxy ADT capture.
Validation remains enabled under `python3 -O` / `PYTHONOPTIMIZE`. It does
not connect to the Air or write any GPIO.

Guard regressions use the same captured inputs and mutate only copied parser
objects. Run both normal and optimized Python. Maintainer development uses
the same remote fleet lease for both commands:

```sh
~/.local/bin/with-fleet-test-lock python3 tests/check_mtp_reset_guards.py air-adt.bin candidate.dtb
~/.local/bin/with-fleet-test-lock python3 -O tests/check_mtp_reset_guards.py air-adt.bin candidate.dtb
```

This is a pin-identity correction, not proof of working keyboard or trackpad.
The existing active-low polarity remains unchanged and needs hardware
confirmation. SMC, MTP firmware transport and HID initialization must still
work. The current bring-up initramfs has no `apple/tpmtfw-j713.bin`; the
multi-touch interface requests that firmware and cannot start without it.
The keyboard interface does not request this file. Preserve that distinction
when interpreting the input boot.

## Recorded verification, 2026-10-06

The check failed for both old pins on OptiPlex and passed after patch 0014.
Reverting either compiled pin independently also failed. All runs used the
fleet host lease. Rebuilding the unmodified DT source at
`c70dc9cfcb0ba6e8c2e5df3a635b68730b582dca` reproduced the preserved r4 DTB
byte for byte. The corrected build retains the baseline AIC `simple_bus_reg`
warning; no new compiler warning appeared.

Corrected `r4-mtp-reset-20261006.dtb` SHA-256:

```text
319859f88aec06bda503e83a99107dc83cb00a1e98feafca63b53cc5c28d577a
```

The corrected image has not yet been booted. Storage-only and SMC-only
isolation tests do not enable MTP and are unaffected by this correction.
