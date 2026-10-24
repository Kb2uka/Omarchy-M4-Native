// SPDX-License-Identifier: GPL-2.0-only
/* Copyright 2023 Eileen Yoon <eyn@gmx.com> */

#include <linux/firmware.h>
#include <linux/unaligned.h>

#include "isp-cam.h"
#include "isp-cmd.h"
#include "isp-fw.h"
#include "isp-iommu.h"
#include "isp-regs.h"

#define ISP_MAX_PRESETS 32

struct isp_setfile {
	u32 version;
	u32 magic;
	const char *path;
	size_t size;
	/* bytes present in the file if fewer than size; the rest is zero */
	size_t file_size;
};

// clang-format off
static const struct isp_setfile isp_setfiles[] = {
	[ISP_IMX248_1820_01] = {0x248, 0x18200103, "apple/isp_1820_01XX.dat", 0x442c},
	[ISP_IMX248_1822_02] = {0x248, 0x18220201, "apple/isp_1822_02XX.dat", 0x442c},
	[ISP_IMX343_5221_02] = {0x343, 0x52210211, "apple/isp_5221_02XX.dat", 0x4870},
	[ISP_IMX354_9251_02] = {0x354, 0x92510208, "apple/isp_9251_02XX.dat", 0xa5ec},
	[ISP_IMX356_4820_01] = {0x356, 0x48200107, "apple/isp_4820_01XX.dat", 0x9324},
	[ISP_IMX356_4820_02] = {0x356, 0x48200206, "apple/isp_4820_02XX.dat", 0x9324},
	[ISP_IMX364_8720_01] = {0x364, 0x87200103, "apple/isp_8720_01XX.dat", 0x36ac},
	[ISP_IMX364_8723_01] = {0x364, 0x87230101, "apple/isp_8723_01XX.dat", 0x361c},
	[ISP_IMX372_3820_01] = {0x372, 0x38200108, "apple/isp_3820_01XX.dat", 0xfdb0},
	[ISP_IMX372_3820_02] = {0x372, 0x38200205, "apple/isp_3820_02XX.dat", 0xfdb0},
	[ISP_IMX372_3820_11] = {0x372, 0x38201104, "apple/isp_3820_11XX.dat", 0xfdb0},
	[ISP_IMX372_3820_12] = {0x372, 0x38201204, "apple/isp_3820_12XX.dat", 0xfdb0},
	[ISP_IMX405_9720_01] = {0x405, 0x97200102, "apple/isp_9720_01XX.dat", 0x92c8},
	[ISP_IMX405_9721_01] = {0x405, 0x97210102, "apple/isp_9721_01XX.dat", 0x9818},
	[ISP_IMX405_9723_01] = {0x405, 0x97230101, "apple/isp_9723_01XX.dat", 0x92c8},
	[ISP_IMX414_2520_01] = {0x414, 0x25200102, "apple/isp_2520_01XX.dat", 0xa444},
	[ISP_IMX503_7820_01] = {0x503, 0x78200109, "apple/isp_7820_01XX.dat", 0xb268},
	[ISP_IMX503_7820_02] = {0x503, 0x78200206, "apple/isp_7820_02XX.dat", 0xb268},
	[ISP_IMX505_3921_01] = {0x505, 0x39210102, "apple/isp_3921_01XX.dat", 0x89b0},
	[ISP_IMX514_2820_01] = {0x514, 0x28200108, "apple/isp_2820_01XX.dat", 0xa198},
	[ISP_IMX514_2820_02] = {0x514, 0x28200205, "apple/isp_2820_02XX.dat", 0xa198},
	[ISP_IMX514_2820_03] = {0x514, 0x28200305, "apple/isp_2820_03XX.dat", 0xa198},
	[ISP_IMX514_2820_04] = {0x514, 0x28200405, "apple/isp_2820_04XX.dat", 0xa198},
	[ISP_IMX558_1921_01] = {0x558, 0x19210106, "apple/isp_1921_01XX.dat", 0xad40},
	[ISP_IMX558_1922_02] = {0x558, 0x19220201, "apple/isp_1922_02XX.dat", 0xad40},
	[ISP_IMX558_1925_03] = {0x558, 0x19250306, "apple/isp_1925_03XX.dat", 0xcbc0, 0xcba6},
	[ISP_IMX603_7920_01] = {0x603, 0x79200109, "apple/isp_7920_01XX.dat", 0xad2c},
	[ISP_IMX603_7920_02] = {0x603, 0x79200205, "apple/isp_7920_02XX.dat", 0xad2c},
	[ISP_IMX603_7921_01] = {0x603, 0x79210104, "apple/isp_7921_01XX.dat", 0xad90},
	[ISP_IMX613_4920_01] = {0x613, 0x49200108, "apple/isp_4920_01XX.dat", 0x9324},
	[ISP_IMX613_4920_02] = {0x613, 0x49200204, "apple/isp_4920_02XX.dat", 0x9324},
	[ISP_IMX614_2921_01] = {0x614, 0x29210107, "apple/isp_2921_01XX.dat", 0xed6c},
	[ISP_IMX614_2921_02] = {0x614, 0x29210202, "apple/isp_2921_02XX.dat", 0xed6c},
	[ISP_IMX614_2922_02] = {0x614, 0x29220201, "apple/isp_2922_02XX.dat", 0xed6c},
	[ISP_IMX633_3622_01] = {0x633, 0x36220111, "apple/isp_3622_01XX.dat", 0x100d4},
	[ISP_IMX703_7721_01] = {0x703, 0x77210106, "apple/isp_7721_01XX.dat", 0x936c},
	[ISP_IMX703_7722_01] = {0x703, 0x77220106, "apple/isp_7722_01XX.dat", 0xac20},
	[ISP_IMX713_4721_01] = {0x713, 0x47210107, "apple/isp_4721_01XX.dat", 0x936c},
	[ISP_IMX713_4722_01] = {0x713, 0x47220109, "apple/isp_4722_01XX.dat", 0x9218},
	[ISP_IMX714_2022_01] = {0x714, 0x20220107, "apple/isp_2022_01XX.dat", 0xa198},
	[ISP_IMX772_3721_01] = {0x772, 0x37210106, "apple/isp_3721_01XX.dat", 0xfdf8},
	[ISP_IMX772_3721_11] = {0x772, 0x37211106, "apple/isp_3721_11XX.dat", 0xfe14},
	[ISP_IMX772_3722_01] = {0x772, 0x37220104, "apple/isp_3722_01XX.dat", 0xfca4},
	[ISP_IMX772_3723_01] = {0x772, 0x37230106, "apple/isp_3723_01XX.dat", 0xfca4},
	[ISP_IMX814_2123_01] = {0x814, 0x21230101, "apple/isp_2123_01XX.dat", 0xed54},
	[ISP_IMX853_7622_01] = {0x853, 0x76220112, "apple/isp_7622_01XX.dat", 0x247f8},
	[ISP_IMX913_7523_01] = {0x913, 0x75230107, "apple/isp_7523_01XX.dat", 0x247f8},
	[ISP_VD56G0_6221_01] = {0xd56, 0x62210102, "apple/isp_6221_01XX.dat", 0x1b80},
	[ISP_VD56G0_6222_01] = {0xd56, 0x62220102, "apple/isp_6222_01XX.dat", 0x1b80},
};
// clang-format on

