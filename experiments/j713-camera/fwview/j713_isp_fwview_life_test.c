/* Included from j713_isp_probe_mock.c after life.c. No second main.
 * The preflight cases match ../dartmap-life-preflight/check_recovery.c.
 * struct life_fake is complete before life_step reads its fields.
 */
struct life_fake {
	int fail_op;
	int fail_err;
	int map_owned;
	int force_owned_op;
	unsigned int cpu_fail_mask;
	unsigned int cpu_fail_at;
	unsigned int map_partial;
	unsigned int unmap_left;
	int rearm_mapped_on_detach;
	u32 fabric[4];
	u32 dart_error[3];
	int heap_dirty;
	int text_lookup_bad;
};

static int life_step(void *ctx, struct j713_dm *dm, int op)
{
	struct life_fake *f = ctx;

	/* op_calls[op] is already incremented by the helper. */
	if (op == J713_DM_OP_CPU && f->cpu_fail_mask &&
	    (dm->flags & f->cpu_fail_mask))
		return -EBUSY;
	if (op == J713_DM_OP_CPU && f->cpu_fail_at &&
	    dm->op_calls[J713_DM_OP_CPU] == f->cpu_fail_at)
		return -EBUSY;
	if (f->force_owned_op >= 0 && op == f->force_owned_op) {
		if (op == J713_DM_OP_MAP)
			dm->map_count = f->map_partial ? f->map_partial : 2u;
		return J713_DM_ERR_OWNED;
	}
	/* These run before fail_op so a matrix injection on PREMISE still
	 * returns the injected error when the premise words are clean.
	 */
	if (op == J713_DM_OP_PREMISE) {
		int premise = j713_dm_premise_rc(f->fabric, f->dart_error);

		if (premise)
			return premise;
	}
	if (op == J713_DM_OP_SURFACE && f->heap_dirty)
		return J713_DM_ERR_LADDER;
	if (op == J713_DM_OP_LOOKUP && f->text_lookup_bad) {
		dm->lookup_count = 0;
		return -EIO;
	}
	if (op == f->fail_op) {
		if (op == J713_DM_OP_MAP && f->map_owned) {
			dm->map_count = 2;
			return J713_DM_ERR_OWNED;
		}
		if (op == J713_DM_OP_MAP && f->map_partial) {
			dm->map_count = f->map_partial;
			return f->fail_err ? f->fail_err : -EIO;
		}
		if (op == J713_DM_OP_UNMAP && f->unmap_left) {
			dm->map_count = f->unmap_left;
			return f->fail_err ? f->fail_err : -EIO;
		}
		return f->fail_err ? f->fail_err : -EIO;
	}
	if (op == J713_DM_OP_DETACH && f->rearm_mapped_on_detach)
		dm->flags |= J713_DM_F_MAPPED;
	if (op == J713_DM_OP_MAP)
		dm->map_count = J713_DM_PAGE_COUNT;
	if (op == J713_DM_OP_LOOKUP)
		dm->lookup_count = J713_DM_PAGE_COUNT;
	if (op == J713_DM_OP_READBACK)
		dm->readback_count = J713_ISP_DART_INSTANCES * 2u;
	return 0;
}

#define LIFE_STEP(name, id) \
	static int name(void *ctx, struct j713_dm *dm) \
	{ return life_step(ctx, dm, id); }

LIFE_STEP(life_cpu, J713_DM_OP_CPU)
LIFE_STEP(life_base, J713_DM_OP_BASELINE)
LIFE_STEP(life_rel_dart, J713_DM_OP_RELEASE_DART)
LIFE_STEP(life_reg, J713_DM_OP_REG_DRIVER)
LIFE_STEP(life_apply, J713_DM_OP_APPLY_OVERLAY)
LIFE_STEP(life_bound, J713_DM_OP_MASTER_BOUND)
LIFE_STEP(life_ro, J713_DM_OP_REMAP_RO)
LIFE_STEP(life_pages, J713_DM_OP_ALLOC_PAGES)
LIFE_STEP(life_dom, J713_DM_OP_ALLOC_DOMAIN)
LIFE_STEP(life_premise, J713_DM_OP_PREMISE)
LIFE_STEP(life_att, J713_DM_OP_ATTACH)
LIFE_STEP(life_map, J713_DM_OP_MAP)
LIFE_STEP(life_look, J713_DM_OP_LOOKUP)
LIFE_STEP(life_rb, J713_DM_OP_READBACK)
LIFE_STEP(life_unmap, J713_DM_OP_UNMAP)
LIFE_STEP(life_det, J713_DM_OP_DETACH)
LIFE_STEP(life_quiesce, J713_DM_OP_QUIESCE)
LIFE_STEP(life_fd, J713_DM_OP_FREE_DOMAIN)
LIFE_STEP(life_surface, J713_DM_OP_SURFACE)
LIFE_STEP(life_fp, J713_DM_OP_FREE_PAGES)
LIFE_STEP(life_uro, J713_DM_OP_UNMAP_RO)
LIFE_STEP(life_unb, J713_DM_OP_UNBIND)
LIFE_STEP(life_rm, J713_DM_OP_REMOVE_OVERLAY)
LIFE_STEP(life_unreg, J713_DM_OP_UNREG)
LIFE_STEP(life_pd, J713_DM_OP_POWER_DOWN)
LIFE_STEP(life_rc, J713_DM_OP_RELEASE_CLAIMS)
LIFE_STEP(life_survey, J713_DM_OP_SURVEY)

static const struct j713_dm_ops life_ops = {
	.cpu_stopped = life_cpu,
	.dart_baseline = life_base,
	.release_dart_claims = life_rel_dart,
	.register_driver = life_reg,
	.apply_overlay = life_apply,
	.master_bound = life_bound,
	.remap_dart_ro = life_ro,
	.alloc_pages = life_pages,
	.alloc_domain = life_dom,
	.premise = life_premise,
	.attach = life_att,
	.map_iova = life_map,
	.lookup = life_look,
	.readback = life_rb,
	.unmap = life_unmap,
	.detach = life_det,
	.dart_quiesce = life_quiesce,
	.free_domain = life_fd,
	.check_surfaces = life_surface,
	.free_pages = life_fp,
	.unmap_dart_ro = life_uro,
	.unbind_master = life_unb,
	.remove_overlay = life_rm,
	.unregister_driver = life_unreg,
	.power_down = life_pd,
	.release_claims = life_rc,
	.dart_survey = life_survey,
};

static void life_reset(struct j713_dm *dm, struct life_fake *f)
{
	memset(dm, 0, sizeof(*dm));
	memset(f, 0, sizeof(*f));
	f->fail_op = -1;
	f->force_owned_op = -1;
	dm->flags = J713_DM_F_POWER | J713_DM_F_DART_CLAIM | J713_DM_F_OTHER_CLAIM;
}

static int trace_has(const struct j713_dm *dm, int op)
{
	unsigned int i;

	for (i = 0; i < dm->ntrace; i++) {
		if (dm->trace_op[i] == op)
			return 1;
	}
	return 0;
}

static int trace_before(const struct j713_dm *dm, int earlier, int later)
{
	unsigned int i;
	int at = -1;

	for (i = 0; i < dm->ntrace; i++) {
		if (dm->trace_op[i] == earlier)
			at = (int)i;
		if (dm->trace_op[i] == later)
			return at >= 0 && at < (int)i;
	}
	return 0;
}

static void assert_cleanup_not_forward(const struct j713_dm *dm)
{
	CHECK(dm->forward_calls[J713_DM_OP_UNMAP] == 0);
	CHECK(dm->forward_calls[J713_DM_OP_DETACH] == 0);
	CHECK(dm->forward_calls[J713_DM_OP_QUIESCE] == 0);
	CHECK(dm->forward_calls[J713_DM_OP_FREE_DOMAIN] == 0);
	CHECK(dm->forward_calls[J713_DM_OP_SURFACE] == 0);
	CHECK(dm->forward_calls[J713_DM_OP_FREE_PAGES] == 0);
	CHECK(dm->forward_calls[J713_DM_OP_UNMAP_RO] == 0);
	CHECK(dm->forward_calls[J713_DM_OP_UNBIND] == 0);
	CHECK(dm->forward_calls[J713_DM_OP_REMOVE_OVERLAY] == 0);
	CHECK(dm->forward_calls[J713_DM_OP_UNREG] == 0);
	CHECK(dm->forward_calls[J713_DM_OP_POWER_DOWN] == 0);
	CHECK(dm->forward_calls[J713_DM_OP_RELEASE_CLAIMS] == 0);
	CHECK(dm->forward_calls[J713_DM_OP_MAP] <= dm->op_calls[J713_DM_OP_MAP]);
	CHECK(dm->forward_calls[J713_DM_OP_ATTACH] <= dm->op_calls[J713_DM_OP_ATTACH]);
	CHECK(dm->forward_calls[J713_DM_OP_APPLY_OVERLAY] <=
	      dm->op_calls[J713_DM_OP_APPLY_OVERLAY]);
	CHECK(dm->forward_calls[J713_DM_OP_REG_DRIVER] <=
	      dm->op_calls[J713_DM_OP_REG_DRIVER]);
}

static void assert_teardown_order(const struct j713_dm *dm)
{
	if (trace_has(dm, J713_DM_OP_UNMAP) && trace_has(dm, J713_DM_OP_DETACH))
		CHECK(trace_before(dm, J713_DM_OP_UNMAP, J713_DM_OP_DETACH));
	if (trace_has(dm, J713_DM_OP_DETACH) &&
	    trace_has(dm, J713_DM_OP_QUIESCE))
		CHECK(trace_before(dm, J713_DM_OP_DETACH, J713_DM_OP_QUIESCE));
	if (trace_has(dm, J713_DM_OP_QUIESCE) &&
	    trace_has(dm, J713_DM_OP_FREE_DOMAIN))
		CHECK(trace_before(dm, J713_DM_OP_QUIESCE, J713_DM_OP_FREE_DOMAIN));
	if (trace_has(dm, J713_DM_OP_QUIESCE) &&
	    trace_has(dm, J713_DM_OP_UNBIND))
		CHECK(trace_before(dm, J713_DM_OP_QUIESCE, J713_DM_OP_UNBIND));
	if (trace_has(dm, J713_DM_OP_QUIESCE) &&
	    trace_has(dm, J713_DM_OP_REMOVE_OVERLAY))
		CHECK(trace_before(dm, J713_DM_OP_QUIESCE, J713_DM_OP_REMOVE_OVERLAY));
	if (trace_has(dm, J713_DM_OP_DETACH) &&
	    trace_has(dm, J713_DM_OP_FREE_DOMAIN))
		CHECK(trace_before(dm, J713_DM_OP_DETACH, J713_DM_OP_FREE_DOMAIN));
	if (trace_has(dm, J713_DM_OP_FREE_DOMAIN) &&
	    trace_has(dm, J713_DM_OP_SURFACE))
		CHECK(trace_before(dm, J713_DM_OP_FREE_DOMAIN, J713_DM_OP_SURFACE));
	if (trace_has(dm, J713_DM_OP_SURFACE) &&
	    trace_has(dm, J713_DM_OP_FREE_PAGES))
		CHECK(trace_before(dm, J713_DM_OP_SURFACE, J713_DM_OP_FREE_PAGES));
	if (trace_has(dm, J713_DM_OP_FREE_DOMAIN) &&
	    trace_has(dm, J713_DM_OP_FREE_PAGES))
		CHECK(trace_before(dm, J713_DM_OP_FREE_DOMAIN, J713_DM_OP_FREE_PAGES));
	if (trace_has(dm, J713_DM_OP_FREE_PAGES) &&
	    trace_has(dm, J713_DM_OP_UNMAP_RO))
		CHECK(trace_before(dm, J713_DM_OP_FREE_PAGES, J713_DM_OP_UNMAP_RO));
	if (trace_has(dm, J713_DM_OP_UNMAP_RO) && trace_has(dm, J713_DM_OP_UNBIND))
		CHECK(trace_before(dm, J713_DM_OP_UNMAP_RO, J713_DM_OP_UNBIND));
	if (trace_has(dm, J713_DM_OP_UNBIND) &&
	    trace_has(dm, J713_DM_OP_REMOVE_OVERLAY))
		CHECK(trace_before(dm, J713_DM_OP_UNBIND, J713_DM_OP_REMOVE_OVERLAY));
	if (trace_has(dm, J713_DM_OP_REMOVE_OVERLAY) &&
	    trace_has(dm, J713_DM_OP_UNREG))
		CHECK(trace_before(dm, J713_DM_OP_REMOVE_OVERLAY, J713_DM_OP_UNREG));
	if (trace_has(dm, J713_DM_OP_UNREG) && trace_has(dm, J713_DM_OP_POWER_DOWN))
		CHECK(trace_before(dm, J713_DM_OP_UNREG, J713_DM_OP_POWER_DOWN));
	if (trace_has(dm, J713_DM_OP_POWER_DOWN) &&
	    trace_has(dm, J713_DM_OP_RELEASE_CLAIMS))
		CHECK(trace_before(dm, J713_DM_OP_POWER_DOWN, J713_DM_OP_RELEASE_CLAIMS));
}

