/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef J713_AUDIO_H
#define J713_AUDIO_H
#include <linux/types.h>
struct device;
struct i2c_client;
struct j713_audio;

#define J713_GAIN_MAX 0x28
#define J713_DVC_MIN 0x00
#define J713_DVC_MAX 0xc9

struct j713_transport_ops {
	/* Called with all external PCM operations joined, before a new epoch. */
	int (*quiescent)(void *context);
	/* Both directions, frame geometry and shared routes verified by MCA. */
	int (*running)(void *context);
	/* RX and fixed clocks ready; TX may be absent while sensing in MUTE. */
	int (*sensing)(void *context);
	/* Queue PCM cancellation; must not enter ALSA or wait here. */
	void (*cancelled)(void *context);
};

/* Managed supplier link; call during consumer probe, before publication. */
struct j713_audio *devm_j713_audio_get(struct device *consumer);
int devm_j713_codec_join(struct j713_audio *audio, struct i2c_client *client,
			unsigned int *index);
int devm_j713_transport_join(struct j713_audio *audio, struct device *consumer,
			    const struct j713_transport_ops *ops, void *context);
int j713_audio_prepare(struct j713_audio *audio, const void *owner);
bool j713_audio_owned(struct j713_audio *audio);
bool j713_audio_cancelled(struct j713_audio *audio);
int j713_audio_low_ready(struct j713_audio *audio);
int j713_audio_set_owner(struct j713_audio *audio, const void *owner, bool claim);
int j713_audio_sensing(struct j713_audio *audio);
int j713_audio_playback_mute(struct j713_audio *audio);
int j713_audio_codec_mute(struct j713_audio *audio, unsigned int index, bool mute);
int j713_audio_renew(struct j713_audio *audio, const void *owner,
		     s64 generation, s64 sequence);
u64 j713_audio_generation(struct j713_audio *audio);
int j713_audio_revoke(struct j713_audio *audio);
int j713_audio_stop(struct j713_audio *audio, const void *owner);
int j713_audio_quiesced(struct j713_audio *audio);
int j713_audio_level(struct j713_audio *audio, unsigned int index,
		     unsigned int reg, unsigned int *value, bool write);
int j713_audio_sensor(struct j713_audio *audio, unsigned int index, bool is_current,
		      unsigned int *enabled);
#endif
