/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef TAS2764_J713_H
#define TAS2764_J713_H
#include "j713-audio.h"

struct tas2764_j713 {
	struct j713_audio *group;
	unsigned int index;
};

static int j713_level_get(struct snd_kcontrol *k, struct snd_ctl_elem_value *v)
{
	struct tas2764_j713 *codec = snd_soc_component_get_drvdata(snd_kcontrol_chip(k));
	unsigned int reg = k->private_value, value = 0;
	int ret = j713_audio_level(codec->group, codec->index, reg, &value, false);
	if (!ret) v->value.integer.value[0] = reg == 3 ? value / 2 : J713_DVC_MAX - value;
	return ret;
}
static int j713_level_put(struct snd_kcontrol *k, struct snd_ctl_elem_value *v)
{
	struct tas2764_j713 *codec = snd_soc_component_get_drvdata(snd_kcontrol_chip(k));
	unsigned int reg = k->private_value, value;
	long requested = v->value.integer.value[0];
	if (requested < 0 || requested > (reg == 3 ? J713_GAIN_MAX / 2 : J713_DVC_MAX - J713_DVC_MIN))
		return -ERANGE;
	value = reg == 3 ? requested * 2 : J713_DVC_MAX - requested;
	return j713_audio_level(codec->group, codec->index, reg, &value, true);
}
static int j713_level_info(struct snd_kcontrol *k, struct snd_ctl_elem_info *info)
{
	info->type = SNDRV_CTL_ELEM_TYPE_INTEGER;
	info->count = 1;
	info->value.integer.min = 0;
	info->value.integer.max = k->private_value == 3 ? J713_GAIN_MAX / 2 : J713_DVC_MAX - J713_DVC_MIN;
	return 0;
}
static int j713_sensor_get(struct snd_kcontrol *k, struct snd_ctl_elem_value *v)
{
	struct tas2764_j713 *codec = snd_soc_component_get_drvdata(snd_kcontrol_chip(k));
	unsigned int enabled;
	int ret = j713_audio_sensor(codec->group, codec->index, k->private_value, &enabled);
	if (!ret) v->value.integer.value[0] = enabled;
	return ret;
}
static const struct snd_kcontrol_new j713_codec_controls[] = {
	{ .iface = SNDRV_CTL_ELEM_IFACE_MIXER, .name = "VSENSE Switch",
	  .access = SNDRV_CTL_ELEM_ACCESS_READ | SNDRV_CTL_ELEM_ACCESS_VOLATILE,
	  .info = snd_ctl_boolean_mono_info, .get = j713_sensor_get, .private_value = 0 },
	{ .iface = SNDRV_CTL_ELEM_IFACE_MIXER, .name = "ISENSE Switch",
	  .access = SNDRV_CTL_ELEM_ACCESS_READ | SNDRV_CTL_ELEM_ACCESS_VOLATILE,
	  .info = snd_ctl_boolean_mono_info, .get = j713_sensor_get, .private_value = 1 },
	{ .iface = SNDRV_CTL_ELEM_IFACE_MIXER, .name = "Speaker Volume",
	  .info = j713_level_info, .get = j713_level_get, .put = j713_level_put,
	  .private_value = 0x1a, .access = SNDRV_CTL_ELEM_ACCESS_READWRITE |
	  SNDRV_CTL_ELEM_ACCESS_TLV_READ, .tlv.p = tas2764_playback_volume },
	{ .iface = SNDRV_CTL_ELEM_IFACE_MIXER, .name = "Amp Gain Volume",
	  .info = j713_level_info, .get = j713_level_get, .put = j713_level_put,
	  .private_value = 3, .access = SNDRV_CTL_ELEM_ACCESS_READWRITE |
	  SNDRV_CTL_ELEM_ACCESS_TLV_READ, .tlv.p = tas2764_digital_tlv },
};

