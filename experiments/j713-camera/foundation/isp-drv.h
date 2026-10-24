// SPDX-License-Identifier: GPL-2.0-only
/* Copyright 2023 Eileen Yoon <eyn@gmx.com> */

#ifndef __ISP_DRV_H__
#define __ISP_DRV_H__

#include <linux/hrtimer.h>
#include <linux/list.h>
#include <linux/mutex.h>
#include <linux/types.h>
#include <linux/spinlock.h>

#include <drm/drm_mm.h>
#include <media/v4l2-ctrls.h>
#include <media/v4l2-device.h>
#include <media/videobuf2-core.h>
#include <media/videobuf2-v4l2.h>

#define APPLE_ISP_DEVICE_NAME "apple-isp"
#define APPLE_ISP_CARD_NAME "FaceTime HD Camera"

#define ISP_MAX_CHANNELS      6
#define ISP_IPC_MESSAGE_SIZE  64
#define ISP_IPC_FLAG_ACK      0x1
#define ISP_META_SIZE_T8103  0x4640
#define ISP_META_SIZE_T8112  0x4840
#define ISP_META_SIZE_T6031  0x4a40
#define ISP_META_SIZE_T8140  0x4d00
#define ISP_CAPTURE_META_SIZE_T8140 0x29fc0

/* used to limit the user space buffers to the buffer_pool_config */
#define ISP_MAX_BUFFERS 16

/* capture metadata buffers, for firmware that has that pool */
#define ISP_CAPMETA_BUFFERS 8

/*
 * The command area holds one command or one buffer batch at a time. A
 * batch is a 16-byte header and a 64-byte descriptor per buffer, and
 * carries at most every metadata buffer, the @capmeta capture metadata
 * buffers and one full rendered pool.
 */
#define ISP_BUFLIST_HDR_SIZE  0x10
#define ISP_BUFLIST_DESC_SIZE 0x40
#define ISP_CMD_AREA_SIZE(capmeta) \
	(ISP_BUFLIST_HDR_SIZE + ISP_BUFLIST_DESC_SIZE * \
	 (ISP_MAX_BUFFERS + (capmeta) + ISP_MAX_BUFFERS))

enum isp_generation {
	ISP_GEN_T8103,
	ISP_GEN_T8112,
	ISP_GEN_T6031,
	ISP_GEN_T8140,
	ISP_GEN_T8132,
};

/*
 * Firmware interface generation. The existing SoCs pick their command
 * layouts by apple,firmware-compat; T8140 only runs the H17 firmware, so
 * its match data selects that interface. T8132 does not inherit H17
 * command/render ABI from that match.
 */
enum isp_fw_abi {
	ISP_FW_ABI_LEGACY,
	ISP_FW_ABI_H17,
};

/* Boot-argument blob. Independent of isp_fw_abi. */
enum isp_boot_args {
	ISP_BOOT_ARGS_LEGACY,
	ISP_BOOT_ARGS_H16,
};

enum isp_fw_state {
	ISP_FW_OFF,
	ISP_FW_RUNNING,
	/* resident firmware that stopped: it cannot be started again */
	ISP_FW_DEAD,
};

enum isp_firmware_version {
	ISP_FIRMWARE_V_UNKNOWN,
	ISP_FIRMWARE_V_12_3,
	ISP_FIRMWARE_V_12_4,
	ISP_FIRMWARE_V_13_5,
	ISP_FIRMWARE_V_14_7,
	ISP_FIRMWARE_V_26_5,
};

struct isp_surf {
	struct drm_mm_node *mm;
	struct list_head head;
	u64 size;
	u64 type;
	u32 num_pages;
	struct page **pages;
	struct sg_table sgt;
	dma_addr_t iova;
	void *virt;
	refcount_t refcount;
	bool gc;
	bool submitted;
};

struct isp_message {
	u64 arg0;
	u64 arg1;
	u64 arg2;
	u64 arg3;
	u64 arg4;
	u64 arg5;
	u64 arg6;
	u64 arg7;
} __packed;
static_assert(sizeof(struct isp_message) == ISP_IPC_MESSAGE_SIZE);

struct isp_channel {
	char *name;
	u32 type;
	u32 src;
	u32 num;
	u64 size;
	dma_addr_t iova;
	void *virt;
	u32 doorbell;
	u32 cursor;
	struct mutex lock;
	struct isp_message req;
	struct isp_message rsp;
	const struct isp_chan_ops *ops;
};

struct coord {
	u32 x;
	u32 y;
};

/* MMIO window the firmware accesses at its physical address */
struct isp_mmio_window {
	u64 base;
	u64 size;
};

struct isp_preset {
	u32 index;
	struct coord input_dim;
	struct coord output_dim;
	struct coord crop_offset;
	struct coord crop_size;
};

