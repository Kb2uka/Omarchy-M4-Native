/* SPDX-License-Identifier: GPL-2.0 */
/*
 * J713 headphone jack register plans and the two-direction policy. No
 * function in this header performs MMIO.
 *
 * The headphone stream is the macOS "coS " codec stream from the AOP
 * firmware: mck2 (PMGR mux on nco3, 3.072 MHz) -> ckg2 -> syn2 (64-bit
 * 50% frame) -> TX serializer ms02 -> I2S port 2 -> CS42L84. The headset
 * mic ("cirx") runs back on the same clock and frame: I2S port 2 -> RX
 * serializer 2 -> DMA adapter 2's RX half -> ADMAC channel 5. Both live in
 * MCA cluster 2 of the block at 0x393600000, beside the speaker's cluster 0.
 *
 * Shared-register contract. The global block at +0x000..+0x22f is shared
 * with the speaker driver (snd-soc-apple-mca, cluster 0). These are the
 * only accesses to it:
 *  - writes SYNC_OMUX_SEL(2) (+0x108) := 3, in PROGRAM only (prepare);
 *  - writes J713_HP_BIT to SYNC_OMUX_ENABLE_SET/_CLR (+0x140/+0x144),
 *    RX_ENABLE_SET/_CLR (+0x150/+0x154) and TX_ENABLE_SET/_CLR
 *    (+0x160/+0x164), and 1 to ALIGNED_ENABLE_TRIGGER (+0x170), only from
 *    j713_hp_stage() (trigger, and the driver's guard);
 *  - reads TX_ACTIVE (+0x168) and RX_ACTIVE (+0x158), in the driver's guard;
 *  - never touches SYNC_OMUX_SEL(0) (+0x100, the speaker's) or any other
 *    bit. The speaker stages bit 0 of the same SET/CLR registers (TX, and
 *    RX for its IV-sense feedback) under its own lock.
 * Every other write lands in cluster 2's clock window (port 2 included),
 * TX serializer 2, RX serializer 2 or DMA adapter 2. The timing registers
 * of cluster 2, both serializers included, are written only by PROGRAM,
 * with both directions idle and the cluster's clocks off. The one
 * exception is the driver's serializer reset, which toggles CONF's sync
 * select with the clocks running, as the speaker's
 * m4_cleanup_direction_once() does: TX whenever TX is not enabled, RX only
 * while TX is not enabled either (j713_hp_prepare()). kernel/test/plan_test.c
 * checks all of this for every plan and every stage.
 *
 * Contract with apple-admac (the pinned admac.c). Channel 5 is claimed by
 * this driver alone (DT dma-names "rx", chan_id checked at probe). Its
 * BUS_WIDTH and FIFOCTL words are written only by j713_hp_rx_dma_plan(),
 * from capture prepare, where ALSA has stopped and synchronized the stream
 * (dmaengine terminate + synchronize), and are verified by readback.
 * apple-admac never writes those two words for channel 5: its RX setter is
 * channel 1 only, and its generic paths write CTL, CONFIG, BURST, ENABLE
 * and the SRAM carveout.
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
#define J713_HP_RX_ACTIVE		0x158	/* bit 0 is the speaker's IV sense */
#define J713_HP_TX_ENABLE_SET		0x160
#define J713_HP_TX_ENABLE_CLR		0x164
#define J713_HP_TX_ACTIVE		0x168
#define J713_HP_ALIGNED_ENABLE_TRIGGER	0x170

/* mck2/ckg2/syn2/ms02, OMUX 2, I2S port 2, RX/TX serializer 2, DMA adapter 2 */
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

/* Either serializer: CONTROL and CONF sit at the same offsets. */
#define J713_HP_SER_CONTROL		0x00
#define J713_HP_SER_CONF		0x04
#define  J713_HP_CONF_SYNC_SEL		0x70000

