/* SPDX-License-Identifier: GPL-2.0-only */
/* Shared J713 ISP identity-probe algorithm. Kernel module and mock harness.
 * Inspection is optional and read-only: 37 words, then one RVBAR u64.
 * It is not firmware startup. Zero from that read is an observation.
 */
#ifndef J713_ISP_PROBE_ALG_H
#define J713_ISP_PROBE_ALG_H

#ifdef __KERNEL__
#include <linux/errno.h>
#include <linux/kernel.h>
#include <linux/types.h>
#else
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
typedef uint32_t u32;
typedef uint64_t u64;
#ifndef ARRAY_SIZE
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#endif
#endif

#define J713_ISP_CPU_BASE		0x48c000000ULL
#define J713_ISP_WRAPPER_SIZE		0x3ff0000u

#define J713_ISP_PMGR_PHYS		0x380700000ULL
#define J713_ISP_PMGR_SIZE		0x14000u
#define J713_ISP_LOCAL_PHYS		0x380704000ULL
#define J713_ISP_LOCAL_SIZE		0x64u
#define J713_ISP_VER_PHYS		0x48d800000ULL
#define J713_ISP_VER_SIZE		4u

/* ISP-relative 0x1050000. One native-width 64-bit read. No write. */
#define J713_ISP_RVBAR_REL		0x1050000u
#define J713_ISP_RVBAR_PHYS		(J713_ISP_CPU_BASE + J713_ISP_RVBAR_REL)
#define J713_ISP_RVBAR_SIZE		8u

/* ISP-relative 0x1600044/0x1600048. Not the disable word at 0x1600040. */
#define J713_ISP_COPROC_REL		0x1600044u
#define J713_ISP_COPROC_PHYS		(J713_ISP_CPU_BASE + J713_ISP_COPROC_REL)
#define J713_ISP_COPROC_SIZE		8u
#define J713_ISP_COPROC_CTRL		0x0u
#define J713_ISP_COPROC_STATUS		0x4u

/* ISP-relative 0x29241d0, eight shared GPIO words. Clock word +0x20 is out. */
#define J713_ISP_GPIO_REL		0x29241d0u
#define J713_ISP_GPIO_PHYS		(J713_ISP_CPU_BASE + J713_ISP_GPIO_REL)
#define J713_ISP_GPIO_COUNT		8u
#define J713_ISP_GPIO_SIZE		(J713_ISP_GPIO_COUNT * 4u)

#define J713_ISP_DART0_PHYS		0x48e8c0000ULL
#define J713_ISP_DART1_PHYS		0x48e8e0000ULL
#define J713_ISP_DART2_PHYS		0x48e900000ULL
#define J713_ISP_DART_SIZE		0xc000u
#define J713_ISP_DART_INSTANCES		3u
#define J713_ISP_DART_WORDS		9u
/* apple-dart.c T8110: PARAMS1/2, PARAMS3/4, PROTECT. Not PROTECT_LOCK 0x208. */
#define J713_ISP_DART_PARAMS1		0x00u
#define J713_ISP_DART_PARAMS2		0x04u
#define J713_ISP_DART_PARAMS3		0x08u
#define J713_ISP_DART_PARAMS4		0x0cu
#define J713_ISP_DART_PROTECT		0x200u
#define J713_ISP_DART_TCR_BASE		0x1000u
#define J713_ISP_DART_TTBR_BASE		0x1400u
#define J713_ISP_DART_SID_STRIDE	4u
#define J713_ISP_DART_TTBR_COUNT	1u
#define J713_ISP_DART_SID0		0u
#define J713_ISP_DART_SID12		12u
#define J713_ISP_DART_SID0_TCR		(J713_ISP_DART_TCR_BASE + (J713_ISP_DART_SID0 * J713_ISP_DART_SID_STRIDE))
#define J713_ISP_DART_SID0_TTBR		(J713_ISP_DART_TTBR_BASE + ((J713_ISP_DART_TTBR_COUNT * J713_ISP_DART_SID0) * J713_ISP_DART_SID_STRIDE))
#define J713_ISP_DART_SID12_TCR		(J713_ISP_DART_TCR_BASE + (J713_ISP_DART_SID12 * J713_ISP_DART_SID_STRIDE))
#define J713_ISP_DART_SID12_TTBR	(J713_ISP_DART_TTBR_BASE + ((J713_ISP_DART_TTBR_COUNT * J713_ISP_DART_SID12) * J713_ISP_DART_SID_STRIDE))

