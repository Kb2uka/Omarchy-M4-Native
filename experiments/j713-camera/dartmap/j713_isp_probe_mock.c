/* SPDX-License-Identifier: GPL-2.0-only */
/* Userspace mock of the shared identity-probe algorithm. No MMIO. */
#include "j713_isp_probe_alg.h"
#include "j713_isp_probe_alg.c"

#include <stdio.h>
#include <string.h>

#define LOG_CAP 64
#define PMGR_WORDS (J713_ISP_PMGR_SIZE / 4)

struct mock_write {
	u32 off;
	u32 val;
};

struct mock_poll {
	u32 off;
	u32 want_low;
};

struct mock {
	u32 pmgr[PMGR_WORDS];
	u32 isp_ver;
	u32 inspect_mem[J713_ISP_INSPECT_COUNT];
	char logs[J713_ISP_INSPECT_COUNT][160];
	struct mock_write writes[LOG_CAP];
	struct mock_poll polls[LOG_CAP];
	unsigned nwrites;
	unsigned npolls;
	unsigned nreads_ver;
	unsigned nreads_pmgr;
	unsigned nreads_inspect;
	unsigned nlogs;
	unsigned enable_polls;
	unsigned cleanup_polls;
	int fail_enable;	/* 0-based enable poll to fail; -1 none */
	int fail_cleanup_off;	/* local offset whose restore poll fails; -1 none */
	int verify_or_off;	/* PMGR offset corrupted on post-cleanup reads; -1 none */
	int fail_inspect_read;	/* plan index whose read fails; -1 none */
	int inspect_cb_error;	/* non-zero: callback fails before any inspect read */
	int fail_ver;		/* version read32 fails */
	int inspect_calls;
	int inspect_at_writes;
	int inspect_at_enable_polls;
	int inspect_at_cleanup_polls;
	int inspect_at_ver_reads;
	u32 inspect_seen_version;
	u32 verify_or;
	struct j713_isp_state st;
};

static const u32 live_local[J713_ISP_LOCAL_COUNT] = {
	0x300, 0xf000300, 0xf000300, 0x300, 0x244,
	0x300, 0x300, 0x300, 0x300, 0x300, 0x300, 0x300, 0x300
};

static int failures;

static void fail_at(const char *name, int line, const char *why)
{
	fprintf(stderr, "FAIL %s:%d %s\n", name, line, why);
	failures++;
}

#define CHECK(cond) do { \
	if (!(cond)) fail_at(__func__, __LINE__, #cond); \
} while (0)

static void load_live(struct mock *m)
{
	unsigned i;

	memset(m, 0, sizeof(*m));
	m->pmgr[J713_ISP_MAIN_188 / 4] = 0x040002ff;
	m->pmgr[J713_ISP_MAIN_190 / 4] = 0x040002ff;
	m->pmgr[J713_ISP_MAIN_530 / 4] = 0x000023ff;
	for (i = 0; i < J713_ISP_LOCAL_COUNT; i++)
		m->pmgr[j713_isp_local_off(i) / 4] = live_local[i];
	m->isp_ver = J713_ISP_VERSION_GEN << J713_ISP_VERSION_GEN_SHIFT;
	m->fail_enable = -1;
	m->fail_cleanup_off = -1;
	m->verify_or_off = -1;
	m->fail_inspect_read = -1;
	{
		unsigned int n = 0;
		unsigned int i;
		const struct j713_isp_inspect_word *plan = j713_isp_inspect_plan(&n);

		for (i = 0; i < n && i < J713_ISP_INSPECT_COUNT; i++)
			m->inspect_mem[i] = 0xA5000000u ^ plan[i].off ^
					    ((u32)plan[i].space << 16);
	}
}

static int mock_read32(void *ctx, int space, u32 off, u32 *val)
{
	struct mock *m = ctx;

	if (!val)
		return -EINVAL;
	if (space == J713_ISP_SPACE_VER) {
		if (off)
			return -EINVAL;
		if (m->fail_ver)
			return -EIO;
		m->nreads_ver++;
		*val = m->isp_ver;
		return 0;
	}
	if (j713_isp_inspect_off_ok(space, off)) {
		unsigned int n = 0;
		unsigned int i;
		const struct j713_isp_inspect_word *plan = j713_isp_inspect_plan(&n);

		for (i = 0; i < n; i++) {
			if (plan[i].space != space || plan[i].off != off)
				continue;
			if (m->fail_inspect_read >= 0 &&
			    (unsigned)m->fail_inspect_read == i)
				return -EIO;
			m->nreads_inspect++;
			*val = m->inspect_mem[i];
			return 0;
		}
		return -EINVAL;
	}
	if (space != J713_ISP_SPACE_PMGR)
		return -EINVAL;
	if (off + 4 > J713_ISP_PMGR_SIZE || (off & 3))
		return -EINVAL;
	m->nreads_pmgr++;
	*val = m->pmgr[off / 4];
	if (m->verify_or_off >= 0 && m->cleanup_polls > 0 &&
	    off == (u32)m->verify_or_off)
		*val |= m->verify_or;
	return 0;
}

