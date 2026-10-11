/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef MACAUDIO_J713_H
#define MACAUDIO_J713_H
#include <sound/pcm_params.h>
#include "j713-audio.h"

static int j713_ctl_info(struct snd_kcontrol *k, struct snd_ctl_elem_info *info)
{
	info->type = SNDRV_CTL_ELEM_TYPE_INTEGER64;
	info->count = k->private_value;
	info->value.integer64.min = 0;
	info->value.integer64.max = S64_MAX;
	return 0;
}
static int j713_generation_get(struct snd_kcontrol *k, struct snd_ctl_elem_value *v)
{
	struct macaudio_snd_data *ma = snd_soc_card_get_drvdata(snd_kcontrol_chip(k));
	v->value.integer64.value[0] = j713_audio_generation(ma->j713_group);
	return 0;
}
static int j713_owner_lock(struct snd_kcontrol *k, struct snd_ctl_file *owner)
{
	struct macaudio_snd_data *ma = snd_soc_card_get_drvdata(snd_kcontrol_chip(k));
	int ret;
	mutex_lock(&ma->volume_lock_mutex);
	ret = j713_audio_set_owner(ma->j713_group, owner, true);
	if (!ret) ma->speaker_lock_owner = owner;
	mutex_unlock(&ma->volume_lock_mutex);
	return ret;
}
static void j713_owner_unlock(struct snd_kcontrol *k)
{
	struct macaudio_snd_data *ma = snd_soc_card_get_drvdata(snd_kcontrol_chip(k));
	mutex_lock(&ma->volume_lock_mutex);
	if (ma->speaker_lock_owner)
		j713_audio_set_owner(ma->j713_group, ma->speaker_lock_owner, false);
	ma->speaker_lock_owner = NULL;
	mutex_unlock(&ma->volume_lock_mutex);
}
static int j713_unlock_get(struct snd_kcontrol *k, struct snd_ctl_elem_value *v)
{
	v->value.integer.value[0] = 0;
	return 0;
}
static int j713_unlock_put(struct snd_kcontrol *k, struct snd_ctl_elem_value *v)
{
	/* Legacy untagged writes never grant output on this board. */
	return -EOPNOTSUPP;
}
static int j713_lease_put(struct snd_kcontrol *k, struct snd_ctl_elem_value *v)
{
	struct snd_soc_card *card = snd_kcontrol_chip(k);
	struct macaudio_snd_data *ma = snd_soc_card_get_drvdata(card);
	struct snd_kcontrol *child;
	unsigned int found = 0;
	int ret = -EPERM;
	mutex_lock(&ma->volume_lock_mutex);
	if (!ma->speaker_lock_owner || k->count != 1 ||
	    k->vd[0].owner != ma->speaker_lock_owner) goto out;
	list_for_each_entry(child, &card->snd_card->controls, list) {
		if (!strstr(child->id.name, "Speaker Volume") &&
		    !strstr(child->id.name, "Amp Gain Volume")) continue;
		if (!strcmp(child->id.name, "Speaker Volume Unlock")) continue;
		if (child->count != 1 || child->vd[0].owner != ma->speaker_lock_owner) goto out;
		found++;
	}
	if (found != 8) goto out;
	ret = j713_audio_renew(ma->j713_group, ma->speaker_lock_owner,
		v->value.integer64.value[0], v->value.integer64.value[1]);
out:
	if (ret) j713_audio_revoke(ma->j713_group);
	mutex_unlock(&ma->volume_lock_mutex);
	return ret;
}
static void j713_lease_unlock(struct snd_kcontrol *k)
{
	struct macaudio_snd_data *ma = snd_soc_card_get_drvdata(snd_kcontrol_chip(k));
	j713_audio_revoke(ma->j713_group);
}
static const struct snd_kcontrol_new j713_card_controls[] = {
	{ .iface = SNDRV_CTL_ELEM_IFACE_MIXER, .name = "Speaker Sample Rate",
	  .access = SNDRV_CTL_ELEM_ACCESS_READ | SNDRV_CTL_ELEM_ACCESS_VOLATILE,
	  .info = macaudio_sss_info, .get = macaudio_sss_get },
	{ .iface = SNDRV_CTL_ELEM_IFACE_MIXER, .name = "Speaker Volume Unlock",
	  .access = SNDRV_CTL_ELEM_ACCESS_READWRITE, .info = macaudio_slk_info,
	  .get = j713_unlock_get, .put = j713_unlock_put,
	  .lock = j713_owner_lock, .unlock = j713_owner_unlock },
	{ .iface = SNDRV_CTL_ELEM_IFACE_MIXER, .name = "Speaker Protection Generation",
	  .access = SNDRV_CTL_ELEM_ACCESS_READ | SNDRV_CTL_ELEM_ACCESS_VOLATILE,
	  .info = j713_ctl_info, .get = j713_generation_get, .private_value = 1 },
	{ .iface = SNDRV_CTL_ELEM_IFACE_MIXER, .name = "Speaker Protection Lease",
	  .access = SNDRV_CTL_ELEM_ACCESS_WRITE, .info = j713_ctl_info,
	  .put = j713_lease_put, .unlock = j713_lease_unlock, .private_value = 2 },
};

