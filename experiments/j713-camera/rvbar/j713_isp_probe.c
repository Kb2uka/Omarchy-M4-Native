// SPDX-License-Identifier: GPL-2.0-only
/*
 * One-shot J713/T8132 ISP identity probe, plus an optional read-only
 * register inspection. Default apply=false: DT/kernel guards plus PMGR
 * snapshot only. apply=true reuses the already proven PMGR writer: 13
 * local regs (0x4000..0x4060) with plain 0xf, poll actual/request 0xff
 * and busy clear, one version read at 0x48d800000, reverse restore of
 * incoming request 0 or 4 (0x4020 currently 4), then re-read all 16 PMGR
 * words. Never mutates main PMGR 0x188/0x190/0x530.
 *
 * inspect=true requires apply=true. After all 13 ACKs and version exactly
 * 0x100001, it reads 37 known words and logs each bank, physical address,
 * offset, and raw value, then reads RVBAR once as a raw 64-bit word.
 * isp_reads counts only the version read. inspect_reads counts the 37
 * inspection words and does not count RVBAR. rvbar_reads counts only a
 * successful RVBAR read. No COPROC, GPIO, DART, or RVBAR write, no
 * firmware launch, no DMA setup, no TLB flush, no boot change, and no
 * machine power change. No automatic retry. A raw RVBAR of zero is an
 * observation, not a fault and not permission to start firmware.
 *
 * Native DisableISPClocksAndPower stores 0x2000000 at COPROC 0x1600040
 * before power-off; native StartFirmware stores 0x10 at 0x1600044. This
 * module maps neither that disable word nor any command path. Public ASC
 * RUN / RUNNING / STOPPED bits are not a success mask and are not written.
 * Whether a power-on resumes prior coproc state is unknown.
 *
 * Exclusive claims, only after guards and before any power write:
 * local PMGR 0x380704000/0x64 and version 0x48d800000/4 when apply=true;
 * COPROC 0x48d600044/8, GPIO 0x48e9241d0/32, DART 0x48e8c0000,
 * 0x48e8e0000, 0x48e900000 each 0xc000, and RVBAR 0x48d050000/8 when
 * inspect=true. The ISP wrapper is not claimed. The full PMGR window is
 * ioremap'd for the snapshot and is not an exclusive claim. Any claim or
 * ioremap failure unwinds every claim and map already taken and returns
 * before the first PMGR write. request_mem_region and ioremap do not read
 * the target. Unpowered ISP, COPROC, GPIO, DART, and RVBAR addresses are
 * not read during mapping or guards. RVBAR is readq of that 8-byte window
 * and only from the inspect callback.
 *
 * A busy claim is a real conflict. Generic IOMMU drivers for other
 * devices are not treated as conflicts. This kernel has no ISP driver.
 * expected_boot is not a live guard. 7.1.9 driver_find drops its kset
 * reference before return.
 */
#define pr_fmt(fmt) KBUILD_MODNAME ": " fmt

#include <linux/capability.h>
#include <linux/device.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/ioport.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/of_address.h>
#include <linux/platform_device.h>
#include <linux/string.h>
#include <linux/utsname.h>

#include "j713_isp_probe_alg.h"
#include "j713_isp_probe_alg.c"

#define J713_ISP_RELEASE	"7.1.9-m4-gpu-backport-20261008"
#define J713_ISP_PMGR_PATH	"/soc/power-management@380700000"
#define J713_ISP_BOOT_UUID	"REQUALIFY_BOOT_UUID"

static bool apply;
static bool inspect;
static int result = -EAGAIN;
static int cleanup;
static int verify;
static int stage;
static int fail_stage;
static unsigned int attempted;
static unsigned int isp_reads;
static unsigned int inspect_reads;
static unsigned int inspect_words[J713_ISP_INSPECT_COUNT];
static unsigned int inspect_word_num = J713_ISP_INSPECT_COUNT;
static unsigned int rvbar_reads;
static unsigned long long raw_rvbar;
static unsigned int version;
static char *expected_boot = J713_ISP_BOOT_UUID;