static int mock_write32(void *ctx, int space, u32 off, u32 val)
{
	struct mock *m = ctx;

	if (space != J713_ISP_SPACE_PMGR)
		return -EINVAL;
	if (off < J713_ISP_LOCAL_BASE || off > 0x4060 || (off & 7))
		return -EINVAL;
	if (m->nwrites >= LOG_CAP)
		return -ENOMEM;
	m->writes[m->nwrites].off = off;
	m->writes[m->nwrites].val = val;
	m->nwrites++;
	/* Keep previous actual until poll acknowledges requested-to-actual. */
	{
		u32 prev_actual = (m->pmgr[off / 4] >> 4) & J713_ISP_REQ_MASK;

		m->pmgr[off / 4] = (prev_actual << 4) | (val & J713_ISP_REQ_MASK);
	}
	return 0;
}

static int mock_poll_ps(void *ctx, u32 off, u32 want_low)
{
	struct mock *m = ctx;

	if (off < J713_ISP_LOCAL_BASE || off > 0x4060 || (off & 7))
		return -EINVAL;
	if (m->npolls >= LOG_CAP)
		return -ENOMEM;
	m->polls[m->npolls].off = off;
	m->polls[m->npolls].want_low = want_low;
	m->npolls++;
	if (want_low == J713_ISP_LOW_MASK) {
		if (m->fail_enable >= 0 &&
		    (int)m->enable_polls == m->fail_enable)
			return -ETIMEDOUT;
		m->enable_polls++;
	} else {
		m->cleanup_polls++;
		if (m->fail_cleanup_off >= 0 && (int)off == m->fail_cleanup_off)
			return -ETIMEDOUT;
	}
	m->pmgr[off / 4] = (m->pmgr[off / 4] & ~J713_ISP_LOW_MASK) | want_low;
	return 0;
}

static void mock_log(void *ctx, const char *msg)
{
	struct mock *m = ctx;

	if (!msg || strncmp(msg, "inspect ", 8) != 0)
		return;
	if (m->nlogs < J713_ISP_INSPECT_COUNT)
		snprintf(m->logs[m->nlogs], sizeof(m->logs[0]), "%s", msg);
	m->nlogs++;
}

static const struct j713_isp_ops mock_ops = {
	.read32 = mock_read32,
	.write32 = mock_write32,
	.poll_ps = mock_poll_ps,
	.log = mock_log,
};

static int mock_inspect(void *ctx, struct j713_isp_state *st)
{
	struct mock *m = ctx;
	unsigned int writes;
	int ret;

	m->inspect_calls++;
	m->inspect_at_writes = (int)m->nwrites;
	m->inspect_at_enable_polls = (int)m->enable_polls;
	m->inspect_at_cleanup_polls = (int)m->cleanup_polls;
	m->inspect_at_ver_reads = (int)m->nreads_ver;
	m->inspect_seen_version = st->version;
	if (m->inspect_cb_error)
		return m->inspect_cb_error;
	writes = m->nwrites;
	ret = j713_isp_inspect_read(&mock_ops, ctx, st);
	CHECK(m->nwrites == writes);
	return ret;
}

static int run_with(struct mock *m, bool apply, j713_isp_inspect_fn inspect)
{
	return j713_isp_probe_run(&mock_ops, m, apply, inspect, &m->st);
}

static int run(struct mock *m, bool apply)
{
	return run_with(m, apply, NULL);
}

static int run_inspect(struct mock *m)
{
	return run_with(m, true, mock_inspect);
}

static void test_validate_only(void)
{
	struct mock m;

	load_live(&m);
	CHECK(run(&m, false) == 0);
	CHECK(m.st.result == 0);
	CHECK(m.st.cleanup == 0);
	CHECK(m.st.verify == 0);
	CHECK(m.st.fail_stage == 0);
	CHECK(m.st.attempted == 0);
	CHECK(m.st.isp_reads == 0);
	CHECK(m.nwrites == 0);
	CHECK(m.npolls == 0);
	CHECK(m.nreads_ver == 0);
	CHECK(m.nreads_inspect == 0);
	CHECK(m.st.inspect_reads == 0);
	CHECK(m.inspect_calls == 0);
	CHECK(m.nreads_pmgr >= 16);
	CHECK(m.st.saved_req[J713_ISP_RESTORE_INDEX] == J713_ISP_REQ_PARTIAL);
}

static void test_good_path(void)
{
	struct mock m;
	unsigned i, w;

	load_live(&m);
	CHECK(run(&m, true) == 0);
	CHECK(m.st.result == 0);
	CHECK(m.st.cleanup == 0);
	CHECK(m.st.verify == 0);
	CHECK(m.st.fail_stage == 0);
	CHECK(m.st.attempted == J713_ISP_LOCAL_COUNT);
	CHECK(m.st.isp_reads == 1);
	CHECK(m.st.inspect_reads == 0);
	CHECK(m.nreads_ver == 1);
	CHECK(m.nreads_inspect == 0);
	CHECK(m.nlogs == 0);
	CHECK(m.nreads_pmgr == 32);
	CHECK(m.st.version ==
	      (J713_ISP_VERSION_GEN << J713_ISP_VERSION_GEN_SHIFT));
	CHECK(m.nwrites == 2 * J713_ISP_LOCAL_COUNT);
	CHECK(m.npolls == 2 * J713_ISP_LOCAL_COUNT);
	CHECK(m.enable_polls == J713_ISP_LOCAL_COUNT);
	CHECK(m.cleanup_polls == J713_ISP_LOCAL_COUNT);
	for (i = 0; i < J713_ISP_LOCAL_COUNT; i++) {
		CHECK(m.writes[i].off == j713_isp_local_off(i));
		CHECK(m.writes[i].val == J713_ISP_ENABLE_VAL);
		CHECK(m.polls[i].off == j713_isp_local_off(i));
		CHECK(m.polls[i].want_low == J713_ISP_LOW_MASK);
	}
	w = J713_ISP_LOCAL_COUNT;
	for (i = J713_ISP_LOCAL_COUNT; i > 0; i--) {
		unsigned idx = i - 1;
		u32 want = (idx == J713_ISP_RESTORE_INDEX) ?
			   J713_ISP_REQ_PARTIAL : J713_ISP_REQ_OFF;

		CHECK(m.writes[w].off == j713_isp_local_off(idx));
		CHECK(m.writes[w].val == want);
		CHECK(m.polls[w].off == j713_isp_local_off(idx));
		CHECK(m.polls[w].want_low == j713_isp_ack_low(want));
		w++;
	}
	CHECK(m.nreads_ver == 1);
	CHECK(m.pmgr[j713_isp_local_off(J713_ISP_RESTORE_INDEX) / 4] ==
	      j713_isp_ack_low(J713_ISP_REQ_PARTIAL));
	for (i = 0; i < J713_ISP_LOCAL_COUNT; i++) {
		if (i == J713_ISP_RESTORE_INDEX)
			continue;
		CHECK(m.pmgr[j713_isp_local_off(i) / 4] == 0);
	}
	CHECK(m.pmgr[J713_ISP_MAIN_188 / 4] == 0x040002ff);
	CHECK(m.pmgr[J713_ISP_MAIN_530 / 4] == 0x000023ff);
}