/* RX serializer 2 (the m4_t8132_build_rx() layout) */
#define J713_HP_RXS			(0x4000 + J713_HP_IDX * 0x100)
#define J713_HP_RXS_CONTROL		(J713_HP_RXS + J713_HP_SER_CONTROL)
#define J713_HP_RXS_CONF		(J713_HP_RXS + J713_HP_SER_CONF)
#define J713_HP_RXS_DELAY		(J713_HP_RXS + 0x08)
#define J713_HP_RXS_MASK_HI		(J713_HP_RXS + 0x0c)
#define J713_HP_RXS_MASK_LO		(J713_HP_RXS + 0x10)
#define J713_HP_RXS_SOURCES		(J713_HP_RXS + 0x14)
#define J713_HP_RXS_LOOPBACK		(J713_HP_RXS + 0x18)
#define J713_HP_RXS_END			(J713_HP_RXS + 0x100)

/* TX serializer 2 */
#define J713_HP_TXS			(0x6000 + J713_HP_IDX * 0x100)
#define J713_HP_TXS_CONTROL		(J713_HP_TXS + J713_HP_SER_CONTROL)
#define J713_HP_TXS_CONF		(J713_HP_TXS + J713_HP_SER_CONF)
#define J713_HP_TXS_BITSTART		(J713_HP_TXS + 0x08)
#define J713_HP_TXS_SLOTMASK(n)		(J713_HP_TXS + 0x0c + 4 * (n))
#define J713_HP_TXS_END			(J713_HP_TXS + 0x100)

/* DMA adapter 2 */
#define J713_HP_ADP			(0x8000 + J713_HP_IDX * 0x100)
#define J713_HP_ADP_FIFO		(J713_HP_ADP + 0x00)
#define J713_HP_ADP_FIFO_EN		(J713_HP_ADP + 0x04)
#define  J713_HP_FIFO_EN_TX		0x1
#define  J713_HP_FIFO_EN_RX		0x10
#define  J713_HP_FIFO_EN_BOTH		(J713_HP_FIFO_EN_TX | J713_HP_FIFO_EN_RX)
#define J713_HP_ADP_LEVEL		(J713_HP_ADP + 0x08)
#define J713_HP_ADP_RX_LEVEL		(J713_HP_ADP + 0x0c)	/* debugfs only */
#define J713_HP_ADP_END			(J713_HP_ADP + 0x100)

/* The recipe (docs/j713-headphones.md, "What made it work") */
#define J713_HP_CLOCK_GEN		0x100	/* divisor 1, select 0: mck2 at 3.072 MHz */
#define J713_HP_SYNC_SEL		(J713_HP_IDX + 1)	/* bit 9 (frame invert) OFF */
#define J713_HP_HALF_FRAME		31	/* HI/LO 31/31: 64-bit 50% frame */
/* NCHANS 2 | WIDTH_32 | UNK123 | SYNC_SEL 3; no BCLK_POL, MSB first */
#define J713_HP_TX_CONF			(0x1 | 0x100 | 0x7000 | (J713_HP_SYNC_SEL << 16))
#define J713_HP_BITSTART		1
/*
 * m4_t8132_build_rx({channels 0x1, width 32, delay 1, i2s_sources 0x4,
 * loopback 0, sync_type 2, sync_index 2, falling_edge false}):
 * 0xb000 | SYNC_SEL 3 | WIDTH_32 | BCLK_POL (rising edge) | last index 0.
 */
#define J713_HP_RX_CONF			(0xb000 | (J713_HP_SYNC_SEL << 16) | 0x100 | 0x400)
#define J713_HP_RX_DELAY		1
#define J713_HP_RX_SOURCES		J713_HP_BIT	/* I2S port 2 */
#define J713_HP_RX_SOURCES_MASK		0x3f
/*
 * One write for both halves of the shared FIFO word, so the readback
 * checks both: m4_t8132_build_tx_fifo(old, 1, 32, 16) (threshold 16, no
 * packing or pad) | m4_t8132_build_rx_fifo(old, 1, 32, false) (no packing
 * or pad: the RX field is 0).
 */
