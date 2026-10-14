/* SPDX-License-Identifier: GPL-2.0 */
/*
 * J713 headphone register plans. No function in this header performs MMIO.
 *
 * The headphone stream is the macOS "coS " codec stream from the AOP
 * firmware: mck2 (PMGR mux on nco3, 3.072 MHz) -> ckg2 -> syn2 (64-bit
 * 50% frame) -> TX serializer ms02 -> I2S port 2 -> CS42L84. It lives in
 * MCA cluster 2 of the block at 0x393600000, beside the speaker's cluster 0.
 *
 * Shared-register contract. The global block at +0x000..+0x22f is shared
 * with the speaker driver (snd-soc-apple-mca, cluster 0). These are the
 * only accesses to it, and j713_hp_plan() is the only place that emits them:
 *  - writes SYNC_OMUX_SEL(2) (+0x108) := 3, in PROGRAM only (prepare);
 *  - writes BIT(2) to SYNC_OMUX_ENABLE_SET/_CLR (+0x140/+0x144) and
 *    TX_ENABLE_SET/_CLR (+0x160/+0x164), and 1 to ALIGNED_ENABLE_TRIGGER
 *    (+0x170), in START and STOP only (trigger);
 *  - reads TX_ACTIVE (+0x168), in the driver's guard;
 *  - never touches SYNC_OMUX_SEL(0) (+0x100, the speaker's), any other bit,
 *    or RX_ENABLE_SET/_CLR (+0x150/+0x154).
 * Every other write lands in cluster 2's clock window (port 2 included),
 * TX serializer 2 or DMA adapter 2. kernel/test/plan_test.c checks all of
 * this for every step.
 *
 * Global register names are the AOP firmware's (routine 0x105cb58): the
 * enable path writes SEL, OMUX set, RX set, TX set, trigger; the disable
 * path OMUX clr, RX clr, TX clr, trigger. The SET/CLR/trigger registers
 * are write-only (they read back 0).
 */
#ifndef J713_HP_PLAN_H
#define J713_HP_PLAN_H

#define J713_HP_MCA_PHYS		0x393600000ULL
#define J713_HP_MCA_SIZE		0x9000

/* Shared global block */
#define J713_HP_GLOBAL_SIZE		0x230
#define J713_HP_SYNC_OMUX_SEL(n)	(0x100 + 4 * (n))
#define J713_HP_SYNC_OMUX_ENABLE_SET	0x140
#define J713_HP_SYNC_OMUX_ENABLE_CLR	0x144
#define J713_HP_RX_ENABLE_SET		0x150
#define J713_HP_RX_ENABLE_CLR		0x154
#define J713_HP_TX_ENABLE_SET		0x160
#define J713_HP_TX_ENABLE_CLR		0x164
#define J713_HP_TX_ACTIVE		0x168
#define J713_HP_ALIGNED_ENABLE_TRIGGER	0x170

/* mck2/ckg2/syn2/ms02, OMUX 2, I2S port 2, DMA adapter 2 */
#define J713_HP_IDX			2
#define J713_HP_BIT			(1U << J713_HP_IDX)

/* Cluster 2 clock window: clock gen +0x00, sync gen +0x20, port 2 +0x60 */
#define J713_HP_CLK			(0x2000 + J713_HP_IDX * 0x100)
#define J713_HP_MCLK_STATUS		(J713_HP_CLK + 0x00)
#define J713_HP_CLOCK_GEN_CONFIG	(J713_HP_CLK + 0x04)
#define J713_HP_SYNC_STATUS		(J713_HP_CLK + 0x20)
#define J713_HP_SYNC_CONFIG		(J713_HP_CLK + 0x24)
#define J713_HP_SYNC_HI			(J713_HP_CLK + 0x28)
#define J713_HP_SYNC_LO			(J713_HP_CLK + 0x2c)
#define J713_HP_PORT_ENABLES		(J713_HP_CLK + 0x60)
#define J713_HP_PORT_CLOCK_SEL		(J713_HP_CLK + 0x64)
#define J713_HP_PORT_DATA_SEL		(J713_HP_CLK + 0x68)
#define J713_HP_CLK_END			(J713_HP_CLK + 0x100)
#define  J713_HP_EN			0x1	/* MCLK_STATUS / SYNC_STATUS enable */

/* TX serializer 2 */
#define J713_HP_TXS			(0x6000 + J713_HP_IDX * 0x100)
#define J713_HP_TXS_CONTROL		(J713_HP_TXS + 0x00)
#define J713_HP_TXS_CONF		(J713_HP_TXS + 0x04)
#define J713_HP_TXS_BITSTART		(J713_HP_TXS + 0x08)
#define J713_HP_TXS_SLOTMASK(n)		(J713_HP_TXS + 0x0c + 4 * (n))
#define J713_HP_TXS_END			(J713_HP_TXS + 0x100)
#define  J713_HP_CONF_SYNC_SEL		0x70000

