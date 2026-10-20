// SPDX-License-Identifier: GPL-2.0-only
/*
 * KUnit tests for the videobuf2 queue of the Apple ISP driver, built into
 * isp-v4l2.c. They check the queue parameters vb2 accepts and the buffer
 * limit queue_setup() enforces, without any hardware.
 */

#include <kunit/device.h>
#include <kunit/test.h>
#include <linux/bitmap.h>

#define ISP_TEST_FRAME_SIZE 0x1000

static struct apple_isp *isp_test_alloc(struct kunit *test)
{
	struct apple_isp *isp;

	isp = kunit_kzalloc(test, sizeof(*isp), GFP_KERNEL);
	KUNIT_ASSERT_NOT_ERR_OR_NULL(test, isp);

	mutex_init(&isp->video_lock);
	isp_get_current_format(isp)->total_size = ISP_TEST_FRAME_SIZE;

	return isp;
}

/* The queue must initialise with the parameters the driver probes with. */
static void isp_test_queue_init(struct kunit *test)
{
	struct apple_isp *isp = isp_test_alloc(test);

	isp->dev = kunit_device_register(test, "apple-isp-test");
	KUNIT_ASSERT_NOT_ERR_OR_NULL(test, isp->dev);

	KUNIT_ASSERT_EQ(test, isp_vb2_queue_init(isp), 0);
	KUNIT_EXPECT_GE(test, isp->vbq.max_num_buffers, VB2_MAX_FRAME);
	vb2_queue_release(&isp->vbq);
}

/* Pretend the queue already has @num buffers, as vb2_get_num_buffers() sees. */
static void isp_test_set_num_buffers(struct kunit *test, struct vb2_queue *q,
				     unsigned int num)
{
	q->max_num_buffers = VB2_MAX_FRAME;
	q->bufs_bitmap = kunit_kzalloc(test,
				       BITS_TO_LONGS(VB2_MAX_FRAME) *
					       sizeof(unsigned long),
				       GFP_KERNEL);
	KUNIT_ASSERT_NOT_NULL(test, q->bufs_bitmap);
	bitmap_set(q->bufs_bitmap, 0, num);
}

static int isp_test_setup(struct kunit *test, unsigned int existing,
			  unsigned int *nbuffers, unsigned int *num_planes,
			  unsigned int sizes[])
{
	struct apple_isp *isp = isp_test_alloc(test);

	isp->vbq.drv_priv = isp;
	isp_test_set_num_buffers(test, &isp->vbq, existing);

	return isp_vb2_queue_setup(&isp->vbq, nbuffers, num_planes, sizes,
				   NULL);
}

/* REQBUFS: an empty queue gets at most one pool of buffers. */
static void isp_test_reqbufs_limit(struct kunit *test)
{
	unsigned int sizes[VB2_MAX_PLANES] = {};
	unsigned int nbuffers = VB2_MAX_FRAME, num_planes = 0;

	KUNIT_ASSERT_EQ(test, isp_test_setup(test, 0, &nbuffers, &num_planes,
					     sizes), 0);
	KUNIT_EXPECT_EQ(test, nbuffers, ISP_MAX_BUFFERS);
	KUNIT_EXPECT_EQ(test, num_planes, 1);
	KUNIT_EXPECT_EQ(test, sizes[0], ISP_TEST_FRAME_SIZE);

	nbuffers = 4;
	num_planes = 0;
	KUNIT_ASSERT_EQ(test, isp_test_setup(test, 0, &nbuffers, &num_planes,
					     sizes), 0);
	KUNIT_EXPECT_EQ(test, nbuffers, 4);
}

/* CREATE_BUFS: the buffers the queue has count against the pool. */
static void isp_test_create_bufs_limit(struct kunit *test)
{
	unsigned int sizes[VB2_MAX_PLANES] = { ISP_TEST_FRAME_SIZE };
	unsigned int nbuffers, num_planes;

	nbuffers = 10;
	num_planes = 1;
	KUNIT_ASSERT_EQ(test, isp_test_setup(test, 10, &nbuffers, &num_planes,
					     sizes), 0);
	KUNIT_EXPECT_EQ(test, nbuffers, ISP_MAX_BUFFERS - 10);

	nbuffers = 1;
	num_planes = 1;
	KUNIT_EXPECT_EQ(test, isp_test_setup(test, ISP_MAX_BUFFERS, &nbuffers,
					     &num_planes, sizes), -ENOBUFS);

	/* A plane smaller than a frame is still refused. */
	nbuffers = 1;
	num_planes = 1;
	sizes[0] = ISP_TEST_FRAME_SIZE - 1;
	KUNIT_EXPECT_EQ(test, isp_test_setup(test, 0, &nbuffers, &num_planes,
					     sizes), -EINVAL);
}

static struct kunit_case isp_v4l2_test_cases[] = {
	KUNIT_CASE(isp_test_queue_init),
	KUNIT_CASE(isp_test_reqbufs_limit),
	KUNIT_CASE(isp_test_create_bufs_limit),
	{}
};

static struct kunit_suite isp_v4l2_test_suite = {
	.name = "apple-isp-v4l2",
	.test_cases = isp_v4l2_test_cases,
};

kunit_test_suite(isp_v4l2_test_suite);
