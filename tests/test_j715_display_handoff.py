# SPDX-License-Identifier: MIT
"""Compile actual staged C validation, never a parallel Python implementation."""
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_j715_display_profile import FIXTURE, PROFILE, ROOT, environment

CLIENT_READERS = r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <sys/types.h>
typedef uint32_t u32;
typedef uint8_t u8;
typedef long loff_t;
#include "profile.h"
struct mutex { int unused; };
struct completion { int unused; };
struct device { void *of_node; };
struct platform_device { struct device dev; void *data; };
struct apple_rtkit { int unused; };
struct afk_endpoint { int unused; };
struct dentry { int unused; };
struct backlight_device { int unused; };
struct file { void *private_data; };
struct backlight_properties { int type; u32 max_brightness, brightness; };
struct of_phandle_args { int args_count; u32 args[1]; void *np; };
static int wake_calls, command7, registered, bad_reply, fail_command7, board_ok = 1;
static struct apple_rtkit rtk;
static struct afk_endpoint endpoint;
static struct dentry debug;
static struct backlight_device backlight;
#define __user
#define READ_ONCE(v) (v)
#define WRITE_ONCE(v, x) ((v) = (x))
#define IS_ERR(v) ((intptr_t)(v) < 0)
#define PTR_ERR(v) ((int)(intptr_t)(v))
#define GFP_KERNEL 0
#define BACKLIGHT_RAW 1
#define THIS_MODULE 0
#define DMA_BIT_MASK(n) ((1ULL << (n)) - 1)
#define mutex_lock(v) ((void)(v))
#define mutex_unlock(v) ((void)(v))
#define mutex_init(v) ((void)(v))
#define init_completion(v) ((void)(v))
#define dev_err(dev, ...) ((void)(dev))
#define dev_info(dev, ...) ((void)(dev))
#define dev_err_probe(dev, err, ...) ((void)(dev), (err))
#define of_node_put(v) ((void)(v))
#define __module_get(v) ((void)(v))
#define client_remove_debugfs 0
#define client_rtkit_ops 0
#define start_fops 0
#define brightness_fops 0
#define status_fops 0
static const int backlight_ops;
#define devm_add_action_or_reset(dev, callback, data) ((void)(dev), (void)(data), 0)
#define debugfs_create_file(name, mode, parent, client, ops) ((void)(client), (void)(parent), &debug)
#define devm_apple_rtkit_init(dev, client, name, index, ops) ((void)(dev), (void)(client), &rtk)
#define afk_init(dev, rt, client, ready, failed) ((void)(dev), (void)(rt), (void)(client), &endpoint)
static int kstrtou32_from_user(const char *p, size_t size, int base, u32 *value)
{ (void)size; *value = (u32)strtoul(p, NULL, base); return 0; }
static int apple_rtkit_wake(struct apple_rtkit *p) { (void)p; ++wake_calls; return 0; }
static int afk_start(struct afk_endpoint *p) { (void)p; return 0; }
static int wait_for_completion_timeout(struct completion *p, int timeout)
{ (void)p; (void)timeout; return 1; }
static int msecs_to_jiffies(int value) { return value; }
static u32 get_unaligned_le32(const void *p)
{ const u8 *b = p; return b[0] | (u32)b[1] << 8 | (u32)b[2] << 16 | (u32)b[3] << 24; }
static unsigned int fls(u32 value) { return 32 - __builtin_clz(value); }
static int of_machine_is_compatible(const char *p) { (void)p; return board_ok; }
static int of_property_read_bool(void *node, const char *name) { (void)node; (void)name; return 1; }
static void *of_find_property(void *node, const char *name, int *length)
{ (void)node; (void)name; (void)length; return &debug; }
static int of_property_match_string(void *node, const char *name, const char *value)
{ (void)node; (void)name; (void)value; return 0; }
static int of_count_phandle_with_args(void *node, const char *name, const char *cells)
{ (void)node; (void)name; (void)cells; return 1; }
static int of_parse_phandle_with_args(void *node, const char *name, const char *cells,
                                     int index, struct of_phandle_args *args)
{ (void)node; (void)name; (void)cells; (void)index; args->args_count = 1; args->args[0] = J715_STREAM; return 0; }
static void *iommu_get_domain_for_dev(struct device *dev) { return dev; }
static int dma_set_mask_and_coherent(struct device *dev, uint64_t mask) { (void)dev; (void)mask; return 0; }
static void *devm_kzalloc(struct device *dev, size_t size, int flags)
{ (void)dev; (void)flags; return calloc(1, size); }
static struct dentry *debugfs_create_dir(const char *name, void *parent)
{ (void)name; (void)parent; return &debug; }
static void platform_set_drvdata(struct platform_device *dev, void *data) { dev->data = data; }
static struct backlight_device *backlight_device_register(const char *name, struct device *dev,
             void *client, const void *ops, struct backlight_properties *props)
{
    (void)dev; (void)client; (void)ops;
    assert(!strcmp(name, "j715-dcp") && command7 == 1 && !fail_command7);
    assert(props->brightness == J715_PANEL_INITIAL_NITS && props->max_brightness == J715_PANEL_MAX_NITS);
    ++registered; return &backlight;
}
"""

CLIENT_COMMAND = r"""
static int iboot_command(struct j715_client *client, u32 op, u32 bits,
                         bool has_value, void *reply, size_t size)
{
    (void)has_value; (void)size;
    if (op == 3) {
        const u32 words[5] = {3, 20, 1, 1, 1};
        memcpy(reply, words, sizeof(words));
        if (bad_reply) ((u8 *)reply)[12] = 0;
    } else {
        assert(op == 7);
        float nits = J715_PANEL_INITIAL_NITS;
        u32 expected;
        memcpy(&expected, &nits, sizeof(expected));
        assert(bits == expected);
        ++command7;
        if (fail_command7) { client_failed(client, -EIO); return -EIO; }
    }
    return 0;
}
"""

CLIENT_MAIN = r"""
int main(int argc, char **argv)
{
    assert(argc == 2);
    bad_reply = !strcmp(argv[1], "protocol");
    fail_command7 = !strcmp(argv[1], "command7");
    board_ok = strcmp(argv[1], "board") != 0;
    struct platform_device device = {0};
    int result = client_probe(&device);
    assert(wake_calls == 0 && command7 == 0 && registered == 0);
    if (!board_ok) { assert(result != 0 && !device.data); return 0; }
    assert(result == 0 && device.data);
    struct j715_client *client = device.data;
    struct file file = {.private_data = client};
    result = (int)start_write(&file, "1", 1, NULL);
    assert(wake_calls == 1 && registered == 0);
    if (bad_reply) {
        assert(result < 0 && client->state.failed);
    } else {
        assert(result == 1);
        if (!strcmp(argv[1], "bounds")) {
            assert(brightness_write(&file, "124", 3, NULL) < 0 && !command7 && !registered);
            assert(brightness_write(&file, "17", 2, NULL) < 0 && !command7 && client->state.failed);
            free(client);
            return 0;
        }
        result = (int)brightness_write(&file, "17", 2, NULL);
        assert(command7 == 1);
        assert(fail_command7 ? result < 0 && !registered : result == 2 && registered == 1);
    }
    assert(start_write(&file, "1", 1, NULL) < 0 && wake_calls == 1);
    free(client);
    return 0;
}
"""


class Handoff(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.scratch = Path(cls.temporary.name)
        (cls.scratch / "src").mkdir()
        # Exact N1 identities are verified before any source is staged.
        lock = PROFILE.SourceLock.read(ROOT / "display/j715/source-lock.json")
        for source in PROFILE.local_sources(ROOT, lock):
            if source.pin.path.endswith("/src/kboot.c"):
                (cls.scratch / "src/kboot.c").write_bytes(source.data)
            if source.pin.path.endswith("/src/dart.c"):
                dart = source.data.decode()
        result = subprocess.run(["git", "apply", "--unsafe-paths",
                                 str(ROOT / "display/j715/m1n1-j715-handoff.patch")],
                                cwd=cls.scratch, env=environment(), capture_output=True, text=True, check=False)
        if result.returncode:
            raise AssertionError(result.stderr)
        for name in ("m1n1-sep-handoff.patch", "m1n1-preserve-sep-rng.patch"):
            result = subprocess.run(
                ["git", "apply", str(ROOT / "display/j715" / name)],
                cwd=cls.scratch, env=environment(), capture_output=True, text=True, check=False)
            if result.returncode:
                raise AssertionError(result.stderr)
        kboot = (cls.scratch / "src/kboot.c").read_text()
        actual = kboot[kboot.index("struct dcp_handoff_run {"):kboot.index("static int dt_set_display(void)")]
        (cls.scratch / "actual-handoff.inc").write_text(actual)
        constants = "\n".join(line for line in dart.splitlines()
                              if line.startswith(("#define DART_T8110_", "#define DART_PTE_VALID")))
        walker = dart[dart.index("int dart_visit_existing_fdt("):dart.index("int dart_setup_pt_region(")]
        (cls.scratch / "actual-dart.inc").write_text(constants + "\n" + walker)
        (cls.scratch / "profile.h").write_bytes((ROOT / "display/j715/backlight/profile.h").read_bytes())
        (cls.scratch / "j715-profile-generated.h").write_text(PROFILE.header(PROFILE.parse(FIXTURE)))
        cls.binary = cls.scratch / "handoff"
        result = subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-fno-pie", "-no-pie",
                                 "-I", str(cls.scratch), str(ROOT / "tests/j715_display_handoff_fixture.c"),
                                 "-o", str(cls.binary)], env=environment(), capture_output=True, text=True, check=False)
        if result.returncode:
            raise AssertionError(result.stderr)

    def test_actual_client_probe_start_protocol_and_first_write(self):
        source = (ROOT / "display/j715/backlight/client.c").read_text()
        def section(start, end):
            return source[source.index(start):source.index(end)]
        pieces = [
            CLIENT_READERS, section("struct j715_client {", "struct iboot_request"),
            section("static void client_failed(", "static void client_receive("), CLIENT_COMMAND,
            section("static u32 nits_bits(", "static int backlight_update("),
            section("static ssize_t start_write(", "static int status_show("),
            section("static int validate_stream(", "static const struct of_device_id"),
            CLIENT_MAIN]
        code, binary = self.scratch / "client.c", self.scratch / "client"
        code.write_text("\n".join(pieces))
        result = subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                                 "-I", str(self.scratch), str(code), "-o", str(binary)],
                                env=environment(), capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        for case in ("valid", "board", "protocol", "command7", "bounds"):
            with self.subTest(case=case):
                result = subprocess.run([str(binary), case], env=environment(),
                                        capture_output=True, text=True, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_actual_handoff_refuses_invalid_current_boot_before_publishing(self):
        for case in ("valid", "board", "profile", "firmware", "stream", "nub", "overflow",
                     "unreserved", "partial", "translation", "unreserved-tables", "missing-table",
                     "malformed-table", "bypass", "framebuffer", "framebuffer-overflow", "allocator",
                     "publish-1", "publish-2", "publish-3", "publish-4", "publish-5", "publish-6", "publish-7"):
            with self.subTest(case=case):
                result = subprocess.run([str(self.binary), case], env=environment(),
                                        capture_output=True, text=True, check=False)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