static void test_fail_each_enable(void)
{
	unsigned idx, i, w;

	for (idx = 0; idx < J713_ISP_LOCAL_COUNT; idx++) {
		struct mock m;

		load_live(&m);
		m.fail_enable = (int)idx;
		CHECK(run(&m, true) == -ETIMEDOUT);
		CHECK(m.st.result == -ETIMEDOUT);
		CHECK(m.st.fail_stage == J713_ISP_STAGE_ENABLE);
		CHECK(m.st.stage == J713_ISP_STAGE_ENABLE);
		CHECK(m.st.verify == 0);
		CHECK(m.st.cleanup == 0);
		CHECK(m.st.attempted == idx + 1);
		CHECK(m.st.isp_reads == 0);
		CHECK(m.nreads_ver == 0);
		CHECK(m.nwrites == 2 * (idx + 1));
		for (i = 0; i <= idx; i++) {
			CHECK(m.writes[i].off == j713_isp_local_off(i));
			CHECK(m.writes[i].val == J713_ISP_ENABLE_VAL);
		}
		w = idx + 1;
		for (i = idx + 1; i > 0; i--) {
			unsigned ridx = i - 1;
			u32 want = (ridx == J713_ISP_RESTORE_INDEX) ?
				   J713_ISP_REQ_PARTIAL : J713_ISP_REQ_OFF;

			CHECK(m.writes[w].off == j713_isp_local_off(ridx));
			CHECK(m.writes[w].val == want);
			w++;
		}
	}
}

static void test_cleanup_continues(void)
{
	struct mock m;
	unsigned i, restores, seen_4020;

	load_live(&m);
	m.fail_cleanup_off = (int)j713_isp_local_off(J713_ISP_LOCAL_COUNT - 1);
	CHECK(run(&m, true) == -ETIMEDOUT);
	CHECK(m.st.result == -ETIMEDOUT);
	CHECK(m.st.cleanup == -ETIMEDOUT);
	CHECK(m.st.verify == -EINVAL);
	CHECK(m.st.fail_stage == J713_ISP_STAGE_CLEANUP);
	CHECK(m.st.isp_reads == 1);
	CHECK(m.nreads_ver == 1);
	CHECK(m.nwrites == 2 * J713_ISP_LOCAL_COUNT);
	CHECK(m.cleanup_polls == J713_ISP_LOCAL_COUNT);
	restores = 0;
	seen_4020 = 0;
	for (i = J713_ISP_LOCAL_COUNT; i < m.nwrites; i++) {
		restores++;
		if (m.writes[i].off == j713_isp_local_off(J713_ISP_RESTORE_INDEX)) {
			CHECK(m.writes[i].val == J713_ISP_REQ_PARTIAL);
			seen_4020++;
		}
	}
	CHECK(restores == J713_ISP_LOCAL_COUNT);
	CHECK(seen_4020 == 1);
	CHECK(m.writes[J713_ISP_LOCAL_COUNT].off ==
	      j713_isp_local_off(J713_ISP_LOCAL_COUNT - 1));
	CHECK(m.writes[2 * J713_ISP_LOCAL_COUNT - 1].off ==
	      j713_isp_local_off(0));
}

static void test_invalid_writes_nothing(void)
{
	struct mock m;

	load_live(&m);
	m.pmgr[J713_ISP_MAIN_188 / 4] = 0x04000200;
	CHECK(run(&m, true) == -EINVAL);
	CHECK(m.nwrites == 0);
	CHECK(m.npolls == 0);
	CHECK(m.nreads_ver == 0);
	CHECK(m.st.attempted == 0);

	load_live(&m);
	m.pmgr[J713_ISP_MAIN_530 / 4] = 0x00002300;
	CHECK(run(&m, true) == -EINVAL);
	CHECK(m.nwrites == 0);
	CHECK(m.npolls == 0);
	CHECK(m.nreads_ver == 0);

	load_live(&m);
	m.pmgr[j713_isp_local_off(0) / 4] = 0x311;
	CHECK(run(&m, true) == -EINVAL);
	CHECK(m.nwrites == 0);
	CHECK(m.npolls == 0);
	CHECK(m.nreads_ver == 0);

	load_live(&m);
	m.pmgr[j713_isp_local_off(J713_ISP_RESTORE_INDEX) / 4] = 0x300;
	CHECK(run(&m, true) == -EINVAL);
	CHECK(m.nwrites == 0);
	CHECK(m.npolls == 0);
	CHECK(m.nreads_ver == 0);

	load_live(&m);
	m.pmgr[j713_isp_local_off(2) / 4] = 0x300 | J713_ISP_BUSY;
	CHECK(run(&m, true) == -EINVAL);
	CHECK(m.nwrites == 0);
	CHECK(m.npolls == 0);
	CHECK(m.nreads_ver == 0);
}