static int j713_codec_hw_params(struct snd_pcm_substream *substream,
		struct snd_pcm_hw_params *params, struct snd_soc_dai *dai)
{
	return params_rate(params) == 48000 &&
		params_channels(params) == (substream->stream ? 8 : 4) &&
		params_format(params) == (substream->stream ? SNDRV_PCM_FORMAT_S16_LE : SNDRV_PCM_FORMAT_S32_LE) ? 0 : -EINVAL;
}
static int j713_codec_fmt(struct snd_soc_dai *dai, unsigned int fmt)
{
	return fmt == (SND_SOC_DAIFMT_I2S | SND_SOC_DAIFMT_CBC_CFC |
		       SND_SOC_DAIFMT_GATED | SND_SOC_DAIFMT_NB_IF) ? 0 : -EINVAL;
}
static int j713_codec_mute(struct snd_soc_dai *dai, int mute, int direction)
{
	struct tas2764_j713 *codec = snd_soc_component_get_drvdata(dai->component);
	/* Unmute clears only this codec's veto; activation still requires a lease. */
	return j713_audio_codec_mute(codec->group, codec->index, mute);
}
static const struct snd_soc_dai_ops j713_codec_dai_ops = {
	.hw_params = j713_codec_hw_params, .set_fmt = j713_codec_fmt,
	.mute_stream = j713_codec_mute, .no_capture_mute = 1,
};
static struct snd_soc_dai_driver j713_codec_dai = {
	.name = "tas2764 ASI1",
	.playback = { .stream_name = "ASI1 Playback", .channels_min = 4, .channels_max = 4,
		.rates = SNDRV_PCM_RATE_48000, .formats = SNDRV_PCM_FMTBIT_S32_LE },
	.capture = { .stream_name = "ASI1 Capture", .channels_min = 8, .channels_max = 8,
		.rates = SNDRV_PCM_RATE_48000, .formats = SNDRV_PCM_FMTBIT_S16_LE },
	.ops = &j713_codec_dai_ops,
};
static const struct snd_soc_dapm_widget j713_codec_widgets[] = {
	SND_SOC_DAPM_AIF_IN("ASI1", "ASI1 Playback", 0, SND_SOC_NOPM, 0, 0),
	SND_SOC_DAPM_OUTPUT("OUT"),
	SND_SOC_DAPM_SIGGEN("VMON"), SND_SOC_DAPM_SIGGEN("IMON"),
	SND_SOC_DAPM_AIF_OUT("Feedback", "ASI1 Capture", 0, SND_SOC_NOPM, 0, 0),
};
static const struct snd_soc_dapm_route j713_codec_routes[] = {
	{ "OUT", NULL, "ASI1" }, { "Feedback", NULL, "VMON" }, { "Feedback", NULL, "IMON" },
};
static int j713_codec_suspend(struct snd_soc_component *component)
{
	struct tas2764_j713 *codec = snd_soc_component_get_drvdata(component);
	return j713_audio_revoke(codec->group);
}
static void j713_codec_remove(struct snd_soc_component *component)
{
	j713_codec_suspend(component);
}
static const struct snd_soc_component_driver j713_codec_component = {
	/* No regmap, register controls, cache sync, reset, or automatic ACTIVE path. */
	.remove = j713_codec_remove, .suspend = j713_codec_suspend,
	.controls = j713_codec_controls, .num_controls = ARRAY_SIZE(j713_codec_controls),
	.dapm_widgets = j713_codec_widgets, .num_dapm_widgets = ARRAY_SIZE(j713_codec_widgets),
	.dapm_routes = j713_codec_routes, .num_dapm_routes = ARRAY_SIZE(j713_codec_routes),
	.endianness = 1,
};
static int tas2764_j713_probe(struct i2c_client *client)
{
	struct tas2764_j713 *codec;
	int ret;
	codec = devm_kzalloc(&client->dev, sizeof(*codec), GFP_KERNEL);
	if (!codec) return -ENOMEM;
	codec->group = devm_j713_audio_get(&client->dev);
	if (IS_ERR(codec->group)) return PTR_ERR(codec->group);
	ret = devm_j713_codec_join(codec->group, client, &codec->index);
	if (ret) return ret;
	i2c_set_clientdata(client, codec);
	return devm_snd_soc_register_component(&client->dev, &j713_codec_component,
					      &j713_codec_dai, 1);
}
#endif
