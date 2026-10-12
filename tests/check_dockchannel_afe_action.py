"""Exercise actual DockChannel AFE action functions on a leased test host.

Pass complete pre-patch and post-patch dockchannel-hid.c files. The pre-patch
reset must compile and fail the sequence assertion; compile errors are failures
of this checker. SMC/GPIO/DT/transport callbacks are shims, not hardware proof.
"""
from pathlib import Path
import argparse
import hashlib
import re
import resource
import signal
import subprocess
import tempfile


CASES = ("default", "sequence", "startup", "live-parent", "guards", "preflight", "metadata",
         "failures", "ownership", "callback", "framing", "serialized")
HELPERS = ("dchid_afe_action_enabled", "dchid_put_afe_smc", "dchid_prepare_afe",
           "dchid_afe_write", "dchid_afe_action")
FUNCTIONS = ("dchid_reset_interface", "dchid_request_gpio",
             "dchid_start_interface", "dchid_handle_gpio")
TYPES = ("dchid_stm_id", "dchid_iface", "dockchannel_hid",
         "dchid_gpio_cmd", "dchid_gpio_ack")


def function(source, name):
    match = re.search(r"^static (?:bool|int|void) " + re.escape(name) +
                      r"\([^)]*\)\n\{.*?^\}\n", source, re.MULTILINE | re.DOTALL)
    assert match, f"missing actual function {name}"
    return match.group()


def struct(source, name):
    match = re.search(r"^struct " + re.escape(name) + r" \{.*?^\}[^;\n]*;",
                      source, re.MULTILINE | re.DOTALL)
    assert match, f"missing actual type {name}"
    return match.group()


def unit(source, fixture):
    has_afe = "static bool m4_afe_action" in source
    parameters = []
    for name in ("m4_power_v2", "m4_afe_action"):
        match = re.search(r"^static bool " + name + r"[^;]*;", source, re.MULTILINE)
        parameters.append(match.group() if match else "static bool m4_afe_action;")
    names = (*HELPERS, *FUNCTIONS) if has_afe else FUNCTIONS
    return (fixture.replace("/* PARAMETERS */", f"#define HAVE_AFE_ACTION {int(has_afe)}\n" +
                            "\n".join(parameters))
            .replace("/* ACTUAL_TYPES */", "\n\n".join(struct(source, name) for name in TYPES))
            .replace("/* ACTUAL_FUNCTIONS */", "\n\n".join(function(source, name) for name in names)))


def compile_unit(source, fixture, directory, label, smc_reachable=True):
    cfile, executable = directory / f"{label}.c", directory / label
    cfile.write_text(unit(source, fixture))
    subprocess.run(["cc", "-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror", "-pthread",
                    f"-DTEST_SMC_REACHABLE={int(smc_reachable)}",
                    str(cfile), "-o", str(executable)], check=True)
    return executable


def run(executable, case, fails=False):
    result = subprocess.run([str(executable), case], capture_output=True, text=True, timeout=10)
    if fails:
        assert result.returncode == -signal.SIGABRT and "Assertion" in result.stderr, \
            f"{executable.name}/{case}: expected behavioral assertion; got {result.returncode}: {result.stderr}"
        print(f"KILLED {executable.name}/{case}: {result.stderr.strip()}", flush=True)
    else:
        assert result.returncode == 0, f"{executable.name}/{case}: {result.stderr}"
        print(f"PASS {executable.name}/{case}", flush=True)


def replace_in(source, name, old, new):
    actual = function(source, name)
    assert actual.count(old) == 1, f"mutation anchor must be unique in {name}: {old!r}"
    return source.replace(actual, actual.replace(old, new), 1)


