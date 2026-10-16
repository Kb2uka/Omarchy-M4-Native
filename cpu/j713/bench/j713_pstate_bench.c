// SPDX-License-Identifier: GPL-2.0-only
/*
 * Bounded J713 cluster P-state bench (diagnostic, not shipped).
 *
 * One run per load: requests one P-state above the iBoot handoff state on one
 * cluster, samples the SMC CPU-max temperature (TCMz) and DVFS status every
 * 100 ms, and restores the handoff state when TCMz reaches abort_c, when an
 * SMC read fails, when max_ms elapses, or when the module is removed.
 * It refuses to start unless the cluster is already at its handoff state
 * (cpufreq pinned there by the caller) and TCMz is below abort_c.
 */
#include <linux/delay.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/mfd/macsmc.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/of_platform.h>
#include <linux/platform_device.h>
#include <linux/proc_fs.h>
#include <linux/seq_file.h>
#include <linux/slab.h>
#include <linux/workqueue.h>

#define DVFS_CMD 0x20
#define DVFS_CMD_BUSY BIT(31)
#define DVFS_CMD_SET BIT(25)
#define DVFS_CMD_PS1 GENMASK(4, 0)
#define DVFS_STATUS 0x50
#define SAMPLE_MS 100
#define MAX_SAMPLES 700

static char *cluster = "p";
module_param(cluster, charp, 0444);
static uint pstate;
module_param(pstate, uint, 0444);
static uint max_ms = 20000;
module_param(max_ms, uint, 0444);
static uint abort_c = 90;
module_param(abort_c, uint, 0444);

struct sample { u32 ms; u32 status; u32 tcmz; };

static void __iomem *base;
static uint handoff, top;
static struct platform_device *smc_pdev;
static struct apple_smc *smc;
static struct sample *samples;
static uint nsamples;
static unsigned long start;
static bool active;
static const char *reason = "running";
static u32 abort_bits, max_tcmz;
static int restore_ret;
static DEFINE_MUTEX(lock);
static void bench_work(struct work_struct *w);
static DECLARE_DELAYED_WORK(work, bench_work);

/* IEEE-754 single bits of a positive integer; positive floats order like their bits. */
static u32 float_bits(u32 n)
{
	u32 e = fls(n) - 1;

	return ((127 + e) << 23) | ((n << (23 - e)) & 0x7fffff);
}

static int set_pstate(uint ps)
{
	u64 reg;
	int ret = readq_poll_timeout_atomic(base + DVFS_CMD, reg, !(reg & DVFS_CMD_BUSY), 2, 400);

	reg &= ~(u64)DVFS_CMD_PS1;
	reg |= ps | DVFS_CMD_SET;
	writeq_relaxed(reg, base + DVFS_CMD);
	return ret;
}

static int read_tcmz(u32 *bits)
{
	int ret = apple_smc_read(smc, SMC_KEY(TCMz), bits, sizeof(*bits));

	return ret < 0 ? ret : (ret == sizeof(*bits) ? 0 : -EIO);
}

/* lock held */
static void stop(const char *why)
{
	if (!active)
		return;
	restore_ret = set_pstate(handoff);
	active = false;
	reason = why;
}

static void bench_work(struct work_struct *w)
{
	u32 bits = 0;
	int ret;

	mutex_lock(&lock);
	if (!active)
		goto out;
	ret = read_tcmz(&bits);
	if (nsamples < MAX_SAMPLES) {
		samples[nsamples].ms = jiffies_to_msecs(jiffies - start);
		samples[nsamples].status = readl_relaxed(base + DVFS_STATUS);
		samples[nsamples].tcmz = ret ? 0 : bits;
		nsamples++;
	}
	if (!ret && bits > max_tcmz)
		max_tcmz = bits;
	if (ret)
		stop("smc-read-error");
	else if (bits >= abort_bits)
		stop("abort-temperature");
	else if (time_after(jiffies, start + msecs_to_jiffies(max_ms)))
		stop("time-limit");
	else
		schedule_delayed_work(&work, msecs_to_jiffies(SAMPLE_MS));
out:
	mutex_unlock(&lock);
}