static void assert_no_free_while_live(const struct j713_dm *dm)
{
	if (dm->flags & (J713_DM_F_MAPPED | J713_DM_F_ATTACHED)) {
		CHECK(dm->op_calls[J713_DM_OP_FREE_DOMAIN] == 0);
		CHECK(dm->op_calls[J713_DM_OP_FREE_PAGES] == 0);
	}
	if (dm->flags & J713_DM_F_DOMAIN)
		CHECK(dm->op_calls[J713_DM_OP_FREE_PAGES] == 0);
	if (dm->flags & J713_DM_F_BLOCKS_POWER) {
		CHECK(dm->op_calls[J713_DM_OP_POWER_DOWN] == 0);
		CHECK(dm->saw_power_down == 0);
		CHECK(dm->power_restored == 0);
	}
}

static void assert_power_held(const struct j713_dm *dm)
{
	CHECK(dm->flags & J713_DM_F_POWER);
	CHECK(dm->op_calls[J713_DM_OP_POWER_DOWN] == 0);
	CHECK(dm->saw_power_down == 0);
	CHECK(dm->power_restored == 0);
	assert_cleanup_not_forward(dm);
	assert_teardown_order(dm);
	assert_no_free_while_live(dm);
}

static void test_layout_math(void)
{
	u32 fabric[4] = { 0, 0, 0, 0 };
	u32 error[3] = { 0, 0, 0 };

	CHECK(J713_DM_PAGE_SIZE == 0x4000u);
	CHECK(J713_DM_TEXT_IOVA == 0x10000000000ull);
	CHECK(J713_DM_TEXT_PHYS == 0x10000d20000ull);
	CHECK(J713_DM_TEXT_SIZE == 0xca4000ull);
	CHECK(J713_DM_TEXT_PAGES == 0x329u);
	CHECK(J713_DM_DATA_IOVA == 0x10000ca4000ull);
	CHECK(J713_DM_DATA_PHYS == 0x10001f30000ull);
	CHECK(J713_DM_DATA_SIZE == 0x748000ull);
	CHECK(J713_DM_DATA_PAGES == 0x1d2u);
	CHECK(J713_DM_HEAP_IOVA == 0x100013ec000ull);
	CHECK(J713_DM_HEAP_PAGES == 5u);
	CHECK(J713_DM_IPC_IOVA == 0x10001400000ull);
	CHECK(J713_DM_IPC_PAGES == 7u);
	CHECK(J713_DM_ALLOC_PAGES == 12u);
	CHECK(J713_DM_PAGE_COUNT == 0x329u + 0x1d2u + 5u + 7u);
	CHECK(J713_DM_TEXT_IOVA + J713_DM_TEXT_SIZE == J713_DM_DATA_IOVA);
	CHECK(J713_DM_DATA_IOVA + J713_DM_DATA_SIZE == J713_DM_HEAP_IOVA);
	CHECK(J713_DM_HEAP_IOVA + J713_DM_HEAP_SIZE == J713_DM_IPC_IOVA);
	CHECK(j713_dm_text_iova(1) == 0x10000004000ull);
	CHECK(j713_dm_text_phys(1) == 0x10000d24000ull);
	CHECK(j713_dm_heap_iova(4) + 0x4000ull == 0x10001400000ull);
	CHECK(j713_dm_phys_is_preloaded(0x10000d20000ull));
	CHECK(j713_dm_phys_is_preloaded(0x10001f30000ull)); /* DATA start */
	/* Heap IOVA value falls inside TEXT phys; IOVA != phys. */
	CHECK(j713_dm_phys_is_preloaded(0x100013ec000ull));
	CHECK(!j713_dm_phys_is_preloaded(0x10000d1c000ull)); /* page before TEXT */
	CHECK(!j713_dm_phys_is_preloaded(0x100019c4000ull)); /* TEXT end */
	CHECK(!j713_dm_phys_is_preloaded(0x10002678000ull)); /* DATA end */
	CHECK(j713_dm_phys_is_preloaded(0x100019c0000ull)); /* last TEXT page */
	CHECK(!j713_dm_phys_is_preloaded(0ull));
	CHECK(J713_DM_HW_IAS == 42u);
	CHECK(J713_DM_HW_OAS == 42u);
	CHECK(J713_DM_EFFECTIVE_IAS == 41u);
	CHECK(J713_DM_TABLE_LEVELS == 4);
	CHECK(J713_DM_DMA_BASE == 0x10000000000ull);
	CHECK(J713_DM_DMA_SIZE == 0xe0000000ull);
	CHECK(J713_DM_DMA_SIZE != (1ull << 36));
	CHECK(j713_dm_dma_last() == 0x100dfffffffull);
	CHECK(j713_dm_fls64((1ull << 36) - 1ull) == 36u);
	CHECK(j713_dm_effective_ias(42u, (1ull << 36) - 1ull) == 36u);
	CHECK(j713_dm_effective_ias(42u, j713_dm_dma_last()) == 41u);
	CHECK(j713_dm_levels(36u, 14u) == 3);
	CHECK(j713_dm_levels(41u, 14u) == 4);
	CHECK(j713_dm_levels(42u, 14u) == 4);
	CHECK(J713_DM_TCR_TRANSLATE == 1u);
	CHECK(J713_DM_TCR_FOUR_LEVEL == 8u);
	CHECK(J713_DM_TCR_4LEVEL == 9u);
	CHECK(J713_DM_TTBR_VALID == 1u);
	CHECK(j713_dm_tcr_is_4level(0x9u));
	CHECK(!j713_dm_tcr_is_4level(0x1u));
	CHECK(!j713_dm_tcr_is_4level(0x8u));
	CHECK(!j713_dm_tcr_is_4level(0u));
	CHECK(J713_DM_DART_SID15_TCR == 0x103cu);
	CHECK(j713_dm_premise_rc(fabric, error) == 0);
	fabric[0] = 1u;
	CHECK(j713_dm_premise_rc(fabric, error) == J713_DM_ERR_FABRIC);
	fabric[0] = 0u;
	error[0] = 0x7fffffffu;
	CHECK(j713_dm_premise_rc(fabric, error) == J713_DM_ERR_DART_FLAG);
	error[0] = 1u;
	CHECK(j713_dm_premise_rc(fabric, error) == J713_DM_ERR_DART_FLAG);
	error[0] = 0x80000000u;
	CHECK(j713_dm_premise_rc(fabric, error) == J713_DM_ERR_DART_FLAG);
	CHECK(J713_DM_CPU_RUN == 0x10u);
	CHECK(J713_DM_CPU_RUNNING == 1u);
	CHECK(J713_DM_CPU_STOPPED == 2u);
	CHECK((0x0330006au & 0x10u) == 0u);
	CHECK((0x0330006au & 1u) == 0u);
	CHECK((0x0330006au & 2u) == 2u);
	CHECK(j713_dm_cpu_stopped(0, 0x0330006au));
	CHECK(!j713_dm_cpu_stopped(0x10u, 0x0330006au));
	CHECK(!j713_dm_cpu_stopped(0, 1u | 2u));
}

static void test_life_happy(void)
{
	struct j713_dm dm;
	struct life_fake f;
	static const int expect[] = {
		J713_DM_OP_CPU, J713_DM_OP_BASELINE, J713_DM_OP_RELEASE_DART,
		J713_DM_OP_REG_DRIVER, J713_DM_OP_APPLY_OVERLAY,
		J713_DM_OP_MASTER_BOUND, J713_DM_OP_CPU, J713_DM_OP_REMAP_RO,
		J713_DM_OP_ALLOC_PAGES, J713_DM_OP_ALLOC_DOMAIN, J713_DM_OP_PREMISE,
		J713_DM_OP_ATTACH, J713_DM_OP_CPU, J713_DM_OP_MAP, J713_DM_OP_LOOKUP,
		J713_DM_OP_READBACK, J713_DM_OP_CPU, J713_DM_OP_CPU,
		J713_DM_OP_UNMAP, J713_DM_OP_DETACH, J713_DM_OP_QUIESCE,
		J713_DM_OP_FREE_DOMAIN,
		J713_DM_OP_CPU, J713_DM_OP_SURFACE, J713_DM_OP_FREE_PAGES,
		J713_DM_OP_UNMAP_RO,
		J713_DM_OP_UNBIND, J713_DM_OP_REMOVE_OVERLAY, J713_DM_OP_UNREG
	};
	unsigned int i;

	life_reset(&dm, &f);
	CHECK(j713_dm_run_powered(&dm, &life_ops, &f) == 0);
	CHECK(dm.ntrace == ARRAY_SIZE(expect));
	for (i = 0; i < ARRAY_SIZE(expect) && i < dm.ntrace; i++)
		CHECK(dm.trace_op[i] == expect[i]);
	CHECK(dm.flags == (J713_DM_F_POWER | J713_DM_F_OTHER_CLAIM));
	CHECK(!(dm.flags & J713_DM_F_DART_CLAIM));
	CHECK(dm.map_total == J713_DM_PAGE_COUNT);
	CHECK(dm.map_count == 0);
	CHECK(dm.lookup_count == J713_DM_PAGE_COUNT);
	CHECK(dm.readback_count == J713_ISP_DART_INSTANCES * 2u);
	CHECK(dm.retain == 0);
	CHECK(dm.saw_power_down == 0);
	CHECK(dm.op_calls[J713_DM_OP_POWER_DOWN] == 0);
	CHECK(dm.free_while_ref == 0);
	CHECK(dm.first_error == 0);
	CHECK(dm.cleanup_error == 0);
	CHECK(dm.cleanup_state == J713_DM_CLEAN_IDLE);
	CHECK(dm.power_restored == 0);
	CHECK(dm.ladder_stop == 0);
	CHECK(dm.op_calls[J713_DM_OP_PREMISE] == 1);
	CHECK(dm.forward_calls[J713_DM_OP_PREMISE] == 1);
	CHECK(dm.op_calls[J713_DM_OP_SURFACE] == 1);
	CHECK(dm.forward_calls[J713_DM_OP_SURFACE] == 0);
	CHECK(trace_before(&dm, J713_DM_OP_ALLOC_DOMAIN, J713_DM_OP_PREMISE));
	CHECK(trace_before(&dm, J713_DM_OP_PREMISE, J713_DM_OP_ATTACH));
	CHECK(trace_before(&dm, J713_DM_OP_FREE_DOMAIN, J713_DM_OP_SURFACE));
	CHECK(trace_before(&dm, J713_DM_OP_SURFACE, J713_DM_OP_FREE_PAGES));
	CHECK(dm.forward_calls[J713_DM_OP_CPU] == 4);
	CHECK(dm.op_calls[J713_DM_OP_CPU] == 6);
	CHECK(dm.forward_calls[J713_DM_OP_MAP] == 1);
	CHECK(dm.forward_calls[J713_DM_OP_UNMAP] == 0);
	CHECK(dm.op_calls[J713_DM_OP_UNMAP] == 1);
	CHECK(trace_before(&dm, J713_DM_OP_UNMAP, J713_DM_OP_DETACH));
	CHECK(trace_before(&dm, J713_DM_OP_DETACH, J713_DM_OP_QUIESCE));
	CHECK(trace_before(&dm, J713_DM_OP_QUIESCE, J713_DM_OP_FREE_DOMAIN));
	CHECK(dm.op_calls[J713_DM_OP_QUIESCE] == 1);
	CHECK(dm.forward_calls[J713_DM_OP_QUIESCE] == 0);
	CHECK(dm.op_calls[J713_DM_OP_SURVEY] == 0);
	CHECK(!(dm.flags & J713_DM_F_NEED_QUIESCE));
	CHECK(trace_before(&dm, J713_DM_OP_FREE_DOMAIN, J713_DM_OP_FREE_PAGES));
	CHECK(trace_before(&dm, J713_DM_OP_FREE_PAGES, J713_DM_OP_UNMAP_RO));
	CHECK(trace_before(&dm, J713_DM_OP_UNMAP_RO, J713_DM_OP_UNBIND));
	CHECK(trace_before(&dm, J713_DM_OP_UNBIND, J713_DM_OP_REMOVE_OVERLAY));
	CHECK(trace_before(&dm, J713_DM_OP_REMOVE_OVERLAY, J713_DM_OP_UNREG));
	CHECK(!trace_has(&dm, J713_DM_OP_POWER_DOWN));
	assert_power_held(&dm);
	dm.flags &= ~(J713_DM_F_POWER | J713_DM_F_OTHER_CLAIM);
	CHECK(j713_dm_recover(&dm, &life_ops, &f) == 0);
	CHECK(dm.op_calls[J713_DM_OP_POWER_DOWN] == 0);
	CHECK(dm.saw_power_down == 0);
}