static int j713_fe_startup(struct snd_pcm_substream *s)
{
	int ret = snd_pcm_hw_constraint_single(s->runtime, SNDRV_PCM_HW_PARAM_RATE, 48000);
	if (!ret) ret = snd_pcm_hw_constraint_single(s->runtime, SNDRV_PCM_HW_PARAM_CHANNELS,
						   s->stream ? 8 : 4);
	if (!ret) ret = snd_pcm_hw_constraint_mask64(s->runtime, SNDRV_PCM_HW_PARAM_FORMAT,
						   s->stream ? SNDRV_PCM_FMTBIT_S16_LE : SNDRV_PCM_FMTBIT_S32_LE);
	return ret;
}
static int j713_fe_params(struct snd_pcm_substream *s, struct snd_pcm_hw_params *p)
{
	struct macaudio_snd_data *ma = snd_soc_card_get_drvdata(snd_soc_substream_to_rtd(s)->card);
	if (params_rate(p) != 48000 || params_channels(p) != (s->stream ? 8 : 4) ||
	    params_format(p) != (s->stream ? SNDRV_PCM_FORMAT_S16_LE : SNDRV_PCM_FORMAT_S32_LE)) return -EINVAL;
	/* This control describes the protected feedback stream, including idle MUTE. */
	if (s->stream) ma->speaker_sample_rate = 48000;
	return 0;
}
static int j713_fe_free(struct snd_pcm_substream *s)
{
	struct macaudio_snd_data *ma = snd_soc_card_get_drvdata(snd_soc_substream_to_rtd(s)->card);
	if (s->stream) {
		j713_audio_revoke(ma->j713_group);
		ma->speaker_sample_rate = 0;
	}
	/* Actual MCA must still drain DMA and restore routes even if LOW failed. */
	return 0;
}
static void j713_fe_close(struct snd_pcm_substream *s) { j713_fe_free(s); }
static int j713_fe_init(struct snd_soc_pcm_runtime *rtd)
{
	return snd_soc_dai_set_bclk_ratio(snd_soc_rtd_to_cpu(rtd, 0), 250);
}
static const struct snd_soc_ops j713_fe_ops = {
	.startup = j713_fe_startup, .hw_params = j713_fe_params,
	.hw_free = j713_fe_free, .shutdown = j713_fe_close,
};
static const struct snd_soc_dapm_widget j713_card_widgets[] = {
	SND_SOC_DAPM_OUTPUT("Speaker"), SND_SOC_DAPM_INPUT("Speaker Feedback"),
};
static const struct snd_soc_dapm_route j713_card_routes[] = {
	{ "I2S0 TX", NULL, "PCM0 TX" },
	{ "Speaker", NULL, "Left Woofer OUT" }, { "Speaker", NULL, "Right Woofer OUT" },
	{ "Speaker", NULL, "Left Tweeter OUT" }, { "Speaker", NULL, "Right Tweeter OUT" },
	{ "I2S0 RX", NULL, "Speaker Feedback" }, { "PCM0 RX", NULL, "I2S0 RX" },
};
struct j713_links {
	struct snd_soc_dai_link link[2];
	struct snd_soc_dai_link_component cpu[2], platform, dummy, codec[4];
	struct snd_soc_codec_conf conf[4];
};
static void j713_put_nodes(void *data)
{
	struct j713_links *links = data;
	unsigned int i;
	of_node_put(links->cpu[0].of_node);
	for (i = 0; i < 4; i++) of_node_put(links->codec[i].of_node);
}
static int j713_card_probe(struct snd_soc_card *card)
{
	strscpy(card->snd_card->id, "AppleJ713", sizeof(card->snd_card->id));
	return 0;
}
static int macaudio_j713_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct macaudio_snd_data *ma;
	struct j713_links *links;
	static const char * const prefixes[] = { "Left Woofer", "Right Woofer", "Left Tweeter", "Right Tweeter" };
	static const unsigned int addresses[] = {0x38,0x3b,0x39,0x3c};
	unsigned int i, addr;
	int ret;
	if (!of_machine_is_compatible("apple,j713") || !of_machine_is_compatible("apple,t8132")) return -ENODEV;
	ma = devm_kzalloc(dev, sizeof(*ma), GFP_KERNEL);
	links = devm_kzalloc(dev, sizeof(*links), GFP_KERNEL);
	if (!ma || !links) return -ENOMEM;
	mutex_init(&ma->volume_lock_mutex);
	ma->j713_group = devm_j713_audio_get(dev);
	if (IS_ERR(ma->j713_group)) return PTR_ERR(ma->j713_group);
	links->cpu[0].of_node = of_find_node_by_path("/soc/i2s@393600000");
	if (!links->cpu[0].of_node) return -EINVAL;
	ret = devm_add_action_or_reset(dev, j713_put_nodes, links);
	if (ret) return ret;
	if (!of_device_is_compatible(links->cpu[0].of_node, "apple,t8132-mca")) return -ENODEV;
	links->cpu[0].dai_name = "mca-pcm-0";
	links->cpu[1].of_node = links->cpu[0].of_node;
	links->cpu[1].dai_name = "mca-i2s-0";
	links->platform.of_node = links->cpu[0].of_node;
	links->dummy = (struct snd_soc_dai_link_component){ .name = "snd-soc-dummy", .dai_name = "snd-soc-dummy-dai" };
	for (i = 0; i < 4; i++) {
		links->codec[i].of_node = of_parse_phandle(dev->of_node, "apple,codecs", i);
		if (!links->codec[i].of_node || !of_device_is_compatible(links->codec[i].of_node, "ti,sn012776") ||
		    of_property_read_u32(links->codec[i].of_node, "reg", &addr) || addr != addresses[i]) return -EINVAL;
		links->codec[i].dai_name = "tas2764 ASI1";
		links->conf[i].dlc.of_node = links->codec[i].of_node;
		links->conf[i].name_prefix = prefixes[i];
	}
	links->link[0] = (struct snd_soc_dai_link){
		.name = "Primary", .stream_name = "Primary", .dynamic = 1, .nonatomic = 1,
		.dpcm_merged_rate = 1, .dpcm_merged_chan = 1, .dpcm_merged_format = 1,
		.dai_fmt = SND_SOC_DAIFMT_I2S | SND_SOC_DAIFMT_NB_IF | SND_SOC_DAIFMT_CBC_CFC,
		.init = j713_fe_init, .ops = &j713_fe_ops,
		.cpus = &links->cpu[0], .num_cpus = 1, .codecs = &links->dummy, .num_codecs = 1,
		.platforms = &links->platform, .num_platforms = 1,
	};
	links->link[1] = (struct snd_soc_dai_link){
		.name = "Speakers", .stream_name = "Speakers", .no_pcm = 1,
		.dai_fmt = SND_SOC_DAIFMT_I2S | SND_SOC_DAIFMT_NB_IF | SND_SOC_DAIFMT_CBC_CFC,
		.cpus = &links->cpu[1], .num_cpus = 1, .codecs = links->codec, .num_codecs = 4,
	};
	ma->card = (struct snd_soc_card){
		.name = "Apple J713", .long_name = "Apple J713", .driver_name = "macaudio",
		.owner = THIS_MODULE, .dev = dev, .dai_link = links->link, .num_links = 2,
		.probe = j713_card_probe,
		.codec_conf = links->conf, .num_configs = 4,
		.component_chaining = true, .fully_routed = true,
		.controls = j713_card_controls, .num_controls = ARRAY_SIZE(j713_card_controls),
		.dapm_widgets = j713_card_widgets, .num_dapm_widgets = ARRAY_SIZE(j713_card_widgets),
		.dapm_routes = j713_card_routes, .num_dapm_routes = ARRAY_SIZE(j713_card_routes),
	};
	snd_soc_card_set_drvdata(&ma->card, ma);
	platform_set_drvdata(pdev, ma);
	return devm_snd_soc_register_card(dev, &ma->card);
}
#endif
