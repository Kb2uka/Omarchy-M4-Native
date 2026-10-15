/* SPDX-License-Identifier: GPL-2.0-only OR MIT */
/*
 * Pure policy of the J713 P-cluster guard (j713_pguard.c), kept free of
 * kernel calls so tests/test_j713_pguard_policy.py can compile and check it on
 * a host. The caller provides u32/u64 and bool.
 */
#ifndef J713_PGUARD_POLICY_H
#define J713_PGUARD_POLICY_H

#define PG_BASE_LEVEL 4		/* P4, 1800 MHz: the clock before #66 */
#define PG_TOP_LEVEL 20		/* P20, 4464 MHz */
#define PG_P_CPUS 4
#define PG_E_CPUS 6
#define PG_E_HIGH_KHZ 2496000	/* E6 */

/*
 * CPU die (TCMz) rise, in milli-degrees C, from P4 to each P state with
 * 1-4 busy P cores, measured on the owner's Air with the idle driver loaded
 * about 1.5-2 s after the change: the guard reacts to a new reading within
 * about 1.25 s. Measured points are rounded up; states between them, and
 * states the hardware does not sustain for that core count, take the next
 * measured value or are extrapolated upward (docs/j713-cpu-boost.md). Index 0
 * (no busy core) uses the one-core row: the first core to wake is the
 * one-core case.
 */
static const u32 pg_step[PG_P_CPUS + 1][PG_TOP_LEVEL + 1] = {
	[1] = { [5] = 3000, [6] = 5000, [7] = 8000, [8] = 11000, [9] = 14000,
		[10] = 17000, [11] = 20000, [12] = 25000, [13] = 27000, [14] = 30000,
		[15] = 32000, [16] = 34000, [17] = 34000, [18] = 34000, [19] = 34000,
		[20] = 34000 },
	[2] = { [5] = 4000, [6] = 7000, [7] = 10000, [8] = 14000, [9] = 18000,
		[10] = 23000, [11] = 29000, [12] = 35000, [13] = 41000, [14] = 47000,
		[15] = 50000, [16] = 52000, [17] = 52000, [18] = 52000, [19] = 52000,
		[20] = 52000 },
	[3] = { [5] = 5000, [6] = 8000, [7] = 12000, [8] = 16000, [9] = 22000,
		[10] = 28000, [11] = 34000, [12] = 40000, [13] = 45000, [14] = 50000,
		[15] = 53000, [16] = 55000, [17] = 55000, [18] = 55000, [19] = 55000,
		[20] = 55000 },
	[4] = { [5] = 5000, [6] = 10000, [7] = 14000, [8] = 19000, [9] = 25000,
		[10] = 31000, [11] = 38000, [12] = 45000, [13] = 50000, [14] = 54000,
		[15] = 56000, [16] = 58000, [17] = 58000, [18] = 58000, [19] = 58000,
		[20] = 58000 },
};

/*
 * The E cluster shares the die: six E cores at E7 raised TCMz about 7 C within
 * a second on their own and about 7 C more on top of four busy P cores, so
 * each busy E core adds 2 C to the prediction at E6 and above and 1 C below.
 */
static inline int pg_e_heat(unsigned int e_busy, u32 e_khz)
{
	if (e_busy > PG_E_CPUS)
		e_busy = PG_E_CPUS;
	return (int)e_busy * (e_khz >= PG_E_HIGH_KHZ ? 2000 : 1000);
}

/* IEEE-754 single to milli-degrees C, for the positive readings TCMz returns. */
static inline int pg_float_to_mc(u32 bits, int *mc)
{
	int exp = (bits >> 23) & 0xff;
	u64 mant = (bits & 0x7fffff) | 0x800000;

	if (bits >> 31 || exp < 127 || exp > 134)	/* 1 C to below 256 C */
		return -1;
	*mc = (int)((mant * 1000) >> (150 - exp));
	return 0;
}

/*
 * Fast heat: how much of a P state's rise leaves the CPU die within two
 * seconds of the cluster returning to P4, by busy P cores. Measured on the
 * owner's Air with each state held 15 s, twice (cpu/j713/bench/settle.py,
 * docs/j713-cpu-boost.md). The rest of the rise, 0-12 C of slow creep, is
 * package heat that stays after the drop, so it belongs in the base.
 *
 * Every state from P5 to the first that reached the bench's 95 C abort is
 * measured, except P9, P11, P13 and P16 for one core. Each takes the lower of
 * the two runs, rounded down; a state not measured takes the next measured
 * state below it; then each row is made non-decreasing from the top down by
 * a running minimum, because one busy core asking for P20 runs P17 and measured less.
 * Where a state was measured for more busy cores, no entry for fewer is higher
 * (two cores at P5 take 2 C, three cores' 2.6 C rounded down), because the base uses the fewest
 * busy cores of the look-back while more may have joined. The base subtracts
 * these, so it can only err high.
 */
