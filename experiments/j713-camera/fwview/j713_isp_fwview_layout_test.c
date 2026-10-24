/* SPDX-License-Identifier: GPL-2.0-only */
/* Literal layout checks. Included headers supply the functions; expected
 * values below are numeric literals, not the macros compared with themselves.
 * This file does not include the lifecycle helper and has its own main.
 * No firmware image, sensor map, or DMA trigger is described here.
 */
#include "j713_isp_fwview_layout.h"

#include <stdio.h>
#include <string.h>

static int failures;

static void fail_at(const char *name, int line, const char *why)
{
	fprintf(stderr, "FAIL %s:%d %s\n", name, line, why);
	failures++;
}

#define CHECK(cond) do { \
	if (!(cond)) fail_at(__func__, __LINE__, #cond); \
} while (0)

static void test_firmware_window(void)
{
	CHECK(J713_DM_PAGE_SIZE == 0x4000u);
	CHECK(J713_DM_TEXT_IOVA == 0x10000000000ull);
	CHECK(J713_DM_TEXT_PHYS == 0x10000d20000ull);
	CHECK(J713_DM_TEXT_SIZE == 0xca4000ull);
	CHECK(J713_DM_TEXT_PAGES == 0x329u);
	CHECK(J713_DM_DATA_IOVA == 0x10000ca4000ull);
	CHECK(J713_DM_DATA_PHYS == 0x10001f30000ull);
	CHECK(J713_DM_DATA_SIZE == 0x748000ull);
	CHECK(J713_DM_DATA_PAGES == 0x1d2u);
	CHECK(J713_DM_HEAP_IOVA == 0x100013ec000ull);
	CHECK(J713_DM_HEAP_SIZE == 0x14000ull);
	CHECK(J713_DM_HEAP_PAGES == 5u);
	CHECK(J713_DM_IPC_IOVA == 0x10001400000ull);
	CHECK(J713_DM_IPC_SIZE == 0x1c000ull);
	CHECK(J713_DM_IPC_PAGES == 7u);
	CHECK(J713_DM_ALLOC_PAGES == 12u);
	CHECK(J713_DM_MAP_PAGES == 0x329u + 0x1d2u + 5u + 7u);
	CHECK(J713_DM_PAGE_COUNT == J713_DM_MAP_PAGES);
	CHECK(J713_DM_TEXT_IOVA + J713_DM_TEXT_SIZE == J713_DM_DATA_IOVA);
	CHECK(J713_DM_DATA_IOVA + J713_DM_DATA_SIZE == 0x100013ec000ull);
	CHECK(J713_DM_HEAP_IOVA + J713_DM_HEAP_SIZE == 0x10001400000ull);
	CHECK(J713_DM_IPC_IOVA + J713_DM_IPC_SIZE == 0x1000141c000ull);
	CHECK(J713_DM_IPC_IOVA + J713_DM_IPC_SIZE <=
	      0x10000000000ull + 0xe0000000ull);
	CHECK(j713_dm_text_iova(0) == 0x10000000000ull);
	CHECK(j713_dm_text_iova(1) == 0x10000004000ull);
	CHECK(j713_dm_text_phys(0) == 0x10000d20000ull);
	CHECK(j713_dm_text_phys(1) == 0x10000d24000ull);
	CHECK(j713_dm_data_iova(0) == 0x10000ca4000ull);
	CHECK(j713_dm_data_phys(0) == 0x10001f30000ull);
	CHECK(j713_dm_heap_iova(0) == 0x100013ec000ull);
	CHECK(j713_dm_heap_iova(4) + 0x4000ull == 0x10001400000ull);
	CHECK(j713_dm_ipc_iova(0) == 0x10001400000ull);
	CHECK(j713_dm_ipc_iova(6) + 0x4000ull == 0x1000141c000ull);
	CHECK(j713_dm_phys_is_preloaded(0x10000d20000ull));
	CHECK(j713_dm_phys_is_preloaded(0x10000d20000ull + 0xca4000ull - 1ull));
	CHECK(j713_dm_phys_is_preloaded(0x10001f30000ull));
	CHECK(j713_dm_phys_is_preloaded(0x10001f30000ull + 0x748000ull - 1ull));
	CHECK(!j713_dm_phys_is_preloaded(0ull));
	CHECK(!j713_dm_phys_is_preloaded(0x10000000000ull));
	CHECK(!j713_dm_phys_is_preloaded(0x10000d20000ull + 0xca4000ull));
	CHECK(!j713_dm_phys_is_preloaded(0x10001f30000ull + 0x748000ull));
	CHECK(J713_DM_TEXT_PHYS + J713_DM_TEXT_SIZE == 0x100019c4000ull);
	CHECK(J713_DM_DATA_PHYS + J713_DM_DATA_SIZE == 0x10002678000ull);
}

