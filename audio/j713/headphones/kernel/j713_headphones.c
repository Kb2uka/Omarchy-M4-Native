// SPDX-License-Identifier: GPL-2.0
/*
 * J713 headphone jack card "J713Headphones": MCA cluster 2 <-> I2S port 2 <->
 * CS42L84, beside the speaker card (cluster 0), which keeps running.
 * Playback: TX serializer 2, ADMAC channel 4. Capture (the headset mic): RX
 * serializer 2, the RX half of DMA adapter 2, ADMAC channel 5.
 *
 * All register values, the shared-register contract and the two-direction
 * policy (staging, the prepare decision) live in j713_hp_plan.h. This file
 * executes those plans through one executor (hp_apply) and one checker
 * (hp_verify), and adds the only other accesses: the TX_ACTIVE/RX_ACTIVE
 * reads in the guard, the mck2 mux switch, the serializer reset and the
 * channel 5 idle read, all inside the stream's own windows apart from the
 * status reads.
 *
 * One mutex, h->lock, serializes prepare, trigger, hw_free and unbind of
 * both streams. Under it, h->prepared holds BIT(stream) from prepare to
 * hw_free, and h->active while the stream's staged enable is on. The link
 * is nonatomic, so the trigger may sleep.
 *
 * Callbacks:
 *  - probe: map, attach the power domains, request the "tx" and "rx" DMA
 *    channels (rx must be channel 5, whose registers this driver writes),
 *    record the boot values of mck2 and port 2. No register writes.
 *  - hw_params: format check only (WirePlumber probes cards by opening
 *    them with real hw_params/hw_free cycles). Capture never calls
 *    dmaengine_slave_config(): see hp_pcm_hw_params().
 *  - prepare: guard the stream's own enable. Capture then writes and
 *    verifies channel 5's BUS_WIDTH and FIFOCTL. j713_hp_prepare() decides
 *    the rest: a clocked cluster that matches the recipe is verified, not
 *    rewritten (timing registers are written only while the cluster's
 *    clocks are off, the speaker driver's j713_gates_off rule); otherwise,
 *    with the other direction neither running nor prepared on a clocked
 *    cluster, guard it, tear down, mck2 to nco3, PROGRAM and PORT_OPEN.
 *    Last, the stream's serializer reset (never RX while TX runs).
 *  - trigger: j713_hp_stage(), the only staged-enable writes.
 *  - hw_free: while the other direction is prepared or running, only this
 *    stream's guard. The last one runs hp_quiesce(), the one cleanup owner
 *    (also for unbind): TEARDOWN only once both TX_ACTIVE and RX_ACTIVE bit
 *    2 read clear. If either cannot be stopped it leaves cluster 2 clocked,
 *    and unbind then also keeps mck2 on nco3 and the power domains linked
 *    (until power-off) rather than pull the clock or power from under an
 *    enabled serializer. Otherwise unbind puts mck2 back to its boot source
 *    and drops the domains.
 */
#include <linux/bitfield.h>
#include <linux/debugfs.h>
#include <linux/delay.h>
#include <linux/dma-mapping.h>
#include <linux/dmaengine.h>
#include <linux/io.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/pm_domain.h>
#include <linux/seq_file.h>
#include <sound/dmaengine_pcm.h>
#include <sound/jack.h>
#include <sound/pcm_params.h>
#include <sound/soc.h>

#include "j713_hp_plan.h"

static_assert(J713_HP_TX == SNDRV_PCM_STREAM_PLAYBACK &&
	      J713_HP_RX == SNDRV_PCM_STREAM_CAPTURE);

/*
 * PMGR clock mux feeding MCA clock generator 2 ("mck2" in the AOP
 * firmware). Source 10 is nco3 at 3.072 MHz, the clock macOS uses for this
 * stream; Linux boots with every mux on source 0.
 */
#define PMGR_MCK2		0x388240040ULL
#define  MCK_PENDING		BIT(30)
#define  MCK_SRC_SEL		GENMASK(27, 24)
#define  MCK_SRC_NCO3		10
#define HP_BCLK			3072000
#define HP_RATE			48000

/* Codec DAC volume index 256 = 0 dB (-128..+12 dB in 0.5 dB steps). */
#define HP_VOLUME_MAX		256

