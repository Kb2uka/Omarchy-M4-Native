/* SPDX-License-Identifier: GPL-2.0-only */
/* Pure T8132/T8140 layout helpers. No MMIO. Kernel and userspace. */
#ifndef __ISP_M4_LAYOUT_H__
#define __ISP_M4_LAYOUT_H__

#ifdef __KERNEL__
#include <linux/errno.h>
#include <linux/stddef.h>
#include <linux/string.h>
#include <linux/types.h>
#else
#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
typedef uint8_t u8;
typedef uint32_t u32;
typedef uint64_t u64;
#endif

#define ISP_DSID_MULTI_BC_MAX		4
#define ISP_DSID_MULTI_BC_HDR		0x10
#define ISP_DSID_MULTI_BC_OPCODE	0x3206u
#define ISP_BOOTARGS_H16_SIZE		0x290
#define ISP_H16_DESCRIPTOR_SIZE		0x200
#define ISP_H16_DESC_OFF		0x70
#define ISP_H16_DESC_NO_OPTICAL_CARD	0xb0
#define ISP_SHARED_APERTURE		0x10000000ULL
#define ISP_T8140_SHARED_SIZE		0xee000000ULL
#define ISP_FW_MMIO_PAGE		0x4000u

/* T8140 interrupt routing: keep these values. T8132 uses its own. */
#define ISP_MBOX_IRQ_ENABLE1_OFF		0x18
#define ISP_MBOX_IRQ_ENABLE2_OFF		0x38
#define ISP_MBOX_IRQ_ROUTE_T8140		0x73c
#define ISP_MBOX_IRQ_ROUTE_T8132		0x95c
#define ISP_MBOX_IRQ_ENABLE1_VAL		0x70000000u
#define ISP_MBOX_IRQ_ENABLE2_T8140_VAL		0x700000u
#define ISP_MBOX_IRQ_ENABLE2_T8132_VAL		0x02c00000u
#define ISP_MBOX_IRQ_ROUTE_VAL			0x8u
#define ISP_MBOX2_OFFSET_T8140			0x410u
#define ISP_MBOX2_OFFSET_T8132			0x464u
#define ISP_COPROC_CONTROL_T8132		0x1600044u
/*
 * Native Disable writes VAL then waits ACK_BIT SET (5001 * 10us) on the
 * same word. Written/acknowledged control, not a proven clear/W1C.
 * Offset and native write/ack values are stored unused this dispatch.
 */
#define ISP_COPROC_DISABLE_T8132		0x1600040u
#define ISP_COPROC_DISABLE_VAL_T8132		0x2000000u
#define ISP_COPROC_DISABLE_ACK_BIT_T8132	25

/*
 * opcode at +0 (LE u32 opcode lives at +4), count +8, range +c, bases +10.
 * Attribute is spelled out: userspace has no __packed macro, and a bare
 * __packed token would declare a global object of this type.
 */
struct isp_dsid_multi_bc {
	u64 opcode;
	u8 count;
	u8 pad_9[3];
	u32 range;
	u64 bases[ISP_DSID_MULTI_BC_MAX];
} __attribute__((__packed__));
_Static_assert(offsetof(struct isp_dsid_multi_bc, opcode) == 0,
	       "dsid opcode offset");
_Static_assert(offsetof(struct isp_dsid_multi_bc, count) == 8,
	       "dsid count offset");
_Static_assert(offsetof(struct isp_dsid_multi_bc, range) == 0xc,
	       "dsid range offset");
_Static_assert(offsetof(struct isp_dsid_multi_bc, bases) == 0x10,
	       "dsid bases offset");
_Static_assert(sizeof(struct isp_dsid_multi_bc) ==
		       ISP_DSID_MULTI_BC_HDR +
			       ISP_DSID_MULTI_BC_MAX * sizeof(u64),
	       "dsid multi bc size");

static inline size_t isp_dsid_multi_bc_size(unsigned int count)
{
	return ISP_DSID_MULTI_BC_HDR + (size_t)count * sizeof(u64);
}

static inline int isp_dsid_multi_bc_pack(struct isp_dsid_multi_bc *out,
					 u64 opcode, unsigned int count,
					 u32 range, const u64 *bases)
{
	unsigned int i;

	if (!out || !bases || count == 0 || count > ISP_DSID_MULTI_BC_MAX)
		return -EINVAL;
	memset(out, 0, sizeof(*out));
	out->opcode = opcode;
	out->count = (u8)count;
	out->range = range;
	for (i = 0; i < count; i++)
		out->bases[i] = bases[i];
	return (int)isp_dsid_multi_bc_size(count);
}

static inline u64 isp_bootargs_shared_size(u64 shared_base, u64 override)
{
	if (override)
		return override;
	if (shared_base >= ISP_SHARED_APERTURE)
		return 0;
	return ISP_SHARED_APERTURE - shared_base;
}

static inline u64 isp_fw_mmio_page_down(u64 start)
{
	return start & ~((u64)ISP_FW_MMIO_PAGE - 1);
}

static inline u64 isp_fw_mmio_page_up_inclusive(u64 end_incl)
{
	return (end_incl + ISP_FW_MMIO_PAGE) & ~((u64)ISP_FW_MMIO_PAGE - 1);
}

static inline int isp_fw_mmio_window(u64 start, u64 end_incl, u64 *base,
				     u64 *size)
{
	u64 b, e;

	if (!base || !size)
		return -EINVAL;
	/* Defined even when the window is rejected, so callers cannot read junk. */
	*base = 0;
	*size = 0;
	b = isp_fw_mmio_page_down(start);
	e = isp_fw_mmio_page_up_inclusive(end_incl);
	if (e <= b)
		return -EINVAL;
	*base = b;
	*size = e - b;
	return 0;
}

#endif /* __ISP_M4_LAYOUT_H__ */