static int bench_show(struct seq_file *m, void *v)
{
	uint i;

	mutex_lock(&lock);
	seq_printf(m, "cluster %s pstate %u handoff %u abort_c %u max_ms %u\n",
		   cluster, pstate, handoff, abort_c, max_ms);
	seq_printf(m, "state %s reason %s restore_ret %d max_tcmz %08x cmd %016llx\n",
		   active ? "active" : "stopped", reason, restore_ret, max_tcmz,
		   readq_relaxed(base + DVFS_CMD));
	for (i = 0; i < nsamples; i++)
		seq_printf(m, "%u %08x %08x\n", samples[i].ms, samples[i].status, samples[i].tcmz);
	mutex_unlock(&lock);
	return 0;
}

static const struct of_device_id smc_ids[] = {
	{ .compatible = "apple,t8132-smc" },
	{ }
};

static int __init bench_init(void)
{
	struct device_node *np;
	phys_addr_t addr;
	u32 bits;
	u64 cmd;
	int ret;

	if (!strcmp(cluster, "p")) {
		addr = 0x211e20000ULL; handoff = 4; top = 20;
	} else if (!strcmp(cluster, "e")) {
		addr = 0x210e20000ULL; handoff = 1; top = 8;
	} else {
		return -EINVAL;
	}
	if (pstate <= handoff || pstate > top || max_ms < 1000 || max_ms > 60000 ||
	    abort_c < 60 || abort_c > 95)
		return -EINVAL;
	abort_bits = float_bits(abort_c);

	np = of_find_matching_node(NULL, smc_ids);
	if (!np)
		return -ENODEV;
	smc_pdev = of_find_device_by_node(np);
	of_node_put(np);
	if (!smc_pdev)
		return -ENODEV;
	smc = dev_get_drvdata(&smc_pdev->dev);
	ret = -ENODEV;
	if (!smc)
		goto put;
	ret = read_tcmz(&bits);
	if (ret)
		goto put;
	ret = -EBUSY;
	if (bits >= abort_bits)
		goto put;
	ret = -ENOMEM;
	samples = kcalloc(MAX_SAMPLES, sizeof(*samples), GFP_KERNEL);
	if (!samples)
		goto put;
	base = ioremap(addr, 0x100);
	if (!base)
		goto free;
	cmd = readq_relaxed(base + DVFS_CMD);
	ret = -EBUSY;
	if ((cmd & DVFS_CMD_PS1) != handoff)
		goto unmap;
	ret = -ENOMEM;
	if (!proc_create_single("j713_pstate_bench", 0400, NULL, bench_show))
		goto unmap;

	mutex_lock(&lock);
	start = jiffies;
	active = true;
	max_tcmz = bits;
	ret = set_pstate(pstate);
	if (ret)
		stop("busy-timeout");
	else
		schedule_delayed_work(&work, msecs_to_jiffies(SAMPLE_MS));
	mutex_unlock(&lock);
	pr_info("j713_pstate_bench: %s P%u requested (handoff P%u, abort %u C, %u ms)\n",
		cluster, pstate, handoff, abort_c, max_ms);
	return 0;
unmap:
	iounmap(base);
free:
	kfree(samples);
put:
	put_device(&smc_pdev->dev);
	return ret;
}

static void __exit bench_exit(void)
{
	cancel_delayed_work_sync(&work);
	mutex_lock(&lock);
	stop("module-removed");
	mutex_unlock(&lock);
	remove_proc_entry("j713_pstate_bench", NULL);
	iounmap(base);
	kfree(samples);
	put_device(&smc_pdev->dev);
}

module_init(bench_init);
module_exit(bench_exit);
MODULE_DESCRIPTION("Bounded J713 cluster P-state bench");
MODULE_LICENSE("GPL");
