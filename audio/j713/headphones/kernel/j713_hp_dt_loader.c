// SPDX-License-Identifier: GPL-2.0
/*
 * J713 headphone jack: device tree overlay, applied once per boot.
 * Adds the CS42L84 on i2c1, the AUDIO_MCA2_M power domain and the
 * omarchy,j713-headphones node (j713-headphones.dtso, wrapped by kbuild).
 * The placeholder phandles in the blob are replaced with live ones looked
 * up by path.
 *
 * Load it after m4-j713-audio-register.service: the speaker overlays create
 * the power domains and the ADMAC node it points at.
 */
#include <linux/module.h>
#include <linux/of.h>
#include <linux/slab.h>

extern const u8 __dtbo_j713_headphones_begin[];
extern const u8 __dtbo_j713_headphones_end[];

#define HP_PLACEHOLDER_MASK	0xffffff00
#define HP_PLACEHOLDER		0xfeed0000

static const struct {
	u32 placeholder;
	const char *path;
} hp_nodes[] = {
	{ 0xfeed0001, "/soc/power-management@388284000/power-controller@b8" },
	{ 0xfeed0002, "/soc/power-management@388284000/power-controller@e8" },
	{ 0xfeed0003, "/soc/dma-controller@393800000" },
	{ 0xfeed0005, "/soc/pinctrl@3881f0000" },
	{ 0xfeed0006, "/soc/pinctrl@39a000000" },
};

static int __init hp_dt_init(void)
{
	size_t size = __dtbo_j713_headphones_end - __dtbo_j713_headphones_begin;
	struct device_node *np;
	__be32 *cell;
	u8 *blob;
	size_t i, j;
	int overlay_id = -1, ret;

	if (!of_machine_is_compatible("apple,j713"))
		return -ENODEV;
	np = of_find_node_by_path("/soc/i2c@3ad014000");
	ret = !np ? -ENODEV : of_device_is_available(np) ? -EBUSY : 0;
	of_node_put(np);
	if (ret == -ENODEV)
		pr_err("j713_hp_dt: refusing: no /soc/i2c@3ad014000 node\n");
	if (ret == -EBUSY)
		pr_err("j713_hp_dt: refusing: i2c1 already enabled (test overlay applied this boot?)\n");
	if (ret)
		return ret;

	blob = kmemdup(__dtbo_j713_headphones_begin, size, GFP_KERNEL);
	if (!blob)
		return -ENOMEM;
	for (i = 0; i < ARRAY_SIZE(hp_nodes); i++) {
		phandle ph;

		np = of_find_node_by_path(hp_nodes[i].path);
		ph = np ? np->phandle : 0;
		of_node_put(np);
		if (!ph) {
			pr_err("j713_hp_dt: refusing: speaker topology missing (no phandle for %s)\n",
			       hp_nodes[i].path);
			kfree(blob);
			return -ENODEV;
		}
		for (j = 0; j + 4 <= size; j += 4) {
			cell = (__be32 *)(blob + j);
			if (be32_to_cpu(*cell) == hp_nodes[i].placeholder)
				*cell = cpu_to_be32(ph);
		}
	}
	for (j = 0; j + 4 <= size; j += 4) {
		u32 v = be32_to_cpu(*(__be32 *)(blob + j));

		if ((v & HP_PLACEHOLDER_MASK) == HP_PLACEHOLDER) {
			pr_err("j713_hp_dt: refusing: unresolved placeholder %#x at %zu\n",
			       v, j);
			kfree(blob);
			return -EINVAL;
		}
	}

	/*
	 * of_overlay_fdt_apply() keeps its own copy of the blob. A partial
	 * result is never removed either (same rule as the speaker loaders).
	 */
	ret = of_overlay_fdt_apply(blob, size, &overlay_id, NULL);
	kfree(blob);
	pr_info("j713_hp_dt: overlay apply result=%d id=%d (kept until power-off)\n",
		ret, overlay_id);
	return ret;
}

/*
 * No module_exit: the overlay adds a PMGR power domain, and apple-pmgr
 * cannot unregister one, so removing the overlay corrupts the genpd list.
 */
module_init(hp_dt_init);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("J713 headphone jack overlay (codec, MCA2 domain, card node)");
