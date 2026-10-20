// SPDX-License-Identifier: GPL-2.0-only
/*
 * Apple Image Signal Processor driver
 *
 * Copyright (C) 2023 The Asahi Linux Contributors
 */

#include <linux/capability.h>
#include <linux/iommu.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/of_address.h>
#include <linux/of_device.h>
#include <linux/platform_device.h>
#include <linux/string.h>
#include <linux/utsname.h>
#include <linux/pm_domain.h>
#include <linux/pm_runtime.h>
#include <linux/workqueue.h>

#include "isp-cam.h"
#include "isp-fw.h"
#include "isp-iommu.h"
#include "isp-m4-layout.h"
#include "isp-regs.h"
#include "isp-v4l2.h"

#define J713_ISP_RELEASE "7.1.9-m4-gpu-backport-20261008"

/*
 * Private T8132 opt-in. Default false: refuse T8132 probe before any
 * hardware access. Future operator launch is separately reviewed.
 */
static bool j713_bringup;
module_param(j713_bringup, bool, 0400);
MODULE_PARM_DESC(j713_bringup,
		 "Private J713/T8132 bringup opt-in; default false refuses T8132 probe before hardware access");

static void apple_isp_detach_genpd(struct apple_isp *isp)
{
	if (isp->pd_count <= 1)
		return;

	for (int i = isp->pd_count - 1; i >= 0; i--) {
		if (isp->pd_link[i])
			device_link_del(isp->pd_link[i]);
		if (IS_ERR_OR_NULL(isp->pd_dev[i]))
			continue;
		if (isp->hw->resident_fw)
			dev_pm_syscore_device(isp->pd_dev[i], false);
		dev_pm_domain_detach(isp->pd_dev[i], true);
	}

	return;
}

static int apple_isp_attach_genpd(struct apple_isp *isp)
{
	struct device *dev = isp->dev;

	isp->pd_count = of_count_phandle_with_args(
		dev->of_node, "power-domains", "#power-domain-cells");
	if (isp->pd_count <= 1)
		return 0;

	isp->pd_dev = devm_kcalloc(dev, isp->pd_count, sizeof(*isp->pd_dev),
				   GFP_KERNEL);
	if (!isp->pd_dev)
		return -ENOMEM;

	isp->pd_link = devm_kcalloc(dev, isp->pd_count, sizeof(*isp->pd_link),
				    GFP_KERNEL);
	if (!isp->pd_link)
		return -ENOMEM;

	for (int i = 0; i < isp->pd_count; i++) {
		int flags = DL_FLAG_STATELESS;

		/* Primary power domain uses RPM integration */
		if (i == 0)
			flags |= DL_FLAG_PM_RUNTIME | DL_FLAG_RPM_ACTIVE;

		isp->pd_dev[i] = dev_pm_domain_attach_by_id(dev, i);
		if (IS_ERR(isp->pd_dev[i])) {
			apple_isp_detach_genpd(isp);
			return PTR_ERR(isp->pd_dev[i]);
		}

		isp->pd_link[i] =
			device_link_add(dev, isp->pd_dev[i], flags);

		if (!isp->pd_link[i]) {
			apple_isp_detach_genpd(isp);
			return -EINVAL;
		}

		/*
		 * The domains of resident firmware are switched by the
		 * driver alone, through runtime PM: the firmware does not
		 * start again once they have been off. System sleep would
		 * otherwise also have genpd switch them off in the noirq
		 * phase of suspend and back on in the noirq phase of resume,
		 * where the CPU core domains of a stopped coprocessor fail
		 * to reach the active state.
		 */
		if (isp->hw->resident_fw)
			dev_pm_syscore_device(isp->pd_dev[i], true);
	}

	return 0;
}

static void apple_isp_unmap_fw_mmio(struct apple_isp *isp, unsigned int count)
{
	for (unsigned int i = 0; i < count; i++)
		iommu_unmap(isp->domain, isp->hw->fw_mmio[i].base,
			    isp->hw->fw_mmio[i].size);
}

/*
 * Some firmware accesses registers of other blocks, such as the PMGR
 * scratch registers named in PMP_CTRL_SET, through its DART at their
 * physical addresses. Map those windows 1:1.
 */