#define J713_HP_TX_FIFO_MASK		0x807f007fU
#define J713_HP_RX_FIFO_MASK		0x80007f00U
#define J713_HP_FIFO_MASK		(J713_HP_TX_FIFO_MASK | J713_HP_RX_FIFO_MASK)
#define J713_HP_FIFO_VALUE		(16U << 16)
/* Port staging as the speaker's j713_port_open(): clock = syncgen idx+1 */
#define J713_HP_PORT_CLOCK_MASK		0xf00U
#define J713_HP_PORT_CLOCK		((J713_HP_IDX + 1) << 8)
#define J713_HP_PORT_DATA		J713_HP_BIT	/* serializer 2 */

/*
 * ADMAC channel 5, the RX channel next to the headphone TX channel 4.
 * apple-admac's split-channel layout (admac.c probe): RX bank +0x4000,
 * 0x200 per channel pair.
 */
#define J713_HP_ADMAC_PHYS		0x393800000ULL
#define J713_HP_RX_DMA_CHAN		5
#define J713_HP_RX_DMA_PHYS		(J713_HP_ADMAC_PHYS + 0x8000 +		\
					 (J713_HP_RX_DMA_CHAN & 1) * 0x4000 +	\
					 (J713_HP_RX_DMA_CHAN / 2) * 0x200)
#define J713_HP_RX_DMA_SIZE		0x100
#define J713_HP_DMA_CTL			0x00
#define  J713_HP_DMA_IDLE		0x10000	/* apple-admac's own idle test */
#define J713_HP_DMA_CONFIG		0x04	/* debugfs only */
#define J713_HP_DMA_BUS_WIDTH		0x40
#define  J713_HP_DMA_BUS_MASK		0xff	/* WORD_SIZE | FRAME_SIZE */
#define  J713_HP_DMA_BUS_32BIT		0x02	/* 32-bit words, 1-word frame */
#define J713_HP_DMA_CARVEOUT		0x50	/* debugfs only */
#define J713_HP_DMA_FIFOCTL		0x54
#define  J713_HP_DMA_FIFOCTL_VALUE	0x180	/* as the speaker's channel 1 */
#define J713_HP_DMA_ENABLE		0xa0	/* debugfs only */

/* Directions, numbered as ALSA's streams (0 playback, 1 capture). */
enum j713_hp_dir {
	J713_HP_TX,
	J713_HP_RX,
};

struct j713_hp_direction {
	const char *name;
	u32 enable_set;		/* staged enable, global block */
	u32 enable_clr;
	u32 active;		/* status, read in the guard */
	u32 serializer;		/* window base: J713_HP_SER_CONTROL, _CONF */
	u32 fifo_en;		/* bit in J713_HP_ADP_FIFO_EN */
	u32 channels;
};

static const struct j713_hp_direction j713_hp_dirs[2] = {
	[J713_HP_TX] = { "TX", J713_HP_TX_ENABLE_SET, J713_HP_TX_ENABLE_CLR,
			 J713_HP_TX_ACTIVE, J713_HP_TXS, J713_HP_FIFO_EN_TX, 2 },
	[J713_HP_RX] = { "RX", J713_HP_RX_ENABLE_SET, J713_HP_RX_ENABLE_CLR,
			 J713_HP_RX_ACTIVE, J713_HP_RXS, J713_HP_FIFO_EN_RX, 1 },
};

enum j713_hp_step {
	J713_HP_PROGRAM,	/* prepare, cluster idle: the whole cluster-2 recipe */
	J713_HP_PORT_OPEN,	/* prepare: port 2 staging */
	J713_HP_TEARDOWN,	/* hw_free: port 2 to boot values, enables off */
	J713_HP_NSTEPS
};

/* mask ~0 is an absolute write; anything else is read-modify-write. */
struct j713_hp_write {
	u32 offset;	/* from the plan's base: the MCA, or ch5 for j713_hp_rx_dma_plan() */
	u32 mask;
	u32 value;
};

#define J713_HP_PLAN_MAX	24