static void test_no_isp_before_ack(void)
{
	struct mock m;

	load_live(&m);
	m.fail_enable = (int)(J713_ISP_LOCAL_COUNT - 1);
	CHECK(run(&m, true) == -ETIMEDOUT);
	CHECK(m.nreads_ver == 0);
	CHECK(m.nreads_inspect == 0);
	CHECK(m.st.isp_reads == 0);
	CHECK(m.st.inspect_reads == 0);
	CHECK(m.st.attempted == J713_ISP_LOCAL_COUNT);
}

static const u32 disallowed_ctrl[] = {
	1u << 10,	/* disable */
	1u << 12,	/* reset */
	1u << 16,	/* min-state */
	1u << 28,	/* auto-enable */
	1u << 31	/* reset */
};

static void expect_no_side_effects(struct mock *m, int rc)
{
	CHECK(rc == -EINVAL);
	CHECK(m->nwrites == 0);
	CHECK(m->npolls == 0);
	CHECK(m->nreads_ver == 0);
	CHECK(m->st.isp_reads == 0);
	CHECK(m->st.attempted == 0);
}

static void test_local_control_bits(void)
{
	unsigned b, idx;

	for (b = 0; b < ARRAY_SIZE(disallowed_ctrl); b++) {
		for (idx = 0; idx < J713_ISP_LOCAL_COUNT; idx++) {
			struct mock m;

			load_live(&m);
			m.pmgr[j713_isp_local_off(idx) / 4] |= disallowed_ctrl[b];
			expect_no_side_effects(&m, run(&m, true));
		}
	}
}

static void test_main_control_bits(void)
{
	static const u32 mains[] = {
		J713_ISP_MAIN_188, J713_ISP_MAIN_190, J713_ISP_MAIN_530
	};
	unsigned i, b;
	struct mock m;

	for (i = 0; i < ARRAY_SIZE(mains); i++) {
		for (b = 0; b < ARRAY_SIZE(disallowed_ctrl); b++) {
			load_live(&m);
			m.pmgr[mains[i] / 4] |= disallowed_ctrl[b];
			expect_no_side_effects(&m, run(&m, true));
		}
	}

	load_live(&m);
	m.pmgr[J713_ISP_MAIN_188 / 4] |= J713_ISP_MAIN_530_EXTRA;
	expect_no_side_effects(&m, run(&m, true));

	load_live(&m);
	m.pmgr[J713_ISP_MAIN_190 / 4] |= J713_ISP_MAIN_530_EXTRA;
	expect_no_side_effects(&m, run(&m, true));

	load_live(&m);
	m.pmgr[J713_ISP_MAIN_530 / 4] = 0x000003ff;
	CHECK(run(&m, false) == 0);
	CHECK(m.nwrites == 0);
	CHECK(m.nreads_ver == 0);
}

static void expect_identity_fail(struct mock *m, u32 raw)
{
	CHECK(run(m, true) == J713_ISP_ERR_GENERATION);
	CHECK(m->st.result == J713_ISP_ERR_GENERATION);
	CHECK(m->st.fail_stage == J713_ISP_STAGE_VERSION);
	CHECK(m->st.version == raw);
	CHECK(m->st.isp_reads == 1);
	CHECK(m->st.inspect_reads == 0);
	CHECK(m->nreads_ver == 1);
	CHECK(m->nreads_inspect == 0);
	CHECK(m->inspect_calls == 0);
	CHECK(m->nwrites == 2 * J713_ISP_LOCAL_COUNT);
	CHECK(m->st.attempted == J713_ISP_LOCAL_COUNT);
}

static void test_identity_generation(void)
{
	struct mock m;

	load_live(&m);
	m.isp_ver = 0;
	expect_identity_fail(&m, 0);
	CHECK(m.st.cleanup == 0);
	CHECK(m.st.verify == 0);

	load_live(&m);
	m.isp_ver = 0xffffffffu;
	expect_identity_fail(&m, 0xffffffffu);
	CHECK(m.st.cleanup == 0);

	load_live(&m);
	m.isp_ver = 0x00110000u;
	expect_identity_fail(&m, 0x00110000u);
	CHECK(m.st.cleanup == 0);

	load_live(&m);
	m.isp_ver = 0x000f0000u;
	expect_identity_fail(&m, 0x000f0000u);
	CHECK(m.st.cleanup == 0);

	load_live(&m);
	m.isp_ver = (J713_ISP_VERSION_GEN << J713_ISP_VERSION_GEN_SHIFT) |
		    0xabcd;
	CHECK(run(&m, true) == 0);
	CHECK(m.st.version ==
	      ((J713_ISP_VERSION_GEN << J713_ISP_VERSION_GEN_SHIFT) | 0xabcd));
	CHECK(m.st.cleanup == 0);

	load_live(&m);
	m.isp_ver = 0;
	m.fail_cleanup_off = (int)j713_isp_local_off(J713_ISP_LOCAL_COUNT - 1);
	expect_identity_fail(&m, 0);
	CHECK(m.st.cleanup == -ETIMEDOUT);
	CHECK(m.st.verify == -EINVAL);
	CHECK(m.st.fail_stage == J713_ISP_STAGE_VERSION);
	CHECK(m.nwrites == 2 * J713_ISP_LOCAL_COUNT);
	CHECK(m.cleanup_polls == J713_ISP_LOCAL_COUNT);
}

