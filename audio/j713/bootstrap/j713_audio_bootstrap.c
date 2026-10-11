// SPDX-License-Identifier: GPL-2.0-only
/* Retained exact-board cold-boot transport metadata and domain population. */
#include <linux/capability.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/of_address.h>
#include <linux/of_platform.h>
#include <linux/platform_device.h>
#include "j713_bootstrap_blob.h"

static bool apply;
static int result = -EAGAIN, overlay_id, stage;
module_param(apply, bool, 0400);
module_param(result, int, 0444);
module_param(overlay_id, int, 0444);
module_param(stage, int, 0444);

#define PMGR "/soc/power-management@388284000"
#define MCA "/soc/i2s@393600000"
#define DMA "/soc/dma-controller@393800000"
#define DART "/soc/iommu@390f00000"
#define BUS "/soc/i2c@3ad01c000"

static bool bytes(struct device_node *np, const char *name,
		  const void *expected, size_t size)
{
	int len;
	const void *actual = of_get_property(np, name, &len);
	return actual && len >= 0 && (size_t)len == size && !memcmp(actual, expected, size);
}
#define CELLS(np, name, ...) bytes(np, name, (const __be32[]){ __VA_ARGS__ }, sizeof((const __be32[]){ __VA_ARGS__ }))
#define CELL(v) cpu_to_be32(v)

static bool resource(struct device_node *np, resource_size_t base, resource_size_t size)
{
	struct resource r;
	return !of_address_to_resource(np, 0, &r) && r.start == base && resource_size(&r) == size;
}

static bool no_device(struct device_node *np)
{
	struct platform_device *p = of_find_device_by_node(np);
	if (p) put_device(&p->dev);
	return !p;
}

static int validate_target(void)
{
	static const char *const absent[] = {PMGR, DMA, MCA, "/sound", "/speaker-group"};
	struct device_node *np;
	unsigned int i;
	bool valid;
	if (!capable(CAP_SYS_RAWIO) || !of_machine_is_compatible("apple,j713") ||
	    !of_machine_is_compatible("apple,t8132")) return -EPERM;
	for (i = 0; i < ARRAY_SIZE(absent); i++) {
		np = of_find_node_by_path(absent[i]);
		if (np) { of_node_put(np); return -EEXIST; }
	}
	np = of_find_node_by_path("/soc/aop@390c00000");
	if (!np) return -ENODEV;
	valid = bytes(np, "status", "disabled", 9);
	of_node_put(np);
	if (!valid) return -EINVAL;
	np = of_find_node_by_path("/clock-ref");
	if (!np) return -ENODEV;
	valid = np->phandle == 0x2a && of_device_is_compatible(np, "fixed-clock") &&
		CELLS(np, "#clock-cells", CELL(0)) && CELLS(np, "clock-frequency", CELL(24000000));
	of_node_put(np);
	if (!valid) return -EINVAL;
	np = of_find_node_by_path(DART);
	if (!np) return -ENODEV;
	valid = np->phandle == 0x35 && of_device_is_compatible(np, "apple,t8110-dart") &&
		bytes(np, "status", "disabled", 9) && resource(np, 0x390f00000ULL, 0x4000) &&
		CELLS(np, "#iommu-cells", CELL(1)) && CELLS(np, "interrupt-parent", CELL(0xd)) &&
		CELLS(np, "interrupts", CELL(0), CELL(457), CELL(4)) && no_device(np);
	of_node_put(np);
	if (!valid) return -EINVAL;
	np = of_find_node_by_path(BUS);
	if (!np) return -ENODEV;
	valid = of_device_is_compatible(np, "apple,t8132-i2c") &&
		bytes(np, "status", "disabled", 9) && resource(np, 0x3ad01c000ULL, 0x4000) &&
		CELLS(np, "clocks", CELL(0x2a)) && CELLS(np, "interrupt-parent", CELL(0xd)) &&
		CELLS(np, "interrupts", CELL(0), CELL(1065), CELL(4)) &&
		CELLS(np, "pinctrl-0", CELL(0x48)) && bytes(np, "pinctrl-names", "default", 8) &&
		CELLS(np, "power-domains", CELL(0x49)) && CELLS(np, "#address-cells", CELL(1)) &&
		CELLS(np, "#size-cells", CELL(0)) && !of_get_child_count(np) && no_device(np);
	of_node_put(np);
	if (!valid) return -EINVAL;
	np = of_find_node_by_path("/soc/pinctrl@39a000000/i2c3-pins");
	if (!np) return -ENODEV;
	valid = np->phandle == 0x48 && CELLS(np, "pinmux", CELL(0x1002a), CELL(0x10029));
	of_node_put(np);
	if (!valid) return -EINVAL;
	np = of_find_node_by_path("/soc/power-management@380700000/power-controller@2a0");
	if (!np) return -ENODEV;
	valid = np->phandle == 0x49 && CELLS(np, "reg", CELL(0x2a0), CELL(4)) &&
		CELLS(np, "#power-domain-cells", CELL(0)) && bytes(np, "label", "i2c3", 5) &&
		!of_find_property(np, "power-domains", NULL);
	of_node_put(np);
	return valid ? 0 : -EINVAL;
}

