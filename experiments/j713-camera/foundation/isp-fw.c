// SPDX-License-Identifier: GPL-2.0-only
/* Copyright 2023 Eileen Yoon <eyn@gmx.com> */

#include "isp-fw.h"
#include "isp-ipc-layout.h"
#include "isp-m4-layout.h"

#include <asm/io.h>
#include <linux/delay.h>
#include <linux/iopoll.h>
#include <linux/overflow.h>
#include <linux/pm_runtime.h>
#include <linux/types.h>

#include "isp-cam.h"
#include "isp-cmd.h"
#include "isp-fw.h"
#include "isp-iommu.h"
#include "isp-ipc.h"
#include "isp-regs.h"
#include "isp-v4l2.h"

#define ISP_FIRMWARE_POLL_US	  1000
#define ISP_FIRMWARE_TIMEOUT_US	  1000000

#define ISP_FIRMWARE_IPC_SIZE  0x1c000
#define ISP_FIRMWARE_DATA_SIZE 0x28000

#define ISP_COPROC_IN_WFI      0x3

static inline u32 isp_coproc_read32(struct apple_isp *isp, u32 reg)
{
	return readl(isp->coproc + reg);
}

static inline void isp_coproc_write32(struct apple_isp *isp, u32 reg, u32 val)
{
	writel(val, isp->coproc + reg);
}

static inline u32 isp_gpio_read32(struct apple_isp *isp, u32 reg)
{
	return readl(isp->gpio + reg);
}

static inline void isp_gpio_write32(struct apple_isp *isp, u32 reg, u32 val)
{
	writel(val, isp->gpio + reg);
}

static inline u32 isp_coproc_control(struct apple_isp *isp)
{
	return isp->hw->coproc_control ?: ISP_COPROC_CONTROL;
}

/* Wait for the firmware to write @expected to a GPIO word. */
static int isp_gpio_wait(struct apple_isp *isp, u32 reg, u32 expected)
{
	u32 val;

	return readl_poll_timeout(isp->gpio + reg, val, val == expected,
				  ISP_FIRMWARE_POLL_US,
				  ISP_FIRMWARE_TIMEOUT_US);
}

static int apple_isp_power_up_domains(struct apple_isp *isp)
{
	int ret;

	if (isp->pds_active)
		return 0;

	for (int i = 1; i < isp->pd_count; i++) {
		ret = pm_runtime_resume_and_get(isp->pd_dev[i]);
		if (ret < 0) {
			dev_err(isp->dev,
				"Failed to power up power domain %d: %d\n", i, ret);
			while (--i >= 1)
				pm_runtime_put_sync(isp->pd_dev[i]);
			return ret;
		}
	}

	isp->pds_active = true;

	return 0;
}

static void apple_isp_power_down_domains(struct apple_isp *isp)
{
	int ret;

	if (!isp->pds_active)
		return;

	for (int i = isp->pd_count - 1; i >= 1; i--) {
		ret = pm_runtime_put_sync(isp->pd_dev[i]);
		if (ret < 0)
			dev_err(isp->dev,
				"Failed to power down power domain %d: %d\n", i, ret);
	}

	isp->pds_active = false;
}

void *apple_isp_translate(struct apple_isp *isp, struct isp_surf *surf,
			  dma_addr_t iova, size_t size)
{
	dma_addr_t end = iova + size;
	if (!surf) {
		dev_err_ratelimited(isp->dev,
				    "Failed to translate IPC iova 0x%llx (0x%zx): No surface\n",
				    (long long)iova, size);
		return NULL;
	}

	if (end < iova || iova < surf->iova ||
	    end > (surf->iova + surf->size)) {
		dev_err_ratelimited(isp->dev,
				    "Failed to translate IPC iova 0x%llx (0x%zx): Out of bounds\n",
				    (long long)iova, size);
		return NULL;
	}

	if (!surf->virt) {
		dev_err_ratelimited(isp->dev,
				    "Failed to translate IPC iova 0x%llx (0x%zx): No VMap\n",
				    (long long)iova, size);
		return NULL;
	}

	return surf->virt + (iova - surf->iova);
}

struct isp_firmware_bootargs {
	u32 pad_0[2];
	u64 ipc_iova;
	u64 shared_base;
	u64 shared_size;
	u64 extra_iova;
	u64 extra_size;
	u32 platform_id;
	u32 pad_40;
	u64 logbuf_addr;
	u64 logbuf_size;
	u64 logbuf_entsize;
	u32 ipc_size;
	u32 pad_60[5];
	u32 unk5;
	u32 pad_7c[13];
	u32 pad_b0;
	u32 unk7;
	u32 pad_b8[5];
	u32 unk_iova1;
	u32 pad_c0[47];
	u32 unk9;
} __packed;
static_assert(sizeof(struct isp_firmware_bootargs) == 0x180);