static int isp_ch_get_sensor_id(struct apple_isp *isp, u32 ch)
{
	struct isp_format *fmt = isp_get_format(isp, ch);
	enum isp_sensor_id id;
	int err = 0;

	/* TODO need more datapoints to figure out the sub-versions
	 * Defaulting to 1st release for now, the calib files aren't too different.
	 */
	switch (fmt->version) {
	case 0x248:
		id = ISP_IMX248_1820_01;
		break;
	case 0x343:
		id = ISP_IMX343_5221_02;
		break;
	case 0x354:
		id = ISP_IMX354_9251_02;
		break;
	case 0x356:
		id = ISP_IMX356_4820_01;
		break;
	case 0x364:
		id = ISP_IMX364_8720_01;
		break;
	case 0x372:
		id = ISP_IMX372_3820_01;
		break;
	case 0x405:
		id = ISP_IMX405_9720_01;
		break;
	case 0x414:
		id = ISP_IMX414_2520_01;
		break;
	case 0x503:
		id = ISP_IMX503_7820_01;
		break;
	case 0x505:
		id = ISP_IMX505_3921_01;
		break;
	case 0x514:
		id = ISP_IMX514_2820_01;
		break;
	case 0x558:
		/* The H17 firmware only parses header version 0x21 setfiles. */
		if (isp->hw->fw_abi == ISP_FW_ABI_H17)
			id = ISP_IMX558_1925_03;
		else
			id = ISP_IMX558_1921_01;
		break;
	case 0x603:
		id = ISP_IMX603_7920_01;
		break;
	case 0x613:
		id = ISP_IMX613_4920_01;
		break;
	case 0x614:
		id = ISP_IMX614_2921_01;
		break;
	case 0x633:
		id = ISP_IMX633_3622_01;
		break;
	case 0x703:
		id = ISP_IMX703_7721_01;
		break;
	case 0x713:
		id = ISP_IMX713_4721_01;
		break;
	case 0x714:
		id = ISP_IMX714_2022_01;
		break;
	case 0x772:
		id = ISP_IMX772_3721_01;
		break;
	case 0x814:
		id = ISP_IMX814_2123_01;
		break;
	case 0x853:
		id = ISP_IMX853_7622_01;
		break;
	case 0x913:
		id = ISP_IMX913_7523_01;
		break;
	case 0xd56:
		id = ISP_VD56G0_6221_01;
		break;
	default:
		err = -EINVAL;
		break;
	}

	if (err)
		dev_err(isp->dev, "invalid sensor version: 0x%x\n",
			fmt->version);
	else
		fmt->id = id;

	return err;
}

