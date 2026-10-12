// SPDX-License-Identifier: GPL-2.0-only
/* Retained exact-board metadata adapter for native qualification. */
#include <linux/capability.h>
#include <linux/i2c.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/of_address.h>
#include <linux/of_platform.h>
#include <linux/platform_device.h>
#include "j713_audio_blob.h"

static bool apply;
static int result = -EAGAIN, overlay_id;
module_param(apply, bool, 0400);
module_param(result, int, 0444);
module_param(overlay_id, int, 0444);

static bool exact_resource(struct device_node *np, resource_size_t base,
                           resource_size_t size)
{
	struct resource r;
	return !of_address_to_resource(np, 0, &r) &&
	       r.start == base && resource_size(&r) == size;
}

static int any_i2c_client(struct device *dev, void *unused)
{
	return i2c_verify_client(dev) ? -EBUSY : 0;
}

static bool points_to(struct device_node *np, const char *property,
                      unsigned int index, struct device_node *expected)
{
	struct device_node *actual = of_parse_phandle(np, property, index);
	bool same = actual && actual == expected;
	of_node_put(actual);
	return same;
}

static int postcheck(struct device_node *mca, struct device_node *i2c,
                     struct device_node *gpio)
{
	static const unsigned int addresses[] = {0x38, 0x3b, 0x39, 0x3c};
	struct device_node *group = of_find_node_by_path("/speaker-group");
	struct device_node *card = of_find_node_by_path("/sound"), *amp;
	struct of_phandle_args reset = {};
	u32 address;
	unsigned int i;
	int ret = -EINVAL;
	if (!group || !card || !of_device_is_compatible(group, "apple,j713-speaker-group") ||
	    !of_device_is_compatible(card, "apple,j713-audio") ||
	    !points_to(mca, "apple,speaker-group", 0, group) ||
	    !points_to(card, "apple,speaker-group", 0, group) ||
	    of_find_property(card, "apple,mca", NULL) ||
	    of_get_child_count(i2c) != 4 ||
	    of_property_count_u32_elems(card, "apple,codecs") != 4 ||
	    of_parse_phandle_with_args(group, "reset-gpios", "#gpio-cells", 0, &reset)) goto out;
	if (reset.np != gpio || reset.args_count != 2 || reset.args[0] != 15 || reset.args[1]) goto out;
	for (i = 0; i < ARRAY_SIZE(addresses); i++) {
		amp = of_parse_phandle(card, "apple,codecs", i);
		if (!amp) goto out;
		if (amp->parent != i2c || !of_device_is_compatible(amp, "ti,sn012776") ||
		    of_property_read_u32(amp, "reg", &address) || address != addresses[i] ||
		    !points_to(amp, "apple,speaker-group", 0, group)) {
			of_node_put(amp);
			goto out;
		}
		of_node_put(amp);
	}
	ret = 0;
out:
	of_node_put(reset.np);
	of_node_put(card);
	of_node_put(group);
	return ret;
}

static int __init j713_dt_init(void)
{
	struct device_node *mca = NULL, *i2c = NULL, *gpio = NULL, *existing;
	struct platform_device *pdev = NULL;
	struct i2c_adapter *adapter = NULL;
	u32 cells;
	bool locked = false;
	int ret = -EPERM;
	if (!capable(CAP_SYS_RAWIO) || !of_machine_is_compatible("apple,j713") ||
	    !of_machine_is_compatible("apple,t8132")) return -EPERM;
	existing = of_find_node_by_path("/sound");
	if (existing) { of_node_put(existing); return -EEXIST; }
	existing = of_find_node_by_path("/speaker-group");
	if (existing) { of_node_put(existing); return -EEXIST; }
	mca = of_find_node_by_path("/soc/i2s@393600000");
	i2c = of_find_node_by_path("/soc/i2c@3ad01c000");
	gpio = of_find_node_by_path("/soc/pinctrl@39a000000");
	if (!mca || !i2c || !gpio || !of_device_is_available(mca) ||
	    !of_device_is_available(i2c) || !of_device_is_available(gpio) ||
	    !of_device_is_compatible(mca, "apple,t8132-mca") ||
	    !of_device_is_compatible(i2c, "apple,t8132-i2c") ||
	    !of_device_is_compatible(gpio, "apple,t8132-pinctrl") ||
	    !exact_resource(mca, 0x393600000ULL, 0xc000) ||
	    !exact_resource(i2c, 0x3ad01c000ULL, 0x4000) ||
	    !exact_resource(gpio, 0x39a000000ULL, 0x100000) ||
	    gpio->phandle != 0x3f ||
	    of_property_read_u32(gpio, "#gpio-cells", &cells) || cells != 2 ||
	    of_get_child_count(i2c) || of_find_property(mca, "apple,speaker-group", NULL)) goto out;
	adapter = of_find_i2c_adapter_by_node(i2c);
	if (!adapter || device_for_each_child(&adapter->dev, NULL, any_i2c_client)) goto out;
	pdev = of_find_device_by_node(mca);
	if (!pdev || !device_trylock(&pdev->dev)) goto out;
	locked = true;
	if (pdev->dev.driver) goto out;
	ret = 0;
	if (!apply) {
		pr_info("j713_audio_dt3: metadata preconditions verified; no change\n");
		goto out;
	}
	/* Keep storage and outcome inspectable, including partial-apply failure. */
	__module_get(THIS_MODULE);
	result = of_overlay_fdt_apply(j713_audio_dtbo, sizeof(j713_audio_dtbo), &overlay_id, NULL);
	if (!result) result = postcheck(mca, i2c, gpio);
	pr_info("j713_audio_dt3: result=%d overlay=%d retained\n", result, overlay_id);
out:
	if (locked) device_unlock(&pdev->dev);
	if (pdev) put_device(&pdev->dev);
	if (adapter) put_device(&adapter->dev);
	of_node_put(gpio);
	of_node_put(i2c);
	of_node_put(mca);
	return ret;
}

static void __exit j713_dt_exit(void) { }
module_init(j713_dt_init);
module_exit(j713_dt_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Retained J713 speaker metadata for native qualification");
