// SPDX-License-Identifier: GPL-2.0
/*
 * Host contract test for j713_hp_plan.h: builds every plan and every stage
 * and checks the shared-register contract, the two-direction policy, the
 * recipe (against the speaker stack's own builders in m4_t8132_rx.h, read
 * only), the port restore and ADMAC channel 5's words. No hardware.
 *
 * Build with -I ../ -I ../../../kernel (the Makefile and
 * tests/test_j713_headphones.py do), so a mutated copy of the plan header
 * can be tested in its place.
 */
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint32_t u32;
typedef uint64_t u64;
#define BIT(n)		(1U << (n))

#include "j713_hp_plan.h"
#include "m4_t8132_rx.h"

static int failures;

#define CHECK(cond, ...) do {						\
	if (!(cond)) {							\
		failures++;						\
		printf("FAIL %s:%d: ", __FILE__, __LINE__);		\
		printf(__VA_ARGS__);					\
		printf("\n");						\
	}								\
} while (0)

/* check_contract() kind of a j713_hp_stage() plan; steps are the others. */
#define STAGE		J713_HP_NSTEPS

static bool in(u32 off, u32 lo, u32 end)
{
	return off >= lo && off < end;
}

static bool own_window(u32 off)
{
	return in(off, J713_HP_CLK, J713_HP_CLK_END) ||
	       in(off, J713_HP_RXS, J713_HP_RXS_END) ||
	       in(off, J713_HP_TXS, J713_HP_TXS_END) ||
	       in(off, J713_HP_ADP, J713_HP_ADP_END);
}

static bool is_staged(u32 off)
{
	return off == J713_HP_SYNC_OMUX_ENABLE_SET || off == J713_HP_SYNC_OMUX_ENABLE_CLR ||
	       off == J713_HP_RX_ENABLE_SET || off == J713_HP_RX_ENABLE_CLR ||
	       off == J713_HP_TX_ENABLE_SET || off == J713_HP_TX_ENABLE_CLR;
}

/* Clock, frame and both serializers' format and routing: PROGRAM only. */
static bool is_timing(u32 off)
{
	return off == J713_HP_CLOCK_GEN_CONFIG || off == J713_HP_SYNC_CONFIG ||
	       off == J713_HP_SYNC_HI || off == J713_HP_SYNC_LO ||
	       off == J713_HP_TXS_CONF || off == J713_HP_TXS_BITSTART ||
	       in(off, J713_HP_TXS_SLOTMASK(0), J713_HP_TXS_SLOTMASK(4)) ||
	       in(off, J713_HP_RXS_CONF, J713_HP_RXS_LOOPBACK + 4);
}

/* Rules that hold for every step, every port boot value and every stage. */
static void check_contract(int kind, const char *name, const struct j713_hp_plan *p)
{
	u32 i;

	CHECK(!p->overflow && p->count > 0 && p->count <= J713_HP_PLAN_MAX,
	      "%s: count %u%s", name, p->count, p->overflow ? ", overflow" : "");
	for (i = 0; i < p->count; i++) {
		const struct j713_hp_write *w = &p->writes[i];
		u32 off = w->offset;

		CHECK(!(off & 3), "%s[%u]: unaligned %#x", name, i, off);
		CHECK(off < J713_HP_MCA_SIZE, "%s[%u]: %#x outside the MCA map", name, i, off);
		CHECK((w->value & ~w->mask) == 0, "%s[%u]: value %#x outside mask %#x",
		      name, i, w->value, w->mask);
		if (off < J713_HP_GLOBAL_SIZE) {
			bool ok = false;

			/* Shared registers are never read-modify-written. */
			CHECK(w->mask == ~0U, "%s[%u]: shared %#x written with mask %#x",
			      name, i, off, w->mask);
			if (off == J713_HP_SYNC_OMUX_SEL(J713_HP_IDX))
				ok = kind == J713_HP_PROGRAM && w->value == 3;
			else if (is_staged(off))
				ok = kind == STAGE && w->value == BIT(2);
			else if (off == J713_HP_ALIGNED_ENABLE_TRIGGER)
				ok = kind == STAGE && w->value == 1;
			CHECK(ok, "%s[%u]: forbidden shared write %#x := %#x",
			      name, i, off, w->value);
			CHECK(off != J713_HP_SYNC_OMUX_SEL(0), "%s[%u]: writes the speaker's 0x100",
			      name, i);
		} else {
			CHECK(own_window(off),
			      "%s[%u]: %#x is not cluster 2, RX/TX serdes 2 or adapter 2",
			      name, i, off);
		}
		if (is_timing(off))
			CHECK(kind == J713_HP_PROGRAM,
			      "%s[%u]: timing register %#x outside PROGRAM", name, i, off);
	}
	if (kind == STAGE && p->count)
		CHECK(p->writes[p->count - 1].offset == J713_HP_ALIGNED_ENABLE_TRIGGER,
		      "%s: does not end with the trigger", name);
}