/*
 * The presets come from the device tree. The firmware numbers its own
 * presets from zero, so a config index at or beyond the count it reports
 * names a preset it does not have, and selecting it can only fail.
 */
static int isp_check_presets(struct apple_isp *isp, u32 num_fw_presets)
{
	int num = 0;

	/* Nothing to check against */
	if (!num_fw_presets)
		return 0;

	for (int i = 0; i < isp->num_presets; i++) {
		struct isp_preset *preset = &isp->presets[i];

		if (preset->index >= num_fw_presets) {
			dev_warn(isp->dev,
				 "ignoring %ux%u preset: config %u, firmware has %u\n",
				 preset->output_dim.x, preset->output_dim.y,
				 preset->index, num_fw_presets);
			continue;
		}

		isp->presets[num++] = *preset;
	}

	if (!num) {
		dev_err(isp->dev, "no usable sensor presets\n");
		return -ENODEV;
	}

	isp->num_presets = num;

	return 0;
}

static int isp_ch_cache_sensor_info(struct apple_isp *isp, u32 ch)
{
	struct isp_format *fmt = isp_get_format(isp, ch);
	int err = 0;

	struct cmd_ch_info *args; /* Too big to allocate on stack */
	args = kzalloc(sizeof(*args), GFP_KERNEL);
	if (!args)
		return -ENOMEM;

	err = isp_cmd_ch_info_get(isp, ch, args);
	if (err)
		goto exit;

	dev_dbg(isp->dev, "found sensor %x on ch %d\n", args->version, ch);

	/* The metadata buffers are allocated for the sizes the driver knows. */
	if (isp->hw->fw_abi == ISP_FW_ABI_H17 &&
	    (args->unk_68 != isp->hw->meta_size ||
	     args->unk_78 != isp->hw->capture_meta_size)) {
		dev_err(isp->dev,
			"metadata sizes 0x%x/0x%x, expected 0x%x/0x%x\n",
			args->unk_68, args->unk_78, isp->hw->meta_size,
			isp->hw->capture_meta_size);
		err = -ENODEV;
		goto exit;
	}

	fmt->version = args->version;

	err = isp_ch_get_sensor_id(isp, ch);
	if (err ||
	    (fmt->id != ISP_IMX248_1820_01 && fmt->id != ISP_IMX558_1921_01 &&
	     fmt->id != ISP_IMX558_1925_03 && fmt->id != ISP_IMX364_8720_01)) {
		dev_err(isp->dev,
			"ch %d: unsupported sensor. Please file a bug report with hardware info & dmesg trace.\n",
			ch);
		err = -ENODEV;
		goto exit;
	}

	err = isp_check_presets(isp, args->num_presets);

exit:
	kfree(args);

	return err;
}