struct hp {
	struct device *dev;
	void __iomem *mca;
	void __iomem *mck;
	void __iomem *rxdma;		/* ADMAC channel 5's registers */
	u32 mck_boot;
	u32 port_boot[3];
	struct device *pd[3];
	struct device_link *pd_link[3];
	struct dma_chan *chan[2];	/* by stream: "tx" (4), "rx" (5) */
	struct mutex lock;
	u32 prepared;
	u32 active;
	struct device_node *codec_np;
	struct snd_soc_card card;
	struct snd_soc_dai_link link;
	struct snd_soc_dai_link_component comps[3];
	struct snd_soc_jack jack;
	struct dentry *dbg;
};

static struct hp *hp_from(struct snd_pcm_substream *s)
{
	return snd_soc_card_get_drvdata(snd_soc_substream_to_rtd(s)->card);
}

static u32 rd(struct hp *h, u32 off)
{
	return readl(h->mca + off);
}

static void wr(struct hp *h, u32 off, u32 val)
{
	writel(val, h->mca + off);
}

/* The one executor: every write a plan describes, in order. */
static void hp_apply(void __iomem *base, const struct j713_hp_plan *p)
{
	u32 i;

	for (i = 0; i < p->count; i++) {
		const struct j713_hp_write *w = &p->writes[i];
		u32 val = w->value;

		if (w->mask != ~0U)
			val |= readl(base + w->offset) & ~w->mask;
		writel(val, base + w->offset);
	}
}

/*
 * Count the registers that do not hold what the plan's final writes put
 * there; print every checked write to m if given.
 */
static int hp_verify(void __iomem *base, const struct j713_hp_plan *p,
		     const char *name, struct seq_file *m)
{
	int bad = 0;
	u32 i;

	for (i = 0; i < p->count; i++) {
		const struct j713_hp_write *w = &p->writes[i];
		u32 live;
		bool ok;

		if (!j713_hp_final(p, i) || j713_hp_write_only(w->offset))
			continue;
		live = readl(base + w->offset);
		ok = (live & w->mask) == w->value;
		bad += !ok;
		if (m)
			seq_printf(m, "  %-9s +%04x mask %08x want %08x live %08x%s\n",
				   name, w->offset, w->mask, w->value, live,
				   ok ? "" : "  MISMATCH");
	}
	return bad;
}

static void hp_run(struct hp *h, enum j713_hp_step step)
{
	struct j713_hp_plan p;

	if (!WARN_ON(j713_hp_plan(step, h->port_boot, &p)))
		hp_apply(h->mca, &p);
}

static int hp_check(struct hp *h, enum j713_hp_step step, struct seq_file *m)
{
	struct j713_hp_plan p;

	if (WARN_ON(j713_hp_plan(step, h->port_boot, &p)))
		return 1;
	return hp_verify(h->mca, &p, j713_hp_step_name(step), m);
}

/* Stage one direction's enable on or off; h->lock held. */
static void hp_stage(struct hp *h, enum j713_hp_dir dir, bool on)
{
	struct j713_hp_plan p;

	h->active = j713_hp_stage(h->active, dir, on, &p);
	hp_apply(h->mca, &p);
}

static bool hp_clocked(struct hp *h)
{
	return (rd(h, J713_HP_MCLK_STATUS) | rd(h, J713_HP_SYNC_STATUS)) & J713_HP_EN;
}

/* The trigger is frame-aligned, so allow a few frames for the status to follow. */
static int hp_wait_idle(struct hp *h, enum j713_hp_dir dir)
{
	int i;

	for (i = 0; i < 1000; i++) {
		if (!(rd(h, j713_hp_dirs[dir].active) & J713_HP_BIT))
			return 0;
		udelay(1);
	}
	return -EBUSY;
}

/* No cluster-2 timing write unless dir's status bit 2 reads clear. */
static int hp_guard(struct hp *h, enum j713_hp_dir dir, const char *what)
{
	const struct j713_hp_direction *d = &j713_hp_dirs[dir];

	if (!hp_wait_idle(h, dir))
		return 0;
	dev_warn(h->dev, "%s: %s_ACTIVE %#x still has bit 2, stopping again\n",
		 what, d->name, rd(h, d->active));
	hp_stage(h, dir, false);
	if (!hp_wait_idle(h, dir))
		return 0;
	dev_err(h->dev, "%s: %s_ACTIVE %#x: %s still enabled, refusing\n",
		what, d->name, rd(h, d->active), d->name);
	return -EBUSY;
}

static int hp_mck_settle(struct hp *h)
{
	int i;

	for (i = 0; i < 1000; i++) {
		if (!(readl(h->mck) & MCK_PENDING))
			return 0;
		udelay(1);
	}
	return -ETIMEDOUT;
}

