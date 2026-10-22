// SPDX-License-Identifier: GPL-2.0-only
/*
 * Private J713 ISP firmware view.
 *
 * Link this file with j713_isp_fwview_life.c and j713_isp_probe_alg.c.
 * The embedded DTBO comes from j713_isp_fwview_dtbo.inc, generated off
 * host. This file does not contain hand-written DTB bytes.
 *
 * The processor is never released. This file writes no COPROC, mailbox,
 * GPIO, fabric, 0xc00, 0x1600040, or 0x28a4080 register. The only DART
 * writes are SID0 TCR, SID0 TTBR, and the TLB command, and only during
 * quiesce after detach. dart_survey writes none of them.
 *
 * map_check=false (default): board, kernel, AIC, /soc, and ISP-node guards,
 * then one shared PMGR parent map and a PMGR snapshot. No exclusive claim,
 * no COPROC/DART/version read, no overlay, no domain.
 *
 * dart_survey requires map_check. After the same 13 local ACKs, the exact
 * version check, and the stopped-CPU check, it reads PARAMS1, PARAMS3,
 * PARAMS4, PROTECT, ERROR, and TCR/TTBR for SID 0..15 on all three DARTs.
 * Then the outer power sequence restores the gates. No overlay, provider,
 * domain, allocation, map, or DART write.
 *
 * map_check=true, before any local-PMGR write:
 *   exclusive claims are PMGR local (0x64 bytes), version, COPROC, one
 *   GPIO1 word, four fabric counter words, and the three DART windows.
 *   Not the GPIO block, not the ISP wrapper, not the PMGR parent.
 *   The parent map stays for the main-rail snapshot. Main rails are never
 *   written. GPIO1 and the fabric counters are read-only.
 * After 13 local ACKs and version exactly 0x100001 the power callback marks
 * POWER and the claims, then the shared lifecycle runs:
 *   CPU stopped, DART baseline, release only the three DART claims,
 *   register the test master, apply the overlay, require the master and
 *   the three apple-dart providers to already be bound, remap DART
 *   read-only, allocate twelve zeroed pages (heap and IPC), allocate one
 *   paging domain, read the premise words, attach, map TEXT, DATA, heap,
 *   and IPC, software lookup of every 16KiB page, SID0 TCR/TTBR readback,
 *   then a read-only walk of the DART2 tables those TTBR values name.
 * Teardown unmaps, detaches, quiesces SID0 on all three DARTs (TCR 0,
 * TTBR 0, TLB flush, then TCR/TTBR/ERROR readback), frees the domain,
 * checks heap and IPC are still zero, then frees those pages and removes
 * the overlay. The outer power sequence then restores the 13 local gates.
 * A quiesce failure retains. Recovery is the only path that restores power
 * after a retain, and only once no DART provider, domain, page, overlay,
 * or quiesce flag remains.
 *
 * A failed teardown pins the module and init returns 0. cleanup_only is
 * the root-only recovery write. It never maps again.
 *
 * Probe and remove do not take the module lock. Overlay apply and
 * of_platform_device_destroy call them on this thread.
 */
#define pr_fmt(fmt) KBUILD_MODNAME ": " fmt

#include <linux/capability.h>
#include <linux/device.h>
#include <linux/err.h>
#include <linux/gfp.h>
#include <linux/io.h>
#include <linux/iommu.h>
#include <linux/iopoll.h>
#include <linux/ioport.h>
#include <linux/kernel.h>
#include <linux/mm.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/of_address.h>
#include <linux/of_platform.h>
#include <linux/platform_device.h>
#include <linux/string.h>
#include <linux/utsname.h>

#include "j713_isp_fwview_life.h"

#include "j713_isp_fwview_dtbo.inc"

#define J713_DM_RELEASE		"7.1.9-m4-gpu-backport-20261008"
#define J713_DM_PMGR_PATH	"/soc/power-management@380700000"
#define J713_DM_MASTER_PATH	"/soc/j713-isp-fwview-test"
#define J713_DM_DART_DRIVER	"apple-dart"
#define J713_DM_TLB_CMD		0x80u
#define J713_DM_TLB_BUSY	(1u << 31)
#define J713_DM_DART_ERROR	0x100u
#define J713_DM_TLB_SLEEP_US	10
#define J713_DM_TLB_TIMEOUT_US	100000
/* DART_T8110_TLB_CMD_OP_FLUSH_SID is 1 in bits 10:8. Stream is bits 7:0.
 * SID0 encodes as 0x100. Not ENABLE_STREAMS at 0xc00.
 */
#define J713_DM_TLB_FLUSH_SID0	((1u << 8) | J713_ISP_DART_SID0)

_Static_assert(J713_DM_TLB_FLUSH_SID0 == 0x100u, "flush sid0");

_Static_assert(J713_ISP_LOCAL_SIZE == 0x64u, "PMGR local claim is 0x64 bytes");
_Static_assert(J713_DM_PAGE_SIZE == 0x4000u, "page is 16KiB");
_Static_assert(J713_DM_ALLOC_PAGES == 12u, "Linux allocates heap and IPC only");
_Static_assert(J713_DM_TEXT_PAGES + J713_DM_DATA_PAGES + J713_DM_ALLOC_PAGES ==
	       J713_DM_PAGE_COUNT, "lookup covers every mapped page");
_Static_assert(J713_DM_TABLE_LEVELS == 4, "firmware window is four-level");
_Static_assert(J713_DM_PROT_READ == IOMMU_READ, "IOMMU_READ");
_Static_assert(J713_DM_PROT_WRITE == IOMMU_WRITE, "IOMMU_WRITE");
_Static_assert(J713_DM_PROT_CACHE == IOMMU_CACHE, "IOMMU_CACHE");
_Static_assert((J713_DM_TEXT_PROT & J713_DM_PROT_WRITE) == 0u,
	       "TEXT prot has no write bit");
_Static_assert((J713_DM_RW_PROT & J713_DM_PROT_WRITE) != 0u,
	       "RW prot has the write bit");

enum j713_dm_claim_id {
	CLAIM_LOCAL = 0,
	CLAIM_VER,
	CLAIM_COPROC,
	CLAIM_GPIO1,
	CLAIM_FABRIC0,
	CLAIM_FABRIC1,
	CLAIM_FABRIC2,
	CLAIM_FABRIC3,
	CLAIM_DART0,
	CLAIM_DART1,
	CLAIM_DART2,
	CLAIM_COUNT
};

struct j713_dm_claim {
	const char *name;
	resource_size_t phys;
	resource_size_t size;
	struct resource *res;
	void __iomem *map;
};

struct j713_dm_hw {
	void __iomem *pmgr;
	void __iomem *ver;
	void __iomem *coproc;
	void __iomem *dart_ex[J713_ISP_DART_INSTANCES];
	void __iomem *dart_ro[J713_ISP_DART_INSTANCES];
};

struct j713_dm_slot {
	u64 iova;
	phys_addr_t phys;
	bool live;
};

struct j713_dm_span {
	u64 iova;
	phys_addr_t phys;
	size_t size;
	bool live;
};

static bool map_check;
static bool dart_survey;
static unsigned int survey_tcr[J713_DM_SURVEY_WORDS];
static unsigned int survey_ttbr[J713_DM_SURVEY_WORDS];
static int result = -EAGAIN;
static int first_error;
static int cleanup_error;
static int cleanup_state;
static int cleanup_pending;
static int stage;
static int fail_stage;
static int power_restored;
static int retain;
static int cleanup;
static int verify;
static unsigned int map_count;
static unsigned int map_total;
static unsigned int lookup_count;
static unsigned int readback_count;
static unsigned int attempted;
static unsigned int isp_reads;
static unsigned int version;
static unsigned int flags;
static unsigned int gpio1_val;
static unsigned int fabric_val[4];
static unsigned int sid15_tcr[J713_ISP_DART_INSTANCES];
static unsigned int dart_protect[J713_ISP_DART_INSTANCES];
static unsigned int dart_error[J713_ISP_DART_INSTANCES];
static int ladder_stop;
static unsigned int text_prot;
static unsigned int data_prot;
static unsigned int heap_prot;
static unsigned int ipc_prot;
static unsigned int text_pte_bits[J713_DM_PTE_SAMPLES];
static unsigned int data_pte_bits[J713_DM_PTE_SAMPLES];
static unsigned int heap_pte_bits[J713_DM_PTE_SAMPLES];
static unsigned int ipc_pte_bits[J713_DM_PTE_SAMPLES];
static unsigned int pte_walk_count;

static struct j713_dm_claim claims[CLAIM_COUNT] = {
	[CLAIM_LOCAL] = { "j713_isp_fwview-local", J713_ISP_LOCAL_PHYS,
			  J713_ISP_LOCAL_SIZE },
	[CLAIM_VER] = { "j713_isp_fwview-ver", J713_ISP_VER_PHYS,
			J713_ISP_VER_SIZE },
	[CLAIM_COPROC] = { "j713_isp_fwview-coproc", J713_ISP_COPROC_PHYS,
			   J713_ISP_COPROC_SIZE },
	[CLAIM_GPIO1] = { "j713_isp_fwview-gpio1", J713_DM_GPIO1_PHYS,
			  J713_DM_MMIO_WORD },
	[CLAIM_FABRIC0] = { "j713_isp_fwview-fabric0", J713_DM_FABRIC0_PHYS,
			    J713_DM_MMIO_WORD },
	[CLAIM_FABRIC1] = { "j713_isp_fwview-fabric1", J713_DM_FABRIC1_PHYS,
			    J713_DM_MMIO_WORD },
	[CLAIM_FABRIC2] = { "j713_isp_fwview-fabric2", J713_DM_FABRIC2_PHYS,
			    J713_DM_MMIO_WORD },
	[CLAIM_FABRIC3] = { "j713_isp_fwview-fabric3", J713_DM_FABRIC3_PHYS,
			    J713_DM_MMIO_WORD },
	[CLAIM_DART0] = { "j713_isp_fwview-dart0", J713_ISP_DART0_PHYS,
			  J713_ISP_DART_SIZE },
	[CLAIM_DART1] = { "j713_isp_fwview-dart1", J713_ISP_DART1_PHYS,
			  J713_ISP_DART_SIZE },
	[CLAIM_DART2] = { "j713_isp_fwview-dart2", J713_ISP_DART2_PHYS,
			  J713_ISP_DART_SIZE },
};

static const char *const dart_paths[J713_ISP_DART_INSTANCES] = {
	"/soc/iommu@48e8c0000",
	"/soc/iommu@48e8e0000",
	"/soc/iommu@48e900000",
};

