/* SPDX-License-Identifier: MIT */
/*
 * Host shims for m1n1's real iodev.c and dockchannel_uart.c. The test compiles
 * those files unchanged (or patched) into this file and drives the console the
 * way m1n1 does, against a simulated dockchannel TX FIFO and a simulated
 * clock: one microsecond passes per register read.
 */
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include <unistd.h>

typedef uint8_t u8;
typedef uint32_t u32;
typedef uint64_t u64;
typedef struct {
    int unused;
} spinlock_t;

#define BIT(x)                 (1UL << (x))
#define UNUSED(x)              (void)(x)
#define SPINLOCK_INIT          {0}
#define DECLARE_SPINLOCK(name) spinlock_t name = SPINLOCK_INIT
#define min(a, b)                                                                                  \
    ({                                                                                             \
        __typeof__(a) _a = (a);                                                                    \
        __typeof__(b) _b = (b);                                                                    \
        _a < _b ? _a : _b;                                                                         \
    })
#define max(a, b)                                                                                  \
    ({                                                                                             \
        __typeof__(a) _a = (a);                                                                    \
        __typeof__(b) _b = (b);                                                                    \
        _a > _b ? _a : _b;                                                                         \
    })

#define FIFO_BASE     0x10000UL
#define FIFO_SIZE     4096U
#define READ_CAP      20000000ULL
#define REG_TX8       (FIFO_BASE + 0x4004)
#define REG_TX_FREE   (FIFO_BASE + 0x4014)
#define REG_RX_COUNT  (FIFO_BASE + 0x402c)
#define STREAM_MAX    65536
/* The documented bounds, fixed here so a changed constant cannot pass. */
#define BOUND_US          100000ULL
#define CONSUMER_BOUND_US 2000000ULL
#define SLACK_US          1000ULL

static u64 now_us, reads, drain_every, drain_tick, pause_from, pause_until;
static int boot_cpu = 1;
static u32 fifo_free;
static char dock[STREAM_MAX], fb[STREAM_MAX], stream[STREAM_MAX];
static size_t dock_len, fb_len, stream_len;

static inline u32 read32(u64 addr)
{
    if (++reads > READ_CAP) {
        printf("HANG reads=%llu dock=%zu fb=%zu\n", (unsigned long long)reads, dock_len, fb_len);
        fflush(stdout);
        _exit(3);
    }
    now_us++;
    bool paused = now_us >= pause_from && now_us < pause_until;
    if (drain_every && !paused && ++drain_tick >= drain_every) {
        drain_tick = 0;
        if (fifo_free < FIFO_SIZE)
            fifo_free++;
    }
    if (addr == REG_TX_FREE)
        return fifo_free;
    if (addr == REG_RX_COUNT)
        return 0;
    fprintf(stderr, "unexpected read32 0x%llx\n", (unsigned long long)addr);
    abort();
}

static inline void write32(u64 addr, u32 value)
{
    if (addr != REG_TX8 || fifo_free == 0 || dock_len >= STREAM_MAX) {
        fprintf(stderr, "bad write32 0x%llx free=%u\n", (unsigned long long)addr, fifo_free);
        abort();
    }
    fifo_free--;
    dock[dock_len++] = (char)value;
}

static inline u64 timeout_calculate(u32 usec)
{
    return now_us + usec;
}

static inline bool timeout_expired(u64 timeout)
{
    return now_us > timeout;
}

static inline void spin_lock(spinlock_t *lock)
{
    UNUSED(lock);
}

static inline void spin_unlock(spinlock_t *lock)
{
    UNUSED(lock);
}

static inline bool mmu_active(void)
{
    return false;
}

static inline int is_boot_cpu(void)
{
    return boot_cpu;
}

static void *adt;

static inline int adt_path_offset_trace(void *dt, const char *path, int *out)
{
    UNUSED(dt);
    UNUSED(path);
    UNUSED(out);
    return 0;
}

static inline int adt_get_reg(void *dt, int *path, const char *prop, int idx, u64 *addr, u64 *size)
{
    UNUSED(dt);
    UNUSED(path);
    UNUSED(prop);
    UNUSED(idx);
    UNUSED(size);
    *addr = FIFO_BASE;
    return 0;
}

#define printf(...) ((void)0)

/* Hardware UART, USB and log devices are absent here; the framebuffer records. */
struct iodev;
static bool none_can_write(void *opaque)
{
    UNUSED(opaque);
    return false;
}

static bool fb_can_write(void *opaque)
{
    UNUSED(opaque);
    return true;
}

static ssize_t fb_write(void *opaque, const void *buf, size_t len)
{
    UNUSED(opaque);
    if (fb_len + len > STREAM_MAX)
        abort();
    memcpy(fb + fb_len, buf, len);
    fb_len += len;
    return len;
}

#include "iodev.h"

static const struct iodev_ops none_ops = {.can_write = none_can_write};
static const struct iodev_ops fb_ops = {.can_write = fb_can_write, .write = fb_write};
struct iodev iodev_uart = {.ops = &none_ops};
struct iodev iodev_usb_vuart = {.ops = &none_ops};
struct iodev iodev_log = {.ops = &none_ops};
struct iodev iodev_fb = {.ops = &fb_ops, .usage = USAGE_CONSOLE};

#include "iodev.c"
#include "dockchannel_uart.c"

#undef printf

