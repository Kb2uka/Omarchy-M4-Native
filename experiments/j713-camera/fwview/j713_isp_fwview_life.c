/* SPDX-License-Identifier: GPL-2.0-only */
#include "j713_isp_fwview_life.h"

static int call_op(struct j713_dm *dm, int op, j713_dm_op_fn fn, void *ctx,
		   int forward)
{
	if (!fn)
		return -EINVAL;
	if (dm->ntrace < J713_DM_TRACE_MAX)
		dm->trace_op[dm->ntrace++] = op;
	if (op >= 0 && op < J713_DM_OP_COUNT) {
		dm->op_calls[op]++;
		if (forward)
			dm->forward_calls[op]++;
	}
	return fn(ctx, dm);
}

static void note_fail(struct j713_dm *dm, int stage, int err)
{
	if (!err)
		err = -EIO;
	if (!dm->first_error)
		dm->first_error = err;
	if (!dm->fail_stage)
		dm->fail_stage = stage;
	dm->stage = stage;
}

static void mark_pending(struct j713_dm *dm, int stage, int err)
{
	if (!err)
		err = -EIO;
	if (!dm->cleanup_error)
		dm->cleanup_error = err;
	/* Experiment first_error and fail_stage stay as they were. */
	if (!dm->fail_stage)
		dm->stage = stage;
	dm->retain = 1;
	dm->cleanup_state = J713_DM_CLEAN_PENDING;
}

static int cpu_or_pending(struct j713_dm *dm, const struct j713_dm_ops *ops,
			  void *ctx)
{
	int rc = call_op(dm, J713_DM_OP_CPU, ops->cpu_stopped, ctx, 0);

	if (!rc)
		return 0;
	mark_pending(dm, J713_DM_STAGE_CPU, rc);
	return rc;
}