static void check_golden(const char *name, const struct j713_hp_plan *p,
			 const struct j713_hp_write *want, u32 n)
{
	u32 i;

	CHECK(p->count == n, "%s: %u writes, want %u", name, p->count, n);
	for (i = 0; i < n && i < p->count; i++)
		CHECK(!memcmp(&p->writes[i], &want[i], sizeof(want[i])),
		      "%s[%u]: {%#x, %#x, %#x}, want {%#x, %#x, %#x}", name, i,
		      p->writes[i].offset, p->writes[i].mask, p->writes[i].value,
		      want[i].offset, want[i].mask, want[i].value);
}

/* The proven recipe, write for write. */
static void check_program(const struct j713_hp_plan *p)
{
	static const struct j713_hp_write want[] = {
		{ 0x2204, ~0U, 0x100 },
		{ 0x2224, ~0U, 0x003 },
		{ 0x2228, ~0U, 31 },
		{ 0x222c, ~0U, 31 },
		{ 0x0108, ~0U, 3 },
		{ 0x6204, ~0U, 0x37101 },
		{ 0x6208, ~0U, 1 },
		{ 0x620c, ~0U, ~0U },
		{ 0x6210, ~0U, ~0x3U },
		{ 0x6214, ~0U, ~0U },
		{ 0x6218, ~0U, ~0x3U },
		{ 0x4214, 0x3f, 0x4 },
		{ 0x4218, ~0U, 0 },
		{ 0x4204, ~0U, 0x3b500 },
		{ 0x4208, ~0U, 1 },
		{ 0x420c, ~0U, ~0U },
		{ 0x4210, ~0U, ~0x1U },
		{ 0x8200, 0x807f7f7fU, 0x00100000 },
		{ 0x8204, 0x11, 0x11 },
		{ 0x2220, 0x1, 0x1 },
		{ 0x2200, 0x1, 0x1 },
	};

	check_golden("PROGRAM", p, want, sizeof(want) / sizeof(want[0]));
	CHECK(!(J713_HP_TX_CONF & 0x400), "TX conf sets BCLK_POL");
	CHECK(!(J713_HP_TX_CONF & 0x800), "TX conf sets LSB_FIRST");
	CHECK(J713_HP_RX_CONF & 0x400, "RX conf lacks BCLK_POL");
	CHECK(!(J713_HP_RX_CONF & 0x800), "RX conf sets LSB_FIRST");
	CHECK(!(J713_HP_SYNC_SEL & BIT(9)), "sync config sets bit 9 (frame invert)");
}

