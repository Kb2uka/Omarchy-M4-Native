/* SPDX-License-Identifier: GPL-2.0-only */
/* Literal layout checks. Included headers supply the functions; expected
 * values below are numeric literals, not the macros compared with themselves.
 * This file does not include the lifecycle helper and has its own main.
 * No firmware image, sensor map, or DMA trigger is described here.
 */
#include "j713_isp_dartmap_layout.h"

#include <stdio.h>

static int failures;

static void fail_at(const char *name, int line, const char *why)
{
	fprintf(stderr, "FAIL %s:%d %s\n", name, line, why);
	failures++;
}

#define CHECK(cond) do { \
	if (!(cond)) fail_at(__func__, __LINE__, #cond); \
} while (0)

static void test_scratch_window(void)
{
	CHECK(J713_DM_PAGE_COUNT == 5u);
	CHECK(J713_DM_PAGE_SIZE == 0x4000u);
	CHECK(J713_DM_IOVA_STRIDE == 0x4000u);
	CHECK(J713_DM_IOVA_BASE == 0x13ec000ull);
	CHECK(J713_DM_IOVA_END == 0x1400000ull);
	CHECK(j713_dm_iova(0) == 0x13ec000ull);
	CHECK(j713_dm_iova(1) == 0x13f0000ull);
	CHECK(j713_dm_iova(2) == 0x13f4000ull);
	CHECK(j713_dm_iova(3) == 0x13f8000ull);
	CHECK(j713_dm_iova(4) == 0x13fc000ull);
	CHECK(j713_dm_iova(4) + 0x4000ull == 0x1400000ull);
	CHECK(5ull * 0x4000ull == 0x14000ull);
	CHECK(0x1400000ull - 0x13ec000ull == 0x14000ull);
	CHECK(0x13ec000ull != 0ull);
}

static void test_ias_levels(void)
{
	/* Hardware IAS/OAS stay 42. Linux apple,dma-range is base 0,
	 * size 2^36, so effective IAS is 36 and a 16KiB granule is 3 levels.
	 * A 42-bit IAS on the same granule is 4 levels. 1TB is not the window.
	 */
	CHECK(J713_DM_HW_IAS == 42u);
	CHECK(J713_DM_HW_OAS == 42u);
	CHECK(J713_DM_EFFECTIVE_IAS == 36u);
	CHECK(J713_DM_DMA_BASE == 0ull);
	CHECK(J713_DM_DMA_SIZE == 0x1000000000ull);
	CHECK(J713_DM_DMA_SIZE == (1ull << 36));
	CHECK(J713_DM_DMA_SIZE != 0x10000000000ull);
	CHECK(J713_DM_PARAMS3_EXPECT == 0x2a2a0202u);
	CHECK(j713_dm_fls64(0) == 0u);
	CHECK(j713_dm_fls64((1ull << 36) - 1ull) == 36u);
	CHECK(j713_dm_effective_ias(42u, (1ull << 36) - 1ull) == 36u);
	CHECK(j713_dm_effective_ias(36u, (1ull << 42) - 1ull) == 36u);
	CHECK(j713_dm_levels(36u, 14u) == 3);
	CHECK(j713_dm_levels(42u, 14u) == 4);
	CHECK(j713_dm_levels(14u, 14u) == -EINVAL);
	CHECK(j713_dm_levels(36u, 3u) == -EINVAL);
}

static void test_tcr_ttbr_cpu(void)
{
	u32 noise;

	CHECK(J713_DM_TCR_TRANSLATE == 0x1u);
	CHECK(J713_DM_TCR_FOUR_LEVEL == 0x8u);
	CHECK(J713_DM_TTBR_VALID == 0x1u);
	CHECK(j713_dm_tcr_is_3level(0x1u));
	CHECK(!j713_dm_tcr_is_3level(0x1u | 0x8u));
	CHECK(!j713_dm_tcr_is_3level(0x8u));
	CHECK(!j713_dm_tcr_is_3level(0u));
	CHECK(J713_ISP_DART_SID0_TCR == 0x1000u);
	CHECK(J713_ISP_DART_SID0_TTBR == 0x1400u);

	noise = 0xffff0000u;
	CHECK((noise & 0x1u) == 0u);
	CHECK((0x1u & J713_DM_TTBR_VALID) == 0x1u);
	CHECK((noise | 0x1u) != noise);

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

static void test_aic_literals(void)
{
	CHECK(J713_DM_AIC_PHANDLE == 0x0du);
	CHECK(J713_DM_AIC_CELLS == 3u);
	CHECK(J713_DM_AIC_IRQ == 387u);
	CHECK(J713_DM_AIC_IRQ_TYPE == 0u);
	CHECK(J713_DM_AIC_IRQ_FLAGS == 4u);
}

int main(void)
{
	test_scratch_window();
	test_ias_levels();
	test_tcr_ttbr_cpu();
	test_aic_literals();
	if (failures) {
		fprintf(stderr, "%d layout checks failed\n", failures);
		return 1;
	}
	puts("j713_isp_dartmap_layout_test: all checks passed");
	return 0;
}