def mutants(source):
    enabled = "dchid_afe_action_enabled"
    for label, old in (("opt-in", "m4_afe_action && "), ("v2-guard", "m4_power_v2 &&"),
                       ("board-guard", 'of_machine_is_compatible("apple,j713") &&'),
                       ("chip-guard", 'of_machine_is_compatible("apple,t8132") &&')):
        yield label, "guards", replace_in(source, enabled, old, "")
    yield "interface-guard", "guards", replace_in(source, enabled,
                         '!strcmp(iface->name, "multi-touch")', "((void)iface, true)")
    yield "missing-host", "sequence", replace_in(source, "dchid_reset_interface",
                    "host_ret = dchid_afe_action(iface, state);", "host_ret = 0;")
    late_host = replace_in(source, "dchid_reset_interface",
                    "host_ret = dchid_afe_action(iface, state);", "host_ret = 0;")
    yield "host-after-has", "sequence", replace_in(late_host, "dchid_reset_interface",
                    "request.phase = 1;\n\tret = dchid_comm_cmd(iface->dchid, &request, sizeof(request));",
                    "request.phase = 1;\n\tret = dchid_comm_cmd(iface->dchid, &request, sizeof(request));"
                    "\n\thost_ret = dchid_afe_action(iface, state);")
    yield "managed-link-delete", "preflight", replace_in(source, "dchid_prepare_afe",
                    "if (ret)\n\t\t\tgoto out;\n\t\tdchid->afe_smc = smc;",
                    "if (ret) {\n\t\t\tdevice_link_del(link);\n\t\t\tgoto out;\n\t\t}\n\t\tdchid->afe_smc = smc;")
    changes = (
        ("argument-direction", "sequence", "dchid_afe_action", "state == 0);", "state == 2);"),
        ("output-command", "sequence", "dchid_afe_write", "0x00010000 | argument", "0x01000000 | argument"),
        ("wrong-smc-key", "sequence", "dchid_afe_write", "SMC_KEY(gP1c)", "SMC_KEY(gp1c)"),
        ("wrong-size", "sequence", "dchid_afe_write", "&payload, sizeof(payload)", "&payload, 3"),
        ("off-delay", "sequence", "dchid_afe_write", "msleep(50);", "msleep(49);"),
        ("on-delay", "sequence", "dchid_afe_write", "argument == 1", "argument == 0"),
        ("positive-return", "sequence", "dchid_afe_write", "if (ret < 0)", "if (ret)"),
        ("argument-bound", "sequence", "dchid_afe_write", " || argument > 1", ""),
        ("missing-smc", "sequence", "dchid_afe_write", "!dchid->afe_smc || ", ""),
        ("readiness", "metadata", "dchid_afe_write", "!dchid->afe_ready || ", ""),
        ("false-success-status", "failures", "dchid_reset_interface",
         "cpu_to_le32(host_ret < 0 ? 0xe00002bc : 0)", "cpu_to_le32(0)"),
        ("lost-host-errno", "failures", "dchid_reset_interface", "return host_ret < 0 ? host_ret : ret;", "return ret;"),
        ("ignored-will-error", "failures", "dchid_reset_interface",
         "if (ret < 0)\n\t\treturn ret;", "if (ret < 0)\n\t\tret = 0;"),
        ("no-preflight", "preflight", "dchid_start_interface", "ret = dchid_prepare_afe(iface);", "ret = 0;"),
        ("early-output", "startup", "dchid_prepare_afe", "GPIOD_ASIS", "GPIOD_OUT_LOW"),
        ("cells", "preflight", "dchid_prepare_afe", "args.args_count == 2 &&", ""),
        ("pin", "preflight", "dchid_prepare_afe", "args.args[0] == 28 &&", ""),
        ("dt-flags", "preflight", "dchid_prepare_afe", "args.args[1] == 1 &&", ""),
        ("provider", "preflight", "dchid_prepare_afe", 'of_device_is_compatible(args.np, "apple,smc-gpio")', "true"),
        ("parent", "preflight", "dchid_prepare_afe", ' || !of_device_is_compatible(smc_node, "apple,t8132-smc")', ""),
        ("parent-compatible", "live-parent", "dchid_prepare_afe", '"apple,t8132-smc"', '"apple,smc"'),
        ("key-size", "metadata", "dchid_prepare_afe", "info.size != sizeof(__le32) || ", ""),
        ("key-writable", "metadata", "dchid_prepare_afe", " || !(info.flags & APPLE_SMC_WRITABLE)", ""),
        ("metadata-errno", "metadata", "dchid_prepare_afe", "if (ret < 0)\n\t\tgoto out;", "if (false)\n\t\tgoto out;"),
        ("prepare-config", "config", "dchid_prepare_afe", "if (!IS_REACHABLE(CONFIG_MFD_MACSMC))", "if (false)"),
        ("write-config", "config", "dchid_afe_write", "if (!IS_REACHABLE(CONFIG_MFD_MACSMC))", "if (false)"),
        ("cleanup-reference", "ownership", "dchid_put_afe_smc", "put_device(data);", "(void)data;"),
        ("ready-cache", "ownership", "dchid_prepare_afe", "if (dchid->afe_ready)", "if (false)"),
        ("late-name", "ownership", "dchid_request_gpio", 'if (strcmp(iface->gpio_name, "afe-reset"))', "if (false)"),
        ("callback-error", "callback", "dchid_handle_gpio", "retcode = ret < 0 ? 0xe00002bc : 0;", "retcode = 0;"),
        ("callback-continues", "callback", "dchid_handle_gpio", "if (!ret)", "if (true)"),
        ("callback-delay", "callback", "dchid_afe_write", "msleep(50);", "msleep(10);"),
        ("callback-minimum", "framing", "dchid_handle_gpio", "length < 20", "length < 5"),
        ("callback-id-width", "framing", "dchid_handle_gpio", "get_unaligned_le16((u8 *)data + 2)", "cmd->gpio"),
        ("callback-command-width", "framing", "dchid_handle_gpio", "get_unaligned_le32((u8 *)data + 4)", "cmd->cmd"),
        ("legacy-id-check", "framing", "dchid_handle_gpio",
         "(!dchid_afe_action_enabled(iface) &&\n\t\t\t     cmd->gpio != iface->gpio_id)", "cmd->gpio != iface->gpio_id"),
    )
    for label, case, name, old, new in changes:
        yield label, case, replace_in(source, name, old, new)
    for label, name, case in (("prepare-lock", "dchid_prepare_afe", "serialized"),
                               ("callback-lock", "dchid_handle_gpio", "callback"),
                               ("host-lock", "dchid_afe_action", "sequence")):
        unlocked = replace_in(source, name, "mutex_lock(&dchid->afe_mutex);", "")
        yield label, case, replace_in(unlocked, name, "mutex_unlock(&dchid->afe_mutex);", "")
    assert source.count("static bool m4_afe_action;") == 1
    yield "default-enabled", "default", source.replace("static bool m4_afe_action;",
                                                       "static bool m4_afe_action = true;", 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path)
    parser.add_argument("--skip-mutants", action="store_true")
    args = parser.parse_args()
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    fixture = Path(__file__).with_name("dockchannel_afe_action_fixture.c").read_text()
    before = args.before.read_text()
    print("before sha256", hashlib.sha256(args.before.read_bytes()).hexdigest(), flush=True)
    with tempfile.TemporaryDirectory(prefix="dchid-afe-") as temp:
        directory = Path(temp)
        executable = compile_unit(before, fixture, directory, "before")
        run(executable, "sequence", fails=True)
        if args.after:
            after = args.after.read_text()
            print("after sha256", hashlib.sha256(args.after.read_bytes()).hexdigest(), flush=True)
            assert "module_param(m4_afe_action, bool, 0444);" in after
            probe = function(after, "dockchannel_hid_probe")
            assert probe.index("mutex_init(&dchid->afe_mutex);") < probe.index("dockchannel_init(pdev)")
            print("PASS source contract: read-only option; mutex initialized before transport", flush=True)
            executable = compile_unit(after, fixture, directory, "after")
            for case in CASES:
                run(executable, case)
            unavailable = compile_unit(after, fixture, directory, "smc-unreachable", smc_reachable=False)
            run(unavailable, "config")
            if not args.skip_mutants:
                count = 0
                for label, case, mutated in mutants(after):
                    executable = compile_unit(mutated, fixture, directory, label, smc_reachable=case != "config")
                    run(executable, case, fails=True)
                    count += 1
                print(f"PASS {count} behavioral mutants rejected", flush=True)
                executable = compile_unit(after, fixture, directory, "restored")
                for case in CASES:
                    run(executable, case)
    print("PASS: temporary fixture sources and executables removed.", flush=True)


if __name__ == "__main__":
    main()