/* The RX serializer and the FIFO word against the speaker's canonical builders. */
static void check_oracle(const struct j713_hp_plan *p)
{
	const struct m4_t8132_rx_format f = {
		.channels = 0x1, .width = 32, .delay = 1, .i2s_sources = 0x4,
		.loopback_sources = 0, .sync_type = 2, .sync_index = 2,
		.falling_edge = false, .lsb_first = false,
	};
	struct m4_t8132_write tx_fifo, rx_fifo;
	const struct j713_hp_write *fifo = NULL;
	struct m4_t8132_rx_plan rx;
	u32 i, n = 0;

	CHECK(!m4_t8132_build_rx(&f, &rx), "m4_t8132_build_rx refused the format");
	for (i = 0; i < p->count; i++) {
		const struct j713_hp_write *w = &p->writes[i];

		if (w->offset == J713_HP_ADP_FIFO)
			fifo = w;
		if (!in(w->offset, 0x4200, 0x4300))
			continue;
		if (n < 6)
			CHECK(w->offset == 0x4200 + rx.writes[n].offset &&
			      w->mask == rx.writes[n].mask && w->value == rx.writes[n].value,
			      "RX write %u {%#x, %#x, %#x} differs from m4_t8132_build_rx {+%#x, %#x, %#x}",
			      n, w->offset, w->mask, w->value, rx.writes[n].offset,
			      rx.writes[n].mask, rx.writes[n].value);
		n++;
	}
	CHECK(n == 6, "PROGRAM writes RX serializer 2 %u times, m4_t8132_build_rx 6", n);

	/* Capacity 32, as on the Air. */
	CHECK(!m4_t8132_build_tx_fifo(0x20000000, 1, 32, 16, false, false, &tx_fifo),
	      "m4_t8132_build_tx_fifo refused");
	CHECK(!m4_t8132_build_rx_fifo(0x20000000, 1, 32, false, false, &rx_fifo),
	      "m4_t8132_build_rx_fifo refused");
	CHECK(fifo && fifo->mask == (tx_fifo.mask | rx_fifo.mask) &&
	      fifo->value == (tx_fifo.value | rx_fifo.value),
	      "FIFO word {%#x, %#x}, builders {%#x, %#x}", fifo ? fifo->mask : 0,
	      fifo ? fifo->value : 0, tx_fifo.mask | rx_fifo.mask,
	      tx_fifo.value | rx_fifo.value);
}

static void run(u32 *file, const struct j713_hp_plan *p)
{
	u32 i;

	for (i = 0; i < p->count; i++) {
		const struct j713_hp_write *w = &p->writes[i];
		u32 *r = &file[w->offset / 4];

		*r = w->mask == ~0U ? w->value : (*r & ~w->mask) | w->value;
	}
}

/* Every final write of p is what the register now holds. */
static void check_final(const char *name, const u32 *file, const struct j713_hp_plan *p)
{
	u32 i;

	for (i = 0; i < p->count; i++)
		if (j713_hp_final(p, i))
			CHECK((file[p->writes[i].offset / 4] & p->writes[i].mask) ==
			      p->writes[i].value, "%s[%u] not final", name, i);
}

/* Register file for the windows the MCA plans touch; unwritten regs keep their seed. */
static u32 regs[J713_HP_MCA_SIZE / 4];

