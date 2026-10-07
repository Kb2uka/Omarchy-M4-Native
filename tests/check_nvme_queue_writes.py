#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Compile the patched driver's actual queue-register blocks against mock MMIO.

Usage: python3 tests/check_nvme_queue_writes.py /path/to/linux/drivers/nvme/host/apple.c
This checks values and ordering, not firmware behavior or hardware operation.
"""
from pathlib import Path
import os
import hashlib
import re
import subprocess
import sys
import tempfile


def block(text, needle, start):
    begin = text.index(needle, start)
    brace = text.index("{", begin)
    depth = 1
    end = brace + 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[begin:end]


if len(sys.argv) != 2:
    raise SystemExit("usage: check_nvme_queue_writes.py <patched-apple.c>")
try:
    source_bytes = Path(sys.argv[1]).read_bytes()
    source = source_bytes.decode("utf-8")
except (OSError, UnicodeError) as error:
    print(f"cannot read driver source: {error}", file=sys.stderr)
    raise SystemExit(2)
print("driver_source=" + str(Path(sys.argv[1]).resolve()), flush=True)
print("driver_sha256=" + hashlib.sha256(source_bytes).hexdigest(), flush=True)
reset = source.index("static void apple_nvme_reset_work(")
setup = source.index("dma_set_max_seg_size", reset)
early = block(source, "if (anv->hw->has_lsq_nvmmu)", setup)
ioq = source.index("ret = apple_nvme_create_sq(anv)", setup)
late = block(source, "if (anv->hw->has_separate_nvmmu)", ioq)
tcb = block(source, "if (anv->hw->has_lsq_nvmmu)", source.index("/* Setup the admin queue */", setup))
inval = block(source, "static void apple_nvmmu_inval(", 0)
profiles = "\n".join(block(source, "static const struct apple_nvme_hw " + name, 0) + ";"
                     for name in ("apple_nvme_t8015_hw", "apple_nvme_t8103_hw", "apple_nvme_t8132_hw"))
allocation = source.index("static struct apple_nvme *apple_nvme_alloc(")
map_if = block(source, "if (anv->hw->has_separate_nvmmu)", allocation)
map_else = block(source, "else", source.index(map_if, allocation) + len(map_if))
map_start = source.index("anv->mmio_nvme = devm_platform_ioremap_resource_byname", allocation)
map_end = source.index(map_else, source.index(map_if, allocation)) + len(map_else)
if map_start > source.index(map_if, allocation):
    raise ValueError("NVMMU alias precedes the NVMe mapping")
map_nvmmu = source[map_start:map_end]
profile_match = re.search(r'\{\s*\.compatible\s*=\s*"apple,t8132-nvme-ans2",\s*\.data\s*=\s*&([a-zA-Z0-9_]+)\s*\}', source)
if profile_match is None:
    raise ValueError("missing T8132 compatible mapping")
names = set(re.findall(r"\bAPPLE_[A-Z0-9_]+\b", early + tcb + late + inval))
defines = []
for name in sorted(names):
    match = re.search(r"^#define\s+" + name + r"\s+([^\n]+)$", source, re.M)
    if match is None:
        raise ValueError("missing driver definition: " + name)
    defines.append("#define " + name + " " + match.group(1))

harness = r'''
#include <stdint.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdbool.h>
#include <string.h>
typedef uint32_t u32;
#define BIT(n) (1U << (n))
struct apple_nvme_hw { bool has_lsq_nvmmu, has_separate_nvmmu; unsigned max_queue_depth; };
struct apple_nvme;
struct apple_nvme_queue { uint64_t sq_dma_addr, cq_dma_addr, tcb_dma_addr; struct apple_nvme *owner; };
struct apple_nvme { void *dev; const struct apple_nvme_hw *hw; char *mmio_nvme, *mmio_nvmmu; struct apple_nvme_queue ioq, adminq; };
static struct apple_nvme *queue_to_apple_nvme(struct apple_nvme_queue *q) { return q->owner; }
#define dev_warn_ratelimited(...) ((void)0)
static unsigned map_calls;
static const char *fail_mapping;
static void *devm_platform_ioremap_resource_byname(void *dev, const char *name) {
    (void)dev;
    map_calls++;
    if (fail_mapping && !strcmp(name, fail_mapping))
        return (void *)(intptr_t)(!strcmp(name, "nvme") ? -EBUSY : -EINVAL);
    if (!strcmp(name, "nvme")) return (void *)(uintptr_t)0x10000000;
    if (!strcmp(name, "nvmmu")) return (void *)(uintptr_t)0x20000000;
    abort();
}
#define IS_ERR(p) ((uintptr_t)(p) >= (uintptr_t)-4095)
#define PTR_ERR(p) ((int)(intptr_t)(p))
static uintptr_t status_address;
static uint32_t readl(void *address) { status_address = (uintptr_t)address; return 0; }
struct event { uintptr_t address; uint64_t value; unsigned phase; };
static struct event events[16];
static unsigned count, phase;
static void record(uint64_t value, void *address) {
    if (count == 16) abort();
    events[count++] = (struct event){(uintptr_t)address, value, phase};
}
#define writel(value, address) record((value), (address))
#define writeq(value, address) record((value), (address))
#define lo_hi_writeq(value, address) record((value), (address))
'''
harness += "\n".join(defines) + "\n" + profiles + "\n" + inval
harness += "\nstatic int map_resources(struct apple_nvme *anv) { int ret; void *pdev = anv->dev;\n" + map_nvmmu + "\nreturn 0;\nput_dev: return ret;\n}\n"
harness += "\nstatic void configure(struct apple_nvme *anv) {\nphase=0;\n" + early
harness += "\n" + tcb + "\nphase=1;\n" + late + "\n}\n"
harness += '\n#define M4_PROFILE ' + profile_match.group(1) + '\n'
harness += r'''
static int check(const struct apple_nvme_hw *hw, bool separate) {
    struct apple_nvme anv = {.hw=hw, .mmio_nvme=NULL,
        .mmio_nvmmu=NULL,
        .ioq={.sq_dma_addr=0x33330000,.cq_dma_addr=0x44440000,.tcb_dma_addr=0x55550000},
        .adminq={.tcb_dma_addr=0x66660000}};
    map_calls = 0;
    if (map_resources(&anv)) return 1;
    if (anv.mmio_nvmmu != (char *)(uintptr_t)(separate ? 0x20000000 : 0x10000000)) return 1;
    if (map_calls != (separate ? 2U : 1U)) return 1;
    anv.ioq.owner = &anv;
    if (hw->has_separate_nvmmu != separate || hw->max_queue_depth != 64) return 1;
    count = 0;
    configure(&anv);
    unsigned sizes = 0, bases = 0, nvmmu = 0;
    phase = 2;
    apple_nvmmu_inval(&anv.ioq, 17);
    if (status_address != (uintptr_t)anv.mmio_nvmmu + 0x28120) return 1;
    for (unsigned i = 0; i < count; i++) {
        uintptr_t offset = events[i].address - (uintptr_t)anv.mmio_nvme;
        if (offset == 0x1200 || offset == 0x1208) {
            if (!separate || events[i].phase != 1) return 1;
            uint64_t expected = offset == 0x1200 ? anv.ioq.sq_dma_addr : anv.ioq.cq_dma_addr;
            if (events[i].value != expected) return 1;
            bases++;
        }
        uintptr_t mmu_offset = events[i].address - (uintptr_t)anv.mmio_nvmmu;
        if (mmu_offset == 0x28100 || mmu_offset == 0x28108 || mmu_offset == 0x28110 || mmu_offset == 0x28118) {
            uint64_t expected = mmu_offset == 0x28100 ? 63 :
                mmu_offset == 0x28108 ? anv.adminq.tcb_dma_addr :
                mmu_offset == 0x28110 ? anv.ioq.tcb_dma_addr : 17;
            unsigned expected_phase = mmu_offset == 0x28118 ? 2 : 0;
            if (events[i].value != expected || events[i].phase != expected_phase) return 1;
            nvmmu++;
        }
        if (offset == 0x1210) {
            sizes++;
            uint64_t expected = separate ? 0x003f003f : 0x00400040;
            printf("%s: offset=0x1210 value=0x%08llx phase=%u bases_before=%u\n",
                   separate ? "M4" : "M1-M3", (unsigned long long)events[i].value,
                   events[i].phase, bases);
            if (events[i].value != expected || events[i].phase != (unsigned)separate ||
                (separate && bases != 2)) return 1;
        }
    }
    return sizes != 1 || bases != (separate ? 2U : 0U) || nvmmu != 4;
}
int main(void) {
    if (apple_nvme_t8015_hw.has_lsq_nvmmu) return 1;
    if (check(&apple_nvme_t8103_hw, false) || check(&M4_PROFILE, true)) return 1;
    const char *failures[] = {"nvme", "nvmmu"};
    const int errors[] = {-EBUSY, -EINVAL};
    for (unsigned i = 0; i < 2; i++) {
        struct apple_nvme anv = {.hw=&M4_PROFILE};
        fail_mapping = failures[i];
        if (map_resources(&anv) != errors[i]) return 1;
    }
    puts("PASS: actual hardware profiles, queue size/order and NVMMU window routing");
    return 0;
}
'''
with tempfile.TemporaryDirectory(prefix="nvme-mmio-check-") as tmp:
    path = Path(tmp)
    (path / "check.c").write_text(harness)
    subprocess.run([os.environ.get("CC", "cc"), "-std=c11", "-Wall", "-Wextra",
                    "-Werror", str(path / "check.c"), "-o", str(path / "check")], check=True)
    subprocess.run([str(path / "check")], check=True)