static int teardown_devices(struct j713_dm *dm, const struct j713_dm_ops *ops,
			    void *ctx)
{
	int rc;

	/* Providers and a default domain exist as soon as the overlay or
	 * master is live, before any allocated page. POWER|CLAIMS alone must
	 * not read COPROC: a partial enable can leave that block off.
	 */
	if (dm->flags & J713_DM_F_BLOCKS_POWER) {
		rc = cpu_or_pending(dm, ops, ctx);
		if (rc)
			return rc;
	}

	if (dm->flags & J713_DM_F_MAPPED) {
		rc = call_op(dm, J713_DM_OP_UNMAP, ops->unmap, ctx, 0);
		if (rc) {
			mark_pending(dm, J713_DM_STAGE_UNMAP, rc);
			return rc;
		}
		if (dm->map_count > dm->map_total)
			dm->map_total = dm->map_count;
		dm->flags &= ~J713_DM_F_MAPPED;
		dm->map_count = 0;
	}
	if (dm->flags & J713_DM_F_ATTACHED) {
		rc = call_op(dm, J713_DM_OP_DETACH, ops->detach, ctx, 0);
		if (rc) {
			mark_pending(dm, J713_DM_STAGE_DETACH, rc);
			return rc;
		}
		dm->flags &= ~J713_DM_F_ATTACHED;
		/* Quiesce only after detach has succeeded. Unmap and detach
		 * failures leave this flag clear so their retained masks
		 * stay the same.
		 */
		dm->flags |= J713_DM_F_NEED_QUIESCE;
	}
	if (dm->flags & J713_DM_F_NEED_QUIESCE) {
		rc = call_op(dm, J713_DM_OP_QUIESCE, ops->dart_quiesce, ctx, 0);
		if (rc) {
			mark_pending(dm, J713_DM_STAGE_QUIESCE, rc);
			return rc;
		}
		dm->flags &= ~J713_DM_F_NEED_QUIESCE;
	}
	if (dm->flags & J713_DM_F_DOMAIN) {
		if (dm->flags & J713_DM_F_REFS) {
			dm->free_while_ref++;
			mark_pending(dm, J713_DM_STAGE_FREE_DOMAIN, -EBUSY);
			return -EBUSY;
		}
		rc = call_op(dm, J713_DM_OP_FREE_DOMAIN, ops->free_domain, ctx, 0);
		if (rc) {
			mark_pending(dm, J713_DM_STAGE_FREE_DOMAIN, rc);
			return rc;
		}
		dm->flags &= ~J713_DM_F_DOMAIN;
	}
	if (dm->flags & J713_DM_F_PAGES) {
		if (dm->flags & (J713_DM_F_REFS | J713_DM_F_DOMAIN)) {
			dm->free_while_ref++;
			mark_pending(dm, J713_DM_STAGE_FREE_PAGES, -EBUSY);
			return -EBUSY;
		}
		rc = cpu_or_pending(dm, ops, ctx);
		if (rc)
			return rc;
		/* A nonzero heap or IPC byte stops the ladder. It does not
		 * retain: the pages are still freed and the overlay still
		 * comes off. Any other surface error keeps the pages.
		 */
		if (ops->check_surfaces) {
			rc = call_op(dm, J713_DM_OP_SURFACE, ops->check_surfaces,
				     ctx, 0);
			if (rc == J713_DM_ERR_LADDER) {
				dm->ladder_stop = rc;
				if (!dm->first_error)
					note_fail(dm, J713_DM_STAGE_SURFACE, rc);
			} else if (rc) {
				mark_pending(dm, J713_DM_STAGE_SURFACE, rc);
				return rc;
			}
		}
		rc = call_op(dm, J713_DM_OP_FREE_PAGES, ops->free_pages, ctx, 0);
		if (rc) {
			mark_pending(dm, J713_DM_STAGE_FREE_PAGES, rc);
			return rc;
		}
		dm->flags &= ~J713_DM_F_PAGES;
	}
	if (dm->flags & J713_DM_F_DART_RO) {
		rc = call_op(dm, J713_DM_OP_UNMAP_RO, ops->unmap_dart_ro, ctx, 0);
		if (rc) {
			mark_pending(dm, J713_DM_STAGE_DART_RO, rc);
			return rc;
		}
		dm->flags &= ~J713_DM_F_DART_RO;
	}
	if (dm->flags & J713_DM_F_MASTER) {
		if (dm->flags & (J713_DM_F_REFS | J713_DM_F_DOMAIN | J713_DM_F_PAGES)) {
			mark_pending(dm, J713_DM_STAGE_UNBIND, -EBUSY);
			return -EBUSY;
		}
		rc = call_op(dm, J713_DM_OP_UNBIND, ops->unbind_master, ctx, 0);
		if (rc) {
			mark_pending(dm, J713_DM_STAGE_UNBIND, rc);
			return rc;
		}
		dm->flags &= ~J713_DM_F_MASTER;
	}
	if (dm->flags & J713_DM_F_OVERLAY) {
		if (dm->flags & (J713_DM_F_MASTER | J713_DM_F_REFS | J713_DM_F_DOMAIN |
				 J713_DM_F_PAGES)) {
			mark_pending(dm, J713_DM_STAGE_OVERLAY_OFF, -EBUSY);
			return -EBUSY;
		}
		rc = call_op(dm, J713_DM_OP_REMOVE_OVERLAY, ops->remove_overlay, ctx, 0);
		if (rc) {
			mark_pending(dm, J713_DM_STAGE_OVERLAY_OFF, rc);
			return rc;
		}
		dm->flags &= ~J713_DM_F_OVERLAY;
	}
	if (dm->flags & J713_DM_F_DRIVER) {
		if (dm->flags & J713_DM_F_OVERLAY) {
			mark_pending(dm, J713_DM_STAGE_UNREG, -EBUSY);
			return -EBUSY;
		}
		rc = call_op(dm, J713_DM_OP_UNREG, ops->unregister_driver, ctx, 0);
		if (rc) {
			mark_pending(dm, J713_DM_STAGE_UNREG, rc);
			return rc;
		}
		dm->flags &= ~J713_DM_F_DRIVER;
	}
	return 0;
}

