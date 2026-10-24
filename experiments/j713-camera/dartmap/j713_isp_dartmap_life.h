/* SPDX-License-Identifier: GPL-2.0-only */
/* Injected lifecycle for the ISP DART scratch map.
 * The map path assumes the 13 local gates are already on. It never restores
 * power itself: the identity-probe callback contract does that, unless
 * retain is set. Recovery is the only path that restores power, and only
 * after every DART provider, domain, page, and overlay flag is clear.
 */
#ifndef J713_ISP_DARTMAP_LIFE_H
#define J713_ISP_DARTMAP_LIFE_H

#include "j713_isp_dartmap_layout.h"

#ifndef __KERNEL__
#include <errno.h>
#endif

/* Acquired, but the step did not succeed. The flag stays set. */
#define J713_DM_ERR_OWNED		(-EBADE)

#define J713_DM_CLEAN_IDLE		0
#define J713_DM_CLEAN_COMPLETE		1
#define J713_DM_CLEAN_PENDING		2

#define J713_DM_MODE_MAP		1
#define J713_DM_MODE_RECOVER		2

#define J713_DM_F_POWER			(1u << 0)
#define J713_DM_F_DART_CLAIM		(1u << 1)
#define J713_DM_F_OTHER_CLAIM		(1u << 2)
#define J713_DM_F_DRIVER		(1u << 3)
#define J713_DM_F_OVERLAY		(1u << 4)
#define J713_DM_F_MASTER		(1u << 5)
#define J713_DM_F_DART_RO		(1u << 6)
#define J713_DM_F_PAGES			(1u << 7)
#define J713_DM_F_DOMAIN		(1u << 8)
#define J713_DM_F_ATTACHED		(1u << 9)
#define J713_DM_F_MAPPED		(1u << 10)

/* Anything that can still touch DART MMIO, a default domain, or a
 * scratch page. POWER and claim flags alone are not in this mask:
 * recovery must not read COPROC just because those claims are held.
 */
#define J713_DM_F_BLOCKS_POWER		(J713_DM_F_DRIVER | J713_DM_F_OVERLAY | \
					 J713_DM_F_MASTER | J713_DM_F_DART_RO | \
					 J713_DM_F_PAGES | J713_DM_F_DOMAIN | \
					 J713_DM_F_ATTACHED | J713_DM_F_MAPPED)
#define J713_DM_F_REFS			(J713_DM_F_MAPPED | J713_DM_F_ATTACHED)

enum j713_dm_op {
	J713_DM_OP_CPU = 0,
	J713_DM_OP_BASELINE,
	J713_DM_OP_RELEASE_DART,
	J713_DM_OP_REG_DRIVER,
	J713_DM_OP_APPLY_OVERLAY,
	J713_DM_OP_MASTER_BOUND,
	J713_DM_OP_REMAP_RO,
	J713_DM_OP_ALLOC_PAGES,
	J713_DM_OP_ALLOC_DOMAIN,
	J713_DM_OP_ATTACH,
	J713_DM_OP_MAP,
	J713_DM_OP_LOOKUP,
	J713_DM_OP_READBACK,
	J713_DM_OP_UNMAP,
	J713_DM_OP_DETACH,
	J713_DM_OP_FREE_DOMAIN,
	J713_DM_OP_FREE_PAGES,
	J713_DM_OP_UNMAP_RO,
	J713_DM_OP_UNBIND,
	J713_DM_OP_REMOVE_OVERLAY,
	J713_DM_OP_UNREG,
	J713_DM_OP_POWER_DOWN,
	J713_DM_OP_RELEASE_CLAIMS,
	J713_DM_OP_COUNT
};

#define J713_DM_STAGE_CPU		20
#define J713_DM_STAGE_BASELINE		21
#define J713_DM_STAGE_HANDOFF		22
#define J713_DM_STAGE_DRIVER		23
#define J713_DM_STAGE_OVERLAY		24
#define J713_DM_STAGE_BIND		25
#define J713_DM_STAGE_DART_RO		26
#define J713_DM_STAGE_PAGES		27
#define J713_DM_STAGE_DOMAIN		28
#define J713_DM_STAGE_ATTACH		29
#define J713_DM_STAGE_MAP		30
#define J713_DM_STAGE_LOOKUP		31
#define J713_DM_STAGE_READBACK		32
#define J713_DM_STAGE_UNMAP		33
#define J713_DM_STAGE_DETACH		34
#define J713_DM_STAGE_FREE_DOMAIN	35
#define J713_DM_STAGE_FREE_PAGES	36
#define J713_DM_STAGE_UNBIND		37
#define J713_DM_STAGE_OVERLAY_OFF	38
#define J713_DM_STAGE_UNREG		39
#define J713_DM_STAGE_POWER_OFF		40
#define J713_DM_STAGE_RELEASE		41

#define J713_DM_TRACE_MAX		96

struct j713_dm;

typedef int (*j713_dm_op_fn)(void *ctx, struct j713_dm *dm);

struct j713_dm_ops {
	j713_dm_op_fn cpu_stopped;
	j713_dm_op_fn dart_baseline;
	j713_dm_op_fn release_dart_claims;
	j713_dm_op_fn register_driver;
	j713_dm_op_fn apply_overlay;
	j713_dm_op_fn master_bound;
	j713_dm_op_fn remap_dart_ro;
	j713_dm_op_fn alloc_pages;
	j713_dm_op_fn alloc_domain;
	j713_dm_op_fn attach;
	j713_dm_op_fn map_iova;
	j713_dm_op_fn lookup;
	j713_dm_op_fn readback;
	j713_dm_op_fn unmap;
	j713_dm_op_fn detach;
	j713_dm_op_fn free_domain;
	j713_dm_op_fn free_pages;
	j713_dm_op_fn unmap_dart_ro;
	j713_dm_op_fn unbind_master;
	j713_dm_op_fn remove_overlay;
	j713_dm_op_fn unregister_driver;
	j713_dm_op_fn power_down;
	j713_dm_op_fn release_claims;
};

struct j713_dm {
	unsigned int flags;
	int retain;
	int first_error;
	int cleanup_error;
	int cleanup_state;
	int power_restored;
	int stage;
	int fail_stage;
	int mode;
	/* map_count is live mappings only. Teardown clears it. map_total is
	 * the cumulative number of pages successfully mapped and is the
	 * proof that a five-page experiment ran.
	 * cleanup_error is the first teardown or retention failure. It does
	 * not replace first_error.
	 */
	unsigned int map_count;
	unsigned int map_total;
	unsigned int lookup_count;
	unsigned int readback_count;
	unsigned int op_calls[J713_DM_OP_COUNT];
	unsigned int forward_calls[J713_DM_OP_COUNT];
	int trace_op[J713_DM_TRACE_MAX];
	unsigned int ntrace;
	unsigned int free_while_ref;
	unsigned int power_while_blocked;
	unsigned int saw_power_down;
};

static inline int j713_dm_power_blocked(const struct j713_dm *dm)
{
	return dm && (dm->flags & J713_DM_F_BLOCKS_POWER);
}

int j713_dm_run_powered(struct j713_dm *dm, const struct j713_dm_ops *ops,
			void *ctx);
int j713_dm_recover(struct j713_dm *dm, const struct j713_dm_ops *ops,
		    void *ctx);

#endif
