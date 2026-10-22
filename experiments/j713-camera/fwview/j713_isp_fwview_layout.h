/* SPDX-License-Identifier: GPL-2.0-only */
/* Address constants for the J713 ISP firmware view.
 * The DMA window is the ADT vm-base/vm-size. Hardware IAS/OAS stay 42.
 * TEXT and DATA physical addresses are iBoot-preloaded. They are not
 * Linux RAM and must never be passed to a page allocator or free.
 */
#ifndef J713_ISP_FWVIEW_LAYOUT_H
#define J713_ISP_FWVIEW_LAYOUT_H

#include "j713_isp_probe_alg.h"

#define J713_DM_PAGE_SIZE		0x4000u

/* linux/iommu.h IOMMU_READ, IOMMU_WRITE, and IOMMU_CACHE. main.c asserts
 * these match the kernel macros. TEXT is not writable. DATA, heap, and
 * IPC are.
 */
#define J713_DM_PROT_READ		1u
#define J713_DM_PROT_WRITE		2u
#define J713_DM_PROT_CACHE		4u
#define J713_DM_TEXT_PROT		(J713_DM_PROT_READ | J713_DM_PROT_CACHE)
#define J713_DM_RW_PROT			(J713_DM_PROT_READ | J713_DM_PROT_WRITE | \
					 J713_DM_PROT_CACHE)

/* apple,dma-range: ADT vm-base and vm-size. Not the R5 [0, 2^36) window. */
#define J713_DM_DMA_BASE		0x10000000000ull
#define J713_DM_DMA_SIZE		0xe0000000ull
#define J713_DM_HW_IAS			42u
#define J713_DM_HW_OAS			42u
/* fls64(dma last byte) is 41, and min(hw IAS, 41) is 41. Four-level tables. */
#define J713_DM_EFFECTIVE_IAS		41u
#define J713_DM_TABLE_LEVELS		4

#define J713_DM_TEXT_IOVA		0x10000000000ull
#define J713_DM_TEXT_PHYS		0x10000d20000ull
#define J713_DM_TEXT_SIZE		0xca4000ull
#define J713_DM_DATA_IOVA		0x10000ca4000ull
#define J713_DM_DATA_PHYS		0x10001f30000ull
#define J713_DM_DATA_SIZE		0x748000ull
#define J713_DM_HEAP_IOVA		0x100013ec000ull
#define J713_DM_HEAP_SIZE		0x14000ull
#define J713_DM_IPC_IOVA		0x10001400000ull
#define J713_DM_IPC_SIZE		0x1c000ull

#define J713_DM_TEXT_PAGES		(J713_DM_TEXT_SIZE / J713_DM_PAGE_SIZE)
#define J713_DM_DATA_PAGES		(J713_DM_DATA_SIZE / J713_DM_PAGE_SIZE)
#define J713_DM_HEAP_PAGES		(J713_DM_HEAP_SIZE / J713_DM_PAGE_SIZE)
#define J713_DM_IPC_PAGES		(J713_DM_IPC_SIZE / J713_DM_PAGE_SIZE)
#define J713_DM_ALLOC_PAGES		(J713_DM_HEAP_PAGES + J713_DM_IPC_PAGES)
#define J713_DM_MAP_PAGES		(J713_DM_TEXT_PAGES + J713_DM_DATA_PAGES + \
					 J713_DM_ALLOC_PAGES)
/* Pages the map step reports. Heap+IPC are the only ones Linux allocates. */
#define J713_DM_PAGE_COUNT		J713_DM_MAP_PAGES

#define J713_DM_PARAMS3_EXPECT		0x2a2a0202u
#define J713_DM_PARAMS1_SHIFT_MASK	0x0f000000u
#define J713_DM_PARAMS1_SHIFT_14	0x0e000000u
#define J713_DM_PARAMS4_SID_MASK	0x1ffu
#define J713_DM_SID_COUNT		16u
#define J713_DM_PROTECT_LOCK		0x1u
#define J713_DM_TCR_TRANSLATE		0x1u
#define J713_DM_TCR_FOUR_LEVEL		0x8u
#define J713_DM_TCR_4LEVEL		(J713_DM_TCR_TRANSLATE | J713_DM_TCR_FOUR_LEVEL)
#define J713_DM_TTBR_VALID		0x1u
/* T8110 TTBR: valid is bit 0, the table address field starts at bit 2,
 * and the address is in 16KiB pages. Same fields as DART_T8110_TTBR_*.
 */
