// SPDX-License-Identifier: GPL-2.0-only OR MIT
/* Copyright 2022 Sven Peter <sven@svenpeter.dev> */
/* Modified 2026-10-18: native J713 brightness-only transport, variable ring
 * metadata and alignment, byte transaction tags, and consume-before-release.
 * See NOTICE and LICENSES for source provenance and license terms.
 */
#include <linux/bitfield.h>
#include <linux/bitmap.h>
#include <linux/completion.h>
#include <linux/dma-mapping.h>
#include <linux/slab.h>
#include <linux/spinlock.h>
#include <linux/workqueue.h>
#include <linux/soc/apple/rtkit.h>
#include <linux/unaligned.h>

#include "afk.h"
#include "native-wire.h"

/* The client mutex serializes every firmware command. */
#define MAX_PENDING_CMDS 1
#define PANEL_CHANNEL 3

struct epic_cmd_info {
	u8 tag;
	void *rxbuf;
	void *txbuf;
	dma_addr_t rxbuf_dma;
	dma_addr_t txbuf_dma;
	size_t rxlen;
	size_t txlen;
	u32 retcode;
	bool done;
	bool free_on_ack;
	struct completion *completion;
};

struct afk_service {
	struct epic_cmd_info cmds[MAX_PENDING_CMDS];
	DECLARE_BITMAP(cmd_map, MAX_PENDING_CMDS);
	u8 cmd_tag;
	spinlock_t lock;
	bool enabled;
	bool torndown;
	bool native_opened;
};

struct afk_ringbuffer {
	bool ready;
	__le32 *shared_rptr;
	__le32 *shared_wptr;
	u32 block_size;
	void *buf;
	size_t bufsz;
};

struct afk_endpoint {
	struct device *dev;
	struct apple_rtkit *rtk;
	u8 endpoint;
	void *cookie;
	void (*ready)(void *cookie);
	void (*failed)(void *cookie, int error);
	bool send_failed;
	struct workqueue_struct *wq;
	struct completion started;
	void *bfr;
	u16 bfr_tag;
	size_t bfr_size;
	dma_addr_t bfr_dma;
	struct afk_ringbuffer txbfr;
	struct afk_ringbuffer rxbfr;
	spinlock_t lock;
	u8 qe_seq;
	struct afk_service service;
};

struct afk_qe {
#define QE_MAGIC 0x20504f49 /* ' POI' */
	__le32 magic;
	__le32 size;
	__le32 channel;
	__le32 type;
	u8 data[];
};

struct afk_receive_message_work {
	struct afk_endpoint *ep;
	u64 message;
	struct work_struct work;
};

#define RBEP_TYPE GENMASK(63, 48)

enum rbep_msg_type {
	RBEP_INIT = 0x80,
	RBEP_INIT_ACK = 0xa0,
	RBEP_GETBUF = 0x89,
	RBEP_GETBUF_ACK = 0xa1,
	RBEP_INIT_TX = 0x8a,
	RBEP_INIT_RX = 0x8b,
	RBEP_START = 0xa3,
	RBEP_START_ACK = 0x86,
	RBEP_SEND = 0xa2,
	RBEP_RECV = 0x85,
};

#define BLOCK_SHIFT 6

#define GETBUF_SIZE GENMASK(31, 16)
#define GETBUF_TAG GENMASK(15, 0)
#define GETBUF_ACK_DVA GENMASK(47, 0)

#define INITRB_OFFSET GENMASK(47, 32)
#define INITRB_SIZE GENMASK(31, 16)
#define INITRB_TAG GENMASK(15, 0)

#define SEND_WPTR GENMASK(31, 0)

static void afk_send(struct afk_endpoint *ep, u64 message)
{
	int ret = apple_rtkit_send_message(ep->rtk, ep->endpoint, message, NULL, false);

	if (ret) {
		WRITE_ONCE(ep->send_failed, true);
		dev_err(ep->dev, "AFK message send failed: %d\n", ret);
		ep->failed(ep->cookie, ret);
	}
}

static void afk_destroy_workqueue(void *data)
{
	struct afk_endpoint *ep = data;

	destroy_workqueue(ep->wq);
}