static void test_life_early_fail_restores_via_caller(void)
{
	static const int ops[] = {
		J713_DM_OP_CPU, J713_DM_OP_BASELINE, J713_DM_OP_RELEASE_DART,
		J713_DM_OP_REG_DRIVER
	};
	unsigned int i;

	for (i = 0; i < ARRAY_SIZE(ops); i++) {
		struct j713_dm dm;
		struct life_fake f;

		life_reset(&dm, &f);
		f.fail_op = ops[i];
		f.fail_err = -EIO;
		CHECK(j713_dm_run_powered(&dm, &life_ops, &f) == -EIO);
		CHECK(dm.retain == 0);
		CHECK(dm.op_calls[J713_DM_OP_POWER_DOWN] == 0);
		CHECK(dm.op_calls[J713_DM_OP_FREE_PAGES] == 0);
		CHECK(dm.op_calls[J713_DM_OP_APPLY_OVERLAY] == 0);
		CHECK(dm.op_calls[J713_DM_OP_UNREG] == 0);
		CHECK(!(dm.flags & J713_DM_F_OVERLAY));
		CHECK(dm.first_error == -EIO);
		CHECK(dm.cleanup_error == 0);
		CHECK(dm.power_restored == 0);
		CHECK(dm.flags & J713_DM_F_POWER);
		if (ops[i] == J713_DM_OP_REG_DRIVER)
			CHECK(!(dm.flags & J713_DM_F_DART_CLAIM));
		else
			CHECK(dm.flags & J713_DM_F_DART_CLAIM);
		assert_power_held(&dm);
	}
}

static void test_life_overlay_owned_then_removed(void)
{
	struct j713_dm dm;
	struct life_fake f;

	life_reset(&dm, &f);
	f.fail_op = J713_DM_OP_APPLY_OVERLAY;
	f.fail_err = J713_DM_ERR_OWNED;
	CHECK(j713_dm_run_powered(&dm, &life_ops, &f) == J713_DM_ERR_OWNED);
	CHECK(dm.retain == 0);
	CHECK(dm.op_calls[J713_DM_OP_REMOVE_OVERLAY] == 1);
	CHECK(dm.op_calls[J713_DM_OP_UNREG] == 1);
	CHECK(dm.op_calls[J713_DM_OP_POWER_DOWN] == 0);
	CHECK(!(dm.flags & J713_DM_F_OVERLAY));
	CHECK(dm.flags == (J713_DM_F_POWER | J713_DM_F_OTHER_CLAIM));
}

static void test_life_teardown_failure_retains(void)
{
	struct j713_dm dm;
	struct life_fake f;

	life_reset(&dm, &f);
	f.fail_op = J713_DM_OP_UNMAP;
	f.fail_err = -EIO;
	CHECK(j713_dm_run_powered(&dm, &life_ops, &f) == -EIO);
	CHECK(dm.retain == 1);
	CHECK(dm.first_error == 0);
	CHECK(dm.cleanup_error == -EIO);
	CHECK(dm.flags & J713_DM_F_MAPPED);
	CHECK(dm.flags & J713_DM_F_OVERLAY);
	CHECK(dm.flags & J713_DM_F_POWER);
	CHECK(dm.map_total == J713_DM_PAGE_COUNT);
	CHECK(dm.op_calls[J713_DM_OP_FREE_PAGES] == 0);
	CHECK(dm.op_calls[J713_DM_OP_POWER_DOWN] == 0);
	CHECK(dm.free_while_ref == 0);

	f.fail_op = -1;
	{
		unsigned int map_fwd = dm.forward_calls[J713_DM_OP_MAP];
		unsigned int att_fwd = dm.forward_calls[J713_DM_OP_ATTACH];
		unsigned int ov_fwd = dm.forward_calls[J713_DM_OP_APPLY_OVERLAY];
		unsigned int reg_fwd = dm.forward_calls[J713_DM_OP_REG_DRIVER];

		CHECK(j713_dm_recover(&dm, &life_ops, &f) == 0);
		CHECK(dm.forward_calls[J713_DM_OP_MAP] == map_fwd);
		CHECK(dm.forward_calls[J713_DM_OP_ATTACH] == att_fwd);
		CHECK(dm.forward_calls[J713_DM_OP_APPLY_OVERLAY] == ov_fwd);
		CHECK(dm.forward_calls[J713_DM_OP_REG_DRIVER] == reg_fwd);
	}
	CHECK(dm.op_calls[J713_DM_OP_POWER_DOWN] == 1);
	CHECK(dm.saw_power_down == 1);
	CHECK(dm.power_restored == 1);
	CHECK(dm.retain == 0);
	CHECK(dm.flags == 0);
	CHECK(dm.map_total == J713_DM_PAGE_COUNT);
	CHECK(dm.cleanup_state == J713_DM_CLEAN_COMPLETE);
	CHECK(j713_dm_recover(&dm, &life_ops, &f) == 0);
	CHECK(dm.op_calls[J713_DM_OP_POWER_DOWN] == 1);
	CHECK(dm.op_calls[J713_DM_OP_MAP] == 1);
}

static void test_life_detach_and_page_failures(void)
{
	struct j713_dm dm;
	struct life_fake f;

	life_reset(&dm, &f);
	f.fail_op = J713_DM_OP_DETACH;
	CHECK(j713_dm_run_powered(&dm, &life_ops, &f) != 0);
	CHECK(dm.retain == 1);
	CHECK(dm.cleanup_error != 0);
	CHECK(dm.first_error == 0);
	CHECK(!(dm.flags & J713_DM_F_MAPPED));
	CHECK(dm.flags & J713_DM_F_ATTACHED);
	CHECK(dm.op_calls[J713_DM_OP_FREE_DOMAIN] == 0);
	CHECK(dm.op_calls[J713_DM_OP_FREE_PAGES] == 0);
	CHECK(dm.flags & J713_DM_F_OVERLAY);
	CHECK(dm.op_calls[J713_DM_OP_POWER_DOWN] == 0);

	life_reset(&dm, &f);
	f.fail_op = J713_DM_OP_FREE_PAGES;
	CHECK(j713_dm_run_powered(&dm, &life_ops, &f) != 0);
	CHECK(dm.flags & J713_DM_F_PAGES);
	CHECK(!(dm.flags & J713_DM_F_DOMAIN));
	CHECK(dm.op_calls[J713_DM_OP_REMOVE_OVERLAY] == 0);
	CHECK(dm.op_calls[J713_DM_OP_POWER_DOWN] == 0);
	CHECK(dm.free_while_ref == 0);

	life_reset(&dm, &f);
	f.fail_op = J713_DM_OP_REMOVE_OVERLAY;
	CHECK(j713_dm_run_powered(&dm, &life_ops, &f) != 0);
	CHECK(dm.retain == 1);
	CHECK(dm.first_error == 0);
	CHECK(dm.cleanup_error != 0);
	CHECK(dm.flags & J713_DM_F_OVERLAY);
	CHECK(dm.flags & J713_DM_F_POWER);
	CHECK(dm.op_calls[J713_DM_OP_POWER_DOWN] == 0);
	CHECK(dm.map_total == J713_DM_PAGE_COUNT);
	CHECK(dm.map_count == 0);
}

static void test_life_partial_map_unmaps(void)
{
	struct j713_dm dm;
	struct life_fake f;

	life_reset(&dm, &f);
	f.fail_op = J713_DM_OP_MAP;
	f.map_owned = 1;
	CHECK(j713_dm_run_powered(&dm, &life_ops, &f) == J713_DM_ERR_OWNED);
	CHECK(dm.map_total == 2);
	CHECK(dm.op_calls[J713_DM_OP_UNMAP] == 1);
	CHECK(dm.retain == 0);
	CHECK(dm.op_calls[J713_DM_OP_POWER_DOWN] == 0);
}

static void test_life_cpu_run_retains_without_free(void)
{
	struct j713_dm dm;
	struct life_fake f;

	life_reset(&dm, &f);
	f.cpu_fail_mask = J713_DM_F_MAPPED;
	CHECK(j713_dm_run_powered(&dm, &life_ops, &f) == -EBUSY);
	CHECK(dm.retain == 1);
	CHECK(dm.op_calls[J713_DM_OP_UNMAP] == 0);
	CHECK(dm.op_calls[J713_DM_OP_FREE_PAGES] == 0);
	CHECK(dm.op_calls[J713_DM_OP_POWER_DOWN] == 0);
	CHECK(dm.flags & J713_DM_F_MAPPED);
	CHECK(dm.flags & J713_DM_F_PAGES);
	CHECK(dm.map_count == J713_DM_PAGE_COUNT);
	CHECK(dm.map_total == J713_DM_PAGE_COUNT);
	CHECK(dm.cleanup_error == -EBUSY);
	CHECK(dm.first_error == 0);

	life_reset(&dm, &f);
	f.cpu_fail_mask = J713_DM_F_OVERLAY;
	CHECK(j713_dm_run_powered(&dm, &life_ops, &f) == -EBUSY);
	CHECK(dm.retain == 1);
	CHECK(dm.op_calls[J713_DM_OP_REMOVE_OVERLAY] == 0);
	CHECK(dm.op_calls[J713_DM_OP_FREE_PAGES] == 0);
	CHECK(dm.flags & J713_DM_F_OVERLAY);
	CHECK(dm.op_calls[J713_DM_OP_POWER_DOWN] == 0);

	life_reset(&dm, &f);
	f.fail_op = J713_DM_OP_CPU;
	f.fail_err = -EBUSY;
	CHECK(j713_dm_run_powered(&dm, &life_ops, &f) == -EBUSY);
	CHECK(dm.retain == 0);
	CHECK(dm.op_calls[J713_DM_OP_APPLY_OVERLAY] == 0);
	CHECK(dm.op_calls[J713_DM_OP_POWER_DOWN] == 0);
	CHECK(dm.flags & J713_DM_F_DART_CLAIM);
}

