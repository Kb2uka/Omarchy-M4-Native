/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef M4_CLEANUP_CORE_H
#define M4_CLEANUP_CORE_H
/* Diagnostic-only core. The same functions execute in the module and fixtures. */
enum m4_phase { M4_ARMED, M4_STARTED, M4_STOPPED, M4_RUNNING, M4_DONE, M4_FAILED };
enum m4_reg { M4_CONTROL, M4_CONFIG, M4_FIFO_ENABLE, M4_FIFO_LEVEL };
struct m4_cleanup {
 int phase;
 bool wrote, needs_params;
 int error;
 unsigned int saved_config, saved_fifo, before_level, final_control, final_level;
};
struct m4_io {
 void *ctx;
 unsigned int (*read)(void *, enum m4_reg);
 void (*write)(void *, enum m4_reg, unsigned int);
 void (*delay_us)(void *, unsigned int);
 int (*idle)(void *);
 void (*synchronize)(void *);
 int (*powered)(void *);
};
/* Caller supplies READ_ONCE and cmpxchg, including equivalent host atomics. */
static int m4_gate(struct m4_cleanup *s, bool require_params)
{
 int phase = READ_ONCE(s->phase);
 if (phase == M4_FAILED || phase == M4_RUNNING)
  return -EIO;
 if (require_params && (phase == M4_STOPPED || READ_ONCE(s->needs_params)))
  return -EPIPE;
 return 0;
}
static inline int m4_params_gate(struct m4_cleanup *s, bool clocks_in_use)
{
 int ret = m4_gate(s, false);
 if (ret) return ret;
 return READ_ONCE(s->needs_params) && clocks_in_use ? -EBUSY : 0;
}
static inline int m4_params_finish(struct m4_cleanup *s, int result, unsigned int fifo_enable)
{
 if (result) return result;
 if (fifo_enable != 1) return -EIO;
 WRITE_ONCE(s->needs_params, false);
 return 0;
}
static void m4_note_start(struct m4_cleanup *s)
{
 (void)cmpxchg(&s->phase, M4_ARMED, M4_STARTED);
}
static void m4_note_stop(struct m4_cleanup *s)
{
 (void)cmpxchg(&s->phase, M4_STARTED, M4_STOPPED);
}
static int m4_cleanup_direction_once(struct m4_cleanup *s, const struct m4_io *io,
                                     unsigned int fifo_bit)
{
 unsigned int value, i;
 int ret;
 if (fifo_bit != 1 && fifo_bit != 0x10) return -EINVAL;
 if (cmpxchg(&s->phase, M4_STOPPED, M4_RUNNING) != M4_STOPPED)
  return m4_gate(s, false);
 ret = io->idle(io->ctx);
 if (ret) goto fail;
 io->synchronize(io->ctx);
 ret = io->idle(io->ctx);
 if (ret) goto fail;
 ret = io->powered(io->ctx);
 if (ret) goto fail;
 value = io->read(io->ctx, M4_CONTROL);
 if (value & 0xa) { ret = -EBUSY; goto fail; }
 s->saved_config = io->read(io->ctx, M4_CONFIG);
 s->saved_fifo = io->read(io->ctx, M4_FIFO_ENABLE);
 s->before_level = io->read(io->ctx, M4_FIFO_LEVEL);
 s->wrote = true;
 io->write(io->ctx, M4_FIFO_ENABLE, s->saved_fifo & ~fifo_bit);
 if (io->read(io->ctx, M4_FIFO_ENABLE) != (s->saved_fifo & ~fifo_bit)) {
  ret = -EIO; goto restore;
 }
 value = io->read(io->ctx, M4_CONTROL);
 io->write(io->ctx, M4_CONTROL, value | 2u);
 value = io->read(io->ctx, M4_CONFIG);
 io->write(io->ctx, M4_CONFIG, value & ~0x70000u);
 value = io->read(io->ctx, M4_CONFIG);
 io->write(io->ctx, M4_CONFIG, (value & ~0x70000u) | 0x70000u);
 io->delay_us(io->ctx, 200);
 ret = -ETIMEDOUT;
 for (i = 0; i < 100; i++) {
  s->final_control = io->read(io->ctx, M4_CONTROL);
  if (!(s->final_control & 0xa)) { ret = 0; break; }
 }
restore:
 io->write(io->ctx, M4_CONFIG, s->saved_config);
 if (io->read(io->ctx, M4_CONFIG) != s->saved_config)
  ret = -EIO;
 if (ret) goto fail;
 ret = io->idle(io->ctx);
 if (ret) goto fail;
 s->final_control = io->read(io->ctx, M4_CONTROL);
 s->final_level = io->read(io->ctx, M4_FIFO_LEVEL);
 /* The stock stop path warns on nonzero getLevel, then continues. */
 if ((s->final_control & 0xa) ||
     io->read(io->ctx, M4_FIFO_ENABLE) != (s->saved_fifo & ~fifo_bit)) {
  ret = -EIO; goto fail;
 }
 WRITE_ONCE(s->needs_params, true);
 WRITE_ONCE(s->phase, M4_DONE);
 return 0;
fail:
 s->error = ret;
 WRITE_ONCE(s->phase, M4_FAILED);
 return ret;
}
static inline int m4_cleanup_once(struct m4_cleanup *s, const struct m4_io *io)
{
 return m4_cleanup_direction_once(s, io, 1);
}
static inline int m4_cleanup_rx_once(struct m4_cleanup *s, const struct m4_io *io)
{
 return m4_cleanup_direction_once(s, io, 0x10);
}
#endif