static void test_verify_unexpected_state(void)
{
	struct mock m;
	unsigned i;

	load_live(&m);
	m.verify_or_off = (int)J713_ISP_MAIN_188;
	m.verify_or = 1u << 10;
	CHECK(run(&m, true) == -EINVAL);
	CHECK(m.st.result == -EINVAL);
	CHECK(m.st.cleanup == 0);
	CHECK(m.st.verify == -EINVAL);
	CHECK(m.st.fail_stage == J713_ISP_STAGE_VERIFY);
	CHECK(m.st.isp_reads == 1);
	CHECK(m.nreads_ver == 1);
	CHECK(m.nwrites == 2 * J713_ISP_LOCAL_COUNT);

	load_live(&m);
	m.verify_or_off = (int)j713_isp_local_off(0);
	m.verify_or = 1u << 10;
	CHECK(run(&m, true) == -EINVAL);
	CHECK(m.st.verify == -EINVAL);
	CHECK(m.st.fail_stage == J713_ISP_STAGE_VERIFY);
	CHECK(m.nreads_ver == 1);
	CHECK(m.st.isp_reads == 1);

	load_live(&m);
	m.verify_or_off = (int)J713_ISP_MAIN_530;
	m.verify_or = 1u << 28;
	CHECK(run(&m, true) == -EINVAL);
	CHECK(m.st.verify == -EINVAL);
	CHECK(m.nreads_ver == 1);

	load_live(&m);
	CHECK(run(&m, true) == 0);
	CHECK(m.st.verify == 0);
	CHECK(m.st.result == 0);
	for (i = 0; i < J713_ISP_LOCAL_COUNT; i++)
		CHECK(m.st.saved_req[i] ==
		      ((i == J713_ISP_RESTORE_INDEX) ? J713_ISP_REQ_PARTIAL :
						       J713_ISP_REQ_OFF));
}

static void test_primary_retained_on_verify_fail(void)
{
	struct mock m;

	load_live(&m);
	m.fail_enable = 0;
	m.verify_or_off = (int)J713_ISP_MAIN_190;
	m.verify_or = 1u << 12;
	CHECK(run(&m, true) == -ETIMEDOUT);
	CHECK(m.st.result == -ETIMEDOUT);
	CHECK(m.st.cleanup == 0);
	CHECK(m.st.verify == -EINVAL);
	CHECK(m.st.fail_stage == J713_ISP_STAGE_ENABLE);
	CHECK(m.st.isp_reads == 0);
	CHECK(m.nreads_ver == 0);
	CHECK(m.nwrites == 2);
}

static u64 space_base(int space)
{
	switch (space) {
	case J713_ISP_SPACE_COPROC:
		return J713_ISP_COPROC_PHYS;
	case J713_ISP_SPACE_GPIO:
		return J713_ISP_GPIO_PHYS;
	case J713_ISP_SPACE_DART0:
		return J713_ISP_DART0_PHYS;
	case J713_ISP_SPACE_DART1:
		return J713_ISP_DART1_PHYS;
	case J713_ISP_SPACE_DART2:
		return J713_ISP_DART2_PHYS;
	default:
		return ~(u64)0;
	}
}

static unsigned int count_allowed(int space, u32 size)
{
	unsigned int n = 0;
	u32 off;

	for (off = 0; off < size; off += 4) {
		if (j713_isp_inspect_off_ok(space, off))
			n++;
	}
	return n;
}

static void check_restores(const struct mock *m)
{
	unsigned int i;
	unsigned int w;

	CHECK(m->nwrites == 2 * J713_ISP_LOCAL_COUNT);
	w = J713_ISP_LOCAL_COUNT;
	for (i = J713_ISP_LOCAL_COUNT; i > 0; i--) {
		unsigned int idx = i - 1;
		u32 want = (idx == J713_ISP_RESTORE_INDEX) ?
			   J713_ISP_REQ_PARTIAL : J713_ISP_REQ_OFF;

		CHECK(m->writes[w].off == j713_isp_local_off(idx));
		CHECK(m->writes[w].val == want);
		w++;
	}
}