#define J713_DM_TTBR_ADDR_FIELD_SHIFT	2u
#define J713_DM_TTBR_SHIFT		14u
#define J713_DM_DART_ERROR_FLAG		(1u << 31)

/* APPLE_DART2 PTE. VALID bit 0, NO_CACHE bit 1, NO_WRITE bit 2, NO_READ
 * bit 3. The output page is (pte & GENMASK_ULL(37, 10)) << 4.
 * A 16KiB granule uses bits_per_level 11 (page shift minus ilog2 of an
 * 8-byte iopte). IAS 41 gives four levels including the TTBR level and
 * tbl_bits 0, so SID0's TTBR is the only top table.
 */
#define J713_DM_PTE_VALID		(1ull << 0)
#define J713_DM_PTE_NO_CACHE		(1ull << 1)
#define J713_DM_PTE_NO_WRITE		(1ull << 2)
#define J713_DM_PTE_NO_READ		(1ull << 3)
#define J713_DM_PTE_PADDR_MASK		0x3ffffffc00ull
#define J713_DM_PTE_PADDR_SHIFT		4u
#define J713_DM_PTE_BITS_PER_LEVEL	11u
#define J713_DM_PTE_IOPTE_SHIFT		3u
#define J713_DM_PTE_INDEX_MASK		((1u << J713_DM_PTE_BITS_PER_LEVEL) - 1u)
#define J713_DM_PTE_LEVELS		4u
#define J713_DM_PTE_TBL_BITS		0u
#define J713_DM_PTE_SAMPLES		3u

#define J713_DM_ERR_FABRIC		(-EPROTO)
#define J713_DM_ERR_DART_FLAG		(-EIO)
#define J713_DM_ERR_LADDER		(-EUCLEAN)
#define J713_DM_ERR_PTE			(-EILSEQ)

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

#define J713_DM_MASTER_COMPAT		"kb2uka,j713-isp-fwview-test"
#define J713_DM_DART_COMPAT		"apple,t8110-dart"

/* One 32-bit read. GPIO1 and each fabric counter get their own window. */
#define J713_DM_MMIO_WORD		4u
#define J713_DM_GPIO1_REL		0x29241d4u
#define J713_DM_GPIO1_PHYS		(J713_ISP_CPU_BASE + J713_DM_GPIO1_REL)
#define J713_DM_FABRIC0_REL		0x74cu
#define J713_DM_FABRIC1_REL		0x84cu
#define J713_DM_FABRIC2_REL		0x94cu
#define J713_DM_FABRIC3_REL		0xa54u
#define J713_DM_FABRIC0_PHYS		(J713_ISP_CPU_BASE + J713_DM_FABRIC0_REL)
#define J713_DM_FABRIC1_PHYS		(J713_ISP_CPU_BASE + J713_DM_FABRIC1_REL)
#define J713_DM_FABRIC2_PHYS		(J713_ISP_CPU_BASE + J713_DM_FABRIC2_REL)
#define J713_DM_FABRIC3_PHYS		(J713_ISP_CPU_BASE + J713_DM_FABRIC3_REL)
#define J713_DM_DART_SID15		15u
#define J713_DM_DART_SID15_TCR		(J713_ISP_DART_TCR_BASE + \
					 (J713_DM_DART_SID15 * J713_ISP_DART_SID_STRIDE))