static void console_line(int i)
{
    char line[128];
    int n = snprintf(line, sizeof(line), "[%03d] pmgr: resetting device %d, stage output\n", i, i);
    memcpy(stream + stream_len, line, n);
    stream_len += n;
    iodev_console_write(line, n);
}

static int fail(const char *what)
{
    printf("FAIL %s now=%llu dock=%zu fb=%zu stream=%zu\n", what, (unsigned long long)now_us,
           dock_len, fb_len, stream_len);
    return 1;
}

static bool screen_complete(void)
{
    return fb_len == stream_len && !memcmp(fb, stream, stream_len);
}

/* Give a slowly drained FIFO time to take everything the console still holds for it. */
static void settle(void)
{
    for (int i = 0; i < 1000000 && con_rp[IODEV_DOCKCHANNEL_UART] < con_wp; i++)
        iodev_console_write(NULL, 0);
}

int main(int argc, char **argv)
{
    const char *which = argc > 1 ? argv[1] : "";

    if (!strcmp(which, "undrained")) {
        /* The issue #82 state: part of the FIFO is free and nothing ever drains it. */
        fifo_free = 37;
        dockchannel_uart_init();
        for (int i = 0; i < 200; i++)
            console_line(i);
        if (!screen_complete())
            return fail("screen missed console output");
        if (dock_len != 37 || memcmp(dock, stream, dock_len))
            return fail("dockchannel did not keep the bytes that fit");
        if (now_us < BOUND_US)
            return fail("gave up before the timeout");
        if (now_us > BOUND_US + SLACK_US)
            return fail("waited longer than one timeout");
    } else if (!strcmp(which, "slow_drain")) {
        /* A host drains one byte per 200 us, well inside the timeout: nothing may be lost. */
        fifo_free = 8;
        drain_every = 200;
        dockchannel_uart_init();
        for (int i = 0; i < 40; i++)
            console_line(i);
        settle();
        if (!screen_complete())
            return fail("screen missed console output");
        if (dock_len != stream_len || memcmp(dock, stream, stream_len))
            return fail("drained dockchannel lost or reordered bytes");
    } else if (!strcmp(which, "recover")) {
        /* Stall first, then a host starts draining: later output must arrive whole. */
        fifo_free = 37;
        dockchannel_uart_init();
        for (int i = 0; i < 20; i++)
            console_line(i);
        size_t later = stream_len;
        drain_every = 200;
        for (int i = 20; i < 40; i++)
            console_line(i);
        settle();
        size_t tail = stream_len - later;
        if (!screen_complete())
            return fail("screen missed console output");
        if (dock_len < tail || memcmp(dock + dock_len - tail, stream + later, tail))
            return fail("output after the host attached was lost");
    } else if (!strcmp(which, "fast_host_pause")) {
        /* A debug host keeps the FIFO from filling, then pauses 150 ms: nothing may be lost. */
        fifo_free = 64;
        drain_every = 1;
        dockchannel_uart_init();
        for (int i = 0; i < 10; i++)
            console_line(i);
        pause_from = now_us;
        pause_until = now_us + 150000;
        for (int i = 10; i < 50; i++)
            console_line(i);
        settle();
        if (!screen_complete())
            return fail("screen missed console output");
        if (dock_len != stream_len || memcmp(dock, stream, stream_len))
            return fail("a paused host lost bytes");
    } else if (!strcmp(which, "slow_host_pause")) {
        /*
         * A proxy reply to a host slower than the writes: the FIFO stays full and
         * every drain lands inside a wait. Then the host pauses 150 ms mid-reply.
         */
        char reply[800];
        for (size_t i = 0; i < sizeof(reply); i++)
            reply[i] = (char)('a' + i % 26);
        fifo_free = 0;
        drain_every = 200;
        dockchannel_uart_init();
        iodev_write(IODEV_DOCKCHANNEL_UART, reply, 400);
        pause_from = now_us;
        pause_until = now_us + 150000;
        iodev_write(IODEV_DOCKCHANNEL_UART, reply + 400, 400);
        if (dock_len != sizeof(reply) || memcmp(dock, reply, sizeof(reply)))
            return fail("a paused slow host lost reply bytes");
    } else if (!strcmp(which, "consumer_vanishes")) {
        /* A host drains, then stops for good: still one bounded wait, then no more. */
        fifo_free = 64;
        drain_every = 1;
        dockchannel_uart_init();
        for (int i = 0; i < 10; i++)
            console_line(i);
        drain_every = 0;
        u64 start = now_us;
        for (int i = 10; i < 200; i++)
            console_line(i);
        if (!screen_complete())
            return fail("screen missed console output");
        if (now_us - start < CONSUMER_BOUND_US)
            return fail("gave up before the longer timeout");
        if (now_us - start > CONSUMER_BOUND_US + SLACK_US)
            return fail("waited longer than one longer timeout");
    } else if (!strcmp(which, "secondary")) {
        /* After a stall, a secondary CPU printing before the MMU is up must not wait. */
        fifo_free = 37;
        dockchannel_uart_init();
        for (int i = 0; i < 20; i++)
            console_line(i);
        boot_cpu = 0;
        u64 start = now_us;
        for (int i = 20; i < 30; i++)
            console_line(i);
        if (now_us - start > SLACK_US)
            return fail("secondary CPU waited on a stalled FIFO");
    } else {
        fprintf(stderr, "unknown case\n");
        return 2;
    }
    printf("OK %s now=%llu dock=%zu fb=%zu\n", which, (unsigned long long)now_us, dock_len, fb_len);
    return 0;
}