/*
 * Boot descriptor of the H17 firmware. The values the driver fills in are
 * the ones this firmware boots with; how they derive from the rest of the
 * setup is not known.
 */
struct isp_firmware_bootargs_h16 {
	u32 pad_0[2];
	u64 ipc_iova;
	u64 shared_base;
	u64 shared_size;
	u64 extra_iova;
	u64 extra_size;
	u32 platform_id;
	u32 pad_34;
	u64 logbuf_addr;
	u64 logbuf_size;
	u64 logbuf_entsize;
	u64 ipc_queue_size;
	u32 pad_58[4];
	u32 unk_68;
	u32 pad_6c;
	u8 descriptor[ISP_H16_DESCRIPTOR_SIZE];
	u32 pad_270[8];
} __packed;
static_assert(sizeof(struct isp_firmware_bootargs_h16) == 0x290);

/* set in ISP_GPIO_2 by firmware that takes the H16 boot descriptor */
#define ISP_GPIO2_H16_DESCRIPTOR BIT(1)

struct isp_chan_desc {
	char name[64];
	u32 type;
	u32 src;
	u32 num;
	u32 pad;
	u64 iova;
	u32 padding[0x2a];
} __packed;
static_assert(sizeof(struct isp_chan_desc) == 0x100);

static const struct isp_chan_ops tm_ops = {
	.handle = ipc_tm_handle,
};

static const struct isp_chan_ops sm_ops = {
	.handle = ipc_sm_handle,
};

static const struct isp_chan_ops bt_ops = {
	.handle = ipc_bt_handle,
};

static irqreturn_t apple_isp_isr(int irq, void *dev)
{
	struct apple_isp *isp = dev;

	isp_mbox2_write32(isp, ISP_MBOX2_IRQ_ACK,
			 isp_mbox_read32(isp, ISP_MBOX_IRQ_INTERRUPT));

	return IRQ_WAKE_THREAD;
}

static irqreturn_t apple_isp_isr_thread(int irq, void *dev)
{
	struct apple_isp *isp = dev;

	wake_up_all(&isp->wait);

	ipc_chan_handle(isp, isp->chan_sm);
	wake_up_all(&isp->wait); /* Some commands depend on sm */

	ipc_chan_handle(isp, isp->chan_tm);

	ipc_chan_handle(isp, isp->chan_bt);
	wake_up_all(&isp->wait);

	return IRQ_HANDLED;
}

static void isp_disable_irq(struct apple_isp *isp)
{
	isp_mbox_write32(isp, isp->hw->mbox_irq_enable, 0x0);
	if (isp->hw->mbox_irq_route) {
		isp_mbox_write32(isp, isp->hw->mbox_irq_enable1, 0x0);
		isp_mbox_write32(isp, isp->hw->mbox_irq_enable2, 0x0);
	}
	free_irq(isp->irq, isp);
	isp_gpio_write32(isp, ISP_GPIO_1, 0xfeedbabe); /* real funny */
}

static int isp_enable_irq(struct apple_isp *isp)
{
	int err;

	err = request_threaded_irq(isp->irq, apple_isp_isr,
				   apple_isp_isr_thread, 0, "apple-isp", isp);
	if (err < 0) {
		isp_err(isp, "failed to request IRQ#%u (%d)\n", isp->irq, err);
		return err;
	}

	isp_dbg(isp, "about to enable interrupts...\n");

	isp_mbox_write32(isp, isp->hw->mbox_irq_enable, 0xf);
	if (isp->hw->mbox_irq_route) {
		isp_mbox_write32(isp, isp->hw->mbox_irq_enable1,
				 isp->hw->mbox_irq_enable1_val);
		isp_mbox_write32(isp, isp->hw->mbox_irq_enable2,
				 isp->hw->mbox_irq_enable2_val);
		isp_mbox_write32(isp, isp->hw->mbox_irq_route_off,
				 isp->hw->mbox_irq_route_val);
	}

	return 0;
}

/*
 * Shared ISP17a-style reset (T8140 and T8132). Only the reset request bit
 * is known to signal completion. The acknowledge words change between the
 * eight reads, but no condition on them is known, and the coprocessor
 * status is not polled for WFI on this sequence. Which of the writes,
 * reads and waits are required has not been established.
 */