static void test_inspect_plan_bounded(void)
{
	static const u32 dart_off[] = {
		J713_ISP_DART_PARAMS1, J713_ISP_DART_PARAMS2, J713_ISP_DART_PARAMS3,
		J713_ISP_DART_PARAMS4, J713_ISP_DART_PROTECT,
		J713_ISP_DART_SID0_TCR, J713_ISP_DART_SID0_TTBR,
		J713_ISP_DART_SID12_TCR, J713_ISP_DART_SID12_TTBR
	};
	static const u32 forbidden[] = {
		0x20u, 0x80u, 0x100u, 0x104u, 0x170u, 0x174u, 0x1c0u,
		0x204u, 0x208u, 0xc00u, 0xc20u
	};
	const struct j713_isp_inspect_word *plan;
	struct j713_isp_range claims[J713_ISP_WIN_COUNT];
	struct j713_isp_range pair[2];
	unsigned int n = 0;
	unsigned int i, j;
	unsigned int coproc = 0;
	unsigned int gpio = 0;
	unsigned int dart[3] = { 0, 0, 0 };
	unsigned int allowed;

	plan = j713_isp_inspect_plan(&n);
	CHECK(plan != NULL);
	CHECK(n == 37u);
	CHECK(n == J713_ISP_INSPECT_COUNT);
	CHECK(J713_ISP_DART_SID12_TCR == 0x1030u);
	CHECK(J713_ISP_DART_SID12_TTBR == 0x1430u);
	CHECK(J713_ISP_DART_TTBR_COUNT == 1);
	for (i = 0; i < n; i++) {
		CHECK(j713_isp_inspect_off_ok(plan[i].space, plan[i].off));
		CHECK(plan[i].phys == space_base(plan[i].space) + plan[i].off);
		CHECK(plan[i].bank != NULL);
		for (j = i + 1; j < n; j++) {
			CHECK(plan[i].phys != plan[j].phys);
			CHECK(!(plan[i].space == plan[j].space &&
				plan[i].off == plan[j].off));
		}
		if (plan[i].space == J713_ISP_SPACE_COPROC)
			coproc++;
		else if (plan[i].space == J713_ISP_SPACE_GPIO)
			gpio++;
		else if (plan[i].space >= J713_ISP_SPACE_DART0 &&
			 plan[i].space <= J713_ISP_SPACE_DART2)
			dart[plan[i].space - J713_ISP_SPACE_DART0]++;
		else
			fail_at(__func__, __LINE__, "unexpected inspect space");
	}
	CHECK(coproc == 2u);
	CHECK(gpio == 8u);
	CHECK(dart[0] == 9u && dart[1] == 9u && dart[2] == 9u);
	CHECK(strcmp(plan[0].bank, "coproc-ctrl") == 0);
	CHECK(plan[0].phys == 0x48d600044ULL);
	CHECK(plan[0].off == 0u);
	CHECK(strcmp(plan[1].bank, "coproc-status") == 0);
	CHECK(plan[1].phys == 0x48d600048ULL);
	CHECK(strcmp(plan[2].bank, "gpio0") == 0);
	CHECK(plan[2].phys == 0x48e9241d0ULL);
	CHECK(strcmp(plan[9].bank, "gpio7") == 0);
	CHECK(plan[9].phys == 0x48e9241ecULL);
	CHECK(plan[9].off == 0x1cu);
	for (i = 0; i < 3; i++) {
		unsigned int base = 10u + i * 9u;

		for (j = 0; j < 9; j++)
			CHECK(plan[base + j].off == dart_off[j]);
		CHECK(plan[base + 4].off == J713_ISP_DART_PROTECT);
		CHECK(plan[base + 4].off != 0x208u);
	}
	CHECK(plan[10].phys == 0x48e8c0000ULL);
	CHECK(plan[18].phys == 0x48e8c1430ULL);
	CHECK(plan[19].phys == 0x48e8e0000ULL);
	CHECK(plan[28].phys == 0x48e900000ULL);
	CHECK(plan[36].phys == 0x48e901430ULL);
	for (i = 0; i < ARRAY_SIZE(forbidden); i++) {
		CHECK(!j713_isp_inspect_off_ok(J713_ISP_SPACE_GPIO, forbidden[i]));
		CHECK(!j713_isp_inspect_off_ok(J713_ISP_SPACE_DART0, forbidden[i]));
		CHECK(!j713_isp_inspect_off_ok(J713_ISP_SPACE_DART1, forbidden[i]));
		CHECK(!j713_isp_inspect_off_ok(J713_ISP_SPACE_DART2, forbidden[i]));
		CHECK(!j713_isp_inspect_off_ok(J713_ISP_SPACE_COPROC, forbidden[i]));
		CHECK(!j713_isp_inspect_off_ok(J713_ISP_SPACE_VER, forbidden[i]));
	}
	CHECK(!j713_isp_inspect_off_ok(J713_ISP_SPACE_GPIO, J713_ISP_GPIO_SIZE));
	CHECK(!j713_isp_inspect_off_ok(J713_ISP_SPACE_COPROC, 8u));
	allowed = count_allowed(J713_ISP_SPACE_COPROC, 16u);
	allowed += count_allowed(J713_ISP_SPACE_GPIO, 64u);
	allowed += count_allowed(J713_ISP_SPACE_DART0, J713_ISP_DART_SIZE);
	allowed += count_allowed(J713_ISP_SPACE_DART1, J713_ISP_DART_SIZE);
	allowed += count_allowed(J713_ISP_SPACE_DART2, J713_ISP_DART_SIZE);
	CHECK(allowed == 37u);
	CHECK(j713_isp_fill_claims(false, claims) == 2u);
	CHECK(claims[0].start == J713_ISP_LOCAL_PHYS);
	CHECK(claims[1].start == J713_ISP_VER_PHYS);
	CHECK(j713_isp_ranges_overlap(claims, 2) == 0);
	CHECK(j713_isp_fill_claims(true, claims) == J713_ISP_WIN_COUNT);
	CHECK(j713_isp_ranges_overlap(claims, J713_ISP_WIN_COUNT) == 0);
	for (i = 0; i < J713_ISP_WIN_COUNT; i++) {
		CHECK(claims[i].start != J713_ISP_CPU_BASE);
		CHECK(claims[i].size != J713_ISP_WRAPPER_SIZE);
	}
	pair[0].start = J713_ISP_CPU_BASE;
	pair[0].size = J713_ISP_WRAPPER_SIZE;
	pair[1].start = J713_ISP_COPROC_PHYS;
	pair[1].size = J713_ISP_COPROC_SIZE;
	CHECK(j713_isp_ranges_overlap(pair, 2) != 0);
	pair[1].start = J713_ISP_GPIO_PHYS;
	pair[1].size = J713_ISP_GPIO_SIZE;
	CHECK(j713_isp_ranges_overlap(pair, 2) != 0);
	pair[1].start = J713_ISP_DART0_PHYS;
	pair[1].size = J713_ISP_DART_SIZE;
	CHECK(j713_isp_ranges_overlap(pair, 2) != 0);
	pair[1].start = J713_ISP_VER_PHYS;
	pair[1].size = J713_ISP_VER_SIZE;
	CHECK(j713_isp_ranges_overlap(pair, 2) != 0);
}

