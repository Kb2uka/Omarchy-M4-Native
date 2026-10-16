// SPDX-License-Identifier: GPL-2.0-only OR MIT
/*
 * CPU idle for the Apple M4 (T8132), issue #65.
 *
 * Based on drivers/cpuidle/cpuidle-apple.c (Copyright The Asahi Linux
 * Contributors), which leaves T8132 out.
 *
 * On M1 to M3 a plain WFI only clock-gates the core; the core powers down only
 * when the kernel sets the ok-to-power-down bits in s3_5_c15_c5_0 (CYC_OVRD),
 * and cpuidle-apple's "CPU PD" state saves what that loses first. On M4 iBoot
 * locks those bits: writing the register raises an SError, and every WFI
 * powers the core down and zeroes its general registers. That is why the
 * kernel boots with idle=nop, which never executes WFI.
 *
 * So the only usable idle state on M4 is cpuidle-apple's CPU PD sequence
 * without the register writes: cpu_pm_enter() saves FP/SIMD and the other
 * per-CPU state its notifiers own, the stack keeps the callee-saved general
 * registers, and the core waits in WFI until ISR_EL1 shows a pending
 * interrupt. State 0 does what idle=nop does today (return at once), so a
 * CPU-latency request too tight for state 1 keeps today's behaviour.
 *
 * Nothing here writes a system or MMIO register. idle=nop stays on the
 * command line: it covers boot until this module loads, and every CPU this
 * driver does not serve.
 */
#include <linux/cpu.h>
#include <linux/cpu_pm.h>
#include <linux/cpuidle.h>
#include <linux/cpumask.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/smp.h>

enum {
	J713_IDLE_SPIN,
	J713_IDLE_PD,
	J713_IDLE_COUNT,
};

static char *cpus = "all";
module_param(cpus, charp, 0444);
MODULE_PARM_DESC(cpus, "CPUs to serve, as a cpulist (default all)");

static uint exit_latency_us = 10;
module_param(exit_latency_us, uint, 0444);
MODULE_PARM_DESC(exit_latency_us, "Exit latency of the power-down state in microseconds");

static uint target_residency_us = 100;
module_param(target_residency_us, uint, 0444);
MODULE_PARM_DESC(target_residency_us, "Shortest idle period worth powering down for, in microseconds");

/*
 * cpuidle-apple's apple_cpu_deep_wfi without the CYC_OVRD writes. x0-x17
 * are caller-saved, so the call itself already gives them up; x18-x30 and
 * the frame are kept on the stack, which M4 WFI does not touch.
 */
asm(
	".pushsection .cpuidle.text, \"ax\"\n"
	".type j713_cpu_wfi_saved, @function\n"
	"j713_cpu_wfi_saved:\n"
		"hint 34\n" /* bti c */
		"str x30, [sp, #-16]!\n"
		"stp x28, x29, [sp, #-16]!\n"
		"stp x26, x27, [sp, #-16]!\n"
		"stp x24, x25, [sp, #-16]!\n"
		"stp x22, x23, [sp, #-16]!\n"
		"stp x20, x21, [sp, #-16]!\n"
		"stp x18, x19, [sp, #-16]!\n"
	"1:\n"
		"dsb sy\n"
		"wfi\n"
		"mrs x0, ISR_EL1\n"
		"cbz x0, 1b\n"
		"ldp x18, x19, [sp], #16\n"
		"ldp x20, x21, [sp], #16\n"
		"ldp x22, x23, [sp], #16\n"
		"ldp x24, x25, [sp], #16\n"
		"ldp x26, x27, [sp], #16\n"
		"ldp x28, x29, [sp], #16\n"
		"ldr x30, [sp], #16\n"
		"ret\n"
	".size j713_cpu_wfi_saved, . - j713_cpu_wfi_saved\n"
	".popsection\n"
);

void j713_cpu_wfi_saved(void);

/* What idle=nop does: return, and let the idle loop check for work again. */
static __cpuidle int j713_enter_spin(struct cpuidle_device *dev,
				     struct cpuidle_driver *drv, int index)
{
	return index;
}

static __cpuidle int j713_enter_pd(struct cpuidle_device *dev,
				   struct cpuidle_driver *drv, int index)
{
	if (cpu_pm_enter())
		return -1;

	ct_cpuidle_enter();
	j713_cpu_wfi_saved();
	ct_cpuidle_exit();

	cpu_pm_exit();

	return index;
}

static struct cpumask served;

static struct cpuidle_driver j713_idle_driver = {
	.name = "j713_idle",
	.owner = THIS_MODULE,
	.states = {
		[J713_IDLE_SPIN] = {
			.enter			= j713_enter_spin,
			.enter_s2idle		= j713_enter_spin,
			.exit_latency		= 0,
			.target_residency	= 0,
			.power_usage		= UINT_MAX,
			.name			= "SPIN",
			.desc			= "idle=nop loop, core running",
		},
		[J713_IDLE_PD] = {
			.enter			= j713_enter_pd,
			.enter_s2idle		= j713_enter_pd,
			.power_usage		= 0,
			.name			= "CPU PD",
			.desc			= "M4 WFI, core powered down",
			.flags			= CPUIDLE_FLAG_RCU_IDLE,
		},
	},
	.safe_state_index = J713_IDLE_SPIN,
	.state_count = J713_IDLE_COUNT,
	.cpumask = &served,
};

static int __init j713_cpuidle_init(void)
{
	struct cpuidle_state *pd = &j713_idle_driver.states[J713_IDLE_PD];
	int ret;

	if (!of_machine_is_compatible("apple,t8132"))
		return -ENODEV;

	if (!strcmp(cpus, "all"))
		cpumask_copy(&served, cpu_possible_mask);
	else if (cpulist_parse(cpus, &served))
		return -EINVAL;
	cpumask_and(&served, &served, cpu_possible_mask);
	if (cpumask_empty(&served))
		return -EINVAL;
	if (!exit_latency_us || target_residency_us < exit_latency_us)
		return -EINVAL;

	pd->exit_latency = exit_latency_us;
	pd->target_residency = target_residency_us;

	ret = cpuidle_register(&j713_idle_driver, NULL);
	if (ret)
		return ret;

	pr_info("j713_cpuidle: CPU PD (exit %u us, residency %u us) on CPUs %*pbl\n",
		exit_latency_us, target_residency_us, cpumask_pr_args(&served));
	return 0;
}

static void __exit j713_cpuidle_exit(void)
{
	cpuidle_unregister(&j713_idle_driver);
	/*
	 * cpuidle_unregister() wakes idle CPUs and waits for RCU, but a CPU
	 * that has just woken is still in this module's text and outside RCU.
	 * Wait until every CPU has taken an interrupt, which it does only
	 * after leaving the idle state, before the text is freed.
	 */
	kick_all_cpus_sync();
}

module_init(j713_cpuidle_init);
module_exit(j713_cpuidle_exit);
MODULE_DESCRIPTION("CPU idle for the Apple M4 (T8132)");
MODULE_AUTHOR("KB2UKA");
MODULE_LICENSE("Dual MIT/GPL");