static int isp_reset_coproc_isp17a(struct apple_isp *isp)
{
	u32 val;

	isp_coproc_write32(isp, ISP_COPROC_EDPRCR, 0x2);
	if (readl_poll_timeout(isp->coproc + ISP_COPROC_EDPRCR, val,
			       !(val & 0x2), ISP_FIRMWARE_POLL_US,
			       100 * USEC_PER_MSEC)) {
		isp_err(isp, "coprocessor reset request did not clear\n");
		return -ETIMEDOUT;
	}
	msleep(50);

	isp_coproc_write32(isp, ISP_COPROC_FABRIC_0_T8140, 0xffffffff);
	isp_coproc_write32(isp, ISP_COPROC_FABRIC_1_T8140, 0xffffffff);
	isp_coproc_write32(isp, ISP_COPROC_FABRIC_2_T8140, 0xffffffff);
	isp_coproc_write32(isp, ISP_COPROC_FABRIC_3_T8140, 0xffffffff);

	isp_coproc_write32(isp, ISP_COPROC_IRQ_MASK_0_T8140, 0xffffffff);
	isp_coproc_write32(isp, ISP_COPROC_IRQ_MASK_1_T8140, 0xffffffff);
	isp_coproc_write32(isp, ISP_COPROC_IRQ_MASK_2_T8140, 0xffffffff);
	isp_coproc_write32(isp, ISP_COPROC_IRQ_MASK_3_T8140, 0xffffffff);

	for (int i = 0; i < 8; i++) {
		isp_coproc_read32(isp, ISP_COPROC_RESET_ACK_0_T8140);
		isp_coproc_read32(isp, ISP_COPROC_RESET_ACK_1_T8140);
	}
	msleep(50);

	return 0;
}

static int isp_reset_coproc(struct apple_isp *isp)
{
	int retries;
	u32 status;
	u32 val;

	if (isp->hw->coproc_reset_isp17a)
		return isp_reset_coproc_isp17a(isp);

	isp_coproc_write32(isp, ISP_COPROC_EDPRCR, 0x2);

	isp_coproc_write32(isp, ISP_COPROC_FABRIC_0, 0xff00ff);
	isp_coproc_write32(isp, ISP_COPROC_FABRIC_1, 0xff00ff);
	isp_coproc_write32(isp, ISP_COPROC_FABRIC_2, 0xff00ff);
	isp_coproc_write32(isp, ISP_COPROC_FABRIC_3, 0xff00ff);

	isp_coproc_write32(isp, ISP_COPROC_IRQ_MASK_0, 0xffffffff);
	isp_coproc_write32(isp, ISP_COPROC_IRQ_MASK_1, 0xffffffff);
	isp_coproc_write32(isp, ISP_COPROC_IRQ_MASK_2, 0xffffffff);
	isp_coproc_write32(isp, ISP_COPROC_IRQ_MASK_3, 0xffffffff);
	isp_coproc_write32(isp, ISP_COPROC_IRQ_MASK_4, 0xffffffff);
	isp_coproc_write32(isp, ISP_COPROC_IRQ_MASK_5, 0xffffffff);

	for (retries = 0; retries < 128; retries++) {
		val = isp_coproc_read32(isp, 0x818);
		if (val == 0)
			break;
	}

	for (retries = 0; retries < 128; retries++) {
		val = isp_coproc_read32(isp, 0x81c);
		if (val == 0)
			break;
	}

	if (readl_poll_timeout(isp->coproc + ISP_COPROC_STATUS, status,
			       status & ISP_COPROC_IN_WFI,
			       ISP_FIRMWARE_POLL_US, ISP_FIRMWARE_TIMEOUT_US)) {
		isp_err(isp, "coproc NOT in WFI (status: 0x%x)\n", status);
		return -ENODEV;
	}
	isp_dbg(isp, "coproc in WFI (status: 0x%x)\n", status);

	return 0;
}

static void isp_firmware_shutdown_stage1(struct apple_isp *isp)
{
	isp_coproc_write32(isp, isp_coproc_control(isp), 0x0);

	apple_isp_power_down_domains(isp);
}

static int isp_firmware_boot_stage1(struct apple_isp *isp)
{
	int err;
	// u32 val;

	err = apple_isp_power_up_domains(isp);
	if (err < 0)
		return err;

	if (!isp->hw->skip_gpio_clock_enable)
		isp_gpio_write32(isp, ISP_GPIO_CLOCK_EN, 0x1);

#if 0
	/* This doesn't work well with system sleep */
	val = isp_gpio_read32(isp, ISP_GPIO_1);
	if (val == 0xfeedbabe) {
		err = isp_reset_coproc(isp);
		if (err < 0)
			return err;
	}
#endif

	err = isp_reset_coproc(isp);
	if (err < 0)
		goto shutdown;

	isp_gpio_write32(isp, ISP_GPIO_0, 0x0);
	isp_gpio_write32(isp, ISP_GPIO_1, 0x0);
	isp_gpio_write32(isp, ISP_GPIO_2, 0x0);
	isp_gpio_write32(isp, ISP_GPIO_3, 0x0);
	isp_gpio_write32(isp, ISP_GPIO_4, 0x0);
	isp_gpio_write32(isp, ISP_GPIO_5, 0x0);
	isp_gpio_write32(isp, ISP_GPIO_6, isp->hw->boot_mode);
	isp_gpio_write32(isp, ISP_GPIO_7, 0x0);

	isp_mbox_write32(isp, isp->hw->mbox_irq_enable, 0x0);

	isp_coproc_write32(isp, isp_coproc_control(isp), 0x0);
	isp_coproc_write32(isp, isp_coproc_control(isp), 0x10);

	/* Wait for ISP_GPIO_7 to 0x0 -> 0x8042006 */
	if (isp_gpio_wait(isp, ISP_GPIO_7, 0x8042006)) {
		isp_err(isp,
			"never received first magic number from firmware\n");
		err = -ENODEV;
		goto shutdown;
	}
	isp_dbg(isp, "got first magic number from firmware\n");

	return 0;

shutdown:
	isp_firmware_shutdown_stage1(isp);
	return err;
}

