/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef __ISP_IPC_LAYOUT_H__
#define __ISP_IPC_LAYOUT_H__

#include <linux/types.h>

struct isp_ipc_extent {
	u64 iova;
	u64 size;
};

/* Subtraction keeps firmware-selected extents from wrapping at U64_MAX. */
static inline bool isp_ipc_extent_contains(struct isp_ipc_extent outer,
					 struct isp_ipc_extent inner)
{
	return outer.size <= (u64)-1 - outer.iova &&
	       inner.size && inner.iova >= outer.iova &&
	       inner.size <= outer.size &&
	       inner.iova - outer.iova <= outer.size - inner.size;
}

static inline bool isp_ipc_extents_overlap(struct isp_ipc_extent a,
					 struct isp_ipc_extent b)
{
	if (!a.size || !b.size)
		return false;
	if (a.iova <= b.iova)
		return b.iova - a.iova < a.size;
	return a.iova - b.iova < b.size;
}

static inline bool isp_ipc_ring_valid(struct isp_ipc_extent ipc,
				    struct isp_ipc_extent ring,
				    const struct isp_ipc_extent *reserved,
				    unsigned int count)
{
	/* Messages are 64 bytes and arg0 is published with a u64 store. */
	if ((ring.iova & 7) || (ring.size & 63) ||
	    !isp_ipc_extent_contains(ipc, ring))
		return false;

	for (unsigned int i = 0; i < count; i++)
		if (isp_ipc_extents_overlap(ring, reserved[i]))
			return false;

	return true;
}

#endif /* __ISP_IPC_LAYOUT_H__ */