struct afk_endpoint *afk_init(struct device *dev, struct apple_rtkit *rtk,
			    void *cookie, void (*ready)(void *cookie),
			    void (*failed)(void *cookie, int error))
{
	struct afk_endpoint *ep;
	int ret;

	ep = devm_kzalloc(dev, sizeof(*ep), GFP_KERNEL);
	if (!ep)
		return ERR_PTR(-ENOMEM);
	ep->dev = dev;
	ep->rtk = rtk;
	ep->endpoint = J713_AFK_ENDPOINT;
	ep->cookie = cookie;
	ep->ready = ready;
	ep->failed = failed;
	ep->wq = alloc_ordered_workqueue("j713-dcp-afk", WQ_MEM_RECLAIM);
	if (!ep->wq)
		return ERR_PTR(-ENOMEM);
	ret = devm_add_action_or_reset(dev, afk_destroy_workqueue, ep);
	if (ret)
		return ERR_PTR(ret);
	init_completion(&ep->started);
	spin_lock_init(&ep->lock);
	spin_lock_init(&ep->service.lock);
	return ep;
}

int afk_start(struct afk_endpoint *ep)
{
	int ret = apple_rtkit_start_ep(ep->rtk, ep->endpoint);

	if (ret)
		return ret;
	afk_send(ep, FIELD_PREP(RBEP_TYPE, RBEP_INIT));
	if (!wait_for_completion_timeout(&ep->started, msecs_to_jiffies(1000)))
		return -ETIMEDOUT;
	return 0;
}

static void afk_getbuf(struct afk_endpoint *ep, u64 message)
{
	u32 size = FIELD_GET(GETBUF_SIZE, message) << BLOCK_SHIFT;
	u16 tag = FIELD_GET(GETBUF_TAG, message);
	u64 reply;


	if (!size)
		return;

	if (ep->bfr) {
		dev_err(ep->dev,
			"Got GETBUF message but buffer already exists\n");
		return;
	}

	ep->bfr = dmam_alloc_coherent(ep->dev, size, &ep->bfr_dma,
				      GFP_KERNEL);
	if (!ep->bfr) {
		dev_err(ep->dev, "Failed to allocate %d bytes buffer\n",
			size);
		return;
	}

	ep->bfr_size = size;
	ep->bfr_tag = tag;

	reply = FIELD_PREP(RBEP_TYPE, RBEP_GETBUF_ACK);
	reply |= FIELD_PREP(GETBUF_ACK_DVA, ep->bfr_dma);
	afk_send(ep, reply);
}

static void afk_init_rxtx(struct afk_endpoint *ep, u64 message,
			  struct afk_ringbuffer *bfr)
{
	u32 base = FIELD_GET(INITRB_OFFSET, message) << BLOCK_SHIFT;
	u32 size = FIELD_GET(INITRB_SIZE, message) << BLOCK_SHIFT;
	u16 tag = FIELD_GET(INITRB_TAG, message);
	u32 bufsz, end, header_size, block_size;

	if (tag != ep->bfr_tag) {
		dev_err(ep->dev, "AFK[ep:%02x]: expected tag 0x%x but got 0x%x\n",
			ep->endpoint, ep->bfr_tag, tag);
		return;
	}

	if (bfr->ready) {
		dev_err(ep->dev, "AFK[ep:%02x]: buffer is already initialized\n",
			ep->endpoint);
		return;
	}

	if (base >= ep->bfr_size) {
		dev_err(ep->dev,
			"AFK[ep:%02x]: requested base 0x%x >= max size 0x%lx\n",
			ep->endpoint, base, ep->bfr_size);
		return;
	}

	end = base + size;
	if (end > ep->bfr_size) {
		dev_err(ep->dev,
			"AFK[ep:%02x]: requested end 0x%x > max size 0x%lx\n",
			ep->endpoint, end, ep->bfr_size);
		return;
	}

	if (size < sizeof(__le32))
		return;

	dma_rmb();
	bufsz = le32_to_cpu(*(__le32 *)(ep->bfr + base));
	if (bufsz >= size)
		goto invalid_layout;

