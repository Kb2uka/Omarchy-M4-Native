/* SPDX-License-Identifier: GPL-2.0-only OR MIT */
/* Copyright 2022 Sven Peter <sven@svenpeter.dev> */
/* Modified 2026-10-18: native J713 brightness-only AFK transport. */
#ifndef J713_BACKLIGHT_AFK_H
#define J713_BACKLIGHT_AFK_H

#include <linux/types.h>

struct device;
struct apple_rtkit;
struct afk_endpoint;

#define J713_AFK_ENDPOINT 0x23

struct afk_endpoint *afk_init(struct device *dev, struct apple_rtkit *rtk,
			    void *cookie, void (*ready)(void *cookie),
			    void (*failed)(void *cookie, int error));
int afk_start(struct afk_endpoint *ep);
int afk_receive_message(struct afk_endpoint *ep, u64 message);
/* The client must serialize commands, including the initial service open. */
int afk_send_command(struct afk_endpoint *ep, const void *payload,
		     size_t payload_len, void *output, size_t output_len,
		     u32 *retcode);

#endif