int apple_isp_alloc_firmware_surface(struct apple_isp *isp)
{
	/* These are static, so let's do it once and for all */
	isp->ipc_surf = isp_alloc_surface_vmap(isp, ISP_FIRMWARE_IPC_SIZE);
	if (!isp->ipc_surf) {
		isp_err(isp, "failed to alloc shared surface for ipc\n");
		return -ENOMEM;
	}
	dev_dbg(isp->dev, "IPC surface iova: 0x%llx\n",
		(long long)isp->ipc_surf->iova);

	isp->data_surf = isp_alloc_surface_vmap(isp, ISP_FIRMWARE_DATA_SIZE);
	if (!isp->data_surf) {
		isp_err(isp, "failed to alloc shared surface for data files\n");
		isp_free_surface(isp, isp->ipc_surf);
		return -ENOMEM;
	}
	dev_dbg(isp->dev, "Data surface iova: 0x%llx\n",
		(long long)isp->data_surf->iova);

	return 0;
}

void apple_isp_free_firmware_surface(struct apple_isp *isp)
{
	isp_free_surface(isp, isp->data_surf);
	isp_free_surface(isp, isp->ipc_surf);
}

static void isp_firmware_shutdown_stage2(struct apple_isp *isp)
{
	if (!isp->extra_surf)
		return;

	isp_free_surface(isp, isp->extra_surf);
	isp->extra_surf = NULL;
}

static void isp_write_bootargs(struct apple_isp *isp, void *virt,
			       dma_addr_t iova)
{
	struct isp_firmware_bootargs args;

	memset(&args, 0, sizeof(args));
	args.ipc_iova = isp->ipc_surf->iova;
	args.ipc_size = isp->ipc_surf->size;
	args.shared_base = isp->fw.heap_top & 0xffffffff;
	args.shared_size = 0x10000000UL - args.shared_base;
	args.extra_iova = isp->extra_surf->iova;
	args.extra_size = isp->extra_surf->size;
	args.platform_id = isp->platform_id;
	args.unk5 = 0x40;
	args.unk7 = 0x1; // 0?
	args.unk_iova1 = iova + sizeof(args) - 0xc;
	args.unk9 = 0x3;
	memcpy(virt, &args, sizeof(args));
}

static void isp_write_bootargs_h16(struct apple_isp *isp, void *virt,
				   u32 args_offset)
{
	struct isp_firmware_bootargs_h16 args;

	memset(&args, 0, sizeof(args));
	args.ipc_iova = isp->ipc_surf->iova;
	args.shared_base = isp->fw.heap_top & 0xffffffff;
	args.shared_size = isp_bootargs_shared_size(args.shared_base,
						    isp->hw->shared_size);
	args.extra_iova = isp->extra_surf->iova;
	args.extra_size = isp->extra_surf->size;
	args.platform_id = isp->platform_id;
	args.ipc_queue_size = (u64)args_offset + 1;
	args.unk_68 = 0x40;
	args.descriptor[ISP_H16_DESC_NO_OPTICAL_CARD] = 1;
	memcpy(virt, &args, sizeof(args));
}