static const u32 pg_fast[PG_P_CPUS + 1][PG_TOP_LEVEL + 1] = {
	[1] = { [5] = 1000, [6] = 3000, [7] = 5000, [8] = 7000, [9] = 7000, [10] = 13000, [11] = 13000, [12] = 20000, [13] = 20000,
		[14] = 26000, [15] = 26000, [16] = 26000, [17] = 26000, [18] = 26000, [19] = 26000,
		[20] = 26000 },
	[2] = { [5] = 2000, [6] = 5000, [7] = 6000, [8] = 10000, [9] = 14000, [10] = 16000, [11] = 25000, [12] = 27000, [13] = 27000,
		[14] = 27000, [15] = 27000, [16] = 27000, [17] = 27000, [18] = 27000, [19] = 27000,
		[20] = 27000 },
	[3] = { [5] = 2000, [6] = 5000, [7] = 8000, [8] = 10000, [9] = 16000, [10] = 21000, [11] = 21000,
		[12] = 21000, [13] = 21000, [14] = 21000, [15] = 21000, [16] = 21000, [17] = 21000,
		[18] = 21000, [19] = 21000, [20] = 21000 },
	[4] = { [5] = 3000, [6] = 6000, [7] = 8000, [8] = 11000, [9] = 18000, [10] = 18000, [11] = 18000,
		[12] = 18000, [13] = 18000, [14] = 18000, [15] = 18000, [16] = 18000, [17] = 18000,
		[18] = 18000, [19] = 18000, [20] = 18000 },
};

/*
 * How far back the base looks for the lowest P state and the fewest busy P
 * cores: PG_RECENT_MS of whole slow ticks kept, plus the current tick, so
 * 750-1000 ms at the default 250 ms period. The fast heat comes and goes with
 * a time constant of about 0.25 s, so after 750 ms less than 5% of an earlier
 * state's fast heat is left; a dip longer ago no longer holds the base up.
 */
#define PG_RECENT_MS 750
#define PG_RECENT_MAX 16	/* ticks kept: 750 ms at the shortest period, 50 ms */

/* Keep a finished tick's lowest state and busy count in the ring of n; returns the next position. */
static inline unsigned int pg_keep(u32 *recent, u32 *recent_busy, unsigned int pos, unsigned int n,
				   u32 tick, u32 tick_busy)
{
	recent[pos] = tick;
	recent_busy[pos] = tick_busy;
	return (pos + 1) % n;
}

/* The lowest of the n values kept for recent ticks and the current tick's. */
static inline u32 pg_lowest(const u32 *recent, unsigned int n, u32 now)
{
	unsigned int i;

	for (i = 0; i < n; i++)
		if (recent[i] < now)
			now = recent[i];
	return now;
}

/*
 * The base: what the die would read with the cluster back at P4. Heat is
 * subtracted only when a P core was busy through the window that the new
 * reading covers (pg_window_busy), and then only the fast heat (pg_fast) of
 * the fewest busy P cores and the lowest P state seen in the last
 * PG_RECENT_MS. Any other window subtracts nothing, so the base errs high.
 */
static inline int pg_base(int reading_mc, bool window_busy, unsigned int min_busy, u32 recent_level)
{
	if (!window_busy || recent_level <= PG_BASE_LEVEL || recent_level > PG_TOP_LEVEL)
		return reading_mc;
	if (min_busy == 0)
		min_busy = 1;
	if (min_busy > PG_P_CPUS)
		min_busy = PG_P_CPUS;
	return reading_mc - (int)pg_fast[min_busy][recent_level];
}

/* A P core not idle for at least 90% of the window: the cluster ran loaded throughout. */
static inline bool pg_window_busy(const u64 *d_idle, const u64 *d_wall, unsigned int n)
{
	unsigned int i;

	for (i = 0; i < n; i++)
		if (d_wall[i] && d_idle[i] * 10 <= d_wall[i])
			return true;
	return false;
}

/*
 * The busy counts to hold after a new sample. A count that rose takes effect at
 * once; a lower one waits until the hold has expired. Returns true when the
 * hold restarts.
 */
static inline bool pg_hold(unsigned int *busy, unsigned int *e_busy, unsigned int held, unsigned int e_held,
			   bool expired)
{
	if (*busy >= held && *e_busy >= e_held)
		return true;
	if (expired)
		return false;
	if (*busy < held)
		*busy = held;
	if (*e_busy < e_held)
		*e_busy = e_held;
	return *busy > held || *e_busy > e_held;
}

/* Busy P cores: more than half of the interval not idle. */
static inline unsigned int pg_busy_count(const u64 *d_idle, const u64 *d_wall, unsigned int n)
{
	unsigned int i, busy = 0;

	for (i = 0; i < n; i++)
		if (d_wall[i] && d_idle[i] * 2 < d_wall[i])
			busy++;
	return busy;
}

/*
 * The highest P state whose measured rise for this many busy P cores, plus
 * the E cluster's heat, keeps base + rise at or below the limit; P4 when hot.
 * level_khz[] marks which states the policy offers (0 for none).
 */
static inline u32 pg_allowed(int base_mc, unsigned int busy, int e_heat_mc, int limit_mc, bool hot,
			     const u32 *level_khz)
{
	u32 level;

	if (hot)
		return PG_BASE_LEVEL;
	if (busy == 0)
		busy = 1;
	if (busy > PG_P_CPUS)
		busy = PG_P_CPUS;
	for (level = PG_TOP_LEVEL; level > PG_BASE_LEVEL; level--)
		if (level_khz[level] && base_mc + e_heat_mc + (int)pg_step[busy][level] <= limit_mc)
			return level;
	return PG_BASE_LEVEL;
}

#endif