static void test_preloaded_span_ends(void)
{
	/* First byte and last byte. The documented ends are exclusive. */
	CHECK(j713_dm_span_is_preloaded(0x10000d20000ull, 0xca4000ull));
	CHECK(j713_dm_span_is_preloaded(0x10001f30000ull, 0x748000ull));
	CHECK(j713_dm_span_is_preloaded(0x100019c4000ull - 1ull, 1ull));
	CHECK(j713_dm_span_is_preloaded(0x10002678000ull - 1ull, 1ull));
	CHECK(!j713_dm_span_is_preloaded(0x100019c4000ull - 1ull, 2ull));
	CHECK(!j713_dm_span_is_preloaded(0x10002678000ull - 1ull, 2ull));
	CHECK(!j713_dm_span_is_preloaded(0x10000d20000ull, 0xca4000ull + 1ull));
	CHECK(!j713_dm_span_is_preloaded(0x10001f30000ull, 0x748000ull + 1ull));
	CHECK(!j713_dm_span_is_preloaded(0x100019c4000ull, 1ull));
	CHECK(!j713_dm_span_is_preloaded(0x10002678000ull, 1ull));
	CHECK(!j713_dm_span_is_preloaded(0x10000d20000ull, 0ull));
	CHECK(!j713_dm_span_is_preloaded(~0ull - 1ull, 3ull));
}

static void test_ias_levels(void)
{
	/* Hardware IAS/OAS stay 42. The ADT window keeps bit 40, so the
	 * last byte's fls is 41 and a 16KiB granule is 4 levels.
	 * A 36-bit window is still 3 levels and cannot name these IOVAs.
	 */
	CHECK(J713_DM_HW_IAS == 42u);
	CHECK(J713_DM_HW_OAS == 42u);
	CHECK(J713_DM_EFFECTIVE_IAS == 41u);
	CHECK(J713_DM_TABLE_LEVELS == 4);
	CHECK(J713_DM_DMA_BASE == 0x10000000000ull);
	CHECK(J713_DM_DMA_SIZE == 0xe0000000ull);
	CHECK(J713_DM_DMA_SIZE != 0x1000000000ull);
	CHECK(j713_dm_dma_last() == 0x100dfffffffull);
	CHECK(j713_dm_fls64(0) == 0u);
	CHECK(j713_dm_fls64(0x100dfffffffull) == 41u);
	CHECK(j713_dm_fls64((1ull << 36) - 1ull) == 36u);
	CHECK(j713_dm_effective_ias(42u, 0x100dfffffffull) == 41u);
	CHECK(j713_dm_effective_ias(36u, (1ull << 42) - 1ull) == 36u);
	CHECK(j713_dm_levels(41u, 14u) == 4);
	CHECK(j713_dm_levels(36u, 14u) == 3);
	CHECK(j713_dm_levels(42u, 14u) == 4);
	CHECK(j713_dm_levels(14u, 14u) == -EINVAL);
	CHECK(j713_dm_levels(36u, 3u) == -EINVAL);
	CHECK(J713_DM_PARAMS3_EXPECT == 0x2a2a0202u);
}

