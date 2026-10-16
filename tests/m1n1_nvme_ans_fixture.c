/* SPDX-License-Identifier: MIT */
/*
 * Host shims for m1n1's real nvme.c (issue #82). The test compiles that file
 * unchanged (or patched) into this one, runs nvme_init() and nvme_shutdown()
 * the way stage 1 does around loading stage 2, and prints the order in which
 * ANS is booted, put to sleep, reset through PMGR and released:
 *
 *   ./fixture m4|m1|m4_initfail|m1_initfail
 *
 * m4 has the ADT's nvme-secure-bar property (T8132), m1 does not. The
 * _initfail cases make the ANS boot status check fail, so nvme_init() takes
 * its error path after rtkit_sleep().
 */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;

#define BIT(x)            (1UL << (x))
#define GENMASK(h, l)     (((~0UL) << (l)) & (~0UL >> (63 - (h))))
#define FIELD_PREP(m, v)  (((u64)(v) << __builtin_ctzll(m)) & (m))
#define FIELD_GET(m, v)   (((u64)(v) & (m)) >> __builtin_ctzll(m))
#define UNUSED(x)         (void)(x)
#define SZ_4K             0x1000
#define SZ_16K            0x4000
#define USEC_PER_SEC      1000000
#define PMGR_DIE_ID       GENMASK(31, 28)
#define dma_wmb()         __sync_synchronize()
#define dma_rmb()         __sync_synchronize()

#define NVMMU_REGS 0x100000UL
#define NVME_REGS  0x200000UL
#define STEP_CAP   10000000ULL

typedef struct {
    int unused;
} asc_dev_t, rtkit_dev_t, sart_dev_t;

struct rtkit_message {
    u8 ep;
    u64 msg;
};

static bool secure_bar, boot_ok, ans_running;
static u64 now_us;
static u32 cc;
static char trace[1024];
static asc_dev_t the_asc;
static rtkit_dev_t the_rtkit;
static sart_dev_t the_sart;
static void *adt = &the_asc;

static void note(const char *event)
{
    if (strlen(trace) + strlen(event) + 2 > sizeof(trace))
        abort();
    if (trace[0])
        strcat(trace, " ");
    strcat(trace, event);
}

static inline u32 read32(u64 addr);
static inline void write32(u64 addr, u32 value);
static inline void set32(u64 addr, u32 set);
static inline void clear32(u64 addr, u32 clear);
static inline void mask32(u64 addr, u32 clear, u32 set);

static inline void write64_lo_hi(u64 addr, u64 value)
{
    UNUSED(addr);
    UNUSED(value);
}

static inline int poll32(u64 addr, u32 mask, u32 target, u32 timeout)
{
    UNUSED(addr);
    UNUSED(mask);
    UNUSED(target);
    UNUSED(timeout);
    return boot_ok ? 0 : -1;
}

static inline u64 timeout_calculate(u32 usec)
{
    return now_us + usec;
}

static inline bool timeout_expired(u64 timeout)
{
    if (++now_us > STEP_CAP) {
        printf("HANG\n");
        exit(3);
    }
    return now_us > timeout;
}

static void *memalign(size_t align, size_t size)
{
    void *p = NULL;
    return posix_memalign(&p, align, size) ? NULL : p;
}

static inline int adt_path_offset_trace(void *dt, const char *path, int *out)
{
    UNUSED(dt);
    UNUSED(out);
    return strcmp(path, "/arm-io/ans") ? -1 : 1;
}

static inline const void *adt_get_property(void *dt, int node, const char *name)
{
    UNUSED(dt);
    UNUSED(node);
    return secure_bar && !strcmp(name, "nvme-secure-bar") ? dt : NULL;
}

static inline int adt_get_reg(void *dt, int *path, const char *prop, int idx, u64 *addr, u64 *size)
{
    UNUSED(dt);
    UNUSED(path);
    UNUSED(prop);
    UNUSED(size);
    *addr = idx == 9 ? NVME_REGS : NVMMU_REGS;
    return 0;
}

static inline int adt_getprop_fail(void *dt, int node, const char *name, void *val)
{
    UNUSED(dt);
    UNUSED(node);
    UNUSED(name);
    UNUSED(val);
    return -1;
}
#define ADT_GETPROP(dt, node, name, val) adt_getprop_fail(dt, node, name, val)

static asc_dev_t *asc_init(const char *path)
{
    UNUSED(path);
    return &the_asc;
}