/* Only while the cluster is idle; the mux is glitch-free and reports PENDING. */
static int hp_set_mck(struct hp *h)
{
	u32 old = readl(h->mck);

	if (FIELD_GET(MCK_SRC_SEL, old) == MCK_SRC_NCO3)
		return 0;
	writel((old & ~MCK_SRC_SEL) | FIELD_PREP(MCK_SRC_SEL, MCK_SRC_NCO3), h->mck);
	if (!hp_mck_settle(h)) {
		dev_info(h->dev, "mck2: %#x -> %#x\n", old, readl(h->mck));
		return 0;
	}
	dev_err(h->dev, "mck2: still pending after 1 ms (%#x): is nco3 running?\n",
		readl(h->mck));
	writel(old, h->mck);
	hp_mck_settle(h);
	return -ETIMEDOUT;
}

/*
 * As the speaker path's m4_cleanup_direction_once(): FIFO off, control RST,
 * sync select none and back, wait for control bits 0xa to clear. Runs before
 * every start with the clocks running and dir not enabled (RX also only
 * while TX is not, j713_hp_prepare()); without it the FIFO keeps stale
 * words and every start sounds different.
 */
static int hp_reset_serializer(struct hp *h, enum j713_hp_dir dir)
{
	const struct j713_hp_direction *d = &j713_hp_dirs[dir];
	u32 ctl_reg = d->serializer + J713_HP_SER_CONTROL;
	u32 conf_reg = d->serializer + J713_HP_SER_CONF;
	u32 fifo = rd(h, J713_HP_ADP_FIFO_EN);
	u32 conf = rd(h, conf_reg);
	u32 ctrl = 0;
	int i;

	wr(h, J713_HP_ADP_FIFO_EN, fifo & ~d->fifo_en);
	wr(h, ctl_reg, rd(h, ctl_reg) | 2);
	wr(h, conf_reg, conf & ~J713_HP_CONF_SYNC_SEL);
	wr(h, conf_reg, conf | J713_HP_CONF_SYNC_SEL);
	udelay(200);
	for (i = 0; i < 100; i++) {
		ctrl = rd(h, ctl_reg);
		if (!(ctrl & 0xa))
			break;
	}
	wr(h, conf_reg, conf);
	wr(h, J713_HP_ADP_FIFO_EN, fifo | d->fifo_en);
	if (ctrl & 0xa) {
		dev_err(h->dev, "%s serializer reset: control %#x did not clear\n",
			d->name, ctrl);
		return -EIO;
	}
	return 0;
}

/*
 * Channel 5's BUS_WIDTH and FIFOCTL, in place of dmaengine_slave_config().
 * ALSA has stopped and synchronized the stream before prepare, and the
 * channel is this driver's alone; CTL bit 16 mirrors apple-admac's own
 * idle test.
 */
static int hp_rx_dma(struct hp *h)
{
	u32 ctl = readl(h->rxdma + J713_HP_DMA_CTL);
	struct j713_hp_plan p;
	int bad;

	if (!(ctl & J713_HP_DMA_IDLE)) {
		dev_err(h->dev, "prepare: ADMAC channel %d not idle (ctl %#x), refusing\n",
			J713_HP_RX_DMA_CHAN, ctl);
		return -EBUSY;
	}
	if (WARN_ON(j713_hp_rx_dma_plan(&p)))
		return -EINVAL;
	hp_apply(h->rxdma, &p);
	bad = hp_verify(h->rxdma, &p, "ch5", NULL);
	dev_dbg(h->dev, "prepare: ADMAC channel %d bus_width %#x fifoctl %#x\n",
		J713_HP_RX_DMA_CHAN, readl(h->rxdma + J713_HP_DMA_BUS_WIDTH),
		readl(h->rxdma + J713_HP_DMA_FIFOCTL));
	if (bad) {
		dev_err(h->dev, "prepare: ADMAC channel %d: %d word(s) did not read back\n",
			J713_HP_RX_DMA_CHAN, bad);
		return -EIO;
	}
	return 0;
}

static int hp_set_fmt(struct snd_soc_dai *dai, unsigned int fmt)
{
	return fmt == (SND_SOC_DAIFMT_I2S | SND_SOC_DAIFMT_IB_IF |
		       SND_SOC_DAIFMT_BP_FP) ? 0 : -EINVAL;
}

static int hp_hw_params(struct snd_pcm_substream *s, struct snd_pcm_hw_params *p,
			struct snd_soc_dai *dai)
{
	if (params_rate(p) != HP_RATE ||
	    params_channels(p) != j713_hp_dirs[s->stream].channels ||
	    params_format(p) != SNDRV_PCM_FORMAT_S32_LE)
		return -EINVAL;
	return 0;
}