static void test_life_busy_and_power_block(void)
{
	struct j713_dm dm;
	struct life_fake f;

	life_reset(&dm, &f);
	dm.retain = 1;
	dm.cleanup_state = J713_DM_CLEAN_PENDING;
	dm.map_total = 7;
	CHECK(j713_dm_run_powered(&dm, &life_ops, &f) == -EBUSY);
	CHECK(dm.map_total == 7);
	CHECK(dm.op_calls[J713_DM_OP_MAP] == 0);

	life_reset(&dm, &f);
	dm.flags = J713_DM_F_OTHER_CLAIM;
	CHECK(j713_dm_run_powered(&dm, &life_ops, &f) == -EINVAL);

	life_reset(&dm, &f);
	dm.flags = 0;
	CHECK(j713_dm_recover(&dm, &life_ops, &f) == 0);
	CHECK(dm.ntrace == 0);
	CHECK(dm.op_calls[J713_DM_OP_POWER_DOWN] == 0);
	CHECK(dm.forward_calls[J713_DM_OP_MAP] == 0);

	/* Power plus claims, and no device flag: restore without reading CPU. */
	life_reset(&dm, &f);
	f.cpu_fail_mask = J713_DM_F_POWER;
	CHECK(j713_dm_recover(&dm, &life_ops, &f) == 0);
	CHECK(dm.op_calls[J713_DM_OP_CPU] == 0);
	CHECK(dm.op_calls[J713_DM_OP_POWER_DOWN] == 1);
	CHECK(dm.forward_calls[J713_DM_OP_POWER_DOWN] == 0);
	CHECK(dm.forward_calls[J713_DM_OP_MAP] == 0);
	CHECK(dm.power_restored == 1);
	CHECK(dm.retain == 0);
	CHECK(dm.flags == 0);
	CHECK(dm.saw_power_down == 1);
	CHECK(trace_before(&dm, J713_DM_OP_POWER_DOWN, J713_DM_OP_RELEASE_CLAIMS));
}

/* Same shape as dartmap-life-preflight/check_recovery.c. Sparse ops:
 * a NULL callback must not be invoked.
 */
struct preflight_counts {
	unsigned int cpu, master, overlay, driver, power, claims;
	int cpu_rc;
};

static int pre_cpu(void *v, struct j713_dm *d)
{
	struct preflight_counts *c = v;
	(void)d;
	c->cpu++;
	return c->cpu_rc;
}
static int pre_master(void *v, struct j713_dm *d)
{
	struct preflight_counts *c = v;
	(void)d;
	c->master++;
	return 0;
}
static int pre_overlay(void *v, struct j713_dm *d)
{
	struct preflight_counts *c = v;
	(void)d;
	c->overlay++;
	return 0;
}
static int pre_driver(void *v, struct j713_dm *d)
{
	struct preflight_counts *c = v;
	(void)d;
	c->driver++;
	return 0;
}
static int pre_power(void *v, struct j713_dm *d)
{
	struct preflight_counts *c = v;
	(void)d;
	c->power++;
	return 0;
}
static int pre_claims(void *v, struct j713_dm *d)
{
	struct preflight_counts *c = v;
	(void)d;
	c->claims++;
	return 0;
}

static const struct j713_dm_ops preflight_ops = {
	.cpu_stopped = pre_cpu,
	.unbind_master = pre_master,
	.remove_overlay = pre_overlay,
	.unregister_driver = pre_driver,
	.power_down = pre_power,
	.release_claims = pre_claims,
};

static void test_preflight_recovery_cases(void)
{
	struct j713_dm d;
	struct preflight_counts c;
	int rc;

	memset(&d, 0, sizeof(d));
	memset(&c, 0, sizeof(c));
	d.flags = J713_DM_F_POWER | J713_DM_F_OTHER_CLAIM | J713_DM_F_DRIVER |
		  J713_DM_F_OVERLAY | J713_DM_F_MASTER;
	d.retain = 1;
	d.cleanup_state = J713_DM_CLEAN_PENDING;
	d.first_error = -EIO;
	d.fail_stage = J713_DM_STAGE_OVERLAY;
	rc = j713_dm_recover(&d, &preflight_ops, &c);
	CHECK(rc == 0);
	CHECK(d.retain == 0);
	CHECK(d.flags == 0);
	CHECK(d.power_restored == 1);
	CHECK(c.power == 1);
	CHECK(c.claims == 1);
	CHECK(c.master == 1);
	CHECK(c.overlay == 1);
	CHECK(c.driver == 1);
	CHECK(d.first_error == -EIO);
	CHECK(d.cleanup_error == 0);
	CHECK(d.forward_calls[J713_DM_OP_MAP] == 0);
	CHECK(d.forward_calls[J713_DM_OP_ATTACH] == 0);
	CHECK(d.forward_calls[J713_DM_OP_APPLY_OVERLAY] == 0);
	CHECK(d.forward_calls[J713_DM_OP_REG_DRIVER] == 0);
	CHECK(trace_before(&d, J713_DM_OP_UNBIND, J713_DM_OP_REMOVE_OVERLAY));
	CHECK(trace_before(&d, J713_DM_OP_REMOVE_OVERLAY, J713_DM_OP_UNREG));
	CHECK(trace_before(&d, J713_DM_OP_UNREG, J713_DM_OP_POWER_DOWN));

	memset(&d, 0, sizeof(d));
	memset(&c, 0, sizeof(c));
	d.flags = J713_DM_F_POWER | J713_DM_F_OTHER_CLAIM | J713_DM_F_DRIVER |
		  J713_DM_F_OVERLAY;
	d.retain = 1;
	d.cleanup_state = J713_DM_CLEAN_PENDING;
	c.cpu_rc = -EBUSY;
	rc = j713_dm_recover(&d, &preflight_ops, &c);
	CHECK(rc != 0);
	CHECK(c.cpu != 0);
	CHECK(c.overlay == 0);
	CHECK(c.driver == 0);
	CHECK(c.power == 0);
	CHECK(d.retain == 1);
	CHECK(d.flags & J713_DM_F_OVERLAY);
	CHECK(d.flags & J713_DM_F_DRIVER);
	CHECK(d.flags & J713_DM_F_POWER);
	CHECK(d.cleanup_error == -EBUSY);
	CHECK(d.first_error == 0);
	CHECK(d.forward_calls[J713_DM_OP_MAP] == 0);
	CHECK(d.forward_calls[J713_DM_OP_APPLY_OVERLAY] == 0);

	/* Partial enable: claims only. CPU must not be read. */
	memset(&d, 0, sizeof(d));
	memset(&c, 0, sizeof(c));
	d.flags = J713_DM_F_POWER | J713_DM_F_OTHER_CLAIM | J713_DM_F_DART_CLAIM;
	d.retain = 1;
	d.cleanup_state = J713_DM_CLEAN_PENDING;
	d.first_error = -EIO;
	c.cpu_rc = -EBUSY;
	rc = j713_dm_recover(&d, &preflight_ops, &c);
	CHECK(rc == 0);
	CHECK(c.cpu == 0);
	CHECK(c.overlay == 0);
	CHECK(c.driver == 0);
	CHECK(c.power == 1);
	CHECK(c.claims == 1);
	CHECK(d.power_restored == 1);
	CHECK(d.retain == 0);
	CHECK(d.flags == 0);
	CHECK(d.first_error == -EIO);
	CHECK(d.cleanup_error == 0);
	CHECK(d.saw_power_down == 1);
	CHECK(c.cpu == 0);
	CHECK(trace_before(&d, J713_DM_OP_POWER_DOWN, J713_DM_OP_RELEASE_CLAIMS));
	CHECK(d.forward_calls[J713_DM_OP_MAP] == 0);
	CHECK(d.forward_calls[J713_DM_OP_POWER_DOWN] == 0);

	/* Default domain is enough to require a stopped CPU before free. */
	memset(&d, 0, sizeof(d));
	memset(&c, 0, sizeof(c));
	d.flags = J713_DM_F_POWER | J713_DM_F_DOMAIN;
	d.retain = 1;
	d.cleanup_state = J713_DM_CLEAN_PENDING;
	c.cpu_rc = -EBUSY;
	rc = j713_dm_recover(&d, &preflight_ops, &c);
	CHECK(rc != 0);
	CHECK(c.cpu == 1);
	CHECK(c.power == 0);
	CHECK(d.retain == 1);
	CHECK(d.flags & J713_DM_F_DOMAIN);
	CHECK(d.flags & J713_DM_F_POWER);
	CHECK(d.cleanup_error == -EBUSY);
	CHECK(d.saw_power_down == 0);
	CHECK(d.forward_calls[J713_DM_OP_MAP] == 0);
	CHECK(d.forward_calls[J713_DM_OP_APPLY_OVERLAY] == 0);
}

static int retain_cb(void *ctx, struct j713_isp_state *st)
{
	(void)ctx;
	st->retain_power = 1;
	return 0;
}

static int retain_staged_cb(void *ctx, struct j713_isp_state *st)
{
	(void)ctx;
	st->fail_stage = J713_DM_STAGE_MAP;
	st->retain_power = 1;
	return -EIO;
}

static int eio_cb(void *ctx, struct j713_isp_state *st)
{
	(void)ctx;
	(void)st;
	return -EIO;
}

static int ok_cb(void *ctx, struct j713_isp_state *st)
{
	(void)ctx;
	(void)st;
	return 0;
}

static void test_retain_power_callback(void)
{
	struct mock m;
	unsigned int i;

	load_live(&m);
	m.isp_ver = J713_ISP_VERSION_EXACT;
	CHECK(run_with(&m, true, retain_cb) == -EBUSY);
	CHECK(m.st.retain_power == 1);
	CHECK(m.st.power_restored == 0);
	CHECK(m.st.cleanup == -EBUSY);
	CHECK(m.st.verify == 0);
	CHECK(m.st.result == -EBUSY);
	CHECK(m.st.fail_stage == J713_ISP_STAGE_RETAIN);
	CHECK(m.nwrites == J713_ISP_LOCAL_COUNT);
	CHECK(m.cleanup_polls == 0);
	for (i = 0; i < J713_ISP_LOCAL_COUNT; i++) {
		u32 word = m.pmgr[j713_isp_local_off(i) / 4];

		CHECK((word & J713_ISP_REQ_MASK) == J713_ISP_ENABLE_VAL);
		CHECK((word & J713_ISP_LOW_MASK) == J713_ISP_LOW_MASK);
	}

	load_live(&m);
	m.isp_ver = J713_ISP_VERSION_EXACT;
	CHECK(run_with(&m, true, retain_staged_cb) == -EIO);
	CHECK(m.st.fail_stage == J713_DM_STAGE_MAP);
	CHECK(m.st.power_restored == 0);
	CHECK(m.st.cleanup == -EBUSY);
	CHECK(m.nwrites == J713_ISP_LOCAL_COUNT);
	CHECK(m.cleanup_polls == 0);

	load_live(&m);
	m.isp_ver = J713_ISP_VERSION_EXACT;
	CHECK(run_with(&m, true, eio_cb) == -EIO);
	CHECK(m.st.fail_stage == J713_ISP_STAGE_INSPECT);
	CHECK(m.st.retain_power == 0);
	CHECK(m.st.power_restored == 1);
	check_restores(&m);

	load_live(&m);
	m.isp_ver = J713_ISP_VERSION_EXACT;
	CHECK(run_with(&m, true, ok_cb) == 0);
	CHECK(m.st.retain_power == 0);
	CHECK(m.st.power_restored == 1);
	CHECK(m.st.result == 0);
	check_restores(&m);
}