/* DMA adapter 2 */
#define J713_HP_ADP			(0x8000 + J713_HP_IDX * 0x100)
#define J713_HP_ADP_FIFO		(J713_HP_ADP + 0x00)
#define J713_HP_ADP_FIFO_EN		(J713_HP_ADP + 0x04)
#define J713_HP_ADP_LEVEL		(J713_HP_ADP + 0x08)
#define J713_HP_ADP_END			(J713_HP_ADP + 0x100)

/* The recipe (docs/j713-headphones.md, "What made it work") */
#define J713_HP_CLOCK_GEN		0x100	/* divisor 1, select 0: mck2 at 3.072 MHz */
#define J713_HP_SYNC_SEL		(J713_HP_IDX + 1)	/* bit 9 (frame invert) OFF */
#define J713_HP_HALF_FRAME		31	/* HI/LO 31/31: 64-bit 50% frame */
/* NCHANS 2 | WIDTH_32 | UNK123 | SYNC_SEL 3; no BCLK_POL, MSB first */
#define J713_HP_TX_CONF			(0x1 | 0x100 | 0x7000 | (J713_HP_SYNC_SEL << 16))
#define J713_HP_BITSTART		1
/* m4_t8132_build_tx_fifo(old, 1, 32, 16): threshold 16, no packing/pad */
#define J713_HP_FIFO_MASK		0x807f007fU
#define J713_HP_FIFO_VALUE		(16U << 16)
/* Port staging as the speaker's j713_port_open(): clock = syncgen idx+1 */
#define J713_HP_PORT_CLOCK_MASK		0xf00U
#define J713_HP_PORT_CLOCK		((J713_HP_IDX + 1) << 8)
#define J713_HP_PORT_DATA		J713_HP_BIT	/* serializer 2 */

enum j713_hp_step {
	J713_HP_PROGRAM,	/* prepare, cluster idle: the whole cluster-2 recipe */
	J713_HP_PORT_OPEN,	/* prepare: port 2 staging */
	J713_HP_START,		/* trigger start */
	J713_HP_STOP,		/* trigger stop */
	J713_HP_TEARDOWN,	/* hw_free: port 2 to boot values, enables off */
	J713_HP_NSTEPS
};

/* mask ~0 is an absolute write; anything else is read-modify-write. */
struct j713_hp_write {
	u32 offset;	/* from J713_HP_MCA_PHYS */
	u32 mask;
	u32 value;
};

#define J713_HP_PLAN_MAX	16

struct j713_hp_plan {
	struct j713_hp_write writes[J713_HP_PLAN_MAX];
	u32 count;
};

static inline const char *j713_hp_step_name(enum j713_hp_step step)
{
	static const char *const names[J713_HP_NSTEPS] = {
		"PROGRAM", "PORT_OPEN", "START", "STOP", "TEARDOWN",
	};

	return step < J713_HP_NSTEPS ? names[step] : "?";
}

static inline void j713_hp_add(struct j713_hp_plan *p, u32 offset, u32 mask,
			       u32 value)
{
	p->writes[p->count++] = (struct j713_hp_write){ offset, mask, value & mask };
}

/* port_boot: port 2 ENABLES, CLOCK_SEL, DATA_SEL as read at probe. */
static inline int j713_hp_plan(enum j713_hp_step step, const u32 port_boot[3],
			       struct j713_hp_plan *out)
{
	struct j713_hp_plan p = { .count = 0 };
	u32 en, sel, data;

	if (!out || !port_boot)
		return -EINVAL;
	en = port_boot[0];
	sel = port_boot[1];
	data = port_boot[2];