static void test_tcr_ttbr_cpu(void)
{
	u32 noise;
	u32 fabric[4] = { 0, 0, 0, 0 };
	u32 error[3] = { 0, 0, 0 };

	CHECK(J713_DM_TCR_TRANSLATE == 0x1u);
	CHECK(J713_DM_TCR_FOUR_LEVEL == 0x8u);
	CHECK(J713_DM_TCR_4LEVEL == 0x9u);
	CHECK(J713_DM_TTBR_VALID == 0x1u);
	CHECK(j713_dm_tcr_is_4level(0x9u));
	CHECK(!j713_dm_tcr_is_4level(0x1u));
	CHECK(!j713_dm_tcr_is_4level(0x8u));
	CHECK(!j713_dm_tcr_is_4level(0u));
	CHECK(!j713_dm_tcr_is_4level(0x9u | 0x2u));
	CHECK(!j713_dm_tcr_is_4level(0xbu));
	CHECK(J713_ISP_DART_SID0_TCR == 0x1000u);
	CHECK(J713_ISP_DART_SID0_TTBR == 0x1400u);
	CHECK(J713_DM_DART_SID15_TCR == 0x103cu);
	CHECK(J713_ISP_DART_TCR(7) == 0x101cu);
	CHECK(J713_ISP_DART_TTBR(7) == 0x141cu);
	CHECK(J713_ISP_DART_TTBR(15) == 0x143cu);
	CHECK(J713_DM_SURVEY_WORDS == 48u);
	CHECK(J713_DM_ERR_QUIESCE != 0);

	noise = 0xffff0000u;
	CHECK((noise & 0x1u) == 0u);
	CHECK((0x1u & J713_DM_TTBR_VALID) == 0x1u);
	CHECK((noise | 0x1u) != noise);

	CHECK(j713_dm_premise_rc(fabric, error) == 0);
	fabric[3] = 1u;
	CHECK(j713_dm_premise_rc(fabric, error) == J713_DM_ERR_FABRIC);
	fabric[3] = 0u;
	/* Any nonzero ERROR word, not only bit 31. */
	error[1] = 0x7fffffffu;
	CHECK(j713_dm_premise_rc(fabric, error) == J713_DM_ERR_DART_FLAG);
	error[1] = 0u;
	error[0] = 1u;
	CHECK(j713_dm_premise_rc(fabric, error) == J713_DM_ERR_DART_FLAG);
	error[0] = 0u;
	error[2] = 0x80000000u;
	CHECK(j713_dm_premise_rc(fabric, error) == J713_DM_ERR_DART_FLAG);
	CHECK(j713_dm_premise_rc(NULL, error) == -EINVAL);
	CHECK(J713_DM_GPIO1_PHYS == 0x48e9241d4ull);
	CHECK(J713_DM_FABRIC0_PHYS == 0x48c00074cull);
	CHECK(J713_DM_FABRIC1_PHYS == 0x48c00084cull);
	CHECK(J713_DM_FABRIC2_PHYS == 0x48c00094cull);
	CHECK(J713_DM_FABRIC3_PHYS == 0x48c000a54ull);

	/* Live coproc status 0x0330006a: RUN 0x10 clear, RUNNING 0x1 clear,
	 * STOPPED 0x2 set. Not a firmware-start or DMA claim.
	 */
	CHECK(J713_DM_CPU_RUN == 0x10u);
	CHECK(J713_DM_CPU_RUNNING == 0x1u);
	CHECK(J713_DM_CPU_STOPPED == 0x2u);
	CHECK((0x0330006au & 0x10u) == 0u);
	CHECK((0x0330006au & 0x1u) == 0u);
	CHECK((0x0330006au & 0x2u) == 0x2u);
	CHECK(j713_dm_cpu_stopped(0x0u, 0x0330006au) == 1);
	CHECK(j713_dm_cpu_stopped(0x10u, 0x0330006au) == 0);
	CHECK(j713_dm_cpu_stopped(0x0u, 0x0330006au | 0x1u) == 0);
	CHECK(j713_dm_cpu_stopped(0x0u, 0x0330006au | 0x10u) == 0);
	CHECK(j713_dm_cpu_stopped(0x0u, 0x0330006au & ~0x2u) == 0);
}

