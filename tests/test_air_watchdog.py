# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
import contextlib
import io
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import sys
import tempfile

spec = importlib.util.spec_from_file_location("air_watchdog", Path(__file__).resolve().parents[1] / "tools/air_watchdog.py")
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)


class Proxy:
    def __init__(self, control=0, bad_alarm=False):
        self.regs = {w.BASE + w.CONTROL: control}
        self.writes = []
        self.bad_alarm = bad_alarm

    def read32(self, address):
        if self.bad_alarm and address == w.BASE + w.ALARM:
            return 0
        return self.regs.get(address, 0)

    def write32(self, address, value):
        self.writes.append((address, value))
        self.regs[address] = value


class WatchdogTests(unittest.TestCase):
    def test_arm_sequence_and_limits(self):
        for seconds in (10, 15, 120):
            p = Proxy()
            w.arm(p, seconds)
            self.assertEqual(p.writes, [(w.BASE + 0x14, seconds * 24_000_000),
                                        (w.BASE + 0x10, 0), (w.BASE + 0x1C, 4)])

    def test_invalid_timeout_never_writes(self):
        for seconds in (0, 9, 121, 180, 1.5):
            p = Proxy()
            with self.assertRaises(ValueError):
                w.arm(p, seconds)
            self.assertEqual(p.writes, [])

    def test_inspection_uses_adt_identity_fields(self):
        for model, compatible, base, size in (
                ("J773AP", ["wdt,t8132"], w.BASE, 0x4000),
                ("J713AP", ["wdt,t8103"], w.BASE, 0x4000),
                ("J713AP", ["wdt,t8132"], 0x23D2B0000, 0x4000),
                ("J713AP", ["wdt,t8132"], w.BASE, 4)):
            with self.subTest(model=model, compatible=compatible, base=base, size=size):
                node = SimpleNamespace(get_reg=lambda index: (base, size), compatible=compatible)
                utils = SimpleNamespace(adt={"/arm-io/wdt": node,
                                             "/product": SimpleNamespace(unique_model=model)})
                p = Proxy()
                p.get_chipid = lambda: 0x8132
                with patch.object(p, "read32", side_effect=AssertionError("read before target validation")):
                    with self.assertRaises(ValueError):
                        w.inspect(p, utils)
                self.assertEqual(p.writes, [])

    def test_active_control_never_writes(self):
        for control in (1, 4, 5):
            p = Proxy(control)
            with self.assertRaises(ValueError):
                w.arm(p, 15)
            self.assertEqual(p.writes, [])

    def test_alarm_readback_prevents_enable(self):
        p = Proxy(bad_alarm=True)
        with self.assertRaises(RuntimeError):
            w.arm(p, 15)
        self.assertEqual(p.writes, [(w.BASE + w.ALARM, 360_000_000)])

    def test_enable_readback_failure_is_reported(self):
        p = Proxy()
        with patch.object(p, "read32", side_effect=[0, 360_000_000, 0]):
            with self.assertRaisesRegex(RuntimeError, "state uncertain"):
                w.arm(p, 15)
        self.assertEqual(p.writes[-1], (w.BASE + w.CONTROL, 4))

    def test_identity_must_match_every_field(self):
        valid = [0x8132, "J713AP", ["wdt,t8132"], w.BASE, 0x4000]
        w.validate_target(*valid)
        for index, bad in enumerate((0x8103, "J773AP", ["wdt,t8103"], 0x23D2B0000, 4)):
            args = valid.copy()
            args[index] = bad
            with self.assertRaises(ValueError):
                w.validate_target(*args)

    def test_counter_rate_not_cpu_timer(self):
        w.validate_rate(4_800_000, .2)
        for delta, elapsed in ((200_000_000, .2), (0, .2), (4_800_000, 0)):
            with self.assertRaises(ValueError):
                w.validate_rate(delta, elapsed)

    def test_inspection_checks_target_clock_and_control(self):
        node = SimpleNamespace(get_reg=lambda index: (w.BASE, 0x4000), compatible=["wdt,t8132"])
        utils = SimpleNamespace(adt={"/arm-io/wdt": node, "/product": SimpleNamespace(unique_model="J713AP")})
        for chip, control, delta, valid in ((0x8132, 0, 24_000_000, True),
                                          (0x8103, 0, 24_000_000, False),
                                          (0x8132, 4, 24_000_000, False),
                                          (0x8132, 0, 1_000_000_000, False)):
            p = Proxy(control)
            p.get_chipid = lambda: chip
            with patch.object(p, "read32", side_effect=[control, 0, delta]), \
                    patch.object(w.time, "perf_counter", side_effect=[0, 1.01]), \
                    patch.object(w.time, "sleep") as sleep:
                if valid:
                    w.inspect(p, utils)
                    sleep.assert_called_once_with(1.0)
                else:
                    with self.assertRaises(ValueError):
                        w.inspect(p, utils)
            self.assertEqual(p.writes, [])

    def test_usb_device_alias_resolves_to_enumerated_port(self):
        with tempfile.TemporaryDirectory() as tmp:
            device = Path(tmp) / "ttyACM0"
            device.touch()
            alias = Path(tmp) / "proxy"
            try:
                alias.symlink_to(device.name)
            except OSError as error:
                self.skipTest(f"host does not permit symlink creation: {error}")
            self.assertTrue(w.same_device(str(device), str(alias)))
            self.assertTrue(w.same_device(str(alias), str(device)))
            self.assertFalse(w.same_device(str(device), str(Path(tmp) / "other")))

    def test_cli_boot_without_arm_rejects_before_usb(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "m1n1").mkdir()
            (root / "m1n1/proxy.py").touch()
            for name in ("kernel", "dtb", "initrd"):
                (root / name).touch()
            argv = ["air_watchdog.py", "--proxyclient", tmp, "--device", "/dev/fake",
                    "--serial", "expected", "--kernel", str(root / "kernel"),
                    "--dtb", str(root / "dtb"), "--initramfs", str(root / "initrd")]
            ports = SimpleNamespace(comports=lambda: self.fail("USB enumerated before authorization guard"))
            modules = {"serial": SimpleNamespace(), "serial.tools": SimpleNamespace(list_ports=ports)}
            with patch.dict(sys.modules, modules), patch.object(sys, "argv", argv), patch.object(w, "arm") as arm:
                with self.assertRaises(SystemExit) as error:
                    w.main()
                self.assertEqual(error.exception.code, 2)
                arm.assert_not_called()

    def test_cli_usb_identity_rejects_before_proxy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "m1n1").mkdir()
            (root / "m1n1/proxy.py").touch()
            argv = ["air_watchdog.py", "--proxyclient", tmp, "--device", "/dev/fake", "--serial", "expected", "--arm"]
            port = SimpleNamespace(device="/dev/fake", vid=0x1209, pid=0x316D, serial_number="expected")
            ports = SimpleNamespace(comports=lambda: [port])
            modules = {"serial": SimpleNamespace(), "serial.tools": SimpleNamespace(list_ports=ports),
                       "m1n1": SimpleNamespace(),
                       "m1n1.proxy": SimpleNamespace(UartInterface=lambda: self.fail("proxy opened before USB validation"),
                                                     M1N1Proxy=Proxy),
                       "m1n1.proxyutils": SimpleNamespace(ProxyUtils=object, bootstrap_port=lambda *args: None)}
            for field, bad in (("vid", 0), ("pid", 0), ("serial_number", "wrong")):
                with self.subTest(field=field), patch.object(port, field, bad), \
                        patch.dict(sys.modules, modules), patch.dict(w.os.environ), \
                        patch.object(sys, "path", sys.path.copy()), \
                        patch.object(sys, "argv", argv), patch.object(w, "arm") as arm:
                    with self.assertRaises(SystemExit) as error:
                        w.main()
                    self.assertEqual(error.exception.code, 2)
                    arm.assert_not_called()

    def test_cli_missing_device_rejects_before_proxy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "m1n1").mkdir()
            (root / "m1n1/proxy.py").touch()
            argv = ["air_watchdog.py", "--proxyclient", tmp, "--device", "/dev/missing",
                    "--serial", "expected", "--arm"]
            port = SimpleNamespace(device="/dev/other", vid=0x1209, pid=0x316D,
                                   serial_number="expected")
            modules = {"serial": SimpleNamespace(),
                       "serial.tools": SimpleNamespace(list_ports=SimpleNamespace(comports=lambda: [port])),
                       "m1n1": SimpleNamespace(),
                       "m1n1.proxy": SimpleNamespace(UartInterface=lambda: self.fail("unverified proxy opened"))}
            stderr = io.StringIO()
            with patch.dict(sys.modules, modules), patch.object(sys, "argv", argv), \
                    patch.object(w, "arm") as arm, contextlib.redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as error:
                    w.main()
                self.assertEqual(error.exception.code, 2)
                self.assertIn("specified USB proxy device not found", stderr.getvalue())
                arm.assert_not_called()

    def test_cli_default_inspects_and_arm_follows_inspection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "m1n1").mkdir()
            (root / "m1n1/proxy.py").touch()
            device = root / "ttyACM0"
            device.touch()
            requested_devices = [str(device)]
            alias = root / ("proxy" if w.os.name == "nt" else "pci-0000:00:14.0-usb-0:2:1.0")
            try:
                alias.symlink_to(device.name)
            except OSError:
                pass
            else:
                requested_devices.append(str(alias))
            argv = ["air_watchdog.py", "--proxyclient", tmp,
                    "--serial", "expected", "--seconds", "37"]
            port = SimpleNamespace(device=str(device), vid=0x1209, pid=0x316D, serial_number="expected")
            second_port = SimpleNamespace(device=str(root / "ttyACM1"), vid=0x1209,
                                          pid=0x316D, serial_number="expected")
            for requested_device in requested_devices:
                for armed, inspection_error in ((False, False), (True, False), (True, True)):
                    events = []
                    iface = SimpleNamespace(dev=SimpleNamespace(close=lambda: events.append("closed")))
                    proxy = Proxy()
                    utils = object()
                    def open_interface():
                        self.assertEqual(w.os.environ["M1N1DEVICE"], str(device))
                        return iface
                    modules = {
                        "serial": SimpleNamespace(),
                        "serial.tools": SimpleNamespace(list_ports=SimpleNamespace(comports=lambda: [second_port, port])),
                        "m1n1": SimpleNamespace(),
                        "m1n1.proxy": SimpleNamespace(UartInterface=open_interface, M1N1Proxy=lambda arg: proxy),
                        "m1n1.proxyutils": SimpleNamespace(ProxyUtils=lambda arg: utils,
                                                           bootstrap_port=lambda *args: events.append("bootstrap")),
                    }
                    def inspection(actual_proxy, actual_utils):
                        self.assertIs(actual_proxy, proxy)
                        self.assertIs(actual_utils, utils)
                        events.append("inspect")
                        if inspection_error:
                            raise ValueError("bad target")
                    def armed_call(actual_proxy, seconds):
                        self.assertIs(actual_proxy, proxy)
                        self.assertEqual(seconds, 37)
                        events.append("arm")
                    with patch.dict(sys.modules, modules), patch.dict(w.os.environ), \
                            patch.object(sys, "path", sys.path.copy()), \
                            patch.object(sys, "argv", argv + ["--device", requested_device] + (["--arm"] if armed else [])), \
                            patch.object(w, "inspect", inspection), \
                            patch.object(w, "arm", side_effect=armed_call):
                        if inspection_error:
                            with self.assertRaisesRegex(ValueError, "bad target"):
                                w.main()
                        else:
                            w.main()
                    expected = ["bootstrap", "inspect"]
                    if armed and not inspection_error:
                        expected.append("arm")
                    self.assertEqual(events, expected + ["closed"])

    def test_boot_arms_only_at_handoff_and_restores_hooks(self):
        events = []
        class BootProxy(Proxy):
            def kboot_boot(self, address):
                events.append(("boot", address, self.regs[w.BASE + w.CONTROL]))
        class Interface:
            def ttymode(self, dev=None):
                raise AssertionError("interactive terminal must not run")
        original = BootProxy.kboot_boot
        original_tty = Interface.ttymode
        original_argv = sys.argv
        p = BootProxy()
        setup = SimpleNamespace(u=object(), iface=SimpleNamespace(dev=SimpleNamespace(close=lambda: events.append("closed"))))
        modules = {"m1n1": SimpleNamespace(), "m1n1.proxy": SimpleNamespace(M1N1Proxy=BootProxy, UartInterface=Interface), "m1n1.setup": setup}
        def loader(path, run_name):
            self.assertEqual(p.writes, [])
            p.kboot_boot(0x10000)
            Interface().ttymode()
            with self.assertRaises(RuntimeError):
                p.kboot_boot(0x10000)
        def inspection(proxy, utils):
            self.assertEqual(proxy.writes, [])
            events.append("inspect")
        with patch.dict(sys.modules, modules), patch.object(w.runpy, "run_path", loader), patch.object(w, "inspect", side_effect=inspection) as inspect:
            w.boot_once(Path("/proxy"), Path("kernel"), Path("dtb"), Path("initrd"), "panic=0", 60)
            inspect.assert_called_once_with(p, setup.u)
        self.assertEqual(events, ["inspect", ("boot", 0x10000, 4), "closed"])
        self.assertIs(BootProxy.kboot_boot, original)
        self.assertIs(Interface.ttymode, original_tty)
        self.assertIs(sys.argv, original_argv)

    def test_loader_failure_before_handoff_does_not_arm(self):
        class BootProxy(Proxy):
            def kboot_boot(self, address):
                raise AssertionError("must not boot")
        class Interface:
            def ttymode(self, dev=None):
                pass
        modules = {"m1n1": SimpleNamespace(), "m1n1.proxy": SimpleNamespace(M1N1Proxy=BootProxy, UartInterface=Interface)}
        original_boot = BootProxy.kboot_boot
        original_tty = Interface.ttymode
        original_argv = sys.argv
        with patch.dict(sys.modules, modules), patch.object(w.runpy, "run_path", side_effect=ValueError("bad image")), patch.object(w, "arm") as arm:
            with self.assertRaises(ValueError):
                w.boot_once(Path("/proxy"), Path("kernel"), Path("dtb"), Path("initrd"), "panic=0", 60)
            arm.assert_not_called()
        self.assertIs(BootProxy.kboot_boot, original_boot)
        self.assertIs(Interface.ttymode, original_tty)
        self.assertIs(sys.argv, original_argv)

    def test_loader_without_handoff_or_partial_setup_never_arms(self):
        class BootProxy(Proxy):
            def kboot_boot(self, address):
                raise AssertionError("must not boot")
        class Interface:
            def ttymode(self, dev=None):
                pass
        modules = {"m1n1": SimpleNamespace(), "m1n1.proxy": SimpleNamespace(M1N1Proxy=BootProxy, UartInterface=Interface),
                   "m1n1.setup": SimpleNamespace()}
        for loader_error in (None, ValueError("partial setup failed")):
            with patch.dict(sys.modules, modules), patch.object(w.runpy, "run_path", side_effect=loader_error), \
                    patch.object(w, "arm") as arm:
                expected = ValueError if loader_error else RuntimeError
                message = "partial setup failed" if loader_error else "without a watchdog-protected handoff"
                with self.assertRaisesRegex(expected, message):
                    w.boot_once(Path("/proxy"), Path("kernel"), Path("dtb"), Path("initrd"), "panic=0", 60)
                arm.assert_not_called()


if __name__ == "__main__":
    unittest.main()