static void asc_free(asc_dev_t *asc)
{
    UNUSED(asc);
}

static bool asc_cpu_running(asc_dev_t *asc)
{
    UNUSED(asc);
    return ans_running;
}

static sart_dev_t *sart_init(const char *path)
{
    UNUSED(path);
    return &the_sart;
}

static void sart_free(sart_dev_t *sart)
{
    UNUSED(sart);
}

static rtkit_dev_t *rtkit_init(const char *name, asc_dev_t *asc, void *dart, void *dart_iovad,
                               sart_dev_t *sart, bool sram)
{
    UNUSED(name);
    UNUSED(asc);
    UNUSED(dart);
    UNUSED(dart_iovad);
    UNUSED(sart);
    UNUSED(sram);
    return &the_rtkit;
}

static bool rtkit_boot(rtkit_dev_t *rtk)
{
    UNUSED(rtk);
    ans_running = true;
    note("boot");
    return true;
}

static int rtkit_recv(rtkit_dev_t *rtk, struct rtkit_message *msg)
{
    UNUSED(rtk);
    UNUSED(msg);
    return 0;
}

/* SLEEP, then clear the ANS CPU's RUN bit, as m1n1's rtkit_sleep() does. */
static bool rtkit_sleep(rtkit_dev_t *rtk)
{
    UNUSED(rtk);
    ans_running = false;
    note("sleep");
    return true;
}

static void rtkit_free(rtkit_dev_t *rtk)
{
    UNUSED(rtk);
    note("free");
}

static int pmgr_reset(int die, const char *name)
{
    char event[64];

    snprintf(event, sizeof(event), "reset:%d.%s", die, name);
    note(event);
    return 0;
}

#include "nvme.h"
#include "nvme.c"

/* The controller: CC.EN drives CSTS.RDY, a normal shutdown request completes at once. */
static u32 csts(void)
{
    u32 value = (cc & NVME_CC_EN) ? NVME_CSTS_RDY : 0;

    if (FIELD_GET(NVME_CC_SHN, cc) == NVME_CC_SHN_NORMAL)
        value |= FIELD_PREP(NVME_CSTS_SHST, NVME_CSTS_SHST_DONE);
    return value;
}

static inline u32 read32(u64 addr)
{
    if (addr == nvme_base + NVME_CSTS)
        return csts();
    if (addr == nvmmu_base + NVMMU_TCB_STAT)
        return 0;
    fprintf(stderr, "unexpected read32 0x%llx\n", (unsigned long long)addr);
    abort();
}

/* A doorbell completes the command at once, in the queue's current phase. */
static void complete(struct nvme_queue *q, u32 tag)
{
    q->cqes[q->cq_head].tag = (u16)tag;
    q->cqes[q->cq_head].status = q->cq_phase;
}

static inline void write32(u64 addr, u32 value)
{
    if (addr == nvme_base + NVME_DB_LINEAR_ASQ)
        complete(&adminq, value);
    else if (addr == nvme_base + NVME_DB_LINEAR_IOSQ)
        complete(&ioq, value);
    else if (addr == nvme_base + NVME_CC)
        cc = value;
}

static inline void set32(u64 addr, u32 set)
{
    if (addr == nvme_base + NVME_CC)
        cc |= set;
}

static inline void clear32(u64 addr, u32 clear)
{
    if (addr == nvme_base + NVME_CC)
        cc &= ~clear;
}

static inline void mask32(u64 addr, u32 clear, u32 set)
{
    if (addr == nvme_base + NVME_CC)
        cc = (cc & ~clear) | set;
}

int main(int argc, char **argv)
{
    if (argc != 2)
        return 2;
    const char *c = argv[1];
    secure_bar = !strncmp(c, "m4", 2);
    boot_ok = !strstr(c, "_initfail");
    if (strcmp(c, "m4") && strcmp(c, "m1") && strcmp(c, "m4_initfail") && strcmp(c, "m1_initfail"))
        return 2;

    bool up = nvme_init();
    if (up != boot_ok) {
        printf("FAIL init returned %d\n", up);
        return 1;
    }
    if (up)
        nvme_shutdown();
    printf("TRACE %s type=%s initialized=%d ans_running=%d\n", trace,
           nvme_type == NVME_T8132 ? "t8132" : "t8103", nvme_initialized, ans_running);
    return 0;
}