_Static_assert(J713_DM_PAGE_SIZE == 0x4000u, "page is 16KiB");
_Static_assert(J713_DM_TCR_4LEVEL == 0x9u, "translate plus four-level");
_Static_assert(J713_DM_TEXT_PAGES == 0x329u, "TEXT page count");
_Static_assert(J713_DM_DATA_PAGES == 0x1d2u, "DATA page count");
_Static_assert(J713_DM_HEAP_PAGES == 5u, "heap is 5 pages");
_Static_assert(J713_DM_IPC_PAGES == 7u, "IPC is 7 pages");
_Static_assert(J713_DM_ALLOC_PAGES == 12u, "Linux allocates heap and IPC only");
_Static_assert((J713_DM_TEXT_IOVA & (J713_DM_PAGE_SIZE - 1u)) == 0ull, "TEXT iova");
_Static_assert((J713_DM_TEXT_PHYS & (J713_DM_PAGE_SIZE - 1u)) == 0ull, "TEXT phys");
_Static_assert((J713_DM_TEXT_SIZE & (J713_DM_PAGE_SIZE - 1u)) == 0ull, "TEXT size");
_Static_assert((J713_DM_DATA_IOVA & (J713_DM_PAGE_SIZE - 1u)) == 0ull, "DATA iova");
_Static_assert((J713_DM_DATA_PHYS & (J713_DM_PAGE_SIZE - 1u)) == 0ull, "DATA phys");
_Static_assert((J713_DM_DATA_SIZE & (J713_DM_PAGE_SIZE - 1u)) == 0ull, "DATA size");
_Static_assert((J713_DM_HEAP_IOVA & (J713_DM_PAGE_SIZE - 1u)) == 0ull, "heap iova");
_Static_assert((J713_DM_HEAP_SIZE & (J713_DM_PAGE_SIZE - 1u)) == 0ull, "heap size");
_Static_assert((J713_DM_IPC_IOVA & (J713_DM_PAGE_SIZE - 1u)) == 0ull, "IPC iova");
_Static_assert((J713_DM_IPC_SIZE & (J713_DM_PAGE_SIZE - 1u)) == 0ull, "IPC size");
_Static_assert(J713_DM_TEXT_IOVA + J713_DM_TEXT_SIZE == J713_DM_DATA_IOVA, "TEXT end");
_Static_assert(J713_DM_DATA_IOVA + J713_DM_DATA_SIZE == J713_DM_HEAP_IOVA, "DATA end");
_Static_assert(J713_DM_HEAP_IOVA == 0x100013ec000ull, "heap start");
_Static_assert(J713_DM_HEAP_IOVA + J713_DM_HEAP_SIZE == J713_DM_IPC_IOVA, "heap end");
_Static_assert(J713_DM_IPC_IOVA == 0x10001400000ull, "IPC start");
_Static_assert(J713_DM_TEXT_IOVA >= J713_DM_DMA_BASE, "TEXT inside window");
_Static_assert(J713_DM_IPC_IOVA + J713_DM_IPC_SIZE <=
	       J713_DM_DMA_BASE + J713_DM_DMA_SIZE, "IPC inside window");
_Static_assert(J713_DM_TEXT_PHYS + J713_DM_TEXT_SIZE <= J713_DM_DATA_PHYS,
	       "preloaded phys overlap");
_Static_assert(J713_DM_TEXT_PHYS + J713_DM_TEXT_SIZE == 0x100019c4000ull,
	       "TEXT phys end");
_Static_assert(J713_DM_DATA_PHYS + J713_DM_DATA_SIZE == 0x10002678000ull,
	       "DATA phys end");
