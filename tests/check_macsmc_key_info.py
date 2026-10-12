"""Compile actual SMC metadata code with a deterministic competing SRAM writer."""
from pathlib import Path
import argparse
import subprocess
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--before", type=Path, required=True)
parser.add_argument("--after", type=Path, required=True)
args = parser.parse_args()
fixture = Path(__file__).with_name("macsmc_key_info_fixture.c").read_text()


def between(source, start, end):
    first = source.index(start)
    return source[first:source.index(end, first)]


def extract(source):
    wrapper = between(source, "static int apple_smc_cmd(", "\nstatic int apple_smc_rw_locked(")
    # The fixed function no longer uses the legacy wrapper; keep it compiled.
    wrapper = wrapper.replace("static int apple_smc_cmd(", "static __attribute__((unused)) int apple_smc_cmd(")
    function = between(source, "int apple_smc_get_key_info(", "\nEXPORT_SYMBOL(apple_smc_get_key_info);")
    return wrapper, function


before, after = args.before.read_text(), args.after.read_text()
wrapper, target = extract(after)
mutations = {
    "unprotected_copy": (
        "\tguard(mutex)(&smc->mutex);\n\n\tret = apple_smc_cmd_locked",
        "\tret = apple_smc_cmd", "success"),
    "early_unlock": (
        "\tret = apple_smc_cmd_locked(smc, SMC_MSG_GET_KEY_INFO, key, 0, 0, NULL);",
        "\tret = apple_smc_cmd_locked(smc, SMC_MSG_GET_KEY_INFO, key, 0, 0, NULL);\n"
        "\tassert(pthread_mutex_unlock(&smc->mutex) == 0);\n"
        "\tscope_guard.mutex = NULL;", "success"),
    "drop_copy": ("memcpy_fromio(key_info, smc->shmem.iomem, sizeof(key_info));", "memset(key_info, 0, sizeof(key_info));", "success"),
    "wrong_size": ("info->size = key_info[0];", "info->size = key_info[5];", "success"),
    "wrong_type": ("get_unaligned_be32(&key_info[1])", "get_unaligned_be32(&key_info[0])", "success"),
    "wrong_flags": ("info->flags = key_info[5];", "info->flags = key_info[0];", "success"),
    "drop_error_guard": ("if (ret >= 0 && info)", "if (info)", "error"),
    "reject_positive": ("if (ret >= 0 && info)", "if (ret == 0 && info)", "positive"),
    "lost_error": ("return ret;", "return 0;", "error"),
}
cases = ("success", "positive", "error", "null", "null_error")
with tempfile.TemporaryDirectory(prefix="smc-key-info-") as temp:
    directory = Path(temp)

    def run(name, wrapper, target, selected, success):
        cfile, executable = directory / (name + ".c"), directory / name
        cfile.write_text(fixture.replace("/* ACTUAL_FUNCTIONS */", wrapper + target))
        subprocess.run(["cc", "-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror", "-pthread", str(cfile), "-o", str(executable)], check=True)
        for case in selected:
            result = subprocess.run([str(executable), case], capture_output=True, text=True, timeout=10)
            print(name, case, "rc", result.returncode, result.stdout.strip(), result.stderr.strip(), flush=True)
            if success:
                assert result.returncode == 0, (name, case)
            else:
                assert result.returncode == -6 and "Assertion" in result.stderr, (name, case, "must fail an assertion")

    run("before", *extract(before), ("success",), False)
    run("after", wrapper, target, cases, True)
    for name, (old, new, case) in mutations.items():
        assert target.count(old) == 1, (name, "non-unique mutation")
        mutated = target.replace(old, new)
        run(name, wrapper, mutated, (case,), False)
    run("restored", wrapper, target, cases, True)
print("PASS: actual-source race, exit paths, named mutants, restored cases; temporary files removed.")