struct apple_isp_hw {
	enum isp_generation gen;
	enum isp_fw_abi fw_abi;
	u64 pmu_base;

	int dsid_count;
	u64 dsid_clr_base0;
	u64 dsid_clr_base1;
	u64 dsid_clr_base2;
	u64 dsid_clr_base3;
	u32 dsid_clr_range0;
	u32 dsid_clr_range1;
	u32 dsid_clr_range2;
	u32 dsid_clr_range3;

	u64 clock_scratch;
	u64 clock_base;
	u8 clock_bit;
	u8 clock_size;
	u64 bandwidth_scratch;
	u64 bandwidth_base;
	u8 bandwidth_bit;
	u8 bandwidth_size;

	u32 mbox_irq_enable;
	u32 meta_size;
	bool scl1;
	bool lpdp;

	/* address bits the ISP DARTs translate, 0 for all */
	u64 fw_iova_mask;
	/* true: do not guess a mask; firmware IOVA paths stay unavailable */
	bool fw_iova_mask_unknown;

	/* doorbell block inside "mbox" instead of an "mbox2" window */
	u32 mbox2_offset;
	/* further interrupt enable and routing words, per-hardware */
	bool mbox_irq_route;
	u32 mbox_irq_enable1;
	u32 mbox_irq_enable2;
	u32 mbox_irq_route_off;
	u32 mbox_irq_enable1_val;
	u32 mbox_irq_enable2_val;
	u32 mbox_irq_route_val;
	/* coprocessor control register, 0 for ISP_COPROC_CONTROL */
	u32 coproc_control;
	/*
	 * Native Disable sibling (T8132: 0x1600040). Native writes
	 * ISP_COPROC_DISABLE_VAL_T8132 then waits
	 * BIT(ISP_COPROC_DISABLE_ACK_BIT_T8132). Not a proven clear
	 * register. Stored unused; no write/wait on this path.
	 */
	u32 coproc_disable;
	bool skip_gpio_clock_enable;
	bool coproc_reset_isp17a;
	bool dsid_multi_bc;
	enum isp_boot_args boot_args;
	/* 0: shared_size = 0x10000000 - shared_base */
	u64 shared_size;

	/* windows mapped 1:1 for the firmware */
	const struct isp_mmio_window *fw_mmio;
	unsigned int num_fw_mmio;

	/* ISP_GPIO_6 boot mode */
	u32 boot_mode;

	/* size of a capture metadata buffer, 0 for no such pool */
	u32 capture_meta_size;

	/*
	 * The firmware can be started only once per system boot, so it is
	 * started at probe and kept running while the driver is bound.
	 */
	bool resident_fw;
};

enum isp_sensor_id {
	ISP_IMX248_1820_01,
	ISP_IMX248_1822_02,
	ISP_IMX343_5221_02,
	ISP_IMX354_9251_02,
	ISP_IMX356_4820_01,
	ISP_IMX356_4820_02,
	ISP_IMX364_8720_01,
	ISP_IMX364_8723_01,
	ISP_IMX372_3820_01,
	ISP_IMX372_3820_02,
	ISP_IMX372_3820_11,
	ISP_IMX372_3820_12,
	ISP_IMX405_9720_01,
	ISP_IMX405_9721_01,
	ISP_IMX405_9723_01,
	ISP_IMX414_2520_01,
	ISP_IMX503_7820_01,
	ISP_IMX503_7820_02,
	ISP_IMX505_3921_01,
	ISP_IMX514_2820_01,
	ISP_IMX514_2820_02,
	ISP_IMX514_2820_03,
	ISP_IMX514_2820_04,
	ISP_IMX558_1921_01,
	ISP_IMX558_1922_02,
	ISP_IMX558_1925_03,
	ISP_IMX603_7920_01,
	ISP_IMX603_7920_02,
	ISP_IMX603_7921_01,
	ISP_IMX613_4920_01,
	ISP_IMX613_4920_02,
	ISP_IMX614_2921_01,
	ISP_IMX614_2921_02,
	ISP_IMX614_2922_02,
	ISP_IMX633_3622_01,
	ISP_IMX703_7721_01,
	ISP_IMX703_7722_01,
	ISP_IMX713_4721_01,
	ISP_IMX713_4722_01,
	ISP_IMX714_2022_01,
	ISP_IMX772_3721_01,
	ISP_IMX772_3721_11,
	ISP_IMX772_3722_01,
	ISP_IMX772_3723_01,
	ISP_IMX814_2123_01,
	ISP_IMX853_7622_01,
	ISP_IMX913_7523_01,
	ISP_VD56G0_6221_01,
	ISP_VD56G0_6222_01,
};