static int isp_detect_camera(struct apple_isp *isp)
{
	int err;

	struct cmd_config_get args;
	memset(&args, 0, sizeof(args));

	err = isp_cmd_config_get(isp, &args);
	if (err)
		return err;

	if (!args.num_channels) {
		dev_err(isp->dev, "did not detect any channels\n");
		return -ENODEV;
	}

	if (args.num_channels > ISP_MAX_CHANNELS) {
		dev_warn(isp->dev, "found %d channels when maximum is %d\n",
			 args.num_channels, ISP_MAX_CHANNELS);
		args.num_channels = ISP_MAX_CHANNELS;
	}

	if (args.num_channels > 1) {
		dev_warn(
			isp->dev,
			"warning: driver doesn't support multiple channels. Please file a bug report with hardware info & dmesg trace.\n");
	}

	isp->num_channels = args.num_channels;
	isp->current_ch = 0;

	err = isp_ch_cache_sensor_info(isp, isp->current_ch);
	if (err) {
		dev_err(isp->dev, "failed to cache sensor info\n");
		return err;
	}

	return 0;
}

int apple_isp_detect_camera(struct apple_isp *isp)
{
	int err;

	if (isp->hw->gen == ISP_GEN_T8132)
		return -EOPNOTSUPP;

	/* RPM must be enabled prior to calling this */
	err = apple_isp_firmware_boot(isp);
	if (err) {
		dev_err(isp->dev,
			"failed to boot firmware for initial sensor detection: %d\n",
			err);
		/* Resident firmware would fail a deferred retry the same way. */
		return isp->hw->resident_fw ? err : -EPROBE_DEFER;
	}

	err = isp_detect_camera(isp);

	/* Resident firmware stays up; the channel is set up per stream. */
	if (isp->hw->resident_fw) {
		if (err)
			apple_isp_firmware_halt(isp);
		return err;
	}

	isp_cmd_flicker_sensor_set(isp, 0);

	isp_cmd_ch_stop(isp, 0);
	isp_cmd_ch_buffer_return(isp, isp->current_ch);

	apple_isp_firmware_shutdown(isp);

	return err;
}

static int isp_ch_load_setfile(struct apple_isp *isp, u32 ch)
{
	struct isp_format *fmt = isp_get_format(isp, ch);
	const struct isp_setfile *setfile = &isp_setfiles[fmt->id];
	size_t len = setfile->file_size ?: setfile->size;
	const struct firmware *fw;
	u32 magic;
	int err;

	if (WARN_ON_ONCE(setfile->size > isp->data_surf->size))
		return -ENOSPC;

	err = firmware_request_nowarn(&fw, setfile->path, isp->dev);
	if (err) {
		dev_warn_once(isp->dev,
			      "setfile '%s' not loaded (%d), streaming without calibration\n",
			      setfile->path, err);
		return err;
	}

	if (fw->size < len) {
		dev_err(isp->dev, "setfile '%s' too small (0x%zx/0x%zx)\n",
			setfile->path, fw->size, len);
		release_firmware(fw);
		return -EINVAL;
	}

	magic = get_unaligned_be32(fw->data);
	if (magic != setfile->magic) {
		dev_err(isp->dev, "setfile '%s' has magic 0x%08x, expected 0x%08x\n",
			setfile->path, magic, setfile->magic);
		release_firmware(fw);
		return -EINVAL;
	}

	memcpy(isp->data_surf->virt, fw->data, len);
	memset(isp->data_surf->virt + len, 0, setfile->size - len);
	release_firmware(fw);

	return isp_cmd_ch_set_file_load(isp, ch, isp->data_surf->iova,
					setfile->size);
}

static int isp_ch_configure_frame_rate(struct apple_isp *isp, u32 ch)
{
	u32 rate = isp->frame_rate * ISP_FRAME_RATE_SCALE;
	u32 min_rate = rate;
	int err;

	/*
	 * Auto exposure lengthens the frame time in low light only if it may
	 * vary the frame rate; otherwise it holds the rate and raises the gain.
	 */
	if (v4l2_ctrl_g_ctrl(isp->exposure_priority))
		min_rate = ISP_FRAME_RATE_MIN * ISP_FRAME_RATE_SCALE;

	err = isp_cmd_ch_ae_frame_rate_max_set(isp, ch, rate);
	if (err)
		return err;

	return isp_cmd_ch_ae_frame_rate_min_set(isp, ch, min_rate);
}

