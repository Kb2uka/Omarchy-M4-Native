/* SPDX-License-Identifier: MIT */
/* Synthetic readers around the actual patched handoff and pinned DART walker.
 * No device, firmware or live memory is accessed. */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint64_t u64;
typedef uint32_t u32;
typedef u32 fdt32_t;
#define BIT(n) (1ULL << (n))
#define GENMASK(h, l) ((~0ULL >> (63 - (h))) & (~0ULL << (l)))
#define FIELD_GET(mask, value) (((value) & (mask)) >> __builtin_ctzll(mask))
#define SZ_16K 16384ULL
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define min(a, b) ((a) < (b) ? (a) : (b))
#define max(a, b) ((a) > (b) ? (a) : (b))
#define ALIGN_UP(v, a) (((v) + (a) - 1) & ~((a) - 1))
#define bail(...) return -1
#define cpu_to_fdt64(v) (v)
#define fdt32_ld(p) (*(p))
#include "j715-profile-generated.h"
#include "profile.h"

static void *dt, *adt;
static struct { const char *string; const char *iboot; } os_firmware = {
    "unknown", J715_FIRMWARE_ID
};
static struct { struct { u64 base, stride, height; } video; } cur_boot_args;
struct adt_segment_ranges { u64 phys, remap, size; };
static struct adt_segment_ranges segments[3];
struct property { const char *name; u32 size; u64 value[2]; };
static struct property properties[2];
#define ADT_FOREACH_PROPERTY(blob, node, prop) \
    for (struct property *prop = properties; prop < properties + 2; ++prop)
static _Alignas(16384) u64 tables[4096];
static u64 vm_base, vm_size, pt[2], aperture[2];
static u32 iommus[2], tcr, ttbr;
static int board_ok, profile_ok, nub_ok, table_reads, marker, writes, fail_write;
static int adt_path_offset(void *blob, const char *path)
{
    (void)blob;
    if (!strcmp(path, "/chosen/carveout-memory-map")) return 1;
    if (!strcmp(path, J715_ADT_NUB)) return nub_ok ? 2 : -1;
    return !strcmp(path, J715_ADT_DART) ? 3 : -1;
}
static int adt_path_offset_trace(void *blob, const char *path, int *trace)
{
    (void)blob;
    trace[0] = !strcmp(path, J715_ADT_DART) ? 3 : 4;
    return trace[0];
}
static int adt_get_reg(void *blob, int *trace, const char *name, int index, u64 *base, u64 *size)
{
    (void)blob; (void)name; (void)index;
    *base = trace[0] == 3 ? J715_DART_ADDRESS : J715_DCP_ADDRESS;
    *size = trace[0] == 3 ? J715_DART_SIZE : J715_DCP_SIZE;
    return 0;
}
static const void *adt_getprop(void *blob, int node, const char *name, u32 *length)
{
    (void)blob; (void)node;
    if (strcmp(name, "segment-ranges")) return NULL;
    *length = sizeof(segments);
    return segments;
}
static int adt_copy(const char *name, void *output)
{
    char expected[32];
    snprintf(expected, sizeof(expected), "vm-size-%u", J715_STREAM);
    if (!strcmp(name, "vm-base")) { memcpy(output, &vm_base, 8); return 8; }
    if (!strcmp(name, expected)) { memcpy(output, &vm_size, 8); return 8; }
    snprintf(expected, sizeof(expected), "pt-region-%u", J715_STREAM);
    if (!strcmp(name, expected)) { memcpy(output, pt, 16); return 16; }
    return -1;
}
#define ADT_GETPROP(blob, node, name, out) adt_copy(name, out)
#define ADT_GETPROP_ARRAY(blob, node, name, out) adt_copy(name, out)
static int fdt_path_offset(void *blob, const char *path)
{ (void)blob; return !strcmp(path, "dcp") ? 1 : -1; }
static int fdt_node_check_compatible(void *blob, int node, const char *name)
{
    (void)blob;
    if (node == 0) return !board_ok;
    if (node == 1) return strcmp(name, "omarchy,j715-dcp-backlight");
    return strcmp(name, "apple,t8110-dart");
}
static const void *fdt_getprop(void *blob, int node, const char *name, int *length)
{
    (void)blob; (void)node;
    if (!strcmp(name, "iommus")) { *length = sizeof(iommus); return iommus; }
    *length = sizeof(J715_PROFILE_SHA256);
    return profile_ok ? J715_PROFILE_SHA256 : NULL;
}
static int fdt_delprop(void *blob, int node, const char *name)
{ (void)blob; (void)node; (void)name; marker = 0; return 0; }
static u32 fdt_get_phandle(void *blob, int node)
{ (void)blob; return (u32)node; }
static int fdt_node_offset_by_phandle(void *blob, u32 handle)
{ (void)blob; return (int)handle; }
static u64 dt_get_address(void *blob, int node)
{ (void)blob; return node == 2 ? J715_DART_ADDRESS : J715_DCP_ADDRESS; }
static u32 read32(u64 address)
{
    ++table_reads;
    return address == J715_DART_ADDRESS + 0x1000 + 4 * J715_STREAM ? tcr : ttbr;
}
typedef int (*dart_mapping_visitor_t)(u64, u64, void *);
#include "actual-dart.inc"