static void test_life_failure_matrix(void)
{
	static const struct {
		int op;
		int stage;
	} forward_ops[] = {
		{ J713_DM_OP_CPU, J713_DM_STAGE_CPU },
		{ J713_DM_OP_BASELINE, J713_DM_STAGE_BASELINE },
		{ J713_DM_OP_RELEASE_DART, J713_DM_STAGE_HANDOFF },
		{ J713_DM_OP_REG_DRIVER, J713_DM_STAGE_DRIVER },
		{ J713_DM_OP_APPLY_OVERLAY, J713_DM_STAGE_OVERLAY },
		{ J713_DM_OP_MASTER_BOUND, J713_DM_STAGE_BIND },
		{ J713_DM_OP_REMAP_RO, J713_DM_STAGE_DART_RO },
		{ J713_DM_OP_ALLOC_PAGES, J713_DM_STAGE_PAGES },
		{ J713_DM_OP_ALLOC_DOMAIN, J713_DM_STAGE_DOMAIN },
		{ J713_DM_OP_PREMISE, J713_DM_STAGE_PREMISE },
		{ J713_DM_OP_ATTACH, J713_DM_STAGE_ATTACH },
		{ J713_DM_OP_MAP, J713_DM_STAGE_MAP },
		{ J713_DM_OP_LOOKUP, J713_DM_STAGE_LOOKUP },
		{ J713_DM_OP_READBACK, J713_DM_STAGE_READBACK },
	};
	static const struct {
		int op;
		int stage;
		unsigned int keep;
		unsigned int map_count;
	} tears[] = {
		{ J713_DM_OP_UNMAP, J713_DM_STAGE_UNMAP,
		  J713_DM_F_MAPPED | J713_DM_F_ATTACHED | J713_DM_F_DOMAIN |
		  J713_DM_F_PAGES | J713_DM_F_DART_RO | J713_DM_F_MASTER |
		  J713_DM_F_OVERLAY | J713_DM_F_DRIVER | J713_DM_F_POWER |
		  J713_DM_F_OTHER_CLAIM, J713_DM_PAGE_COUNT },
		{ J713_DM_OP_DETACH, J713_DM_STAGE_DETACH,
		  J713_DM_F_ATTACHED | J713_DM_F_DOMAIN | J713_DM_F_PAGES |
		  J713_DM_F_DART_RO | J713_DM_F_MASTER | J713_DM_F_OVERLAY |
		  J713_DM_F_DRIVER | J713_DM_F_POWER | J713_DM_F_OTHER_CLAIM, 0u },
		{ J713_DM_OP_FREE_DOMAIN, J713_DM_STAGE_FREE_DOMAIN,
		  J713_DM_F_DOMAIN | J713_DM_F_PAGES | J713_DM_F_DART_RO |
		  J713_DM_F_MASTER | J713_DM_F_OVERLAY | J713_DM_F_DRIVER |
		  J713_DM_F_POWER | J713_DM_F_OTHER_CLAIM, 0u },
		{ J713_DM_OP_FREE_PAGES, J713_DM_STAGE_FREE_PAGES,
		  J713_DM_F_PAGES | J713_DM_F_DART_RO | J713_DM_F_MASTER |
		  J713_DM_F_OVERLAY | J713_DM_F_DRIVER | J713_DM_F_POWER |
		  J713_DM_F_OTHER_CLAIM, 0u },
		{ J713_DM_OP_UNMAP_RO, J713_DM_STAGE_DART_RO,
		  J713_DM_F_DART_RO | J713_DM_F_MASTER | J713_DM_F_OVERLAY |
		  J713_DM_F_DRIVER | J713_DM_F_POWER | J713_DM_F_OTHER_CLAIM, 0u },
		{ J713_DM_OP_UNBIND, J713_DM_STAGE_UNBIND,
		  J713_DM_F_MASTER | J713_DM_F_OVERLAY | J713_DM_F_DRIVER |
		  J713_DM_F_POWER | J713_DM_F_OTHER_CLAIM, 0u },
		{ J713_DM_OP_REMOVE_OVERLAY, J713_DM_STAGE_OVERLAY_OFF,
		  J713_DM_F_OVERLAY | J713_DM_F_DRIVER | J713_DM_F_POWER |
		  J713_DM_F_OTHER_CLAIM, 0u },
		{ J713_DM_OP_UNREG, J713_DM_STAGE_UNREG,
		  J713_DM_F_DRIVER | J713_DM_F_POWER | J713_DM_F_OTHER_CLAIM, 0u },
	};
	static const unsigned int blocks[] = {
		J713_DM_F_DRIVER, J713_DM_F_OVERLAY, J713_DM_F_MASTER,
		J713_DM_F_DART_RO, J713_DM_F_PAGES, J713_DM_F_DOMAIN,
		J713_DM_F_ATTACHED, J713_DM_F_MAPPED
	};
	unsigned int i;

	for (i = 0; i < ARRAY_SIZE(forward_ops); i++) {
		struct j713_dm dm;
		struct life_fake f;
		int op = forward_ops[i].op;

		life_reset(&dm, &f);
		f.fail_op = op;
		f.fail_err = -EIO;
		CHECK(j713_dm_run_powered(&dm, &life_ops, &f) == -EIO);
		CHECK(dm.first_error == -EIO);
		CHECK(dm.cleanup_error == 0);
		CHECK(dm.retain == 0);
		CHECK(dm.fail_stage == forward_ops[i].stage);
		CHECK(dm.free_while_ref == 0);
		CHECK(!(dm.flags & J713_DM_F_BLOCKS_POWER));
		assert_power_held(&dm);
		if (op == J713_DM_OP_CPU || op == J713_DM_OP_BASELINE ||
		    op == J713_DM_OP_RELEASE_DART) {
			CHECK(dm.flags == (J713_DM_F_POWER | J713_DM_F_DART_CLAIM |
					   J713_DM_F_OTHER_CLAIM));
			CHECK(dm.op_calls[J713_DM_OP_REG_DRIVER] == 0);
		} else {
			CHECK(dm.flags == (J713_DM_F_POWER | J713_DM_F_OTHER_CLAIM));
		}
		if (op == J713_DM_OP_REG_DRIVER)
			CHECK(dm.op_calls[J713_DM_OP_UNREG] == 0);
		if (op == J713_DM_OP_APPLY_OVERLAY) {
			CHECK(dm.op_calls[J713_DM_OP_REMOVE_OVERLAY] == 0);
			CHECK(dm.op_calls[J713_DM_OP_UNREG] == 1);
			CHECK(dm.op_calls[J713_DM_OP_MASTER_BOUND] == 0);
		}
		if (op == J713_DM_OP_MASTER_BOUND) {
			CHECK(dm.op_calls[J713_DM_OP_UNBIND] == 0);
			CHECK(dm.op_calls[J713_DM_OP_REMOVE_OVERLAY] == 1);
		}
		if (op == J713_DM_OP_MAP) {
			CHECK(dm.map_total == 0);
			CHECK(dm.map_count == 0);
			CHECK(dm.op_calls[J713_DM_OP_UNMAP] == 0);
			CHECK(dm.op_calls[J713_DM_OP_DETACH] == 1);
			CHECK(dm.forward_calls[J713_DM_OP_MAP] == 1);
			CHECK(dm.op_calls[J713_DM_OP_LOOKUP] == 0);
		}
		if (op == J713_DM_OP_LOOKUP || op == J713_DM_OP_READBACK) {
			CHECK(dm.map_total == J713_DM_PAGE_COUNT);
			CHECK(dm.map_count == 0);
			CHECK(dm.op_calls[J713_DM_OP_UNMAP] == 1);
			CHECK(dm.forward_calls[J713_DM_OP_UNMAP] == 0);
			CHECK(dm.forward_calls[J713_DM_OP_MAP] == 1);
		}
		if (op == J713_DM_OP_LOOKUP)
			CHECK(dm.op_calls[J713_DM_OP_READBACK] == 0);
		if (op == J713_DM_OP_READBACK)
			CHECK(dm.lookup_count == J713_DM_PAGE_COUNT);
		if (op == J713_DM_OP_ALLOC_DOMAIN)
			CHECK(dm.op_calls[J713_DM_OP_FREE_DOMAIN] == 0);
		if (op == J713_DM_OP_ATTACH) {
			CHECK(dm.op_calls[J713_DM_OP_DETACH] == 0);
			CHECK(dm.op_calls[J713_DM_OP_FREE_DOMAIN] == 1);
		}
		if (op == J713_DM_OP_MAP || op == J713_DM_OP_LOOKUP ||
		    op == J713_DM_OP_READBACK) {
			CHECK(dm.op_calls[J713_DM_OP_QUIESCE] == 1);
			CHECK(trace_before(&dm, J713_DM_OP_DETACH, J713_DM_OP_QUIESCE));
			CHECK(!(dm.flags & J713_DM_F_NEED_QUIESCE));
		} else {
			CHECK(dm.op_calls[J713_DM_OP_QUIESCE] == 0);
			CHECK(!(dm.flags & J713_DM_F_NEED_QUIESCE));
		}
	}

	for (i = 0; i < ARRAY_SIZE(tears); i++) {
		struct j713_dm dm;
		struct life_fake f;

		life_reset(&dm, &f);
		f.fail_op = tears[i].op;
		f.fail_err = -EIO;
		CHECK(j713_dm_run_powered(&dm, &life_ops, &f) == -EIO);
		CHECK(dm.retain == 1);
		CHECK(dm.first_error == 0);
		CHECK(dm.cleanup_error == -EIO);
		CHECK(dm.fail_stage == 0);
		CHECK(dm.stage == tears[i].stage);
		CHECK(dm.cleanup_state == J713_DM_CLEAN_PENDING);
		CHECK(dm.flags == tears[i].keep);
		CHECK(dm.map_total == J713_DM_PAGE_COUNT);
		CHECK(dm.map_count == tears[i].map_count);
		CHECK(dm.free_while_ref == 0);
		assert_power_held(&dm);
		if (tears[i].op == J713_DM_OP_UNMAP)
			CHECK(dm.op_calls[J713_DM_OP_DETACH] == 0);
		else
			CHECK(dm.op_calls[J713_DM_OP_DETACH] == 1);
		if (tears[i].op == J713_DM_OP_UNMAP ||
		    tears[i].op == J713_DM_OP_DETACH) {
			CHECK(dm.op_calls[J713_DM_OP_QUIESCE] == 0);
			CHECK(!(dm.flags & J713_DM_F_NEED_QUIESCE));
		} else {
			CHECK(dm.op_calls[J713_DM_OP_QUIESCE] == 1);
			CHECK(!(dm.flags & J713_DM_F_NEED_QUIESCE));
		}
		if (tears[i].op == J713_DM_OP_DETACH)
			CHECK(dm.flags & J713_DM_F_ATTACHED);
		if (tears[i].op == J713_DM_OP_UNREG)
			CHECK(dm.op_calls[J713_DM_OP_UNREG] == 1);
		else
			CHECK(dm.op_calls[J713_DM_OP_UNREG] == 0);
	}

	/* Owned acquire keeps the resource only until a successful teardown. */
	for (i = 0; i < 6; i++) {
		static const int owned_ops[] = {
			J713_DM_OP_REG_DRIVER, J713_DM_OP_APPLY_OVERLAY,
			J713_DM_OP_REMAP_RO, J713_DM_OP_ALLOC_PAGES,
			J713_DM_OP_ALLOC_DOMAIN, J713_DM_OP_ATTACH
		};
		struct j713_dm dm;
		struct life_fake f;

		life_reset(&dm, &f);
		f.fail_op = owned_ops[i];
		f.fail_err = J713_DM_ERR_OWNED;
		CHECK(j713_dm_run_powered(&dm, &life_ops, &f) == J713_DM_ERR_OWNED);
		CHECK(dm.retain == 0);
		CHECK(dm.first_error == J713_DM_ERR_OWNED);
		CHECK(dm.cleanup_error == 0);
		CHECK(dm.flags == (J713_DM_F_POWER | J713_DM_F_OTHER_CLAIM));
		CHECK(dm.free_while_ref == 0);
		if (owned_ops[i] == J713_DM_OP_ATTACH)
			CHECK(dm.op_calls[J713_DM_OP_QUIESCE] == 1);
		else
			CHECK(dm.op_calls[J713_DM_OP_QUIESCE] == 0);
		assert_power_held(&dm);
	}

	{
		struct j713_dm dm;
		struct life_fake f;

		life_reset(&dm, &f);
		f.fail_op = J713_DM_OP_MAP;
		f.map_partial = 3;
		f.fail_err = -EIO;
		CHECK(j713_dm_run_powered(&dm, &life_ops, &f) == -EIO);
		CHECK(dm.map_total == 3u);
		CHECK(dm.map_count == 0);
		CHECK(dm.op_calls[J713_DM_OP_UNMAP] == 1);
		CHECK(dm.op_calls[J713_DM_OP_LOOKUP] == 0);
		CHECK(dm.retain == 0);
		assert_power_held(&dm);

		life_reset(&dm, &f);
		f.fail_op = J713_DM_OP_UNMAP;
		f.unmap_left = 3;
		f.fail_err = -EIO;
		CHECK(j713_dm_run_powered(&dm, &life_ops, &f) == -EIO);
		CHECK(dm.map_count == 3u);
		CHECK(dm.map_total == J713_DM_PAGE_COUNT);
		CHECK(dm.flags & J713_DM_F_MAPPED);
		CHECK(dm.op_calls[J713_DM_OP_DETACH] == 0);
		CHECK(dm.retain == 1);
		assert_power_held(&dm);

		/* Overlay applied, then remove fails: first_error stays owned. */
		life_reset(&dm, &f);
		f.force_owned_op = J713_DM_OP_APPLY_OVERLAY;
		f.fail_op = J713_DM_OP_REMOVE_OVERLAY;
		f.fail_err = -EIO;
		CHECK(j713_dm_run_powered(&dm, &life_ops, &f) == J713_DM_ERR_OWNED);
		CHECK(dm.first_error == J713_DM_ERR_OWNED);
		CHECK(dm.cleanup_error == -EIO);
		CHECK(dm.retain == 1);
		CHECK(dm.flags & J713_DM_F_OVERLAY);
		CHECK(dm.flags & J713_DM_F_DRIVER);
		CHECK(dm.op_calls[J713_DM_OP_MASTER_BOUND] == 0);
		CHECK(dm.op_calls[J713_DM_OP_UNREG] == 0);
		assert_power_held(&dm);

		life_reset(&dm, &f);
		f.force_owned_op = J713_DM_OP_MAP;
		f.map_partial = 2;
		f.fail_op = J713_DM_OP_UNMAP;
		f.fail_err = -EIO;
		CHECK(j713_dm_run_powered(&dm, &life_ops, &f) == J713_DM_ERR_OWNED);
		CHECK(dm.first_error == J713_DM_ERR_OWNED);
		CHECK(dm.cleanup_error == -EIO);
		CHECK(dm.map_total == 2u);
		CHECK(dm.map_count == 2u);
		CHECK(dm.flags & J713_DM_F_MAPPED);
		CHECK(dm.op_calls[J713_DM_OP_DETACH] == 0);
		CHECK(dm.forward_calls[J713_DM_OP_MAP] == 1);
		assert_power_held(&dm);

		life_reset(&dm, &f);
		f.rearm_mapped_on_detach = 1;
		CHECK(j713_dm_run_powered(&dm, &life_ops, &f) == -EBUSY);
		CHECK(dm.free_while_ref == 1);
		CHECK(dm.flags & J713_DM_F_MAPPED);
		CHECK(dm.flags & J713_DM_F_DOMAIN);
		CHECK(!(dm.flags & J713_DM_F_ATTACHED));
		CHECK(dm.op_calls[J713_DM_OP_FREE_DOMAIN] == 0);
		CHECK(dm.op_calls[J713_DM_OP_FREE_PAGES] == 0);
		CHECK(dm.retain == 1);
		assert_power_held(&dm);
	}

	/* 5th CPU is the first teardown check; 6th is the free-pages check. */
	{
		struct j713_dm dm;
		struct life_fake f;

		life_reset(&dm, &f);
		f.cpu_fail_at = 5;
		CHECK(j713_dm_run_powered(&dm, &life_ops, &f) == -EBUSY);
		CHECK(dm.retain == 1);
		CHECK(dm.first_error == 0);
		CHECK(dm.cleanup_error == -EBUSY);
		CHECK(dm.flags & J713_DM_F_MAPPED);
		CHECK(dm.flags & J713_DM_F_DOMAIN);
		CHECK(dm.op_calls[J713_DM_OP_UNMAP] == 0);
		CHECK(dm.op_calls[J713_DM_OP_QUIESCE] == 0);
		CHECK(dm.op_calls[J713_DM_OP_CPU] == 5);
		CHECK(dm.forward_calls[J713_DM_OP_CPU] == 4);
		CHECK(dm.map_count == J713_DM_PAGE_COUNT);
		assert_power_held(&dm);

		life_reset(&dm, &f);
		f.cpu_fail_at = 6;
		CHECK(j713_dm_run_powered(&dm, &life_ops, &f) == -EBUSY);
		CHECK(dm.flags & J713_DM_F_PAGES);
		CHECK(!(dm.flags & J713_DM_F_DOMAIN));
		CHECK(!(dm.flags & J713_DM_F_MAPPED));
		CHECK(dm.op_calls[J713_DM_OP_FREE_PAGES] == 0);
		CHECK(dm.op_calls[J713_DM_OP_FREE_DOMAIN] == 1);
		CHECK(dm.op_calls[J713_DM_OP_QUIESCE] == 1);
		CHECK(dm.op_calls[J713_DM_OP_REMOVE_OVERLAY] == 0);
		CHECK(dm.op_calls[J713_DM_OP_CPU] == 6);
		CHECK(dm.forward_calls[J713_DM_OP_CPU] == 4);
		CHECK(dm.map_count == 0);
		CHECK(dm.map_total == J713_DM_PAGE_COUNT);
		assert_power_held(&dm);

		life_reset(&dm, &f);
		f.cpu_fail_mask = J713_DM_F_DOMAIN;
		CHECK(j713_dm_run_powered(&dm, &life_ops, &f) == -EBUSY);
		CHECK(dm.flags & J713_DM_F_DOMAIN);
		CHECK(dm.flags & J713_DM_F_ATTACHED);
		CHECK(dm.op_calls[J713_DM_OP_MAP] == 0);
		CHECK(dm.op_calls[J713_DM_OP_FREE_DOMAIN] == 0);
		CHECK(dm.retain == 1);
		assert_power_held(&dm);
	}

	for (i = 0; i < ARRAY_SIZE(blocks); i++) {
		struct j713_dm dm;
		struct life_fake f;

		life_reset(&dm, &f);
		dm.flags = J713_DM_F_POWER | J713_DM_F_OTHER_CLAIM | blocks[i];
		dm.retain = 1;
		dm.cleanup_state = J713_DM_CLEAN_PENDING;
		dm.first_error = -EIO;
		f.cpu_fail_mask = blocks[i];
		CHECK(j713_dm_recover(&dm, &life_ops, &f) == -EBUSY);
		CHECK(dm.op_calls[J713_DM_OP_CPU] == 1);
		CHECK(dm.op_calls[J713_DM_OP_POWER_DOWN] == 0);
		CHECK(dm.flags & blocks[i]);
		CHECK(dm.flags & J713_DM_F_POWER);
		CHECK(dm.retain == 1);
		CHECK(dm.cleanup_error == -EBUSY);
		CHECK(dm.first_error == -EIO);
		CHECK(dm.forward_calls[J713_DM_OP_MAP] == 0);
		CHECK(dm.forward_calls[J713_DM_OP_ATTACH] == 0);
		CHECK(dm.forward_calls[J713_DM_OP_APPLY_OVERLAY] == 0);
		CHECK(dm.forward_calls[J713_DM_OP_REG_DRIVER] == 0);
		CHECK(dm.power_restored == 0);
		assert_no_free_while_live(&dm);
	}

	{
		struct j713_dm dm;
		struct life_fake f;
		unsigned int map_fwd;

		life_reset(&dm, &f);
		CHECK(j713_dm_run_powered(&dm, &life_ops, &f) == 0);
		map_fwd = dm.forward_calls[J713_DM_OP_MAP];
		dm.retain = 1;
		dm.cleanup_state = J713_DM_CLEAN_PENDING;
		CHECK(j713_dm_recover(&dm, &life_ops, &f) == 0);
		CHECK(dm.op_calls[J713_DM_OP_POWER_DOWN] == 1);
		CHECK(dm.op_calls[J713_DM_OP_CPU] == 6);
		CHECK(dm.forward_calls[J713_DM_OP_MAP] == map_fwd);
		CHECK(dm.forward_calls[J713_DM_OP_ATTACH] == 1);
		CHECK(dm.forward_calls[J713_DM_OP_APPLY_OVERLAY] == 1);
		CHECK(dm.forward_calls[J713_DM_OP_REG_DRIVER] == 1);
		CHECK(dm.retain == 0);
		CHECK(dm.flags == 0);
		CHECK(dm.power_restored == 1);
		CHECK(dm.map_total == J713_DM_PAGE_COUNT);
		CHECK(dm.cleanup_state == J713_DM_CLEAN_COMPLETE);
		assert_cleanup_not_forward(&dm);
		CHECK(j713_dm_recover(&dm, &life_ops, &f) == 0);
		CHECK(dm.op_calls[J713_DM_OP_POWER_DOWN] == 1);
		CHECK(dm.op_calls[J713_DM_OP_MAP] == 1);

		life_reset(&dm, &f);
		dm.flags = J713_DM_F_POWER | J713_DM_F_OTHER_CLAIM | J713_DM_F_DART_CLAIM;
		dm.retain = 1;
		dm.cleanup_state = J713_DM_CLEAN_PENDING;
		dm.first_error = -EIO;
		dm.map_total = 5;
		f.cpu_fail_mask = J713_DM_F_POWER;
		CHECK(j713_dm_recover(&dm, &life_ops, &f) == 0);
		CHECK(dm.op_calls[J713_DM_OP_CPU] == 0);
		CHECK(dm.op_calls[J713_DM_OP_POWER_DOWN] == 1);
		CHECK(dm.op_calls[J713_DM_OP_RELEASE_CLAIMS] == 1);
		CHECK(dm.flags == 0);
		CHECK(dm.retain == 0);
		CHECK(dm.power_restored == 1);
		CHECK(dm.first_error == -EIO);
		CHECK(dm.cleanup_error == 0);
		CHECK(dm.map_total == 5u);
		CHECK(dm.forward_calls[J713_DM_OP_MAP] == 0);
		CHECK(trace_before(&dm, J713_DM_OP_POWER_DOWN, J713_DM_OP_RELEASE_CLAIMS));

		life_reset(&dm, &f);
		dm.flags = J713_DM_F_POWER | J713_DM_F_OTHER_CLAIM;
		dm.retain = 1;
		dm.cleanup_state = J713_DM_CLEAN_PENDING;
		f.fail_op = J713_DM_OP_POWER_DOWN;
		f.fail_err = -EIO;
		CHECK(j713_dm_recover(&dm, &life_ops, &f) == -EBUSY);
		CHECK(dm.op_calls[J713_DM_OP_CPU] == 0);
		CHECK(dm.op_calls[J713_DM_OP_POWER_DOWN] == 1);
		CHECK(dm.op_calls[J713_DM_OP_RELEASE_CLAIMS] == 0);
		CHECK(dm.forward_calls[J713_DM_OP_POWER_DOWN] == 0);
		CHECK(dm.saw_power_down == 1);
		CHECK(dm.power_restored == 0);
		CHECK(dm.flags == (J713_DM_F_POWER | J713_DM_F_OTHER_CLAIM));
		CHECK(dm.retain == 1);
		CHECK(dm.cleanup_error == -EIO);
		CHECK(dm.forward_calls[J713_DM_OP_MAP] == 0);

		life_reset(&dm, &f);
		dm.flags = J713_DM_F_POWER | J713_DM_F_OTHER_CLAIM;
		dm.retain = 1;
		dm.cleanup_state = J713_DM_CLEAN_PENDING;
		f.fail_op = J713_DM_OP_RELEASE_CLAIMS;
		f.fail_err = -EBUSY;
		CHECK(j713_dm_recover(&dm, &life_ops, &f) == -EBUSY);
		CHECK(dm.op_calls[J713_DM_OP_POWER_DOWN] == 1);
		CHECK(dm.op_calls[J713_DM_OP_RELEASE_CLAIMS] == 1);
		CHECK(dm.power_restored == 1);
		CHECK(!(dm.flags & J713_DM_F_POWER));
		CHECK(dm.flags & J713_DM_F_OTHER_CLAIM);
		CHECK(dm.retain == 1);
		CHECK(dm.cleanup_error == -EBUSY);
		CHECK(dm.forward_calls[J713_DM_OP_RELEASE_CLAIMS] == 0);
		CHECK(dm.forward_calls[J713_DM_OP_MAP] == 0);

		life_reset(&dm, &f);
		dm.flags |= J713_DM_F_OVERLAY;
		dm.map_total = 4;
		CHECK(j713_dm_run_powered(&dm, &life_ops, &f) == -EBUSY);
		CHECK(dm.map_total == 4u);
		CHECK(dm.ntrace == 0);
		CHECK(dm.op_calls[J713_DM_OP_MAP] == 0);
		CHECK(dm.flags & J713_DM_F_OVERLAY);

		life_reset(&dm, &f);
		CHECK(j713_dm_run_powered(NULL, &life_ops, &f) == -EINVAL);
		CHECK(j713_dm_run_powered(&dm, NULL, &f) == -EINVAL);
		CHECK(j713_dm_recover(NULL, &life_ops, &f) == -EINVAL);
		CHECK(j713_dm_recover(&dm, NULL, &f) == -EINVAL);
		CHECK(dm.flags & J713_DM_F_POWER);
	}
}