#define J713_ISP_INSPECT_COUNT		(2u + J713_ISP_GPIO_COUNT + (J713_ISP_DART_INSTANCES * J713_ISP_DART_WORDS))

#define J713_ISP_MAIN_188		0x188u
#define J713_ISP_MAIN_190		0x190u
#define J713_ISP_MAIN_530		0x530u
#define J713_ISP_LOCAL_BASE		0x4000u
#define J713_ISP_LOCAL_STRIDE		8u
#define J713_ISP_LOCAL_COUNT		13u
#define J713_ISP_RESTORE_INDEX		4u	/* 0x4020 */

#define J713_ISP_ENABLE_VAL		0xfu
#define J713_ISP_REQ_MASK		0xfu
#define J713_ISP_LOW_MASK		0xffu
#define J713_ISP_BUSY			(1u << 11)
#define J713_ISP_REQ_OFF		0u
#define J713_ISP_REQ_PARTIAL		4u
#define J713_ISP_POLL_TIMEOUT_US	100000u
#define J713_ISP_POLL_SLEEP_US		10u
/* Low byte, historical flags 8/9, auto-actual 27:24. No disable/reset/min/auto-enable. */
#define J713_ISP_OBS_MASK		0x0f0003ffu
#define J713_ISP_MAIN_530_EXTRA		0x2000u
#define J713_ISP_VERSION_GEN		0x10u
#define J713_ISP_VERSION_GEN_SHIFT	16
/* Live Air identity. High 16 matches VERSION_GEN. Inspection requires the whole word. */
#define J713_ISP_VERSION_EXACT		0x100001u
#define J713_ISP_ERR_GENERATION		(-EPROTO)
#define J713_ISP_ERR_EXACT		(-EBADMSG)

#define J713_ISP_SPACE_PMGR		0
#define J713_ISP_SPACE_VER		1
#define J713_ISP_SPACE_COPROC		2
#define J713_ISP_SPACE_GPIO		3
#define J713_ISP_SPACE_DART0		4
#define J713_ISP_SPACE_DART1		5
#define J713_ISP_SPACE_DART2		6
#define J713_ISP_SPACE_RVBAR		7

#define J713_ISP_STAGE_IDLE		0
#define J713_ISP_STAGE_GUARDS		1
#define J713_ISP_STAGE_MAPPED		2
#define J713_ISP_STAGE_SNAPSHOT		3
#define J713_ISP_STAGE_ENABLE		4
#define J713_ISP_STAGE_VERSION		5
#define J713_ISP_STAGE_CLEANUP		6
#define J713_ISP_STAGE_DONE		7
#define J713_ISP_STAGE_VERIFY		8
#define J713_ISP_STAGE_INSPECT		9

#define J713_ISP_WIN_LOCAL		0
#define J713_ISP_WIN_VER		1
#define J713_ISP_WIN_COPROC		2
#define J713_ISP_WIN_GPIO		3
#define J713_ISP_WIN_DART0		4
#define J713_ISP_WIN_DART1		5
#define J713_ISP_WIN_DART2		6
#define J713_ISP_WIN_RVBAR		7
#define J713_ISP_WIN_COUNT		8

struct j713_isp_ops {
	int (*read32)(void *ctx, int space, u32 off, u32 *val);
	int (*write32)(void *ctx, int space, u32 off, u32 val);
	/* Succeed when (val & 0xff) == want_low and bit 11 is clear. */
	int (*poll_ps)(void *ctx, u32 off, u32 want_low);
	void (*log)(void *ctx, const char *msg);
	/* Injected native-width read. Used only for the one RVBAR window. */
	int (*read64)(void *ctx, int space, u32 off, u64 *val);
};

struct j713_isp_state {
	int result;
	int cleanup;
	int verify;
	int stage;
	int fail_stage;
	unsigned int attempted;
	unsigned int isp_reads;
	unsigned int inspect_reads;
	u32 version;
	u32 saved_req[J713_ISP_LOCAL_COUNT];
	u32 inspect_val[J713_ISP_INSPECT_COUNT];
	u64 raw_rvbar;
	unsigned int rvbar_reads;
};

struct j713_isp_inspect_word {
	const char *bank;
	int space;
	u32 off;
	u64 phys;
};

struct j713_isp_range {
	u64 start;
	u64 size;
};

typedef int (*j713_isp_inspect_fn)(void *ctx, struct j713_isp_state *st);

static inline u32 j713_isp_local_off(unsigned int i)
{
	return J713_ISP_LOCAL_BASE + i * J713_ISP_LOCAL_STRIDE;
}