static int apple_isp_map_fw_mmio(struct apple_isp *isp)
{
	u64 start = isp->fw.heap_top, end = start + isp->iova_size;

	for (unsigned int i = 0; i < isp->hw->num_fw_mmio; i++) {
		const struct isp_mmio_window *w = &isp->hw->fw_mmio[i];
		int err;

		/* Keep them out of the range the surfaces come from. */
		if (w->base < end && w->base + w->size > start) {
			dev_err(isp->dev,
				"MMIO window 0x%llx+0x%llx overlaps the IOVA range\n",
				w->base, w->size);
			err = -EINVAL;
		} else {
			/*
			 * Cacheable: with the no-cache attribute, the ISP took
			 * an SError on its first write to the PMP scratch
			 * register when streaming started.
			 */
			err = iommu_map(isp->domain, w->base, w->base, w->size,
					IOMMU_READ | IOMMU_WRITE | IOMMU_CACHE,
					GFP_KERNEL);
			if (err)
				dev_err(isp->dev,
					"failed to map MMIO window 0x%llx+0x%llx: %d\n",
					w->base, w->size, err);
		}

		if (err) {
			apple_isp_unmap_fw_mmio(isp, i);
			return err;
		}
	}

	return 0;
}

static int apple_isp_init_iommu(struct apple_isp *isp)
{
	struct device *dev = isp->dev;
	phys_addr_t heap_base;
	size_t heap_size;
	u64 vm_size;
	int err;
	int idx;
	int size;
	struct device_node *mem_node;
	const __be32 *maps, *end;

	isp->domain = iommu_get_domain_for_dev(isp->dev);
	if (!isp->domain)
		return -ENODEV;
	isp->shift = __ffs(isp->domain->pgsize_bitmap);
	if (isp->hw->fw_iova_mask_unknown) {
		dev_err(dev, "ISP firmware IOVA mask is not established\n");
		return -EOPNOTSUPP;
	}
	isp->fw_iova_mask = isp->hw->fw_iova_mask ?: U64_MAX;

	idx = of_property_match_string(dev->of_node, "memory-region-names",
				       "heap");
	mem_node = of_parse_phandle(dev->of_node, "memory-region", idx);
	if (!mem_node) {
		dev_err(dev, "No memory-region found for heap\n");
		return -ENODEV;
	}

	maps = of_get_property(mem_node, "iommu-addresses", &size);
	if (!maps || !size) {
		dev_err(dev, "No valid iommu-addresses found for heap\n");
		return -ENODEV;
	}

	end = maps + size / sizeof(__be32);

	while (maps < end) {
		maps++;
		maps = of_translate_dma_region(dev->of_node, maps, &heap_base,
					       &heap_size);
	}

	isp->fw.heap_top = heap_base + heap_size;

	err = of_property_read_u64(dev->of_node, "apple,dart-vm-size",
				   &vm_size);
	if (err) {
		dev_err(dev, "failed to read 'apple,dart-vm-size': %d\n", err);
		return err;
	}

	// FIXME: refactor this, maybe use regular iova stuff?
	isp->iova_size = vm_size - (heap_base & 0xffffffff);
	drm_mm_init(&isp->iovad, isp->fw.heap_top, isp->iova_size);

	err = apple_isp_map_fw_mmio(isp);
	if (err) {
		drm_mm_takedown(&isp->iovad);
		return err;
	}

	return 0;
}

static void apple_isp_free_iommu(struct apple_isp *isp)
{
	apple_isp_unmap_fw_mmio(isp, isp->hw->num_fw_mmio);
	drm_mm_takedown(&isp->iovad);
}

static int isp_of_read_coord(struct device *dev, struct device_node *np,
			     const char *prop, struct coord *val)
{
	u32 xy[2];
	int ret;

	ret = of_property_read_u32_array(np, prop, xy, 2);
	if (ret) {
		dev_err(dev, "failed to read '%s' property\n", prop);
		return ret;
	}

	val->x = xy[0];
	val->y = xy[1];
	return 0;
}

static int apple_isp_init_presets(struct apple_isp *isp)
{
	struct device *dev = isp->dev;
	struct isp_preset *preset;
	int err = 0;

	struct device_node *np __free(device_node) =
		of_get_child_by_name(dev->of_node, "sensor-presets");
	if (!np) {
		dev_err(dev, "failed to get DT node 'presets'\n");
		return -EINVAL;
	}

	isp->num_presets = of_get_child_count(np);
	if (!isp->num_presets) {
		dev_err(dev, "no sensor presets found\n");
		return -EINVAL;
	}

	isp->presets = devm_kzalloc(
		dev, sizeof(*isp->presets) * isp->num_presets, GFP_KERNEL);
	if (!isp->presets)
		return -ENOMEM;

	preset = isp->presets;
	for_each_child_of_node_scoped(np, child) {
		u32 xywh[4];

		err = of_property_read_u32(child, "apple,config-index",
					   &preset->index);
		if (err) {
			dev_err(dev, "no apple,config-index property\n");
			return err;
		}

		err = isp_of_read_coord(dev, child, "apple,input-size",
					&preset->input_dim);
		if (err)
			return err;
		err = isp_of_read_coord(dev, child, "apple,output-size",
					&preset->output_dim);
		if (err)
			return err;

		err = of_property_read_u32_array(child, "apple,crop", xywh, 4);
		if (err) {
			dev_err(dev, "failed to read 'apple,crop' property\n");
			return err;
		}
		preset->crop_offset.x = xywh[0];
		preset->crop_offset.y = xywh[1];
		preset->crop_size.x = xywh[2];
		preset->crop_size.y = xywh[3];

		preset++;
	}

	return 0;
}