static int isp_ch_configure_pools(struct apple_isp *isp, u32 ch)
{
	struct isp_format *fmt = isp_get_format(isp, ch);
	int err;

	err = isp_cmd_ch_buffer_pool_config_set(isp, ch, CISP_POOL_TYPE_META);
	if (err)
		return err;

	err = isp_cmd_ch_buffer_pool_config_set(isp, ch,
						CISP_POOL_TYPE_META_CAPTURE);
	if (err)
		return err;

	/* The H17 firmware also takes the geometry of the output buffers. */
	if (isp->hw->fw_abi != ISP_FW_ABI_H17)
		return 0;

	return isp_cmd_ch_buffer_pool_config_set_rendered(isp, ch,
							  ISP_MAX_BUFFERS,
							  fmt->plane_size[0],
							  fmt->strides[0],
							  fmt->plane_size[1],
							  fmt->strides[1]);
}

static int isp_ch_configure_capture(struct apple_isp *isp, u32 ch)
{
	struct isp_format *fmt = isp_get_format(isp, ch);
	bool h17 = isp->hw->fw_abi == ISP_FW_ABI_H17;
	int err;

	isp_cmd_flicker_sensor_set(isp, 0);

	/*
	 * The setfile isn't requisite but then we don't get calibration. If
	 * loading it was interrupted by a signal, give up on the stream.
	 */
	err = isp_ch_load_setfile(isp, ch);
	if (err == -EINTR)
		return err;

	if (isp->hw->lpdp) {
		err = isp_cmd_ch_lpdp_hs_receiver_tuning_set(isp, ch, 1, 15);
		if (err)
			return err;
	}

	err = isp_cmd_ch_sbs_enable(isp, ch, 1);
	if (err)
		return err;

	err = isp_cmd_ch_camera_config_select(isp, ch, fmt->preset->index);
	if (err)
		return err;

	err = isp_cmd_ch_buffer_recycle_mode_set(
		isp, ch, CISP_BUFFER_RECYCLE_MODE_EMPTY_ONLY);
	if (err)
		return err;

	err = isp_cmd_ch_buffer_recycle_start(isp, ch);
	if (err)
		return err;

	err = isp_cmd_ch_crop_set(isp, ch, fmt->preset->crop_offset.x,
				  fmt->preset->crop_offset.y,
				  fmt->preset->crop_size.x,
				  fmt->preset->crop_size.y);
	if (err)
		return err;

	err = isp_cmd_ch_output_config_set(isp, ch, fmt->preset->output_dim.x,
					   fmt->preset->output_dim.y,
					   fmt->strides, CISP_COLORSPACE_REC709,
					   CISP_OUTPUT_FORMAT_YUV_2PLANE);
	if (err)
		return err;

	/*
	 * The H17 firmware takes the frame rate limits and the buffer pools
	 * before the preview stream is enabled. It also needs its local raw
	 * buffers enabled: the sensor data takes the ISP-local raw path, and
	 * without them the firmware waits for a raw pool from the host.
	 */
	if (h17) {
		err = isp_ch_configure_frame_rate(isp, ch);
		if (err)
			return err;

		err = isp_ch_configure_pools(isp, ch);
		if (err)
			return err;

		err = isp_cmd_ch_local_raw_buffer_enable(isp, ch, 1);
		if (err)
			return err;
	}

	err = isp_cmd_ch_preview_stream_set(isp, ch, 1);
	if (err)
		return err;

	err = isp_cmd_ch_cnr_start(isp, ch);
	if (err)
		return err;

	err = isp_cmd_ch_mbnr_enable(isp, ch, 0, ISP_MBNR_MODE_ENABLE, 1);
	if (err)
		return err;

	/* The H17 firmware faults on this face-detection metering setup. */
	if (!h17) {
		err = isp_cmd_apple_ch_ae_fd_scene_metering_config_set(isp,
								       ch);
		if (err)
			return err;
	}

	err = isp_cmd_apple_ch_ae_metering_mode_set(isp, ch, 3);
	if (err)
		return err;

	err = isp_cmd_ch_ae_stability_set(isp, ch, 32);
	if (err)
		return err;

	err = isp_cmd_ch_ae_stability_to_stable_set(isp, ch, 20);
	if (err)
		return err;

	err = isp_cmd_ch_sif_pixel_format_set(isp, ch);
	if (err)
		return err;

	if (!h17) {
		err = isp_ch_configure_frame_rate(isp, ch);
		if (err)
			return err;
	}

	err = isp_cmd_apple_ch_temporal_filter_start(isp, ch, isp->temporal_filter);
	if (err)
		return err;

	err = isp_cmd_apple_ch_motion_history_start(isp, ch);
	if (err)
		return err;

	err = isp_cmd_apple_ch_temporal_filter_enable(isp, ch);
	if (err)
		return err;

	if (h17)
		return isp_cmd_ch_master_slave_sync_mode_set(isp, ch, 0);

	return isp_ch_configure_pools(isp, ch);
}