/* ALSA calls this with the stream stopped and synchronized (sync_stop). */
static int hp_prepare(struct snd_pcm_substream *s, struct snd_soc_dai *dai)
{
	struct hp *h = hp_from(s);
	enum j713_hp_dir dir = s->stream;
	struct j713_hp_prep a;
	bool clocked;
	int ret, bad = 0;

	mutex_lock(&h->lock);
	ret = hp_guard(h, dir, "prepare");
	if (!ret && dir == J713_HP_RX)
		ret = hp_rx_dma(h);
	if (ret)
		goto out;
	clocked = hp_clocked(h);
	if (clocked)
		bad = hp_check(h, J713_HP_PROGRAM, NULL) + hp_check(h, J713_HP_PORT_OPEN, NULL);
	a = j713_hp_prepare(dir, h->prepared, h->active, clocked, clocked && !bad);
	if (a.busy) {
		dev_err(h->dev, "prepare: cluster 2 %s, %d register(s) differ, and %s holds it: refusing\n",
			clocked ? "clocked" : "idle", bad, j713_hp_dirs[!dir].name);
		ret = -EBUSY;
		goto out;
	}
	if (a.guard_other) {
		ret = hp_guard(h, !dir, "prepare");
		if (ret)
			goto out;
	}
	if (a.teardown) {
		dev_warn(h->dev, "prepare: %d register(s) changed while clocked, reprogramming\n",
			 bad);
		hp_run(h, J713_HP_TEARDOWN);
	}
	if (a.program) {
		ret = hp_set_mck(h);
		if (ret)
			goto out;
		hp_run(h, J713_HP_PROGRAM);
		hp_run(h, J713_HP_PORT_OPEN);
	}
	if (a.reset)
		ret = hp_reset_serializer(h, dir);
	if (!ret)
		h->prepared |= BIT(dir);
out:
	mutex_unlock(&h->lock);
	return ret;
}

static int hp_trigger(struct snd_pcm_substream *s, int cmd, struct snd_soc_dai *dai)
{
	struct hp *h = hp_from(s);
	bool on;

	switch (cmd) {
	case SNDRV_PCM_TRIGGER_START:
	case SNDRV_PCM_TRIGGER_RESUME:
	case SNDRV_PCM_TRIGGER_PAUSE_RELEASE:
		on = true;
		break;
	case SNDRV_PCM_TRIGGER_STOP:
	case SNDRV_PCM_TRIGGER_SUSPEND:
	case SNDRV_PCM_TRIGGER_PAUSE_PUSH:
		on = false;
		break;
	default:
		return -EINVAL;
	}
	mutex_lock(&h->lock);
	hp_stage(h, s->stream, on);
	mutex_unlock(&h->lock);
	return 0;
}

/*
 * The one cleanup owner, for the last hw_free and for unbind; h->lock held.
 * Both guards run, so a stuck TX does not also leave RX enabled. With either
 * direction stuck on, leave the cluster clocked rather than touch it, and
 * return -EBUSY: the caller must then keep mck2 and the power domains as
 * they are too. ASoC ignores hw_free's return value, so unbind checks the
 * hardware again.
 */
static int hp_quiesce(struct hp *h, const char *what)
{
	int tx = hp_guard(h, J713_HP_TX, what);
	int rx = hp_guard(h, J713_HP_RX, what);

	if (tx || rx)
		return -EBUSY;
	hp_run(h, J713_HP_TEARDOWN);
	return 0;
}

static int hp_hw_free(struct snd_pcm_substream *s, struct snd_soc_dai *dai)
{
	struct hp *h = hp_from(s);
	enum j713_hp_dir dir = s->stream;
	int ret;

	mutex_lock(&h->lock);
	h->prepared &= ~BIT(dir);
	if ((h->prepared | h->active) & BIT(!dir))
		ret = hp_guard(h, dir, "hw_free");
	else
		ret = hp_quiesce(h, "hw_free");
	mutex_unlock(&h->lock);
	return ret;
}

static const struct snd_soc_dai_ops hp_dai_ops = {
	.set_fmt = hp_set_fmt,
	.hw_params = hp_hw_params,
	.prepare = hp_prepare,
	.trigger = hp_trigger,
	.hw_free = hp_hw_free,
};