static const char * isp_fw2str(enum isp_firmware_version version)
{
	switch (version) {
	case ISP_FIRMWARE_V_12_3:
		return "12.3";
	case ISP_FIRMWARE_V_12_4:
		return "12.4";
	case ISP_FIRMWARE_V_13_5:
		return "13.5";
	case ISP_FIRMWARE_V_14_7:
		return "14.7";
	case ISP_FIRMWARE_V_26_5:
		return "26.5";
	default:
		return "unknown";
	}
}

#define ISP_FW_VERSION_MIN_LEN	3
#define ISP_FW_VERSION_MAX_LEN	5

static enum isp_firmware_version isp_read_fw_version(struct device *dev,
						     const char *name)
{
	u32 ver[ISP_FW_VERSION_MAX_LEN];
	int size, len;

	/* The array reader truncates trailing bytes; require complete cells. */
	if (of_find_property(dev->of_node, name, &size) && size % sizeof(u32)) {
		dev_warn(dev, "could not parse %s: invalid length %d\n", name, size);
		return ISP_FIRMWARE_V_UNKNOWN;
	}

	len = of_property_read_variable_u32_array(dev->of_node, name, ver,
						  ISP_FW_VERSION_MIN_LEN,
						  ISP_FW_VERSION_MAX_LEN);

	switch (len) {
	case -EINVAL:
		/* not provided, see isp_check_firmware_version() */
		break;
	case 3:
		if (ver[0] == 12 && ver[1] == 3 && ver[2] <= 1)
			return ISP_FIRMWARE_V_12_3;
		else if (ver[0] == 12 && ver[1] == 4 && ver[2] == 0)
			return ISP_FIRMWARE_V_12_4;
		else if (ver[0] == 13 && ver[1] == 5 && ver[2] == 0)
			return ISP_FIRMWARE_V_13_5;
		else if (ver[0] == 14 && ver[1] == 7 && ver[2] == 0)
			return ISP_FIRMWARE_V_14_7;
		else if (ver[0] == 26 && ver[1] == 5)
			return ISP_FIRMWARE_V_26_5;

		dev_warn(dev, "unknown %s: %d.%d.%d\n", name, ver[0], ver[1], ver[2]);
		break;
	case 4:
		dev_warn(dev, "unknown %s: %d.%d.%d.%d\n", name, ver[0], ver[1],
			 ver[2], ver[3]);
		break;
	case 5:
		dev_warn(dev, "unknown %s: %d.%d.%d.%d.%d\n", name, ver[0],
			 ver[1], ver[2], ver[3], ver[4]);
		break;
	default:
		dev_warn(dev, "could not parse %s: %d\n", name, len);
		break;
	}

	return ISP_FIRMWARE_V_UNKNOWN;
}

static enum isp_firmware_version
isp_check_firmware_version(struct apple_isp *isp)
{
	struct device *dev = isp->dev;
	enum isp_firmware_version version, compat;

	/* The H17 interface is selected by match data, not by version. */
	if (isp->hw->fw_abi == ISP_FW_ABI_H17)
		return ISP_FIRMWARE_V_UNKNOWN;

	/* firmware version is just informative */
	version = isp_read_fw_version(dev, "apple,firmware-version");
	compat = isp_read_fw_version(dev, "apple,firmware-compat");

	dev_dbg(dev, "ISP firmware-compat: %s (FW: %s)\n", isp_fw2str(compat),
		isp_fw2str(version));

	if (!of_property_present(dev->of_node, "apple,firmware-compat"))
		dev_warn(dev, "firmware compatibility version not provided, assuming 12.x\n");

	return compat;
}

static int apple_isp_t8132_bringup_guard(struct device *dev)
{
	if (!j713_bringup) {
		dev_err(dev, "T8132 probe refused until j713_bringup=1\n");
		return -ENODEV;
	}
	if (!capable(CAP_SYS_RAWIO)) {
		dev_err(dev, "T8132 bringup requires CAP_SYS_RAWIO\n");
		return -EPERM;
	}
	if (!of_machine_is_compatible("apple,j713") ||
	    !of_machine_is_compatible("apple,t8132")) {
		dev_err(dev,
			"T8132 bringup requires apple,j713 and apple,t8132\n");
		return -EPERM;
	}
	if (strcmp(utsname()->release, J713_ISP_RELEASE)) {
		dev_err(dev, "T8132 bringup requires kernel %s\n",
			J713_ISP_RELEASE);
		return -EPERM;
	}
	/*
	 * Guards passed. No GPIO, firmware, DT bootstrap, heap allocator,
	 * or IOVA mask this dispatch. Remaining inputs are listed in the
	 * T8132 hardware comment.
	 */
	dev_err(dev,
		"T8132 bringup guards passed; firmware, GPIO and streaming stay unavailable\n");
	return -EOPNOTSUPP;
}