static void check_port(const u32 boot[3])
{
	struct j713_hp_plan prog, open, down;

	memset(regs, 0, sizeof(regs));
	regs[J713_HP_PORT_ENABLES / 4] = boot[0];
	regs[J713_HP_PORT_CLOCK_SEL / 4] = boot[1];
	regs[J713_HP_PORT_DATA_SEL / 4] = boot[2];
	regs[J713_HP_ADP_FIFO / 4] = 0x20000000;	/* capacity 32, as on the Air */

	CHECK(!j713_hp_plan(J713_HP_PROGRAM, boot, &prog), "PROGRAM failed");
	CHECK(!j713_hp_plan(J713_HP_PORT_OPEN, boot, &open), "PORT_OPEN failed");
	CHECK(!j713_hp_plan(J713_HP_TEARDOWN, boot, &down), "TEARDOWN failed");

	run(regs, &prog);
	run(regs, &open);
	CHECK(regs[J713_HP_PORT_ENABLES / 4] == (boot[0] | 14), "open: enables %#x",
	      regs[J713_HP_PORT_ENABLES / 4]);
	CHECK(regs[J713_HP_PORT_CLOCK_SEL / 4] == ((boot[1] & ~0xf00U) | 0x300),
	      "open: clock sel %#x", regs[J713_HP_PORT_CLOCK_SEL / 4]);
	CHECK(regs[J713_HP_PORT_DATA_SEL / 4] == (boot[2] | 4), "open: data sel %#x",
	      regs[J713_HP_PORT_DATA_SEL / 4]);
	CHECK(regs[J713_HP_ADP_FIFO / 4] == 0x20100000, "FIFO word %#x",
	      regs[J713_HP_ADP_FIFO / 4]);
	CHECK((regs[J713_HP_ADP_FIFO_EN / 4] & 0x11) == 0x11, "PROGRAM: FIFO enables %#x",
	      regs[J713_HP_ADP_FIFO_EN / 4]);
	check_final("PROGRAM", regs, &prog);
	check_final("PORT_OPEN", regs, &open);

	run(regs, &down);
	CHECK(regs[J713_HP_PORT_ENABLES / 4] == boot[0], "teardown: enables %#x != %#x",
	      regs[J713_HP_PORT_ENABLES / 4], boot[0]);
	CHECK(regs[J713_HP_PORT_CLOCK_SEL / 4] == boot[1], "teardown: clock sel %#x != %#x",
	      regs[J713_HP_PORT_CLOCK_SEL / 4], boot[1]);
	CHECK(regs[J713_HP_PORT_DATA_SEL / 4] == boot[2], "teardown: data sel %#x != %#x",
	      regs[J713_HP_PORT_DATA_SEL / 4], boot[2]);
	CHECK(!(regs[J713_HP_SYNC_STATUS / 4] & 1) && !(regs[J713_HP_MCLK_STATUS / 4] & 1) &&
	      !(regs[J713_HP_ADP_FIFO_EN / 4] & 0x11), "teardown leaves an enable on");
}

/* The merged START/STOP and the RX equivalents, offsets and values. */
static void check_stage_golden(void)
{
	static const struct j713_hp_write start[] = {
		{ 0x144, ~0U, 4 }, { 0x164, ~0U, 4 }, { 0x170, ~0U, 1 },
		{ 0x140, ~0U, 4 }, { 0x160, ~0U, 4 }, { 0x170, ~0U, 1 },
	};
	static const struct j713_hp_write stop[] = {
		{ 0x144, ~0U, 4 }, { 0x164, ~0U, 4 }, { 0x170, ~0U, 1 },
	};
	static const struct j713_hp_write rx_start[] = {
		{ 0x144, ~0U, 4 }, { 0x154, ~0U, 4 }, { 0x170, ~0U, 1 },
		{ 0x140, ~0U, 4 }, { 0x150, ~0U, 4 }, { 0x170, ~0U, 1 },
	};
	static const struct j713_hp_write rx_start_beside_tx[] = {
		{ 0x154, ~0U, 4 }, { 0x170, ~0U, 1 }, { 0x150, ~0U, 4 }, { 0x170, ~0U, 1 },
	};
	static const struct j713_hp_write rx_stop_beside_tx[] = {
		{ 0x154, ~0U, 4 }, { 0x170, ~0U, 1 },
	};
	struct j713_hp_plan p;

	CHECK(j713_hp_stage(0, J713_HP_TX, true, &p) == BIT(0), "TX start: next mask");
	check_golden("TX start (merged START)", &p, start, 6);
	CHECK(j713_hp_stage(BIT(0), J713_HP_TX, false, &p) == 0, "TX stop: next mask");
	check_golden("TX stop (merged STOP)", &p, stop, 3);
	CHECK(j713_hp_stage(0, J713_HP_RX, true, &p) == BIT(1), "RX start: next mask");
	check_golden("RX start alone", &p, rx_start, 6);
	CHECK(j713_hp_stage(BIT(0), J713_HP_RX, true, &p) == 3, "RX start beside TX: next mask");
	check_golden("RX start beside TX", &p, rx_start_beside_tx, 4);
	CHECK(j713_hp_stage(3, J713_HP_RX, false, &p) == BIT(0), "RX stop beside TX: next mask");
	check_golden("RX stop beside TX", &p, rx_stop_beside_tx, 2);
	CHECK(j713_hp_stage(BIT(1), (enum j713_hp_dir)2, true, &p) == BIT(1) && p.count == 0,
	      "unknown direction staged");
}