static int isp_firmware_boot_stage2(struct apple_isp *isp)
{
	bool h16 = isp->hw->boot_args == ISP_BOOT_ARGS_H16;
	size_t args_size = h16 ? sizeof(struct isp_firmware_bootargs_h16) :
				 sizeof(struct isp_firmware_bootargs);
	size_t cmd_size = ISP_CMD_AREA_SIZE(isp_num_capmeta(isp));
	dma_addr_t args_iova, cmd_iova;
	void *args_virt, *cmd_virt;

	u32 num_ipc_chans = isp_gpio_read32(isp, ISP_GPIO_0);
	u32 args_offset = isp_gpio_read32(isp, ISP_GPIO_1);
	u32 desc_flags = isp_gpio_read32(isp, ISP_GPIO_2);
	u32 extra_size = isp_gpio_read32(isp, ISP_GPIO_3);
	isp->num_ipc_chans = num_ipc_chans;

	if (!num_ipc_chans || num_ipc_chans > ISP_IPC_MAX_CHANNELS) {
		dev_err(isp->dev, "invalid IPC channel count %u\n",
			num_ipc_chans);
		return -ENODEV;
	}

	if (isp->num_ipc_chans != 7)
		dev_warn(isp->dev, "unexpected channel count (%d)\n",
			 num_ipc_chans);

	/* Only hand the H16 descriptor to firmware that asks for it. */
	if (h16 && !(desc_flags & ISP_GPIO2_H16_DESCRIPTOR)) {
		dev_err(isp->dev,
			"firmware did not request the H16 boot descriptor (0x%x)\n",
			desc_flags);
		return -ENODEV;
	}

	/*
	 * The firmware picks the offset of the boot arguments in the IPC
	 * surface, and the command area follows them. Both have to lie
	 * inside the surface, and the command area must hold the largest
	 * buffer batch, which is more than any command needs.
	 */
	args_virt = NULL;
	cmd_virt = NULL;
	if (!check_add_overflow(isp->ipc_surf->iova, (u64)args_offset + 0x40,
				&args_iova) &&
	    !check_add_overflow(args_iova, args_size + 0x40, &cmd_iova)) {
		args_virt = apple_isp_ipc_translate(isp, args_iova, args_size);
		cmd_virt = apple_isp_ipc_translate(isp, cmd_iova, cmd_size);
	}
	if (!args_virt || !cmd_virt) {
		dev_err(isp->dev, "invalid boot arguments offset 0x%x\n",
			args_offset);
		return -EIO;
	}

	isp->extra_surf = isp_alloc_surface_vmap(isp, extra_size);
	if (!isp->extra_surf) {
		isp_err(isp, "failed to alloc surface for extra heap\n");
		return -ENOMEM;
	}

	isp->cmd_iova = cmd_iova;
	isp->ipc_boot_iova = args_iova;
	isp->ipc_boot_size = args_size + 0x40 + cmd_size;
	isp->cmd_virt = cmd_virt;

	if (h16)
		isp_write_bootargs_h16(isp, args_virt, args_offset);
	else
		isp_write_bootargs(isp, args_virt, args_iova);

	isp_gpio_write32(isp, ISP_GPIO_0, args_iova);
	/* TODO: handle this via Kconfig depends? hardware is only present on
	 *       64-bit SoCs.
	 */
	if (IS_ENABLED(CONFIG_ARCH_DMA_ADDR_T_64BIT))
		isp_gpio_write32(isp, ISP_GPIO_1, args_iova >> 32);
	dma_wmb();

	/* Wait for ISP_GPIO_7 to 0xf7fbdff9 -> 0x8042006 */
	isp_gpio_write32(isp, ISP_GPIO_7, 0xf7fbdff9);

	/*
	 * The firmware may already use the extra heap: it is freed only once
	 * the coprocessor has stopped, see isp_firmware_boot().
	 */
	if (isp_gpio_wait(isp, ISP_GPIO_7, 0x8042006)) {
		isp_err(isp,
			"never received second magic number from firmware\n");
		return -ENODEV;
	}
	isp_dbg(isp, "got second magic number from firmware\n");

	return 0;
}

static inline struct isp_channel *isp_get_chan_index(struct apple_isp *isp,
						     const char *name)
{
	for (int i = 0; i < isp->num_ipc_chans; i++) {
		if (!strcasecmp(isp->ipc_chans[i]->name, name))
			return isp->ipc_chans[i];
	}
	return NULL;
}

static void isp_free_channel_info(struct apple_isp *isp)
{
	if (!isp->ipc_chans)
		return;

	for (int i = 0; i < isp->num_ipc_chans; i++) {
		struct isp_channel *chan = isp->ipc_chans[i];
		if (!chan)
			continue;
		kfree(chan->name);
		kfree(chan);
		isp->ipc_chans[i] = NULL;
	}
	kfree(isp->ipc_chans);
	isp->ipc_chans = NULL;
}

