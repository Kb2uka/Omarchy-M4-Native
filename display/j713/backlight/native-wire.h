/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef J713_BACKLIGHT_NATIVE_WIRE_H
#define J713_BACKLIGHT_NATIVE_WIRE_H

#include <linux/errno.h>
#include <linux/string.h>
#include <linux/types.h>
#include <linux/unaligned.h>

/* AFK's native envelope: 8-byte route, 16-byte classification, then body.
 * Byte 1 and bytes 19..23 are padding, not version discriminators.
 * These offsets follow the running firmware's legacy/native converters. */
#define NATIVE_HEADER_SIZE 24
#define NATIVE_TRANSACTION_SIZE 8
#define NATIVE_DMA_SIZE 24
#define NATIVE_COMMAND_SIZE 56
#define NATIVE_OPEN_TYPE 0x12
#define NATIVE_SERVICE_TYPE 0xc0

struct native_message {
	u16 channel;
	u8 sequence;
	u8 type;
	u8 category;
	u8 flavor;
	const u8 *body;
	size_t body_size;
};

static inline int native_decode(const u8 *data, size_t size, struct native_message *msg)
{
	if (size < NATIVE_HEADER_SIZE ||
	    get_unaligned_le32(data + 4) != size - 8)
		return -EPROTO;
	msg->sequence = data[0];
	msg->channel = get_unaligned_le16(data + 2);
	msg->type = data[16];
	msg->category = data[17];
	msg->flavor = data[18];
	msg->body = data + NATIVE_HEADER_SIZE;
	msg->body_size = size - NATIVE_HEADER_SIZE;
	return 0;
}

static inline void native_encode_header(u8 *data, size_t size, u16 channel,
				u8 sequence, u8 type, u8 category, u8 flavor)
{
	memset(data, 0, NATIVE_HEADER_SIZE);
	data[0] = sequence;
	put_unaligned_le16(channel, data + 2);
	put_unaligned_le32(size - 8, data + 4);
	data[16] = type;
	data[17] = category;
	data[18] = flavor;
}

static inline void native_encode_command(u8 *data, u16 channel, u8 sequence, u8 tag,
				 u64 rx, u64 tx, u32 rxlen, u32 txlen)
{
	native_encode_header(data, NATIVE_COMMAND_SIZE, channel, sequence,
			     NATIVE_SERVICE_TYPE, 1, 0);
	memset(data + NATIVE_HEADER_SIZE, 0,
	       NATIVE_COMMAND_SIZE - NATIVE_HEADER_SIZE);
	data[24] = 0xff;
	data[25] = tag;
	put_unaligned_le32(rxlen, data + 28);
	put_unaligned_le64(rx, data + 32);
	put_unaligned_le64(tx, data + 40);
	put_unaligned_le32(rxlen, data + 48);
	put_unaligned_le32(txlen, data + 52);
}
#endif