/* Every stage, exhaustively over active x dir x on/off. */
static void check_stage(void)
{
	u32 active, dir, on, i;

	for (active = 0; active < 4; active++)
		for (dir = 0; dir < 2; dir++)
			for (on = 0; on < 2; on++) {
				const struct j713_hp_direction *d = &j713_hp_dirs[dir];
				const struct j713_hp_direction *o = &j713_hp_dirs[!dir];
				bool other = active & BIT(!dir);
				u32 omux = 0, set = 0, clr = 0, foreign = 0, triggers = 0;
				bool order = true;
				struct j713_hp_plan p;
				char name[48];
				u32 next;

				next = j713_hp_stage(active, dir, on, &p);
				snprintf(name, sizeof(name), "stage(active %u, %s, %s)", active,
					 d->name, on ? "on" : "off");
				check_contract(STAGE, name, &p);
				CHECK(next == (on ? active | BIT(dir) : active & ~BIT(dir)),
				      "%s: next mask %u", name, next);
				for (i = 0; i < p.count; i++) {
					u32 off = p.writes[i].offset;

					if (off == J713_HP_SYNC_OMUX_ENABLE_CLR || off == d->enable_clr) {
						omux += off == J713_HP_SYNC_OMUX_ENABLE_CLR;
						clr += off == d->enable_clr;
						order &= !triggers;	/* clears before the first trigger */
					} else if (off == J713_HP_SYNC_OMUX_ENABLE_SET ||
						   off == d->enable_set) {
						omux += off == J713_HP_SYNC_OMUX_ENABLE_SET;
						set += off == d->enable_set;
						order &= triggers == 1;	/* sets between the triggers */
					} else if (off == o->enable_set || off == o->enable_clr) {
						foreign++;
					} else if (off == J713_HP_ALIGNED_ENABLE_TRIGGER) {
						triggers++;
					}
				}
				CHECK(!foreign, "%s: writes the other direction's enable", name);
				CHECK(omux == (other ? 0 : on ? 2 : 1), "%s: %u OMUX writes", name, omux);
				CHECK(clr == 1 && set == on, "%s: %u clears, %u sets", name, clr, set);
				CHECK(triggers == 1 + on, "%s: %u triggers", name, triggers);
				CHECK(order, "%s: staged writes out of order", name);
			}
}

/*
 * Staged-enable block with merge semantics (SET/CLR accumulate, the trigger
 * applies them), seeded with the speaker's bit 0 on everywhere.
 */
struct staging {
	u32 omux, rx, tx;
	u32 set[3], clr[3];
};

static void stage_run(struct staging *s, const struct j713_hp_plan *p)
{
	u32 *state[3] = { &s->omux, &s->rx, &s->tx };
	u32 i, k;

	for (i = 0; i < p->count; i++) {
		u32 off = p->writes[i].offset, v = p->writes[i].value;

		switch (off) {
		case 0x140: s->set[0] |= v; break;
		case 0x144: s->clr[0] |= v; break;
		case 0x150: s->set[1] |= v; break;
		case 0x154: s->clr[1] |= v; break;
		case 0x160: s->set[2] |= v; break;
		case 0x164: s->clr[2] |= v; break;
		case 0x170:
			for (k = 0; k < 3; k++) {
				*state[k] = (*state[k] & ~s->clr[k]) | s->set[k];
				s->set[k] = s->clr[k] = 0;
			}
			break;
		}
	}
}