struct isp_format {
	enum isp_sensor_id id;
	u32 version;
	struct isp_preset *preset;
	unsigned int num_planes;
	u32 strides[VB2_MAX_PLANES];
	size_t plane_size[VB2_MAX_PLANES];
	size_t total_size;
};

struct apple_isp {
	struct device *dev;
	const struct apple_isp_hw *hw;
	enum isp_firmware_version fw_compat;
	u32 platform_id;
	u32 temporal_filter;
	struct isp_preset *presets;
	int num_presets;

	int num_channels;
	struct isp_format fmts[ISP_MAX_CHANNELS];
	unsigned int current_ch;

	struct video_device vdev;
	struct media_device mdev;
	struct v4l2_device v4l2_dev;
	struct v4l2_ctrl_handler ctrl_handler;
	struct v4l2_ctrl *exposure_priority;
	struct vb2_queue vbq;
	struct mutex video_lock;
	unsigned int sequence;
	bool multiplanar;
	/* capture rate in frames per second, changes under video_lock */
	unsigned int frame_rate;

	int pd_count;
	struct device **pd_dev;
	struct device_link **pd_link;
	bool pds_active;

	int irq;

	void __iomem *coproc;
	void __iomem *mbox;
	void __iomem *gpio;
	void __iomem *mbox2;
	void __iomem *wdt;
	struct hrtimer wdt_timer;
	bool wdt_running;

	struct iommu_domain *domain;
	unsigned long shift;
	struct drm_mm iovad; /* TODO iova.c can't allocate bottom-up */
	u64 iova_size; /* size of the iovad range */
	u64 fw_iova_mask; /* see isp_fw_iova() */
	struct mutex iovad_lock;

	struct isp_firmware {
		u64 heap_top;
	} fw;
	/* changes under video_lock once the video device is registered */
	enum isp_fw_state fw_state;

	struct isp_surf *ipc_surf;
	struct isp_surf *extra_surf;
	struct isp_surf *data_surf;
	struct isp_surf *log_surf;
	struct isp_surf *bt_surf;
	struct isp_surf *meta_surfs[ISP_MAX_BUFFERS];
	struct isp_surf *capmeta_surfs[ISP_CAPMETA_BUFFERS];
	/*
	 * SHAREDMALLOC surfaces: those the firmware holds on @gc, those it has
	 * freed on @gc_released until the stream that freed them has stopped.
	 */
	struct list_head gc;
	struct list_head gc_released;
	struct mutex gc_lock; /* protects @gc and @gc_released */
	struct workqueue_struct *wq;

	int num_ipc_chans;
	struct isp_channel **ipc_chans;
	struct isp_channel *chan_tm; /* TERMINAL */
	struct isp_channel *chan_io; /* IO */
	struct isp_channel *chan_dg; /* DEBUG */
	struct isp_channel *chan_bh; /* BUF_H2T */
	struct isp_channel *chan_bt; /* BUF_T2H */
	struct isp_channel *chan_sm; /* SHAREDMALLOC */
	struct isp_channel *chan_it; /* IO_T2H */

	wait_queue_head_t wait;
	dma_addr_t cmd_iova;
	void *cmd_virt;
	dma_addr_t ipc_boot_iova;
	size_t ipc_boot_size;

	unsigned long state;
	spinlock_t buf_lock;
	struct list_head bufs_pending;
	struct list_head bufs_submitted;
};

struct isp_chan_ops {
	int (*handle)(struct apple_isp *isp, struct isp_channel *chan);
};

struct isp_buffer {
	struct vb2_v4l2_buffer vb;
	struct list_head link;
	struct isp_surf surfs[VB2_MAX_PLANES];
};

#define to_isp_buffer(x) container_of((x), struct isp_buffer, vb)

enum {
	ISP_STATE_STREAMING,
	ISP_STATE_LOGGING,
	ISP_STATE_SLEEPING,
};

#define isp_dbg(isp, fmt, ...) \
	dev_dbg((isp)->dev, "[%s] " fmt, __func__, ##__VA_ARGS__)

#define isp_err(isp, fmt, ...) \
	dev_err((isp)->dev, "[%s] " fmt, __func__, ##__VA_ARGS__)

/*
 * Addresses from the firmware may carry bits above the ones the DART
 * translates (the vm-base on T8140); strip them before comparing or
 * translating.
 */
#define isp_fw_iova(isp, x)	    ((x) & (isp)->fw_iova_mask)
#define isp_get_format(isp, ch)	    (&(isp)->fmts[(ch)])
#define isp_num_capmeta(isp) \
	((isp)->hw->capture_meta_size ? ISP_CAPMETA_BUFFERS : 0)
#define isp_get_current_format(isp) (isp_get_format(isp, isp->current_ch))

#endif /* __ISP_DRV_H__ */