module_param(apply, bool, 0400);
MODULE_PARM_DESC(apply, "Write local PMGR and read ISP version; default false");
module_param(inspect, bool, 0400);
MODULE_PARM_DESC(inspect, "After exact version 0x100001, read 37 COPROC/GPIO/DART words then one RVBAR u64; requires apply=true");
module_param(result, int, 0444);
module_param(cleanup, int, 0444);
module_param(verify, int, 0444);
module_param(stage, int, 0444);
MODULE_PARM_DESC(stage, "First failure stage, or last stage on success");
module_param(fail_stage, int, 0444);
MODULE_PARM_DESC(fail_stage, "First failure stage; 0 if none");
module_param(attempted, uint, 0444);
module_param(isp_reads, uint, 0444);
MODULE_PARM_DESC(isp_reads, "Version-register reads only; does not count inspection words or RVBAR");
module_param(inspect_reads, uint, 0444);
MODULE_PARM_DESC(inspect_reads, "Successful 32-bit inspection words, in plan order; does not count RVBAR");
module_param_array(inspect_words, uint, &inspect_word_num, 0444);
MODULE_PARM_DESC(inspect_words, "Raw inspection words in plan order; valid prefix length is inspect_reads");
module_param(rvbar_reads, uint, 0444);
MODULE_PARM_DESC(rvbar_reads, "Successful 64-bit RVBAR reads (0 or 1); not included in isp_reads or inspect_reads");
module_param(raw_rvbar, ullong, 0444);
MODULE_PARM_DESC(raw_rvbar, "Raw 64-bit RVBAR observation after the 37-word inspect; zero is an observation, not a fault and not a firmware start");
module_param(version, uint, 0444);
module_param(expected_boot, charp, 0444);
MODULE_PARM_DESC(expected_boot,
		 "Documented boot UUID for the caller to check before insmod; this module does not read boot_id and does not treat this parameter as a live guard");

struct j713_isp_hw {
	void __iomem *pmgr;
	void __iomem *ver;
	void __iomem *coproc;
	void __iomem *gpio;
	void __iomem *dart[J713_ISP_DART_INSTANCES];
	void __iomem *rvbar;
};

struct j713_isp_map {
	const char *name;
	resource_size_t phys;
	resource_size_t size;
	struct resource *res;
	void __iomem *map;
};

static bool resource_ok(struct device_node *np, resource_size_t base,
			resource_size_t size)
{
	struct resource r;

	return !of_address_to_resource(np, 0, &r) && r.start == base &&
	       resource_size(&r) == size;
}

static bool node_exists(const char *path)
{
	struct device_node *np = of_find_node_by_path(path);

	if (np)
		of_node_put(np);
	return np != NULL;
}

static int hw_read32(void *ctx, int space, u32 off, u32 *val)
{
	struct j713_isp_hw *h = ctx;

	if (!val)
		return -EINVAL;
	if (space == J713_ISP_SPACE_PMGR) {
		if (!h->pmgr || off + 4 > J713_ISP_PMGR_SIZE || (off & 3))
			return -EINVAL;
		*val = readl(h->pmgr + off);
		return 0;
	}
	if (space == J713_ISP_SPACE_VER) {
		if (!h->ver || off)
			return -EINVAL;
		*val = readl(h->ver);
		return 0;
	}
	if (space == J713_ISP_SPACE_COPROC || space == J713_ISP_SPACE_GPIO ||
	    space == J713_ISP_SPACE_DART0 || space == J713_ISP_SPACE_DART1 ||
	    space == J713_ISP_SPACE_DART2) {
		void __iomem *base = NULL;

		if (!j713_isp_inspect_off_ok(space, off))
			return -EINVAL;
		if (space == J713_ISP_SPACE_COPROC)
			base = h->coproc;
		else if (space == J713_ISP_SPACE_GPIO)
			base = h->gpio;
		else
			base = h->dart[space - J713_ISP_SPACE_DART0];
		if (!base)
			return -EINVAL;
		*val = readl(base + off);
		return 0;
	}
	return -EINVAL;
}