static int record_write(void)
{ ++writes; return fail_write && writes == fail_write ? -1 : 0; }
static int dt_get_or_add_reserved_mem(const char *name, const char *type, bool nomap, u64 phys, u64 size)
{
    (void)name; (void)type;
    assert(nomap && size && phys <= UINT64_MAX - size);
    return record_write() ? -1 : 4;
}
static int dt_device_set_reserved_mem(int node, const char *name, u32 handle, u64 iova, u64 size)
{
    (void)node; (void)name;
    assert(handle == 1 && iova >= vm_base && size);
    return record_write();
}
static int dt_device_add_mem_region(const char *name, u32 handle, const char *label)
{ (void)name; (void)handle; (void)label; return record_write(); }
static int fdt_setprop(void *blob, int node, const char *name, const void *value, int length)
{
    (void)blob; (void)node;
    assert(!strcmp(name, "apple,dma-range") && length == 16);
    memcpy(aperture, value, 16);
    return record_write();
}
static int fdt_setprop_empty(void *blob, int node, const char *name)
{
    (void)blob; (void)node;
    assert(!strcmp(name, "omarchy,display-handoff-verified"));
    if (record_write()) return -1;
    marker = 1;
    return 0;
}
#include "actual-handoff.inc"

static void setup(void)
{
    board_ok = profile_ok = nub_ok = 1;
    vm_base = BIT(36); vm_size = BIT(36);
    pt[0] = (uintptr_t)tables; pt[1] = pt[0] + sizeof(tables);
    assert(pt[1] < BIT(42));
    properties[0] = (struct property){"region-id-1", 16, {pt[0], sizeof(tables)}};
    properties[1] = (struct property){"region-id-2", 16, {0x800000, 3 * SZ_16K}};
    tables[0] = ((pt[0] + SZ_16K) >> 4) | 1;
    tables[2049] = (0x800000 >> 4) | 1;
    tables[2050] = (0x804000 >> 4) | 1;
    for (unsigned int i = 0; i < 3; ++i)
        segments[i] = (struct adt_segment_ranges){0x800000 + i * SZ_16K,
                                                  vm_base + (i + 1) * SZ_16K, SZ_16K};
    iommus[0] = 2; iommus[1] = J715_STREAM;
    tcr = 1; ttbr = (u32)((pt[0] >> 14) << 2) | 1;
    cur_boot_args.video.base = 0x800000;
    cur_boot_args.video.stride = 256;
    cur_boot_args.video.height = 2;
}

int main(int argc, char **argv)
{
    assert(argc == 2);
    setup();
    const char *test = argv[1];
    if (!strcmp(test, "board")) board_ok = 0;
    else if (!strcmp(test, "profile")) profile_ok = 0;
    else if (!strcmp(test, "firmware")) os_firmware.iboot = "other";
    else if (!strcmp(test, "stream")) ++iommus[1];
    else if (!strcmp(test, "nub")) nub_ok = 0;
    else if (!strcmp(test, "overflow")) segments[0].size = UINT64_MAX;
    else if (!strcmp(test, "unreserved")) properties[1].value[1] = SZ_16K;
    else if (!strcmp(test, "partial")) segments[1].size = 2 * SZ_16K;
    else if (!strcmp(test, "translation")) segments[0].remap += SZ_16K;
    else if (!strcmp(test, "unreserved-tables")) properties[0].value[1] = SZ_16K;
    else if (!strcmp(test, "missing-table")) ttbr = 0;
    else if (!strcmp(test, "malformed-table")) tables[0] = ((pt[1] + SZ_16K) >> 4) | 1;
    else if (!strcmp(test, "bypass")) tcr = 2;
    else if (!strcmp(test, "framebuffer")) cur_boot_args.video.base = 0x900000;
    else if (!strcmp(test, "framebuffer-overflow")) cur_boot_args.video.stride = UINT64_MAX;
    else if (!strcmp(test, "allocator")) {
        tables[2047] = tables[0];
        tables[4095] = (0x808000 >> 4) | 1;
    } else if (!strncmp(test, "publish-", 8)) fail_write = atoi(test + 8);
    else assert(!strcmp(test, "valid"));
    marker = 1; /* An imported marker cannot survive any validation failure. */
    int result = dt_set_t8132_dcp_handoff();
    if (!strcmp(test, "valid")) {
        assert(result == 0 && marker && writes);
        assert(aperture[0] == vm_base + 4 * SZ_16K);
        assert(aperture[1] == vm_size - 4 * SZ_16K);
    } else {
        assert(result != 0 && !marker);
        if (strncmp(test, "publish-", 8)) assert(!writes);
        if (!strcmp(test, "unreserved-tables")) assert(!table_reads);
    }
    puts("fixture passed");
    return 0;
}