_Static_assert(J713_DM_TEXT_PHYS != J713_DM_TEXT_IOVA, "TEXT phys is not the IOVA");
_Static_assert(J713_DM_DATA_PHYS != J713_DM_DATA_IOVA, "DATA phys is not the IOVA");
_Static_assert(J713_DM_GPIO1_PHYS == 0x48e9241d4ull, "GPIO1 cpu phys");
_Static_assert(J713_DM_FABRIC0_PHYS == 0x48c00074cull, "fabric 0x74c");
_Static_assert(J713_DM_FABRIC1_PHYS == 0x48c00084cull, "fabric 0x84c");
_Static_assert(J713_DM_FABRIC2_PHYS == 0x48c00094cull, "fabric 0x94c");
_Static_assert(J713_DM_FABRIC3_PHYS == 0x48c000a54ull, "fabric 0xa54");
_Static_assert(J713_DM_DART_SID15_TCR == 0x103cu, "SID15 TCR");
_Static_assert(J713_DM_MMIO_WORD == 4u, "premise windows are one word");
_Static_assert(J713_DM_PROT_READ == 1u, "IOMMU_READ value");
_Static_assert(J713_DM_PROT_WRITE == 2u, "IOMMU_WRITE value");
_Static_assert(J713_DM_PROT_CACHE == 4u, "IOMMU_CACHE value");
_Static_assert((J713_DM_TEXT_PROT & J713_DM_PROT_WRITE) == 0u, "TEXT prot");
_Static_assert((J713_DM_RW_PROT & J713_DM_PROT_WRITE) != 0u, "RW prot");
_Static_assert(J713_DM_TEXT_PROT == 5u, "TEXT is read and cache");
_Static_assert(J713_DM_RW_PROT == 7u, "RW is read, write, and cache");
_Static_assert(J713_DM_PTE_PADDR_MASK == 0x3ffffffc00ull, "DART2 paddr mask");
_Static_assert(J713_DM_PTE_LEVELS == J713_DM_TABLE_LEVELS, "walk levels");
_Static_assert(J713_DM_PTE_SAMPLES == 3u, "first, middle, last");

static inline u64 j713_dm_dma_last(void)
{
	return J713_DM_DMA_BASE + J713_DM_DMA_SIZE - 1ull;
}

static inline u64 j713_dm_text_iova(unsigned int page)
{
	return J713_DM_TEXT_IOVA + (u64)page * J713_DM_PAGE_SIZE;
}

static inline u64 j713_dm_text_phys(unsigned int page)
{
	return J713_DM_TEXT_PHYS + (u64)page * J713_DM_PAGE_SIZE;
}

static inline u64 j713_dm_data_iova(unsigned int page)
{
	return J713_DM_DATA_IOVA + (u64)page * J713_DM_PAGE_SIZE;
}

static inline u64 j713_dm_data_phys(unsigned int page)
{
	return J713_DM_DATA_PHYS + (u64)page * J713_DM_PAGE_SIZE;
}

static inline u64 j713_dm_heap_iova(unsigned int page)
{
	return J713_DM_HEAP_IOVA + (u64)page * J713_DM_PAGE_SIZE;
}

static inline u64 j713_dm_ipc_iova(unsigned int page)
{
	return J713_DM_IPC_IOVA + (u64)page * J713_DM_PAGE_SIZE;
}

/* True for every byte of iBoot TEXT and DATA. Never allocate or free these. */
static inline int j713_dm_phys_is_preloaded(u64 phys)
{
	if (phys >= J713_DM_TEXT_PHYS &&
	    phys < J713_DM_TEXT_PHYS + J713_DM_TEXT_SIZE)
		return 1;
	if (phys >= J713_DM_DATA_PHYS &&
	    phys < J713_DM_DATA_PHYS + J713_DM_DATA_SIZE)
		return 1;
	return 0;
}

/* Both endpoints of [phys, phys + size) are iBoot TEXT or DATA.
 * A zero or wrapping size is not a preloaded span.
 */
static inline int j713_dm_span_is_preloaded(u64 phys, u64 size)
{
	u64 last;

	if (!size || size - 1ull > ~0ull - phys)
		return 0;
	last = phys + size - 1ull;
	return j713_dm_phys_is_preloaded(phys) &&
	       j713_dm_phys_is_preloaded(last);
}

static inline unsigned int j713_dm_fls64(u64 value)
{
	if (!value)
		return 0;
	return 64u - (unsigned int)__builtin_clzll(value);
}

/* min(hw IAS, fls64(dma_max)). dma_max is the last byte of the DMA window. */
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

