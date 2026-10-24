/* SPDX-License-Identifier: GPL-2.0-only */
/* Address and capability constants for the J713 ISP DART scratch map.
 * The 36-bit DMA window is a Linux configuration. Hardware IAS/OAS stay 42.
 */
#ifndef J713_ISP_DARTMAP_LAYOUT_H
#define J713_ISP_DARTMAP_LAYOUT_H

#include "j713_isp_probe_alg.h"

#define J713_DM_PAGE_COUNT		5u
#define J713_DM_PAGE_SIZE		0x4000u
#define J713_DM_IOVA_BASE		0x13ec000ull
#define J713_DM_IOVA_STRIDE		0x4000ull
#define J713_DM_IOVA_END		0x1400000ull

/* apple,dma-range: base 0, size 1<<36. Not the ADT 1TB vm-base. */
#define J713_DM_DMA_BASE		0ull
#define J713_DM_DMA_SIZE		(1ull << 36)
#define J713_DM_HW_IAS			42u
#define J713_DM_HW_OAS			42u
#define J713_DM_EFFECTIVE_IAS		36u
#define J713_DM_LEVELS_3		3

#define J713_DM_PARAMS3_EXPECT		0x2a2a0202u
#define J713_DM_PARAMS1_SHIFT_MASK	0x0f000000u
#define J713_DM_PARAMS1_SHIFT_14	0x0e000000u
#define J713_DM_PARAMS4_SID_MASK	0x1ffu
#define J713_DM_SID_COUNT		16u
#define J713_DM_PROTECT_LOCK		0x1u
#define J713_DM_TCR_TRANSLATE		0x1u
#define J713_DM_TCR_FOUR_LEVEL		0x8u
#define J713_DM_TTBR_VALID		0x1u

#define J713_DM_CPU_RUN			0x10u
#define J713_DM_CPU_RUNNING		0x1u
#define J713_DM_CPU_STOPPED		0x2u

#define J713_DM_AIC_PATH		"/soc/interrupt-controller@381180000"
#define J713_DM_AIC_PHANDLE		0x0du
#define J713_DM_AIC_CELLS		3u
#define J713_DM_AIC_REG0		0x381000000ull
#define J713_DM_AIC_REG0_SIZE		0x1cc000ull
#define J713_DM_AIC_REG1		0x381040000ull
#define J713_DM_AIC_REG1_SIZE		0x4000ull
#define J713_DM_AIC_IRQ			387u
#define J713_DM_AIC_IRQ_TYPE		0u
#define J713_DM_AIC_IRQ_FLAGS		4u

#define J713_DM_MASTER_COMPAT		"kb2uka,j713-isp-dart-map-test"
#define J713_DM_DART_COMPAT		"apple,t8110-dart"

static inline u64 j713_dm_iova(unsigned int index)
{
	return J713_DM_IOVA_BASE + (u64)index * J713_DM_IOVA_STRIDE;
}

static inline unsigned int j713_dm_fls64(u64 value)
{
	if (!value)
		return 0;
	return 64u - (unsigned int)__builtin_clzll(value);
}

/* min(hw IAS, fls64(dma_max)). dma_max is the last byte, size-1. */
static inline unsigned int j713_dm_effective_ias(unsigned int hw_ias, u64 dma_max)
{
	unsigned int fls = j713_dm_fls64(dma_max);

	return hw_ias < fls ? hw_ias : fls;
}

/* io-pgtable-dart level count for a non-4KiB granule, including the table. */
static inline int j713_dm_levels(unsigned int ias, unsigned int pg_shift)
{
	unsigned int bits_per_level;
	unsigned int va_bits;
	int levels;

	if (pg_shift <= 3 || ias <= pg_shift)
		return -EINVAL;
	bits_per_level = pg_shift - 3u;
	va_bits = ias - pg_shift;
	levels = (int)((va_bits + bits_per_level - 1u) / bits_per_level);
	if (levels < 2)
		levels = 2;
	return levels + 1;
}

static inline int j713_dm_cpu_stopped(u32 ctrl, u32 status)
{
	if (ctrl & J713_DM_CPU_RUN)
		return 0;
	if (status & J713_DM_CPU_RUN)
		return 0;
	if (status & J713_DM_CPU_RUNNING)
		return 0;
	if (!(status & J713_DM_CPU_STOPPED))
		return 0;
	return 1;
}

static inline int j713_dm_tcr_is_3level(u32 tcr)
{
	return tcr == J713_DM_TCR_TRANSLATE && !(tcr & J713_DM_TCR_FOUR_LEVEL);
}

#endif
