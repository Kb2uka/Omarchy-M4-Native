/* SPDX-License-Identifier: GPL-2.0-only */
/* Offline layout tests. Orchestrator runs these off-host. No MMIO. */
#include "isp-m4-layout.h"

#include <stdio.h>
#include <string.h>

#define CISP_OPCODE(x) (((u64)(x)) << 32)

static int failures;

static void fail_at(const char *fn, int line, const char *why)
{
	fprintf(stderr, "FAIL %s:%d %s\n", fn, line, why);
	failures++;
}

#define CHECK(cond) do { \
	if (!(cond)) fail_at(__func__, __LINE__, #cond); \
} while (0)

static void test_dsid_count1_size(void)
{
	struct isp_dsid_multi_bc pkt;
	u64 bases[1] = { 0x220114000ULL };
	int n;

	n = isp_dsid_multi_bc_pack(&pkt, CISP_OPCODE(ISP_DSID_MULTI_BC_OPCODE),
				   1, 0x2fc, bases);
	CHECK(n == 0x18);
	CHECK(isp_dsid_multi_bc_size(1) == 0x18);
	CHECK(offsetof(struct isp_dsid_multi_bc, count) == 8);
	CHECK(offsetof(struct isp_dsid_multi_bc, range) == 0xc);
	CHECK(offsetof(struct isp_dsid_multi_bc, bases) == 0x10);
	CHECK(pkt.count == 1);
	CHECK(pkt.range == 0x2fc);
	CHECK(pkt.bases[0] == 0x220114000ULL);
	CHECK(pkt.bases[1] == 0);
	CHECK((pkt.opcode >> 32) == ISP_DSID_MULTI_BC_OPCODE);
	CHECK((u32)pkt.opcode == 0);
}

static void test_dsid_count2_size(void)
{
	struct isp_dsid_multi_bc pkt;
	u64 bases[2] = { 0x2201d4000ULL, 0x2221d4000ULL };
	int n;

	n = isp_dsid_multi_bc_pack(&pkt, CISP_OPCODE(ISP_DSID_MULTI_BC_OPCODE),
				   2, 0x2fc, bases);
	CHECK(n == 0x20);
	CHECK(isp_dsid_multi_bc_size(2) == 0x20);
	CHECK(pkt.count == 2);
	CHECK(pkt.range == 0x2fc);
	CHECK(pkt.bases[0] == 0x2201d4000ULL);
	CHECK(pkt.bases[1] == 0x2221d4000ULL);
	CHECK(pkt.bases[2] == 0);
}

static void test_dsid_bounds(void)
{
	struct isp_dsid_multi_bc pkt;
	u64 bases[5] = { 1, 2, 3, 4, 5 };

	CHECK(isp_dsid_multi_bc_pack(NULL, 0, 1, 0, bases) == -EINVAL);
	CHECK(isp_dsid_multi_bc_pack(&pkt, 0, 0, 0, bases) == -EINVAL);
	CHECK(isp_dsid_multi_bc_pack(&pkt, 0, 5, 0, bases) == -EINVAL);
	CHECK(isp_dsid_multi_bc_pack(&pkt, 0, 1, 0, NULL) == -EINVAL);
}

static void test_bootargs_shared_size(void)
{
	CHECK(ISP_BOOTARGS_H16_SIZE == 0x290);
	CHECK(ISP_H16_DESC_OFF == 0x70);
	CHECK(isp_bootargs_shared_size(0x1400000ULL, 0) == 0xec00000ULL);
	CHECK(isp_bootargs_shared_size(0x1400000ULL, ISP_T8140_SHARED_SIZE) ==
	      ISP_T8140_SHARED_SIZE);
	CHECK(ISP_T8140_SHARED_SIZE == 0xee000000ULL);
	CHECK(isp_bootargs_shared_size(0x1400000ULL, 0) != 0xee000000ULL);
	CHECK(isp_bootargs_shared_size(0x1400000ULL, 0) != 0xde600000ULL);
	CHECK(isp_bootargs_shared_size(ISP_SHARED_APERTURE, 0) == 0);
}

