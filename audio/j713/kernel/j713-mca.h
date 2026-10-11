/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef J713_MCA_H
#define J713_MCA_H
extern int j713_admac_tx_idle(struct dma_chan *, u32 *);
extern int j713_admac_rx_idle(struct dma_chan *, u32 *);
extern int j713_admac_rx_ready(struct dma_chan *);
static int j713_frame_unlocked(struct j713_transport *t)
{
 struct mca_cluster *cl = &t->mca->clusters[0];
 if (!t->frame_ready || READ_ONCE(t->failed) ||
     clk_get_rate(cl->clk_parent) != 24000000 || cl->configured_bclk != 12000000 ||
     readl(mca_reg(cl, REG_MCLK_CONF)) != 0x200 ||
     readl(mca_reg(cl, REG_SYNCGEN_HI_PERIOD)) != 0 ||
     readl(mca_reg(cl, REG_SYNCGEN_LO_PERIOD)) != 248 ||
     readl(cl->base + 0x6004) != 0x17503 || readl(cl->base + 0x6008) != 1 ||
     readl(cl->base + 0x600c) != ~0U || readl(cl->base + 0x6010) != 0xfffffff0 ||
     readl(cl->base + 0x6014) != ~0U || readl(cl->base + 0x6018) != 0xfffffff0 ||
     readl(cl->base + 0x4004) != 0x1b047 || readl(cl->base + 0x4008) != 1 ||
     readl(cl->base + 0x400c) != ~0U || readl(cl->base + 0x4010) != 0xffffff00 ||
     (readl(cl->base + 0x4014) & 0x3f) != 3 || readl(cl->base + 0x4018) ||
     readl(t->mca->switch_base) != 0x20101000) return -EIO;
 if (!t->port_owned || readl(cl->base + 0x2160) != (t->port_saved[0] | 14) ||
     readl(cl->base + 0x2164) != (t->port_saved[1] | 0x100) ||
     readl(cl->base + 0x2168) != (t->port_saved[2] | 1)) return -EIO;
 return 0;
}
/* Supplier renewal never takes the transport mutex. Serialize only frame reads
 * against the finite serializer reset sequence performed during TX cleanup. */