static struct snd_soc_dai_driver hp_dai = {
	.name = "j713-headphones",
	.playback = {
		.stream_name = "Playback",
		.channels_min = 2,
		.channels_max = 2,
		.rates = SNDRV_PCM_RATE_48000,
		.formats = SNDRV_PCM_FMTBIT_S32_LE,
	},
	.capture = {
		.stream_name = "Capture",
		.channels_min = 1,
		.channels_max = 1,
		.rates = SNDRV_PCM_RATE_48000,
		.formats = SNDRV_PCM_FMTBIT_S32_LE,
	},
	.ops = &hp_dai_ops,
};

static int hp_pcm_open(struct snd_soc_component *c, struct snd_pcm_substream *s)
{
	struct dma_chan *chan = hp_from(s)->chan[s->stream];
	struct snd_dmaengine_dai_dma_data dma_data = {};
	struct snd_pcm_hardware hw = {
		.info = SNDRV_PCM_INFO_MMAP | SNDRV_PCM_INFO_MMAP_VALID |
			SNDRV_PCM_INFO_INTERLEAVED,
		.periods_min = 2,
		.periods_max = UINT_MAX,
		.period_bytes_min = 256,
		.period_bytes_max = dma_get_max_seg_size(chan->device->dev),
		.buffer_bytes_max = SIZE_MAX,
		.fifo_size = 16,
	};
	int ret;

	ret = snd_dmaengine_pcm_refine_runtime_hwparams(s, &dma_data, &hw, chan);
	if (ret)
		return ret;
	ret = snd_soc_set_runtime_hwparams(s, &hw);
	if (ret)
		return ret;
	return snd_dmaengine_pcm_open(s, chan);
}

static int hp_pcm_hw_params(struct snd_soc_component *c, struct snd_pcm_substream *s,
			    struct snd_pcm_hw_params *p)
{
	struct dma_slave_config config = {};
	int ret;

	/*
	 * apple-admac sends every split-channel RX config to its channel-1-only
	 * RX setter, which refuses channel 5 and would fail hw_params. Capture
	 * prepare writes channel 5's two words instead (hp_rx_dma()).
	 */
	if (s->stream == SNDRV_PCM_STREAM_CAPTURE)
		return 0;
	ret = snd_hwparams_to_dma_slave_config(s, p, &config);
	if (ret)
		return ret;
	config.dst_port_window_size = params_channels(p);
	return dmaengine_slave_config(snd_dmaengine_pcm_get_chan(s), &config);
}

static int hp_pcm_trigger(struct snd_soc_component *c, struct snd_pcm_substream *s,
			  int cmd)
{
	return snd_dmaengine_pcm_trigger(s, cmd);
}

static snd_pcm_uframes_t hp_pcm_pointer(struct snd_soc_component *c,
					struct snd_pcm_substream *s)
{
	return snd_dmaengine_pcm_pointer(s);
}

static int hp_pcm_sync_stop(struct snd_soc_component *c, struct snd_pcm_substream *s)
{
	snd_dmaengine_pcm_sync_stop(s);
	return 0;
}

static int hp_pcm_close(struct snd_soc_component *c, struct snd_pcm_substream *s)
{
	return snd_dmaengine_pcm_close(s);
}

static int hp_pcm_new(struct snd_soc_component *c, struct snd_soc_pcm_runtime *rtd)
{
	struct hp *h = snd_soc_card_get_drvdata(rtd->card);
	int i;

	for (i = 0; i < ARRAY_SIZE(h->chan); i++) {
		struct snd_pcm_substream *s = rtd->pcm->streams[i].substream;

		if (s)
			snd_pcm_set_managed_buffer(s, SNDRV_DMA_TYPE_DEV_IRAM,
						   h->chan[i]->device->dev, 512 * 1024,
						   SIZE_MAX);
	}
	return 0;
}

static const struct snd_soc_component_driver hp_component = {
	.name = "j713-headphones",
	.open = hp_pcm_open,
	.close = hp_pcm_close,
	.hw_params = hp_pcm_hw_params,
	.trigger = hp_pcm_trigger,
	.sync_stop = hp_pcm_sync_stop,
	.pointer = hp_pcm_pointer,
	.pcm_new = hp_pcm_new,
};

static const struct snd_soc_dapm_widget hp_widgets[] = {
	SND_SOC_DAPM_HP("Headphone", NULL),
	SND_SOC_DAPM_MIC("Headset Mic", NULL),
};

static const struct snd_soc_dapm_route hp_routes[] = {
	{ "Headphone", NULL, "Jack HP" },
	{ "Jack HS", NULL, "Headset Mic" },
};