static int finish_power(struct j713_dm *dm, const struct j713_dm_ops *ops,
			void *ctx)
{
	int rc;

	if (j713_dm_power_blocked(dm)) {
		dm->power_while_blocked++;
		mark_pending(dm, J713_DM_STAGE_POWER_OFF, -EBUSY);
		return -EBUSY;
	}
	if (dm->flags & J713_DM_F_POWER) {
		dm->saw_power_down++;
		rc = call_op(dm, J713_DM_OP_POWER_DOWN, ops->power_down, ctx, 0);
		if (rc) {
			dm->cleanup_error = rc;
			mark_pending(dm, J713_DM_STAGE_POWER_OFF, rc);
			return rc;
		}
		dm->flags &= ~J713_DM_F_POWER;
		dm->power_restored = 1;
	}
	if (dm->flags & (J713_DM_F_OTHER_CLAIM | J713_DM_F_DART_CLAIM)) {
		rc = call_op(dm, J713_DM_OP_RELEASE_CLAIMS, ops->release_claims, ctx, 0);
		if (rc) {
			dm->cleanup_error = rc;
			mark_pending(dm, J713_DM_STAGE_RELEASE, rc);
			return rc;
		}
		dm->flags &= ~(J713_DM_F_OTHER_CLAIM | J713_DM_F_DART_CLAIM);
	}
	dm->retain = 0;
	dm->cleanup_state = J713_DM_CLEAN_COMPLETE;
	if (!dm->fail_stage)
		dm->stage = J713_ISP_STAGE_DONE;
	return 0;
}

static int owned_or_fail(struct j713_dm *dm, int rc, unsigned int flag, int stage)
{
	if (!rc) {
		dm->flags |= flag;
		return 0;
	}
	if (rc == J713_DM_ERR_OWNED) {
		dm->flags |= flag;
		note_fail(dm, stage, rc);
		return rc;
	}
	note_fail(dm, stage, rc);
	return rc;
}

static int held_or_error(const struct j713_dm *dm, int rc)
{
	if (dm->first_error)
		return dm->first_error;
	if (dm->cleanup_error)
		return dm->cleanup_error;
	if (rc)
		return rc;
	if (dm->retain)
		return -EBUSY;
	return 0;
}

static int begin_attempt(struct j713_dm *dm, int mode)
{
	unsigned int keep;
	int i;

	if (!dm)
		return -EINVAL;
	if (dm->retain || dm->cleanup_state == J713_DM_CLEAN_PENDING)
		return -EBUSY;
	if (!(dm->flags & J713_DM_F_POWER))
		return -EINVAL;
	if (dm->flags & J713_DM_F_BLOCKS_POWER)
		return -EBUSY;

	keep = dm->flags & (J713_DM_F_POWER | J713_DM_F_DART_CLAIM |
			    J713_DM_F_OTHER_CLAIM);
	dm->flags = keep;
	dm->retain = 0;
	dm->first_error = 0;
	dm->cleanup_state = J713_DM_CLEAN_IDLE;
	dm->cleanup_error = 0;
	dm->power_restored = 0;
	dm->fail_stage = 0;
	dm->stage = J713_DM_STAGE_CPU;
	dm->mode = mode;
	dm->map_count = 0;
	dm->map_total = 0;
	dm->lookup_count = 0;
	dm->readback_count = 0;
	dm->ladder_stop = 0;
	dm->ntrace = 0;
	dm->free_while_ref = 0;
	dm->power_while_blocked = 0;
	dm->saw_power_down = 0;
	for (i = 0; i < J713_DM_OP_COUNT; i++) {
		dm->op_calls[i] = 0;
		dm->forward_calls[i] = 0;
	}
	return 0;
}

static int after_overlay_cpu(struct j713_dm *dm, const struct j713_dm_ops *ops,
			     void *ctx)
{
	int rc = call_op(dm, J713_DM_OP_CPU, ops->cpu_stopped, ctx, 1);

	if (!rc)
		return 0;
	/* DART provider or pages may be live. Do not tear them down on RUN. */
	if (dm->flags & (J713_DM_F_OVERLAY | J713_DM_F_MASTER | J713_DM_F_REFS |
			 J713_DM_F_DOMAIN | J713_DM_F_PAGES)) {
		mark_pending(dm, J713_DM_STAGE_CPU, rc);
		return rc;
	}
	note_fail(dm, J713_DM_STAGE_CPU, rc);
	return rc;
}