static int apple_isp_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct apple_isp *isp;
	struct resource *res;
	const struct apple_isp_hw *hw;
	int err;

	hw = of_device_get_match_data(dev);
	if (hw && hw->gen == ISP_GEN_T8132)
		return apple_isp_t8132_bringup_guard(dev);

	err = dma_set_mask_and_coherent(dev, DMA_BIT_MASK(42));
	if (err)
		return err;

	/*
	 * Not devres: an open video device file keeps this until it is
	 * closed, also after unbind. See apple_isp_release().
	 */
	isp = kzalloc_obj(*isp);
	if (!isp)
		return -ENOMEM;

	isp->dev = dev;
	isp->hw = of_device_get_match_data(dev);
	platform_set_drvdata(pdev, isp);
	dev_set_drvdata(dev, isp);

	/* Differences between firmware versions are rather minor so try to work
	 * with unknown firmware.
	 */
	isp->fw_compat = isp_check_firmware_version(isp);

	err = of_property_read_u32(dev->of_node, "apple,platform-id",
				   &isp->platform_id);
	if (err) {
		dev_err(dev, "failed to get 'apple,platform-id' property: %d\n",
			err);
		goto free_isp;
	}

	err = of_property_read_u32(dev->of_node, "apple,temporal-filter",
				   &isp->temporal_filter);
	if (err)
		isp->temporal_filter = 0;

	err = apple_isp_init_presets(isp);
	if (err) {
		dev_err(dev, "failed to initialize presets\n");
		goto free_isp;
	}

	err = apple_isp_attach_genpd(isp);
	if (err) {
		dev_err(dev, "failed to attatch power domains\n");
		goto free_isp;
	}

	isp->coproc = devm_platform_ioremap_resource_byname(pdev, "coproc");
	if (IS_ERR(isp->coproc)) {
		err = PTR_ERR(isp->coproc);
		goto detach_genpd;
	}

	res = platform_get_resource_byname(pdev, IORESOURCE_MEM, "mbox");
	isp->mbox = devm_ioremap_resource(dev, res);
	if (IS_ERR(isp->mbox)) {
		err = PTR_ERR(isp->mbox);
		goto detach_genpd;
	}

	isp->gpio = devm_platform_ioremap_resource_byname(pdev, "gpio");
	if (IS_ERR(isp->gpio)) {
		err = PTR_ERR(isp->gpio);
		goto detach_genpd;
	}

	if (isp->hw->mbox2_offset) {
		if (resource_size(res) <
		    isp->hw->mbox2_offset + ISP_MBOX2_SIZE) {
			dev_err(dev, "mbox window too small for the doorbells\n");
			err = -EINVAL;
			goto detach_genpd;
		}
		isp->mbox2 = isp->mbox + isp->hw->mbox2_offset;
	} else {
		isp->mbox2 = devm_platform_ioremap_resource_byname(pdev,
								   "mbox2");
		if (IS_ERR(isp->mbox2)) {
			err = PTR_ERR(isp->mbox2);
			goto detach_genpd;
		}
	}

	/* Capture watchdog, where the ISP gates its frames on one */
	res = platform_get_resource_byname(pdev, IORESOURCE_MEM, "wdt");
	if (res) {
		isp->wdt = devm_ioremap_resource(dev, res);
		if (IS_ERR(isp->wdt)) {
			err = PTR_ERR(isp->wdt);
			goto detach_genpd;
		}
		apple_isp_wdt_init(isp);
	}

	isp->irq = platform_get_irq(pdev, 0);
	if (isp->irq < 0) {
		err = isp->irq;
		goto detach_genpd;
	}
	if (!isp->irq) {
		err = -ENODEV;
		goto detach_genpd;
	}

	mutex_init(&isp->iovad_lock);
	mutex_init(&isp->video_lock);
	spin_lock_init(&isp->buf_lock);
	init_waitqueue_head(&isp->wait);
	INIT_LIST_HEAD(&isp->gc);
	INIT_LIST_HEAD(&isp->gc_released);
	mutex_init(&isp->gc_lock);
	INIT_LIST_HEAD(&isp->bufs_pending);
	INIT_LIST_HEAD(&isp->bufs_submitted);
	isp->wq = alloc_workqueue("apple-isp-wq", WQ_UNBOUND, 0);
	if (!isp->wq) {
		dev_err(dev, "failed to create workqueue\n");
		err = -ENOMEM;
		goto detach_genpd;
	}

	err = apple_isp_init_iommu(isp);
	if (err) {
		dev_err(dev, "failed to init iommu: %d\n", err);
		goto destroy_wq;
	}

	err = apple_isp_alloc_firmware_surface(isp);
	if (err) {
		dev_err(dev, "failed to alloc firmware surface: %d\n", err);
		goto free_iommu;
	}

	pm_runtime_enable(dev);

	err = apple_isp_detect_camera(isp);
	if (err) {
		dev_err(dev, "failed to detect camera: %d\n", err);
		goto free_surface;
	}

	err = apple_isp_setup_video(isp);
	if (err) {
		dev_err(dev, "failed to register video device: %d\n", err);
		goto halt_firmware;
	}

	return 0;