static void test_premise_aborts_before_map(void)
{
	struct j713_dm dm;
	struct life_fake f;

	life_reset(&dm, &f);
	f.fabric[0] = 1u;
	CHECK(j713_dm_run_powered(&dm, &life_ops, &f) == J713_DM_ERR_FABRIC);
	CHECK(dm.fail_stage == J713_DM_STAGE_PREMISE);
	CHECK(dm.first_error == J713_DM_ERR_FABRIC);
	CHECK(dm.op_calls[J713_DM_OP_PREMISE] == 1);
	CHECK(dm.op_calls[J713_DM_OP_MAP] == 0);
	CHECK(dm.op_calls[J713_DM_OP_ATTACH] == 0);
	CHECK(dm.op_calls[J713_DM_OP_FREE_PAGES] == 1);
	CHECK(dm.op_calls[J713_DM_OP_REMOVE_OVERLAY] == 1);
	CHECK(dm.retain == 0);
	CHECK(dm.cleanup_state == J713_DM_CLEAN_IDLE);
	CHECK(dm.flags & J713_DM_F_POWER);
	CHECK(!(dm.flags & J713_DM_F_PAGES));
	CHECK(!(dm.flags & J713_DM_F_BLOCKS_POWER));
	assert_power_held(&dm);

	life_reset(&dm, &f);
	f.fabric[3] = 1u;
	CHECK(j713_dm_run_powered(&dm, &life_ops, &f) == J713_DM_ERR_FABRIC);
	CHECK(dm.op_calls[J713_DM_OP_MAP] == 0);
	CHECK(dm.op_calls[J713_DM_OP_ATTACH] == 0);
	CHECK(dm.retain == 0);
	CHECK(dm.op_calls[J713_DM_OP_FREE_PAGES] == 1);
	CHECK(dm.op_calls[J713_DM_OP_REMOVE_OVERLAY] == 1);
	assert_power_held(&dm);

	life_reset(&dm, &f);
	f.dart_error[1] = 0x80000000u;
	CHECK(j713_dm_run_powered(&dm, &life_ops, &f) == J713_DM_ERR_DART_FLAG);
	CHECK(dm.fail_stage == J713_DM_STAGE_PREMISE);
	CHECK(dm.first_error == J713_DM_ERR_DART_FLAG);
	CHECK(dm.op_calls[J713_DM_OP_MAP] == 0);
	CHECK(dm.op_calls[J713_DM_OP_ATTACH] == 0);
	CHECK(dm.op_calls[J713_DM_OP_FREE_PAGES] == 1);
	CHECK(dm.op_calls[J713_DM_OP_REMOVE_OVERLAY] == 1);
	CHECK(dm.retain == 0);
	CHECK(dm.cleanup_state == J713_DM_CLEAN_IDLE);
	CHECK(dm.flags & J713_DM_F_POWER);
	CHECK(!(dm.flags & J713_DM_F_PAGES));
	CHECK(!(dm.flags & J713_DM_F_BLOCKS_POWER));
	assert_power_held(&dm);

	life_reset(&dm, &f);
	f.dart_error[0] = 0x7fffffffu;
	CHECK(j713_dm_run_powered(&dm, &life_ops, &f) == J713_DM_ERR_DART_FLAG);
	CHECK(dm.fail_stage == J713_DM_STAGE_PREMISE);
	CHECK(dm.first_error == J713_DM_ERR_DART_FLAG);
	CHECK(dm.op_calls[J713_DM_OP_MAP] == 0);
	CHECK(dm.op_calls[J713_DM_OP_ATTACH] == 0);
	CHECK(dm.op_calls[J713_DM_OP_FREE_PAGES] == 1);
	CHECK(dm.op_calls[J713_DM_OP_REMOVE_OVERLAY] == 1);
	CHECK(dm.retain == 0);
	CHECK(dm.cleanup_state == J713_DM_CLEAN_IDLE);
	CHECK(dm.flags & J713_DM_F_POWER);
	CHECK(!(dm.flags & J713_DM_F_PAGES));
	CHECK(!(dm.flags & J713_DM_F_BLOCKS_POWER));
	assert_power_held(&dm);

	life_reset(&dm, &f);
	f.dart_error[2] = 1u;
	CHECK(j713_dm_run_powered(&dm, &life_ops, &f) == J713_DM_ERR_DART_FLAG);
	CHECK(dm.fail_stage == J713_DM_STAGE_PREMISE);
	CHECK(dm.first_error == J713_DM_ERR_DART_FLAG);
	CHECK(dm.op_calls[J713_DM_OP_MAP] == 0);
	CHECK(dm.op_calls[J713_DM_OP_ATTACH] == 0);
	CHECK(dm.op_calls[J713_DM_OP_FREE_PAGES] == 1);
	CHECK(dm.op_calls[J713_DM_OP_REMOVE_OVERLAY] == 1);
	CHECK(dm.retain == 0);
	CHECK(dm.cleanup_state == J713_DM_CLEAN_IDLE);
	CHECK(dm.flags & J713_DM_F_POWER);
	CHECK(!(dm.flags & J713_DM_F_PAGES));
	CHECK(!(dm.flags & J713_DM_F_BLOCKS_POWER));
	assert_power_held(&dm);
}