int j713_dm_run_powered(struct j713_dm *dm, const struct j713_dm_ops *ops,
			void *ctx)
{
	int rc;

	if (!dm || !ops)
		return -EINVAL;
	rc = begin_attempt(dm, J713_DM_MODE_MAP);
	if (rc)
		return rc;

	rc = call_op(dm, J713_DM_OP_CPU, ops->cpu_stopped, ctx, 1);
	if (rc) {
		note_fail(dm, J713_DM_STAGE_CPU, rc);
		return dm->first_error;
	}
	rc = call_op(dm, J713_DM_OP_BASELINE, ops->dart_baseline, ctx, 1);
	if (rc) {
		note_fail(dm, J713_DM_STAGE_BASELINE, rc);
		return dm->first_error;
	}
	rc = call_op(dm, J713_DM_OP_RELEASE_DART, ops->release_dart_claims, ctx, 1);
	if (rc) {
		note_fail(dm, J713_DM_STAGE_HANDOFF, rc);
		return dm->first_error;
	}
	dm->flags &= ~J713_DM_F_DART_CLAIM;

	rc = owned_or_fail(dm,
			   call_op(dm, J713_DM_OP_REG_DRIVER, ops->register_driver, ctx, 1),
			   J713_DM_F_DRIVER, J713_DM_STAGE_DRIVER);
	if (rc)
		goto devices;

	rc = owned_or_fail(dm,
			   call_op(dm, J713_DM_OP_APPLY_OVERLAY, ops->apply_overlay, ctx, 1),
			   J713_DM_F_OVERLAY, J713_DM_STAGE_OVERLAY);
	if (rc)
		goto devices;

	rc = call_op(dm, J713_DM_OP_MASTER_BOUND, ops->master_bound, ctx, 1);
	if (rc) {
		note_fail(dm, J713_DM_STAGE_BIND, rc);
		goto devices;
	}
	dm->flags |= J713_DM_F_MASTER;

	rc = after_overlay_cpu(dm, ops, ctx);
	if (rc)
		return held_or_error(dm, rc);

	rc = owned_or_fail(dm,
			   call_op(dm, J713_DM_OP_REMAP_RO, ops->remap_dart_ro, ctx, 1),
			   J713_DM_F_DART_RO, J713_DM_STAGE_DART_RO);
	if (rc)
		goto devices;

	rc = owned_or_fail(dm,
			   call_op(dm, J713_DM_OP_ALLOC_PAGES, ops->alloc_pages, ctx, 1),
			   J713_DM_F_PAGES, J713_DM_STAGE_PAGES);
	if (rc)
		goto devices;

	rc = owned_or_fail(dm,
			   call_op(dm, J713_DM_OP_ALLOC_DOMAIN, ops->alloc_domain, ctx, 1),
			   J713_DM_F_DOMAIN, J713_DM_STAGE_DOMAIN);
	if (rc)
		goto devices;

	rc = call_op(dm, J713_DM_OP_PREMISE, ops->premise, ctx, 1);
	if (rc) {
		note_fail(dm, J713_DM_STAGE_PREMISE, rc);
		goto devices;
	}

	rc = owned_or_fail(dm,
			   call_op(dm, J713_DM_OP_ATTACH, ops->attach, ctx, 1),
			   J713_DM_F_ATTACHED, J713_DM_STAGE_ATTACH);
	if (rc)
		goto devices;

	rc = after_overlay_cpu(dm, ops, ctx);
	if (rc)
		return held_or_error(dm, rc);

	rc = call_op(dm, J713_DM_OP_MAP, ops->map_iova, ctx, 1);
	if (dm->map_count > dm->map_total)
		dm->map_total = dm->map_count;
	if (rc == 0) {
		dm->flags |= J713_DM_F_MAPPED;
	} else if (rc == J713_DM_ERR_OWNED || dm->map_count) {
		dm->flags |= J713_DM_F_MAPPED;
		note_fail(dm, J713_DM_STAGE_MAP, rc);
		goto devices;
	} else {
		note_fail(dm, J713_DM_STAGE_MAP, rc);
		goto devices;
	}

	rc = call_op(dm, J713_DM_OP_LOOKUP, ops->lookup, ctx, 1);
	if (rc) {
		note_fail(dm, J713_DM_STAGE_LOOKUP, rc);
		goto devices;
	}
	rc = call_op(dm, J713_DM_OP_READBACK, ops->readback, ctx, 1);
	if (rc) {
		note_fail(dm, J713_DM_STAGE_READBACK, rc);
		goto devices;
	}

	rc = after_overlay_cpu(dm, ops, ctx);
	if (rc)
		return held_or_error(dm, rc);

devices:
	if (dm->retain)
		return held_or_error(dm, 0);
	rc = teardown_devices(dm, ops, ctx);
	if (rc || dm->retain)
		return held_or_error(dm, rc);
	if (!dm->first_error)
		dm->stage = J713_ISP_STAGE_DONE;
	dm->cleanup_state = J713_DM_CLEAN_IDLE;
	return dm->first_error;
}

