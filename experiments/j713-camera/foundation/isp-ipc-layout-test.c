// SPDX-License-Identifier: GPL-2.0-only
#include <kunit/test.h>
#include <linux/module.h>

#include "isp-ipc-layout.h"

static void isp_ipc_high_address_test(struct kunit *test)
{
	struct isp_ipc_extent ipc = { 0x10002000000ULL, 0x1c000 };
	struct isp_ipc_extent table = { ipc.iova + 0x1000, 0x700 };
	struct isp_ipc_extent ring = { ipc.iova + 0x4000, 0x400 };

	KUNIT_EXPECT_TRUE(test, isp_ipc_ring_valid(ipc, ring, &table, 1));
	/* Truncating the ring to 32 bits must not identify owned memory. */
	ring.iova = (u32)ring.iova;
	KUNIT_EXPECT_FALSE(test, isp_ipc_ring_valid(ipc, ring, &table, 1));
}

static void isp_ipc_extent_test(struct kunit *test)
{
	struct isp_ipc_extent ipc = { 0x10000, 0x4000 };
	struct isp_ipc_extent ring = { 0x13fc0, 0x40 };

	KUNIT_EXPECT_TRUE(test, isp_ipc_ring_valid(ipc, ring, NULL, 0));
	ring.size += 0x40;
	KUNIT_EXPECT_FALSE(test, isp_ipc_ring_valid(ipc, ring, NULL, 0));
	ring = (struct isp_ipc_extent){ 0xffc0, 0x40 };
	KUNIT_EXPECT_FALSE(test, isp_ipc_ring_valid(ipc, ring, NULL, 0));
	ring = (struct isp_ipc_extent){ 0x10000, 0 };
	KUNIT_EXPECT_FALSE(test, isp_ipc_ring_valid(ipc, ring, NULL, 0));
}

static void isp_ipc_alignment_test(struct kunit *test)
{
	struct isp_ipc_extent ipc = { 0x10000, 0x4000 };
	struct isp_ipc_extent ring = { 0x10001, 0x40 };

	KUNIT_EXPECT_FALSE(test, isp_ipc_ring_valid(ipc, ring, NULL, 0));
	ring = (struct isp_ipc_extent){ 0x10000, 0x41 };
	KUNIT_EXPECT_FALSE(test, isp_ipc_ring_valid(ipc, ring, NULL, 0));
	/* Only the u64 publication alignment is required for older firmware. */
	ring = (struct isp_ipc_extent){ 0x10008, 0x40 };
	KUNIT_EXPECT_TRUE(test, isp_ipc_ring_valid(ipc, ring, NULL, 0));
}

static void isp_ipc_reserved_test(struct kunit *test)
{
	struct isp_ipc_extent ipc = { 0x10000, 0x4000 };
	struct isp_ipc_extent reserved[] = { { 0x10100, 0x700 },
					   { 0x11000, 0x400 } };
	struct isp_ipc_extent ring = { 0x100c0, 0x80 };

	KUNIT_EXPECT_FALSE(test, isp_ipc_ring_valid(ipc, ring, reserved, 2));
	ring = (struct isp_ipc_extent){ 0x10800, 0x40 };
	KUNIT_EXPECT_TRUE(test, isp_ipc_ring_valid(ipc, ring, reserved, 2));
	ring = (struct isp_ipc_extent){ 0x10fc0, 0x40 };
	KUNIT_EXPECT_TRUE(test, isp_ipc_ring_valid(ipc, ring, reserved, 2));
	ring = reserved[1];
	KUNIT_EXPECT_FALSE(test, isp_ipc_ring_valid(ipc, ring, reserved, 2));
	ring = (struct isp_ipc_extent){ 0x11100, 0x40 };
	KUNIT_EXPECT_FALSE(test, isp_ipc_ring_valid(ipc, ring, reserved, 2));
	ring = (struct isp_ipc_extent){ 0x10fc0, 0x80 };
	KUNIT_EXPECT_FALSE(test, isp_ipc_ring_valid(ipc, ring, reserved, 2));
}

static void isp_ipc_overflow_test(struct kunit *test)
{
	struct isp_ipc_extent ipc = { (u64)-1 - 0x3fff, 0x4000 };
	struct isp_ipc_extent ring = { ipc.iova, 0x40 };

	KUNIT_EXPECT_FALSE(test, isp_ipc_ring_valid(ipc, ring, NULL, 0));
	ipc = (struct isp_ipc_extent){ 0, (u64)-1 };
	ring = (struct isp_ipc_extent){ (u64)-1 - 7, 0x40 };
	KUNIT_EXPECT_FALSE(test, isp_ipc_ring_valid(ipc, ring, NULL, 0));
}

static void isp_ipc_captured_layout_test(struct kunit *test)
{
	/* J616s 25G76 channel rings occupy addresses above 1 TB. */
	struct isp_ipc_extent ipc = { 0x10001ff0000ULL, 0x1c000 };
	struct isp_ipc_extent reserved[9] = {
		{ 0x10001ff0000ULL, 0x700 },
		{ 0x10001ffef80ULL, 0x290 + 0x40 + 0x400 },
		{ 0x10001ff0700ULL, 768 * 64 },
		{ 0x10001ffc700ULL, 8 * 64 },
		{ 0x10001ffc900ULL, 8 * 64 },
		{ 0x10001ffcb00ULL, 64 * 64 },
		{ 0x10001ffdb00ULL, 64 * 64 },
		{ 0x10001ffeb00ULL, 8 * 64 },
		{ 0x10001ffed00ULL, 8 * 64 },
	};

	for (unsigned int i = 2; i < ARRAY_SIZE(reserved); i++)
		KUNIT_EXPECT_TRUE(test,
			isp_ipc_ring_valid(ipc, reserved[i], reserved, i));

	/* A ring over the command storage must not be admitted. */
	reserved[8] = (struct isp_ipc_extent){ 0x10001fff280ULL, 0x40 };
	KUNIT_EXPECT_FALSE(test,
		isp_ipc_ring_valid(ipc, reserved[8], reserved, 8));
}

static struct kunit_case isp_ipc_layout_cases[] = {
	KUNIT_CASE(isp_ipc_high_address_test),
	KUNIT_CASE(isp_ipc_extent_test),
	KUNIT_CASE(isp_ipc_alignment_test),
	KUNIT_CASE(isp_ipc_reserved_test),
	KUNIT_CASE(isp_ipc_overflow_test),
	KUNIT_CASE(isp_ipc_captured_layout_test),
	{}
};

static struct kunit_suite isp_ipc_layout_suite = {
	.name = "apple-isp-ipc-layout",
	.test_cases = isp_ipc_layout_cases,
};
kunit_test_suite(isp_ipc_layout_suite);

MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Apple ISP IPC layout KUnit tests");
