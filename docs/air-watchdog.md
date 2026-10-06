# J713 timed reset for RAM boot experiments

`tools/air_watchdog.py` can arm one hardware watchdog reset while the M4 Air
is in the m1n1 USB proxy. Its optional boot mode arms immediately before the
existing Linux loader hands control to the kernel. The timer does not depend
on Linux remaining responsive. The script never rearms automatically.

This is an explicitly authorized bench operation: a successful Linux boot
will also reset at the deadline if the kernel leaves the watchdog unchanged.
Use a diagnostic kernel that does not bind its Apple watchdog driver: that
driver can adopt an already-running WD1 timer, change its timeout, and keep
pinging it. Any kernel code that reprograms the watchdog invalidates the host
deadline. The observed RAM boots below did not bind that driver.
Use RAM-only diagnostic images without mounted
filesystems or disk-writing workloads. It cannot arm the timer retroactively
once the proxy has disappeared into a stalled kernel.

## Checked hardware

The implementation accepts only chip T8132, product J713AP, and the watchdog
whose translated ADT register range is `0x3882b0000` / `0x4000`, with compatible
`wdt,t8132`. USB vendor/product and the operator-supplied serial must match.
Select the primary proxy interface, not the hypervisor console interface.
Linux symlinks to an enumerated device are accepted. On macOS, select the
enumerated `/dev/cu.*` path; the distinct `/dev/tty.*` node is not an alias.

WD1 uses counter `+0x10`, alarm `+0x14` and control `+0x1c`. The existing
m1n1 `src/wdt.c` reset path uses these registers and control value 4. The
counter was measured near 24 MHz on the Air. Its CPU `CNTFRQ_EL0` is 1 GHz
and must not be used for the watchdog timeout calculation. The tool checks
counter progress before enabling the timer, accepts 10–120 seconds, requires
the watchdog initially disabled, and verifies alarm/control readback.
The counter sample spans one second, measured with the host's high-resolution
performance clock, to reduce the effect of USB response latency.

The watchdog mechanism is described in the
[Asahi watchdog documentation](https://asahilinux.org/docs/hw/soc/wdt/).
That page's M1 address is not the M4 address: use the translated live ADT.

## Commands

Use the existing proxyclient Python environment with its dependencies. No
files in the m1n1 checkout are modified. Inspection is the default:

```sh
python3 tools/air_watchdog.py --proxyclient /path/to/m1n1/proxyclient \
  --device /dev/ttyACM0 --serial YOUR_AIR_USB_SERIAL
```

Add `--arm --seconds 15` for a single timed reset in proxy mode. For one
watchdog-protected boot:

```sh
python3 tools/air_watchdog.py --proxyclient /path/to/m1n1/proxyclient \
  --device /dev/ttyACM0 --serial YOUR_AIR_USB_SERIAL --arm --seconds 60 \
  --kernel /path/to/Image.gz --dtb /path/to/control.dtb \
  --initramfs /path/to/initramfs.cpio.gz
```

The boot wrapper validates the target before loading and again at handoff.
It calls the preserved `tools/linux.py`, bypasses its interactive terminal,
and returns after handoff. It does not wait for or claim a successful Linux
boot. After USB returns, run inspection again to confirm the proxy responds
and the watchdog is disabled. Do not infer storage, input, or desktop success
from a reset or USB reconnect. An error after enabling may leave the timer
armed; do not blindly retry the command.

Host unit checks: `python3 tests/test_air_watchdog.py`. Maintainer development
checks run through the remote fleet lease. Tests use a fake register interface
and do not touch hardware. USB enumeration and the Linux host are exercised
on the bench; other host platforms need their own hardware validation.

## Observed on 2026-10-06

- A 15-second proxy-only watchdog arm reset the Air. USB disappeared and
  returned; a fresh proxy session responded with watchdog control 0.
- A 60-second watchdog survived direct Linux control-image handoff. USB
  returned and a fresh proxy again responded with control 0.
- The same 60-second arm recovered the storage-only boot configuration that
  previously stalled. USB returned after about 66 seconds from handoff, and
  inspection confirmed a responsive proxy with watchdog control 0.
- These observations establish automatic reset and return to the proxy.
  They do not capture the Linux screen or establish Linux boot success.

Kernel console capture remains separate work. The current direct-boot USB
proxy disappears during Linux execution, so automatic resetting alone does
not provide unattended kernel diagnostics.
