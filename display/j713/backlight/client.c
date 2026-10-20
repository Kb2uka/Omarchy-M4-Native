// SPDX-License-Identifier: GPL-2.0-only
/* J713 brightness-only DCP client. Probe never wakes firmware; activation
 * is explicit and requires the verified per-boot display DMA handoff.
 * Transport or firmware failure disables brightness until the next reboot.
 */
#include <linux/backlight.h>
#include <linux/debugfs.h>
#include <linux/dma-mapping.h>
#include <linux/io.h>
#include <linux/iommu.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/of_address.h>
#include <linux/platform_device.h>
#include <linux/seq_file.h>
#include <linux/soc/apple/rtkit.h>
#include <linux/uaccess.h>
#include <linux/unaligned.h>
#include "afk.h"

#define PANEL_MAX_NITS 525

struct j713_client {
	struct device *dev;
	struct apple_rtkit *rtk;
	struct afk_endpoint *ep;
	struct mutex control;
	struct completion service_ready;
	struct backlight_device *backlight;
	struct dentry *debugfs;
	bool prepared;
	bool attempted;
	bool online;
	bool failed;
	u32 applied_nits;
	int last_error;
};

struct iboot_request {
	__le32 op, length, reserved[2], value;
};

static void client_failed(void *cookie, int error)
{
	struct j713_client *client = cookie;

	WRITE_ONCE(client->last_error, error);
	WRITE_ONCE(client->failed, true);
	WRITE_ONCE(client->online, false);
}

static void client_receive(void *cookie, u8 ep, u64 message)
{
	struct j713_client *client = cookie;
	if (ep == J713_AFK_ENDPOINT && client->ep) {
		int ret = afk_receive_message(client->ep, message);

		if (ret)
			client_failed(client, ret);
	} else
		dev_warn(client->dev, "Unexpected DCP endpoint %02x\n", ep);
}

static void client_crashed(void *cookie, const void *log, size_t length)
{
	struct j713_client *client = cookie;

	client_failed(client, -EIO);
	dev_err(client->dev, "DCP crashed; brightness unavailable until reboot\n");
}

static bool reserved_span(struct device *dev, phys_addr_t phys, size_t size)
{
	struct device_node *node;
	struct resource res;
	unsigned int i;
	if (!size || phys + size - 1 < phys)
		return false;
	for (i = 0; (node = of_parse_phandle(dev->of_node, "memory-region", i)); ++i) {
		int ret = of_property_read_bool(node, "no-map") ?
			of_address_to_resource(node, 0, &res) : -EINVAL;
		of_node_put(node);
		if (!ret && phys >= res.start && phys + size - 1 <= res.end)
			return true;
	}
	return false;
}

static int client_shmem_setup(void *cookie, struct apple_rtkit_shmem *bfr)
{
	struct j713_client *client = cookie;
	struct device *dev = client->dev;
	struct iommu_domain *domain;
	phys_addr_t phys;
	size_t offset;
	if (!bfr->size)
		return -EINVAL;
	if (!bfr->iova) {
		bfr->buffer = dma_alloc_coherent(dev, bfr->size, &bfr->iova, GFP_KERNEL);
		return bfr->buffer ? 0 : -ENOMEM;
	}
	domain = iommu_get_domain_for_dev(dev);
	if (!domain || bfr->iova + bfr->size - 1 < bfr->iova)
		return -EINVAL;
	phys = iommu_iova_to_phys(domain, bfr->iova);
	if (!phys || !reserved_span(dev, phys, bfr->size))
		return -EINVAL;
	for (offset = 0; offset < bfr->size;) {
		if (iommu_iova_to_phys(domain, bfr->iova + offset) != phys + offset)
			return -EINVAL;
		offset += min_t(size_t, bfr->size - offset,
				 PAGE_SIZE - offset_in_page(bfr->iova + offset));
	}
	if (iommu_iova_to_phys(domain, bfr->iova + bfr->size - 1) != phys + bfr->size - 1)
		return -EINVAL;
	/* Boot reservations are no-map: avoid creating a cached alias for shared
	 * firmware log buffers. These are reserved DRAM, not register windows. */
	bfr->buffer = memremap(phys, bfr->size, MEMREMAP_WC);
	if (!bfr->buffer)
		return -ENOMEM;
	bfr->is_mapped = true;
	return 0;
}

static void client_shmem_destroy(void *cookie, struct apple_rtkit_shmem *bfr)
{
	struct j713_client *client = cookie;
	if (bfr->is_mapped)
		memunmap(bfr->buffer);
	else if (bfr->buffer)
		dma_free_coherent(client->dev, bfr->size, bfr->buffer, bfr->iova);
}

static const struct apple_rtkit_ops client_rtkit_ops = {
	.crashed = client_crashed,
	.recv_message = client_receive,
	.shmem_setup = client_shmem_setup,
	.shmem_destroy = client_shmem_destroy,
};