static void check_hw(const char *name, const struct staging *s, u32 active)
{
	CHECK((s->tx & 4) == (active & BIT(0) ? 4U : 0U), "%s: TX bit 2 %#x, active %u",
	      name, s->tx, active);
	CHECK((s->rx & 4) == (active & BIT(1) ? 4U : 0U), "%s: RX bit 2 %#x, active %u",
	      name, s->rx, active);
	CHECK((s->omux & 4) == (active ? 4U : 0U), "%s: OMUX bit 2 %#x, active %u",
	      name, s->omux, active);
	CHECK(s->omux == ((s->omux & 4) | 1) && s->rx == ((s->rx & 4) | 1) &&
	      s->tx == ((s->tx & 4) | 1), "%s: speaker bit 0 or another bit changed", name);
}

/* OMUX first on, last off, in every start/stop order; a repeated stop disturbs nothing. */
static void check_stage_orders(void)
{
	/* +1 start, -1 stop; TX = 0, RX = 1 */
	static const int orders[6][4][2] = {
		{ { 0, 1 }, { 1, 1 }, { 0, 0 }, { 1, 0 } },
		{ { 0, 1 }, { 1, 1 }, { 1, 0 }, { 0, 0 } },
		{ { 0, 1 }, { 0, 0 }, { 1, 1 }, { 1, 0 } },
		{ { 1, 1 }, { 0, 1 }, { 0, 0 }, { 1, 0 } },
		{ { 1, 1 }, { 0, 1 }, { 1, 0 }, { 0, 0 } },
		{ { 1, 1 }, { 1, 0 }, { 0, 1 }, { 0, 0 } },
	};
	u32 o, e, dir;

	for (o = 0; o < 6; o++) {
		struct staging s = { .omux = 1, .rx = 1, .tx = 1 };
		u32 active = 0;

		for (e = 0; e < 4; e++) {
			struct j713_hp_plan p;
			char name[48];

			active = j713_hp_stage(active, orders[o][e][0], orders[o][e][1], &p);
			stage_run(&s, &p);
			snprintf(name, sizeof(name), "order %u step %u", o, e);
			check_hw(name, &s, active);
			for (dir = 0; dir < 2; dir++) {
				struct staging again = s;

				if (active & BIT(dir))
					continue;
				CHECK(j713_hp_stage(active, dir, false, &p) == active,
				      "%s: stop again changed the mask", name);
				stage_run(&again, &p);
				snprintf(name, sizeof(name), "order %u step %u, %s stopped again",
					 o, e, j713_hp_dirs[dir].name);
				check_hw(name, &again, active);
			}
		}
	}
}