halt_firmware:
	apple_isp_firmware_halt(isp);
free_surface:
	pm_runtime_disable(dev);
	apple_isp_free_firmware_surface(isp);
free_iommu:
	apple_isp_free_iommu(isp);
destroy_wq:
	destroy_workqueue(isp->wq);
detach_genpd:
	apple_isp_detach_genpd(isp);
free_isp:
	/* Nothing else can reach it before the video device is registered. */
	kfree(isp);
	return err;
}

static void apple_isp_remove(struct platform_device *pdev)
{
	struct apple_isp *isp = platform_get_drvdata(pdev);

	apple_isp_remove_video(isp);
	/* Stop resident firmware before freeing what it may still use. */
	apple_isp_firmware_halt(isp);
	apple_isp_free_video(isp);
	pm_runtime_disable(isp->dev);
	apple_isp_free_firmware_surface(isp);
	apple_isp_free_iommu(isp);
	destroy_workqueue(isp->wq);
	apple_isp_detach_genpd(isp);
	/* Freed now, or when the last open file is closed. */
	v4l2_device_put(&isp->v4l2_dev);
}

static const struct apple_isp_hw apple_isp_hw_t8103 = {
	.gen = ISP_GEN_T8103,
	.pmu_base = 0x23b704000,

	.dsid_count = 4,
	.dsid_clr_base0 = 0x200014000,
	.dsid_clr_base1 = 0x200054000,
	.dsid_clr_base2 = 0x200094000,
	.dsid_clr_base3 = 0x2000d4000,
	.dsid_clr_range0 = 0x1000,
	.dsid_clr_range1 = 0x1000,
	.dsid_clr_range2 = 0x1000,
	.dsid_clr_range3 = 0x1000,

	.clock_scratch = 0x23b738010,
	.clock_base = 0x23bc3c000,
	.clock_bit = 0x1,
	.clock_size = 0x4,
	.bandwidth_scratch = 0x23b73800c,
	.bandwidth_base = 0x23bc3c000,
	.bandwidth_bit = 0x0,
	.bandwidth_size = 0x4,
	.mbox_irq_enable = ISP_MBOX_IRQ_ENABLE,

	.scl1 = false,
	.lpdp = false,
	.meta_size = ISP_META_SIZE_T8103,
};

static const struct apple_isp_hw apple_isp_hw_t6000 = {
	.gen = ISP_GEN_T8103,
	.pmu_base = 0x28e584000,

	.dsid_count = 1,
	.dsid_clr_base0 = 0x200014000,
	.dsid_clr_base1 = 0x200054000,
	.dsid_clr_base2 = 0x200094000,
	.dsid_clr_base3 = 0x2000d4000,
	.dsid_clr_range0 = 0x1000,
	.dsid_clr_range1 = 0x1000,
	.dsid_clr_range2 = 0x1000,
	.dsid_clr_range3 = 0x1000,

	.clock_scratch = 0x28e3d0868,
	.clock_base = 0x0,
	.clock_bit = 0x0,
	.clock_size = 0x8,
	.bandwidth_scratch = 0x28e3d0980,
	.bandwidth_base = 0x0,
	.bandwidth_bit = 0x0,
	.bandwidth_size = 0x8,
	.mbox_irq_enable = ISP_MBOX_IRQ_ENABLE,

	.scl1 = false,
	.lpdp = false,
	.meta_size = ISP_META_SIZE_T8103,
};