static int isp_configure_capture(struct apple_isp *isp)
{
	return isp_ch_configure_capture(isp, isp->current_ch);
}

int apple_isp_start_camera(struct apple_isp *isp)
{
	int err;

	if (isp->hw->gen == ISP_GEN_T8132)
		return -EOPNOTSUPP;

	err = apple_isp_firmware_boot(isp);
	if (err < 0) {
		dev_err(isp->dev, "failed to boot firmware: %d\n", err);
		return err;
	}

	err = isp_configure_capture(isp);
	if (err) {
		dev_err(isp->dev, "failed to configure capture: %d\n", err);
		apple_isp_firmware_shutdown(isp);
		return err;
	}

	return 0;
}

void apple_isp_stop_camera(struct apple_isp *isp)
{
	apple_isp_firmware_shutdown(isp);
}

/*
 * Capture watchdog. While it is not serviced, the ISP firmware replaces
 * every frame with a flat fill. The firmware expects it to be serviced
 * only while a camera-in-use indicator is shown, which makes it a privacy
 * interlock. That indicator is drawn by the display pipeline for secure
 * firmware that Linux does not run, so the driver services the watchdog
 * whenever it captures, with the register sequence and rate observed on
 * the running hardware.
 */
static void apple_isp_wdt_kick(struct apple_isp *isp)
{
	writel(0, isp->wdt + ISP_WDT_CLEAR);
	writel(ISP_WDT_RELOAD_VAL, isp->wdt + ISP_WDT_RELOAD);
	writel(1, isp->wdt + ISP_WDT_KICK);
}

static enum hrtimer_restart apple_isp_wdt_timer(struct hrtimer *timer)
{
	struct apple_isp *isp = container_of(timer, struct apple_isp,
					     wdt_timer);

	apple_isp_wdt_kick(isp);
	hrtimer_forward_now(timer, ns_to_ktime(ISP_WDT_PERIOD_NS));

	return HRTIMER_RESTART;
}

void apple_isp_wdt_init(struct apple_isp *isp)
{
	hrtimer_setup(&isp->wdt_timer, apple_isp_wdt_timer, CLOCK_MONOTONIC,
		      HRTIMER_MODE_REL);
}

static void apple_isp_wdt_start(struct apple_isp *isp)
{
	if (!isp->wdt || isp->wdt_running)
		return;

	isp->wdt_running = true;
	apple_isp_wdt_kick(isp);
	hrtimer_start(&isp->wdt_timer, ns_to_ktime(ISP_WDT_PERIOD_NS),
		      HRTIMER_MODE_REL);
}

void apple_isp_wdt_stop(struct apple_isp *isp)
{
	if (!isp->wdt_running)
		return;

	hrtimer_cancel(&isp->wdt_timer);
	isp->wdt_running = false;
}

int apple_isp_start_capture(struct apple_isp *isp)
{
	int err;

	/* Serviced from before the channel starts... */
	apple_isp_wdt_start(isp);

	err = isp_cmd_ch_start(isp, 0); // TODO channel mask
	if (err)
		apple_isp_wdt_stop(isp);

	return err;
}

void apple_isp_stop_capture(struct apple_isp *isp)
{
	isp_cmd_ch_stop(isp, 0); // TODO channel mask
	isp_cmd_ch_buffer_return(isp, isp->current_ch);

	/* ... until its buffers are back. */
	apple_isp_wdt_stop(isp);
}