static int hw_read64(void *ctx, int space, u32 off, u64 *val)
{
	struct j713_isp_hw *h = ctx;

	if (!val)
		return -EINVAL;
	/* Sole 64-bit MMIO access: readq of the claimed 8-byte window. */
	if (space != J713_ISP_SPACE_RVBAR || off != 0 || !h->rvbar)
		return -EINVAL;
	*val = readq(h->rvbar);
	return 0;
}

static int hw_write32(void *ctx, int space, u32 off, u32 val)
{
	struct j713_isp_hw *h = ctx;

	if (space != J713_ISP_SPACE_PMGR)
		return -EINVAL;
	if (!h->pmgr || off < J713_ISP_LOCAL_BASE || off > 0x4060 || (off & 7))
		return -EINVAL;
	writel(val, h->pmgr + off);
	return 0;
}

static int hw_poll_ps(void *ctx, u32 off, u32 want_low)
{
	struct j713_isp_hw *h = ctx;
	u32 val;
	int ret;

	if (!h->pmgr || off < J713_ISP_LOCAL_BASE || off > 0x4060 || (off & 7))
		return -EINVAL;
	ret = readl_poll_timeout(h->pmgr + off, val,
				 ((val & J713_ISP_LOW_MASK) == want_low) &&
				 !(val & J713_ISP_BUSY),
				 J713_ISP_POLL_SLEEP_US,
				 J713_ISP_POLL_TIMEOUT_US);
	if (ret)
		pr_err("poll timeout off=0x%x want_low=0x%x last=0x%x err=%d\n",
		       off, want_low, val, ret);
	return ret;
}

static void hw_log(void *ctx, const char *msg)
{
	(void)ctx;
	pr_info("%s\n", msg);
}

static const struct j713_isp_ops hw_ops = {
	.read32 = hw_read32,
	.write32 = hw_write32,
	.poll_ps = hw_poll_ps,
	.log = hw_log,
	.read64 = hw_read64,
};

static int hw_inspect(void *ctx, struct j713_isp_state *st)
{
	/* 37 32-bit words, then one RVBAR u64. No write on either path. */
	return j713_isp_inspect_then_rvbar(&hw_ops, ctx, st);
}

static int reject_compat(const char *compat)
{
	struct device_node *np = of_find_compatible_node(NULL, NULL, compat);

	if (!np)
		return 0;
	of_node_put(np);
	return -EEXIST;
}

static int guard_no_isp(void)
{
	static const char *const isp_compat[] = {
		"apple,t8132-isp", "apple,t8122-isp", "apple,t8140-isp",
		"apple,t8103-isp", "apple,t8112-isp", "apple-isp"
	};
	struct device_node *np;
	struct device_driver *drv;
	unsigned int i;
	int ret;

	np = of_find_node_by_name(NULL, "isp");
	if (np) {
		of_node_put(np);
		return -EEXIST;
	}
	if (node_exists("/soc/isp@48c000000"))
		return -EEXIST;
	for (i = 0; i < ARRAY_SIZE(isp_compat); i++) {
		ret = reject_compat(isp_compat[i]);
		if (ret)
			return ret;
	}
	if (node_exists(J713_ISP_PMGR_PATH "/power-controller@188") ||
	    node_exists(J713_ISP_PMGR_PATH "/power-controller@190") ||
	    node_exists(J713_ISP_PMGR_PATH "/power-controller@530"))
		return -EEXIST;
	/* ISP DART only. Other devices' apple-dart nodes are not conflicts. */
	{
		static const char *const dart_names[] = {
			"dart-isp", "mapper-isp", "mapper-isp-mpm"
		};
		static const char *const dart_paths[] = {
			"/soc/dart-isp",
			"/soc/iommu@48e8c0000",
			"/soc/iommu@48e8e0000",
			"/soc/iommu@48e900000",
			"/arm-io/dart-isp"
		};

		for (i = 0; i < ARRAY_SIZE(dart_names); i++) {
			np = of_find_node_by_name(NULL, dart_names[i]);
			if (np) {
				of_node_put(np);
				return -EEXIST;
			}
		}
		for (i = 0; i < ARRAY_SIZE(dart_paths); i++) {
			if (node_exists(dart_paths[i]))
				return -EEXIST;
		}
	}
	/* 7.1.9 driver_find already dropped the kset reference. */
	drv = driver_find("apple-isp", &platform_bus_type);
	if (drv)
		return -EEXIST;
	return 0;
}