static void test_ladder_and_text_lookup(void)
{
	struct j713_dm dm;
	struct life_fake f;

	life_reset(&dm, &f);
	f.heap_dirty = 1;
	CHECK(j713_dm_run_powered(&dm, &life_ops, &f) == J713_DM_ERR_LADDER);
	CHECK(dm.ladder_stop == J713_DM_ERR_LADDER);
	CHECK(dm.first_error == J713_DM_ERR_LADDER);
	CHECK(dm.fail_stage == J713_DM_STAGE_SURFACE);
	CHECK(dm.op_calls[J713_DM_OP_UNMAP] == 1);
	CHECK(dm.op_calls[J713_DM_OP_DETACH] == 1);
	CHECK(dm.op_calls[J713_DM_OP_FREE_PAGES] == 1);
	CHECK(dm.op_calls[J713_DM_OP_REMOVE_OVERLAY] == 1);
	CHECK(dm.retain == 0);
	CHECK(dm.cleanup_state == J713_DM_CLEAN_IDLE);
	CHECK(!(dm.flags & J713_DM_F_PAGES));
	CHECK(dm.flags & J713_DM_F_POWER);
	CHECK(dm.map_count == 0);
	CHECK(dm.map_total == J713_DM_PAGE_COUNT);
	CHECK(dm.saw_power_down == 0);
	CHECK(trace_before(&dm, J713_DM_OP_DETACH, J713_DM_OP_SURFACE));
	CHECK(trace_before(&dm, J713_DM_OP_SURFACE, J713_DM_OP_FREE_PAGES));
	assert_power_held(&dm);

	life_reset(&dm, &f);
	f.text_lookup_bad = 1;
	CHECK(j713_dm_run_powered(&dm, &life_ops, &f) == -EIO);
	CHECK(dm.fail_stage == J713_DM_STAGE_LOOKUP);
	CHECK(dm.lookup_count == 0);
	CHECK(dm.op_calls[J713_DM_OP_PREMISE] == 1);
	CHECK(dm.op_calls[J713_DM_OP_ATTACH] == 1);
	CHECK(dm.op_calls[J713_DM_OP_MAP] == 1);
	CHECK(dm.op_calls[J713_DM_OP_READBACK] == 0);
	CHECK(dm.op_calls[J713_DM_OP_UNMAP] == 1);
	CHECK(dm.op_calls[J713_DM_OP_FREE_PAGES] == 1);
	CHECK(dm.op_calls[J713_DM_OP_REMOVE_OVERLAY] == 1);
	CHECK(dm.retain == 0);
	CHECK(dm.map_count == 0);
	assert_power_held(&dm);
}

static struct j713_dm survey_dm;
static struct life_fake survey_fake;
static int survey_fail_op = -1;
static int survey_fail_err;

static int survey_cb(void *ctx, struct j713_isp_state *st)
{
	int rc;

	(void)ctx;
	life_reset(&survey_dm, &survey_fake);
	survey_fake.fail_op = survey_fail_op;
	survey_fake.fail_err = survey_fail_err;
	rc = j713_dm_run_survey(&survey_dm, &life_ops, &survey_fake);
	/* A survey read error is published. The gates still go off. */
	st->retain_power = 0;
	survey_dm.retain = 0;
	return rc;
}

