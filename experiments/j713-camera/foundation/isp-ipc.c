// SPDX-License-Identifier: GPL-2.0-only
/* Copyright 2023 Eileen Yoon <eyn@gmx.com> */

#include "isp-iommu.h"
#include "isp-ipc.h"
#include "isp-regs.h"
#include "isp-fw.h"

#define ISP_IPC_FLAG_TERMINAL_ACK	0x3
/* longer firmware log lines are cut */
#define ISP_IPC_TERMINAL_MAX_LEN	512
#define ISP_IPC_BUFEXC_STAT_META_OFFSET 0x10

struct isp_sm_deferred_work {
	struct work_struct work;
	struct apple_isp *isp;
	struct isp_surf *surf;
};

struct isp_bufexc_stat {
	u64 unk_0; // 2
	u64 unk_8; // 2

	u64 meta_iova;
	u64 pad_20[3];
	u64 meta_size; // 0x4640
	u64 unk_38;

	u32 unk_40; // 1
	u32 unk_44;
	u64 unk_48;

	u64 iova0;
	u64 iova1;
	u64 iova2;
	u64 iova3;
	u32 pad_70[4];

	u32 unk_80; // 2
	u32 unk_84; // 1
	u32 unk_88; // 0x10 || 0x13
	u32 unk_8c;
	u32 pad_90[96];

	u32 unk_210; // 0x28
	u32 unk_214;
	u32 index;
	u16 bes_width; // 1296, 0x510
	u16 bes_height; // 736, 0x2e0

	u32 unk_220; // 0x0 || 0x1
	u32 pad_224[3];
	u32 unk_230; // 0xf7ed38
	u32 unk_234; // 3
	u32 pad_238[2];
	u32 pad_240[16];
} __packed;
static_assert(sizeof(struct isp_bufexc_stat) == ISP_IPC_BUFEXC_STAT_SIZE);

static inline void *chan_msg_virt(struct isp_channel *chan, u32 index)
{
	return chan->virt + (index * ISP_IPC_MESSAGE_SIZE);
}

static inline void chan_read_msg_index(struct apple_isp *isp,
				       struct isp_channel *chan,
				       struct isp_message *msg, u32 index)
{
	memcpy(msg, chan_msg_virt(chan, index), sizeof(*msg));
}

static inline void chan_read_msg(struct apple_isp *isp,
				 struct isp_channel *chan,
				 struct isp_message *msg)
{
	chan_read_msg_index(isp, chan, msg, chan->cursor);
}

static inline void chan_write_msg_index(struct apple_isp *isp,
					struct isp_channel *chan,
					struct isp_message *msg, u32 index)
{
	u64 *p0 = chan_msg_virt(chan, index);
	memcpy(p0 + 1, &msg->arg1, sizeof(*msg) - 8);

	/* Make sure we write arg0 last, since that indicates message validity. */

	dma_wmb();
	*p0 = msg->arg0;
	dma_wmb();
}

static inline void chan_write_msg(struct apple_isp *isp,
				  struct isp_channel *chan,
				  struct isp_message *msg)
{
	chan_write_msg_index(isp, chan, msg, chan->cursor);
}

static inline void chan_update_cursor(struct isp_channel *chan)
{
	if (chan->cursor >= (chan->num - 1)) {
		chan->cursor = 0;
	} else {
		chan->cursor += 1;
	}
}

static int chan_handle_once(struct apple_isp *isp, struct isp_channel *chan)
{
	int err;

	lockdep_assert_held(&chan->lock);

	err = chan->ops->handle(isp, chan);
	if (err < 0) {
		dev_err_ratelimited(isp->dev, "%s: handler failed: %d\n",
				    chan->name, err);
		return err;
	}

	chan_write_msg(isp, chan, &chan->rsp);

	isp_mbox2_write32(isp, ISP_MBOX2_IRQ_DOORBELL, chan->doorbell);

	chan_update_cursor(chan);

	return 0;
}

static inline bool chan_rx_done(struct apple_isp *isp, struct isp_channel *chan)
{
	if (((chan->req.arg0 & 0xf) == ISP_IPC_FLAG_ACK) ||
	    ((chan->req.arg0 & 0xf) == ISP_IPC_FLAG_TERMINAL_ACK)) {
		return true;
	}
	return false;
}

int ipc_chan_handle(struct apple_isp *isp, struct isp_channel *chan)
{
	int err = 0;

	mutex_lock(&chan->lock);
	while (1) {
		chan_read_msg(isp, chan, &chan->req);
		if (chan_rx_done(isp, chan)) {
			err = 0;
			break;
		}
		err = chan_handle_once(isp, chan);
		if (err < 0) {
			break;
		}
	}
	mutex_unlock(&chan->lock);

	return err;
}

