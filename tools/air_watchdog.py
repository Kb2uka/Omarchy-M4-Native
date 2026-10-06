#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""One-shot J713 watchdog experiment using an existing m1n1 proxy checkout."""

import argparse
import os
from pathlib import Path
import runpy
import sys
import time

BASE = 0x3882B0000
RATE = 24_000_000
COUNT, ALARM, CONTROL = 0x10, 0x14, 0x1C


def validate_target(chip, model, compatible, base, size):
    if (chip != 0x8132 or model != "J713AP" or "wdt,t8132" not in compatible
            or base != BASE or size != 0x4000):
        raise ValueError("target is not the validated J713/T8132 watchdog")


def validate_rate(delta, elapsed):
    if elapsed <= 0 or not 23_000_000 <= delta / elapsed <= 25_000_000:
        raise ValueError("watchdog counter does not match the 24 MHz reference")


def same_device(requested, enumerated):
    return requested == enumerated or Path(requested).resolve() == Path(enumerated).resolve()


def arm(proxy, seconds):
    if not isinstance(seconds, int) or not 10 <= seconds <= 120:
        raise ValueError("timeout must be 10..120 whole seconds")
    if proxy.read32(BASE + CONTROL) != 0:
        raise ValueError("watchdog already active or control state unexpected")
    ticks = RATE * seconds
    proxy.write32(BASE + ALARM, ticks)
    if proxy.read32(BASE + ALARM) != ticks:
        raise RuntimeError("watchdog alarm readback mismatch; not armed")
    proxy.write32(BASE + COUNT, 0)
    proxy.write32(BASE + CONTROL, 4)
    if proxy.read32(BASE + CONTROL) != 4:
        raise RuntimeError("watchdog enable readback mismatch; state uncertain")


def inspect(proxy, utils):
    node = utils.adt["/arm-io/wdt"]
    base, size = node.get_reg(0)
    chip = proxy.get_chipid()
    model = utils.adt["/product"].unique_model
    validate_target(chip, model, node.compatible, base, size)
    control = proxy.read32(base + CONTROL)
    if control != 0:
        raise ValueError("watchdog is not disabled; refusing to alter it")
    started = time.perf_counter()
    first = proxy.read32(base + COUNT)
    time.sleep(1.0)
    last = proxy.read32(base + COUNT)
    elapsed = time.perf_counter() - started
    delta = (last - first) & 0xFFFFFFFF
    validate_rate(delta, elapsed)
    print(f"target={model} chip={chip:#x} watchdog={base:#x} "
          f"control={control} measured_hz={delta / elapsed:.0f}", flush=True)


def boot_once(proxyclient, kernel, dtb, initramfs, bootargs, seconds):
    """Arm at the last host-side handoff without modifying the bootloader tree."""
    from m1n1.proxy import M1N1Proxy, UartInterface
    original_boot = M1N1Proxy.kboot_boot
    original_tty = UartInterface.ttymode
    original_argv = sys.argv
    handed_off = False

    def guarded_boot(proxy, address):
        nonlocal handed_off
        if handed_off:
            raise RuntimeError("only one boot handoff is allowed")
        setup = sys.modules["m1n1.setup"]
        inspect(proxy, setup.u)
        arm(proxy, seconds)
        handed_off = True
        print(f"ARMED before Linux handoff: {seconds}s; no automatic rearm", flush=True)
        return original_boot(proxy, address)

    try:
        M1N1Proxy.kboot_boot = guarded_boot
        UartInterface.ttymode = lambda self, dev=None: None
        sys.argv = [str(proxyclient / "tools/linux.py"), "-b", bootargs,
                    str(kernel), str(dtb), str(initramfs)]
        runpy.run_path(sys.argv[0], run_name="__main__")
        if not handed_off:
            raise RuntimeError("loader exited without a watchdog-protected handoff")
    finally:
        M1N1Proxy.kboot_boot = original_boot
        UartInterface.ttymode = original_tty
        sys.argv = original_argv
        setup = sys.modules.get("m1n1.setup")
        device = getattr(getattr(setup, "iface", None), "dev", None)
        if device is not None:
            device.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proxyclient", type=Path, required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--serial", required=True, help="expected USB serial")
    parser.add_argument("--seconds", type=int, default=15)
    parser.add_argument("--arm", action="store_true", help="authorize one timed hardware reset")
    parser.add_argument("--kernel", type=Path, help="optional Linux image for one protected boot")
    parser.add_argument("--dtb", type=Path)
    parser.add_argument("--initramfs", type=Path)
    parser.add_argument("--bootargs", default="nosmp idle=nop panic=0 console=tty0 fbcon=nodefer loglevel=7")
    args = parser.parse_args()
    if not 10 <= args.seconds <= 120:
        parser.error("--seconds must be 10..120")
    proxyclient = args.proxyclient.resolve()
    if not (proxyclient / "m1n1/proxy.py").is_file():
        parser.error("--proxyclient must name the existing proxyclient directory")
    if args.kernel or args.dtb or args.initramfs:
        if not args.arm or not all((args.kernel, args.dtb, args.initramfs)):
            parser.error("boot requires --arm, --kernel, --dtb and --initramfs")
        for path in (args.kernel, args.dtb, args.initramfs):
            if not path.is_file():
                parser.error(f"missing boot input: {path}")
    from serial.tools import list_ports
    ports = [p for p in list_ports.comports() if same_device(args.device, p.device)]
    if len(ports) != 1:
        parser.error("specified USB proxy device not found")
    port = ports[0]
    if (port.vid, port.pid, port.serial_number) != (0x1209, 0x316D, args.serial):
        parser.error("USB identity mismatch")
    os.environ["M1N1DEVICE"] = port.device
    sys.path.insert(0, str(proxyclient))
    from m1n1.proxy import UartInterface, M1N1Proxy
    from m1n1.proxyutils import ProxyUtils, bootstrap_port
    iface = UartInterface()
    try:
        proxy = M1N1Proxy(iface)
        bootstrap_port(iface, proxy)
        inspect(proxy, ProxyUtils(proxy))
        if args.kernel:
            iface.dev.close()
            boot_once(proxyclient, args.kernel.resolve(), args.dtb.resolve(),
                      args.initramfs.resolve(), args.bootargs, args.seconds)
        elif args.arm:
            arm(proxy, args.seconds)
            print(f"ARMED: one hardware reset in approximately {args.seconds}s; "
                  "no automatic rearm", flush=True)
        else:
            print("INSPECT ONLY: no watchdog writes", flush=True)
    finally:
        iface.dev.close()


if __name__ == "__main__":
    main()
