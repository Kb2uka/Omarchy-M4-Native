/* T8132 register plans. No function in this header performs MMIO. */
#ifndef M4_T8132_RX_H
#define M4_T8132_RX_H

struct m4_t8132_write {
	u32 offset;
	u32 mask;
	u32 value;
};

/* Source: TX FIFO configure 0x1049f08 and validation 0x1049f88. */
static inline int m4_t8132_build_tx_fifo(u32 old, u32 count, u32 width,
                                       u32 threshold, bool common_flag,
                                       bool rx_active,
                                       struct m4_t8132_write *out)
{
 u32 packing, capacity = (old >> 24) & 0x7f;

 if (!out || (width != 16 && width != 24 && width != 32))
  return -EINVAL;
 switch (count) {
 case 1: packing = 0; break;
 case 2: packing = 1; break;
 case 4: packing = 2; break;
 case 8: packing = 3; break;
 default: return -EINVAL;
 }
 if (threshold < count || threshold > capacity)
  return -EINVAL;
 if (rx_active && !!(old & 0x80000000U) != common_flag)
  return -EBUSY;
 *out = (struct m4_t8132_write){0, 0x807f007fU,
         (threshold << 16) | (packing << 5) | (32 - width) |
         ((u32)common_flag << 31)};
 return 0;
}

struct m4_t8132_rx_format {
	u64 channels;
	u32 width;
	u32 delay;
	u32 i2s_sources;
	u32 loopback_sources;
	u32 sync_type;
	u32 sync_index;
	bool falling_edge;
	bool lsb_first;
};

struct m4_t8132_rx_plan {
	struct m4_t8132_write writes[6];
};

/* Source: J713 SerDes configure 0x1047250 and validation 0x104762c. */
static inline int m4_t8132_build_rx(const struct m4_t8132_rx_format *f,
				  struct m4_t8132_rx_plan *out)
{
	struct m4_t8132_rx_plan p;
	u32 width, sync, last = 0, config;
	u64 bits;

	if (!f || !out || !f->channels || f->i2s_sources > 0x3f ||
	    f->loopback_sources > 0x1ffff)
		return -EINVAL;
	switch (f->width) {
	case 16: width = 0x40; break;
	case 24: width = 0xc0; break;
	case 32: width = 0x100; break;
	default: return -EINVAL;
	}
	switch (f->sync_type) {
	case 0: sync = 0; break;
	case 0x30: sync = 7; break;
	case 2:
		if (f->sync_index >= 6)
			return -EINVAL;
		sync = f->sync_index + 1;
		break;
	default: return -EINVAL;
	}
	for (bits = f->channels; bits >>= 1; )
		last++;
	config = 0xb000 | (sync << 16) | width | last;
	if (!f->falling_edge)
		config |= 0x400;
	if (f->lsb_first)
		config |= 0x800;
	p.writes[0] = (struct m4_t8132_write){0x14, 0x3f, f->i2s_sources};
	p.writes[1] = (struct m4_t8132_write){0x18, ~0U, f->loopback_sources};
	p.writes[2] = (struct m4_t8132_write){0x04, ~0U, config};
	p.writes[3] = (struct m4_t8132_write){0x08, ~0U, f->delay};
	p.writes[4] = (struct m4_t8132_write){0x0c, ~0U, ~(u32)(f->channels >> 32)};
	p.writes[5] = (struct m4_t8132_write){0x10, ~0U, ~(u32)f->channels};
	*out = p;
	return 0;
}

/* Source: shared FIFO configure 0x1049f64. Preserve all TX fields. */
static inline int m4_t8132_build_rx_fifo(u32 old, u32 count, u32 width,
				       bool common_flag, bool tx_active,
				       struct m4_t8132_write *out)
{
	u32 packing, pad;

	if (!out || (width != 16 && width != 24 && width != 32))
		return -EINVAL;
	switch (count) {
	case 1: packing = 0; break;
	case 2: packing = 1; break;
	case 4: packing = 2; break;
	case 8: packing = 3; break;
	default: return -EINVAL;
	}
	if (tx_active && !!(old & 0x80000000U) != common_flag)
		return -EBUSY;
	pad = 32 - width;
	*out = (struct m4_t8132_write){0, 0x80007f00U,
		(packing << 13) | (pad << 8) | ((u32)common_flag << 31)};
	return 0;
}

struct m4_t8132_stage_plan {
	struct m4_t8132_write writes[3];
	u32 count;
	u32 next_active;
};

/* Caller must serialize ownership; this only calculates the write list. */
static inline int m4_t8132_build_stage(u32 active, u32 direction, bool enable,
				     struct m4_t8132_stage_plan *out)
{
	struct m4_t8132_stage_plan p = {0};
	u32 bit, next;

	if (!out || active > 3 || direction > 1)
		return -EINVAL;
	bit = 1U << direction;
	next = enable ? active | bit : active & ~bit;
	p.next_active = next;
	if (next == active) {
		*out = p;
		return 0;
	}
	if (!active && enable)
		p.writes[p.count++] = (struct m4_t8132_write){0x140, ~0U, 1};
	else if (!next && !enable)
		p.writes[p.count++] = (struct m4_t8132_write){0x144, ~0U, 1};
	p.writes[p.count++] = (struct m4_t8132_write){
		direction ? (enable ? 0x150 : 0x154) : (enable ? 0x160 : 0x164),
		~0U, 1};
	p.writes[p.count++] = (struct m4_t8132_write){0x170, ~0U, 1};
	*out = p;
	return 0;
}
#endif