static int guard_pmgr_dt(void)
{
	struct device_node *np = of_find_node_by_path(J713_ISP_PMGR_PATH);
	bool ok;

	if (!np)
		return -ENODEV;
	ok = resource_ok(np, J713_ISP_PMGR_PHYS, J713_ISP_PMGR_SIZE) &&
	     of_device_is_compatible(np, "apple,t8132-pmgr") &&
	     of_device_is_compatible(np, "apple,t8103-pmgr") &&
	     of_device_is_compatible(np, "syscon") &&
	     of_device_is_compatible(np, "simple-mfd");
	of_node_put(np);
	return ok ? 0 : -EINVAL;
}

static int guard_board(void)
{
	if (!capable(CAP_SYS_RAWIO))
		return -EPERM;
	if (!of_machine_is_compatible("apple,j713") ||
	    !of_machine_is_compatible("apple,t8132"))
		return -EPERM;
	if (strcmp(utsname()->release, J713_ISP_RELEASE))
		return -EPERM;
	return 0;
}

static void publish(const struct j713_isp_state *st)
{
	unsigned int i;

	result = st->result;
	cleanup = st->cleanup;
	verify = st->verify;
	stage = st->fail_stage ? st->fail_stage : st->stage;
	fail_stage = st->fail_stage;
	attempted = st->attempted;
	isp_reads = st->isp_reads;
	inspect_reads = st->inspect_reads;
	inspect_word_num = J713_ISP_INSPECT_COUNT;
	for (i = 0; i < J713_ISP_INSPECT_COUNT; i++)
		inspect_words[i] = st->inspect_val[i];
	rvbar_reads = st->rvbar_reads;
	raw_rvbar = st->raw_rvbar;
	version = st->version;
	pr_info("result=%d cleanup=%d verify=%d fail_stage=%d stage=%d attempted=%u isp_reads=%u inspect_reads=%u rvbar_reads=%u version=0x%x raw_rvbar=0x%llx apply=%d inspect=%d\n",
		result, cleanup, verify, fail_stage, stage, attempted,
		isp_reads, inspect_reads, rvbar_reads, version, raw_rvbar,
		apply, inspect);
}

static const char *const claim_names[J713_ISP_WIN_COUNT] = {
	"j713_isp_probe-local",
	"j713_isp_probe-ver",
	"j713_isp_probe-coproc",
	"j713_isp_probe-gpio",
	"j713_isp_probe-dart0",
	"j713_isp_probe-dart1",
	"j713_isp_probe-dart2",
	"j713_isp_probe-rvbar",
};

_Static_assert(ARRAY_SIZE(claim_names) == J713_ISP_WIN_COUNT,
	       "one claim name per window");

static void release_one(struct j713_isp_map *m)
{
	if (!m)
		return;
	if (m->map) {
		iounmap(m->map);
		m->map = NULL;
	}
	if (m->res) {
		release_mem_region(m->phys, m->size);
		m->res = NULL;
	}
}

static int claim_one(struct j713_isp_map *m)
{
	m->res = request_mem_region(m->phys, m->size, m->name);
	if (!m->res) {
		pr_err("%s 0x%llx size 0x%llx busy; no writes\n",
		       m->name, (unsigned long long)m->phys,
		       (unsigned long long)m->size);
		return -EBUSY;
	}
	/* ioremap does not read the window. Unpowered blocks stay unread. */
	m->map = ioremap(m->phys, m->size);
	if (!m->map) {
		pr_err("%s 0x%llx ioremap failed; no writes\n",
		       m->name, (unsigned long long)m->phys);
		release_one(m);
		return -ENOMEM;
	}
	return 0;
}