struct j713_hp_plan {
	struct j713_hp_write writes[J713_HP_PLAN_MAX];
	u32 count;
	bool overflow;	/* a write did not fit; the plan is never used */
};

static inline const char *j713_hp_step_name(enum j713_hp_step step)
{
	static const char *const names[J713_HP_NSTEPS] = {
		"PROGRAM", "PORT_OPEN", "TEARDOWN",
	};

	return step < J713_HP_NSTEPS ? names[step] : "?";
}

static inline void j713_hp_add(struct j713_hp_plan *p, u32 offset, u32 mask,
			       u32 value)
{
	if (p->count >= J713_HP_PLAN_MAX) {
		p->overflow = true;
		return;
	}
	p->writes[p->count++] = (struct j713_hp_write){ offset, mask, value & mask };
}

/* Hand out a finished plan; one that overflowed is refused. */
static inline int j713_hp_finish(const struct j713_hp_plan *p,
				 struct j713_hp_plan *out)
{
	if (p->overflow)
		return -E2BIG;
	*out = *p;
	return 0;
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
		/* In m4_t8132_build_rx() order; one slot: masks ~0 / ~1. */
		j713_hp_add(&p, J713_HP_RXS_SOURCES, J713_HP_RX_SOURCES_MASK,
			    J713_HP_RX_SOURCES);
		j713_hp_add(&p, J713_HP_RXS_LOOPBACK, ~0U, 0);
		j713_hp_add(&p, J713_HP_RXS_CONF, ~0U, J713_HP_RX_CONF);
		j713_hp_add(&p, J713_HP_RXS_DELAY, ~0U, J713_HP_RX_DELAY);
		j713_hp_add(&p, J713_HP_RXS_MASK_HI, ~0U, ~0U);
		j713_hp_add(&p, J713_HP_RXS_MASK_LO, ~0U, ~0x1U);
		j713_hp_add(&p, J713_HP_ADP_FIFO, J713_HP_FIFO_MASK, J713_HP_FIFO_VALUE);
		j713_hp_add(&p, J713_HP_ADP_FIFO_EN, J713_HP_FIFO_EN_BOTH, J713_HP_FIFO_EN_BOTH);
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
	case J713_HP_TEARDOWN:
		/* Reverse of PORT_OPEN, as the speaker's j713_port_close(). */
		j713_hp_add(&p, J713_HP_PORT_ENABLES, 8, 0);
		j713_hp_add(&p, J713_HP_PORT_DATA_SEL, ~0U, data);
		j713_hp_add(&p, J713_HP_PORT_ENABLES, 12, 0);
		j713_hp_add(&p, J713_HP_PORT_ENABLES, ~0U, en);
		j713_hp_add(&p, J713_HP_PORT_CLOCK_SEL, ~0U, sel);
		j713_hp_add(&p, J713_HP_SYNC_STATUS, J713_HP_EN, 0);
		j713_hp_add(&p, J713_HP_MCLK_STATUS, J713_HP_EN, 0);
		j713_hp_add(&p, J713_HP_ADP_FIFO_EN, J713_HP_FIFO_EN_BOTH, 0);
		break;
	default:
		return -EINVAL;
	}
	return j713_hp_finish(&p, out);
}

/*
 * ADMAC channel 5's own words, offsets from J713_HP_RX_DMA_PHYS, in place
 * of dmaengine_slave_config() (apple-admac's RX setter is channel 1 only).
 * BUS_WIDTH keeps bits 31:8 (reset value 0xf00).
 */
static inline int j713_hp_rx_dma_plan(struct j713_hp_plan *out)
{
	struct j713_hp_plan p = { .count = 0 };

	if (!out)
		return -EINVAL;
	j713_hp_add(&p, J713_HP_DMA_BUS_WIDTH, J713_HP_DMA_BUS_MASK, J713_HP_DMA_BUS_32BIT);
	j713_hp_add(&p, J713_HP_DMA_FIFOCTL, ~0U, J713_HP_DMA_FIFOCTL_VALUE);
	return j713_hp_finish(&p, out);
}