static int isp_fill_channel_info(struct apple_isp *isp)
{
	struct isp_ipc_extent reserved[ISP_IPC_MAX_CHANNELS + 2];
	struct isp_ipc_extent ipc = {
		.iova = isp->ipc_surf->iova,
		.size = isp->ipc_surf->size,
	};
	u64 table_iova = isp_gpio_read32(isp, ISP_GPIO_0) |
			 ((u64)isp_gpio_read32(isp, ISP_GPIO_1)) << 32;
	void *table_virt = apple_isp_ipc_translate(
		isp, isp_fw_iova(isp, table_iova),
		array_size(sizeof(struct isp_chan_desc), isp->num_ipc_chans));
	int err = -EIO;

	if (!table_virt) {
		dev_err(isp->dev, "Failed to find channel table\n");
		return -EIO;
	}
	reserved[0].iova = isp_fw_iova(isp, table_iova);
	reserved[0].size = array_size(sizeof(struct isp_chan_desc),
				      isp->num_ipc_chans);
	/* Use the argument/command span already validated by stage 2. */
	reserved[1].iova = isp->ipc_boot_iova;
	reserved[1].size = isp->ipc_boot_size;
	if (!isp_ipc_extent_contains(ipc, reserved[0]) ||
	    !isp_ipc_extent_contains(ipc, reserved[1]) ||
	    isp_ipc_extents_overlap(reserved[0], reserved[1])) {
		isp_err(isp, "invalid ipc channel table or boot storage\n");
		return -EIO;
	}

	isp->ipc_chans = kcalloc(isp->num_ipc_chans,
				 sizeof(struct isp_channel *), GFP_KERNEL);
	if (!isp->ipc_chans)
		return -ENOMEM;

	for (int i = 0; i < isp->num_ipc_chans; i++) {
		struct isp_chan_desc desc;
		void *desc_virt = table_virt + (i * sizeof(desc));
		struct isp_channel *chan =
			kzalloc(sizeof(struct isp_channel), GFP_KERNEL);
		if (!chan) {
			err = -ENOMEM;
			goto out;
		}
		isp->ipc_chans[i] = chan;

		memcpy(&desc, desc_virt, sizeof(desc));
		/* The firmware does not have to NUL-terminate the name. */
		chan->name = kstrndup(desc.name, sizeof(desc.name), GFP_KERNEL);
		if (!chan->name) {
			err = -ENOMEM;
			goto out;
		}
		chan->type = desc.type;
		chan->src = desc.src;
		chan->num = desc.num;
		chan->size = (u64)desc.num * ISP_IPC_MESSAGE_SIZE;
		chan->iova = isp_fw_iova(isp, desc.iova);
		chan->cursor = 0;
		mutex_init(&chan->lock);

		if ((chan->type != ISP_IPC_CHAN_TYPE_COMMAND) &&
		    (chan->type != ISP_IPC_CHAN_TYPE_REPLY) &&
		    (chan->type != ISP_IPC_CHAN_TYPE_REPORT)) {
			isp_err(isp, "invalid ipc chan type (%d)\n",
				chan->type);
			goto out;
		}

		/* Each channel has its own doorbell bit and at least one slot. */
		if (chan->src >= ISP_IPC_MAX_CHANNELS || !chan->num) {
			isp_err(isp, "invalid ipc chan %s (src %u, num %u)\n",
				chan->name, chan->src, chan->num);
			goto out;
		}
		chan->doorbell = BIT(chan->src);
		reserved[i + 2].iova = chan->iova;
		reserved[i + 2].size = chan->size;
		if (!isp_ipc_ring_valid(ipc, reserved[i + 2], reserved, i + 2)) {
			isp_err(isp, "invalid ipc chan %s layout\n",
				chan->name);
			goto out;
		}

		chan->virt =
			apple_isp_ipc_translate(isp, chan->iova, chan->size);
		if (!chan->virt) {
			dev_err(isp->dev, "Failed to find channel buffer\n");
			goto out;
		}

		isp_dbg(isp, "chan: %s type: %d src: %d num: %d iova: %pad\n",
			chan->name, chan->type, chan->src, chan->num,
			&chan->iova);
	}

	isp->chan_tm = isp_get_chan_index(isp, "TERMINAL");
	isp->chan_io = isp_get_chan_index(isp, "IO");
	isp->chan_dg = isp_get_chan_index(isp, "DEBUG");
	isp->chan_bh = isp_get_chan_index(isp, "BUF_H2T");
	isp->chan_bt = isp_get_chan_index(isp, "BUF_T2H");
	isp->chan_sm = isp_get_chan_index(isp, "SHAREDMALLOC");
	isp->chan_it = isp_get_chan_index(isp, "IO_T2H");

	if (!isp->chan_tm || !isp->chan_io || !isp->chan_dg || !isp->chan_bh ||
	    !isp->chan_bt || !isp->chan_sm || !isp->chan_it) {
		isp_err(isp, "did not find all of the required ipc chans\n");
		goto out;
	}

	isp->chan_tm->ops = &tm_ops;
	isp->chan_sm->ops = &sm_ops;
	isp->chan_bt->ops = &bt_ops;

	return 0;
out:
	isp_free_channel_info(isp);
	return err;
}

static void isp_firmware_shutdown_stage3(struct apple_isp *isp)
{
	isp_free_channel_info(isp);
}

