// SPDX-License-Identifier: GPL-2.0
/*
 * Host contract test for j713_hp_plan.h: builds every plan and checks the
 * shared-register contract, the recipe and the port restore. No hardware.
 */
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint32_t u32;
#define BIT(n)		(1U << (n))

#include "../j713_hp_plan.h"

static int failures;

#define CHECK(cond, ...) do {						\
	if (!(cond)) {							\
		failures++;						\
		printf("FAIL %s:%d: ", __FILE__, __LINE__);		\
		printf(__VA_ARGS__);					\
		printf("\n");						\
	}								\
} while (0)

static bool in(u32 off, u32 lo, u32 end)
{
	return off >= lo && off < end;
}

static bool own_window(u32 off)
{
	return in(off, J713_HP_CLK, J713_HP_CLK_END) ||
	       in(off, J713_HP_TXS, J713_HP_TXS_END) ||
	       in(off, J713_HP_ADP, J713_HP_ADP_END);
}

static bool is_set(u32 off)
{
	return off == J713_HP_SYNC_OMUX_ENABLE_SET || off == J713_HP_TX_ENABLE_SET;
}

static bool is_clr(u32 off)
{
	return off == J713_HP_SYNC_OMUX_ENABLE_CLR || off == J713_HP_TX_ENABLE_CLR;
}

static bool is_timing(u32 off)
{
	return off == J713_HP_CLOCK_GEN_CONFIG || off == J713_HP_SYNC_CONFIG ||
	       off == J713_HP_SYNC_HI || off == J713_HP_SYNC_LO ||
	       off == J713_HP_TXS_CONF || off == J713_HP_TXS_BITSTART ||
	       in(off, J713_HP_TXS_SLOTMASK(0), J713_HP_TXS_SLOTMASK(4));
}

/* Rules that hold for every step and every port boot value. */
static void check_contract(enum j713_hp_step step, const struct j713_hp_plan *p)
{
	const char *name = j713_hp_step_name(step);
	u32 i;

	CHECK(p->count > 0 && p->count <= J713_HP_PLAN_MAX, "%s: count %u", name, p->count);
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
				ok = step == J713_HP_PROGRAM && w->value == 3;
			else if (is_set(off))
				ok = step == J713_HP_START && w->value == J713_HP_BIT;
			else if (is_clr(off))
				ok = (step == J713_HP_START || step == J713_HP_STOP) &&
				     w->value == J713_HP_BIT;
			else if (off == J713_HP_ALIGNED_ENABLE_TRIGGER)
				ok = (step == J713_HP_START || step == J713_HP_STOP) &&
				     w->value == 1;
			CHECK(ok, "%s[%u]: forbidden shared write %#x := %#x",
			      name, i, off, w->value);
			CHECK(off != J713_HP_SYNC_OMUX_SEL(0), "%s[%u]: writes the speaker's 0x100",
			      name, i);
			CHECK(off != J713_HP_RX_ENABLE_SET && off != J713_HP_RX_ENABLE_CLR,
			      "%s[%u]: writes RX enable %#x", name, i, off);
		} else {
			CHECK(own_window(off), "%s[%u]: %#x is not cluster 2, TX serdes 2 or adapter 2",
			      name, i, off);
		}
		if (is_timing(off))
			CHECK(step == J713_HP_PROGRAM, "%s[%u]: timing register %#x outside PROGRAM",
			      name, i, off);
	}
	if (step == J713_HP_START || step == J713_HP_STOP)
		CHECK(p->writes[p->count - 1].offset == J713_HP_ALIGNED_ENABLE_TRIGGER,
		      "%s: does not end with the trigger", name);
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
		{ 0x8200, 0x807f007fU, 0x00100000 },
		{ 0x8204, 0x1, 0x1 },
		{ 0x2220, 0x1, 0x1 },
		{ 0x2200, 0x1, 0x1 },
	};
	u32 i, n = sizeof(want) / sizeof(want[0]);

	CHECK(p->count == n, "PROGRAM: %u writes, want %u", p->count, n);
	for (i = 0; i < n && i < p->count; i++)
		CHECK(!memcmp(&p->writes[i], &want[i], sizeof(want[i])),
		      "PROGRAM[%u]: {%#x, %#x, %#x}, want {%#x, %#x, %#x}", i,
		      p->writes[i].offset, p->writes[i].mask, p->writes[i].value,
		      want[i].offset, want[i].mask, want[i].value);
	CHECK(!(J713_HP_TX_CONF & 0x400), "TX conf sets BCLK_POL");
	CHECK(!(J713_HP_TX_CONF & 0x800), "TX conf sets LSB_FIRST");
	CHECK(!(J713_HP_SYNC_SEL & BIT(9)), "sync config sets bit 9 (frame invert)");
}