/*
 * Stage dir's enable on or off and return the next active mask
 * (BIT(J713_HP_TX) | BIT(J713_HP_RX), kept by the caller under its lock).
 * The shape of the speaker's m4_t8132_build_stage(), on bit 2, with the
 * clear-first prefix that consumes a staged clear left by an earlier run.
 * OMUX 2 (the frame to port 2) is set by whichever direction starts first
 * and cleared by the last to stop: its writes appear only while the other
 * direction is off. The writes are emitted even when the mask does not
 * change (the guard's "stop again"). An unknown dir gives no writes.
 */
static inline u32 j713_hp_stage(u32 active, enum j713_hp_dir dir, bool on,
				struct j713_hp_plan *out)
{
	const struct j713_hp_direction *d;
	bool other;

	if (!out)
		return active;
	*out = (struct j713_hp_plan){ .count = 0 };
	if (dir != J713_HP_TX && dir != J713_HP_RX)
		return active;
	d = &j713_hp_dirs[dir];
	other = active & (1U << !dir);

	if (!other)
		j713_hp_add(out, J713_HP_SYNC_OMUX_ENABLE_CLR, ~0U, J713_HP_BIT);
	j713_hp_add(out, d->enable_clr, ~0U, J713_HP_BIT);
	j713_hp_add(out, J713_HP_ALIGNED_ENABLE_TRIGGER, ~0U, 1);
	if (!on)
		return active & ~(1U << dir);
	if (!other)
		j713_hp_add(out, J713_HP_SYNC_OMUX_ENABLE_SET, ~0U, J713_HP_BIT);
	j713_hp_add(out, d->enable_set, ~0U, J713_HP_BIT);
	j713_hp_add(out, J713_HP_ALIGNED_ENABLE_TRIGGER, ~0U, 1);
	return active | (1U << dir);
}

/* What prepare does, in this order; busy excludes everything else. */
struct j713_hp_prep {
	bool busy;		/* refuse: the other direction holds a cluster that differs */
	bool guard_other;	/* the other direction's enable must read clear first */
	bool teardown;		/* TEARDOWN: the clocked cluster differs from the recipe */
	bool program;		/* mck2 to nco3, PROGRAM, PORT_OPEN */
	bool reset;		/* this direction's serializer reset */
};

/*
 * prepared, active: BIT(dir) masks as the driver keeps them. clocked:
 * cluster 2's clocks run. recipe_ok: clocked, and PROGRAM and PORT_OPEN
 * read back as written. Never TEARDOWN or PROGRAM while the other
 * direction runs, or while it is prepared on a clocked cluster; reprogram
 * an unclocked cluster even if the other direction is prepared (it cannot
 * run without clocks: resume after power loss). Never reset RX while TX
 * runs: that case was never tested.
 */
static inline struct j713_hp_prep j713_hp_prepare(enum j713_hp_dir dir, u32 prepared,
						  u32 active, bool clocked,
						  bool recipe_ok)
{
	u32 other = 1U << !dir;
	struct j713_hp_prep a = { .busy = false };

	if (clocked && recipe_ok) {
		a.reset = !(dir == J713_HP_RX && (active & (1U << J713_HP_TX)));
	} else if ((active & other) || (clocked && (prepared & other))) {
		a.busy = true;
	} else {
		a.guard_other = true;
		a.teardown = clocked;
		a.program = true;
		a.reset = true;
	}
	return a;
}

/* The staged registers are write-only (they read back 0): never verified. */
static inline bool j713_hp_write_only(u32 offset)
{
	switch (offset) {
	case J713_HP_SYNC_OMUX_ENABLE_SET:
	case J713_HP_SYNC_OMUX_ENABLE_CLR:
	case J713_HP_RX_ENABLE_SET:
	case J713_HP_RX_ENABLE_CLR:
	case J713_HP_TX_ENABLE_SET:
	case J713_HP_TX_ENABLE_CLR:
	case J713_HP_ALIGNED_ENABLE_TRIGGER:
		return true;
	}
	return false;
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