static void test_prot_constants(void)
{
	CHECK(J713_DM_PROT_READ == 1u);
	CHECK(J713_DM_PROT_WRITE == 2u);
	CHECK(J713_DM_PROT_CACHE == 4u);
	CHECK(J713_DM_TEXT_PROT == 5u);
	CHECK(J713_DM_RW_PROT == 7u);
	CHECK((J713_DM_TEXT_PROT & 2u) == 0u);
	CHECK((J713_DM_RW_PROT & 2u) == 2u);
}

static void test_pte_geometry(void)
{
	int levels = -1;
	int tbl = -1;
	int bpl = -1;
	u64 phys = 1;

	CHECK(j713_dm_pte_geometry(41u, 14u, &levels, &tbl, &bpl) == 0);
	CHECK(levels == 4);
	CHECK(tbl == 0);
	CHECK(bpl == 11);
	CHECK(j713_dm_pte_geometry(32u, 12u, &levels, &tbl, &bpl) == 0);
	CHECK(levels == 3);
	CHECK(tbl == 2);
	CHECK(bpl == 9);
	CHECK(j713_dm_pte_geometry(36u, 12u, &levels, &tbl, &bpl) == 0);
	CHECK(levels == 4);
	CHECK(tbl == 0);
	CHECK(bpl == 9);
	CHECK(j713_dm_pte_geometry(14u, 14u, &levels, &tbl, &bpl) == -EINVAL);

	/* T8110: valid bit 0, field from bit 2, then << 14. Bit 1 falls out. */
	CHECK(j713_dm_ttbr_to_phys(0x80001u, &phys) == 0);
	CHECK(phys == 0x80000000ull);
	CHECK(j713_dm_ttbr_to_phys(0x80003u, &phys) == 0);
	CHECK(phys == 0x80000000ull);
	CHECK(j713_dm_ttbr_to_phys(0x80005u, &phys) == 0);
	CHECK(phys == 0x80004000ull);
	CHECK(j713_dm_ttbr_to_phys(0x80007u, &phys) == 0);
	CHECK(phys == 0x80004000ull);
	CHECK(j713_dm_ttbr_to_phys(0x80009u, &phys) == 0);
	CHECK(phys == 0x80008000ull);
	CHECK(j713_dm_ttbr_to_phys(0x80000u, &phys) == -EINVAL);
	CHECK(j713_dm_ttbr_to_phys(0x1u, &phys) == J713_DM_ERR_PTE);
	CHECK(j713_dm_ttbr_to_phys(0x80001u, NULL) == -EINVAL);

	CHECK(j713_dm_sample_index(809u, 0u) == 0u);
	CHECK(j713_dm_sample_index(809u, 1u) == 404u);
	CHECK(j713_dm_sample_index(809u, 2u) == 808u);
	CHECK(j713_dm_sample_index(466u, 1u) == 233u);
	CHECK(j713_dm_sample_index(466u, 2u) == 465u);
	CHECK(j713_dm_sample_index(5u, 1u) == 2u);
	CHECK(j713_dm_sample_index(5u, 2u) == 4u);
	CHECK(j713_dm_sample_index(7u, 1u) == 3u);
	CHECK(j713_dm_sample_index(7u, 2u) == 6u);
}

/* Leaves sit at these L1 slots. The walk must find them by index math,
 * not because the test stored through j713_dm_pte_index.
 */
struct pte_sample {
	u64 iova;
	u64 phys;
	unsigned int l1;
	int no_write;
};

static const struct pte_sample pte_samples[] = {
	{ 0x10000000000ull, 0x10000d20000ull, 0x000u, 1 },
	{ 0x10000650000ull, 0x10001370000ull, 0x194u, 1 },
	{ 0x10000ca0000ull, 0x100019c0000ull, 0x328u, 1 },
	{ 0x10000ca4000ull, 0x10001f30000ull, 0x329u, 0 },
	{ 0x10001048000ull, 0x100022d4000ull, 0x412u, 0 },
	{ 0x100013e8000ull, 0x10002674000ull, 0x4fau, 0 },
	{ 0x100013ec000ull, 0x90000000ull, 0x4fbu, 0 },
	{ 0x100013f4000ull, 0x90008000ull, 0x4fdu, 0 },
	{ 0x100013fc000ull, 0x90010000ull, 0x4ffu, 0 },
	{ 0x10001400000ull, 0x91000000ull, 0x500u, 0 },
	{ 0x1000140c000ull, 0x9100c000ull, 0x503u, 0 },
	{ 0x10001418000ull, 0x91018000ull, 0x506u, 0 },
};