	/* AFK uses three equally spaced metadata blocks, not a fixed header.
	 * See m1n1's AFKRingBuf: M4 uses 0x80-byte blocks rather than 0x40.
	 */
	header_size = size - bufsz;
	block_size = header_size / 3;
	if (header_size % 3 || block_size < (1 << BLOCK_SHIFT) ||
	    (block_size & (block_size - 1)) || !bufsz ||
	    bufsz % block_size || bufsz < block_size)
		goto invalid_layout;

	bfr->shared_rptr = ep->bfr + base + block_size;
	bfr->shared_wptr = ep->bfr + base + 2 * block_size;
	bfr->block_size = block_size;
	bfr->buf = ep->bfr + base + header_size;
	bfr->bufsz = bufsz;
	bfr->ready = true;

	if (ep->rxbfr.ready && ep->txbfr.ready)
		afk_send(ep, FIELD_PREP(RBEP_TYPE, RBEP_START));
	return;

invalid_layout:
	dev_err(ep->dev,
		"AFK[ep:%02x]: invalid ring layout: size=0x%x payload=0x%x\n",
		ep->endpoint, size, bufsz);
}

static struct afk_service *afk_find_service(struct afk_endpoint *ep, u32 channel)
{
	if (channel == PANEL_CHANNEL && ep->service.enabled)
		return &ep->service;
	return NULL;
}

static void afk_recv_handle_init(struct afk_endpoint *ep, u32 channel,
				 const u8 *payload, size_t payload_size)
{
	/* Native announcements carry a 16-byte name followed by a UUID. Only
	 * the qualified panel service is a command target for this module. */
	if (channel != PANEL_CHANNEL || payload_size < 32 ||
	    memcmp(payload, "disp0-service", sizeof("disp0-service")))
		return;
	if (ep->service.enabled)
		return;
	ep->service.enabled = true;
	ep->ready(ep->cookie);
}

static void afk_recv_handle_teardown(struct afk_endpoint *ep, u32 channel)
{
	struct afk_service *service = afk_find_service(ep, channel);
	unsigned long flags;

	if (!service)
		return;
	/* Keep the service present so an outstanding DMA acknowledgement can
	 * still release its buffers. Teardown forbids all future commands. */
	spin_lock_irqsave(&service->lock, flags);
	service->torndown = true;
	spin_unlock_irqrestore(&service->lock, flags);
	ep->failed(ep->cookie, -ENODEV);
}

static void afk_recv_handle_reply(struct afk_endpoint *ep, u32 channel,
				  u8 tag, u32 retcode, const u8 *payload,
				  size_t payload_size, bool dma)
{
	struct afk_service *service;
	unsigned long flags;
	u8 idx = 0;
	void *rxbuf, *txbuf;
	dma_addr_t rxbuf_dma, txbuf_dma;
	size_t rxlen, txlen;

	service = afk_find_service(ep, channel);
	if (!service) {
		dev_warn(ep->dev, "AFK[ep:%02x]: command reply on disabled channel %u\n",
			 ep->endpoint, channel);
		return;
	}

	if (dma && payload_size < NATIVE_DMA_SIZE) {
		dev_err(ep->dev, "AFK[ep:%02x]: truncated DMA reply\n",
			ep->endpoint);
		return;
	}


	spin_lock_irqsave(&service->lock, flags);
	if (!test_bit(idx, service->cmd_map) || service->cmds[idx].done) {
		spin_unlock_irqrestore(&service->lock, flags);
		return;
	}

	if (tag != service->cmds[idx].tag) {
		dev_err(ep->dev,
			"AFK[ep:%02x]: command reply on channel %d has invalid tag: expected 0x%04x != 0x%04x\n",
			ep->endpoint, channel, tag, service->cmds[idx].tag);
		spin_unlock_irqrestore(&service->lock, flags);
		return;
	}

	if (!dma && payload_size > service->cmds[idx].rxlen) {
		spin_unlock_irqrestore(&service->lock, flags);
		dev_err(ep->dev, "AFK[ep:%02x]: oversized inline reply\n",
			ep->endpoint);
		return;
	}
	if (!dma && payload_size)
		memcpy(service->cmds[idx].rxbuf, payload, payload_size);
	dma_rmb();
	service->cmds[idx].done = true;
	service->cmds[idx].retcode = retcode;
	if (service->cmds[idx].free_on_ack) {
		/* The timed-out caller left ownership here until this acknowledgement. */
		rxbuf = service->cmds[idx].rxbuf;
		txbuf = service->cmds[idx].txbuf;
		rxlen = service->cmds[idx].rxlen;
		txlen = service->cmds[idx].txlen;
		rxbuf_dma = service->cmds[idx].rxbuf_dma;
		txbuf_dma = service->cmds[idx].txbuf_dma;
		bitmap_release_region(service->cmd_map, idx, 0);
	} else {
		rxbuf = txbuf = NULL;
		rxlen = txlen = 0;
	}
	if (service->cmds[idx].completion)
		complete(service->cmds[idx].completion);

	spin_unlock_irqrestore(&service->lock, flags);

	if (rxbuf && rxlen)
		dma_free_coherent(ep->dev, rxlen, rxbuf, rxbuf_dma);
	if (txbuf && txlen)
		dma_free_coherent(ep->dev, txlen, txbuf, txbuf_dma);
}