static int isp_firmware_boot_stage3(struct apple_isp *isp)
{
	int err;

	err = isp_fill_channel_info(isp);
	if (err < 0)
		return err;

	/* Mask the command channels to prepare for submission */
	for (int i = 0; i < isp->num_ipc_chans; i++) {
		struct isp_channel *chan = isp->ipc_chans[i];
		if (chan->type != ISP_IPC_CHAN_TYPE_COMMAND)
			continue;
		for (int j = 0; j < chan->num; j++) {
			struct isp_message msg;
			void *msg_virt = chan->virt + (j * sizeof(msg));

			memset(&msg, 0, sizeof(msg));
			msg.arg0 = ISP_IPC_FLAG_ACK;
			memcpy(msg_virt, &msg, sizeof(msg));
		}
	}
	dma_wmb();

	/* Wait for ISP_GPIO_3 to 0x8042006 -> 0x0 */
	isp_gpio_write32(isp, ISP_GPIO_3, 0x8042006);

	if (isp_gpio_wait(isp, ISP_GPIO_3, 0x0)) {
		isp_err(isp,
			"never received third magic number from firmware\n");
		isp_free_channel_info(isp);
		return -ENODEV;
	}

	isp_dbg(isp, "firmware booted!\n");

	return 0;
}

static int isp_stop_command_processor(struct apple_isp *isp)
{
#if 0
	int res = isp_cmd_stop(isp, 0);
	if (res) {
		isp_err(isp, "isp_cmd_stop() failed\n");
		return res;
	}

	/* Wait for ISP_GPIO_0 to 0xf7fbdff9 -> 0x8042006 */
	isp_gpio_write32(isp, ISP_GPIO_0, 0xf7fbdff9);

	isp_cmd_power_down(isp);
#else
	isp_gpio_write32(isp, ISP_GPIO_0, 0xf7fbdff9);

	int res = isp_cmd_suspend(isp);
	if (res) {
		isp_err(isp, "isp_cmd_suspend() failed\n");
		return res;
	}
#endif

	if (isp_gpio_wait(isp, ISP_GPIO_0, 0x8042006)) {
		isp_err(isp, "never received magic number from firmware\n");
		return -ENODEV;
	}

	return 0;
}

/* Pass the PMU base and the DSID broadcast-clear windows. */
static int isp_set_dsid_clr(struct apple_isp *isp)
{
	const struct apple_isp_hw *hw = isp->hw;
	int err;

	/*
	 * Opcode 0x3206: no SET_ISP_PMU_BASE. Count/bases come from hardware
	 * data; T8140 stays count 1, same 0x18-byte packet.
	 */
	if (hw->dsid_multi_bc) {
		u64 bases[ISP_DSID_MULTI_BC_MAX] = {
			hw->dsid_clr_base0,
			hw->dsid_clr_base1,
			hw->dsid_clr_base2,
			hw->dsid_clr_base3,
		};

		if (hw->dsid_count < 1 ||
		    hw->dsid_count > ISP_DSID_MULTI_BC_MAX)
			return -EINVAL;
		return isp_cmd_set_dsid_clr_multi_bc(isp, hw->dsid_count,
						     hw->dsid_clr_range0,
						     bases);
	}

	err = isp_cmd_set_isp_pmu_base(isp, hw->pmu_base);
	if (err)
		return err;

	if (hw->dsid_count == 1)
		return isp_cmd_set_dsid_clr_req_base(isp, hw->dsid_clr_base0,
						     hw->dsid_clr_range0);

	return isp_cmd_set_dsid_clr_req_base2(isp, hw->dsid_clr_base0,
					      hw->dsid_clr_base1,
					      hw->dsid_clr_base2,
					      hw->dsid_clr_base3,
					      hw->dsid_clr_range0,
					      hw->dsid_clr_range1,
					      hw->dsid_clr_range2,
					      hw->dsid_clr_range3);
}

static int isp_start_command_processor(struct apple_isp *isp)
{
	int err;

	err = isp_cmd_print_enable(isp, 1);
	if (err)
		return err;

	err = isp_set_dsid_clr(isp);
	if (err)
		return err;

	if (isp->hw->clock_scratch) {
		err = isp_cmd_pmp_ctrl_set(
			isp, isp->hw->clock_scratch, isp->hw->clock_base,
			isp->hw->clock_bit, isp->hw->clock_size,
			isp->hw->bandwidth_scratch, isp->hw->bandwidth_base,
			isp->hw->bandwidth_bit, isp->hw->bandwidth_size);
		if (err)
			return err;
	}

	err = isp_cmd_start(isp, 0);
	if (err)
		return err;

	/* Now we can access CISP_CMD_CH_* commands */

	return 0;
}

static void isp_collect_gc_surface(struct apple_isp *isp)
{
	struct isp_surf *tmp, *surf;

	isp->log_surf = NULL;
	isp->bt_surf = NULL;

	isp_free_released_surfaces(isp);

	list_for_each_entry_safe_reverse(surf, tmp, &isp->gc, head) {
		isp_dbg(isp, "freeing iova: %pad size: 0x%llx virt: %pS\n",
			&surf->iova, surf->size, (void *)surf->virt);
		isp_free_surface(isp, surf);
	}
}