/* Mapped SID0 TCR. Exactly translate-enable plus four-level. 0x1 is not it. */
static inline int j713_dm_tcr_is_4level(u32 tcr)
{
	return tcr == J713_DM_TCR_4LEVEL;
}

/* Fabric counters must all be 0. Any nonzero DART ERROR word rejects the
 * run before attach. GPIO1, SID15 TCR, and PROTECT are recorded by the
 * caller and not judged.
 */
static inline int j713_dm_premise_rc(const u32 fabric[4], const u32 error[3])
{
	unsigned int i;

	if (!fabric || !error)
		return -EINVAL;
	for (i = 0; i < 4u; i++) {
		if (fabric[i])
			return J713_DM_ERR_FABRIC;
	}
	for (i = 0; i < J713_ISP_DART_INSTANCES; i++) {
		if (error[i])
			return J713_DM_ERR_DART_FLAG;
	}
	return 0;
}

/* apple_dart_alloc_pgtable's level split. levels includes the TTBR level.
 * A 4KiB granule may use up to four top tables. A 16KiB granule uses one.
 */
static inline int j713_dm_pte_geometry(unsigned int ias, unsigned int pg_shift,
				       int *levels, int *tbl_bits,
				       int *bits_per_level)
{
	int max_tbl_bits;
	int bpl;
	int va_bits;
	int nlevels;
	int tbl;

	if (!levels || !tbl_bits || !bits_per_level)
		return -EINVAL;
	if (pg_shift <= 3u || ias <= pg_shift)
		return -EINVAL;
	max_tbl_bits = pg_shift == 12u ? 2 : 0;
	bpl = (int)pg_shift - 3;
	va_bits = (int)ias - (int)pg_shift;
	nlevels = (va_bits - max_tbl_bits + bpl - 1) / bpl;
	if (nlevels < 2)
		nlevels = 2;
	if (nlevels > 3)
		return -EINVAL;
	tbl = va_bits - (bpl * nlevels);
	if (tbl < 0)
		tbl = 0;
	if (tbl > max_tbl_bits)
		return -EINVAL;
	*levels = nlevels + 1;
	*tbl_bits = tbl;
	*bits_per_level = bpl;
	return 0;
}

/* dart_get_index: iova >> (level * bits_per_level + ilog2(sizeof(iopte))). */
static inline int j713_dm_pte_index(u64 iova, unsigned int level)
{
	unsigned int shift;

	if (level < 1u || level > J713_DM_PTE_LEVELS)
		return -EINVAL;
	shift = level * J713_DM_PTE_BITS_PER_LEVEL + J713_DM_PTE_IOPTE_SHIFT;
	if (shift >= 64u)
		return 0;
	return (int)((iova >> shift) & J713_DM_PTE_INDEX_MASK);
}

/* which is 0, 1, 2: first page, pages/2, and the last page. */
static inline unsigned int j713_dm_sample_index(unsigned int pages,
					       unsigned int which)
{
	if (!pages || which >= J713_DM_PTE_SAMPLES)
		return 0;
	if (which == 0u)
		return 0u;
	if (which == 1u)
		return pages / 2u;
	return pages - 1u;
}

/* T8110 decode from apple_dart_hw_map_locked_ttbr. Bit 1 is shifted out. */
static inline int j713_dm_ttbr_to_phys(u32 ttbr, u64 *phys)
{
	u32 field;

	if (!phys)
		return -EINVAL;
	if (!(ttbr & J713_DM_TTBR_VALID))
		return -EINVAL;
	field = ttbr & ~J713_DM_TTBR_VALID;
	field >>= J713_DM_TTBR_ADDR_FIELD_SHIFT;
	*phys = (u64)field << J713_DM_TTBR_SHIFT;
	if (!*phys || (*phys & (J713_DM_PAGE_SIZE - 1ull)))
		return J713_DM_ERR_PTE;
	return 0;
}

static inline u64 j713_dm_pte_to_paddr(u64 pte)
{
	return (pte & J713_DM_PTE_PADDR_MASK) << J713_DM_PTE_PADDR_SHIFT;
}