static const struct apple_isp_hw apple_isp_hw_t8112 = {
	.gen = ISP_GEN_T8112,
	.pmu_base = 0x23b704000,

	.dsid_count = 1,
	.dsid_clr_base0 = 0x200f14000,
	.dsid_clr_range0 = 0x1000,

	.clock_scratch = 0x23b3d0560,
	.clock_base = 0x0,
	.clock_bit = 0x0,
	.clock_size = 0x8,
	.bandwidth_scratch = 0x23b3d05d0,
	.bandwidth_base = 0x0,
	.bandwidth_bit = 0x0,
	.bandwidth_size = 0x8,
	.mbox_irq_enable = ISP_MBOX_IRQ_ENABLE,

	.scl1 = false,
	.lpdp = false,
	.meta_size = ISP_META_SIZE_T8112,
};

static const struct apple_isp_hw apple_isp_hw_t6020 = {
	.gen = ISP_GEN_T8112,
	.pmu_base = 0x290284000,

	.dsid_count = 1,
	.dsid_clr_base0 = 0x200f14000,
	.dsid_clr_range0 = 0x1000,

	.clock_scratch = 0x28e3d10a8,
	.clock_base = 0x0,
	.clock_bit = 0x0,
	.clock_size = 0x8,
	.bandwidth_scratch = 0x28e3d1200,
	.bandwidth_base = 0x0,
	.bandwidth_bit = 0x0,
	.bandwidth_size = 0x8,
	.mbox_irq_enable = ISP_MBOX_IRQ_ENABLE,

	.scl1 = true,
	.lpdp = true,
	.meta_size = ISP_META_SIZE_T8112,
};

static const struct apple_isp_hw apple_isp_hw_t8122 = {
	.gen = ISP_GEN_T8112,
	.pmu_base = 0x2d07040000,

	.dsid_count = 1,
	.dsid_clr_base0 = 0x2221c4000,
	.dsid_clr_range0 = 0x1000,

	.clock_scratch = 0x2d03d08a0,
	.clock_base = 0x0,
	.clock_bit = 0x0,
	.clock_size = 0x8,
	.bandwidth_scratch = 0x2d03d0900,
	.bandwidth_base = 0x0,
	.bandwidth_bit = 0x0,
	.bandwidth_size = 0x8,
	.mbox_irq_enable = ISP_MBOX_IRQ_ENABLE_T6031,

	.scl1 = true,
	.lpdp = true,
	.meta_size = ISP_META_SIZE_T6031,
};

static const struct apple_isp_hw apple_isp_hw_t6030 = {
	.gen = ISP_GEN_T6031,
	.pmu_base = 0x350704000,

	.dsid_count = 1,
	.dsid_clr_base0 = 0x200f14000,
	.dsid_clr_range0 = 0x1000,

	.clock_scratch = 0x3503d0920,
	.clock_base = 0x0,
	.clock_bit = 0x0,
	.clock_size = 0x8,
	.bandwidth_scratch = 0x3503d0980,
	.bandwidth_base = 0x0,
	.bandwidth_bit = 0x0,
	.bandwidth_size = 0x8,
	.mbox_irq_enable = ISP_MBOX_IRQ_ENABLE_T6031,

	.scl1 = true,
	.lpdp = false,
	.meta_size = ISP_META_SIZE_T6031,
};

static const struct apple_isp_hw apple_isp_hw_t6031 = {
	.gen = ISP_GEN_T6031,
	.pmu_base = 0x292284008,

	.dsid_count = 1,
	.dsid_clr_base0 = 0x200f14000,
	.dsid_clr_range0 = 0x1000,

	.clock_scratch = 0x0,
	.mbox_irq_enable = ISP_MBOX_IRQ_ENABLE_T6031,

	.scl1 = true,
	.lpdp = false,
	.meta_size = ISP_META_SIZE_T6031,
};

/*
 * Registers the T8140 firmware accesses at their physical addresses: the
 * bootloader's DART address filter ranges for the ISP, rounded out to
 * 16 KiB pages.
 */
static const struct isp_mmio_window apple_isp_fw_mmio_t8140[] = {
	{ 0x220004000, 0x14000 },	/* DSID broadcast-clear windows */
	{ 0x220044000, 0x14000 },
	{ 0x220084000, 0x14000 },
	{ 0x2200c4000, 0x14000 },
	{ 0x220104000, 0x14000 },
	{ 0x3003c0000, 0x28000 },	/* PMGR, with the PMP clock scratch */
	{ 0x300704000, 0x4000 },	/* PMGR, ISP power states */
	{ 0x300730000, 0x4000 },
	{ 0x300e3c000, 0x4000 },
	{ 0x302824000, 0x4000 },	/* PMP bandwidth scratch */
	{ 0x31062c000, 0x20000 },
	{ 0x401660000, 0x4000 },
};