static const u64 dart_phys[J713_ISP_DART_INSTANCES] = {
	J713_ISP_DART0_PHYS, J713_ISP_DART1_PHYS, J713_ISP_DART2_PHYS
};

static DEFINE_MUTEX(j713_dm_lock);
static struct j713_dm dm;
static struct j713_dm_hw hw;
static struct j713_isp_state isp;
static struct j713_dm_span text_span;
static struct j713_dm_span data_span;
static struct j713_dm_slot heap_slots[J713_DM_HEAP_PAGES];
static struct j713_dm_slot ipc_slots[J713_DM_IPC_PAGES];
static struct page *heap_pages[J713_DM_HEAP_PAGES];
static struct page *ipc_pages[J713_DM_IPC_PAGES];
static struct iommu_domain *domain;
static struct device *master_dev;
static int ovcs_id;
static bool master_is_bound;
static bool driver_registered;
static bool pinned;
static unsigned int npages;

static void publish(void);
static int destroy_master_now(void);
static int master_still_present(void);
static int dart_still_present(const char *path);
static int owned_node_exists(void);

module_param(map_check, bool, 0400);
MODULE_PARM_DESC(map_check, "Map TEXT DATA heap IPC; default false is PMGR validate only");
module_param(dart_survey, bool, 0400);
MODULE_PARM_DESC(dart_survey, "Read-only DART survey; requires map_check; no overlay or DART write");
module_param_array(survey_tcr, uint, NULL, 0444);
MODULE_PARM_DESC(survey_tcr, "TCR for SID 0..15 on each DART; index instance*16+sid");
module_param_array(survey_ttbr, uint, NULL, 0444);
MODULE_PARM_DESC(survey_ttbr, "TTBR for SID 0..15 on each DART; index instance*16+sid");
module_param(result, int, 0444);
MODULE_PARM_DESC(result, "Experiment error, else cleanup error while retained, else power-sequence result");
module_param(first_error, int, 0444);
module_param(cleanup_error, int, 0444);
module_param(cleanup_state, int, 0444);
MODULE_PARM_DESC(cleanup_state, "0 idle, 1 complete, 2 pending");
module_param(cleanup_pending, int, 0444);
module_param(stage, int, 0444);
module_param(fail_stage, int, 0444);
module_param(power_restored, int, 0444);
module_param(retain, int, 0444);
module_param(cleanup, int, 0444);
MODULE_PARM_DESC(cleanup, "PMGR restore error from the power sequence; 0 if that writeback succeeded");
module_param(verify, int, 0444);
module_param(map_count, uint, 0444);
MODULE_PARM_DESC(map_count, "Live mapped pages; teardown clears this");
module_param(map_total, uint, 0444);
MODULE_PARM_DESC(map_total, "Cumulative pages successfully mapped");
module_param(lookup_count, uint, 0444);
module_param(readback_count, uint, 0444);
module_param(attempted, uint, 0444);
module_param(isp_reads, uint, 0444);
module_param(version, uint, 0444);
module_param(flags, uint, 0444);
module_param(gpio1_val, uint, 0444);
MODULE_PARM_DESC(gpio1_val, "GPIO1 word read before attach; not judged");
module_param_array(fabric_val, uint, NULL, 0444);
MODULE_PARM_DESC(fabric_val, "Fabric counters at 0x74c 0x84c 0x94c 0xa54");
module_param_array(sid15_tcr, uint, NULL, 0444);
MODULE_PARM_DESC(sid15_tcr, "SID15 TCR on each DART; not judged");
module_param_array(dart_protect, uint, NULL, 0444);
MODULE_PARM_DESC(dart_protect, "PROTECT on each DART; not judged");
module_param_array(dart_error, uint, NULL, 0444);
MODULE_PARM_DESC(dart_error, "ERROR on each DART before attach");
module_param(ladder_stop, int, 0444);
MODULE_PARM_DESC(ladder_stop, "EUCLEAN when heap or IPC was nonzero after detach");
module_param(text_prot, uint, 0444);
MODULE_PARM_DESC(text_prot, "prot passed to iommu_map for TEXT");
module_param(data_prot, uint, 0444);
MODULE_PARM_DESC(data_prot, "prot passed to iommu_map for DATA");
module_param(heap_prot, uint, 0444);
MODULE_PARM_DESC(heap_prot, "prot passed to iommu_map for heap");
module_param(ipc_prot, uint, 0444);
MODULE_PARM_DESC(ipc_prot, "prot passed to iommu_map for IPC");
module_param_array(text_pte_bits, uint, NULL, 0444);
MODULE_PARM_DESC(text_pte_bits, "Low 32 bits of the first, middle, and last TEXT leaf");
module_param_array(data_pte_bits, uint, NULL, 0444);
MODULE_PARM_DESC(data_pte_bits, "Low 32 bits of the first, middle, and last DATA leaf");
module_param_array(heap_pte_bits, uint, NULL, 0444);
MODULE_PARM_DESC(heap_pte_bits, "Low 32 bits of the first, middle, and last heap leaf");
module_param_array(ipc_pte_bits, uint, NULL, 0444);
MODULE_PARM_DESC(ipc_pte_bits, "Low 32 bits of the first, middle, and last IPC leaf");
module_param(pte_walk_count, uint, 0444);
MODULE_PARM_DESC(pte_walk_count, "Leaf PTEs read while the map was live");

static void pin_module(void)
{
	if (pinned)
		return;
	__module_get(THIS_MODULE);
	pinned = true;
}

static void unpin_module(void)
{
	if (!pinned)
		return;
	pinned = false;
	module_put(THIS_MODULE);
}

static void publish(void)
{
	int pending = dm.cleanup_state == J713_DM_CLEAN_PENDING || dm.retain ||
		      isp.retain_power;

	flags = dm.flags;
	first_error = dm.first_error;
	cleanup_error = dm.cleanup_error;
	cleanup_state = dm.cleanup_state;
	cleanup_pending = pending ? 1 : 0;
	retain = dm.retain || isp.retain_power;
	power_restored = dm.power_restored;
	map_count = dm.map_count;
	map_total = dm.map_total;
	lookup_count = dm.lookup_count;
	readback_count = dm.readback_count;
	ladder_stop = dm.ladder_stop;
	cleanup = isp.cleanup;
	verify = isp.verify;
	attempted = isp.attempted;
	isp_reads = isp.isp_reads;
	version = isp.version;
	if (dm.fail_stage)
		fail_stage = dm.fail_stage;
	else
		fail_stage = isp.fail_stage;
	if (dm.stage)
		stage = dm.stage;
	else if (isp.fail_stage)
		stage = isp.fail_stage;
	else
		stage = isp.stage;
	if (dm.first_error)
		result = dm.first_error;
	else if (dm.cleanup_error && pending)
		result = dm.cleanup_error;
	else
		result = isp.result;
	pr_info("result=%d first=%d cleanup_error=%d state=%d pending=%d stage=%d fail=%d power_restored=%d retain=%d flags=0x%x map=%u/%u lookup=%u readback=%u ladder=%d cleanup=%d verify=%d attempted=%u version=0x%x pte_walk=%u\n",
		result, first_error, cleanup_error, cleanup_state, cleanup_pending,
		stage, fail_stage, power_restored, retain, flags, map_count,
		map_total, lookup_count, readback_count, ladder_stop, cleanup,
		verify, attempted, version, pte_walk_count);
}

static int hw_read32(void *ctx, int space, u32 off, u32 *val)
{
	struct j713_dm_hw *h = ctx;
	void __iomem *base = NULL;

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
	if (space == J713_ISP_SPACE_COPROC) {
		if (!h->coproc || !j713_isp_inspect_off_ok(space, off))
			return -EINVAL;
		*val = readl(h->coproc + off);
		return 0;
	}
	if (space == J713_ISP_SPACE_GPIO)
		return -EINVAL;
	if (space >= J713_ISP_SPACE_DART0 && space <= J713_ISP_SPACE_DART2) {
		unsigned int inst = space - J713_ISP_SPACE_DART0;

		if (!j713_isp_inspect_off_ok(space, off))
			return -EINVAL;
		/* Exclusive and read-only views must not both be live. */
		if (h->dart_ex[inst] && h->dart_ro[inst])
			return -EBUSY;
		base = h->dart_ex[inst] ? h->dart_ex[inst] : h->dart_ro[inst];
		if (!base)
			return -EINVAL;
		*val = readl(base + off);
		return 0;
	}
	return -EINVAL;
}

static int hw_write32(void *ctx, int space, u32 off, u32 val)
{
	struct j713_dm_hw *h = ctx;

	/* Local request nibble only. Never a main rail, COPROC, GPIO, or DART. */
	if (space != J713_ISP_SPACE_PMGR)
		return -EINVAL;
	if (off == J713_ISP_MAIN_188 || off == J713_ISP_MAIN_190 ||
	    off == J713_ISP_MAIN_530)
		return -EPERM;
	if (!h->pmgr || off < J713_ISP_LOCAL_BASE || off > 0x4060 || (off & 7))
		return -EINVAL;
	writel(val, h->pmgr + off);
	return 0;
}

static int hw_poll_ps(void *ctx, u32 off, u32 want_low)
{
	struct j713_dm_hw *h = ctx;
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
};

static bool resource_ok(struct device_node *np, int index, u64 base, u64 size)
{
	struct resource r;

	return !of_address_to_resource(np, index, &r) && r.start == base &&
	       resource_size(&r) == size;
}

static bool node_exists(const char *path)
{
	struct device_node *np = of_find_node_by_path(path);

	if (np)
		of_node_put(np);
	return np != NULL;
}

static int reject_compat(const char *compat)
{
	struct device_node *np = of_find_compatible_node(NULL, NULL, compat);

	if (!np)
		return 0;
	of_node_put(np);
	return -EEXIST;
}

static int guard_board(void)
{
	if (!capable(CAP_SYS_RAWIO))
		return -EPERM;
	if (!of_machine_is_compatible("apple,j713") ||
	    !of_machine_is_compatible("apple,t8132"))
		return -EPERM;
	if (strcmp(utsname()->release, J713_DM_RELEASE))
		return -EPERM;
	return 0;
}

static int guard_pmgr_dt(void)
{
	struct device_node *np = of_find_node_by_path(J713_DM_PMGR_PATH);
	bool ok;

	if (!np)
		return -ENODEV;
	ok = resource_ok(np, 0, J713_ISP_PMGR_PHYS, J713_ISP_PMGR_SIZE) &&
	     of_device_is_compatible(np, "apple,t8132-pmgr") &&
	     of_device_is_compatible(np, "apple,t8103-pmgr") &&
	     of_device_is_compatible(np, "syscon") &&
	     of_device_is_compatible(np, "simple-mfd");
	of_node_put(np);
	return ok ? 0 : -EINVAL;
}

