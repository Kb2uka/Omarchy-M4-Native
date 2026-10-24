// SPDX-License-Identifier: GPL-2.0-only
/* Copyright 2023 Eileen Yoon <eyn@gmx.com> */

#ifndef __ISP_REGS_H__
#define __ISP_REGS_H__

#include "isp-drv.h"
#include "isp-m4-layout.h"

#define ISP_COPROC_FABRIC_0    0x738
#define ISP_COPROC_FABRIC_1    0x798
#define ISP_COPROC_FABRIC_2    0x7f8
#define ISP_COPROC_FABRIC_3    0x858

#define ISP_COPROC_RVBAR       0x1050000
#define ISP_COPROC_EDPRCR      0x1010310
#define ISP_COPROC_CONTROL     0x1400044
#define ISP_COPROC_STATUS      0x1400048

#define ISP_COPROC_IRQ_MASK_0  0x1400a00
#define ISP_COPROC_IRQ_MASK_1  0x1400a04
#define ISP_COPROC_IRQ_MASK_2  0x1400a08
#define ISP_COPROC_IRQ_MASK_3  0x1400a0c
#define ISP_COPROC_IRQ_MASK_4  0x1400a10
#define ISP_COPROC_IRQ_MASK_5  0x1400a14

#define ISP_COPROC_CONTROL_T8140 0x1600044

/*
 * ISP17a (T8140) reset. The fourth fabric word is at 0xa50, off the 0x100
 * stride of the other three, in the observed sequence.
 */
#define ISP_COPROC_FABRIC_0_T8140    0x748
#define ISP_COPROC_FABRIC_1_T8140    0x848
#define ISP_COPROC_FABRIC_2_T8140    0x948
#define ISP_COPROC_FABRIC_3_T8140    0xa50
#define ISP_COPROC_IRQ_MASK_0_T8140  0x1600a00
#define ISP_COPROC_IRQ_MASK_1_T8140  0x1600a04
#define ISP_COPROC_IRQ_MASK_2_T8140  0x1600a08
#define ISP_COPROC_IRQ_MASK_3_T8140  0x1600a0c
#define ISP_COPROC_RESET_ACK_0_T8140 0x1600818
#define ISP_COPROC_RESET_ACK_1_T8140 0x160081c

#define ISP_MBOX_IRQ_INTERRUPT    0x00
#define ISP_MBOX_IRQ_ENABLE       0x04
#define ISP_MBOX_IRQ_ENABLE_T6031 0x08
#define ISP_MBOX2_IRQ_DOORBELL    0x00
#define ISP_MBOX2_IRQ_ACK         0x0c
#define ISP_MBOX2_SIZE            0x10

/*
 * ISP17a (T8140) interrupt enable names used by the hardware tables.
 * Offsets, route words, enable values, mbox2, and the T8132 coproc
 * control/disable constants are defined once in isp-m4-layout.h.
 */
#define ISP_MBOX_IRQ_ENABLE1_T8140     ISP_MBOX_IRQ_ENABLE1_OFF
#define ISP_MBOX_IRQ_ENABLE2_T8140     ISP_MBOX_IRQ_ENABLE2_OFF
#define ISP_MBOX_IRQ_ENABLE1_T8140_VAL ISP_MBOX_IRQ_ENABLE1_VAL
#define ISP_MBOX_IRQ_ROUTE_T8140_VAL   ISP_MBOX_IRQ_ROUTE_VAL

/* capture watchdog ("wdt") */
#define ISP_WDT_KICK	   0x00
#define ISP_WDT_RELOAD	   0x08
#define ISP_WDT_CLEAR	   0x0c
#define ISP_WDT_RELOAD_VAL 0x244140
#define ISP_WDT_PERIOD_NS  (NSEC_PER_SEC / 30)

#define ISP_GPIO_0	       0x00
#define ISP_GPIO_1	       0x04
#define ISP_GPIO_2	       0x08
#define ISP_GPIO_3	       0x0c
#define ISP_GPIO_4	       0x10
#define ISP_GPIO_5	       0x14
#define ISP_GPIO_6	       0x18
#define ISP_GPIO_7	       0x1c
#define ISP_GPIO_CLOCK_EN      0x20

static inline u32 isp_mbox_read32(struct apple_isp *isp, u32 reg)
{
	return readl(isp->mbox + reg);
}

static inline void isp_mbox_write32(struct apple_isp *isp, u32 reg, u32 val)
{
	writel(val, isp->mbox + reg);
}

static inline void isp_mbox2_write32(struct apple_isp *isp, u32 reg, u32 val)
{
	writel(val, isp->mbox2 + reg);
}

#endif /* __ISP_REGS_H__ */