int j713_dm_run_survey(struct j713_dm *dm, const struct j713_dm_ops *ops,
		       void *ctx)
{
	int rc;

	if (!dm || !ops)
		return -EINVAL;
	rc = begin_attempt(dm, J713_DM_MODE_SURVEY);
	if (rc)
		return rc;

	rc = call_op(dm, J713_DM_OP_CPU, ops->cpu_stopped, ctx, 1);
	if (rc) {
		note_fail(dm, J713_DM_STAGE_CPU, rc);
		return dm->first_error;
	}
	rc = call_op(dm, J713_DM_OP_SURVEY, ops->dart_survey, ctx, 1);
	if (rc) {
		note_fail(dm, J713_DM_STAGE_SURVEY, rc);
		return dm->first_error;
	}
	dm->stage = J713_ISP_STAGE_DONE;
	return 0;
}

int j713_dm_recover(struct j713_dm *dm, const struct j713_dm_ops *ops, void *ctx)
{
	int rc;
	unsigned int before_map;
	unsigned int before_attach;
	unsigned int before_overlay;
	unsigned int before_power_up_calls;

	if (!dm || !ops)
		return -EINVAL;
	if (!dm->retain && dm->cleanup_state != J713_DM_CLEAN_PENDING &&
	    !(dm->flags & (J713_DM_F_BLOCKS_POWER | J713_DM_F_POWER |
			   J713_DM_F_OTHER_CLAIM | J713_DM_F_DART_CLAIM))) {
		dm->cleanup_state = J713_DM_CLEAN_IDLE;
		return 0;
	}
	/* Recovery never maps, attaches, or applies an overlay. */
	before_map = dm->forward_calls[J713_DM_OP_MAP];
	before_attach = dm->forward_calls[J713_DM_OP_ATTACH];
	before_overlay = dm->forward_calls[J713_DM_OP_APPLY_OVERLAY];
	before_power_up_calls = dm->forward_calls[J713_DM_OP_REG_DRIVER];
	dm->mode = J713_DM_MODE_RECOVER;
	rc = teardown_devices(dm, ops, ctx);
	if (dm->forward_calls[J713_DM_OP_MAP] != before_map ||
	    dm->forward_calls[J713_DM_OP_ATTACH] != before_attach ||
	    dm->forward_calls[J713_DM_OP_APPLY_OVERLAY] != before_overlay ||
	    dm->forward_calls[J713_DM_OP_REG_DRIVER] != before_power_up_calls) {
		mark_pending(dm, J713_DM_STAGE_MAP, -EPROTO);
		return -EPROTO;
	}
	/* retain was set by the failed map. A teardown that just succeeded
	 * has dropped every device flag, so that stale retain must not skip
	 * the power restore. A failure in this call leaves retain set.
	 */
	if (rc)
		return -EBUSY;
	dm->retain = 0;
	rc = finish_power(dm, ops, ctx);
	if (rc || dm->retain)
		return -EBUSY;
	return 0;
}