static inline u64 j713_dm_paddr_to_iopte(u64 paddr)
{
	return (paddr >> J713_DM_PTE_PADDR_SHIFT) & J713_DM_PTE_PADDR_MASK;
}

static inline u32 j713_dm_pte_low_bits(u64 pte)
{
	return (u32)pte;
}

/* want_no_write is 1 for TEXT and 0 for DATA, heap, and IPC.
 * NO_READ and NO_CACHE must be clear. IOMMU_CACHE was requested.
 */
static inline int j713_dm_pte_check_leaf(u64 pte, u64 expect_phys,
					int want_no_write)
{
	if (!(pte & J713_DM_PTE_VALID))
		return J713_DM_ERR_PTE;
	if (pte & J713_DM_PTE_NO_READ)
		return J713_DM_ERR_PTE;
	if (want_no_write) {
		if (!(pte & J713_DM_PTE_NO_WRITE))
			return J713_DM_ERR_PTE;
	} else if (pte & J713_DM_PTE_NO_WRITE) {
		return J713_DM_ERR_PTE;
	}
	if (pte & J713_DM_PTE_NO_CACHE)
		return J713_DM_ERR_PTE;
	if (j713_dm_pte_to_paddr(pte) != expect_phys)
		return J713_DM_ERR_PTE;
	return 0;
}

typedef int (*j713_dm_pte_read_fn)(void *ctx, u64 table_phys,
				  unsigned int index, u64 *pte);

/* Read-only walk from a decoded SID0 TTBR. Level 4 must be pgd[0].
 * Levels 3 and 2 are next-table entries. Level 1 is the leaf.
 * read_pte must not write. This function does not write.
 */
static inline int j713_dm_pte_walk(u64 ttbr_phys, u64 iova,
				  j713_dm_pte_read_fn read_pte, void *ctx,
				  u64 *leaf)
{
	u64 table;
	u64 pte;
	unsigned int level;
	int idx;
	int rc;

	if (!read_pte || !leaf)
		return -EINVAL;
	if (!ttbr_phys || (ttbr_phys & (J713_DM_PAGE_SIZE - 1ull)))
		return J713_DM_ERR_PTE;
	idx = j713_dm_pte_index(iova, J713_DM_PTE_LEVELS);
	if (idx < 0)
		return -EINVAL;
	/* tbl_bits 0: only pgd[0]. dart_map_pages rejects tbl >= 1 << tbl_bits. */
	if (idx >= (1 << J713_DM_PTE_TBL_BITS))
		return J713_DM_ERR_PTE;
	table = ttbr_phys;
	for (level = J713_DM_PTE_LEVELS - 1u; level > 1u; level--) {
		idx = j713_dm_pte_index(iova, level);
		if (idx < 0)
			return -EINVAL;
		pte = 0;
		rc = read_pte(ctx, table, (unsigned int)idx, &pte);
		if (rc)
			return rc;
		if (!(pte & J713_DM_PTE_VALID))
			return J713_DM_ERR_PTE;
		table = j713_dm_pte_to_paddr(pte);
		if (!table || (table & (J713_DM_PAGE_SIZE - 1ull)))
			return J713_DM_ERR_PTE;
	}
	idx = j713_dm_pte_index(iova, 1u);
	if (idx < 0)
		return -EINVAL;
	pte = 0;
	rc = read_pte(ctx, table, (unsigned int)idx, &pte);
	if (rc)
		return rc;
	*leaf = pte;
	return 0;
}

static inline int j713_dm_pte_check_sample(u64 ttbr_phys, u64 iova,
					  u64 expect_phys, int want_no_write,
					  j713_dm_pte_read_fn read_pte,
					  void *ctx, u64 *leaf_out)
{
	u64 leaf = 0;
	int rc;

	rc = j713_dm_pte_walk(ttbr_phys, iova, read_pte, ctx, &leaf);
	if (leaf_out)
		*leaf_out = leaf;
	if (rc)
		return rc;
	return j713_dm_pte_check_leaf(leaf, expect_phys, want_no_write);
}

#endif