static struct snd_soc_jack_pin hp_jack_pins[] = {
	{ .pin = "Headphone", .mask = SND_JACK_HEADPHONE },
	{ .pin = "Headset Mic", .mask = SND_JACK_MICROPHONE },
};

static int hp_link_hw_params(struct snd_pcm_substream *s, struct snd_pcm_hw_params *p)
{
	struct snd_soc_pcm_runtime *rtd = snd_soc_substream_to_rtd(s);

	/* Stock CS42L84 row: 3.072 MHz BCLK through its PLL to 12.288 MHz. */
	return snd_soc_dai_set_sysclk(snd_soc_rtd_to_codec(rtd, 0), 0, HP_BCLK,
				      SND_SOC_CLOCK_IN);
}

static const struct snd_soc_ops hp_link_ops = {
	.hw_params = hp_link_hw_params,
};

static int hp_link_init(struct snd_soc_pcm_runtime *rtd)
{
	struct hp *h = snd_soc_card_get_drvdata(rtd->card);
	int ret;

	/* Kcontrols "Headphone Jack" and "Headset Mic Jack" */
	ret = snd_soc_card_jack_new_pins(rtd->card, "Headphone Jack",
					 SND_JACK_HEADSET | SND_JACK_HEADPHONE,
					 &h->jack, hp_jack_pins,
					 ARRAY_SIZE(hp_jack_pins));
	if (ret)
		return ret;
	return snd_soc_component_set_jack(snd_soc_rtd_to_codec(rtd, 0)->component,
					  &h->jack, NULL);
}

static void hp_link_exit(struct snd_soc_pcm_runtime *rtd)
{
	snd_soc_component_set_jack(snd_soc_rtd_to_codec(rtd, 0)->component,
				   NULL, NULL);
}

/* Without a cap, 100 % on the desktop is +12 dB of digital gain. */
static int hp_late_probe(struct snd_soc_card *card)
{
	int ret = snd_soc_limit_volume(card, "Jack DAC Playback Volume", HP_VOLUME_MAX);

	return ret < 0 ? ret : 0;
}

static int regs_show(struct seq_file *m, void *unused)
{
	struct hp *h = m->private;
	struct j713_hp_plan p;
	u32 off;
	int bad;

	mutex_lock(&h->lock);
	seq_printf(m, "cluster 2 %s, TX_ACTIVE (+0168) %08x, RX_ACTIVE (+0158) %08x, prepared %x active %x\n",
		   hp_clocked(h) ? "clocked" : "idle", rd(h, J713_HP_TX_ACTIVE),
		   rd(h, J713_HP_RX_ACTIVE), h->prepared, h->active);
	seq_puts(m, "recipe (final writes of PROGRAM and PORT_OPEN):\n");
	bad = hp_check(h, J713_HP_PROGRAM, m);
	bad += hp_check(h, J713_HP_PORT_OPEN, m);
	seq_printf(m, "mismatches: %d\n", bad);
	seq_printf(m, "mck2 @%#llx: %08x (boot %08x)\n", PMGR_MCK2, readl(h->mck),
		   h->mck_boot);
	seq_puts(m, "global:\n");
	for (off = J713_HP_SYNC_OMUX_SEL(0); off <= J713_HP_ALIGNED_ENABLE_TRIGGER; off += 4)
		seq_printf(m, "  +%04x: %08x\n", off, rd(h, off));
	seq_printf(m, "port 2: enables %08x clock_sel %08x data_sel %08x (boot %08x %08x %08x)\n",
		   rd(h, J713_HP_PORT_ENABLES), rd(h, J713_HP_PORT_CLOCK_SEL),
		   rd(h, J713_HP_PORT_DATA_SEL), h->port_boot[0], h->port_boot[1],
		   h->port_boot[2]);
	seq_printf(m, "serializer control %08x, fifo %08x en %08x level %08x\n",
		   rd(h, J713_HP_TXS_CONTROL), rd(h, J713_HP_ADP_FIFO),
		   rd(h, J713_HP_ADP_FIFO_EN), rd(h, J713_HP_ADP_LEVEL));
	seq_printf(m, "rx serializer control %08x conf %08x delay %08x masks %08x %08x src %08x lb %08x, rx level %08x\n",
		   rd(h, J713_HP_RXS_CONTROL), rd(h, J713_HP_RXS_CONF),
		   rd(h, J713_HP_RXS_DELAY), rd(h, J713_HP_RXS_MASK_HI),
		   rd(h, J713_HP_RXS_MASK_LO), rd(h, J713_HP_RXS_SOURCES),
		   rd(h, J713_HP_RXS_LOOPBACK), rd(h, J713_HP_ADP_RX_LEVEL));
	seq_printf(m, "admac ch%d @%#llx: ctl %08x config %08x carveout %08x enable %08x\n",
		   J713_HP_RX_DMA_CHAN, J713_HP_RX_DMA_PHYS,
		   readl(h->rxdma + J713_HP_DMA_CTL), readl(h->rxdma + J713_HP_DMA_CONFIG),
		   readl(h->rxdma + J713_HP_DMA_CARVEOUT), readl(h->rxdma + J713_HP_DMA_ENABLE));
	if (!WARN_ON(j713_hp_rx_dma_plan(&p)))
		seq_printf(m, "ch5 mismatches: %d\n", hp_verify(h->rxdma, &p, "ch5", m));
	mutex_unlock(&h->lock);
	return 0;
}
DEFINE_SHOW_ATTRIBUTE(regs);