static void test_t8140_route_preserved(void)
{
	CHECK(ISP_MBOX_IRQ_ROUTE_T8140 == 0x73c);
	CHECK(ISP_MBOX_IRQ_ENABLE2_T8140_VAL == 0x700000u);
	CHECK(ISP_MBOX_IRQ_ENABLE1_VAL == 0x70000000u);
	CHECK(ISP_MBOX_IRQ_ROUTE_VAL == 0x8u);
	CHECK(ISP_MBOX2_OFFSET_T8140 == 0x410u);
	CHECK(ISP_MBOX_IRQ_ROUTE_T8132 == 0x95c);
	CHECK(ISP_MBOX_IRQ_ENABLE2_T8132_VAL == 0x02c00000u);
	CHECK(ISP_MBOX2_OFFSET_T8132 == 0x464u);
	CHECK(ISP_MBOX_IRQ_ROUTE_T8132 != ISP_MBOX_IRQ_ROUTE_T8140);
	CHECK(ISP_MBOX_IRQ_ENABLE2_T8132_VAL != ISP_MBOX_IRQ_ENABLE2_T8140_VAL);
	CHECK(ISP_MBOX2_OFFSET_T8132 != ISP_MBOX2_OFFSET_T8140);
}

static void test_coproc_disable_constants(void)
{
	CHECK(ISP_COPROC_CONTROL_T8132 == 0x1600044u);
	CHECK(ISP_COPROC_DISABLE_T8132 == 0x1600040u);
	CHECK(ISP_COPROC_DISABLE_VAL_T8132 == 0x2000000u);
	CHECK(ISP_COPROC_DISABLE_ACK_BIT_T8132 == 25);
	CHECK(ISP_COPROC_DISABLE_T8132 != ISP_COPROC_CONTROL_T8132);
	CHECK((1u << ISP_COPROC_DISABLE_ACK_BIT_T8132) ==
	      ISP_COPROC_DISABLE_VAL_T8132);
}

static void test_fw_mmio_dapf_windows(void)
{
	static const struct {
		u64 start;
		u64 end_incl;
		u64 base;
		u64 size;
	} want[] = {
		{ 0x380704000ULL, 0x380704063ULL, 0x380704000ULL, 0x4000 },
		{ 0x380730000ULL, 0x380733fffULL, 0x380730000ULL, 0x4000 },
		{ 0x380e3c000ULL, 0x380e3c003ULL, 0x380e3c000ULL, 0x4000 },
		{ 0x39062c000ULL, 0x390648003ULL, 0x39062c000ULL, 0x20000 },
		{ 0x501660000ULL, 0x501660003ULL, 0x501660000ULL, 0x4000 },
		{ 0x220004000ULL, 0x2200162fcULL, 0x220004000ULL, 0x14000 },
		{ 0x220044000ULL, 0x2200562fcULL, 0x220044000ULL, 0x14000 },
		{ 0x2201c4000ULL, 0x2201d62fcULL, 0x2201c4000ULL, 0x14000 },
		{ 0x222004000ULL, 0x2220162fcULL, 0x222004000ULL, 0x14000 },
		{ 0x222044000ULL, 0x2220562fcULL, 0x222044000ULL, 0x14000 },
		{ 0x2221c4000ULL, 0x2221d62fcULL, 0x2221c4000ULL, 0x14000 },
		{ 0x3803c0000ULL, 0x3803e4000ULL, 0x3803c0000ULL, 0x28000 },
		{ 0x382824000ULL, 0x382824004ULL, 0x382824000ULL, 0x4000 },
	};
	unsigned int i;

	CHECK(sizeof(want) / sizeof(want[0]) == 13);
	for (i = 0; i < sizeof(want) / sizeof(want[0]); i++) {
		u64 base = ~(u64)0;
		u64 size = ~(u64)0;
		int rc;

		rc = isp_fw_mmio_window(want[i].start, want[i].end_incl,
					&base, &size);
		CHECK(rc == 0);
		if (rc != 0)
			continue;
		CHECK(base == want[i].base);
		CHECK(size == want[i].size);
	}
}

int main(void)
{
	test_dsid_count1_size();
	test_dsid_count2_size();
	test_dsid_bounds();
	test_bootargs_shared_size();
	test_t8140_route_preserved();
	test_coproc_disable_constants();
	test_fw_mmio_dapf_windows();
	if (failures) {
		fprintf(stderr, "%d checks failed\n", failures);
		return 1;
	}
	puts("isp-m4-layout-test: all checks passed");
	return 0;
}
