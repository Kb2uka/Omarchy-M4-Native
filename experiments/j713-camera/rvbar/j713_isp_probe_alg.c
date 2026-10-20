/* SPDX-License-Identifier: GPL-2.0-only */
/* Shared identity-probe power sequence. Native 16b path writes plain 0xf.
 * The optional inspection callback is the only reader of COPROC, GPIO,
 * DART, and RVBAR. It does not write those blocks, launch firmware, or
 * program DMA. RVBAR is one 64-bit read after the 37 words.
 */
#include "j713_isp_probe_alg.h"
#ifndef __KERNEL__
#include <stdio.h>
#endif

/* Nine T8110 words per DART: params, PROTECT, SID0 TCR/TTBR, SID12 TCR/TTBR.
 * PROTECT_LOCK 0x208, ERROR, IRQ, FIFO, and TLB command are not in this table.
 */
#define DART_ROW(inst, tag, off, base) \
	{ "dart" #inst "-" tag, J713_ISP_SPACE_DART0 + (inst), (off), (base) + (u64)(off) }

static const struct j713_isp_inspect_word j713_isp_inspect_words[] = {
	{ "coproc-ctrl", J713_ISP_SPACE_COPROC, J713_ISP_COPROC_CTRL,
	  J713_ISP_COPROC_PHYS + J713_ISP_COPROC_CTRL },
	{ "coproc-status", J713_ISP_SPACE_COPROC, J713_ISP_COPROC_STATUS,
	  J713_ISP_COPROC_PHYS + J713_ISP_COPROC_STATUS },
	{ "gpio0", J713_ISP_SPACE_GPIO, 0x00u, J713_ISP_GPIO_PHYS + 0x00u },
	{ "gpio1", J713_ISP_SPACE_GPIO, 0x04u, J713_ISP_GPIO_PHYS + 0x04u },
	{ "gpio2", J713_ISP_SPACE_GPIO, 0x08u, J713_ISP_GPIO_PHYS + 0x08u },
	{ "gpio3", J713_ISP_SPACE_GPIO, 0x0cu, J713_ISP_GPIO_PHYS + 0x0cu },
	{ "gpio4", J713_ISP_SPACE_GPIO, 0x10u, J713_ISP_GPIO_PHYS + 0x10u },
	{ "gpio5", J713_ISP_SPACE_GPIO, 0x14u, J713_ISP_GPIO_PHYS + 0x14u },
	{ "gpio6", J713_ISP_SPACE_GPIO, 0x18u, J713_ISP_GPIO_PHYS + 0x18u },
	{ "gpio7", J713_ISP_SPACE_GPIO, 0x1cu, J713_ISP_GPIO_PHYS + 0x1cu },
	DART_ROW(0, "params1", J713_ISP_DART_PARAMS1, J713_ISP_DART0_PHYS),
	DART_ROW(0, "params2", J713_ISP_DART_PARAMS2, J713_ISP_DART0_PHYS),
	DART_ROW(0, "params3", J713_ISP_DART_PARAMS3, J713_ISP_DART0_PHYS),
	DART_ROW(0, "params4", J713_ISP_DART_PARAMS4, J713_ISP_DART0_PHYS),
	DART_ROW(0, "protect", J713_ISP_DART_PROTECT, J713_ISP_DART0_PHYS),
	DART_ROW(0, "sid0-tcr", J713_ISP_DART_SID0_TCR, J713_ISP_DART0_PHYS),
	DART_ROW(0, "sid0-ttbr", J713_ISP_DART_SID0_TTBR, J713_ISP_DART0_PHYS),
	DART_ROW(0, "sid12-tcr", J713_ISP_DART_SID12_TCR, J713_ISP_DART0_PHYS),
	DART_ROW(0, "sid12-ttbr", J713_ISP_DART_SID12_TTBR, J713_ISP_DART0_PHYS),
	DART_ROW(1, "params1", J713_ISP_DART_PARAMS1, J713_ISP_DART1_PHYS),
	DART_ROW(1, "params2", J713_ISP_DART_PARAMS2, J713_ISP_DART1_PHYS),
	DART_ROW(1, "params3", J713_ISP_DART_PARAMS3, J713_ISP_DART1_PHYS),
	DART_ROW(1, "params4", J713_ISP_DART_PARAMS4, J713_ISP_DART1_PHYS),
	DART_ROW(1, "protect", J713_ISP_DART_PROTECT, J713_ISP_DART1_PHYS),
	DART_ROW(1, "sid0-tcr", J713_ISP_DART_SID0_TCR, J713_ISP_DART1_PHYS),
	DART_ROW(1, "sid0-ttbr", J713_ISP_DART_SID0_TTBR, J713_ISP_DART1_PHYS),
	DART_ROW(1, "sid12-tcr", J713_ISP_DART_SID12_TCR, J713_ISP_DART1_PHYS),
	DART_ROW(1, "sid12-ttbr", J713_ISP_DART_SID12_TTBR, J713_ISP_DART1_PHYS),
	DART_ROW(2, "params1", J713_ISP_DART_PARAMS1, J713_ISP_DART2_PHYS),
	DART_ROW(2, "params2", J713_ISP_DART_PARAMS2, J713_ISP_DART2_PHYS),
	DART_ROW(2, "params3", J713_ISP_DART_PARAMS3, J713_ISP_DART2_PHYS),
	DART_ROW(2, "params4", J713_ISP_DART_PARAMS4, J713_ISP_DART2_PHYS),
	DART_ROW(2, "protect", J713_ISP_DART_PROTECT, J713_ISP_DART2_PHYS),
	DART_ROW(2, "sid0-tcr", J713_ISP_DART_SID0_TCR, J713_ISP_DART2_PHYS),
	DART_ROW(2, "sid0-ttbr", J713_ISP_DART_SID0_TTBR, J713_ISP_DART2_PHYS),
	DART_ROW(2, "sid12-tcr", J713_ISP_DART_SID12_TCR, J713_ISP_DART2_PHYS),
	DART_ROW(2, "sid12-ttbr", J713_ISP_DART_SID12_TTBR, J713_ISP_DART2_PHYS),
};

_Static_assert(ARRAY_SIZE(j713_isp_inspect_words) == J713_ISP_INSPECT_COUNT,
	       "inspection plan must be 37 words");
_Static_assert(J713_ISP_INSPECT_COUNT == 37u, "2 coproc + 8 gpio + 27 dart");
_Static_assert(J713_ISP_SPACE_DART1 == J713_ISP_SPACE_DART0 + 1, "dart space");
_Static_assert(J713_ISP_SPACE_DART2 == J713_ISP_SPACE_DART0 + 2, "dart space");
_Static_assert(J713_ISP_DART_SID12_TCR == 0x1030u, "sid12 tcr");
_Static_assert(J713_ISP_DART_SID12_TTBR == 0x1430u, "sid12 ttbr");
_Static_assert(J713_ISP_COPROC_PHYS == 0x48d600044ULL, "coproc phys");
_Static_assert(J713_ISP_GPIO_PHYS == 0x48e9241d0ULL, "gpio phys");
_Static_assert((J713_ISP_VERSION_EXACT >> J713_ISP_VERSION_GEN_SHIFT) ==
	       J713_ISP_VERSION_GEN, "exact identity generation");
_Static_assert(J713_ISP_RVBAR_REL == 0x1050000u, "rvbar relative");
_Static_assert(J713_ISP_RVBAR_PHYS == 0x48d050000ULL, "rvbar phys");
_Static_assert(J713_ISP_RVBAR_SIZE == 8u, "rvbar is one u64");
_Static_assert(J713_ISP_WIN_RVBAR + 1u == J713_ISP_WIN_COUNT, "rvbar last claim");
_Static_assert(J713_ISP_RVBAR_PHYS + J713_ISP_RVBAR_SIZE <= J713_ISP_COPROC_PHYS,
	       "rvbar does not reach coproc");
_Static_assert(J713_ISP_RVBAR_PHYS + J713_ISP_RVBAR_SIZE <= J713_ISP_VER_PHYS,
	       "rvbar does not reach version");

static void probe_log(const struct j713_isp_ops *ops, void *ctx, const char *msg);

const struct j713_isp_inspect_word *j713_isp_inspect_plan(unsigned int *count)
{
	if (count)
		*count = ARRAY_SIZE(j713_isp_inspect_words);
	return j713_isp_inspect_words;
}

static void log_inspect(const struct j713_isp_ops *ops, void *ctx,
			const struct j713_isp_inspect_word *word, u32 val)
{
	char msg[160];

#ifdef __KERNEL__
	scnprintf(msg, sizeof(msg),
		  "inspect bank=%s phys=0x%llx off=0x%x val=0x%x",
		  word->bank, (unsigned long long)word->phys, word->off, val);
#else
	snprintf(msg, sizeof(msg),
		 "inspect bank=%s phys=0x%llx off=0x%x val=0x%x",
		 word->bank, (unsigned long long)word->phys, word->off, val);
#endif
	probe_log(ops, ctx, msg);
}

static void probe_log(const struct j713_isp_ops *ops, void *ctx, const char *msg)
{
	if (ops->log)
		ops->log(ctx, msg);
}

int j713_isp_inspect_read(const struct j713_isp_ops *ops, void *ctx,
			  struct j713_isp_state *st)
{
	unsigned int n = 0;
	unsigned int i;
	const struct j713_isp_inspect_word *plan;

	if (!ops || !ops->read32 || !st)
		return -EINVAL;
	plan = j713_isp_inspect_plan(&n);
	if (!plan || n != J713_ISP_INSPECT_COUNT)
		return -EINVAL;
	/* One walk. A second call must not read the block again. */
	if (st->inspect_reads)
		return -EALREADY;
	for (i = 0; i < n; i++) {
		u32 val = 0;
		int ret;

		if (!j713_isp_inspect_off_ok(plan[i].space, plan[i].off))
			return -EINVAL;
		ret = ops->read32(ctx, plan[i].space, plan[i].off, &val);
		if (ret)
			return ret;
		st->inspect_val[i] = val;
		st->inspect_reads = i + 1;
		log_inspect(ops, ctx, &plan[i], val);
	}
	return 0;
}

static void log_rvbar(const struct j713_isp_ops *ops, void *ctx, u64 val)
{
	char msg[96];

#ifdef __KERNEL__
	scnprintf(msg, sizeof(msg), "rvbar phys=0x%llx val=0x%llx",
		  (unsigned long long)J713_ISP_RVBAR_PHYS,
		  (unsigned long long)val);
#else
	snprintf(msg, sizeof(msg), "rvbar phys=0x%llx val=0x%llx",
		 (unsigned long long)J713_ISP_RVBAR_PHYS,
		 (unsigned long long)val);
#endif
	probe_log(ops, ctx, msg);
}

static bool rvbar_window_open(const struct j713_isp_state *st)
{
	return st->stage == J713_ISP_STAGE_INSPECT &&
	       st->fail_stage == 0 &&
	       st->attempted == J713_ISP_LOCAL_COUNT &&
	       st->isp_reads == 1 &&
	       st->inspect_reads == J713_ISP_INSPECT_COUNT &&
	       st->version == J713_ISP_VERSION_EXACT;
}

int j713_isp_rvbar_read(const struct j713_isp_ops *ops, void *ctx,
			struct j713_isp_state *st, u32 off)
{
	u64 val = 0;
	int ret;

	if (!ops || !ops->read64 || !st)
		return -EINVAL;
	/* Any neighbor, including a 32-bit slot inside the u64, is refused
	 * before the injected read. The claim itself is only 8 bytes.
	 */
	if (off != 0)
		return -EINVAL;
	if (st->rvbar_reads)
		return -EALREADY;
	/* Power-off, identity-only, a short ACK run, a wrong version, and
	 * a partial 37-word walk all fail closed without touching RVBAR.
	 */
	if (!rvbar_window_open(st))
		return -EPERM;
	ret = ops->read64(ctx, J713_ISP_SPACE_RVBAR, 0, &val);
	if (ret)
		return ret;
	st->raw_rvbar = val;
	st->rvbar_reads = 1;
	log_rvbar(ops, ctx, val);
	return 0;
}

int j713_isp_inspect_then_rvbar(const struct j713_isp_ops *ops, void *ctx,
				struct j713_isp_state *st)
{
	int ret;

	ret = j713_isp_inspect_read(ops, ctx, st);
	if (ret)
		return ret;
	return j713_isp_rvbar_read(ops, ctx, st, 0);
}

static void log_word(const struct j713_isp_ops *ops, void *ctx, const char *tag,
		     u32 off, u32 val, int err)
{
	char msg[96];

#ifdef __KERNEL__
	scnprintf(msg, sizeof(msg), "%s off=0x%x val=0x%x err=%d",
		  tag, off, val, err);
#else
	snprintf(msg, sizeof(msg), "%s off=0x%x val=0x%x err=%d",
		 tag, off, val, err);
#endif
	probe_log(ops, ctx, msg);
}

static int read_pmgr(const struct j713_isp_ops *ops, void *ctx, u32 off, u32 *val)
{
	return ops->read32(ctx, J713_ISP_SPACE_PMGR, off, val);
}

static u32 main_obs_mask(u32 off)
{
	if (off == J713_ISP_MAIN_530)
		return J713_ISP_OBS_MASK | J713_ISP_MAIN_530_EXTRA;
	return J713_ISP_OBS_MASK;
}

static int check_main(u32 off, u32 val)
{
	if ((val & J713_ISP_LOW_MASK) != J713_ISP_LOW_MASK)
		return -EINVAL;
	if (val & J713_ISP_BUSY)
		return -EINVAL;
	if (val & ~main_obs_mask(off))
		return -EINVAL;
	return 0;
}

static int check_local_req(u32 val, u32 expected_req)
{
	u32 req = val & J713_ISP_REQ_MASK;
	u32 actual = (val >> 4) & J713_ISP_REQ_MASK;

	if (expected_req != J713_ISP_REQ_OFF &&
	    expected_req != J713_ISP_REQ_PARTIAL)
		return -EINVAL;
	if (val & ~J713_ISP_OBS_MASK)
		return -EINVAL;
	if (val & J713_ISP_BUSY)
		return -EINVAL;
	if (req != expected_req || actual != expected_req)
		return -EINVAL;
	if ((val & J713_ISP_LOW_MASK) != j713_isp_ack_low(expected_req))
		return -EINVAL;
	return 0;
}

static void capture_fail(struct j713_isp_state *st, int stage_now)
{
	if (!st->fail_stage)
		st->fail_stage = stage_now;
}

static int snapshot(const struct j713_isp_ops *ops, void *ctx,
		    struct j713_isp_state *st)
{
	static const u32 mains[] = {
		J713_ISP_MAIN_188, J713_ISP_MAIN_190, J713_ISP_MAIN_530
	};
	unsigned int i;
	u32 val;
	int ret;

	st->stage = J713_ISP_STAGE_SNAPSHOT;
	probe_log(ops, ctx, "snapshot");
	for (i = 0; i < ARRAY_SIZE(mains); i++) {
		ret = read_pmgr(ops, ctx, mains[i], &val);
		if (ret)
			return ret;
		ret = check_main(mains[i], val);
		if (ret)
			return ret;
	}
	for (i = 0; i < J713_ISP_LOCAL_COUNT; i++) {
		u32 expect = (i == J713_ISP_RESTORE_INDEX) ?
			     J713_ISP_REQ_PARTIAL : J713_ISP_REQ_OFF;

		ret = read_pmgr(ops, ctx, j713_isp_local_off(i), &val);
		if (ret)
			return ret;
		ret = check_local_req(val, expect);
		if (ret)
			return ret;
		st->saved_req[i] = expect;
	}
	return 0;
}

static int restore_changed(const struct j713_isp_ops *ops, void *ctx,
			   struct j713_isp_state *st)
{
	unsigned int n = st->attempted;
	int first = 0;
	int ret;

	probe_log(ops, ctx, "cleanup");
	if (n > J713_ISP_LOCAL_COUNT)
		n = J713_ISP_LOCAL_COUNT;
	while (n) {
		u32 off;
		u32 req;

		n--;
		off = j713_isp_local_off(n);
		req = st->saved_req[n];
		if (req != J713_ISP_REQ_OFF && req != J713_ISP_REQ_PARTIAL) {
			if (!first)
				first = -EINVAL;
			continue;
		}
		ret = ops->write32(ctx, J713_ISP_SPACE_PMGR, off, req);
		if (ret) {
			if (!first)
				first = ret;
			continue;
		}
		ret = ops->poll_ps(ctx, off, j713_isp_ack_low(req));
		if (ret && !first)
			first = ret;
	}
	return first;
}

static int verify_restored(const struct j713_isp_ops *ops, void *ctx,
			   struct j713_isp_state *st)
{
	static const u32 mains[] = {
		J713_ISP_MAIN_188, J713_ISP_MAIN_190, J713_ISP_MAIN_530
	};
	unsigned int i;
	u32 val;
	int first = 0;
	int ret;

	probe_log(ops, ctx, "verify");
	for (i = 0; i < ARRAY_SIZE(mains); i++) {
		val = 0;
		ret = read_pmgr(ops, ctx, mains[i], &val);
		if (!ret)
			ret = check_main(mains[i], val);
		if (ret) {
			log_word(ops, ctx, "verify-main", mains[i], val, ret);
			if (!first)
				first = ret;
		}
	}
	for (i = 0; i < J713_ISP_LOCAL_COUNT; i++) {
		u32 off = j713_isp_local_off(i);

		val = 0;
		ret = read_pmgr(ops, ctx, off, &val);
		if (!ret)
			ret = check_local_req(val, st->saved_req[i]);
		if (ret) {
			log_word(ops, ctx, "verify-local", off, val, ret);
			if (!first)
				first = ret;
		}
	}
	if (first)
		probe_log(ops, ctx, "verify-fail");
	return first;
}

int j713_isp_probe_run(const struct j713_isp_ops *ops, void *ctx, bool apply,
		       j713_isp_inspect_fn inspect, struct j713_isp_state *st)
{
	unsigned int i;
	int first = 0;
	int ret;

	if (!ops || !ops->read32 || !ops->write32 || !ops->poll_ps || !st)
		return -EINVAL;
	st->result = -EAGAIN;
	st->cleanup = 0;
	st->verify = 0;
	st->fail_stage = 0;
	st->attempted = 0;
	st->isp_reads = 0;
	st->inspect_reads = 0;
	st->rvbar_reads = 0;
	st->version = 0;
	st->raw_rvbar = 0;
	for (i = 0; i < J713_ISP_LOCAL_COUNT; i++)
		st->saved_req[i] = 0;
	for (i = 0; i < J713_ISP_INSPECT_COUNT; i++)
		st->inspect_val[i] = 0;

	ret = snapshot(ops, ctx, st);
	if (ret) {
		st->stage = J713_ISP_STAGE_SNAPSHOT;
		st->fail_stage = J713_ISP_STAGE_SNAPSHOT;
		st->result = ret;
		probe_log(ops, ctx, "snapshot-fail");
		return ret;
	}
	if (!apply) {
		st->stage = J713_ISP_STAGE_DONE;
		st->result = 0;
		probe_log(ops, ctx, "validate-only");
		return 0;
	}

	st->stage = J713_ISP_STAGE_ENABLE;
	probe_log(ops, ctx, "enable");
	for (i = 0; i < J713_ISP_LOCAL_COUNT; i++) {
		u32 off = j713_isp_local_off(i);

		st->attempted++;
		ret = ops->write32(ctx, J713_ISP_SPACE_PMGR, off,
				   J713_ISP_ENABLE_VAL);
		if (ret) {
			first = ret;
			capture_fail(st, J713_ISP_STAGE_ENABLE);
			probe_log(ops, ctx, "enable-write-fail");
			break;
		}
		ret = ops->poll_ps(ctx, off, J713_ISP_LOW_MASK);
		if (ret) {
			first = ret;
			capture_fail(st, J713_ISP_STAGE_ENABLE);
			probe_log(ops, ctx, "enable-ack-fail");
			break;
		}
	}

	if (!first) {
		st->stage = J713_ISP_STAGE_VERSION;
		probe_log(ops, ctx, "version");
		ret = ops->read32(ctx, J713_ISP_SPACE_VER, 0, &st->version);
		if (ret) {
			first = ret;
			capture_fail(st, J713_ISP_STAGE_VERSION);
		} else {
			st->isp_reads++;
			if ((st->version >> J713_ISP_VERSION_GEN_SHIFT) !=
			    J713_ISP_VERSION_GEN) {
				first = J713_ISP_ERR_GENERATION;
				capture_fail(st, J713_ISP_STAGE_VERSION);
				probe_log(ops, ctx, "generation-mismatch");
			} else if (inspect &&
				   st->version != J713_ISP_VERSION_EXACT) {
				first = J713_ISP_ERR_EXACT;
				capture_fail(st, J713_ISP_STAGE_VERSION);
				probe_log(ops, ctx, "version-not-exact");
			} else if (inspect) {
				st->stage = J713_ISP_STAGE_INSPECT;
				probe_log(ops, ctx, "inspect");
				ret = inspect(ctx, st);
				if (ret) {
					first = ret;
					capture_fail(st, J713_ISP_STAGE_INSPECT);
					probe_log(ops, ctx, "inspect-fail");
				}
			}
		}
	}

	st->stage = J713_ISP_STAGE_CLEANUP;
	st->cleanup = restore_changed(ops, ctx, st);
	if (st->cleanup)
		capture_fail(st, J713_ISP_STAGE_CLEANUP);

	st->stage = J713_ISP_STAGE_VERIFY;
	st->verify = verify_restored(ops, ctx, st);
	if (st->verify)
		capture_fail(st, J713_ISP_STAGE_VERIFY);

	if (first)
		st->result = first;
	else if (st->cleanup)
		st->result = st->cleanup;
	else
		st->result = st->verify;
	if (!st->result)
		st->stage = J713_ISP_STAGE_DONE;
	else
		st->stage = st->fail_stage;
	probe_log(ops, ctx, st->result ? "fail" : "done");
	return st->result;
}