static bool points_to(struct device_node *np, const char *name, int index,
		      const char *path)
{
	struct device_node *actual = of_parse_phandle(np, name, index);
	struct device_node *expected = of_find_node_by_path(path);
	bool valid = actual && expected && actual == expected;
	of_node_put(expected);
	of_node_put(actual);
	return valid;
}

static int postcheck(void)
{
	struct device_node *mca = of_find_node_by_path(MCA);
	struct device_node *dma = of_find_node_by_path(DMA);
	struct device_node *dart = of_find_node_by_path(DART);
	struct device_node *bus = of_find_node_by_path(BUS);
	struct of_phandle_args arg = {};
	unsigned int i;
	int ret = -EINVAL;
	if (!mca || !dma || !dart || !bus || !of_device_is_available(mca) ||
	    !of_device_is_available(dma) || !of_device_is_available(dart) ||
	    !of_device_is_available(bus) || of_get_child_count(bus) ||
	    !resource(mca, 0x393600000ULL, 0xc000) || !resource(dma, 0x393800000ULL, 0x34000) ||
	    !CELLS(mca, "clocks", CELL(0x2a)) ||
	    !bytes(mca, "dma-names", "tx0a\0rx0a", 10) ||
	    of_count_phandle_with_args(mca, "dmas", "#dma-cells") != 2 ||
	    !points_to(mca, "power-domains", 0, PMGR "/power-controller@b8") ||
	    !points_to(mca, "power-domains", 1, PMGR "/power-controller@e8") ||
	    !points_to(dma, "power-domains", 0, PMGR "/power-controller@d8") ||
	    !points_to(dart, "power-domains", 0, PMGR "/power-controller@20") ||
	    !CELLS(dma, "iommus", CELL(0x35), CELL(8)) ||
	    !CELLS(dart, "apple,dma-range", CELL(0x100), CELL(0x10000000), CELL(0), CELL(0x10000000))) goto out;
	for (i = 0; i < 2; i++) {
		if (of_parse_phandle_with_args(mca, "dmas", "#dma-cells", i, &arg)) goto out;
		if (arg.np != dma || arg.args_count != 1 || arg.args[0] != i) {
			of_node_put(arg.np); goto out;
		}
		of_node_put(arg.np);
	}
	ret = 0;
out:
	of_node_put(bus); of_node_put(dart); of_node_put(dma); of_node_put(mca);
	return ret;
}

static int populate_domains(void)
{
	static const u32 offsets[] = {0x20, 0x90, 0xb8, 0xd8, 0xe8};
	struct device_node *np = of_find_node_by_path(PMGR), *child;
	struct platform_device *parent, *device;
	u32 offset;
	unsigned long seen = 0;
	unsigned int i, count = 0;
	int ret = -EINVAL;
	if (!np) return -ENODEV;
	if (!resource(np, 0x388284000ULL, 0x4000) || !of_device_is_compatible(np, "syscon")) goto out;
	for_each_child_of_node(np, child) {
		if (!of_device_is_compatible(child, "apple,t8132-pmgr-pwrstate") ||
		    of_property_read_u32(child, "reg", &offset)) goto bad_child;
		for (i = 0; i < ARRAY_SIZE(offsets) && offsets[i] != offset; i++) { }
		if (i == ARRAY_SIZE(offsets) || (seen & (1UL << i)) || !no_device(child)) goto bad_child;
		seen |= 1UL << i; count++;
	}
	if (count != ARRAY_SIZE(offsets)) goto out;
	parent = of_find_device_by_node(np);
	if (!parent) { ret = -ENODEV; goto out; }
	ret = of_platform_populate(np, NULL, NULL, &parent->dev);
	put_device(&parent->dev);
	if (ret) goto out;
	for_each_child_of_node(np, child) {
		device = of_find_device_by_node(child);
		if (!device) { ret = -ENODEV; goto bad_child; }
		put_device(&device->dev);
	}
	goto out;
bad_child:
	of_node_put(child);
out:
	of_node_put(np);
	return ret;
}

static int __init j713_bootstrap_init(void)
{
	int ret = validate_target();
	if (ret) return ret;
	if (!apply) { pr_info("j713_audio_bootstrap: fresh-base guards passed; no change\n"); return 0; }
	/* Keep blob, status and any partial result inspectable; never auto-remove. */
	__module_get(THIS_MODULE);
	stage = 1;
	result = of_overlay_fdt_apply(j713_bootstrap_dtbo, sizeof(j713_bootstrap_dtbo), &overlay_id, NULL);
	if (!result) { stage = 2; result = postcheck(); }
	if (!result) { stage = 3; result = populate_domains(); }
	if (!result) stage = 4;
	pr_info("j713_audio_bootstrap: result=%d stage=%d overlay=%d retained\n", result, stage, overlay_id);
	return 0;
}
static void __exit j713_bootstrap_exit(void) { }
module_init(j713_bootstrap_init);
module_exit(j713_bootstrap_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Retained J713 fresh-boot audio transport metadata");