static void test_life_survey(void)
{
	struct j713_dm dm;
	struct life_fake f;

	life_reset(&dm, &f);
	CHECK(j713_dm_run_survey(&dm, &life_ops, &f) == 0);
	CHECK(dm.ntrace == 2);
	CHECK(dm.trace_op[0] == J713_DM_OP_CPU);
	CHECK(dm.trace_op[1] == J713_DM_OP_SURVEY);
	CHECK(dm.op_calls[J713_DM_OP_CPU] == 1);
	CHECK(dm.forward_calls[J713_DM_OP_CPU] == 1);
	CHECK(dm.op_calls[J713_DM_OP_SURVEY] == 1);
	CHECK(dm.forward_calls[J713_DM_OP_SURVEY] == 1);
	CHECK(dm.op_calls[J713_DM_OP_BASELINE] == 0);
	CHECK(dm.op_calls[J713_DM_OP_APPLY_OVERLAY] == 0);
	CHECK(dm.op_calls[J713_DM_OP_ATTACH] == 0);
	CHECK(dm.op_calls[J713_DM_OP_MAP] == 0);
	CHECK(dm.op_calls[J713_DM_OP_QUIESCE] == 0);
	CHECK(dm.op_calls[J713_DM_OP_POWER_DOWN] == 0);
	CHECK(dm.retain == 0);
	CHECK(dm.first_error == 0);
	CHECK(dm.mode == J713_DM_MODE_SURVEY);
	CHECK(dm.stage == J713_ISP_STAGE_DONE);
	CHECK(dm.flags & J713_DM_F_POWER);
	CHECK(dm.saw_power_down == 0);
	assert_power_held(&dm);

	life_reset(&dm, &f);
	f.fail_op = J713_DM_OP_SURVEY;
	f.fail_err = -EIO;
	CHECK(j713_dm_run_survey(&dm, &life_ops, &f) == -EIO);
	CHECK(dm.retain == 0);
	CHECK(dm.op_calls[J713_DM_OP_POWER_DOWN] == 0);
	CHECK(dm.op_calls[J713_DM_OP_APPLY_OVERLAY] == 0);
	CHECK(dm.op_calls[J713_DM_OP_ATTACH] == 0);
	CHECK(dm.op_calls[J713_DM_OP_MAP] == 0);
	CHECK(dm.op_calls[J713_DM_OP_SURVEY] == 1);
	CHECK(dm.first_error == -EIO);
	CHECK(dm.fail_stage == J713_DM_STAGE_SURVEY);
	CHECK(dm.flags & J713_DM_F_POWER);
	assert_power_held(&dm);

	life_reset(&dm, &f);
	f.fail_op = J713_DM_OP_CPU;
	f.fail_err = -EBUSY;
	CHECK(j713_dm_run_survey(&dm, &life_ops, &f) == -EBUSY);
	CHECK(dm.op_calls[J713_DM_OP_SURVEY] == 0);
	CHECK(dm.op_calls[J713_DM_OP_APPLY_OVERLAY] == 0);
	CHECK(dm.retain == 0);
	CHECK(dm.op_calls[J713_DM_OP_POWER_DOWN] == 0);
	CHECK(dm.first_error == -EBUSY);
	CHECK(dm.flags & J713_DM_F_POWER);
}

static void test_life_survey_restores_power(void)
{
	struct mock m;

	survey_fail_op = -1;
	survey_fail_err = 0;
	load_live(&m);
	m.isp_ver = J713_ISP_VERSION_EXACT;
	CHECK(run_with(&m, true, survey_cb) == 0);
	CHECK(m.st.power_restored == 1);
	CHECK(m.st.retain_power == 0);
	CHECK(m.st.result == 0);
	CHECK(m.nwrites == 2 * J713_ISP_LOCAL_COUNT);
	CHECK(m.writes[0].val == J713_ISP_ENABLE_VAL);
	check_restores(&m);
	CHECK(survey_dm.op_calls[J713_DM_OP_CPU] == 1);
	CHECK(survey_dm.op_calls[J713_DM_OP_SURVEY] == 1);
	CHECK(survey_dm.op_calls[J713_DM_OP_BASELINE] == 0);
	CHECK(survey_dm.op_calls[J713_DM_OP_APPLY_OVERLAY] == 0);
	CHECK(survey_dm.op_calls[J713_DM_OP_ATTACH] == 0);
	CHECK(survey_dm.op_calls[J713_DM_OP_MAP] == 0);
	CHECK(survey_dm.op_calls[J713_DM_OP_QUIESCE] == 0);
	CHECK(survey_dm.op_calls[J713_DM_OP_POWER_DOWN] == 0);
	CHECK(survey_dm.retain == 0);
	CHECK(trace_before(&survey_dm, J713_DM_OP_CPU, J713_DM_OP_SURVEY));

	survey_fail_op = J713_DM_OP_SURVEY;
	survey_fail_err = -EIO;
	load_live(&m);
	m.isp_ver = J713_ISP_VERSION_EXACT;
	CHECK(run_with(&m, true, survey_cb) == -EIO);
	CHECK(m.st.power_restored == 1);
	CHECK(m.st.retain_power == 0);
	CHECK(m.st.result == -EIO);
	check_restores(&m);
	CHECK(survey_dm.op_calls[J713_DM_OP_SURVEY] == 1);
	CHECK(survey_dm.op_calls[J713_DM_OP_APPLY_OVERLAY] == 0);
	CHECK(survey_dm.op_calls[J713_DM_OP_ATTACH] == 0);
	CHECK(survey_dm.op_calls[J713_DM_OP_MAP] == 0);
	CHECK(survey_dm.retain == 0);
	CHECK(survey_dm.first_error == -EIO);
	CHECK(m.nwrites == 2 * J713_ISP_LOCAL_COUNT);
}

static void test_life_quiesce(void)
{
	struct j713_dm dm;
	struct life_fake f;

	life_reset(&dm, &f);
	f.fail_op = J713_DM_OP_BASELINE;
	f.fail_err = -EPROTO;
	CHECK(j713_dm_run_powered(&dm, &life_ops, &f) == -EPROTO);
	CHECK(dm.op_calls[J713_DM_OP_QUIESCE] == 0);
	CHECK(dm.op_calls[J713_DM_OP_ATTACH] == 0);
	CHECK(dm.retain == 0);

	life_reset(&dm, &f);
	f.fail_op = J713_DM_OP_ATTACH;
	f.fail_err = -EIO;
	CHECK(j713_dm_run_powered(&dm, &life_ops, &f) == -EIO);
	CHECK(dm.op_calls[J713_DM_OP_ATTACH] == 1);
	CHECK(dm.op_calls[J713_DM_OP_DETACH] == 0);
	CHECK(dm.op_calls[J713_DM_OP_QUIESCE] == 0);

	life_reset(&dm, &f);
	f.fail_op = J713_DM_OP_MAP;
	f.fail_err = -EIO;
	CHECK(j713_dm_run_powered(&dm, &life_ops, &f) == -EIO);
	CHECK(dm.map_count == 0);
	CHECK(dm.op_calls[J713_DM_OP_UNMAP] == 0);
	CHECK(dm.op_calls[J713_DM_OP_DETACH] == 1);
	CHECK(dm.op_calls[J713_DM_OP_QUIESCE] == 1);
	CHECK(trace_before(&dm, J713_DM_OP_DETACH, J713_DM_OP_QUIESCE));
	CHECK(trace_before(&dm, J713_DM_OP_QUIESCE, J713_DM_OP_FREE_DOMAIN));
	CHECK(dm.op_calls[J713_DM_OP_REMOVE_OVERLAY] == 1);
	CHECK(dm.op_calls[J713_DM_OP_POWER_DOWN] == 0);
	CHECK(dm.retain == 0);
	CHECK(!(dm.flags & J713_DM_F_NEED_QUIESCE));

	life_reset(&dm, &f);
	f.fail_op = J713_DM_OP_LOOKUP;
	f.fail_err = -EIO;
	CHECK(j713_dm_run_powered(&dm, &life_ops, &f) == -EIO);
	CHECK(dm.op_calls[J713_DM_OP_QUIESCE] == 1);
	CHECK(dm.op_calls[J713_DM_OP_REMOVE_OVERLAY] == 1);
	CHECK(dm.retain == 0);
	CHECK(dm.op_calls[J713_DM_OP_POWER_DOWN] == 0);

	life_reset(&dm, &f);
	f.fail_op = J713_DM_OP_UNMAP;
	f.fail_err = -EIO;
	CHECK(j713_dm_run_powered(&dm, &life_ops, &f) == -EIO);
	CHECK(dm.op_calls[J713_DM_OP_QUIESCE] == 0);
	CHECK(dm.flags & J713_DM_F_ATTACHED);
	CHECK(dm.retain == 1);

	life_reset(&dm, &f);
	f.fail_op = J713_DM_OP_DETACH;
	f.fail_err = -EIO;
	CHECK(j713_dm_run_powered(&dm, &life_ops, &f) == -EIO);
	CHECK(dm.op_calls[J713_DM_OP_QUIESCE] == 0);
	CHECK(dm.flags & J713_DM_F_ATTACHED);
	CHECK(!(dm.flags & J713_DM_F_NEED_QUIESCE));
	CHECK(dm.retain == 1);

	life_reset(&dm, &f);
	f.fail_op = J713_DM_OP_ATTACH;
	f.fail_err = J713_DM_ERR_OWNED;
	CHECK(j713_dm_run_powered(&dm, &life_ops, &f) == J713_DM_ERR_OWNED);
	CHECK(dm.op_calls[J713_DM_OP_QUIESCE] == 1);
	CHECK(dm.retain == 0);

	life_reset(&dm, &f);
	f.cpu_fail_at = 5;
	CHECK(j713_dm_run_powered(&dm, &life_ops, &f) == -EBUSY);
	CHECK(dm.op_calls[J713_DM_OP_QUIESCE] == 0);
	CHECK(dm.flags & J713_DM_F_ATTACHED);
	CHECK(dm.op_calls[J713_DM_OP_CPU] == 5);
	f.cpu_fail_at = 0;
	f.fail_op = -1;
	CHECK(j713_dm_recover(&dm, &life_ops, &f) == 0);
	CHECK(dm.op_calls[J713_DM_OP_QUIESCE] == 1);
	CHECK(dm.op_calls[J713_DM_OP_POWER_DOWN] == 1);
	CHECK(!(dm.flags & J713_DM_F_NEED_QUIESCE));

	life_reset(&dm, &f);
	f.fail_op = J713_DM_OP_QUIESCE;
	f.fail_err = J713_DM_ERR_QUIESCE;
	CHECK(j713_dm_run_powered(&dm, &life_ops, &f) == J713_DM_ERR_QUIESCE);
	CHECK(dm.retain == 1);
	CHECK(dm.cleanup_error == J713_DM_ERR_QUIESCE);
	CHECK(dm.first_error == 0);
	CHECK(dm.fail_stage == 0);
	CHECK(dm.stage == J713_DM_STAGE_QUIESCE);
	CHECK(dm.op_calls[J713_DM_OP_FREE_DOMAIN] == 0);
	CHECK(dm.op_calls[J713_DM_OP_POWER_DOWN] == 0);
	CHECK(dm.flags & J713_DM_F_NEED_QUIESCE);
	CHECK(!(dm.flags & J713_DM_F_ATTACHED));
	CHECK(dm.flags & J713_DM_F_DOMAIN);
	CHECK(dm.flags & J713_DM_F_POWER);
	CHECK(dm.op_calls[J713_DM_OP_QUIESCE] == 1);
	assert_power_held(&dm);
	f.fail_op = -1;
	f.fail_err = 0;
	CHECK(j713_dm_recover(&dm, &life_ops, &f) == 0);
	CHECK(dm.op_calls[J713_DM_OP_QUIESCE] == 2);
	CHECK(dm.op_calls[J713_DM_OP_POWER_DOWN] == 1);
	CHECK(!(dm.flags & J713_DM_F_NEED_QUIESCE));
	CHECK(dm.flags == 0);
}

static void test_run_fwview(void)
{
	test_layout_math();
	test_life_happy();
	test_life_early_fail_restores_via_caller();
	test_life_overlay_owned_then_removed();
	test_life_teardown_failure_retains();
	test_life_detach_and_page_failures();
	test_life_partial_map_unmaps();
	test_life_cpu_run_retains_without_free();
	test_life_busy_and_power_block();
	test_preflight_recovery_cases();
	test_life_failure_matrix();
	test_retain_power_callback();
	test_premise_aborts_before_map();
	test_ladder_and_text_lookup();
	test_life_survey();
	test_life_survey_restores_power();
	test_life_quiesce();
}
