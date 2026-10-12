"""Compile the actual startup functions against deterministic host callbacks."""
from pathlib import Path
import argparse
import subprocess
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--before", type=Path, required=True)
parser.add_argument("--after", type=Path, required=True)
args = parser.parse_args()
fixture = Path(__file__).with_name("dockchannel_retry_fixture.c").read_text()


def between(source, start, end):
    first = source.index(start)
    return source[first:source.index(end, first)]


def extract(source):
    constants = "\n".join(line for line in source.splitlines() if line.startswith(("#define START_TIMEOUT_MS ", "#define J713_START_ATTEMPTS ")))
    types = between(source, "struct fw_header {", "\nstruct dchid_work")
    types += between(source, "struct dchid_iface {", "\nstruct dockchannel_hid {")
    functions = between(source, "static struct dchid_iface *\ndchid_get_interface(", "\nstatic u32 dchid_checksum")
    functions += between(source, "static int dchid_reset_interface(", "\nstatic int dchid_request_gpio")
    functions += between(source, "static int dchid_start_interface(", "\nstatic int dchid_start(")
    opening = "static int dchid_open_retry(" if "static int dchid_open_retry(" in source else "static int dchid_open("
    functions += between(source, opening, "\nstatic void dchid_close(")
    return fixture.replace("/* ACTUAL_CONSTANTS */", constants).replace("/* ACTUAL_TYPES */", types).replace("/* ACTUAL_FUNCTIONS */", functions)


before, after = args.before.read_text(), args.after.read_text()
# Each mutation changes real extracted source; compilation failures do not count.
mutations = {
    "timeout_latch": ("\t\tiface->starting = false;\n\t\tret = -ETIMEDOUT;", "\t\tret = -ETIMEDOUT;", "first_timeout"),
    "terminal_latch": ("done:\n\tiface->starting = false;", "done:\n\t/* omitted */", "normal_ready"),
    "optin_guard": ("return m4_power_v2 &&", "return true &&", "guards"),
    "board_guard": ('of_machine_is_compatible("apple,j713") &&', "true &&", "guards"),
    "chip_guard": ('of_machine_is_compatible("apple,t8132") &&', "true &&", "guards"),
    "interface_guard": ('!strcmp(iface->name, "multi-touch");', "iface != NULL;", "guards"),
    "extra_attempt": ("#define J713_START_ATTEMPTS 3U", "#define J713_START_ATTEMPTS 4U", "all_timeout"),
    "unbounded_retry": ("attempt < J713_START_ATTEMPTS", "true", "all_timeout"),
    "late_ready": ("if (ret == -ETIMEDOUT && completion_done(&iface->ready))", "if (false)", "late_ready"),
    "retry_command_error": ("\t\tif (ret < 0)\n\t\t\tgoto done;", "\t\tif (ret < 0) {\n\t\t\tif (attempt + 1 == J713_START_ATTEMPTS) goto done;\n\t\t\tcontinue;\n\t\t}", "errors"),
    "ignore_command_error": ("\t\tif (ret < 0)\n\t\t\tgoto done;", "\t\tif (ret < 0) {\n\t\t\tif (false) goto done;\n\t\t\tbreak;\n\t\t}", "errors"),
    "firmware_cache": ("if (dchid_start_retry_enabled(iface) && iface->startup_firmware)", "if (false)", "all_timeout"),
    "dma_cache": ("if (retry && iface->startup_dma)", "if (false)", "all_timeout"),
    "mutex_init": ("\tmutex_init(&iface->start_mutex);", "", "normal_ready"),
    "serialization": ("\tmutex_lock(&iface->start_mutex);", "\t/* omitted lock */", "serialized"),
}
cases = ("first_timeout", "all_timeout", "normal_ready", "errors", "late_ready", "guards", "serialized")
with tempfile.TemporaryDirectory(prefix="dchid-retry-") as temp:
    directory = Path(temp)

    def run(name, source, selected, success):
        cfile, executable = directory / (name + ".c"), directory / name
        cfile.write_text(extract(source))
        subprocess.run(["cc", "-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror", "-pthread", str(cfile), "-o", str(executable)], check=True)
        for case in selected:
            result = subprocess.run([str(executable), case], capture_output=True, text=True, timeout=10)
            print(name, case, "rc", result.returncode, result.stdout.strip(), result.stderr.strip(), flush=True)
            if success:
                assert result.returncode == 0, (name, case)
            else:
                assert result.returncode == -6 and "Assertion" in result.stderr, (name, case, "must fail an assertion")

    run("before", before, ("first_timeout", "all_timeout"), False)
    run("after", after, cases, True)
    for name, (old, new, case) in mutations.items():
        # Other opt-in helpers can contain the same board predicates. Mutate
        # the actual retry guard, not an unrelated helper with matching text.
        if name in {"board_guard", "chip_guard", "interface_guard"}:
            start = after.index("static bool dchid_start_retry_enabled(")
            end = after.index("\nstatic int dchid_send_firmware", start)
            body = after[start:end]
            assert body.count(old) == 1, (name, "non-unique guard mutation")
            mutated = after[:start] + body.replace(old, new) + after[end:]
        else:
            assert after.count(old) == 1, (name, "non-unique mutation")
            mutated = after.replace(old, new)
        # A removed lock must not fail by unlocking an unowned pthread mutex.
        if name == "serialization":
            mutated = mutated.replace("\tmutex_unlock(&iface->start_mutex);", "\t/* omitted unlock */")
        run(name, mutated, (case,), False)
    run("restored", after, cases, True)
print("PASS: actual-source cases, named mutants, restored cases; temporary build files removed.")