static void check_sequences(void)
{
	static const u32 start[] = { 0x144, 0x164, 0x170, 0x140, 0x160, 0x170 };
	static const u32 stop[] = { 0x144, 0x164, 0x170 };
	const u32 boot[3] = { 0, 0, 0 };
	struct j713_hp_plan p;
	u32 i;

	CHECK(!j713_hp_plan(J713_HP_START, boot, &p), "START failed");
	CHECK(p.count == 6, "START: %u writes", p.count);
	for (i = 0; i < 6 && i < p.count; i++)
		CHECK(p.writes[i].offset == start[i], "START[%u]: %#x", i, p.writes[i].offset);
	CHECK(!j713_hp_plan(J713_HP_STOP, boot, &p), "STOP failed");
	CHECK(p.count == 3, "STOP: %u writes", p.count);
	for (i = 0; i < 3 && i < p.count; i++)
		CHECK(p.writes[i].offset == stop[i], "STOP[%u]: %#x", i, p.writes[i].offset);
	CHECK(j713_hp_plan(J713_HP_NSTEPS, boot, &p) == -EINVAL, "bad step accepted");
	CHECK(j713_hp_plan(J713_HP_PROGRAM, NULL, &p) == -EINVAL, "NULL port_boot accepted");
	CHECK(j713_hp_plan(J713_HP_PROGRAM, boot, NULL) == -EINVAL, "NULL out accepted");
	CHECK(j713_hp_write_only(0x140) && j713_hp_write_only(0x170) &&
	      !j713_hp_write_only(0x168) && !j713_hp_write_only(0x108),
	      "write-only set");
}

/* Register file for the windows the plans touch; reads of unwritten regs give seed values. */
static u32 regs[J713_HP_MCA_SIZE / 4];

static void run(const struct j713_hp_plan *p)
{
	u32 i;

	for (i = 0; i < p->count; i++) {
		const struct j713_hp_write *w = &p->writes[i];
		u32 *r = &regs[w->offset / 4];

		*r = w->mask == ~0U ? w->value : (*r & ~w->mask) | w->value;
	}
}

static void check_port(const u32 boot[3])
{
	struct j713_hp_plan prog, open, down;
	u32 i;

	memset(regs, 0, sizeof(regs));
	regs[J713_HP_PORT_ENABLES / 4] = boot[0];
	regs[J713_HP_PORT_CLOCK_SEL / 4] = boot[1];
	regs[J713_HP_PORT_DATA_SEL / 4] = boot[2];
	regs[J713_HP_ADP_FIFO / 4] = 0x20000000;	/* capacity 32, as on the Air */

	CHECK(!j713_hp_plan(J713_HP_PROGRAM, boot, &prog), "PROGRAM failed");
	CHECK(!j713_hp_plan(J713_HP_PORT_OPEN, boot, &open), "PORT_OPEN failed");
	CHECK(!j713_hp_plan(J713_HP_TEARDOWN, boot, &down), "TEARDOWN failed");

	run(&prog);
	run(&open);
	CHECK(regs[J713_HP_PORT_ENABLES / 4] == (boot[0] | 14), "open: enables %#x",
	      regs[J713_HP_PORT_ENABLES / 4]);
	CHECK(regs[J713_HP_PORT_CLOCK_SEL / 4] == ((boot[1] & ~0xf00U) | 0x300),
	      "open: clock sel %#x", regs[J713_HP_PORT_CLOCK_SEL / 4]);
	CHECK(regs[J713_HP_PORT_DATA_SEL / 4] == (boot[2] | 4), "open: data sel %#x",
	      regs[J713_HP_PORT_DATA_SEL / 4]);
	CHECK(regs[J713_HP_ADP_FIFO / 4] == 0x20100000, "FIFO word %#x",
	      regs[J713_HP_ADP_FIFO / 4]);
	/* Every final write in PROGRAM/PORT_OPEN is what the register now holds. */
	for (i = 0; i < prog.count; i++)
		if (j713_hp_final(&prog, i))
			CHECK((regs[prog.writes[i].offset / 4] & prog.writes[i].mask) ==
			      prog.writes[i].value, "PROGRAM[%u] not final", i);
	for (i = 0; i < open.count; i++)
		if (j713_hp_final(&open, i))
			CHECK((regs[open.writes[i].offset / 4] & open.writes[i].mask) ==
			      open.writes[i].value, "PORT_OPEN[%u] not final", i);

	run(&down);
	CHECK(regs[J713_HP_PORT_ENABLES / 4] == boot[0], "teardown: enables %#x != %#x",
	      regs[J713_HP_PORT_ENABLES / 4], boot[0]);
	CHECK(regs[J713_HP_PORT_CLOCK_SEL / 4] == boot[1], "teardown: clock sel %#x != %#x",
	      regs[J713_HP_PORT_CLOCK_SEL / 4], boot[1]);
	CHECK(regs[J713_HP_PORT_DATA_SEL / 4] == boot[2], "teardown: data sel %#x != %#x",
	      regs[J713_HP_PORT_DATA_SEL / 4], boot[2]);
	CHECK(!(regs[J713_HP_SYNC_STATUS / 4] & 1) && !(regs[J713_HP_MCLK_STATUS / 4] & 1) &&
	      !(regs[J713_HP_ADP_FIFO_EN / 4] & 1), "teardown leaves an enable on");
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
			check_contract(s, &p);
		}
		check_port(boots[b]);
	}
	CHECK(!j713_hp_plan(J713_HP_PROGRAM, boots[0], &p), "PROGRAM failed");
	check_program(&p);
	check_sequences();

	if (failures) {
		printf("plan_test: %d failure(s)\n", failures);
		return 1;
	}
	printf("plan_test: all checks passed\n");
	return 0;
}