static int j713_frame(struct j713_transport *t)
{
 int ret;
 mutex_lock(&t->frame_lock);
 ret = j713_frame_unlocked(t);
 mutex_unlock(&t->frame_lock);
 return ret;
}
static int j713_clocked(struct j713_transport *t)
{
 struct mca_cluster *cl = &t->mca->clusters[0];
 if (!cl->pd_link || !(readl(mca_reg(cl, REG_STATUS)) & STATUS_MCLK_EN) ||
     !(readl(mca_reg(cl, REG_SYNCGEN_STATUS)) & SYNCGEN_STATUS_EN) ||
     readl(mca_reg(cl, REG_SYNCGEN_MCLK_SEL)) != 0x201) return -EIO;
 return j713_frame(t);
}
static int j713_transport_quiet(void *ctx)
{
 struct j713_transport *t = ctx;
 struct mca_cluster *cl = &t->mca->clusters[0];
 u32 last;
 int i, ret;
 if (READ_ONCE(t->dma_running) || READ_ONCE(cl->t8132_active)) return -EBUSY;
 for (i = 0; i < 2; i++) {
  if (!cl->dma_chans[i]) continue;
  ret = i ? j713_admac_rx_idle(cl->dma_chans[i], &last) :
            j713_admac_tx_idle(cl->dma_chans[i], &last);
  if (ret) return ret;
 }
 return 0;
}
static int j713_gates_off(struct j713_transport *t)
{
 struct mca_cluster *cl=&t->mca->clusters[0];
 return (readl(mca_reg(cl,REG_STATUS)) & STATUS_MCLK_EN) ||
        (readl(mca_reg(cl,REG_SYNCGEN_STATUS)) & SYNCGEN_STATUS_EN) ||
        (readl(cl->base+0x6000)&0xa) || (readl(cl->base+0x4000)&0xa) ? -EBUSY : 0;
}
static int j713_transport_sensing(void *ctx)
{
 struct j713_transport *t = ctx;
 if (!(READ_ONCE(t->dma_running) & 2) ||
     !(READ_ONCE(t->mca->clusters[0].t8132_active) & 2)) return -EAGAIN;
 return j713_clocked(t);
}
static int j713_transport_running(void *ctx)
{
 struct j713_transport *t = ctx;
 int ret = j713_transport_sensing(t);
 if (ret) return ret;
 return READ_ONCE(t->dma_running) == 3 && !READ_ONCE(t->tx_stopping) &&
        READ_ONCE(t->mca->clusters[0].t8132_active) == 3 ? 0 : -EAGAIN;
}
static void j713_transport_cancelled(void *ctx)
{
 struct j713_transport *t = ctx;
 if (!READ_ONCE(t->closing)) mod_delayed_work(system_wq, &t->cancelled_work, 0);
}
static void j713_cancelled_work(struct work_struct *work)
{
 struct j713_transport *t = container_of(to_delayed_work(work), struct j713_transport, cancelled_work);
 struct snd_pcm *pcm = READ_ONCE(t->pcm);
 bool retry = false;
 int i;
 if (!pcm || READ_ONCE(t->closing) || !j713_audio_cancelled(t->audio)) return;
 /* Runtime destruction uses open_mutex. Never wait against a close joining us. */
 if (!mutex_trylock(&pcm->open_mutex)) { retry = true; goto out; }
 for (i = 0; i < 2; i++) {
  struct snd_pcm_substream *s = pcm->streams[i].substream;
  if (!s || !s->runtime) continue;
  if (!mutex_trylock(&s->self_group.mutex)) { retry = true; continue; }
  if (j713_audio_cancelled(t->audio) && snd_pcm_running(s))
   snd_pcm_stop(s, SNDRV_PCM_STATE_XRUN);
  mutex_unlock(&s->self_group.mutex);
 }
 mutex_unlock(&pcm->open_mutex);
out:
 if (retry && !READ_ONCE(t->closing))
  mod_delayed_work(system_wq, &t->cancelled_work, msecs_to_jiffies(1));
}
static const struct j713_transport_ops j713_transport_ops = {
 .quiescent = j713_transport_quiet, .running = j713_transport_running,
 .sensing = j713_transport_sensing, .cancelled = j713_transport_cancelled,
};
static int j713_stage(struct j713_transport *t, unsigned int direction, bool enable)
{
 struct mca_cluster *cl = &t->mca->clusters[0];
 struct m4_t8132_stage_plan plan;
 unsigned int i;
 int ret = m4_t8132_build_stage(cl->t8132_active, direction, enable, &plan);
 if (ret) return ret;
 for (i = 0; i < plan.count; i++) writel(plan.writes[i].value, cl->base + plan.writes[i].offset);
 WRITE_ONCE(cl->t8132_active, plan.next_active);
 return 0;
}
static int j713_fmt(struct j713_transport *t, unsigned int fmt)
{
 if (fmt != (SND_SOC_DAIFMT_I2S | SND_SOC_DAIFMT_NB_IF | SND_SOC_DAIFMT_BP_FP)) return -EINVAL;
 t->mca->clusters[0].clk_provider = true;
 return 0;
}
static int j713_fe_start(struct j713_transport *t, struct snd_pcm_substream *s)
{
 if (!s->pcm->nonatomic || snd_pcm_stream_linked(s) || READ_ONCE(t->closing)) return -EINVAL;
 return snd_pcm_hw_constraint_single(s->runtime, SNDRV_PCM_HW_PARAM_CHANNELS, s->stream ? 8 : 4);
}
static int j713_fixed_format(struct j713_transport *t)
{
 struct mca_cluster *cl = &t->mca->clusters[0];
 const struct m4_t8132_rx_format f = { .channels=0xff, .width=16, .delay=1,
  .i2s_sources=3, .sync_type=2, .falling_edge=true };
 struct m4_t8132_rx_plan plan;
 struct m4_t8132_write fifo;
 u32 old;
 unsigned int i;
 int ret;
 if (cl->t8132_active || cl->pd_link || t->dma_running ||
     clk_get_rate(cl->clk_parent) != 24000000 || cl->bclk_ratio != 250) return -EBUSY;
 ret=j713_gates_off(t);if(ret)return ret;
 old = readl(t->mca->switch_base);
 ret = m4_t8132_build_tx_fifo(old, 1, 32, 16, false, false, &fifo);
 if (ret) return ret;
 mca_modify(cl, CLUSTER_TX_OFF + REG_TX_SERDES_CONF, SERDES_CONF_BCLK_POL, SERDES_CONF_BCLK_POL);
 writel(1, cl->base + 0x6008);
 ret = mca_configure_serdes(cl, CLUSTER_TX_OFF, 0xf, 4, 4, 32, true, 1);
 if (ret) return ret;
 writel((old & ~fifo.mask) | fifo.value, t->mca->switch_base);
 ret = mca_fe_set_clocks_divider(cl, 250, 48000);
 if (ret) return ret;
 ret = m4_t8132_build_rx(&f, &plan);
 if (ret) return ret;
 for (i = 0; i < ARRAY_SIZE(plan.writes); i++) {
  const struct m4_t8132_write *w = &plan.writes[i];
  void __iomem *reg = cl->base + 0x4000 + w->offset;
  writel((readl(reg) & ~w->mask) | w->value, reg);
 }
 old = readl(t->mca->switch_base);
 ret = m4_t8132_build_rx_fifo(old, 1, 16, false, false, &fifo);
 if (ret) return ret;
 writel((old & ~fifo.mask) | fifo.value, t->mca->switch_base);
 writel(0x11, t->mca->switch_base + 4);
 t->frame_ready = true;
 return j713_frame(t);
}
static int j713_fe_params(struct j713_transport *t, struct snd_pcm_substream *s,
                           struct snd_pcm_hw_params *p)
{
 int ret;
 if (params_rate(p) != 48000 || params_channels(p) != (s->stream ? 8 : 4) ||
     params_format(p) != (s->stream ? SNDRV_PCM_FORMAT_S16_LE : SNDRV_PCM_FORMAT_S32_LE)) return -EINVAL;
 mutex_lock(&t->lock);
 ret = t->failed ? -EIO : t->frame_ready ? j713_frame(t) : j713_fixed_format(t);
 mutex_unlock(&t->lock);
 return ret;
}
static int j713_port_open(struct j713_transport *t, struct snd_pcm_substream *s)
{
 struct mca_cluster *cl = &t->mca->clusters[0];
 u32 enable, select, data;
 int ret = 0;
 mutex_lock(&t->lock);
 if (t->opened & BIT(s->stream)) { ret = -EBUSY; goto out; }
 if (!t->opened) {
  ret = j713_audio_low_ready(t->audio);
  if (ret) goto out;
  enable = readl(cl->base + 0x2160); select = readl(cl->base + 0x2164); data = readl(cl->base + 0x2168);
  if ((enable & 15) || ((select & 0xf00) != 0 && (select & 0xf00) != 0x100) ||
      ((data & 0x1ffff) != 0 && (data & 0x1ffff) != 1)) { ret = -EBUSY; goto out; }
  t->port_saved[0]=enable; t->port_saved[1]=select; t->port_saved[2]=data;
  t->port_owned = true;
  writel(select | 0x100, cl->base + 0x2164);
  writel(enable | 2, cl->base + 0x2160); writel(enable | 6, cl->base + 0x2160);
  writel(data | 1, cl->base + 0x2168); writel(enable | 14, cl->base + 0x2160);
  writel(0x100, mca_reg(cl, REG_PORT_CLOCK_SEL));
  writel(1, mca_reg(cl, REG_PORT_DATA_SEL));
  mca_modify(cl, REG_PORT_ENABLES, 14, 14);
  cl->port_clk_driver=0;
 }
 t->opened |= BIT(s->stream);
 cl->port_clk_started[s->stream]=true;
out:
 mutex_unlock(&t->lock);
 return ret;
}
static int j713_prepare(struct j713_transport *t, struct snd_pcm_substream *s)
{
 struct mca_cluster *cl=&t->mca->clusters[0];
 int ret;
 mutex_lock(&t->lock);
 ret = t->failed || !(t->params & BIT(s->stream)) ? -EIO : j713_frame(t);
 if (ret) goto out;
 if (t->cleanup[s->stream].phase == M4_DONE) {
  u32 bit=s->stream?0x10:1, old=readl(t->mca->switch_base+4);
  if ((old & ~0x11U) || (old & bit)) {ret=-EIO;goto out;}
  writel(old|bit,t->mca->switch_base+4);
  if(readl(t->mca->switch_base+4)!=(old|bit)){ret=-EIO;goto out;}
  memset(&t->cleanup[s->stream],0,sizeof(t->cleanup[s->stream]));
 }
 if (!t->configured) {
  /* A discovered playback node may precede the protection daemon. */
  if (!j713_audio_owned(t->audio) && !s->stream) { t->prepared |= 1; goto out; }
  ret = j713_gates_off(t);
  if (!ret) ret = j713_transport_quiet(t);
  if (!ret) ret = j713_audio_prepare(t->audio, NULL);
  if (ret) goto out;
  ret = mca_fe_enable_clocks(cl);
  if (ret) { j713_audio_revoke(t->audio); goto out; }
  if (!j713_audio_owned(t->audio)) {
   j713_audio_revoke(t->audio); mca_fe_disable_clocks(cl); ret=-ECANCELED; goto out;
  }
  t->configured = true;
 }
 cl->clocks_in_use[s->stream]=true;
 if (t->prepared & 1) cl->clocks_in_use[0]=true;
 t->prepared |= BIT(s->stream);
out:
 mutex_unlock(&t->lock);
 return ret;
}
static int j713_pcm_open(struct j713_transport *t, struct snd_pcm_substream *s)
{
 struct mca_cluster *cl=&t->mca->clusters[0];
 struct snd_soc_pcm_runtime *rtd=snd_soc_substream_to_rtd(s);
 int ret;
 if (rtd->dai_link->no_pcm) return 0;
 ret=mca_set_runtime_hwparams(snd_soc_rtd_to_cpu(rtd,0)->component,s,cl->dma_chans[s->stream]);
 if (ret) return ret;
 mutex_lock(&t->lock);
 ret=t->stream[s->stream] ? -EBUSY : snd_dmaengine_pcm_open(s,cl->dma_chans[s->stream]);
 if (!ret) t->stream[s->stream]=s;
 mutex_unlock(&t->lock);
 return ret;
}
static int j713_dma_params(struct j713_transport *t, struct snd_pcm_substream *s,
                            struct snd_pcm_hw_params *p)
{
 struct dma_slave_config config={};
 int ret;
 if (snd_soc_substream_to_rtd(s)->dai_link->no_pcm) return 0;
 ret=snd_hwparams_to_dma_slave_config(s,p,&config);
 if (ret) return ret;
 if (s->stream) config.src_port_window_size=1;
 else config.dst_port_window_size=4;
 mutex_lock(&t->lock);
 ret=t->stream[s->stream] != s || t->failed ? -EPERM : dmaengine_slave_config(snd_dmaengine_pcm_get_chan(s),&config);
 if (!ret) t->params |= BIT(s->stream);
 mutex_unlock(&t->lock);
 return ret;
}
/* DMA is terminated even if mode/LOW verification fails. */
static int j713_stop_locked(struct j713_transport *t, struct snd_pcm_substream *s)
{
 unsigned int d=s->stream;
 int ret;
 if (!d) { WRITE_ONCE(t->tx_stopping,true); ret=j713_audio_playback_mute(t->audio); }
 else ret=j713_audio_revoke(t->audio);
 t->requested &= ~BIT(d);
 j713_stage(t,d,false);
 if (t->dma_running & BIT(d)) {
  int e=snd_dmaengine_pcm_trigger(s,SNDRV_PCM_TRIGGER_STOP);
  if (!ret) ret=e;
  t->dma_running &= ~BIT(d);
  m4_note_stop(&t->cleanup[d]);
 }
 return ret;
}
static int j713_pcm_trigger(struct j713_transport *t, struct snd_pcm_substream *s, int cmd)
{
 int ret=0;
 unsigned int d=s->stream, i;
 if (snd_soc_substream_to_rtd(s)->dai_link->no_pcm) return 0;
 mutex_lock(&t->lock);
 if (cmd==SNDRV_PCM_TRIGGER_STOP || cmd==SNDRV_PCM_TRIGGER_SUSPEND) {
  ret=j713_stop_locked(t,s); goto out;
 }
 if (cmd!=SNDRV_PCM_TRIGGER_START) {ret=-EINVAL;goto out;}
 if (t->failed || t->stream[d]!=s || !(t->prepared & BIT(d))) {ret=-EPERM;goto out;}
 t->requested |= BIT(d);
 if (!t->configured && !d) goto out;
 if (!j713_audio_owned(t->audio)) {ret=-ECANCELED;goto fail;}
 ret=j713_clocked(t);
 if (ret) goto fail;
 for (i=0;i<2;i++) {
  if (!(t->requested & BIT(i)) || (t->dma_running & BIT(i))) continue;
  if (!t->stream[i] || !(t->params & BIT(i))) {ret=-EPERM;goto fail;}
  if (i) {ret=j713_admac_rx_ready(t->mca->clusters[0].dma_chans[i]);if(ret)goto fail;}
  ret=snd_dmaengine_pcm_trigger(t->stream[i],SNDRV_PCM_TRIGGER_START);
  if (ret) goto fail;
  t->dma_running |= BIT(i);
  m4_note_start(&t->cleanup[i]);
 }
 if (!d) WRITE_ONCE(t->tx_stopping,false);
 goto out;
fail:
 j713_audio_revoke(t->audio);
 t->failed=true;
out:
 mutex_unlock(&t->lock);
 return ret;
}
static int j713_dai_trigger(struct j713_transport *t, struct snd_pcm_substream *s, int cmd)
{
 int ret=0;
 unsigned int i;
 mutex_lock(&t->lock);
 if (cmd==SNDRV_PCM_TRIGGER_STOP || cmd==SNDRV_PCM_TRIGGER_SUSPEND) goto out;
 if (cmd!=SNDRV_PCM_TRIGGER_START) {ret=-EINVAL;goto out;}
 if (!t->configured && !s->stream) goto out;
 for(i=0;i<2&&!ret;i++) if(t->dma_running & BIT(i)) ret=j713_stage(t,i,true);
 if (!ret && (t->dma_running & 2)) ret=j713_audio_sensing(t->audio);
 if (ret) {j713_audio_revoke(t->audio);t->failed=true;}
out:
 mutex_unlock(&t->lock);
 return ret;
}
struct j713_cleanup_context {struct j713_transport *t;struct snd_pcm_substream *s;bool frame_locked;};
static unsigned int j713_cleanup_read(void *ctx, enum m4_reg reg)
{
 struct j713_cleanup_context *c=ctx;
 struct mca_cluster *cl=&c->t->mca->clusters[0];
 void __iomem *base=cl->base+(c->s->stream?0x4000:0x6000);
 switch(reg){case M4_CONTROL:return readl(base);case M4_CONFIG:return readl(base+4);
 case M4_FIFO_ENABLE:return readl(c->t->mca->switch_base+4);
 case M4_FIFO_LEVEL:return readl(c->t->mca->switch_base+(c->s->stream?0xc:8));}
 return 0;
}
static void j713_cleanup_write(void *ctx, enum m4_reg reg, unsigned int value)
{
 struct j713_cleanup_context *c=ctx;
 struct mca_cluster *cl=&c->t->mca->clusters[0];
 void __iomem *base=cl->base+(c->s->stream?0x4000:0x6000);
 if(reg==M4_CONTROL)writel(value,base);
 else if(reg==M4_CONFIG)writel(value,base+4);
 else if(reg==M4_FIFO_ENABLE)writel(value,c->t->mca->switch_base+4);
}
static void j713_cleanup_delay(void *ctx,unsigned int us){udelay(us);}
static int j713_cleanup_idle(void *ctx)
{
 struct j713_cleanup_context *c=ctx;u32 last;
 struct dma_chan *chan=snd_dmaengine_pcm_get_chan(c->s);
 return c->s->stream?j713_admac_rx_idle(chan,&last):j713_admac_tx_idle(chan,&last);
}
static void j713_cleanup_sync(void *ctx)
{
 struct j713_cleanup_context *c=ctx;snd_dmaengine_pcm_sync_stop(c->s);
}
static int j713_cleanup_powered(void *ctx)
{
 struct j713_cleanup_context *c=ctx;
 int ret;
 if(j713_clocked(c->t))return -EIO;
 ret=c->s->stream?j713_audio_revoke(c->t->audio):j713_audio_playback_mute(c->t->audio);
 if(ret)return ret;
 /* Supplier callbacks finish before frame_lock: renewal takes operation_lock
  * then frame_lock, so the reset span must not call back into the supplier. */
 mutex_lock(&c->t->frame_lock);
 c->frame_locked=true;
 return 0;
}
static int j713_sync_stopped_locked(struct j713_transport *t,struct snd_pcm_substream *s)
{
 struct j713_cleanup_context c={.t=t,.s=s};
 const struct m4_io io={.ctx=&c,.read=j713_cleanup_read,.write=j713_cleanup_write,
  .delay_us=j713_cleanup_delay,.idle=j713_cleanup_idle,.synchronize=j713_cleanup_sync,.powered=j713_cleanup_powered};
 int ret;
 snd_dmaengine_pcm_sync_stop(s);
 if(t->cleanup[s->stream].phase!=M4_STOPPED)return 0;
 ret=m4_cleanup_direction_once(&t->cleanup[s->stream],&io,s->stream?0x10:1);
 if(c.frame_locked)mutex_unlock(&t->frame_lock);
 if(ret){t->failed=true;j713_audio_revoke(t->audio);}
 return ret;
}
static int j713_sync_stop(struct snd_soc_component *component,struct snd_pcm_substream *s)
{
 struct mca_data *mca=snd_soc_component_get_drvdata(component);
 int ret;
 if(snd_soc_substream_to_rtd(s)->dai_link->no_pcm)return 0;
 if(!mca->j713){snd_dmaengine_pcm_sync_stop(s);return 0;}
 mutex_lock(&mca->j713->lock);
 ret=j713_sync_stopped_locked(mca->j713,s);
 mutex_unlock(&mca->j713->lock);
 return ret;
}
static int j713_fe_free(struct j713_transport *t,struct snd_pcm_substream *s)
{
 int ret;
 mutex_lock(&t->lock);
 ret=j713_stop_locked(t,s);
 if(!ret)ret=j713_sync_stopped_locked(t,s);
 else snd_dmaengine_pcm_sync_stop(s);
 t->params&=~BIT(s->stream);t->prepared&=~BIT(s->stream);
 mutex_unlock(&t->lock);
 return ret;
}
static void j713_port_close(struct j713_transport *t,struct snd_pcm_substream *s)
{
 struct mca_cluster *cl=&t->mca->clusters[0];
 unsigned int i;
 int ret=0;
 mutex_lock(&t->lock);
 t->opened&=~BIT(s->stream);cl->port_clk_started[s->stream]=false;
 cl->clocks_in_use[s->stream]=false;
 if(t->opened)goto out;
 ret=j713_audio_revoke(t->audio);
 for(i=0;i<2;i++)if(t->stream[i]) {
  int e=j713_stop_locked(t,t->stream[i]);
  if(!ret)ret=e;
  e=j713_sync_stopped_locked(t,t->stream[i]);
  if(!ret)ret=e;
 }
 if(t->port_owned){
  u32 enable=readl(cl->base+0x2160);
  writel(enable&~8U,cl->base+0x2160);
  writel(t->port_saved[2],cl->base+0x2168);
  writel(enable&~12U,cl->base+0x2160);
  writel(t->port_saved[0],cl->base+0x2160);
  writel(t->port_saved[1],cl->base+0x2164);
  if(readl(cl->base+0x2160)!=t->port_saved[0] || readl(cl->base+0x2164)!=t->port_saved[1] ||
     readl(cl->base+0x2168)!=t->port_saved[2])ret=-EIO;
  t->port_owned=false;
 }
 mca_modify(cl,REG_PORT_ENABLES,14,0);
 writel(0,mca_reg(cl,REG_PORT_DATA_SEL));writel(0,mca_reg(cl,REG_PORT_CLOCK_SEL));
 if(t->configured){mca_fe_disable_clocks(cl);t->configured=false;}
 cl->port_clk_driver=-1;
 if(!ret)ret=j713_audio_quiesced(t->audio);
 if(ret)t->failed=true;
 if(!t->failed){t->frame_ready=false;memset(t->cleanup,0,sizeof(t->cleanup));}
out:
 mutex_unlock(&t->lock);
}
static int j713_pcm_close(struct j713_transport *t,struct snd_pcm_substream *s)
{
 int ret;
 if(snd_soc_substream_to_rtd(s)->dai_link->no_pcm)return 0;
 mutex_lock(&t->lock);
 /* All asynchronous DMA work is joined before the runtime is released. */
 j713_stop_locked(t,s);
 snd_dmaengine_pcm_sync_stop(s);
 ret=snd_dmaengine_pcm_close(s);
 t->stream[s->stream]=NULL;t->params&=~BIT(s->stream);t->prepared&=~BIT(s->stream);
 mutex_unlock(&t->lock);
 return ret;
}
static void j713_pcm_release(struct j713_transport *t)
{
 WRITE_ONCE(t->closing,true);
 j713_audio_revoke(t->audio);
 cancel_delayed_work_sync(&t->cancelled_work);
 WRITE_ONCE(t->pcm,NULL);
}
static void j713_transport_release(void *data)
{
 struct j713_transport *t=data;
 j713_pcm_release(t);
}
static int j713_transport_init(struct mca_data *mca)
{
 struct j713_transport *t;
 struct device_node *expected;
 bool match;
 int ret;
 expected=of_find_node_by_path("/soc/i2s@393600000");
 match=expected && mca->dev->of_node==expected;
 of_node_put(expected);
 if(!of_machine_is_compatible("apple,j713") || mca->nclusters!=1 || !match)return -ENODEV;
 t=devm_kzalloc(mca->dev,sizeof(*t),GFP_KERNEL);if(!t)return -ENOMEM;
 t->mca=mca;mutex_init(&t->lock);mutex_init(&t->frame_lock);INIT_DELAYED_WORK(&t->cancelled_work,j713_cancelled_work);
 t->audio=devm_j713_audio_get(mca->dev);if(IS_ERR(t->audio))return PTR_ERR(t->audio);
 mca->j713=t;
 ret=devm_j713_transport_join(t->audio,mca->dev,&j713_transport_ops,t);
 if(ret)return ret;
 return devm_add_action_or_reset(mca->dev,j713_transport_release,t);
}
#endif