/*
 * Stop the coprocessor and power its domains down, then release the memory
 * the firmware may still be using: its channel table, extra heap and the
 * surfaces it requested.
 */
static void isp_firmware_stop(struct apple_isp *isp)
{
	isp_firmware_shutdown_stage1(isp);

	isp_firmware_shutdown_stage3(isp);
	isp_firmware_shutdown_stage2(isp);
	isp_collect_gc_surface(isp);
}

static int isp_firmware_boot(struct apple_isp *isp)
{
	int err;

	err = isp_firmware_boot_stage1(isp);
	if (err < 0) {
		/* Stage 1 stops the coprocessor and powers it down itself. */
		isp_err(isp, "failed firmware boot stage 1: %d\n", err);
		isp_collect_gc_surface(isp);
		return err;
	}

	/* From here on the firmware runs, and may use what it was given. */
	err = isp_firmware_boot_stage2(isp);
	if (err < 0) {
		isp_err(isp, "failed firmware boot stage 2: %d\n", err);
		goto stop;
	}

	err = isp_firmware_boot_stage3(isp);
	if (err < 0) {
		isp_err(isp, "failed firmware boot stage 3: %d\n", err);
		goto stop;
	}

	err = isp_enable_irq(isp);
	if (err < 0) {
		isp_err(isp, "failed to enable interrupts: %d\n", err);
		goto stop;
	}

	err = isp_start_command_processor(isp);
	if (err < 0) {
		isp_err(isp, "failed to start command processor: %d\n", err);
		goto disable_irqs;
	}

	flush_workqueue(isp->wq);

	return 0;

disable_irqs:
	isp_disable_irq(isp);
stop:
	isp_firmware_stop(isp);
	return err;
}

static void isp_firmware_shutdown(struct apple_isp *isp)
{
	flush_workqueue(isp->wq);

	/*
	 * Stop the coprocessor before releasing any memory the firmware
	 * uses, also when it did not acknowledge the suspend request.
	 */
	if (isp_stop_command_processor(isp))
		dev_warn(isp->dev, "firmware did not suspend, stopping it\n");
	isp_disable_irq(isp);
	isp_firmware_stop(isp);
}

/*
 * Resident firmware (hw->resident_fw) is loaded by the bootloader and
 * does not start again once it has been stopped: after CISP_CMD_SUSPEND,
 * a coprocessor reset and a power cycle of its domains it never completes
 * the first handshake. It is booted once, at probe, and runs until the
 * driver is unbound, probe fails or the system hibernates, also across
 * suspend; after it has stopped the device stays unusable until the next
 * system boot.
 */
int apple_isp_firmware_boot(struct apple_isp *isp)
{
	int err;

	if (isp->hw->gen == ISP_GEN_T8132)
		return -EOPNOTSUPP;

	switch (isp->fw_state) {
	case ISP_FW_OFF:
		break;
	case ISP_FW_RUNNING:
		/* Resident firmware keeps running between streams. */
		return 0;
	case ISP_FW_DEAD:
		dev_err_ratelimited(isp->dev,
				    "firmware was stopped and cannot be restarted before the next boot\n");
		return -EIO;
	}

	/* Needs to be power cycled for IOMMU to behave correctly */
	err = pm_runtime_resume_and_get(isp->dev);
	if (err < 0) {
		dev_err(isp->dev, "failed to enable power: %d\n", err);
		return err;
	}

	err = isp_firmware_boot(isp);
	if (err) {
		dev_err(isp->dev, "failed to boot firmware: %d\n", err);
		pm_runtime_put_sync(isp->dev);
		if (isp->hw->resident_fw)
			isp->fw_state = ISP_FW_DEAD;
		return err;
	}

	isp->fw_state = ISP_FW_RUNNING;

	return 0;
}

/* The end of a stream or of the camera detection at probe */
void apple_isp_firmware_shutdown(struct apple_isp *isp)
{
	if (!isp->hw->resident_fw) {
		apple_isp_firmware_halt(isp);
		return;
	}

	/*
	 * Resident firmware keeps running, so the surfaces it allocated for
	 * the stream and freed again are not collected by a firmware stop.
	 * The stream has stopped, so free them now: wait for the interrupt
	 * thread to finish the free requests the firmware has already sent.
	 */
	if (isp->fw_state == ISP_FW_RUNNING)
		synchronize_irq(isp->irq);
	isp_free_released_surfaces(isp);
}

/* Stop the firmware; resident firmware cannot be started again. */
void apple_isp_firmware_halt(struct apple_isp *isp)
{
	/* Only a capture services the watchdog, but be sure. */
	apple_isp_wdt_stop(isp);

	if (isp->fw_state != ISP_FW_RUNNING)
		return;

	isp_firmware_shutdown(isp);
	pm_runtime_put_sync(isp->dev);
	isp->fw_state = isp->hw->resident_fw ? ISP_FW_DEAD : ISP_FW_OFF;
}
