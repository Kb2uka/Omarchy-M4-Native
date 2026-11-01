/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef J715_DISPLAY_PROFILE_H
#define J715_DISPLAY_PROFILE_H
/* This interface deliberately supplies no board-derived defaults. */
#include "j715-profile-generated.h"

#if J715_PANEL_MIN_NITS > J715_PANEL_INITIAL_NITS || \
    J715_PANEL_INITIAL_NITS > J715_PANEL_MAX_NITS || \
    J715_PANEL_MAX_NITS > 8388607 || J715_STREAM >= 32 || J715_DMA_BITS != 42
#error Unsupported generated profile
#endif

struct j715_startup {
	bool prepared;
	bool attempted;
	bool online;
	bool failed;
};

static inline int j715_begin(struct j715_startup *state)
{
	if (!state->prepared || state->attempted || state->failed)
		return -1;
	state->attempted = true;
	return 0;
}

static inline bool j715_reply_valid(const u32 reply[5])
{
	return reply[0] == 3 && reply[1] == 20 && reply[2] == 1 &&
	       reply[3] == 1 && reply[4] == 1;
}

static inline bool j715_nits_valid(u32 nits, bool registered)
{
	return nits >= J715_PANEL_MIN_NITS && nits <= J715_PANEL_MAX_NITS &&
	       (registered || nits == J715_PANEL_INITIAL_NITS);
}
#endif
