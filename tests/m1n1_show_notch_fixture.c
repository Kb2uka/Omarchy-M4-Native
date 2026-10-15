// SPDX-License-Identifier: MIT
// Copyright (c) 2026 KB2UKA
// Runs m1n1's real get_notchless_fb() (from src/kboot.c, extracted by
// tests/test_m1n1_show_notch.py into notchless.inc) against a simulated boot.
//
//   fixture CASE  ->  prints "base_offset_rows height"
//
// Cases: j713 (2560x1664, partially-occluded-display = 1), no_notch (the ADT
// property absent), occluded_zero (present but 0), already_16_10 (2560x1600
// with the property set).
#include <stdio.h>
#include <string.h>

typedef unsigned long u64;
typedef unsigned int u32;

#define T8015 0x8015
#define STRIDE 10240UL
#define BASE 0x103e1bec000UL

static struct {
    struct {
        u64 base, display, stride, width, height, depth;
    } video;
} cur_boot_args;

static int adt_stub;
static int *adt = &adt_stub;
static u32 chip_id = 0x8132, board_id = 0x2c;
static int occluded = 1, occluded_present = 1;

static int adt_path_offset(int *dt, const char *path)
{
    (void)dt;
    return strcmp(path, "/product") == 0 ? 1 : -1;
}

static int adt_getprop(int node, const char *name, u32 *val)
{
    if (node != 1 || strcmp(name, "partially-occluded-display") != 0 || !occluded_present)
        return -1;
    *val = (u32)occluded;
    return (int)sizeof(*val);
}

#define ADT_GETPROP(dt, node, name, val) ((void)(dt), adt_getprop((node), (name), (val)))

#include "notchless.inc"

int main(int argc, char **argv)
{
    const char *which = argc > 1 ? argv[1] : "";
    cur_boot_args.video.base = BASE;
    cur_boot_args.video.stride = STRIDE;
    cur_boot_args.video.width = 2560;
    cur_boot_args.video.height = 1664;
    if (!strcmp(which, "j713")) {
    } else if (!strcmp(which, "no_notch")) {
        occluded_present = 0;
    } else if (!strcmp(which, "occluded_zero")) {
        occluded = 0;
    } else if (!strcmp(which, "already_16_10")) {
        cur_boot_args.video.height = 1600;
    } else {
        fprintf(stderr, "unknown case %s\n", which);
        return 2;
    }
    u64 base = 0, height = 0;
    get_notchless_fb(&base, &height);
    fflush(stdout);
    fprintf(stderr, "RESULT %lu %lu\n", (base - BASE) / STRIDE, height);
    return 0;
}