	switch (step) {
	case J713_HP_PROGRAM:
		j713_hp_add(&p, J713_HP_CLOCK_GEN_CONFIG, ~0U, J713_HP_CLOCK_GEN);
		j713_hp_add(&p, J713_HP_SYNC_CONFIG, ~0U, J713_HP_SYNC_SEL);
		j713_hp_add(&p, J713_HP_SYNC_HI, ~0U, J713_HP_HALF_FRAME);
		j713_hp_add(&p, J713_HP_SYNC_LO, ~0U, J713_HP_HALF_FRAME);
		j713_hp_add(&p, J713_HP_SYNC_OMUX_SEL(J713_HP_IDX), ~0U,
			    J713_HP_SYNC_SEL);
		j713_hp_add(&p, J713_HP_TXS_CONF, ~0U, J713_HP_TX_CONF);
		j713_hp_add(&p, J713_HP_TXS_BITSTART, ~0U, J713_HP_BITSTART);
		j713_hp_add(&p, J713_HP_TXS_SLOTMASK(0), ~0U, ~0U);
		j713_hp_add(&p, J713_HP_TXS_SLOTMASK(1), ~0U, ~0x3U);
		j713_hp_add(&p, J713_HP_TXS_SLOTMASK(2), ~0U, ~0U);
		j713_hp_add(&p, J713_HP_TXS_SLOTMASK(3), ~0U, ~0x3U);
		j713_hp_add(&p, J713_HP_ADP_FIFO, J713_HP_FIFO_MASK, J713_HP_FIFO_VALUE);
		j713_hp_add(&p, J713_HP_ADP_FIFO_EN, 0x1, 0x1);
		j713_hp_add(&p, J713_HP_SYNC_STATUS, J713_HP_EN, J713_HP_EN);
		j713_hp_add(&p, J713_HP_MCLK_STATUS, J713_HP_EN, J713_HP_EN);
		break;
	case J713_HP_PORT_OPEN:
		j713_hp_add(&p, J713_HP_PORT_CLOCK_SEL, ~0U,
			    (sel & ~J713_HP_PORT_CLOCK_MASK) | J713_HP_PORT_CLOCK);
		j713_hp_add(&p, J713_HP_PORT_ENABLES, ~0U, en | 2);
		j713_hp_add(&p, J713_HP_PORT_ENABLES, ~0U, en | 6);
		j713_hp_add(&p, J713_HP_PORT_DATA_SEL, ~0U, data | J713_HP_PORT_DATA);
		j713_hp_add(&p, J713_HP_PORT_ENABLES, ~0U, en | 14);
		break;
	case J713_HP_START:
		/* Consume any staged clear left by an earlier run first. */
		j713_hp_add(&p, J713_HP_SYNC_OMUX_ENABLE_CLR, ~0U, J713_HP_BIT);
		j713_hp_add(&p, J713_HP_TX_ENABLE_CLR, ~0U, J713_HP_BIT);
		j713_hp_add(&p, J713_HP_ALIGNED_ENABLE_TRIGGER, ~0U, 1);
		j713_hp_add(&p, J713_HP_SYNC_OMUX_ENABLE_SET, ~0U, J713_HP_BIT);
		j713_hp_add(&p, J713_HP_TX_ENABLE_SET, ~0U, J713_HP_BIT);
		j713_hp_add(&p, J713_HP_ALIGNED_ENABLE_TRIGGER, ~0U, 1);
		break;
	case J713_HP_STOP:
		j713_hp_add(&p, J713_HP_SYNC_OMUX_ENABLE_CLR, ~0U, J713_HP_BIT);
		j713_hp_add(&p, J713_HP_TX_ENABLE_CLR, ~0U, J713_HP_BIT);
		j713_hp_add(&p, J713_HP_ALIGNED_ENABLE_TRIGGER, ~0U, 1);
		break;
	case J713_HP_TEARDOWN:
		/* Reverse of PORT_OPEN, as the speaker's j713_port_close(). */
		j713_hp_add(&p, J713_HP_PORT_ENABLES, 8, 0);
		j713_hp_add(&p, J713_HP_PORT_DATA_SEL, ~0U, data);
		j713_hp_add(&p, J713_HP_PORT_ENABLES, 12, 0);
		j713_hp_add(&p, J713_HP_PORT_ENABLES, ~0U, en);
		j713_hp_add(&p, J713_HP_PORT_CLOCK_SEL, ~0U, sel);
		j713_hp_add(&p, J713_HP_SYNC_STATUS, J713_HP_EN, 0);
		j713_hp_add(&p, J713_HP_MCLK_STATUS, J713_HP_EN, 0);
		j713_hp_add(&p, J713_HP_ADP_FIFO_EN, 0x1, 0);
		break;
	default:
		return -EINVAL;
	}
	*out = p;
	return 0;
}

/* SET/CLR/trigger registers read back 0, so their writes cannot be verified. */
static inline bool j713_hp_write_only(u32 offset)
{
	return offset >= J713_HP_SYNC_OMUX_ENABLE_SET &&
	       offset <= J713_HP_ALIGNED_ENABLE_TRIGGER &&
	       offset != J713_HP_TX_ACTIVE;
}

/*
 * True if write i is the last one to its register in the plan, so its
 * masked value is what the register holds once the plan has run.
 */
static inline bool j713_hp_final(const struct j713_hp_plan *p, u32 i)
{
	u32 j;

	for (j = i + 1; j < p->count; j++)
		if (p->writes[j].offset == p->writes[i].offset)
			return false;
	return true;
}
#endif