/* The prepare decision, exhaustively: dir x prepared x active x clocked x recipe_ok. */
static void check_prepare(void)
{
	u32 dir, prepared, active, clocked, ok, cases = 0;

	for (dir = 0; dir < 2; dir++)
		for (prepared = 0; prepared < 4; prepared++)
			for (active = 0; active < 4; active++)
				for (clocked = 0; clocked < 2; clocked++)
					for (ok = 0; ok < 2; ok++) {
						struct j713_hp_prep a = j713_hp_prepare(dir, prepared,
							active, clocked, ok);
						u32 other = BIT(!dir);
						bool rx_beside_tx = dir == 1 && (active & BIT(0));
						char name[80];

						cases++;
						snprintf(name, sizeof(name),
							 "prepare(%s, prepared %u, active %u, %s, recipe %s)",
							 j713_hp_dirs[dir].name, prepared, active,
							 clocked ? "clocked" : "idle", ok ? "ok" : "bad");
						CHECK(!(a.teardown || a.program) ||
						      (a.guard_other && !(active & other)),
						      "%s: reprograms under the other direction", name);
						CHECK(!a.teardown || (clocked && !(prepared & other)),
						      "%s: tears down a cluster the other holds", name);
						CHECK(!a.program || !clocked || a.teardown,
						      "%s: programs a clocked cluster", name);
						CHECK(!(a.reset && rx_beside_tx),
						      "%s: resets RX while TX runs", name);
						CHECK(!a.busy || !(a.guard_other || a.teardown ||
								   a.program || a.reset),
						      "%s: busy and acting", name);
						CHECK(a.busy || a.program || (clocked && ok),
						      "%s: leaves the recipe unverified", name);
						CHECK(a.busy || a.reset == !rx_beside_tx,
						      "%s: skips a serializer reset", name);
						if (!((prepared | active) & other))
							CHECK(!a.busy, "%s: busy with the other direction off",
							      name);
					}
	CHECK(cases == 128, "%u prepare cases", cases);
}

static void check_rx_dma(void)
{
	static const struct j713_hp_write want[] = {
		{ 0x40, 0xff, 0x02 },
		{ 0x54, ~0U, 0x180 },
	};
	static const u32 seeds[][2] = {
		{ 0xf00, 0 },			/* at boot */
		{ 0xf02, 0x180 },		/* after a first prepare */
		{ 0xf01, 0x00c00060 },
		{ 0xabcdef13, 0xffffffff },
	};
	u32 file[J713_HP_RX_DMA_SIZE / 4];
	struct j713_hp_plan p;
	u32 s, i;

	CHECK(J713_HP_RX_DMA_PHYS == 0x39380c400ULL, "ch5 window %#llx",
	      (unsigned long long)J713_HP_RX_DMA_PHYS);
	CHECK(J713_HP_RX_DMA_CHAN == 5, "RX channel %d", J713_HP_RX_DMA_CHAN);
	CHECK(!j713_hp_rx_dma_plan(&p), "ch5 plan failed");
	check_golden("ch5", &p, want, 2);
	CHECK(j713_hp_rx_dma_plan(NULL) == -EINVAL, "NULL out accepted");
	for (i = 0; i < p.count; i++) {
		CHECK(p.writes[i].offset < J713_HP_RX_DMA_SIZE && !(p.writes[i].offset & 3),
		      "ch5[%u]: %#x outside the window", i, p.writes[i].offset);
		CHECK(j713_hp_final(&p, i) && !j713_hp_write_only(p.writes[i].offset),
		      "ch5[%u]: not verifiable", i);
	}
	for (s = 0; s < sizeof(seeds) / sizeof(seeds[0]); s++) {
		memset(file, 0, sizeof(file));
		file[0x40 / 4] = seeds[s][0];
		file[0x54 / 4] = seeds[s][1];
		run(file, &p);
		CHECK(file[0x40 / 4] == ((seeds[s][0] & ~0xffU) | 0x02),
		      "ch5 seed %u: BUS_WIDTH %#x", s, file[0x40 / 4]);
		CHECK(file[0x54 / 4] == 0x180, "ch5 seed %u: FIFOCTL %#x", s, file[0x54 / 4]);
		check_final("ch5", file, &p);
	}
	memset(file, 0, sizeof(file));
	file[0x40 / 4] = 0xf00;
	run(file, &p);
	CHECK(file[0x40 / 4] == 0xf02 && file[0x54 / 4] == 0x180,
	      "ch5 from reset: %#x/%#x, want 0xf02/0x180", file[0x40 / 4], file[0x54 / 4]);
}

