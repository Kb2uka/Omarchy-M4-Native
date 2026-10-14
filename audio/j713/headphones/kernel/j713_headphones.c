// SPDX-License-Identifier: GPL-2.0
/*
 * J713 headphone jack card "J713Headphones": MCA cluster 2 -> I2S port 2 ->
 * CS42L84, beside the speaker card (cluster 0), which keeps running.
 *
 * All register values and the shared-register contract live in
 * j713_hp_plan.h. This file executes those plans and adds the only other
 * accesses: the TX_ACTIVE (+0x168) read in the guard, the mck2 mux switch
 * and the serializer reset, all inside the headphone stream's own windows
 * apart from that read.
 *
 * Callbacks:
 *  - probe: map, attach the power domains, request DMA, record the boot
 *    values of mck2 and port 2. No register writes.
 *  - hw_params: format check only (WirePlumber probes cards by opening
 *    them with real hw_params/hw_free cycles).
 *  - prepare: guard on TX_ACTIVE, then, with the cluster idle, mck2 to nco3
 *    and the PROGRAM and PORT_OPEN plans. A re-prepare with the cluster
 *    still clocked verifies those registers instead of rewriting them
 *    (timing registers are written only while the cluster's clocks are off,
 *    the speaker driver's j713_gates_off rule). Then a serializer reset.
 *  - trigger: START or STOP plan, the only staged-enable writes.
 *  - hw_free and unbind: hp_quiesce(), the one cleanup owner. It runs the
 *    TEARDOWN plan only once TX_ACTIVE bit 2 reads clear. If TX cannot be
 *    stopped it leaves cluster 2 clocked, and unbind then also keeps mck2
 *    on nco3 and the power domains linked (until power-off) rather than
 *    pull the clock or power from under an enabled serializer. Otherwise
 *    unbind puts mck2 back to its boot source and drops the domains.
 */
#include <linux/bitfield.h>
#include <linux/debugfs.h>
#include <linux/delay.h>
#include <linux/dma-mapping.h>
#include <linux/dmaengine.h>
#include <linux/io.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/pm_domain.h>
#include <linux/seq_file.h>
#include <sound/dmaengine_pcm.h>
#include <sound/jack.h>
#include <sound/pcm_params.h>
#include <sound/soc.h>

#include "j713_hp_plan.h"

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
	u32 mck_boot;
	u32 port_boot[3];
	struct device *pd[3];
	struct device_link *pd_link[3];
	struct dma_chan *chan;
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

static void hp_run(struct hp *h, enum j713_hp_step step)
{
	struct j713_hp_plan p;
	u32 i;

	if (WARN_ON(j713_hp_plan(step, h->port_boot, &p)))
		return;
	for (i = 0; i < p.count; i++) {
		const struct j713_hp_write *w = &p.writes[i];
		u32 val = w->value;

		if (w->mask != ~0U)
			val |= rd(h, w->offset) & ~w->mask;
		wr(h, w->offset, val);
	}
}

/*
 * Count the registers that do not hold what the plan's final writes put
 * there; print every checked write to m if given.
 */
static int hp_check(struct hp *h, enum j713_hp_step step, struct seq_file *m)
{
	struct j713_hp_plan p;
	int bad = 0;
	u32 i;

	if (WARN_ON(j713_hp_plan(step, h->port_boot, &p)))
		return 1;
	for (i = 0; i < p.count; i++) {
		const struct j713_hp_write *w = &p.writes[i];
		u32 live;
		bool ok;

		if (!j713_hp_final(&p, i) || j713_hp_write_only(w->offset))
			continue;
		live = rd(h, w->offset);
		ok = (live & w->mask) == w->value;
		bad += !ok;
		if (m)
			seq_printf(m, "  %-9s +%04x mask %08x want %08x live %08x%s\n",
				   j713_hp_step_name(step), w->offset, w->mask,
				   w->value, live, ok ? "" : "  MISMATCH");
	}
	return bad;
}

static bool hp_clocked(struct hp *h)
{
	return (rd(h, J713_HP_MCLK_STATUS) | rd(h, J713_HP_SYNC_STATUS)) & J713_HP_EN;
}