static void test_no_inspect_default(void)
{
	struct mock m;

	load_live(&m);
	m.isp_ver = J713_ISP_VERSION_EXACT;
	CHECK(run_with(&m, false, mock_inspect) == 0);
	CHECK(m.inspect_calls == 0);
	CHECK(m.nreads_inspect == 0);
	CHECK(m.nreads_ver == 0);
	CHECK(m.st.inspect_reads == 0);
	CHECK(m.st.isp_reads == 0);
	CHECK(m.nwrites == 0);
	CHECK(m.npolls == 0);

	load_live(&m);
	m.isp_ver = J713_ISP_VERSION_EXACT;
	CHECK(run(&m, true) == 0);
	CHECK(m.inspect_calls == 0);
	CHECK(m.nreads_inspect == 0);
	CHECK(m.nlogs == 0);
	CHECK(m.st.inspect_reads == 0);
	CHECK(m.st.isp_reads == 1);
	CHECK(m.nreads_ver == 1);
	CHECK(m.st.version == J713_ISP_VERSION_EXACT);
	CHECK(m.st.result == 0);
	check_restores(&m);
}

static void test_no_inspect_before_ack_or_wrong_version(void)
{
	static const u32 wrong[] = {
		0u, 0xffffffffu, 0x00110000u, 0x000f0000u,
		0x00100000u, 0x00100002u, 0x0010abcdu
	};
	unsigned int i;
	struct mock m;

	load_live(&m);
	m.isp_ver = J713_ISP_VERSION_EXACT;
	m.fail_enable = 0;
	CHECK(run_inspect(&m) == -ETIMEDOUT);
	CHECK(m.inspect_calls == 0);
	CHECK(m.nreads_ver == 0);
	CHECK(m.nreads_inspect == 0);
	CHECK(m.st.isp_reads == 0);
	CHECK(m.st.inspect_reads == 0);
	CHECK(m.st.fail_stage == J713_ISP_STAGE_ENABLE);

	load_live(&m);
	m.isp_ver = J713_ISP_VERSION_EXACT;
	m.fail_enable = (int)(J713_ISP_LOCAL_COUNT - 1);
	CHECK(run_inspect(&m) == -ETIMEDOUT);
	CHECK(m.inspect_calls == 0);
	CHECK(m.nreads_ver == 0);
	CHECK(m.st.isp_reads == 0);
	CHECK(m.nreads_inspect == 0);
	CHECK(m.st.attempted == J713_ISP_LOCAL_COUNT);

	load_live(&m);
	m.isp_ver = J713_ISP_VERSION_EXACT;
	m.fail_ver = 1;
	CHECK(run_inspect(&m) == -EIO);
	CHECK(m.inspect_calls == 0);
	CHECK(m.nreads_ver == 0);
	CHECK(m.nreads_inspect == 0);
	CHECK(m.st.isp_reads == 0);
	CHECK(m.st.fail_stage == J713_ISP_STAGE_VERSION);
	check_restores(&m);

	for (i = 0; i < ARRAY_SIZE(wrong); i++) {
		int want = ((wrong[i] >> J713_ISP_VERSION_GEN_SHIFT) ==
			    J713_ISP_VERSION_GEN) ? J713_ISP_ERR_EXACT :
						   J713_ISP_ERR_GENERATION;

		load_live(&m);
		m.isp_ver = wrong[i];
		CHECK(run_inspect(&m) == want);
		CHECK(m.inspect_calls == 0);
		CHECK(m.nreads_inspect == 0);
		CHECK(m.nlogs == 0);
		CHECK(m.st.inspect_reads == 0);
		CHECK(m.st.isp_reads == 1);
		CHECK(m.nreads_ver == 1);
		CHECK(m.st.version == wrong[i]);
		CHECK(m.st.fail_stage == J713_ISP_STAGE_VERSION);
		CHECK(m.st.cleanup == 0);
		check_restores(&m);
	}
}