static const struct apple_isp_hw apple_isp_hw_t8140 = {
	.gen = ISP_GEN_T8140,
	.fw_abi = ISP_FW_ABI_H17,
	.pmu_base = 0x0,

	.dsid_count = 1,
	.dsid_clr_base0 = 0x220114000,
	.dsid_clr_range0 = 0x2fc,

	.clock_scratch = 0x3003d0ca0,
	.clock_base = 0x0,
	.clock_bit = 0x0,
	.clock_size = 0x8,
	.bandwidth_scratch = 0x302824000,
	.bandwidth_base = 0x0,
	.bandwidth_bit = 0x0,
	.bandwidth_size = 0x8,
	.mbox_irq_enable = ISP_MBOX_IRQ_ENABLE_T6031,

	.scl1 = false,
	.lpdp = true,
	.meta_size = ISP_META_SIZE_T8140,

	.fw_iova_mask = GENMASK_ULL(35, 0),
	.mbox2_offset = ISP_MBOX2_OFFSET_T8140,
	.mbox_irq_route = true,
	.mbox_irq_enable1 = ISP_MBOX_IRQ_ENABLE1_T8140,
	.mbox_irq_enable2 = ISP_MBOX_IRQ_ENABLE2_T8140,
	.mbox_irq_route_off = ISP_MBOX_IRQ_ROUTE_T8140,
	.mbox_irq_enable1_val = ISP_MBOX_IRQ_ENABLE1_T8140_VAL,
	.mbox_irq_enable2_val = ISP_MBOX_IRQ_ENABLE2_T8140_VAL,
	.mbox_irq_route_val = ISP_MBOX_IRQ_ROUTE_T8140_VAL,
	.coproc_control = ISP_COPROC_CONTROL_T8140,
	.skip_gpio_clock_enable = true,
	.coproc_reset_isp17a = true,
	.dsid_multi_bc = true,
	.boot_args = ISP_BOOT_ARGS_H16,
	.shared_size = ISP_T8140_SHARED_SIZE,
	.fw_mmio = apple_isp_fw_mmio_t8140,
	.num_fw_mmio = ARRAY_SIZE(apple_isp_fw_mmio_t8140),
	.boot_mode = 1,
	.capture_meta_size = ISP_CAPTURE_META_SIZE_T8140,
	.resident_fw = true,
};

/*
 * T8132 / J713 identity-map candidates from current ADT dapf-instance-0,
 * start rounded down and inclusive end rounded up to 16 KiB pages. Evidence
 * for address windows only; DAPF is not rewritten.
 *
 * Not this dispatch: DT bootstrap, heap allocator (gap 0x13ec000..0x1400000
 * is 80 KiB of fresh memory, not physical bytes after firmware data),
 * IOVA mask, metadata sizes, sensor ID/setfile, stream presets, H17
 * capture/render ABI, firmware restartability, full halt confirmation.
 * Native Disable at 0x1600040 is stored unused. Full poweroff disables
 * DART mappers before this + local shutdown, then frees buffers; do not
 * free DMA buffers while the coprocessor is running.
 */
static const struct isp_mmio_window apple_isp_fw_mmio_t8132[] = {
	{ 0x380704000, 0x4000 },
	{ 0x380730000, 0x4000 },
	{ 0x380e3c000, 0x4000 },
	{ 0x39062c000, 0x20000 },
	{ 0x501660000, 0x4000 },
	{ 0x220004000, 0x14000 },
	{ 0x220044000, 0x14000 },
	{ 0x2201c4000, 0x14000 },
	{ 0x222004000, 0x14000 },
	{ 0x222044000, 0x14000 },
	{ 0x2221c4000, 0x14000 },
	{ 0x3803c0000, 0x28000 },
	{ 0x382824000, 0x4000 },
};

static const struct apple_isp_hw apple_isp_hw_t8132 = {
	.gen = ISP_GEN_T8132,
	.fw_abi = ISP_FW_ABI_LEGACY,
	.pmu_base = 0x380704000,

	.dsid_count = 2,
	.dsid_clr_base0 = 0x2201d4000,
	.dsid_clr_base1 = 0x2221d4000,
	.dsid_clr_range0 = 0x2fc,

	.mbox_irq_enable = ISP_MBOX_IRQ_ENABLE_T6031,

	.dsid_multi_bc = true,
	.boot_args = ISP_BOOT_ARGS_H16,
	.fw_iova_mask_unknown = true,
	.mbox2_offset = ISP_MBOX2_OFFSET_T8132,
	.mbox_irq_route = true,
	.mbox_irq_enable1 = ISP_MBOX_IRQ_ENABLE1_T8140,
	.mbox_irq_enable2 = ISP_MBOX_IRQ_ENABLE2_T8140,
	.mbox_irq_route_off = ISP_MBOX_IRQ_ROUTE_T8132,
	.mbox_irq_enable1_val = ISP_MBOX_IRQ_ENABLE1_T8140_VAL,
	.mbox_irq_enable2_val = ISP_MBOX_IRQ_ENABLE2_T8132_VAL,
	.mbox_irq_route_val = ISP_MBOX_IRQ_ROUTE_T8140_VAL,
	.coproc_control = ISP_COPROC_CONTROL_T8132,
	.coproc_disable = ISP_COPROC_DISABLE_T8132,
	.skip_gpio_clock_enable = true,
	.coproc_reset_isp17a = true,
	.fw_mmio = apple_isp_fw_mmio_t8132,
	.num_fw_mmio = ARRAY_SIZE(apple_isp_fw_mmio_t8132),
	.boot_mode = 1,
};