static void afk_recv_handle(struct afk_endpoint *ep, u8 *data, size_t data_size)
{
	struct native_message msg;
	struct afk_service *service;
	if (native_decode(data, data_size, &msg)) {
		dev_err(ep->dev, "AFK[ep:%02x]: invalid native envelope\n",
			ep->endpoint);
		return;
	}
	service = afk_find_service(ep, msg.channel);
	if (msg.type == 0x11 && msg.category == 0 && msg.flavor == 1) {
		if (!service)
			afk_recv_handle_init(ep, msg.channel, (u8 *)msg.body, msg.body_size);
		return;
	}
	if (msg.type == 0x14 && msg.category == 0 && msg.flavor == 1)
		return afk_recv_handle_teardown(ep, msg.channel);
	if (msg.category == 2 &&
	    (msg.type == NATIVE_SERVICE_TYPE || (msg.type == 0x11 && msg.flavor == 1))) {
		if (msg.body_size < NATIVE_TRANSACTION_SIZE)
			return;
		return afk_recv_handle_reply(ep, msg.channel, msg.body[1],
			get_unaligned_le32(msg.body + 4),
			msg.body + NATIVE_TRANSACTION_SIZE,
			msg.body_size - NATIVE_TRANSACTION_SIZE, msg.body[0] & 1);
	}
}

static bool afk_recv(struct afk_endpoint *ep)
{
	struct afk_qe *hdr;
	u32 rptr, wptr;
	u32 magic, size;

	if (!ep->rxbfr.ready) {
		dev_err(ep->dev, "AFK[ep:%02x]: got RECV but not ready\n",
			ep->endpoint);
		return false;
	}

	rptr = le32_to_cpu(READ_ONCE(*ep->rxbfr.shared_rptr));
	wptr = le32_to_cpu(READ_ONCE(*ep->rxbfr.shared_wptr));

	if (rptr == wptr)
		return false;

	if (wptr >= ep->rxbfr.bufsz || wptr % ep->rxbfr.block_size ||
	    rptr % ep->rxbfr.block_size ||
	    rptr > (ep->rxbfr.bufsz - sizeof(*hdr))) {
		dev_warn(ep->dev,
			 "AFK[ep:%02x]: rptr out of bounds: 0x%x > 0x%lx\n",
			 ep->endpoint, rptr, ep->rxbfr.bufsz - sizeof(*hdr));
		return false;
	}

	dma_rmb();

	hdr = ep->rxbfr.buf + rptr;
	magic = le32_to_cpu(hdr->magic);
	size = le32_to_cpu(hdr->size);

	if (magic != QE_MAGIC) {
		dev_warn(ep->dev, "AFK[ep:%02x]: invalid queue entry magic: 0x%x\n",
			 ep->endpoint, magic);
		return false;
	}

	/*
	 * If there's not enough space for the payload the co-processor inserted
	 * the current dummy queue entry and we have to advance to the next one
	 * which will contain the real data.
	*/
	if (rptr + size + sizeof(*hdr) > ep->rxbfr.bufsz) {
		rptr = 0;
		hdr = ep->rxbfr.buf + rptr;
		magic = le32_to_cpu(hdr->magic);
		size = le32_to_cpu(hdr->size);

		if (magic != QE_MAGIC) {
			dev_warn(ep->dev,
				 "AFK[ep:%02x]: invalid next queue entry magic: 0x%x\n",
				 ep->endpoint, magic);
			return false;
		}

	}

	if (rptr + size + sizeof(*hdr) > ep->rxbfr.bufsz) {
		dev_warn(ep->dev,
			 "AFK[ep:%02x]: queue entry out of bounds: 0x%lx > 0x%lx\n",
			 ep->endpoint, rptr + size + sizeof(*hdr), ep->rxbfr.bufsz);
		return false;
	}


	rptr = ALIGN(rptr + sizeof(*hdr) + size, ep->rxbfr.block_size);
	if (WARN_ON(rptr > ep->rxbfr.bufsz))
		rptr = 0;
	if (rptr == ep->rxbfr.bufsz)
		rptr = 0;

	/* Consume before releasing the ring slot to the firmware. */
	afk_recv_handle(ep, hdr->data, size);
	dma_mb();
	WRITE_ONCE(*ep->rxbfr.shared_rptr, cpu_to_le32(rptr));

	return true;
}