static void check_tables(void)
{
	const struct j713_hp_direction *tx = &j713_hp_dirs[J713_HP_TX];
	const struct j713_hp_direction *rx = &j713_hp_dirs[J713_HP_RX];
	u32 off, n = 0;

	CHECK(J713_HP_TX == 0 && J713_HP_RX == 1, "directions are not ALSA's stream numbers");
	CHECK(!strcmp(tx->name, "TX") && tx->enable_set == 0x160 && tx->enable_clr == 0x164 &&
	      tx->active == 0x168 && tx->serializer == 0x6200 && tx->fifo_en == 0x1 &&
	      tx->channels == 2, "TX table entry");
	CHECK(!strcmp(rx->name, "RX") && rx->enable_set == 0x150 && rx->enable_clr == 0x154 &&
	      rx->active == 0x158 && rx->serializer == 0x4200 && rx->fifo_en == 0x10 &&
	      rx->channels == 1, "RX table entry");
	CHECK(J713_HP_SER_CONF == 0x4 && J713_HP_SER_CONTROL == 0 &&
	      J713_HP_CONF_SYNC_SEL == 0x70000, "serializer reset registers");

	for (off = 0; off < J713_HP_MCA_SIZE; off += 4) {
		bool want = off == 0x140 || off == 0x144 || off == 0x150 || off == 0x154 ||
			    off == 0x160 || off == 0x164 || off == 0x170;

		CHECK(j713_hp_write_only(off) == want, "write-only %#x", off);
		n += want;
	}
	CHECK(n == 7, "%u write-only registers", n);
}

static void check_errors(void)
{
	const u32 boot[3] = { 0, 0, 0 };
	struct j713_hp_plan p = { .count = 0 }, out;
	u32 i;

	CHECK(j713_hp_plan(J713_HP_NSTEPS, boot, &out) == -EINVAL, "bad step accepted");
	CHECK(j713_hp_plan(J713_HP_PROGRAM, NULL, &out) == -EINVAL, "NULL port_boot accepted");
	CHECK(j713_hp_plan(J713_HP_PROGRAM, boot, NULL) == -EINVAL, "NULL out accepted");
	CHECK(!strcmp(j713_hp_step_name(J713_HP_NSTEPS), "?"), "bad step named");

	for (i = 0; i < J713_HP_PLAN_MAX; i++)
		j713_hp_add(&p, 0x2000 + 4 * i, ~0U, i);
	CHECK(!p.overflow && p.count == J713_HP_PLAN_MAX, "full plan: count %u", p.count);
	j713_hp_add(&p, 0x2000, ~0U, 0);
	CHECK(p.overflow && p.count == J713_HP_PLAN_MAX, "overflow: count %u", p.count);
	memset(&out, 0, sizeof(out));
	CHECK(j713_hp_finish(&p, &out) == -E2BIG && out.count == 0,
	      "overflowed plan handed out");
}

int main(void)
{
	static const u32 boots[][3] = {
		{ 0, 0, 0 },			/* the Air's port 2 at probe */
		{ 0x1, 0x10f, 0x8 },
		{ 0xfffffff1, 0xfffff0ff, 0xfffffffb },
		{ 0xffffffff, 0xffffffff, 0xffffffff },
	};
	struct j713_hp_plan p;
	u32 b;
	int s;

	for (b = 0; b < sizeof(boots) / sizeof(boots[0]); b++) {
		for (s = 0; s < J713_HP_NSTEPS; s++) {
			CHECK(!j713_hp_plan(s, boots[b], &p), "%s failed", j713_hp_step_name(s));
			check_contract(s, j713_hp_step_name(s), &p);
		}
		check_port(boots[b]);
	}
	CHECK(!j713_hp_plan(J713_HP_PROGRAM, boots[0], &p), "PROGRAM failed");
	check_program(&p);
	check_oracle(&p);
	check_stage_golden();
	check_stage();
	check_stage_orders();
	check_prepare();
	check_rx_dma();
	check_tables();
	check_errors();

	if (failures) {
		printf("plan_test: %d failure(s)\n", failures);
		return 1;
	}
	printf("plan_test: all checks passed\n");
	return 0;
}
