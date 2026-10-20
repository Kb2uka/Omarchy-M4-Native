// SPDX-License-Identifier: GPL-2.0-only
/*
 * KUnit tests for the SHAREDMALLOC free requests of the Apple ISP driver,
 * built into isp-ipc.c. They check which surface a free request releases,
 * without any hardware.
 */

#include <kunit/device.h>
#include <kunit/test.h>

#define ISP_TEST_IOVA_MASK	0xffffffffULL
#define ISP_TEST_IOVA_ALIAS	0x100000000ULL

struct isp_sm_test {
	struct apple_isp *isp;
	struct isp_channel *chan;
	struct isp_surf *surfs[3];
};

static struct isp_sm_test *isp_sm_test_alloc(struct kunit *test)
{
	struct isp_sm_test *t;
	struct apple_isp *isp;

	t = kunit_kzalloc(test, sizeof(*t), GFP_KERNEL);
	KUNIT_ASSERT_NOT_NULL(test, t);

	isp = kunit_kzalloc(test, sizeof(*isp), GFP_KERNEL);
	KUNIT_ASSERT_NOT_NULL(test, isp);
	isp->dev = kunit_device_register(test, "apple-isp-sm-test");
	KUNIT_ASSERT_NOT_ERR_OR_NULL(test, isp->dev);
	isp->fw_iova_mask = ISP_TEST_IOVA_MASK;
	INIT_LIST_HEAD(&isp->gc);
	INIT_LIST_HEAD(&isp->gc_released);
	mutex_init(&isp->gc_lock);
	t->isp = isp;

	t->chan = kunit_kzalloc(test, sizeof(*t->chan), GFP_KERNEL);
	KUNIT_ASSERT_NOT_NULL(test, t->chan);

	/* Surfaces the firmware allocated, as the malloc request leaves them */
	for (int i = 0; i < ARRAY_SIZE(t->surfs); i++) {
		struct isp_surf *surf;

		surf = kunit_kzalloc(test, sizeof(*surf), GFP_KERNEL);
		KUNIT_ASSERT_NOT_NULL(test, surf);
		surf->iova = 0x5000000 + i * 0x484000;
		surf->size = 0x484000;
		surf->gc = true;
		list_add_tail(&surf->head, &isp->gc);
		t->surfs[i] = surf;
	}

	return t;
}

static void isp_sm_test_free(struct kunit *test, struct isp_sm_test *t,
			     u64 arg0)
{
	struct isp_message *req = &t->chan->req, *rsp = &t->chan->rsp;

	memset(req, 0, sizeof(*req));
	memset(rsp, 0, sizeof(*rsp));
	req->arg0 = arg0;

	KUNIT_ASSERT_EQ(test, ipc_sm_handle(t->isp, t->chan), 0);

	/* Every free request is acknowledged with its own iova. */
	KUNIT_EXPECT_EQ(test, rsp->arg0, arg0 | ISP_IPC_FLAG_ACK);
	KUNIT_EXPECT_EQ(test, rsp->arg1, 0);
	KUNIT_EXPECT_EQ(test, rsp->arg2, 0);
}

static bool isp_sm_test_on(struct list_head *list, struct isp_surf *surf)
{
	struct isp_surf *pos;

	list_for_each_entry(pos, list, head)
		if (pos == surf)
			return true;

	return false;
}

/* A free request releases the surface with that iova, and only that one. */
static void isp_test_sm_free_releases(struct kunit *test)
{
	struct isp_sm_test *t = isp_sm_test_alloc(test);

	isp_sm_test_free(test, t, t->surfs[1]->iova);

	KUNIT_EXPECT_TRUE(test, isp_sm_test_on(&t->isp->gc_released,
					       t->surfs[1]));
	KUNIT_EXPECT_FALSE(test, isp_sm_test_on(&t->isp->gc, t->surfs[1]));
	KUNIT_EXPECT_TRUE(test, isp_sm_test_on(&t->isp->gc, t->surfs[0]));
	KUNIT_EXPECT_TRUE(test, isp_sm_test_on(&t->isp->gc, t->surfs[2]));
	KUNIT_EXPECT_EQ(test, list_count_nodes(&t->isp->gc_released), 1);
}

/* Bits above the translated ones (the T8140 vm-base) do not matter. */
static void isp_test_sm_free_alias(struct kunit *test)
{
	struct isp_sm_test *t = isp_sm_test_alloc(test);

	isp_sm_test_free(test, t, t->surfs[2]->iova | ISP_TEST_IOVA_ALIAS);

	KUNIT_EXPECT_TRUE(test, isp_sm_test_on(&t->isp->gc_released,
					       t->surfs[2]));
	KUNIT_EXPECT_EQ(test, list_count_nodes(&t->isp->gc), 2);
}

/* An unknown iova, or a second free of one surface, releases nothing. */
static void isp_test_sm_free_unknown(struct kunit *test)
{
	struct isp_sm_test *t = isp_sm_test_alloc(test);

	isp_sm_test_free(test, t, t->surfs[0]->iova + 0x4000);
	KUNIT_EXPECT_EQ(test, list_count_nodes(&t->isp->gc), 3);
	KUNIT_EXPECT_TRUE(test, list_empty(&t->isp->gc_released));

	isp_sm_test_free(test, t, t->surfs[0]->iova);
	isp_sm_test_free(test, t, t->surfs[0]->iova);
	KUNIT_EXPECT_EQ(test, list_count_nodes(&t->isp->gc), 2);
	KUNIT_EXPECT_EQ(test, list_count_nodes(&t->isp->gc_released), 1);
}

/* The log and buffer return surfaces are no longer used once released. */
static void isp_test_sm_free_log_bt(struct kunit *test)
{
	struct isp_sm_test *t = isp_sm_test_alloc(test);

	t->isp->log_surf = t->surfs[0];
	t->isp->bt_surf = t->surfs[1];

	isp_sm_test_free(test, t, t->surfs[2]->iova);
	KUNIT_EXPECT_PTR_EQ(test, t->isp->log_surf, t->surfs[0]);
	KUNIT_EXPECT_PTR_EQ(test, t->isp->bt_surf, t->surfs[1]);

	isp_sm_test_free(test, t, t->surfs[0]->iova);
	KUNIT_EXPECT_NULL(test, t->isp->log_surf);
	KUNIT_EXPECT_PTR_EQ(test, t->isp->bt_surf, t->surfs[1]);

	isp_sm_test_free(test, t, t->surfs[1]->iova);
	KUNIT_EXPECT_NULL(test, t->isp->bt_surf);
}

static struct kunit_case isp_ipc_test_cases[] = {
	KUNIT_CASE(isp_test_sm_free_releases),
	KUNIT_CASE(isp_test_sm_free_alias),
	KUNIT_CASE(isp_test_sm_free_unknown),
	KUNIT_CASE(isp_test_sm_free_log_bt),
	{}
};

static struct kunit_suite isp_ipc_test_suite = {
	.name = "apple-isp-sharedmalloc",
	.test_cases = isp_ipc_test_cases,
};

kunit_test_suite(isp_ipc_test_suite);