static void afk_receive_message_worker(struct work_struct *work_)
{
	struct afk_receive_message_work *work;
	u16 type;

	work = container_of(work_, struct afk_receive_message_work, work);

	type = FIELD_GET(RBEP_TYPE, work->message);
	switch (type) {
	case RBEP_INIT_ACK:
		break;

	case RBEP_START_ACK:
		complete_all(&work->ep->started);
		break;

	case RBEP_GETBUF:
		afk_getbuf(work->ep, work->message);
		break;

	case RBEP_INIT_TX:
		afk_init_rxtx(work->ep, work->message, &work->ep->txbfr);
		break;

	case RBEP_INIT_RX:
		afk_init_rxtx(work->ep, work->message, &work->ep->rxbfr);
		break;

	case RBEP_RECV:
		while (afk_recv(work->ep))
			;
		break;

	default:
		dev_err(work->ep->dev,
			"Received unknown AFK message type: 0x%x\n", type);
	}

	kfree(work);
}

int afk_receive_message(struct afk_endpoint *ep, u64 message)
{
	struct afk_receive_message_work *work;

	/* DMA allocation and reply handling run outside the RTKit receive thread. */
	work = kzalloc(sizeof(*work), GFP_KERNEL);
	if (!work)
		return -ENOMEM;

	work->ep = ep;
	work->message = message;
	INIT_WORK(&work->work, afk_receive_message_worker);
	queue_work(ep->wq, &work->work);

	return 0;
}