static void test_inspect_once_after_exact_version(void)
{
	struct mock m;
	const struct j713_isp_inspect_word *plan;
	unsigned int n = 0;
	unsigned int i;

	load_live(&m);
	m.isp_ver = J713_ISP_VERSION_EXACT;
	CHECK(run_inspect(&m) == 0);
	CHECK(m.st.result == 0);
	CHECK(m.st.cleanup == 0);
	CHECK(m.st.verify == 0);
	CHECK(m.st.fail_stage == 0);
	CHECK(m.inspect_calls == 1);
	CHECK(m.inspect_at_writes == (int)J713_ISP_LOCAL_COUNT);
	CHECK(m.inspect_at_enable_polls == (int)J713_ISP_LOCAL_COUNT);
	CHECK(m.inspect_at_cleanup_polls == 0);
	CHECK(m.inspect_at_ver_reads == 1);
	CHECK(m.inspect_seen_version == J713_ISP_VERSION_EXACT);
	CHECK(m.st.version == J713_ISP_VERSION_EXACT);
	CHECK(m.st.isp_reads == 1);
	CHECK(m.nreads_ver == 1);
	CHECK(m.st.inspect_reads == 37u);
	CHECK(m.nreads_inspect == 37u);
	CHECK(m.nlogs == 37u);
	CHECK(m.st.attempted == J713_ISP_LOCAL_COUNT);
	plan = j713_isp_inspect_plan(&n);
	CHECK(n == 37u);
	for (i = 0; i < n; i++) {
		char expect[160];

		CHECK(m.st.inspect_val[i] == m.inspect_mem[i]);
		snprintf(expect, sizeof(expect),
			 "inspect bank=%s phys=0x%llx off=0x%x val=0x%x",
			 plan[i].bank, (unsigned long long)plan[i].phys,
			 plan[i].off, m.inspect_mem[i]);
		CHECK(strcmp(m.logs[i], expect) == 0);
	}
	check_restores(&m);
	CHECK(m.pmgr[j713_isp_local_off(J713_ISP_RESTORE_INDEX) / 4] ==
	      j713_isp_ack_low(J713_ISP_REQ_PARTIAL));
	CHECK(j713_isp_inspect_read(&mock_ops, &m, &m.st) == -EALREADY);
	CHECK(m.nreads_inspect == 37u);
	CHECK(m.st.inspect_reads == 37u);
}

static void test_inspect_error_restores(void)
{
	struct mock m;

	load_live(&m);
	m.isp_ver = J713_ISP_VERSION_EXACT;
	m.inspect_cb_error = -EIO;
	CHECK(run_inspect(&m) == -EIO);
	CHECK(m.st.result == -EIO);
	CHECK(m.st.fail_stage == J713_ISP_STAGE_INSPECT);
	CHECK(m.st.stage == J713_ISP_STAGE_INSPECT);
	CHECK(m.inspect_calls == 1);
	CHECK(m.inspect_at_writes == (int)J713_ISP_LOCAL_COUNT);
	CHECK(m.inspect_at_cleanup_polls == 0);
	CHECK(m.inspect_at_ver_reads == 1);
	CHECK(m.nreads_inspect == 0);
	CHECK(m.nlogs == 0);
	CHECK(m.st.inspect_reads == 0);
	CHECK(m.st.isp_reads == 1);
	CHECK(m.nreads_ver == 1);
	CHECK(m.st.cleanup == 0);
	CHECK(m.st.verify == 0);
	CHECK(m.st.attempted == J713_ISP_LOCAL_COUNT);
	check_restores(&m);

	load_live(&m);
	m.isp_ver = J713_ISP_VERSION_EXACT;
	m.fail_inspect_read = 4;
	CHECK(run_inspect(&m) == -EIO);
	CHECK(m.st.result == -EIO);
	CHECK(m.st.fail_stage == J713_ISP_STAGE_INSPECT);
	CHECK(m.inspect_calls == 1);
	CHECK(m.st.inspect_reads == 4u);
	CHECK(m.nreads_inspect == 4u);
	CHECK(m.nlogs == 4u);
	CHECK(m.st.isp_reads == 1);
	CHECK(m.st.cleanup == 0);
	check_restores(&m);

	load_live(&m);
	m.isp_ver = J713_ISP_VERSION_EXACT;
	m.inspect_cb_error = -EIO;
	m.fail_cleanup_off = (int)j713_isp_local_off(J713_ISP_LOCAL_COUNT - 1);
	m.verify_or_off = (int)J713_ISP_MAIN_190;
	m.verify_or = 1u << 12;
	CHECK(run_inspect(&m) == -EIO);
	CHECK(m.st.result == -EIO);
	CHECK(m.st.fail_stage == J713_ISP_STAGE_INSPECT);
	CHECK(m.st.cleanup == -ETIMEDOUT);
	CHECK(m.st.verify == -EINVAL);
	CHECK(m.inspect_calls == 1);
	CHECK(m.st.inspect_reads == 0);
	CHECK(m.nwrites == 2 * J713_ISP_LOCAL_COUNT);
	CHECK(m.cleanup_polls == J713_ISP_LOCAL_COUNT);
	CHECK(m.st.isp_reads == 1);
}

/* One userspace translation unit. alg.c is included above and life.c here.
 * Do not also compile or link those two files into this binary.
 */
#include "j713_isp_dartmap_life.c"
#include "j713_isp_dartmap_life_test.c"

int main(void)
{
	test_validate_only();
	test_good_path();
	test_fail_each_enable();
	test_cleanup_continues();
	test_invalid_writes_nothing();
	test_no_isp_before_ack();
	test_local_control_bits();
	test_main_control_bits();
	test_identity_generation();
	test_verify_unexpected_state();
	test_primary_retained_on_verify_fail();
	test_inspect_plan_bounded();
	test_no_inspect_default();
	test_no_inspect_before_ack_or_wrong_version();
	test_inspect_once_after_exact_version();
	test_inspect_error_restores();
	test_run_dartmap();
	if (failures) {
		fprintf(stderr, "%d checks failed\n", failures);
		return 1;
	}
	puts("j713_isp_probe_mock: all checks passed");
	return 0;
}