/* The trigger is frame-aligned, so allow a few frames for TX_ACTIVE to follow. */
static int hp_wait_tx_idle(struct hp *h)
{
	int i;

	for (i = 0; i < 1000; i++) {
		if (!(rd(h, J713_HP_TX_ACTIVE) & J713_HP_BIT))
			return 0;
		udelay(1);
	}
	return -EBUSY;
}

/* No cluster-2 timing write unless TX_ACTIVE bit 2 reads clear. */
static int hp_guard(struct hp *h, const char *what)
{
	if (!hp_wait_tx_idle(h))
		return 0;
	dev_warn(h->dev, "%s: TX_ACTIVE %#x still has bit 2, stopping again\n",
		 what, rd(h, J713_HP_TX_ACTIVE));
	hp_run(h, J713_HP_STOP);
	if (!hp_wait_tx_idle(h))
		return 0;
	dev_err(h->dev, "%s: TX_ACTIVE %#x: TX still enabled, refusing\n",
		what, rd(h, J713_HP_TX_ACTIVE));
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
 * every start with the clocks running and TX not enabled; without it the
 * FIFO keeps stale words and every start sounds different.
 */
static int hp_reset_serializer(struct hp *h)
{
	u32 fifo = rd(h, J713_HP_ADP_FIFO_EN);
	u32 conf = rd(h, J713_HP_TXS_CONF);
	u32 ctrl = 0;
	int i;

	wr(h, J713_HP_ADP_FIFO_EN, fifo & ~1U);
	wr(h, J713_HP_TXS_CONTROL, rd(h, J713_HP_TXS_CONTROL) | 2);
	wr(h, J713_HP_TXS_CONF, conf & ~J713_HP_CONF_SYNC_SEL);
	wr(h, J713_HP_TXS_CONF, conf | J713_HP_CONF_SYNC_SEL);
	udelay(200);
	for (i = 0; i < 100; i++) {
		ctrl = rd(h, J713_HP_TXS_CONTROL);
		if (!(ctrl & 0xa))
			break;
	}
	wr(h, J713_HP_TXS_CONF, conf);
	wr(h, J713_HP_ADP_FIFO_EN, fifo | 1);
	if (ctrl & 0xa) {
		dev_err(h->dev, "serializer reset: control %#x did not clear\n", ctrl);
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
	if (params_rate(p) != HP_RATE || params_channels(p) != 2 ||
	    params_format(p) != SNDRV_PCM_FORMAT_S32_LE)
		return -EINVAL;
	return 0;
}

/* ALSA calls this with the stream stopped. */
static int hp_prepare(struct snd_pcm_substream *s, struct snd_soc_dai *dai)
{
	struct hp *h = hp_from(s);
	int ret, bad;

	ret = hp_guard(h, "prepare");
	if (ret)
		return ret;
	if (hp_clocked(h)) {
		bad = hp_check(h, J713_HP_PROGRAM, NULL) +
		      hp_check(h, J713_HP_PORT_OPEN, NULL);
		if (!bad)
			return hp_reset_serializer(h);
		dev_warn(h->dev, "prepare: %d register(s) changed while clocked, reprogramming\n",
			 bad);
		hp_run(h, J713_HP_TEARDOWN);
	}
	ret = hp_set_mck(h);
	if (ret)
		return ret;
	hp_run(h, J713_HP_PROGRAM);
	hp_run(h, J713_HP_PORT_OPEN);
	return hp_reset_serializer(h);
}

static int hp_trigger(struct snd_pcm_substream *s, int cmd, struct snd_soc_dai *dai)
{
	struct hp *h = hp_from(s);

	switch (cmd) {
	case SNDRV_PCM_TRIGGER_START:
	case SNDRV_PCM_TRIGGER_RESUME:
	case SNDRV_PCM_TRIGGER_PAUSE_RELEASE:
		hp_run(h, J713_HP_START);
		return 0;
	case SNDRV_PCM_TRIGGER_STOP:
	case SNDRV_PCM_TRIGGER_SUSPEND:
	case SNDRV_PCM_TRIGGER_PAUSE_PUSH:
		hp_run(h, J713_HP_STOP);
		return 0;
	}
	return -EINVAL;
}

/*
 * The one cleanup owner, for hw_free and unbind. With TX stuck on, leave
 * the cluster clocked rather than touch it, and return -EBUSY: the caller
 * must then keep mck2 and the power domains as they are too. ASoC ignores
 * hw_free's return value, so unbind checks the hardware again.
 */
static int hp_quiesce(struct hp *h, const char *what)
{
	int ret = hp_guard(h, what);

	if (ret)
		return ret;
	hp_run(h, J713_HP_TEARDOWN);
	return 0;
}

static int hp_hw_free(struct snd_pcm_substream *s, struct snd_soc_dai *dai)
{
	return hp_quiesce(hp_from(s), "hw_free");
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
	.ops = &hp_dai_ops,
};

static int hp_pcm_open(struct snd_soc_component *c, struct snd_pcm_substream *s)
{
	struct hp *h = hp_from(s);
	struct snd_dmaengine_dai_dma_data dma_data = {};
	struct snd_pcm_hardware hw = {
		.info = SNDRV_PCM_INFO_MMAP | SNDRV_PCM_INFO_MMAP_VALID |
			SNDRV_PCM_INFO_INTERLEAVED,
		.periods_min = 2,
		.periods_max = UINT_MAX,
		.period_bytes_min = 256,
		.period_bytes_max = dma_get_max_seg_size(h->chan->device->dev),
		.buffer_bytes_max = SIZE_MAX,
		.fifo_size = 16,
	};
	int ret;

	ret = snd_dmaengine_pcm_refine_runtime_hwparams(s, &dma_data, &hw, h->chan);
	if (ret)
		return ret;
	ret = snd_soc_set_runtime_hwparams(s, &hw);
	if (ret)
		return ret;
	return snd_dmaengine_pcm_open(s, h->chan);
}

static int hp_pcm_hw_params(struct snd_soc_component *c, struct snd_pcm_substream *s,
			    struct snd_pcm_hw_params *p)
{
	struct dma_slave_config config = {};
	int ret;

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
	struct snd_pcm_substream *s =
		rtd->pcm->streams[SNDRV_PCM_STREAM_PLAYBACK].substream;

	if (!s)
		return 0;
	snd_pcm_set_managed_buffer(s, SNDRV_DMA_TYPE_DEV_IRAM,
				   h->chan->device->dev, 512 * 1024, SIZE_MAX);
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
	u32 off;
	int bad;

	seq_printf(m, "cluster 2 %s, TX_ACTIVE (+0168) %08x\n",
		   hp_clocked(h) ? "clocked" : "idle", rd(h, J713_HP_TX_ACTIVE));
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
	return 0;
}
DEFINE_SHOW_ATTRIBUTE(regs);

/*
 * Probe's unwind, and unbind's after hp_quiesce(). keep_power leaves mck2's
 * source and the power-domain links in place for good: TX is still enabled.
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
	if (!IS_ERR_OR_NULL(h->chan))
		dma_release_channel(h->chan);
	of_node_put(h->codec_np);
}

/* Unbind: runs after the card and component are unregistered (PCM closed). */
static void hp_release(void *data)
{
	struct hp *h = data;
	bool stuck;

	debugfs_remove_recursive(h->dbg);
	stuck = hp_quiesce(h, "unbind") != 0;
	if (stuck)
		dev_err(h->dev, "unbind: TX stuck on; mck2 and the power domains stay as they are until power-off, and rebinding needs a power cycle\n");
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

	/* Not requested: these windows sit inside the speaker MCA's range. */
	h->mca = devm_ioremap(dev, J713_HP_MCA_PHYS, J713_HP_MCA_SIZE);
	if (!h->mca)
		return -ENOMEM;
	h->mck = devm_ioremap(dev, PMGR_MCK2, 4);
	if (!h->mck)
		return -ENOMEM;
	h->mck_boot = readl(h->mck);

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

	h->chan = dma_request_chan(dev, "tx");
	if (IS_ERR(h->chan)) {
		ret = PTR_ERR(h->chan);
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
	h->link.playback_only = 1;
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
MODULE_DESCRIPTION("J713 headphone jack card (MCA cluster 2 + CS42L84)");