static int afk_send_native(struct afk_endpoint *ep, const u8 *payload,
			   size_t payload_len)
{
	u32 rptr, wptr;
	struct afk_qe *hdr, *hdr2;
	unsigned long flags;
	size_t total_epic_size, total_size;
	int ret;
	if (!ep->txbfr.ready || payload_len < NATIVE_HEADER_SIZE ||
	    payload_len > ep->txbfr.bufsz - sizeof(*hdr))
		return -EINVAL;
	spin_lock_irqsave(&ep->lock, flags);

	dma_rmb();
	rptr = le32_to_cpu(READ_ONCE(*ep->txbfr.shared_rptr));
	wptr = le32_to_cpu(READ_ONCE(*ep->txbfr.shared_wptr));
	if (rptr >= ep->txbfr.bufsz || wptr >= ep->txbfr.bufsz ||
	    rptr % ep->txbfr.block_size || wptr % ep->txbfr.block_size) {
		ret = -EPROTO;
		goto out;
	}
	total_epic_size = payload_len;
	total_size = ALIGN(sizeof(*hdr) + total_epic_size, ep->txbfr.block_size);

	hdr = hdr2 = NULL;

	/*
	 * We need to figure out how to place the entire headers and payload
	 * into the ring buffer:
	 * - If the write pointer is in front of the read pointer we just need
	 *   enough space inbetween to store everything.
	 * - If the read pointer has already wrapped around the end of the
	 *   buffer we can
	 *    a) either store the entire payload at the writer pointer if
	 *       there's enough space until the end,
	 *    b) or just store the queue entry at the write pointer to indicate
	 *       that we need to wrap to the start and then store the headers
	 *       and the payload at the beginning of the buffer. The queue
	 *       header has to be stored twice in this case.
	 * In either case we have to ensure that there's always enough space
	 * so that we don't accidentally overwrite other buffers.
	 */
	if (wptr < rptr) {
		/*
		 * If wptr < rptr we can't wrap around and only have to make
		 * sure that there's enough space for the entire payload.
		 */
		if (wptr + total_size >= rptr) {
			ret = -ENOMEM;
			goto out;
		}

		hdr = ep->txbfr.buf + wptr;
		wptr += sizeof(*hdr);
	} else {
		/* We need enough space to place at least a queue entry */
		if (wptr + sizeof(*hdr) > ep->txbfr.bufsz) {
			ret = -ENOMEM;
			goto out;
		}

		/*
		 * If we can place a single queue entry but not the full payload
		 * we need to place one queue entry at the end of the ring
		 * buffer and then another one together with the entire
		 * payload at the beginning.
		 */
		if (wptr + total_size > ep->txbfr.bufsz) {
			/*
			 * Ensure there's space for the  queue entry at the
			 * beginning
			 */
			if (sizeof(*hdr) > rptr) {
				ret = -ENOMEM;
				goto out;
			}

			/*
			 * Place two queue entries to indicate we want to wrap
			 * around to the firmware.
			 */
			hdr = ep->txbfr.buf + wptr;
			hdr2 = ep->txbfr.buf;
			wptr = sizeof(*hdr);

			/* Ensure there's enough space for the entire payload */
			if (total_size >= rptr) {
				ret = -ENOMEM;
				goto out;
			}
		} else {
			if (wptr + total_size == ep->txbfr.bufsz && !rptr) {
				ret = -ENOMEM;
				goto out;
			}
			/* We have enough space to place the entire payload */
			hdr = ep->txbfr.buf + wptr;
			wptr += sizeof(*hdr);
		}
	}
	/*
	 * At this point we're guaranteed that hdr (and possibly hdr2) point
	 * to a buffer large enough to fit the queue entry and that we have
	 * enough space at wptr to store the payload.
	 */

	hdr->magic = cpu_to_le32(QE_MAGIC);
	hdr->size = cpu_to_le32(total_epic_size);
	hdr->channel = 0;
	hdr->type = 0;
	if (hdr2)
		memcpy(hdr2, hdr, sizeof(*hdr));

	memcpy(ep->txbfr.buf + wptr, payload, payload_len);
	*((u8 *)ep->txbfr.buf + wptr) = (u8)ep->qe_seq++;
	wptr += payload_len;
	wptr = ALIGN(wptr, ep->txbfr.block_size);
	if (wptr == ep->txbfr.bufsz)
		wptr = 0;

	dma_wmb();
	WRITE_ONCE(*ep->txbfr.shared_wptr, cpu_to_le32(wptr));
	/* The non-atomic RTKit mailbox send can sleep if its FIFO is full. */
	spin_unlock_irqrestore(&ep->lock, flags);
	/* After publishing wptr the firmware owns command DMA. A notification
	 * error must not make the caller free buffers before an acknowledgement. */
	afk_send(ep, FIELD_PREP(RBEP_TYPE, RBEP_SEND) |
			     FIELD_PREP(SEND_WPTR, wptr));
	return 0;

out:
	spin_unlock_irqrestore(&ep->lock, flags);
	return ret;
}