static inline u32 j713_isp_ack_low(u32 req)
{
	req &= J713_ISP_REQ_MASK;
	return req | (req << 4);
}

/* True only for the 37 known inspection offsets. Version, PMGR, and RVBAR
 * are separate. RVBAR is not a 32-bit inspection word.
 */
static inline bool j713_isp_inspect_off_ok(int space, u32 off)
{
	switch (space) {
	case J713_ISP_SPACE_COPROC:
		return off == J713_ISP_COPROC_CTRL || off == J713_ISP_COPROC_STATUS;
	case J713_ISP_SPACE_GPIO:
		return off < J713_ISP_GPIO_SIZE && (off & 3u) == 0u;
	case J713_ISP_SPACE_DART0:
	case J713_ISP_SPACE_DART1:
	case J713_ISP_SPACE_DART2:
		switch (off) {
		case J713_ISP_DART_PARAMS1:
		case J713_ISP_DART_PARAMS2:
		case J713_ISP_DART_PARAMS3:
		case J713_ISP_DART_PARAMS4:
		case J713_ISP_DART_PROTECT:
		case J713_ISP_DART_SID0_TCR:
		case J713_ISP_DART_SID0_TTBR:
		case J713_ISP_DART_SID12_TCR:
		case J713_ISP_DART_SID12_TTBR:
			return true;
		default:
			return false;
		}
	default:
		return false;
	}
}

/* Exclusive claims. apply-only is local+version. inspect adds COPROC, GPIO,
 * three DARTs, and the 8-byte RVBAR window. Order matches J713_ISP_WIN_*.
 * The full PMGR ioremap and the ISP wrapper are not exclusive claims.
 */
static inline unsigned int j713_isp_fill_claims(bool inspect,
						struct j713_isp_range *out)
{
	unsigned int n = 0;

	out[n++] = (struct j713_isp_range){ J713_ISP_LOCAL_PHYS, J713_ISP_LOCAL_SIZE };
	out[n++] = (struct j713_isp_range){ J713_ISP_VER_PHYS, J713_ISP_VER_SIZE };
	if (!inspect)
		return n;
	out[n++] = (struct j713_isp_range){ J713_ISP_COPROC_PHYS, J713_ISP_COPROC_SIZE };
	out[n++] = (struct j713_isp_range){ J713_ISP_GPIO_PHYS, J713_ISP_GPIO_SIZE };
	out[n++] = (struct j713_isp_range){ J713_ISP_DART0_PHYS, J713_ISP_DART_SIZE };
	out[n++] = (struct j713_isp_range){ J713_ISP_DART1_PHYS, J713_ISP_DART_SIZE };
	out[n++] = (struct j713_isp_range){ J713_ISP_DART2_PHYS, J713_ISP_DART_SIZE };
	out[n++] = (struct j713_isp_range){ J713_ISP_RVBAR_PHYS, J713_ISP_RVBAR_SIZE };
	return n;
}

static inline int j713_isp_ranges_overlap(const struct j713_isp_range *ranges,
					  unsigned int n)
{
	unsigned int i, j;

	if (!ranges && n)
		return -EINVAL;
	for (i = 0; i < n; i++) {
		u64 end_i;

		if (!ranges[i].size)
			return -EINVAL;
		end_i = ranges[i].start + ranges[i].size;
		if (end_i < ranges[i].start)
			return -EINVAL;
		for (j = i + 1; j < n; j++) {
			u64 end_j;

			if (!ranges[j].size)
				return -EINVAL;
			end_j = ranges[j].start + ranges[j].size;
			if (end_j < ranges[j].start)
				return -EINVAL;
			if (ranges[i].start < end_j && ranges[j].start < end_i)
				return -EINVAL;
		}
	}
	return 0;
}

const struct j713_isp_inspect_word *j713_isp_inspect_plan(unsigned int *count);
int j713_isp_inspect_read(const struct j713_isp_ops *ops, void *ctx,
			  struct j713_isp_state *st);
/* One u64 at offset 0. Refuses any other offset and any call that is not
 * inside the inspect callback after all 13 ACKs, exact version, and the
 * 37-word walk. Does not write. A raw value of zero is success.
 */
int j713_isp_rvbar_read(const struct j713_isp_ops *ops, void *ctx,
			struct j713_isp_state *st, u32 off);
int j713_isp_inspect_then_rvbar(const struct j713_isp_ops *ops, void *ctx,
				struct j713_isp_state *st);
int j713_isp_probe_run(const struct j713_isp_ops *ops, void *ctx, bool apply,
		       j713_isp_inspect_fn inspect, struct j713_isp_state *st);

#endif
