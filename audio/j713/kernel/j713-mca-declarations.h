/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef J713_MCA_DECLARATIONS_H
#define J713_MCA_DECLARATIONS_H
struct j713_transport {
 struct mca_data *mca;
 struct j713_audio *audio;
 struct mutex lock, frame_lock;
 struct delayed_work cancelled_work;
 struct snd_pcm *pcm;
 struct snd_pcm_substream *stream[2];
 unsigned int opened, params, prepared, requested, dma_running;
 bool frame_ready, configured, tx_stopping, closing, failed;
 u32 port_saved[3];
 bool port_owned;
 struct m4_cleanup cleanup[2];
};
static int j713_fe_start(struct j713_transport *, struct snd_pcm_substream *);
static int j713_fe_params(struct j713_transport *, struct snd_pcm_substream *, struct snd_pcm_hw_params *);
static int j713_fe_free(struct j713_transport *, struct snd_pcm_substream *);
static int j713_dai_trigger(struct j713_transport *, struct snd_pcm_substream *, int);
static int j713_prepare(struct j713_transport *, struct snd_pcm_substream *);
static int j713_port_open(struct j713_transport *, struct snd_pcm_substream *);
static void j713_port_close(struct j713_transport *, struct snd_pcm_substream *);
static int j713_fmt(struct j713_transport *, unsigned int);
static int j713_pcm_open(struct j713_transport *, struct snd_pcm_substream *);
static int j713_pcm_close(struct j713_transport *, struct snd_pcm_substream *);
static int j713_dma_params(struct j713_transport *, struct snd_pcm_substream *, struct snd_pcm_hw_params *);
static int j713_pcm_trigger(struct j713_transport *, struct snd_pcm_substream *, int);
static void j713_pcm_release(struct j713_transport *);
#endif