#define PTE_NENTS 2048u
#define PTE_TOP_PHYS 0x80000000ull
#define PTE_MID_PHYS 0x80004000ull
#define PTE_LEAF_PHYS 0x80008000ull

static u64 pte_top[PTE_NENTS];
static u64 pte_mid[PTE_NENTS];
static u64 pte_leaf[PTE_NENTS];

static int pte_read(void *ctx, u64 table_phys, unsigned int index, u64 *pte)
{
	(void)ctx;
	if (!pte || index >= PTE_NENTS)
		return -EFAULT;
	if (table_phys == PTE_TOP_PHYS)
		*pte = pte_top[index];
	else if (table_phys == PTE_MID_PHYS)
		*pte = pte_mid[index];
	else if (table_phys == PTE_LEAF_PHYS)
		*pte = pte_leaf[index];
	else
		return -EFAULT;
	return 0;
}

static u64 sample_leaf(u64 phys, int no_write)
{
	u64 pte = j713_dm_paddr_to_iopte(phys) | J713_DM_PTE_VALID | (0xfffull << 40);

	if (no_write)
		pte |= J713_DM_PTE_NO_WRITE;
	return pte;
}

static void install_good_ptes(void)
{
	unsigned int i;

	memset(pte_top, 0, sizeof(pte_top));
	memset(pte_mid, 0, sizeof(pte_mid));
	memset(pte_leaf, 0, sizeof(pte_leaf));
	/* L3 is 16 for every firmware IOVA. L2 is 0. Next tables are literals. */
	pte_top[16] = 0x8000401ull;
	pte_mid[0] = 0x8000801ull;
	for (i = 0; i < ARRAY_SIZE(pte_samples); i++)
		pte_leaf[pte_samples[i].l1] = sample_leaf(pte_samples[i].phys,
							  pte_samples[i].no_write);
}

static int walk_samples(void)
{
	u64 table = 0;
	unsigned int i;
	int rc;

	rc = j713_dm_ttbr_to_phys(0x80001u, &table);
	if (rc)
		return rc;
	if (table != PTE_TOP_PHYS)
		return -EFAULT;
	for (i = 0; i < ARRAY_SIZE(pte_samples); i++) {
		u64 leaf = 0;

		rc = j713_dm_pte_check_sample(table, pte_samples[i].iova,
					      pte_samples[i].phys,
					      pte_samples[i].no_write,
					      pte_read, NULL, &leaf);
		if (rc)
			return rc;
	}
	return 0;
}