static int claim_windows(struct j713_isp_map *maps, unsigned int *nmap,
			 bool do_inspect)
{
	struct j713_isp_range ranges[J713_ISP_WIN_COUNT];
	unsigned int n;
	unsigned int i;
	int ret;

	n = j713_isp_fill_claims(do_inspect, ranges);
	if ((!do_inspect && n != 2) ||
	    (do_inspect && n != J713_ISP_WIN_COUNT))
		return -EINVAL;
	ret = j713_isp_ranges_overlap(ranges, n);
	if (ret) {
		pr_err("exclusive claim windows overlap; no writes\n");
		return ret;
	}
	for (i = 0; i < n; i++) {
		maps[i].name = claim_names[i];
		maps[i].phys = ranges[i].start;
		maps[i].size = ranges[i].size;
		maps[i].res = NULL;
		maps[i].map = NULL;
		ret = claim_one(&maps[i]);
		if (ret) {
			while (i--)
				release_one(&maps[i]);
			*nmap = 0;
			return ret;
		}
	}
	*nmap = n;
	return 0;
}

static int __init j713_isp_probe_init(void)
{
	struct j713_isp_hw hw = {};
	struct j713_isp_state st = {};
	struct j713_isp_map maps[J713_ISP_WIN_COUNT] = {};
	unsigned int nmap = 0;
	int ret;

	stage = J713_ISP_STAGE_GUARDS;
	pr_info("init apply=%d inspect=%d expected_boot=%s (not a live guard)\n",
		apply, inspect, expected_boot ? expected_boot : "");
	if (inspect && !apply) {
		result = -EINVAL;
		pr_err("inspect requires apply=true; no maps and no writes\n");
		return -EINVAL;
	}
	ret = guard_board();
	if (ret) {
		result = ret;
		pr_err("board/kernel/cap guard failed %d\n", ret);
		return ret;
	}
	ret = guard_pmgr_dt();
	if (ret) {
		result = ret;
		pr_err("PMGR DT guard failed %d\n", ret);
		return ret;
	}
	ret = guard_no_isp();
	if (ret) {
		result = ret;
		pr_err("ISP or DART-ISP device/driver/power-provider present %d\n", ret);
		return ret;
	}

	hw.pmgr = ioremap(J713_ISP_PMGR_PHYS, J713_ISP_PMGR_SIZE);
	if (!hw.pmgr) {
		result = -ENOMEM;
		pr_err("PMGR ioremap failed\n");
		return -ENOMEM;
	}
	stage = J713_ISP_STAGE_MAPPED;

	if (apply) {
		ret = claim_windows(maps, &nmap, inspect);
		if (ret)
			goto unmap;
		hw.ver = maps[J713_ISP_WIN_VER].map;
		if (inspect) {
			hw.coproc = maps[J713_ISP_WIN_COPROC].map;
			hw.gpio = maps[J713_ISP_WIN_GPIO].map;
			hw.dart[0] = maps[J713_ISP_WIN_DART0].map;
			hw.dart[1] = maps[J713_ISP_WIN_DART1].map;
			hw.dart[2] = maps[J713_ISP_WIN_DART2].map;
			hw.rvbar = maps[J713_ISP_WIN_RVBAR].map;
		}
	}

	st.stage = stage;
	ret = j713_isp_probe_run(&hw_ops, &hw, apply,
				 (apply && inspect) ? hw_inspect : NULL, &st);
	publish(&st);
	if (!ret)
		ret = st.result;

unmap:
	while (nmap)
		release_one(&maps[--nmap]);
	iounmap(hw.pmgr);
	if (ret) {
		if (result == -EAGAIN)
			result = ret;
		pr_err("exit ret=%d result=%d cleanup=%d verify=%d fail_stage=%d attempted=%u isp_reads=%u inspect_reads=%u rvbar_reads=%u raw_rvbar=0x%llx\n",
		       ret, result, cleanup, verify, fail_stage, attempted,
		       isp_reads, inspect_reads, rvbar_reads, raw_rvbar);
	}
	return ret;
}

static void __exit j713_isp_probe_exit(void)
{
}

module_init(j713_isp_probe_init);
module_exit(j713_isp_probe_exit);

MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Private J713 ISP identity probe with optional read-only register inspection and one RVBAR read");