/*
 * Probe's unwind, and unbind's after hp_quiesce(). keep_power leaves mck2's
 * source and the power-domain links in place for good: a serializer is
 * still enabled.
 */
static void hp_put(struct hp *h, bool keep_power)
{
	int i;

	if (!keep_power) {
		/* Source select only: bit 8 follows the power domain (0x90100100 off, 0x90100000 on). */
		if (h->mck && (readl(h->mck) ^ h->mck_boot) & MCK_SRC_SEL) {
			writel((readl(h->mck) & ~MCK_SRC_SEL) | (h->mck_boot & MCK_SRC_SEL),
			       h->mck);
			if (hp_mck_settle(h))
				dev_err(h->dev, "mck2: restore to source %lu still pending\n",
					FIELD_GET(MCK_SRC_SEL, h->mck_boot));
		}
		for (i = ARRAY_SIZE(h->pd) - 1; i >= 0; i--) {
			if (h->pd_link[i])
				device_link_del(h->pd_link[i]);
			if (!IS_ERR_OR_NULL(h->pd[i]))
				dev_pm_domain_detach(h->pd[i], true);
		}
	}
	for (i = ARRAY_SIZE(h->chan) - 1; i >= 0; i--)
		if (!IS_ERR_OR_NULL(h->chan[i]))
			dma_release_channel(h->chan[i]);
	of_node_put(h->codec_np);
}

/* Unbind: runs after the card and component are unregistered (PCM closed). */
static void hp_release(void *data)
{
	struct hp *h = data;
	bool stuck;

	debugfs_remove_recursive(h->dbg);
	mutex_lock(&h->lock);
	stuck = hp_quiesce(h, "unbind") != 0;
	mutex_unlock(&h->lock);
	if (stuck)
		dev_err(h->dev, "unbind: TX or RX stuck on; mck2 and the power domains stay as they are until power-off, and rebinding needs a power cycle\n");
	hp_put(h, stuck);
}