int afk_send_command(struct afk_endpoint *ep, const void *payload,
		     size_t payload_len, void *output, size_t output_len,
		     u32 *retcode)
{
	u8 cmd[NATIVE_COMMAND_SIZE];
	void *rxbuf = NULL, *txbuf;
	dma_addr_t rxbuf_dma = 0, txbuf_dma;
	unsigned long flags;
	int ret, idx;
	u8 tag;
	struct afk_service *service = &ep->service;
	DECLARE_COMPLETION_ONSTACK(completion);
	if (!payload || !payload_len || payload_len > U32_MAX ||
	    output_len > U32_MAX || (output_len && !output))
		return -EINVAL;
	if (!service->enabled || READ_ONCE(service->torndown))
		return -ENODEV;
	if (!service->native_opened) {
		u8 open[NATIVE_HEADER_SIZE];
		/* The firmware's compatibility decoder emits this before the
		 * first command, without waiting for an acknowledgement. */
		native_encode_header(open, sizeof(open), PANEL_CHANNEL, 0,
				     NATIVE_OPEN_TYPE, 0, 1);
		ret = afk_send_native(ep, open, sizeof(open));
		if (ret)
			return ret;
		service->native_opened = true;
	}
	if (READ_ONCE(ep->send_failed))
		return -EIO;

	/* Write-only iBoot commands acknowledge status without a response body.
	 * Advertising an RX buffer for an empty response asserts in firmware. */
	if (output_len) {
		rxbuf = dma_alloc_coherent(ep->dev, output_len, &rxbuf_dma,
					   GFP_KERNEL);
		if (!rxbuf)
			return -ENOMEM;
		memset(rxbuf, 0, output_len);
	}
	txbuf = dma_alloc_coherent(ep->dev, payload_len, &txbuf_dma,
				   GFP_KERNEL);
	if (!txbuf) {
		ret = -ENOMEM;
		goto err_free_rxbuf;
	}

	memcpy(txbuf, payload, payload_len);

	native_encode_command(cmd, PANEL_CHANNEL, 0, 0,
			      rxbuf_dma, txbuf_dma, output_len, payload_len);

	spin_lock_irqsave(&service->lock, flags);
	idx = bitmap_find_free_region(service->cmd_map, MAX_PENDING_CMDS, 0);
	if (idx < 0) {
		ret = -ENOSPC;
		goto err_unlock;
	}

	/* Use the full native byte tag; only one transaction can be pending. */
	tag = service->cmd_tag++;

	service->cmds[idx].tag = tag;
	service->cmds[idx].rxbuf = rxbuf;
	service->cmds[idx].txbuf = txbuf;
	service->cmds[idx].rxbuf_dma = rxbuf_dma;
	service->cmds[idx].txbuf_dma = txbuf_dma;
	service->cmds[idx].rxlen = output_len;
	service->cmds[idx].txlen = payload_len;
	service->cmds[idx].free_on_ack = false;
	service->cmds[idx].done = false;
	service->cmds[idx].completion = &completion;
	init_completion(&completion);

	spin_unlock_irqrestore(&service->lock, flags);

	cmd[25] = tag;
	ret = afk_send_native(ep, cmd, sizeof(cmd));
	if (ret)
		goto err_free_cmd;

	ret = wait_for_completion_timeout(&completion,
					  msecs_to_jiffies(MSEC_PER_SEC));

	if (ret <= 0) {
		spin_lock_irqsave(&service->lock, flags);
		/*
		 * Check again while we're inside the lock to make sure
		 * the command wasn't completed just after
		 * wait_for_completion_timeout returned.
		 */
		if (!service->cmds[idx].done) {
			service->cmds[idx].completion = NULL;
			service->cmds[idx].free_on_ack = true;
			spin_unlock_irqrestore(&service->lock, flags);
			return -ETIMEDOUT;
		}
		spin_unlock_irqrestore(&service->lock, flags);
	}

	ret = READ_ONCE(ep->send_failed) ? -EIO : 0;
	if (retcode)
		*retcode = service->cmds[idx].retcode;
	if (output && output_len)
		memcpy(output, rxbuf, output_len);

err_free_cmd:
	spin_lock_irqsave(&service->lock, flags);
	bitmap_release_region(service->cmd_map, idx, 0);
err_unlock:
	spin_unlock_irqrestore(&service->lock, flags);
	dma_free_coherent(ep->dev, payload_len, txbuf, txbuf_dma);
err_free_rxbuf:
	if (rxbuf)
		dma_free_coherent(ep->dev, output_len, rxbuf, rxbuf_dma);
	return ret;
}