static int guard_aic(void)
{
	struct device_node *np = of_find_node_by_path(J713_DM_AIC_PATH);
	u32 cells = 0;

	if (!np)
		return -ENODEV;
	if (np->phandle != J713_DM_AIC_PHANDLE ||
	    of_property_read_u32(np, "#interrupt-cells", &cells) ||
	    cells != J713_DM_AIC_CELLS ||
	    !of_device_is_compatible(np, "apple,t8132-aic3") ||
	    !of_device_is_compatible(np, "apple,t8122-aic3") ||
	    !resource_ok(np, 0, J713_DM_AIC_REG0, J713_DM_AIC_REG0_SIZE) ||
	    !resource_ok(np, 1, J713_DM_AIC_REG1, J713_DM_AIC_REG1_SIZE)) {
		of_node_put(np);
		return -EINVAL;
	}
	of_node_put(np);
	return 0;
}

static int guard_soc(void)
{
	struct device_node *np = of_find_node_by_path("/soc");
	u32 ac = 0, sc = 0;

	if (!np)
		return -ENODEV;
	if (of_property_read_u32(np, "#address-cells", &ac) ||
	    of_property_read_u32(np, "#size-cells", &sc) ||
	    ac != 2 || sc != 2) {
		of_node_put(np);
		return -EINVAL;
	}
	of_node_put(np);
	return 0;
}