static int hp_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct hp *h;
	int i, ret;

	h = devm_kzalloc(dev, sizeof(*h), GFP_KERNEL);
	if (!h)
		return -ENOMEM;
	h->dev = dev;
	mutex_init(&h->lock);

	/* Not requested: these windows sit inside the speaker MCA's range. */
	h->mca = devm_ioremap(dev, J713_HP_MCA_PHYS, J713_HP_MCA_SIZE);
	if (!h->mca)
		return -ENOMEM;
	h->mck = devm_ioremap(dev, PMGR_MCK2, 4);
	if (!h->mck)
		return -ENOMEM;
	h->mck_boot = readl(h->mck);
	/* Not requested either: channel 5's window sits inside apple-admac's range. */
	h->rxdma = devm_ioremap(dev, J713_HP_RX_DMA_PHYS, J713_HP_RX_DMA_SIZE);
	if (!h->rxdma)
		return -ENOMEM;

	for (i = 0; i < ARRAY_SIZE(h->pd); i++) {
		h->pd[i] = dev_pm_domain_attach_by_id(dev, i);
		if (IS_ERR_OR_NULL(h->pd[i])) {
			ret = h->pd[i] ? PTR_ERR(h->pd[i]) : -ENODEV;
			goto err;
		}
		h->pd_link[i] = device_link_add(dev, h->pd[i],
						DL_FLAG_STATELESS | DL_FLAG_PM_RUNTIME |
						DL_FLAG_RPM_ACTIVE);
		if (!h->pd_link[i]) {
			ret = -EINVAL;
			goto err;
		}
	}

	h->chan[J713_HP_TX] = dma_request_chan(dev, "tx");
	if (IS_ERR(h->chan[J713_HP_TX])) {
		ret = PTR_ERR(h->chan[J713_HP_TX]);
		goto err;
	}
	h->chan[J713_HP_RX] = dma_request_chan(dev, "rx");
	if (IS_ERR(h->chan[J713_HP_RX])) {
		ret = PTR_ERR(h->chan[J713_HP_RX]);
		goto err;
	}
	/* The DT names the channel, the plan header its registers: they must agree. */
	if (h->chan[J713_HP_RX]->chan_id != J713_HP_RX_DMA_CHAN) {
		dev_err(dev, "rx DMA is channel %d, expected %d\n",
			h->chan[J713_HP_RX]->chan_id, J713_HP_RX_DMA_CHAN);
		ret = -ENODEV;
		goto err;
	}

	h->codec_np = of_parse_phandle(dev->of_node, "omarchy,codec", 0);
	if (!h->codec_np) {
		ret = -ENODEV;
		goto err;
	}

	/* Runs after the card and component below are unregistered. */
	ret = devm_add_action_or_reset(dev, hp_release, h);
	if (ret)
		return ret;

	/* Port 2 is not the speaker's; read with the domains up. */
	h->port_boot[0] = rd(h, J713_HP_PORT_ENABLES);
	h->port_boot[1] = rd(h, J713_HP_PORT_CLOCK_SEL);
	h->port_boot[2] = rd(h, J713_HP_PORT_DATA_SEL);

	h->dbg = debugfs_create_dir("j713_headphones", NULL);
	debugfs_create_file("regs", 0400, h->dbg, h, &regs_fops);

	ret = devm_snd_soc_register_component(dev, &hp_component, &hp_dai, 1);
	if (ret)
		return dev_err_probe(dev, ret, "component\n");

	h->comps[0].of_node = dev->of_node;
	h->comps[0].dai_name = hp_dai.name;
	h->comps[1].of_node = h->codec_np;
	h->comps[1].dai_name = "cs42l84";
	h->comps[2].of_node = dev->of_node;

	h->link.name = "Headphones";
	h->link.stream_name = "Headphones";
	h->link.cpus = &h->comps[0];
	h->link.num_cpus = 1;
	h->link.codecs = &h->comps[1];
	h->link.num_codecs = 1;
	h->link.platforms = &h->comps[2];
	h->link.num_platforms = 1;
	h->link.dai_fmt = SND_SOC_DAIFMT_I2S | SND_SOC_DAIFMT_IB_IF |
			  SND_SOC_DAIFMT_CBC_CFC;
	h->link.nonatomic = 1;
	h->link.init = hp_link_init;
	h->link.exit = hp_link_exit;
	h->link.ops = &hp_link_ops;

	/* name is the card id and the UCM directory */
	h->card.name = "J713Headphones";
	h->card.driver_name = "J713Headphones";
	h->card.long_name = "J713 Headphones";
	h->card.owner = THIS_MODULE;
	h->card.dev = dev;
	h->card.dai_link = &h->link;
	h->card.num_links = 1;
	h->card.dapm_widgets = hp_widgets;
	h->card.num_dapm_widgets = ARRAY_SIZE(hp_widgets);
	h->card.dapm_routes = hp_routes;
	h->card.num_dapm_routes = ARRAY_SIZE(hp_routes);
	h->card.late_probe = hp_late_probe;
	snd_soc_card_set_drvdata(&h->card, h);

	ret = devm_snd_soc_register_card(dev, &h->card);
	if (ret)
		return dev_err_probe(dev, ret, "card\n");
	dev_info(dev, "mck2 boot %#x, port 2 boot %#x/%#x/%#x\n", h->mck_boot,
		 h->port_boot[0], h->port_boot[1], h->port_boot[2]);
	return 0;

err:
	hp_put(h, false);
	return dev_err_probe(dev, ret, "probe\n");
}

static const struct of_device_id hp_of_match[] = {
	{ .compatible = "omarchy,j713-headphones" },
	{}
};
MODULE_DEVICE_TABLE(of, hp_of_match);

static struct platform_driver hp_driver = {
	.driver = {
		.name = "j713-headphones",
		.of_match_table = hp_of_match,
	},
	.probe = hp_probe,
};
module_platform_driver(hp_driver);

MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("J713 headphone jack card (MCA cluster 2 + CS42L84): headphones and headset mic");