static void service_ready(void *cookie)
{
	struct j713_client *client = cookie;

	complete(&client->service_ready);
}

static int iboot_command(struct j713_client *client, u32 op, u32 bits,
			 bool has_value, void *reply, size_t reply_size)
{
	struct iboot_request request = {};
	size_t length = has_value ? sizeof(request) : offsetof(struct iboot_request, value);
	u32 retcode = 0;
	int ret;
	request.op = cpu_to_le32(op);
	request.length = cpu_to_le32(length);
	request.value = cpu_to_le32(bits);
	if (READ_ONCE(client->failed))
		return -ENODEV;
	ret = afk_send_command(client->ep, &request, length,
			       reply, reply_size, &retcode);
	if (!ret && retcode) {
		dev_err(client->dev, "DCP command %u returned 0x%08x\n", op, retcode);
		ret = -EIO;
	}
	if (ret)
		client_failed(client, ret);
	return ret;
}

/* Encode an integer as IEEE-754 binary32 using integer arithmetic. Kernel
 * code must not use the floating-point unit. All panel values fit exactly. */
static u32 nits_bits(u32 nits)
{
	unsigned int exponent;
	if (!nits)
		return 0;
	exponent = fls(nits) - 1;
	return ((exponent + 127) << 23) | ((nits << (23 - exponent)) & 0x7fffff);
}

static int set_nits(struct j713_client *client, u32 nits)
{
	int ret;
	if (!READ_ONCE(client->online) || READ_ONCE(client->failed))
		return -ENODEV;
	if (nits > PANEL_MAX_NITS)
		return -ERANGE;
	/* Command 7 returns transport status only, not an iBoot reply body. */
	ret = iboot_command(client, 7, nits_bits(nits), true, NULL, 0);
	if (!ret)
		client->applied_nits = nits;
	return ret;
}

static int backlight_update(struct backlight_device *backlight)
{
	struct j713_client *client = bl_get_data(backlight);
	int ret;
	mutex_lock(&client->control);
	ret = set_nits(client, backlight_get_brightness(backlight));
	mutex_unlock(&client->control);
	return ret;
}

static int backlight_read(struct backlight_device *backlight)
{
	struct j713_client *client = bl_get_data(backlight);
	return READ_ONCE(client->applied_nits);
}

static const struct backlight_ops backlight_ops = {
	.update_status = backlight_update,
	.get_brightness = backlight_read,
};

static ssize_t start_write(struct file *file, const char __user *buffer,
			   size_t size, loff_t *pos)
{
	struct j713_client *client = file->private_data;
	u32 value;
	u8 reply[64] = {};
	int ret = kstrtou32_from_user(buffer, size, 0, &value);
	if (ret)
		return ret;
	if (value != 1)
		return -EINVAL;
	mutex_lock(&client->control);
	if (client->attempted) {
		ret = -EALREADY;
		goto out;
	}
	if (!READ_ONCE(client->prepared) || READ_ONCE(client->failed)) {
		ret = -ENODEV;
		goto out;
	}
	client->attempted = true;
	ret = apple_rtkit_wake(client->rtk);
	if (ret)
		goto failed;
	ret = afk_start(client->ep);
	if (ret)
		goto failed;
	if (!wait_for_completion_timeout(&client->service_ready, msecs_to_jiffies(1000))) {
		ret = -ETIMEDOUT;
		goto failed;
	}
	ret = iboot_command(client, 3, 0, false, reply, sizeof(reply));
	if (ret)
		goto failed;
	if (get_unaligned_le32(reply) != 3 || get_unaligned_le32(reply + 4) != 20 ||
	    get_unaligned_le32(reply + 8) != 1 ||
	    get_unaligned_le32(reply + 12) != 1 ||
	    get_unaligned_le32(reply + 16) != 1) {
		ret = -EPROTO;
		goto failed;
	}
	WRITE_ONCE(client->online, true);
	goto out;
failed:
	client_failed(client, ret);
	dev_err(client->dev, "DCP bring-up failed: %d; no retry until reboot\n", ret);
out:
	mutex_unlock(&client->control);
	return ret ? ret : size;
}

static ssize_t brightness_write(struct file *file, const char __user *buffer,
				size_t size, loff_t *pos)
{
	struct j713_client *client = file->private_data;
	struct backlight_properties props = {};
	u32 nits;
	int ret = kstrtou32_from_user(buffer, size, 0, &nits);
	if (ret)
		return ret;
	mutex_lock(&client->control);
	ret = set_nits(client, nits);
	if (!ret && !client->backlight) {
		props.type = BACKLIGHT_RAW;
		props.max_brightness = PANEL_MAX_NITS;
		props.brightness = nits;
		client->backlight = backlight_device_register("j713-dcp", client->dev,
							    client, &backlight_ops, &props);
		if (IS_ERR(client->backlight)) {
			ret = PTR_ERR(client->backlight);
			client->backlight = NULL;
			client_failed(client, ret);
		}
	}
	mutex_unlock(&client->control);
	return ret ? ret : size;
}

