// SPDX-License-Identifier: MIT
// Copyright (c) 2026 KB2UKA
/* Host build of the J713 P-cluster guard's policy (cpu/j713/j713_pguard_policy.h) for ctypes. */
#include <stdbool.h>
#include <stdint.h>

typedef uint32_t u32;
typedef uint64_t u64;

#include "../cpu/j713/j713_pguard_policy.h"

u32 step(unsigned int busy, unsigned int level)
{
	return pg_step[busy][level];
}

u32 fast(unsigned int busy, unsigned int level)
{
	return pg_fast[busy][level];
}

int e_heat(unsigned int e_busy, u32 e_khz)
{
	return pg_e_heat(e_busy, e_khz);
}

int float_to_mc(u32 bits, int *mc)
{
	return pg_float_to_mc(bits, mc);
}

int base(int reading_mc, bool window_busy, unsigned int min_busy, u32 window_min_level)
{
	return pg_base(reading_mc, window_busy, min_busy, window_min_level);
}

unsigned int keep(u32 *recent, u32 *recent_busy, unsigned int pos, unsigned int n, u32 tick, u32 tick_busy)
{
	return pg_keep(recent, recent_busy, pos, n, tick, tick_busy);
}

u32 lowest(const u32 *recent, unsigned int n, u32 now)
{
	return pg_lowest(recent, n, now);
}

int recent_ms(void)
{
	return PG_RECENT_MS;
}

int recent_max(void)
{
	return PG_RECENT_MAX;
}

bool window_busy(const u64 *d_idle, const u64 *d_wall, unsigned int n)
{
	return pg_window_busy(d_idle, d_wall, n);
}

bool hold(unsigned int *busy, unsigned int *e_busy, unsigned int held, unsigned int e_held, bool expired)
{
	return pg_hold(busy, e_busy, held, e_held, expired);
}

unsigned int busy_count(const u64 *d_idle, const u64 *d_wall, unsigned int n)
{
	return pg_busy_count(d_idle, d_wall, n);
}

u32 allowed(int base_mc, unsigned int busy, int e_heat_mc, int limit_mc, bool hot, const u32 *level_khz)
{
	return pg_allowed(base_mc, busy, e_heat_mc, limit_mc, hot, level_khz);
}
