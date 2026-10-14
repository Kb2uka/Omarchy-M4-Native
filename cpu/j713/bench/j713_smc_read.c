// SPDX-License-Identifier: GPL-2.0-only
/*
 * Read-only J713 SMC key reader (bench diagnostic, not shipped).
 * /proc/j713_smc_read reads each 4-byte key in `keys` once per read and
 * prints "KEY hexbits". It never writes a key.
 */
#include <linux/mfd/macsmc.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/of_platform.h>
#include <linux/platform_device.h>
#include <linux/proc_fs.h>
#include <linux/seq_file.h>
#include <linux/string.h>

static char *keys = "TCMz,PSTR,PDTR,PPBR,PHPC,PHPS,PHPM,PHPB";
module_param(keys, charp, 0444);

static struct platform_device *smc_pdev;
static struct apple_smc *smc;

static int show(struct seq_file *m, void *v)
{
	const char *p = keys;

	while (strlen(p) >= 4) {
		smc_key key = (p[0] << 24) | (p[1] << 16) | (p[2] << 8) | p[3];
		u32 bits = 0;
		int ret = apple_smc_read(smc, key, &bits, sizeof(bits));

		seq_printf(m, "%.4s %08x %d\n", p, bits, ret);
		p += 4;
		if (*p == ',')
			p++;
	}
	return 0;
}

static const struct of_device_id ids[] = { { .compatible = "apple,t8132-smc" }, { } };

static int __init rd_init(void)
{
	struct device_node *np = of_find_matching_node(NULL, ids);

	if (!np)
		return -ENODEV;
	smc_pdev = of_find_device_by_node(np);
	of_node_put(np);
	if (!smc_pdev)
		return -ENODEV;
	smc = dev_get_drvdata(&smc_pdev->dev);
	if (!smc || !proc_create_single("j713_smc_read", 0444, NULL, show)) {
		put_device(&smc_pdev->dev);
		return -ENODEV;
	}
	return 0;
}

static void __exit rd_exit(void)
{
	remove_proc_entry("j713_smc_read", NULL);
	put_device(&smc_pdev->dev);
}

module_init(rd_init);
module_exit(rd_exit);
MODULE_LICENSE("GPL");