static void test_pte_walk(void)
{
	u64 leaf = 0;
	unsigned int i;

	CHECK(j713_dm_pte_index(0x10000000000ull, 4u) == 0);
	CHECK(j713_dm_pte_index(0x10000000000ull, 3u) == 16);
	CHECK(j713_dm_pte_index(0x10000000000ull, 2u) == 0);
	CHECK(j713_dm_pte_index(0x10000000000ull, 1u) == 0x0);
	CHECK(j713_dm_pte_index(0x10000650000ull, 1u) == 0x194);
	CHECK(j713_dm_pte_index(0x10000ca0000ull, 1u) == 0x328);
	CHECK(j713_dm_pte_index(0x10000ca4000ull, 3u) == 16);
	CHECK(j713_dm_pte_index(0x10000ca4000ull, 2u) == 0);
	CHECK(j713_dm_pte_index(0x10000ca4000ull, 1u) == 0x329);
	CHECK(j713_dm_pte_index(0x10001048000ull, 1u) == 0x412);
	CHECK(j713_dm_pte_index(0x100013e8000ull, 1u) == 0x4fa);
	CHECK(j713_dm_pte_index(0x100013ec000ull, 1u) == 0x4fb);
	CHECK(j713_dm_pte_index(0x100013f4000ull, 1u) == 0x4fd);
	CHECK(j713_dm_pte_index(0x100013fc000ull, 1u) == 0x4ff);
	CHECK(j713_dm_pte_index(0x10001400000ull, 1u) == 0x500);
	CHECK(j713_dm_pte_index(0x1000140c000ull, 1u) == 0x503);
	CHECK(j713_dm_pte_index(0x10001418000ull, 1u) == 0x506);
	CHECK(j713_dm_pte_index(1ull << 47, 4u) == 1);
	for (i = 0; i < ARRAY_SIZE(pte_samples); i++) {
		CHECK(j713_dm_pte_index(pte_samples[i].iova, 1u) ==
		      (int)pte_samples[i].l1);
		CHECK(j713_dm_pte_index(pte_samples[i].iova, 4u) == 0);
		CHECK(j713_dm_pte_index(pte_samples[i].iova, 3u) == 16);
		CHECK(j713_dm_pte_index(pte_samples[i].iova, 2u) == 0);
	}

	/* Literals, so a cancelled encode/decode pair still fails. */
	CHECK(j713_dm_pte_check_leaf(0x10000d2005ull, 0x10000d20000ull, 1) == 0);
	CHECK(j713_dm_pte_check_leaf(0xfff10000d2005ull, 0x10000d20000ull, 1) == 0);
	CHECK(j713_dm_pte_check_leaf(0x10000d2001ull, 0x10000d20000ull, 1) ==
	      J713_DM_ERR_PTE);
	CHECK(j713_dm_pte_check_leaf(0x10001f3001ull, 0x10001f30000ull, 0) == 0);
	CHECK(j713_dm_pte_check_leaf(0x10001f3005ull, 0x10001f30000ull, 0) ==
	      J713_DM_ERR_PTE);
	CHECK(j713_dm_pte_check_leaf(0x10001f3003ull, 0x10001f30000ull, 0) ==
	      J713_DM_ERR_PTE);
	CHECK(j713_dm_pte_to_paddr(0x10000d2005ull) == 0x10000d20000ull);
	CHECK(j713_dm_pte_to_paddr(0xfff10000d2005ull) == 0x10000d20000ull);

	install_good_ptes();
	CHECK(walk_samples() == 0);
	CHECK(j713_dm_pte_check_sample(PTE_TOP_PHYS, 1ull << 47, 0x10000d20000ull,
				      1, pte_read, NULL, &leaf) == J713_DM_ERR_PTE);

	install_good_ptes();
	pte_leaf[0x0] &= ~J713_DM_PTE_NO_WRITE;
	CHECK(walk_samples() == J713_DM_ERR_PTE);

	install_good_ptes();
	pte_leaf[0x329] |= J713_DM_PTE_NO_WRITE;
	CHECK(walk_samples() == J713_DM_ERR_PTE);

	install_good_ptes();
	pte_leaf[0x4fb] |= J713_DM_PTE_NO_CACHE;
	CHECK(walk_samples() == J713_DM_ERR_PTE);

	install_good_ptes();
	pte_leaf[0x500] = sample_leaf(0x91004000ull, 0);
	CHECK(walk_samples() == J713_DM_ERR_PTE);

	install_good_ptes();
	pte_leaf[0x328] &= ~J713_DM_PTE_VALID;
	CHECK(walk_samples() == J713_DM_ERR_PTE);
}

static void test_aic_literals(void)
{
	CHECK(J713_DM_AIC_PHANDLE == 0x0du);
	CHECK(J713_DM_AIC_CELLS == 3u);
	CHECK(J713_DM_AIC_IRQ == 387u);
	CHECK(J713_DM_AIC_IRQ_TYPE == 0u);
	CHECK(J713_DM_AIC_IRQ_FLAGS == 4u);
	CHECK(J713_DM_MASTER_COMPAT[0] != '\0');
}

int main(void)
{
	test_firmware_window();
	test_preloaded_span_ends();
	test_ias_levels();
	test_tcr_ttbr_cpu();
	test_prot_constants();
	test_pte_geometry();
	test_pte_walk();
	test_aic_literals();
	if (failures) {
		fprintf(stderr, "%d layout checks failed\n", failures);
		return 1;
	}
	puts("j713_isp_fwview_layout_test: all checks passed");
	return 0;
}