static int status_show(struct seq_file *seq, void *unused)
{
	struct j713_client *client = seq->private;
	seq_printf(seq, "attempted=%u online=%u failed=%u applied_nits=%u error=%d\n",
		   READ_ONCE(client->attempted), READ_ONCE(client->online),
		   READ_ONCE(client->failed), READ_ONCE(client->applied_nits),
		   READ_ONCE(client->last_error));
	return 0;
}
DEFINE_SHOW_ATTRIBUTE(status);

static const struct file_operations start_fops = {
	.owner = THIS_MODULE, .open = simple_open, .write = start_write, .llseek = noop_llseek,
};
static const struct file_operations brightness_fops = {
	.owner = THIS_MODULE, .open = simple_open, .write = brightness_write, .llseek = noop_llseek,
};

static void client_remove_debugfs(void *data)
{
	struct j713_client *client = data;

	debugfs_remove_recursive(client->debugfs);
}

static int validate_stream(struct device *dev)
{
	struct of_phandle_args args;
	int ret;

	if (of_count_phandle_with_args(dev->of_node, "iommus", "#iommu-cells") != 1)
		return -EINVAL;
	ret = of_parse_phandle_with_args(dev->of_node, "iommus", "#iommu-cells", 0, &args);
	if (ret)
		return ret;
	ret = args.args_count == 1 && args.args[0] == 23 ? 0 : -EINVAL;
	of_node_put(args.np);
	return ret;
}

static int client_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct j713_client *client;
	int ret;
	if (!of_machine_is_compatible("apple,j713") ||
	    !of_machine_is_compatible("apple,t8132") ||
	    !of_property_read_bool(dev->of_node, "omarchy,display-handoff-verified") ||
	    !of_find_property(dev->of_node, "memory-region", NULL))
		return dev_err_probe(dev, -EINVAL, "Verified J713 DCP handoff required\n");
	ret = validate_stream(dev);
	if (ret)
		return dev_err_probe(dev, ret, "Exactly DCP stream 23 is required\n");
	if (!iommu_get_domain_for_dev(dev))
		return dev_err_probe(dev, -EINVAL, "DCP DMA domain is unavailable\n");
	ret = dma_set_mask_and_coherent(dev, DMA_BIT_MASK(42));
	if (ret)
		return ret;
	client = devm_kzalloc(dev, sizeof(*client), GFP_KERNEL);
	if (!client)
		return -ENOMEM;
	client->dev = dev;
	mutex_init(&client->control);
	init_completion(&client->service_ready);
	client->rtk = devm_apple_rtkit_init(dev, client, "mbox", 0, &client_rtkit_ops);
	if (IS_ERR(client->rtk))
		return dev_err_probe(dev, PTR_ERR(client->rtk), "RTKit mailbox unavailable\n");
	client->ep = afk_init(dev, client->rtk, client, service_ready, client_failed);
	if (IS_ERR(client->ep))
		return PTR_ERR(client->ep);
	client->debugfs = debugfs_create_dir("j713_dcp_backlight", NULL);
	if (IS_ERR(client->debugfs))
		return PTR_ERR(client->debugfs);
	ret = devm_add_action_or_reset(dev, client_remove_debugfs, client);
	if (ret)
		return ret;
	if (IS_ERR(debugfs_create_file("start", 0200, client->debugfs, client, &start_fops)) ||
	    IS_ERR(debugfs_create_file("brightness_nits", 0200, client->debugfs,
				      client, &brightness_fops)) ||
	    IS_ERR(debugfs_create_file("status", 0400, client->debugfs, client, &status_fops)))
		return -ENOMEM;
	platform_set_drvdata(pdev, client);
	/* The Linux DMA domain now owns live display mappings. Until display-safe
	 * detach is qualified, neither unbind nor module unload may free them.
	 * A reboot is the lifetime boundary for this driver. */
	__module_get(THIS_MODULE);
	WRITE_ONCE(client->prepared, true);
	dev_info(dev, "DCP client prepared; start is explicit, unload requires reboot\n");
	return 0;
}

static const struct of_device_id client_match[] = {
	{ .compatible = "omarchy,j713-dcp-backlight" },
	{}
};
MODULE_DEVICE_TABLE(of, client_match);
static struct platform_driver client_driver = {
	.probe = client_probe,
	.driver = {
		.name = "j713-dcp-backlight",
		.of_match_table = client_match,
		.suppress_bind_attrs = true,
	},
};
module_platform_driver(client_driver);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("J713 native DCP panel backlight using Linux RTKit and AFK");