static const struct of_device_id apple_isp_of_match[] = {
	{ .compatible = "apple,t8103-isp", .data = &apple_isp_hw_t8103 },
	{ .compatible = "apple,t8112-isp", .data = &apple_isp_hw_t8112 },
	{ .compatible = "apple,t8122-isp", .data = &apple_isp_hw_t8122 },
	{ .compatible = "apple,t6000-isp", .data = &apple_isp_hw_t6000 },
	{ .compatible = "apple,t6020-isp", .data = &apple_isp_hw_t6020 },
	{ .compatible = "apple,t6030-isp", .data = &apple_isp_hw_t6030 },
	{ .compatible = "apple,t6031-isp", .data = &apple_isp_hw_t6031 },
	{ .compatible = "apple,t8140-isp", .data = &apple_isp_hw_t8140 },
	{ .compatible = "apple,t8132-isp", .data = &apple_isp_hw_t8132 },
	{},
};
MODULE_DEVICE_TABLE(of, apple_isp_of_match);

static __maybe_unused int apple_isp_runtime_suspend(struct device *dev)
{
	/* RPM sleep is called when the V4L2 file handle is closed */
	return 0;
}

static __maybe_unused int apple_isp_runtime_resume(struct device *dev)
{
	return 0;
}

static __maybe_unused int apple_isp_suspend(struct device *dev)
{
	struct apple_isp *isp = dev_get_drvdata(dev);

	/* We must restore V4L2 context on system resume. If we were streaming
	 * before, we (essentially) stop streaming and start streaming again.
	 */
	apple_isp_video_suspend(isp);

	/*
	 * Resident firmware stays up, idle, across suspend-to-idle and
	 * suspend-to-RAM: its domains stay on, see apple_isp_attach_genpd().
	 */
	return 0;
}

static __maybe_unused int apple_isp_freeze(struct device *dev)
{
	struct apple_isp *isp = dev_get_drvdata(dev);

	apple_isp_video_suspend(isp);

	/*
	 * Hibernation hands the system from one kernel to another, and
	 * firmware left running would go on using the memory of the kernel
	 * that started it. Stop it cleanly instead; the camera is then
	 * unavailable until the next system boot.
	 */
	if (isp->hw->resident_fw) {
		mutex_lock(&isp->video_lock);
		if (isp->fw_state == ISP_FW_RUNNING)
			dev_warn(dev, "stopping the firmware for hibernation, the camera is unavailable until the next boot\n");
		apple_isp_firmware_halt(isp);
		mutex_unlock(&isp->video_lock);
	}

	return 0;
}

static __maybe_unused int apple_isp_resume(struct device *dev)
{
	struct apple_isp *isp = dev_get_drvdata(dev);

	apple_isp_video_resume(isp);

	return 0;
}

static const struct dev_pm_ops apple_isp_pm_ops = {
	.suspend	= pm_sleep_ptr(apple_isp_suspend),
	.resume		= pm_sleep_ptr(apple_isp_resume),
	.freeze		= pm_sleep_ptr(apple_isp_freeze),
	.thaw		= pm_sleep_ptr(apple_isp_resume),
	.poweroff	= pm_sleep_ptr(apple_isp_freeze),
	.restore	= pm_sleep_ptr(apple_isp_resume),
	RUNTIME_PM_OPS(apple_isp_runtime_suspend, apple_isp_runtime_resume, NULL)
};

static struct platform_driver apple_isp_driver = {
	.driver	= {
		.name		= "apple-isp",
		.of_match_table	= apple_isp_of_match,
		.pm		= pm_ptr(&apple_isp_pm_ops),
	},
	.probe	= apple_isp_probe,
	.remove	= apple_isp_remove,
};
module_platform_driver(apple_isp_driver);

MODULE_AUTHOR("Eileen Yoon <eyn@gmx.com>");
MODULE_DESCRIPTION("Apple ISP driver");
MODULE_LICENSE("GPL v2");