static int guard_no_isp(void)
{
	static const char *const isp_compat[] = {
		"apple,t8132-isp", "apple,t8122-isp", "apple,t8140-isp",
		"apple,t8103-isp", "apple,t8112-isp", "apple-isp"
	};
	static const char *const dart_names[] = {
		"dart-isp", "mapper-isp", "mapper-isp-mpm"
	};
	static const char *const dart_extra[] = {
		"/soc/dart-isp", "/arm-io/dart-isp"
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
	ret = reject_compat(J713_DM_MASTER_COMPAT);
	if (ret)
		return ret;
	if (node_exists(J713_DM_MASTER_PATH))
		return -EEXIST;
	if (node_exists(J713_DM_PMGR_PATH "/power-controller@188") ||
	    node_exists(J713_DM_PMGR_PATH "/power-controller@190") ||
	    node_exists(J713_DM_PMGR_PATH "/power-controller@530"))
		return -EEXIST;
	for (i = 0; i < ARRAY_SIZE(dart_names); i++) {
		np = of_find_node_by_name(NULL, dart_names[i]);
		if (np) {
			of_node_put(np);
			return -EEXIST;
		}
	}
	for (i = 0; i < J713_ISP_DART_INSTANCES; i++) {
		if (node_exists(dart_paths[i]))
			return -EEXIST;
	}
	for (i = 0; i < ARRAY_SIZE(dart_extra); i++) {
		if (node_exists(dart_extra[i]))
			return -EEXIST;
	}
	/* 7.1.9 driver_find drops the kset reference before returning. */
	drv = driver_find("apple-isp", &platform_bus_type);
	if (drv)
		return -EEXIST;
	drv = driver_find(J713_DM_DART_DRIVER, &platform_bus_type);
	if (!drv)
		return -ENODEV;
	return 0;
}

static int guard_layout(void)
{
	u32 totalsize;

	if (PAGE_SIZE != J713_DM_PAGE_SIZE)
		return -EINVAL;
	if (j713_dm_effective_ias(J713_DM_HW_IAS, j713_dm_dma_last()) !=
	    J713_DM_EFFECTIVE_IAS)
		return -EINVAL;
	if (j713_dm_levels(J713_DM_EFFECTIVE_IAS, 14) != J713_DM_TABLE_LEVELS)
		return -EINVAL;
	if (J713_DM_IPC_IOVA + J713_DM_IPC_SIZE >
	    J713_DM_DMA_BASE + J713_DM_DMA_SIZE)
		return -EINVAL;
	/* Refuse before claim_windows. of_overlay_fdt_apply rejects this. */
	if (((unsigned long)j713_dm_dtbo) % 8UL != 0UL) {
		pr_err("embedded DTBO pointer is not 8-byte aligned; no writes\n");
		return -EINVAL;
	}
	if (j713_dm_dtbo_len < 8)
		return -EINVAL;
	if (j713_dm_dtbo[0] != 0xd0 || j713_dm_dtbo[1] != 0x0d ||
	    j713_dm_dtbo[2] != 0xfe || j713_dm_dtbo[3] != 0xed)
		return -EINVAL;
	totalsize = ((u32)j713_dm_dtbo[4] << 24) | ((u32)j713_dm_dtbo[5] << 16) |
		    ((u32)j713_dm_dtbo[6] << 8) | j713_dm_dtbo[7];
	if (totalsize < 8 || totalsize > j713_dm_dtbo_len)
		return -EINVAL;
	return 0;
}

static void release_slot(int idx)
{
	struct j713_dm_claim *c;

	if (idx < 0 || idx >= CLAIM_COUNT)
		return;
	c = &claims[idx];
	if (c->map) {
		iounmap(c->map);
		c->map = NULL;
	}
	if (c->res) {
		release_mem_region(c->phys, c->size);
		c->res = NULL;
	}
}

static void release_dart_exclusive(void)
{
	unsigned int i;

	for (i = 0; i < J713_ISP_DART_INSTANCES; i++) {
		release_slot(CLAIM_DART0 + i);
		hw.dart_ex[i] = NULL;
	}
}

static void release_ro(void)
{
	unsigned int i;

	for (i = 0; i < J713_ISP_DART_INSTANCES; i++) {
		if (hw.dart_ro[i]) {
			iounmap(hw.dart_ro[i]);
			hw.dart_ro[i] = NULL;
		}
	}
}

/* Drop every window a partially restored ISP must not be read through. */
static void release_non_pmgr(void)
{
	release_slot(CLAIM_VER);
	release_slot(CLAIM_COPROC);
	release_slot(CLAIM_GPIO1);
	release_slot(CLAIM_FABRIC0);
	release_slot(CLAIM_FABRIC1);
	release_slot(CLAIM_FABRIC2);
	release_slot(CLAIM_FABRIC3);
	hw.ver = NULL;
	hw.coproc = NULL;
	release_dart_exclusive();
	release_ro();
}

static void release_all_mappings(void)
{
	release_non_pmgr();
	release_slot(CLAIM_LOCAL);
	if (hw.pmgr) {
		iounmap(hw.pmgr);
		hw.pmgr = NULL;
	}
}

static int claim_windows(void)
{
	struct j713_isp_range ranges[CLAIM_COUNT];
	unsigned int i;
	int ret;

	for (i = 0; i < CLAIM_COUNT; i++) {
		ranges[i].start = claims[i].phys;
		ranges[i].size = claims[i].size;
		claims[i].res = NULL;
		claims[i].map = NULL;
	}
	ret = j713_isp_ranges_overlap(ranges, CLAIM_COUNT);
	if (ret) {
		pr_err("exclusive claim windows overlap; no writes\n");
		return ret;
	}
	for (i = 0; i < CLAIM_COUNT; i++) {
		claims[i].res = request_mem_region(claims[i].phys, claims[i].size,
						   claims[i].name);
		if (!claims[i].res) {
			pr_err("%s 0x%llx size 0x%llx busy; no writes\n",
			       claims[i].name, (unsigned long long)claims[i].phys,
			       (unsigned long long)claims[i].size);
			ret = -EBUSY;
			goto unwind;
		}
		claims[i].map = ioremap(claims[i].phys, claims[i].size);
		if (!claims[i].map) {
			pr_err("%s 0x%llx ioremap failed; no writes\n",
			       claims[i].name, (unsigned long long)claims[i].phys);
			release_slot(i);
			ret = -ENOMEM;
			goto unwind;
		}
	}
	hw.ver = claims[CLAIM_VER].map;
	hw.coproc = claims[CLAIM_COPROC].map;
	for (i = 0; i < J713_ISP_DART_INSTANCES; i++)
		hw.dart_ex[i] = claims[CLAIM_DART0 + i].map;
	return 0;
unwind:
	while (i--)
		release_slot(i);
	hw.ver = NULL;
	hw.coproc = NULL;
	return ret;
}

static int count_dev(struct device *dev, void *data)
{
	unsigned int *n = data;

	(void)dev;
	(*n)++;
	return 0;
}

static int group_is_singleton(struct device *dev)
{
	struct iommu_group *group;
	unsigned int n = 0;
	int ret;

	group = iommu_group_get(dev);
	if (!group)
		return -ENODEV;
	ret = iommu_group_for_each_dev(group, &n, count_dev);
	iommu_group_put(group);
	if (ret)
		return ret;
	if (n != 1)
		return -EINVAL;
	return 0;
}

static int provider_bound(const char *path)
{
	struct device_node *np;
	struct platform_device *pdev;
	const char *name = NULL;
	int ok = 0;

	np = of_find_node_by_path(path);
	if (!np)
		return -ENODEV;
	pdev = of_find_device_by_node(np);
	if (pdev && pdev->dev.driver && pdev->dev.driver->name)
		name = pdev->dev.driver->name;
	if (name && !strcmp(name, J713_DM_DART_DRIVER) &&
	    of_node_check_flag(np, OF_POPULATED))
		ok = 1;
	pr_info("%s driver=%s populated=%d\n", path, name ? name : "-",
		of_node_check_flag(np, OF_POPULATED));
	if (pdev)
		platform_device_put(pdev);
	of_node_put(np);
	return ok ? 0 : -ENODEV;
}

static int j713_dm_probe(struct platform_device *pdev)
{
	if (master_dev)
		return -EBUSY;
	master_dev = &pdev->dev;
	master_is_bound = true;
	return 0;
}

static void j713_dm_remove(struct platform_device *pdev)
{
	if (master_dev == &pdev->dev) {
		master_dev = NULL;
		master_is_bound = false;
	}
}

static const struct of_device_id j713_dm_ids[] = {
	{ .compatible = J713_DM_MASTER_COMPAT },
	{ }
};
MODULE_DEVICE_TABLE(of, j713_dm_ids);

static struct platform_driver j713_dm_pdrv = {
	.probe = j713_dm_probe,
	.remove = j713_dm_remove,
	.driver = {
		.name = "j713-isp-fwview-test",
		.of_match_table = j713_dm_ids,
		.probe_type = PROBE_FORCE_SYNCHRONOUS,
		.suppress_bind_attrs = true,
		.owner = THIS_MODULE,
	},
};

static int destroy_np(struct device_node *np)
{
	struct platform_device *pdev;
	int populated;
	int ret = 0;

	if (!np)
		return 0;
	populated = of_node_check_flag(np, OF_POPULATED);
	pdev = of_find_device_by_node(np);
	if (!pdev) {
		of_node_put(np);
		return populated ? -ENODEV : 0;
	}
	/*
	 * OF_POPULATED clear happens inside of_platform_device_destroy,
	 * before platform_device_unregister. Put the find ref while the
	 * overlay that owns the node is still alive.
	 */
	if (!populated) {
		platform_device_put(pdev);
		of_node_put(np);
		return -EBUSY;
	}
	ret = of_platform_device_destroy(&pdev->dev, NULL);
	platform_device_put(pdev);
	of_node_put(np);
	return ret;
}

static int destroy_master_now(void)
{
	struct device_node *np;

	np = of_find_compatible_node(NULL, NULL, J713_DM_MASTER_COMPAT);
	if (!np)
		return 0;
	return destroy_np(np);
}

static int destroy_dart_at(const char *path)
{
	struct device_node *np = of_find_node_by_path(path);

	if (!np)
		return 0;
	return destroy_np(np);
}

static int node_device_present(struct device_node *np)
{
	struct platform_device *pdev;
	int present;

	if (!np)
		return 0;
	pdev = of_find_device_by_node(np);
	present = pdev || of_node_check_flag(np, OF_POPULATED);
	if (pdev)
		platform_device_put(pdev);
	of_node_put(np);
	return present;
}

static int master_still_present(void)
{
	return node_device_present(of_find_compatible_node(NULL, NULL,
							   J713_DM_MASTER_COMPAT));
}

static int dart_still_present(const char *path)
{
	return node_device_present(of_find_node_by_path(path));
}

static int owned_node_exists(void)
{
	struct device_node *np;
	unsigned int i;

	np = of_find_compatible_node(NULL, NULL, J713_DM_MASTER_COMPAT);
	if (np) {
		of_node_put(np);
		return 1;
	}
	np = of_find_node_by_path(J713_DM_MASTER_PATH);
	if (np) {
		of_node_put(np);
		return 1;
	}
	for (i = 0; i < J713_ISP_DART_INSTANCES; i++) {
		np = of_find_node_by_path(dart_paths[i]);
		if (np) {
			of_node_put(np);
			return 1;
		}
	}
	return 0;
}

static int op_cpu(void *ctx, struct j713_dm *life)
{
	u32 ctrl = 0, status = 0;
	int rc;

	(void)life;
	if (!hw.coproc)
		return -EIO;
	rc = hw_read32(ctx ? ctx : &hw, J713_ISP_SPACE_COPROC,
		       J713_ISP_COPROC_CTRL, &ctrl);
	if (rc)
		return rc;
	rc = hw_read32(ctx ? ctx : &hw, J713_ISP_SPACE_COPROC,
		       J713_ISP_COPROC_STATUS, &status);
	if (rc)
		return rc;
	pr_info("cpu ctrl=0x%x status=0x%x\n", ctrl, status);
	if (!j713_dm_cpu_stopped(ctrl, status))
		return -EBUSY;
	return 0;
}

static int op_baseline(void *ctx, struct j713_dm *life)
{
	static const u32 offs[] = {
		J713_ISP_DART_PARAMS1, J713_ISP_DART_PARAMS3, J713_ISP_DART_PARAMS4,
		J713_ISP_DART_PROTECT, J713_ISP_DART_SID0_TCR, J713_ISP_DART_SID0_TTBR,
		J713_ISP_DART_SID12_TCR, J713_ISP_DART_SID12_TTBR
	};
	unsigned int inst, i;
	int rc;

	(void)life;
	for (inst = 0; inst < J713_ISP_DART_INSTANCES; inst++) {
		u32 val[ARRAY_SIZE(offs)];

		if (!hw.dart_ex[inst])
			return -EINVAL;
		for (i = 0; i < ARRAY_SIZE(offs); i++) {
			rc = hw_read32(ctx ? ctx : &hw, J713_ISP_SPACE_DART0 + inst,
				       offs[i], &val[i]);
			if (rc)
				return rc;
		}
		pr_info("baseline dart%u params1=0x%x params3=0x%x params4=0x%x protect=0x%x sid0 tcr=0x%x ttbr=0x%x sid12 tcr=0x%x ttbr=0x%x\n",
			inst, val[0], val[1], val[2], val[3], val[4], val[5],
			val[6], val[7]);
		if (val[1] != J713_DM_PARAMS3_EXPECT)
			return -EPROTO;
		if ((val[0] & J713_DM_PARAMS1_SHIFT_MASK) != J713_DM_PARAMS1_SHIFT_14)
			return -EPROTO;
		if ((val[2] & J713_DM_PARAMS4_SID_MASK) != J713_DM_SID_COUNT)
			return -EPROTO;
		if (val[3] & J713_DM_PROTECT_LOCK)
			return -EPROTO;
		/* SID0 and SID12 TCR and TTBR are recorded on every instance
		 * and are not a gate. PARAMS and PROTECT still gate the map.
		 * ERROR is judged later by the premise, version by the power
		 * sequence, and COPROC by the stopped-CPU check. apple-dart's
		 * probe resets every SID to TCR 0 (tcr_disabled) when the
		 * overlay is applied, before attach. A valid TTBR left by an
		 * earlier domain can survive the local power gates, so
		 * requiring TCR 0x1 here aborts before that reset. The mapped
		 * readback below still requires 0x9.
		 */
	}
	return 0;
}

static int op_release_dart(void *ctx, struct j713_dm *life)
{
	unsigned int i;

	(void)ctx;
	(void)life;
	release_dart_exclusive();
	for (i = 0; i < J713_ISP_DART_INSTANCES; i++) {
		if (claims[CLAIM_DART0 + i].res || claims[CLAIM_DART0 + i].map ||
		    hw.dart_ex[i])
			return -EBUSY;
	}
	return 0;
}

static int op_register(void *ctx, struct j713_dm *life)
{
	int ret;

	(void)ctx;
	(void)life;
	if (driver_registered)
		return J713_DM_ERR_OWNED;
	ret = platform_driver_register(&j713_dm_pdrv);
	if (ret)
		return ret;
	driver_registered = true;
	return 0;
}

static int op_apply(void *ctx, struct j713_dm *life)
{
	struct device_node *soc;
	int id = 0;
	int ret;

	(void)ctx;
	(void)life;
	soc = of_find_node_by_path("/soc");
	pr_info("soc populated-bus=%d\n",
		soc && of_node_check_flag(soc, OF_POPULATED_BUS));
	if (soc)
		of_node_put(soc);
	ret = of_overlay_fdt_apply(j713_dm_dtbo, j713_dm_dtbo_len, &id, NULL);
	ovcs_id = id;
	pr_info("overlay apply ret=%d id=%d\n", ret, id);
	if (ret) {
		if (id > 0 || owned_node_exists())
			return J713_DM_ERR_OWNED;
		ovcs_id = 0;
		return ret;
	}
	if (id <= 0 || !owned_node_exists())
		return J713_DM_ERR_OWNED;
	return 0;
}

static int op_master_bound(void *ctx, struct j713_dm *life)
{
	unsigned int i;
	int ret;

	(void)ctx;
	(void)life;
	if (!master_is_bound || !master_dev ||
	    master_dev->driver != &j713_dm_pdrv.driver) {
		pr_err("master not bound\n");
		return -ENODEV;
	}
	for (i = 0; i < J713_ISP_DART_INSTANCES; i++) {
		ret = provider_bound(dart_paths[i]);
		if (ret) {
			pr_err("provider %s not bound\n", dart_paths[i]);
			return ret;
		}
	}
	ret = group_is_singleton(master_dev);
	if (ret) {
		pr_err("iommu group is not a singleton (%d)\n", ret);
		return ret;
	}
	return 0;
}

static int op_remap_ro(void *ctx, struct j713_dm *life)
{
	unsigned int i;

	(void)ctx;
	(void)life;
	for (i = 0; i < J713_ISP_DART_INSTANCES; i++) {
		if (claims[CLAIM_DART0 + i].res || hw.dart_ex[i])
			return -EBUSY;
	}
	for (i = 0; i < J713_ISP_DART_INSTANCES; i++) {
		hw.dart_ro[i] = ioremap(dart_phys[i], J713_ISP_DART_SIZE);
		if (!hw.dart_ro[i]) {
			release_ro();
			return -ENOMEM;
		}
	}
	return 0;
}

static void free_one_alloc(struct page **page)
{
	if (!page || !*page)
		return;
	__free_page(*page);
	*page = NULL;
}

static void free_alloc_pages(void)
{
	unsigned int i;

	for (i = 0; i < J713_DM_HEAP_PAGES; i++)
		free_one_alloc(&heap_pages[i]);
	for (i = 0; i < J713_DM_IPC_PAGES; i++)
		free_one_alloc(&ipc_pages[i]);
	npages = 0;
}

static int alloc_pages_are_safe(void)
{
	unsigned int i;

	for (i = 0; i < J713_DM_HEAP_PAGES; i++) {
		if (heap_pages[i] &&
		    j713_dm_phys_is_preloaded(page_to_phys(heap_pages[i])))
			return 0;
	}
	for (i = 0; i < J713_DM_IPC_PAGES; i++) {
		if (ipc_pages[i] &&
		    j713_dm_phys_is_preloaded(page_to_phys(ipc_pages[i])))
			return 0;
	}
	return 1;
}

static int op_alloc_pages(void *ctx, struct j713_dm *life)
{
	unsigned int i;

	(void)ctx;
	(void)life;
	if (PAGE_SIZE != J713_DM_PAGE_SIZE)
		return -EINVAL;
	if (npages)
		return J713_DM_ERR_OWNED;
	for (i = 0; i < J713_DM_HEAP_PAGES; i++) {
		heap_pages[i] = alloc_page(GFP_KERNEL | __GFP_ZERO);
		if (!heap_pages[i]) {
			if (!alloc_pages_are_safe())
				return -EINVAL;
			free_alloc_pages();
			return -ENOMEM;
		}
	}
	for (i = 0; i < J713_DM_IPC_PAGES; i++) {
		ipc_pages[i] = alloc_page(GFP_KERNEL | __GFP_ZERO);
		if (!ipc_pages[i]) {
			if (!alloc_pages_are_safe())
				return -EINVAL;
			free_alloc_pages();
			return -ENOMEM;
		}
	}
	npages = J713_DM_ALLOC_PAGES;
	return 0;
}

static int op_alloc_domain(void *ctx, struct j713_dm *life)
{
	struct iommu_domain *created;

	(void)ctx;
	(void)life;
	if (domain)
		return J713_DM_ERR_OWNED;
	if (!master_is_bound || !master_dev)
		return -ENODEV;
	created = iommu_paging_domain_alloc(master_dev);
	if (IS_ERR(created))
		return PTR_ERR(created);
	if (!created)
		return -ENOMEM;
	domain = created;
	return 0;
}

static int op_attach(void *ctx, struct j713_dm *life)
{
	struct iommu_domain *got;
	int ret;

	(void)ctx;
	(void)life;
	if (!domain || !master_is_bound || !master_dev)
		return -ENODEV;
	ret = group_is_singleton(master_dev);
	if (ret)
		return ret;
	ret = iommu_attach_device(domain, master_dev);
	got = iommu_get_domain_for_dev(master_dev);
	if (ret) {
		if (got == domain)
			return J713_DM_ERR_OWNED;
		return ret;
	}
	if (got != domain)
		return J713_DM_ERR_OWNED;
	return 0;
}

static unsigned int live_maps(void)
{
	unsigned int i, n = 0;

	if (text_span.live)
		n += J713_DM_TEXT_PAGES;
	if (data_span.live)
		n += J713_DM_DATA_PAGES;
	for (i = 0; i < J713_DM_HEAP_PAGES; i++) {
		if (heap_slots[i].live)
			n++;
	}
	for (i = 0; i < J713_DM_IPC_PAGES; i++) {
		if (ipc_slots[i].live)
			n++;
	}
	return n;
}

/* API failure with no translation: drop the record. Any other outcome may
 * have programmed the DART, so keep it for unmap of the same size.
 */
static int map_kept(struct j713_dm *life, int ret, phys_addr_t got,
		    phys_addr_t phys, bool *live)
{
	if (!ret && got == phys)
		return 0;
	if (!ret || got) {
		life->map_count = live_maps();
		return J713_DM_ERR_OWNED;
	}
	*live = false;
	life->map_count = live_maps();
	return life->map_count ? J713_DM_ERR_OWNED : ret;
}

static int map_span(struct j713_dm *life, struct j713_dm_span *span,
		    u64 iova, phys_addr_t phys, size_t size, int prot)
{
	phys_addr_t got;
	int ret;

	if (!size || (size & (J713_DM_PAGE_SIZE - 1u)) || !phys)
		return -EINVAL;
	if (!j713_dm_span_is_preloaded((u64)phys, (u64)size))
		return -EINVAL;
	span->iova = iova;
	span->phys = phys;
	span->size = size;
	span->live = true;
	life->map_count = live_maps();
	ret = iommu_map(domain, (unsigned long)iova, phys, size, prot, GFP_KERNEL);
	got = iommu_iova_to_phys(domain, (dma_addr_t)iova);
	pr_info("map iova=0x%llx phys=0x%llx size=0x%zx got=0x%llx ret=%d\n",
		(unsigned long long)iova, (unsigned long long)phys, size,
		(unsigned long long)got, ret);
	return map_kept(life, ret, got, phys, &span->live);
}

static int map_slot(struct j713_dm *life, struct j713_dm_slot *slot,
		    struct page *page, u64 iova, int prot)
{
	phys_addr_t phys;
	phys_addr_t got;
	int ret;

	if (!page)
		return -EINVAL;
	phys = page_to_phys(page);
	if (!phys || j713_dm_phys_is_preloaded(phys))
		return -EINVAL;
	slot->iova = iova;
	slot->phys = phys;
	slot->live = true;
	life->map_count = live_maps();
	ret = iommu_map(domain, (unsigned long)iova, phys, PAGE_SIZE, prot,
			GFP_KERNEL);
	got = iommu_iova_to_phys(domain, (dma_addr_t)iova);
	pr_info("map iova=0x%llx phys=0x%llx got=0x%llx ret=%d\n",
		(unsigned long long)iova, (unsigned long long)phys,
		(unsigned long long)got, ret);
	return map_kept(life, ret, got, phys, &slot->live);
}

static int op_map(void *ctx, struct j713_dm *life)
{
	unsigned int i;
	int ret;

	(void)ctx;
	/* text_prot and the other prot parameters are the values passed
	 * to iommu_map. The page-table walk in readback checks the DART2
	 * leaf that resulted. No cache maintenance: dcache_clean_poc is
	 * not exported on this kernel, and the processor never reads
	 * these pages in E2.
	 */
	if (!domain || !master_is_bound || !master_dev ||
	    npages != J713_DM_ALLOC_PAGES)
		return -EINVAL;
	if (J713_DM_TEXT_PROT & J713_DM_PROT_WRITE)
		return -EINVAL;
	text_prot = J713_DM_TEXT_PROT;
	data_prot = J713_DM_RW_PROT;
	heap_prot = J713_DM_RW_PROT;
	ipc_prot = J713_DM_RW_PROT;
	ret = map_span(life, &text_span, J713_DM_TEXT_IOVA, J713_DM_TEXT_PHYS,
		       J713_DM_TEXT_SIZE, J713_DM_TEXT_PROT);
	if (ret)
		return ret;
	ret = map_span(life, &data_span, J713_DM_DATA_IOVA, J713_DM_DATA_PHYS,
		       J713_DM_DATA_SIZE, J713_DM_RW_PROT);
	if (ret)
		return ret;
	for (i = 0; i < J713_DM_HEAP_PAGES; i++) {
		ret = map_slot(life, &heap_slots[i], heap_pages[i],
			       j713_dm_heap_iova(i), J713_DM_RW_PROT);
		if (ret)
			return ret;
	}
	for (i = 0; i < J713_DM_IPC_PAGES; i++) {
		ret = map_slot(life, &ipc_slots[i], ipc_pages[i],
			       j713_dm_ipc_iova(i), J713_DM_RW_PROT);
		if (ret)
			return ret;
	}
	life->map_count = live_maps();
	if (life->map_count != J713_DM_PAGE_COUNT)
		return J713_DM_ERR_OWNED;
	return 0;
}

static int lookup_page(struct j713_dm *life, u64 iova, phys_addr_t expect)
{
	phys_addr_t got = iommu_iova_to_phys(domain, (dma_addr_t)iova);

	pr_info("lookup iova=0x%llx phys=0x%llx got=0x%llx\n",
		(unsigned long long)iova, (unsigned long long)expect,
		(unsigned long long)got);
	if (got != expect)
		return -EIO;
	life->lookup_count++;
	return 0;
}

static int op_lookup(void *ctx, struct j713_dm *life)
{
	unsigned int i;
	int rc;

	(void)ctx;
	life->lookup_count = 0;
	if (!domain || life->map_count != J713_DM_PAGE_COUNT)
		return -EINVAL;
	if (!text_span.live || !data_span.live)
		return -EINVAL;
	for (i = 0; i < J713_DM_TEXT_PAGES; i++) {
		rc = lookup_page(life, j713_dm_text_iova(i), j713_dm_text_phys(i));
		if (rc)
			return rc;
	}
	for (i = 0; i < J713_DM_DATA_PAGES; i++) {
		rc = lookup_page(life, j713_dm_data_iova(i), j713_dm_data_phys(i));
		if (rc)
			return rc;
	}
	for (i = 0; i < J713_DM_HEAP_PAGES; i++) {
		if (!heap_slots[i].live)
			return -EINVAL;
		rc = lookup_page(life, heap_slots[i].iova, heap_slots[i].phys);
		if (rc)
			return rc;
	}
	for (i = 0; i < J713_DM_IPC_PAGES; i++) {
		if (!ipc_slots[i].live)
			return -EINVAL;
		rc = lookup_page(life, ipc_slots[i].iova, ipc_slots[i].phys);
		if (rc)
			return rc;
	}
	return life->lookup_count == J713_DM_PAGE_COUNT ? 0 : -EIO;
}

/* Read allow-list: the survey globals, every SID TCR/TTBR, and the TLB
 * and ERROR words readback already used. PARAMS2 stays off this list.
 * Exactly one of the exclusive and read-only windows may be live.
 */
static bool dart_ro_off_ok(u32 off)
{
	unsigned int sid;

	switch (off) {
	case J713_ISP_DART_PARAMS1:
	case J713_ISP_DART_PARAMS3:
	case J713_ISP_DART_PARAMS4:
	case J713_ISP_DART_PROTECT:
	case J713_DM_DART_ERROR:
	case J713_DM_TLB_CMD:
		return true;
	default:
		break;
	}
	for (sid = 0; sid < J713_DM_SID_COUNT; sid++) {
		if (off == J713_ISP_DART_TCR(sid) || off == J713_ISP_DART_TTBR(sid))
			return true;
	}
	return false;
}

/* The only DART write allow-list. SID0 TCR, SID0 TTBR, and the TLB command. */
static bool dart_wr_off_ok(u32 off)
{
	return off == J713_ISP_DART_TCR(J713_ISP_DART_SID0) ||
	       off == J713_ISP_DART_TTBR(J713_ISP_DART_SID0) ||
	       off == J713_DM_TLB_CMD;
}

static int dart_ro_read(unsigned int inst, u32 off, u32 *val)
{
	void __iomem *base;

	if (inst >= J713_ISP_DART_INSTANCES || !val || !dart_ro_off_ok(off))
		return -EINVAL;
	if (hw.dart_ro[inst] && hw.dart_ex[inst])
		return -EBUSY;
	base = hw.dart_ro[inst] ? hw.dart_ro[inst] : hw.dart_ex[inst];
	if (!base)
		return -EINVAL;
	*val = readl(base + off);
	return 0;
}

/* Requires the read-only window. Refuses when the exclusive window is
 * also live, so a survey (exclusive only) cannot write.
 */
static int dart_ro_write(unsigned int inst, u32 off, u32 val)
{
	if (inst >= J713_ISP_DART_INSTANCES || !dart_wr_off_ok(off))
		return -EINVAL;
	if (!hw.dart_ro[inst])
		return -EINVAL;
	if (hw.dart_ex[inst])
		return -EBUSY;
	writel(val, hw.dart_ro[inst] + off);
	return 0;
}

static int quiesce_fail(unsigned int inst, int rc)
{
	pr_err("quiesce dart%u err=%d\n", inst, rc);
	return J713_DM_ERR_QUIESCE;
}

/* After detach and before the overlay comes off. TCR 0, then TTBR 0,
 * then the SID0 TLB flush, then the busy-bit poll readback already uses.
 * Stop on the first instance. Any failure is J713_DM_ERR_QUIESCE so the
 * retain path can leave the registers up for inspection.
 */
static int op_quiesce(void *ctx, struct j713_dm *life)
{
	unsigned int inst;

	(void)ctx;
	(void)life;
	for (inst = 0; inst < J713_ISP_DART_INSTANCES; inst++) {
		u32 tcr = 0, ttbr = 0, tlb = 0, errw = 0;
		int rc;

		rc = dart_ro_write(inst, J713_ISP_DART_TCR(J713_ISP_DART_SID0), 0);
		if (rc)
			return quiesce_fail(inst, rc);
		rc = dart_ro_write(inst, J713_ISP_DART_TTBR(J713_ISP_DART_SID0), 0);
		if (rc)
			return quiesce_fail(inst, rc);
		rc = dart_ro_write(inst, J713_DM_TLB_CMD, J713_DM_TLB_FLUSH_SID0);
		if (rc)
			return quiesce_fail(inst, rc);
		rc = readl_poll_timeout(hw.dart_ro[inst] + J713_DM_TLB_CMD, tlb,
					!(tlb & J713_DM_TLB_BUSY),
					J713_DM_TLB_SLEEP_US, J713_DM_TLB_TIMEOUT_US);
		if (rc)
			return quiesce_fail(inst, rc);
		rc = dart_ro_read(inst, J713_ISP_DART_SID0_TCR, &tcr);
		if (rc)
			return quiesce_fail(inst, rc);
		rc = dart_ro_read(inst, J713_ISP_DART_SID0_TTBR, &ttbr);
		if (rc)
			return quiesce_fail(inst, rc);
		rc = dart_ro_read(inst, J713_DM_DART_ERROR, &errw);
		if (rc)
			return quiesce_fail(inst, rc);
		if (tcr != 0 || (ttbr & J713_DM_TTBR_VALID) || errw)
			return quiesce_fail(inst, -EIO);
		pr_info("quiesce dart%u sid0 tcr=0x%x ttbr=0x%x error=0x%x\n",
			inst, tcr, ttbr, errw);
	}
	return 0;
}

/* Exclusive window only, before the overlay. Values are recorded, not judged. */
static int op_survey(void *ctx, struct j713_dm *life)
{
	unsigned int inst, sid;

	(void)ctx;
	(void)life;
	memset(survey_tcr, 0, sizeof(survey_tcr));
	memset(survey_ttbr, 0, sizeof(survey_ttbr));
	for (inst = 0; inst < J713_ISP_DART_INSTANCES; inst++) {
		u32 params1 = 0, params3 = 0, params4 = 0, protect = 0, error = 0;
		int rc;

		if (!hw.dart_ex[inst] || hw.dart_ro[inst])
			return -EINVAL;
		rc = dart_ro_read(inst, J713_ISP_DART_PARAMS1, &params1);
		if (rc)
			return rc;
		rc = dart_ro_read(inst, J713_ISP_DART_PARAMS3, &params3);
		if (rc)
			return rc;
		rc = dart_ro_read(inst, J713_ISP_DART_PARAMS4, &params4);
		if (rc)
			return rc;
		rc = dart_ro_read(inst, J713_ISP_DART_PROTECT, &protect);
		if (rc)
			return rc;
		rc = dart_ro_read(inst, J713_DM_DART_ERROR, &error);
		if (rc)
			return rc;
		pr_info("survey dart%u params1=0x%x params3=0x%x params4=0x%x protect=0x%x error=0x%x\n",
			inst, params1, params3, params4, protect, error);
		for (sid = 0; sid < J713_DM_SID_COUNT; sid++) {
			u32 tcr = 0, ttbr = 0;
			unsigned int idx = J713_DM_SURVEY_INDEX(inst, sid);

			rc = dart_ro_read(inst, J713_ISP_DART_TCR(sid), &tcr);
			if (rc)
				return rc;
			rc = dart_ro_read(inst, J713_ISP_DART_TTBR(sid), &ttbr);
			if (rc)
				return rc;
			survey_tcr[idx] = tcr;
			survey_ttbr[idx] = ttbr;
			pr_info("survey dart%u sid%u tcr=0x%x ttbr=0x%x\n",
				inst, sid, tcr, ttbr);
		}
	}
	return 0;
}

/* Table pages come from io-pgtable's allocator, so they are in the
 * kernel linear map. Read one iopte. Never write a table or a PTE.
 * The walk calls this for the TTBR table and for every next-level
 * table. phys_to_virt runs only after pfn_valid accepts the address.
 */
static int pte_read_linear(void *ctx, u64 table_phys, unsigned int index, u64 *pte)
{
	u64 *table;

	(void)ctx;
	if (!pte || index >= (1u << J713_DM_PTE_BITS_PER_LEVEL))
		return -EINVAL;
	if (!table_phys || (table_phys & (J713_DM_PAGE_SIZE - 1ull)))
		return -EINVAL;
	if (!pfn_valid(PHYS_PFN(table_phys)))
		return J713_DM_ERR_PTE;
	table = phys_to_virt((phys_addr_t)table_phys);
	*pte = READ_ONCE(table[index]);
	return 0;
}

static int record_leaf(u64 ttbr_phys, u64 iova, phys_addr_t phys, int no_write,
		       unsigned int *bits)
{
	u64 leaf = 0;
	int rc;

	rc = j713_dm_pte_check_sample(ttbr_phys, iova, phys, no_write,
				      pte_read_linear, NULL, &leaf);
	*bits = j713_dm_pte_low_bits(leaf);
	pte_walk_count++;
	pr_info("pte iova=0x%llx phys=0x%llx leaf=0x%llx low=0x%x rc=%d\n",
		(unsigned long long)iova, (unsigned long long)phys,
		(unsigned long long)leaf, *bits, rc);
	return rc;
}

static int walk_span(u64 ttbr_phys, u64 iova, phys_addr_t phys, unsigned int pages,
		     int no_write, unsigned int bits[J713_DM_PTE_SAMPLES])
{
	unsigned int s;

	for (s = 0; s < J713_DM_PTE_SAMPLES; s++) {
		unsigned int page = j713_dm_sample_index(pages, s);
		int rc = record_leaf(ttbr_phys,
				     iova + (u64)page * J713_DM_PAGE_SIZE,
				     phys + (phys_addr_t)page * J713_DM_PAGE_SIZE,
				     no_write, &bits[s]);

		if (rc)
			return rc;
	}
	return 0;
}

static int walk_slots(u64 ttbr_phys, struct j713_dm_slot *slots, unsigned int pages,
		      int no_write, unsigned int bits[J713_DM_PTE_SAMPLES])
{
	unsigned int s;

	for (s = 0; s < J713_DM_PTE_SAMPLES; s++) {
		unsigned int page = j713_dm_sample_index(pages, s);
		int rc;

		if (page >= pages || !slots[page].live)
			return J713_DM_ERR_PTE;
		rc = record_leaf(ttbr_phys, slots[page].iova, slots[page].phys,
				 no_write, &bits[s]);
		if (rc)
			return rc;
	}
	return 0;
}

static void log_pte_walk(void)
{
	pr_info("pte_walk=%u text=0x%x 0x%x 0x%x data=0x%x 0x%x 0x%x heap=0x%x 0x%x 0x%x ipc=0x%x 0x%x 0x%x\n",
		pte_walk_count,
		text_pte_bits[0], text_pte_bits[1], text_pte_bits[2],
		data_pte_bits[0], data_pte_bits[1], data_pte_bits[2],
		heap_pte_bits[0], heap_pte_bits[1], heap_pte_bits[2],
		ipc_pte_bits[0], ipc_pte_bits[1], ipc_pte_bits[2]);
}

/* SID0 TTBR was already read on every instance. The three values must
 * match. Decode instance 0 the way the T8110 driver writes TTBR, then
 * check the first, middle, and last page of each region. A mismatch
 * fails the run; teardown still unmaps.
 */
static int walk_sid0_ptes(u32 ttbr)
{
	u64 table_phys = 0;
	unsigned int s;
	int rc;

	for (s = 0; s < J713_DM_PTE_SAMPLES; s++) {
		text_pte_bits[s] = 0;
		data_pte_bits[s] = 0;
		heap_pte_bits[s] = 0;
		ipc_pte_bits[s] = 0;
	}
	pte_walk_count = 0;
	rc = j713_dm_ttbr_to_phys(ttbr, &table_phys);
	if (rc) {
		log_pte_walk();
		return J713_DM_ERR_PTE;
	}
	rc = walk_span(table_phys, J713_DM_TEXT_IOVA, J713_DM_TEXT_PHYS,
		       J713_DM_TEXT_PAGES, 1, text_pte_bits);
	if (!rc)
		rc = walk_span(table_phys, J713_DM_DATA_IOVA, J713_DM_DATA_PHYS,
			       J713_DM_DATA_PAGES, 0, data_pte_bits);
	if (!rc)
		rc = walk_slots(table_phys, heap_slots, J713_DM_HEAP_PAGES, 0,
				heap_pte_bits);
	if (!rc)
		rc = walk_slots(table_phys, ipc_slots, J713_DM_IPC_PAGES, 0,
				ipc_pte_bits);
	if (!rc && pte_walk_count != J713_DM_PTE_SAMPLES * 4u)
		rc = J713_DM_ERR_PTE;
	log_pte_walk();
	return rc;
}

static int op_readback(void *ctx, struct j713_dm *life)
{
	unsigned int inst;
	u32 sid0_ttbr[J713_ISP_DART_INSTANCES];

	(void)ctx;
	life->readback_count = 0;
	for (inst = 0; inst < J713_ISP_DART_INSTANCES; inst++)
		sid0_ttbr[inst] = 0;
	for (inst = 0; inst < J713_ISP_DART_INSTANCES; inst++) {
		u32 tcr = 0, ttbr = 0, tlb = 0, errw = 0;
		int rc;

		rc = dart_ro_read(inst, J713_ISP_DART_SID0_TCR, &tcr);
		if (rc)
			return rc;
		rc = dart_ro_read(inst, J713_ISP_DART_SID0_TTBR, &ttbr);
		if (rc)
			return rc;
		rc = dart_ro_read(inst, J713_DM_TLB_CMD, &tlb);
		if (rc)
			return rc;
		rc = dart_ro_read(inst, J713_DM_DART_ERROR, &errw);
		if (rc)
			return rc;
		pr_info("readback dart%u sid0 tcr=0x%x ttbr=0x%x tlb=0x%x error=0x%x\n",
			inst, tcr, ttbr, tlb, errw);
		/* Any nonzero ERROR word, the same rule as the premise, before the TLB poll. */
		if (errw) {
			pr_err("readback dart%u error 0x%x\n", inst, errw);
			return -EIO;
		}
		if (!j713_dm_tcr_is_4level(tcr) || !(ttbr & J713_DM_TTBR_VALID))
			return -EPROTO;
		rc = readl_poll_timeout(hw.dart_ro[inst] + J713_DM_TLB_CMD, tlb,
					!(tlb & J713_DM_TLB_BUSY),
					J713_DM_TLB_SLEEP_US, J713_DM_TLB_TIMEOUT_US);
		pr_info("readback dart%u tlb_poll=0x%x err=%d\n", inst, tlb, rc);
		if (rc)
			return rc;
		rc = dart_ro_read(inst, J713_DM_DART_ERROR, &errw);
		if (rc)
			return rc;
		if (errw & J713_DM_DART_ERROR_FLAG) {
			pr_err("readback dart%u error flag 0x%x\n", inst, errw);
			return -EIO;
		}
		sid0_ttbr[inst] = ttbr;
		life->readback_count += 2;
	}
	if (life->readback_count != J713_ISP_DART_INSTANCES * 2u)
		return -EIO;
	for (inst = 1; inst < J713_ISP_DART_INSTANCES; inst++) {
		if (sid0_ttbr[inst] != sid0_ttbr[0]) {
			pr_err("sid0 ttbr mismatch 0x%x 0x%x 0x%x\n",
			       sid0_ttbr[0], sid0_ttbr[1], sid0_ttbr[2]);
			return J713_DM_ERR_PTE;
		}
	}
	return walk_sid0_ptes(sid0_ttbr[0]);
}

static int unmap_range(struct j713_dm *life, u64 iova, size_t size, bool *live,
		       int *bad)
{
	size_t n;
	unsigned int page;
	unsigned int pages;

	if (!*live)
		return 0;
	n = iommu_unmap(domain, (unsigned long)iova, size);
	pages = size / PAGE_SIZE;
	for (page = 0; page < pages; page++) {
		u64 at = iova + (u64)page * PAGE_SIZE;
		phys_addr_t got = iommu_iova_to_phys(domain, (dma_addr_t)at);

		if (got) {
			pr_info("unmap iova=0x%llx still 0x%llx\n",
				(unsigned long long)at, (unsigned long long)got);
			life->map_count = live_maps();
			return J713_DM_ERR_OWNED;
		}
	}
	pr_info("unmap iova=0x%llx bytes=%zu size=0x%zx\n",
		(unsigned long long)iova, n, size);
	*live = false;
	if (n != size)
		*bad = 1;
	life->map_count = live_maps();
	return 0;
}

static int op_unmap(void *ctx, struct j713_dm *life)
{
	unsigned int i;
	int bad = 0;
	int ret;

	(void)ctx;
	if (!domain)
		return -EINVAL;
	ret = unmap_range(life, text_span.iova, text_span.size, &text_span.live,
			  &bad);
	if (ret)
		return ret;
	ret = unmap_range(life, data_span.iova, data_span.size, &data_span.live,
			  &bad);
	if (ret)
		return ret;
	for (i = 0; i < J713_DM_HEAP_PAGES; i++) {
		ret = unmap_range(life, heap_slots[i].iova, PAGE_SIZE,
				  &heap_slots[i].live, &bad);
		if (ret)
			return ret;
	}
	for (i = 0; i < J713_DM_IPC_PAGES; i++) {
		ret = unmap_range(life, ipc_slots[i].iova, PAGE_SIZE,
				  &ipc_slots[i].live, &bad);
		if (ret)
			return ret;
	}
	life->map_count = live_maps();
	if (life->map_count)
		return -EIO;
	return bad ? -EIO : 0;
}

static int op_detach(void *ctx, struct j713_dm *life)
{
	struct iommu_domain *got;

	(void)ctx;
	(void)life;
	if (!domain || !master_dev || !master_is_bound)
		return -ENODEV;
	iommu_detach_device(domain, master_dev);
	got = iommu_get_domain_for_dev(master_dev);
	if (got == domain)
		return -EBUSY;
	return 0;
}

static int op_free_domain(void *ctx, struct j713_dm *life)
{
	(void)ctx;
	if (life->flags & J713_DM_F_REFS)
		return -EBUSY;
	if (!domain)
		return 0;
	iommu_domain_free(domain);
	domain = NULL;
	return 0;
}

static int op_free_pages(void *ctx, struct j713_dm *life)
{
	(void)ctx;
	if (life->flags & (J713_DM_F_REFS | J713_DM_F_DOMAIN | J713_DM_F_MAPPED |
			   J713_DM_F_ATTACHED))
		return -EBUSY;
	/* TEXT and DATA physical addresses are iBoot-preloaded. They are
	 * never stored in heap_pages or ipc_pages. Refuse rather than free
	 * one if it ever shows up here.
	 */
	if (!alloc_pages_are_safe())
		return -EINVAL;
	free_alloc_pages();
	return 0;
}

static int page_still_zero(struct page *page)
{
	void *addr;

	if (!page)
		return -EINVAL;
	addr = page_address(page);
	if (!addr)
		return -EINVAL;
	if (memchr_inv(addr, 0, PAGE_SIZE))
		return J713_DM_ERR_LADDER;
	return 0;
}

static int op_check_surfaces(void *ctx, struct j713_dm *life)
{
	unsigned int i;
	int rc;

	(void)ctx;
	(void)life;
	for (i = 0; i < J713_DM_HEAP_PAGES; i++) {
		rc = page_still_zero(heap_pages[i]);
		if (rc)
			return rc;
	}
	for (i = 0; i < J713_DM_IPC_PAGES; i++) {
		rc = page_still_zero(ipc_pages[i]);
		if (rc)
			return rc;
	}
	return 0;
}

static int op_premise(void *ctx, struct j713_dm *life)
{
	static const int fabric_ids[4] = {
		CLAIM_FABRIC0, CLAIM_FABRIC1, CLAIM_FABRIC2, CLAIM_FABRIC3
	};
	u32 fabric[4];
	u32 error[3];
	unsigned int i;
	u32 word;
	int rc;

	(void)ctx;
	(void)life;
	/* Direct readl on the claim maps. hw_read32 stays the identity-probe
	 * allow-list and still rejects GPIO. Nothing here is a write.
	 */
	if (!claims[CLAIM_GPIO1].map)
		return -EINVAL;
	gpio1_val = readl(claims[CLAIM_GPIO1].map);
	for (i = 0; i < 4u; i++) {
		if (!claims[fabric_ids[i]].map)
			return -EINVAL;
		fabric_val[i] = readl(claims[fabric_ids[i]].map);
		fabric[i] = fabric_val[i];
	}
	for (i = 0; i < J713_ISP_DART_INSTANCES; i++) {
		rc = dart_ro_read(i, J713_DM_DART_SID15_TCR, &word);
		if (rc)
			return rc;
		sid15_tcr[i] = word;
		rc = dart_ro_read(i, J713_ISP_DART_PROTECT, &word);
		if (rc)
			return rc;
		dart_protect[i] = word;
		rc = dart_ro_read(i, J713_DM_DART_ERROR, &word);
		if (rc)
			return rc;
		dart_error[i] = word;
		error[i] = word;
	}
	pr_info("premise gpio1=0x%x fabric=%x %x %x %x sid15_tcr=%x %x %x protect=%x %x %x error=%x %x %x\n",
		gpio1_val,
		fabric[0], fabric[1], fabric[2], fabric[3],
		sid15_tcr[0], sid15_tcr[1], sid15_tcr[2],
		dart_protect[0], dart_protect[1], dart_protect[2],
		dart_error[0], dart_error[1], dart_error[2]);
	return j713_dm_premise_rc(fabric, error);
}

static int op_unmap_ro(void *ctx, struct j713_dm *life)
{
	(void)ctx;
	(void)life;
	release_ro();
	return 0;
}

static int op_unbind(void *ctx, struct j713_dm *life)
{
	int ret;

	(void)ctx;
	(void)life;
	ret = destroy_master_now();
	if (ret)
		return ret;
	if (master_still_present() || master_dev || master_is_bound)
		return -EBUSY;
	return 0;
}

static int op_remove_overlay(void *ctx, struct j713_dm *life)
{
	unsigned int i;
	int ret;
	int saved;

	(void)ctx;
	(void)life;
	/* Master first even when the bound check never set F_MASTER. */
	ret = destroy_master_now();
	if (ret)
		return ret;
	if (master_still_present() || master_dev || master_is_bound)
		return -EBUSY;
	for (i = 0; i < J713_ISP_DART_INSTANCES; i++) {
		ret = destroy_dart_at(dart_paths[i]);
		if (ret)
			return ret;
		if (dart_still_present(dart_paths[i]))
			return -EBUSY;
	}
	if (ovcs_id <= 0) {
		if (owned_node_exists() || master_dev)
			return -EBUSY;
		return 0;
	}
	saved = ovcs_id;
	ret = of_overlay_remove(&ovcs_id);
	pr_info("overlay remove ret=%d id=%d was=%d\n", ret, ovcs_id, saved);
	/*
	 * A negative return can mean the changeset is still there (id > 0)
	 * or that overlay memory is already gone (id == 0). Either way,
	 * leftover nodes keep the retain. A zero id and no owned node is
	 * checked again by cleanup_only; do not power off on this error.
	 */
	if (ret)
		return ret;
	if (ovcs_id != 0 || owned_node_exists() || master_dev)
		return -EBUSY;
	return 0;
}

static int op_unregister(void *ctx, struct j713_dm *life)
{
	unsigned int i;

	(void)ctx;
	(void)life;
	if (master_still_present() || master_dev || master_is_bound)
		return -EBUSY;
	for (i = 0; i < J713_ISP_DART_INSTANCES; i++) {
		if (dart_still_present(dart_paths[i]))
			return -EBUSY;
	}
	if (!driver_registered)
		return 0;
	platform_driver_unregister(&j713_dm_pdrv);
	driver_registered = false;
	return 0;
}

static int op_power_down(void *ctx, struct j713_dm *life)
{
	int rc;

	(void)ctx;
	if (life->power_restored || isp.power_restored) {
		life->power_restored = 1;
		return 0;
	}
	if (!hw.pmgr)
		return -EINVAL;
	/* Restore writes PMGR local requests only. Do not touch COPROC or DART. */
	rc = j713_isp_restore_and_verify(&hw_ops, &hw, &isp);
	if (rc || !isp.power_restored) {
		life->power_restored = 0;
		return rc ? rc : -EIO;
	}
	life->power_restored = 1;
	return 0;
}

static int mappings_remain(void)
{
	unsigned int i;

	if (hw.pmgr || hw.ver || hw.coproc)
		return 1;
	for (i = 0; i < CLAIM_COUNT; i++) {
		if (claims[i].res || claims[i].map)
			return 1;
	}
	for (i = 0; i < J713_ISP_DART_INSTANCES; i++) {
		if (hw.dart_ex[i] || hw.dart_ro[i])
			return 1;
	}
	return 0;
}

static int op_release_claims(void *ctx, struct j713_dm *life)
{
	(void)ctx;
	(void)life;
	release_all_mappings();
	if (mappings_remain())
		return -EBUSY;
	return 0;
}

static const struct j713_dm_ops j713_dm_ops = {
	.cpu_stopped = op_cpu,
	.dart_baseline = op_baseline,
	.release_dart_claims = op_release_dart,
	.register_driver = op_register,
	.apply_overlay = op_apply,
	.master_bound = op_master_bound,
	.remap_dart_ro = op_remap_ro,
	.alloc_pages = op_alloc_pages,
	.alloc_domain = op_alloc_domain,
	.premise = op_premise,
	.attach = op_attach,
	.map_iova = op_map,
	.lookup = op_lookup,
	.readback = op_readback,
	.unmap = op_unmap,
	.detach = op_detach,
	.dart_quiesce = op_quiesce,
	.free_domain = op_free_domain,
	.check_surfaces = op_check_surfaces,
	.free_pages = op_free_pages,
	.unmap_dart_ro = op_unmap_ro,
	.unbind_master = op_unbind,
	.remove_overlay = op_remove_overlay,
	.unregister_driver = op_unregister,
	.power_down = op_power_down,
	.release_claims = op_release_claims,
	.dart_survey = op_survey,
};

/* Device, domain, page, or overlay state. Power and PMGR claims are separate:
 * a restored gate set still holds those flags until release_all drops them.
 */
static int runtime_left(void)
{
	unsigned int i;

	if (dm.flags & J713_DM_F_BLOCKS_POWER)
		return 1;
	if (driver_registered || ovcs_id > 0 || master_dev || master_is_bound)
		return 1;
	if (npages || domain)
		return 1;
	if (text_span.live || data_span.live)
		return 1;
	for (i = 0; i < J713_DM_HEAP_PAGES; i++) {
		if (heap_pages[i] || heap_slots[i].live)
			return 1;
	}
	for (i = 0; i < J713_DM_IPC_PAGES; i++) {
		if (ipc_pages[i] || ipc_slots[i].live)
			return 1;
	}
	return 0;
}

static int map_callback(void *ctx, struct j713_isp_state *st)
{
	int rc;

	if (dm.retain || dm.cleanup_state == J713_DM_CLEAN_PENDING)
		return -EBUSY;
	dm.flags = J713_DM_F_POWER | J713_DM_F_OTHER_CLAIM | J713_DM_F_DART_CLAIM;
	dm.retain = 0;
	dm.cleanup_state = J713_DM_CLEAN_IDLE;
	dm.power_restored = 0;
	rc = j713_dm_run_powered(&dm, &j713_dm_ops, ctx);
	if (dm.retain || dm.cleanup_state == J713_DM_CLEAN_PENDING ||
	    (dm.flags & J713_DM_F_BLOCKS_POWER)) {
		st->retain_power = 1;
		return rc ? rc : -EBUSY;
	}
	st->retain_power = 0;
	return rc;
}

static int survey_callback(void *ctx, struct j713_isp_state *st)
{
	int rc;

	if (dm.retain || dm.cleanup_state == J713_DM_CLEAN_PENDING ||
	    (dm.flags & J713_DM_F_BLOCKS_POWER)) {
		st->retain_power = 1;
		return -EBUSY;
	}
	dm.flags = J713_DM_F_POWER | J713_DM_F_OTHER_CLAIM | J713_DM_F_DART_CLAIM;
	dm.retain = 0;
	dm.cleanup_state = J713_DM_CLEAN_IDLE;
	dm.power_restored = 0;
	rc = j713_dm_run_survey(&dm, &j713_dm_ops, ctx);
	/* Survey never holds a domain. A read error is still published,
	 * and the outer sequence restores the 13 gates.
	 */
	st->retain_power = 0;
	dm.retain = 0;
	return rc;
}

static int finish_after_probe(int run_ret)
{
	if (isp.retain_power || dm.retain || runtime_left() ||
	    dm.cleanup_state == J713_DM_CLEAN_PENDING) {
		pin_module();
		publish();
		pr_err("retained; cleanup_only can recover\n");
		return 0;
	}

	if (isp.power_restored || !map_check || !isp.attempted) {
		release_all_mappings();
		dm.flags &= ~(J713_DM_F_POWER | J713_DM_F_OTHER_CLAIM |
			      J713_DM_F_DART_CLAIM);
		dm.power_restored = isp.power_restored;
		dm.retain = 0;
		if (runtime_left() || mappings_remain()) {
			pin_module();
			publish();
			pr_err("unwind left resources; pinned\n");
			return 0;
		}
		dm.cleanup_state = (map_check && isp.power_restored) ?
				   J713_DM_CLEAN_COMPLETE : J713_DM_CLEAN_IDLE;
		publish();
		if (dm.first_error)
			return dm.first_error;
		return run_ret ? run_ret : isp.result;
	}

	/*
	 * Enable, version, restore, or verify failed before a live DART
	 * provider existed, or the provider was already removed and the
	 * outer restore still failed. Keep PMGR. Do not read COPROC or DART.
	 */
	release_non_pmgr();
	dm.flags &= ~J713_DM_F_DART_CLAIM;
	dm.flags |= J713_DM_F_POWER | J713_DM_F_OTHER_CLAIM;
	dm.power_restored = 0;
	dm.retain = 1;
	dm.cleanup_state = J713_DM_CLEAN_PENDING;
	if (!dm.cleanup_error)
		dm.cleanup_error = isp.cleanup ? isp.cleanup :
				   (isp.verify ? isp.verify : -EIO);
	pin_module();
	publish();
	pr_err("PMGR restore incomplete; claims retained for cleanup_only\n");
	return 0;
}

static int claims_left(void)
{
	return (dm.flags & (J713_DM_F_POWER | J713_DM_F_OTHER_CLAIM |
			    J713_DM_F_DART_CLAIM)) || mappings_remain();
}

static int j713_dm_cleanup_locked(void)
{
	int rc;

	if (!runtime_left() && !claims_left() && !dm.retain &&
	    dm.cleanup_state != J713_DM_CLEAN_PENDING && !pinned)
		return 0;
	rc = j713_dm_recover(&dm, &j713_dm_ops, &hw);
	if (rc || runtime_left() || claims_left() || dm.retain ||
	    dm.cleanup_state == J713_DM_CLEAN_PENDING) {
		pin_module();
		return rc ? rc : -EBUSY;
	}
	/* Life cleared the device flags. The callback's retain bit is stale. */
	isp.retain_power = 0;
	isp.power_restored = dm.power_restored;
	unpin_module();
	return 0;
}

static int cleanup_only_set(const char *val, const struct kernel_param *kp)
{
	int v;
	int ret;

	(void)kp;
	if (!capable(CAP_SYS_RAWIO))
		return -EPERM;
	ret = kstrtoint(val, 0, &v);
	if (ret)
		return ret;
	if (v != 1)
		return -EINVAL;
	mutex_lock(&j713_dm_lock);
	ret = j713_dm_cleanup_locked();
	publish();
	mutex_unlock(&j713_dm_lock);
	return ret;
}

static int cleanup_only_get(char *buf, const struct kernel_param *kp)
{
	(void)kp;
	return scnprintf(buf, PAGE_SIZE, "%d\n", cleanup_pending);
}

static const struct kernel_param_ops cleanup_only_ops = {
	.set = cleanup_only_set,
	.get = cleanup_only_get,
};
module_param_cb(cleanup_only, &cleanup_only_ops, NULL, 0600);
MODULE_PARM_DESC(cleanup_only, "Root write 1 to recover retained resources; does not map again");

static int __init j713_dm_init(void)
{
	int ret;

	mutex_lock(&j713_dm_lock);
	result = -EAGAIN;
	pr_info("init map_check=%d dart_survey=%d\n", map_check, dart_survey);
	ret = guard_board();
	if (ret)
		goto fail;
	ret = guard_pmgr_dt();
	if (ret)
		goto fail;
	ret = guard_aic();
	if (ret)
		goto fail;
	ret = guard_soc();
	if (ret)
		goto fail;
	ret = guard_no_isp();
	if (ret)
		goto fail;
	if (map_check) {
		ret = guard_layout();
		if (ret)
			goto fail;
	}
	if (dart_survey && !map_check) {
		ret = -EINVAL;
		pr_err("dart_survey requires map_check\n");
		goto fail;
	}

	hw.pmgr = ioremap(J713_ISP_PMGR_PHYS, J713_ISP_PMGR_SIZE);
	if (!hw.pmgr) {
		ret = -ENOMEM;
		goto fail;
	}
	if (map_check) {
		ret = claim_windows();
		if (ret)
			goto unmap;
	}

	{
		j713_isp_inspect_fn cb = NULL;

		if (map_check)
			cb = dart_survey ? survey_callback : map_callback;
		ret = j713_isp_probe_run(&hw_ops, &hw, map_check, cb, &isp);
	}
	ret = finish_after_probe(ret);
	mutex_unlock(&j713_dm_lock);
	return ret;

unmap:
	iounmap(hw.pmgr);
	hw.pmgr = NULL;
fail:
	result = ret;
	stage = J713_ISP_STAGE_GUARDS;
	fail_stage = J713_ISP_STAGE_GUARDS;
	pr_err("guard or map failed %d; no power write\n", ret);
	mutex_unlock(&j713_dm_lock);
	return ret;
}

static void __exit j713_dm_exit(void)
{
	if (pinned || runtime_left() || claims_left() || isp.retain_power)
		pr_err("exit while resources remain flags=0x%x pinned=%d\n",
		       dm.flags, pinned);
}

module_init(j713_dm_init);
module_exit(j713_dm_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Private J713 ISP firmware-view map diagnostic");