static inline bool chan_tx_done(struct apple_isp *isp, struct isp_channel *chan)
{
	dma_rmb();

	chan_read_msg(isp, chan, &chan->rsp);
	if (isp_fw_iova(isp, chan->rsp.arg0) ==
	    isp_fw_iova(isp, chan->req.arg0 | ISP_IPC_FLAG_ACK)) {
		chan_update_cursor(chan);
		return true;
	}
	return false;
}

int ipc_chan_send(struct apple_isp *isp, struct isp_channel *chan,
		  unsigned long timeout)
{
	long t;

	chan_write_msg(isp, chan, &chan->req);
	dma_wmb();

	isp_mbox2_write32(isp, ISP_MBOX2_IRQ_DOORBELL, chan->doorbell);

	if (!timeout)
		return 0;

	t = wait_event_timeout(isp->wait, chan_tx_done(isp, chan), timeout);
	if (t == 0) {
		dev_err(isp->dev,
			"%s: timed out on request [0x%llx, 0x%llx, 0x%llx]\n",
			chan->name, chan->req.arg0, chan->req.arg1,
			chan->req.arg2);
		return -ETIME;
	}

	isp_dbg(isp, "%s: request success (%ld)\n", chan->name, t);

	return 0;
}

int ipc_tm_handle(struct apple_isp *isp, struct isp_channel *chan)
{
	struct isp_message *req = &chan->req, *rsp = &chan->rsp;
	dma_addr_t iova =
		isp_fw_iova(isp, req->arg0 & ~ISP_IPC_FLAG_TERMINAL_ACK);
	size_t size = min_t(u64, req->arg1, ISP_IPC_TERMINAL_MAX_LEN);
	const char *line;

	/* Firmware log output, available through dynamic debug */
	if (iova && size && isp->log_surf) {
		line = apple_isp_translate(isp, isp->log_surf, iova, size);
		if (line)
			dev_dbg(isp->dev, "ISPASC: %.*s", (int)size, line);
	}

	rsp->arg0 = ISP_IPC_FLAG_ACK;
	rsp->arg1 = 0x0;
	rsp->arg2 = 0x0;

	return 0;
}

/*
 * The firmware frees a SHAREDMALLOC surface by sending its iova. It does not
 * free every surface it allocated, and may still access a surface after
 * freeing it, so the surface is only moved to the released list here; it is
 * freed once the stream has stopped, see isp_free_released_surfaces(), and
 * what the firmware never frees is freed when the firmware stops.
 */
static void ipc_sm_release(struct apple_isp *isp, dma_addr_t iova)
{
	struct isp_surf *surf, *found = NULL;

	mutex_lock(&isp->gc_lock);
	list_for_each_entry(surf, &isp->gc, head) {
		if (surf->iova == iova) {
			found = surf;
			list_move_tail(&surf->head, &isp->gc_released);
			break;
		}
	}
	mutex_unlock(&isp->gc_lock);

	if (!found) {
		dev_warn_ratelimited(isp->dev,
				     "firmware freed unknown shared surface %pad\n",
				     &iova);
		return;
	}

	/* The terminal and buffer return handlers run in this thread. */
	if (found == isp->log_surf)
		isp->log_surf = NULL;
	if (found == isp->bt_surf)
		isp->bt_surf = NULL;

	isp_dbg(isp, "shared surface released: iova %pad size 0x%llx\n",
		&found->iova, found->size);
}

int ipc_sm_handle(struct apple_isp *isp, struct isp_channel *chan)
{
	struct isp_message *req = &chan->req, *rsp = &chan->rsp;
	int err;

	if (req->arg0 == 0x0) {
		struct isp_surf *surf;

		surf = isp_alloc_surface_gc(isp, req->arg1);
		if (!surf) {
			dev_err_ratelimited(isp->dev,
					    "failed to alloc requested size 0x%llx\n",
					    req->arg1);
			return -ENOMEM;
		}
		surf->type = req->arg2;

		rsp->arg0 = surf->iova | ISP_IPC_FLAG_ACK;
		rsp->arg1 = 0x0;
		rsp->arg2 = 0x0; /* macOS uses this to index surfaces */

		switch (surf->type) {
		case 0x4c4f47: /* "LOG" */
			isp->log_surf = surf;
			break;
		case 0x4d495343: /* "MISC" */
			/* Hacky... maybe there's a better way to identify this surface? */
			if (surf->size == 0xc000)
				isp->bt_surf = surf;
			break;
		default:
			// skip vmap
			return 0;
		}

		err = isp_surf_vmap(isp, surf);
		if (err < 0) {
			isp_err(isp, "failed to vmap iova=0x%llx size=0x%llx\n",
				surf->iova, surf->size);
		}
	} else {
		ipc_sm_release(isp, isp_fw_iova(isp, req->arg0));

		rsp->arg0 = req->arg0 | ISP_IPC_FLAG_ACK;
		rsp->arg1 = 0x0;
		rsp->arg2 = 0x0;
	}

	return 0;
}

#if IS_ENABLED(CONFIG_VIDEO_APPLE_ISP_KUNIT_TEST)
#include "isp-ipc-test.c"
#endif
