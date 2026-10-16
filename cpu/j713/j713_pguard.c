// SPDX-License-Identifier: GPL-2.0-only OR MIT
/*
 * Predictive P-cluster clock guard for the Apple M4 (T8132) Air, issue #66.
 *
 * The M4 P cluster runs any requested state within 0.1 ms. The hardware limits
 * it by the number of busy P cores (P17 with one, P16 with two, P15 with four)
 * but never lowers a clock for heat. The CPU die then rises 9-58 C within a
 * second or two, depending on the state and on how many P cores are busy,
 * while the SMC refreshes its CPU die reading (TCMz) only about once a second.
 * A guard that only reacts to TCMz is always late.
 *
 * So this guard predicts, from two inputs:
 *
 * - Slow path, every period_ms on an unbound workqueue: read TCMz and, on
 *   each new reading, estimate the base, the temperature with the cluster
 *   back at 1800 MHz (pg_base): the reading less the measured fast heat
 *   (pg_fast) of the fewest busy P cores and lowest P state seen in the last
 *   750-1000 ms, when a P core was busy through the reading window. It is the
 *   only path that raises the cap.
 * - Fast path, a deferrable timer every 16 ms pinned to each P core: count
 *   the busy P cores from their idle time. A deferrable timer fires only on
 *   an awake core, so an idle cluster costs nothing, and a busy one is
 *   checked by its own timer. CPU hotplug callbacks keep each timer on its
 *   own core across offline and online. The slow path samples too, as a
 *   backstop. A higher count is held for 300 ms (pg_hold). When it
 *   lowers the allowed state, the cap drops within about 20 ms, long before
 *   the extra heat builds up. It also records the fewest busy P cores and
 *   the lowest P state it sees.
 *
 * The cap is the highest P state whose measured rise for that many busy P
 * cores, plus the heat of the busy E cores (pg_e_heat), keeps base + rise at
 * or below limit_c (pg_allowed), applied as a frequency-QoS maximum on the P
 * policy.
 *
 * The devicetree (patches/j713-pboost.dtso) holds the P cluster at 1800 MHz
 * with a boost-gate trip on cpu-die-thermal, set to 20 C so it is always
 * reached. The guard opens that gate itself, raising the trip to 150 C, only
 * after its own cap is in place, so the gate is never open without the guard.
 * Writing 20000 back to the trip closes it again (m4-cpu-idle --boost off).
 *
 * Its limits follow macOS on the fanless Airs, which lets the CPU die burst
 * past 105 C and run near 100 C under sustained load: a predicted peak of
 * 105 C, a hard pin to 1800 MHz from 108 C until the die is below 100 C, and,
 * when it opens the gate, the zone's sustained trip raised from 95 C to 100 C
 * (docs/j713-cpu-boost.md). Without the guard the zone keeps its 95 C.
 *
 * It fails closed: the cap starts at 1800 MHz (P4), returns there on an SMC
 * read error, and from hot_c until the die is below release_c. It refuses to
 * load unless the J713 idle driver is in charge (without idle the idle P
 * cores spin at the boosted clock, #60) and the closed gate is there. It
 * cannot be unloaded: once the gate is open, its QoS request is what bounds
 * the P cluster. Nothing here writes a hardware register.
 */
#include <linux/cpufreq.h>
#include <linux/cpuhotplug.h>
#include <linux/cpuidle.h>
#include <linux/jiffies.h>
#include <linux/mfd/macsmc.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/of_platform.h>
#include <linux/platform_device.h>
#include <linux/pm_qos.h>
#include <linux/proc_fs.h>
#include <linux/seq_file.h>
#include <linux/spinlock.h>
#include <linux/thermal.h>
#include <linux/tick.h>
#include <linux/timer.h>
#include <linux/workqueue.h>

#include "j713_pguard_policy.h"

#define FAST_MS 16
#define HOLD_MS 300
#define GATE_ZONE "cpu-die-thermal"
#define GATE_CLOSED_MC 20000
#define GATE_OPEN_MC 150000
#define SUSTAIN_MC 95000		/* the zone's sustained passive trip, as the devicetree sets it */
#define SUSTAIN_BOOST_MC 100000		/* ... and while the guard runs */

static uint limit_c = 105;
module_param(limit_c, uint, 0444);
MODULE_PARM_DESC(limit_c, "Predicted CPU die peak the guard allows, in C");

static uint hot_c = 108;
module_param(hot_c, uint, 0444);
MODULE_PARM_DESC(hot_c, "CPU die reading that pins the P cluster to 1800 MHz, in C");

static uint release_c = 100;
module_param(release_c, uint, 0444);
MODULE_PARM_DESC(release_c, "CPU die reading below which the hot pin is released, in C");

static uint period_ms = 250;
module_param(period_ms, uint, 0444);
MODULE_PARM_DESC(period_ms, "TCMz sampling period in milliseconds");

static struct platform_device *smc_pdev;
static struct apple_smc *smc;
static struct cpufreq_policy *policy, *e_policy;
static struct freq_qos_request cap_req;
static u32 level_khz[PG_TOP_LEVEL + 1];
static unsigned int p_cpus[PG_P_CPUS], e_cpus[PG_E_CPUS];
static DEFINE_PER_CPU(struct timer_list, fast_timer);
static DEFINE_PER_CPU(bool, fast_timer_on);
static struct thermal_zone_device *zone;
static struct thermal_trip *gate, *sustain;

/* Fast-path state, under fast_lock (taken from timer softirqs). */
static DEFINE_SPINLOCK(fast_lock);
static u64 last_idle[PG_P_CPUS + PG_E_CPUS], last_wall[PG_P_CPUS + PG_E_CPUS];
static unsigned int busy_held = PG_P_CPUS;	/* assume all busy until measured */
static unsigned int e_busy_held = PG_E_CPUS;
static unsigned long held_until;
static int base_mc = 1000 * 100;		/* until the first reading: allows only P4 */
static bool hot, smc_ok;
static u32 applied_level = PG_BASE_LEVEL;
/*
 * The lowest P state and the fewest busy P cores in the current slow tick,
 * and in the recent ticks before it (pg_lowest). Until they are measured,
 * the kept ticks read P4 and all cores busy, which subtracts nothing.
 */
static u32 tick_min = PG_TOP_LEVEL, tick_min_busy = PG_P_CPUS;
static u32 recent_min[PG_RECENT_MAX], recent_min_busy[PG_RECENT_MAX];
static unsigned int recent_ticks, recent_pos;

/* Slow-path state, under lock. */
static DEFINE_MUTEX(lock);
static int last_mc;
static u32 last_bits;
static u64 win_idle[PG_P_CPUS], win_wall[PG_P_CPUS];
static unsigned long reads, errors, raises, fast_drops;

static void slow_work(struct work_struct *w);
static DECLARE_DELAYED_WORK(slow, slow_work);
static void apply_work(struct work_struct *w);
static DECLARE_WORK(apply, apply_work);

static u32 policy_level(void)
{
	u32 cur = READ_ONCE(policy->cur), level;

	for (level = PG_TOP_LEVEL; level > PG_BASE_LEVEL; level--)
		if (level_khz[level] && cur >= level_khz[level])
			return level;
	return PG_BASE_LEVEL;
}

static unsigned int cpu_at(unsigned int i)
{
	return i < PG_P_CPUS ? p_cpus[i] : e_cpus[i - PG_P_CPUS];
}

/*
 * fast_lock held. Count busy P and E cores since the last sample and hold the
 * higher counts for HOLD_MS; without idle accounting, every core counts as busy.
 */
static void sample_busy(void)
{
	u64 d_idle[PG_P_CPUS + PG_E_CPUS], d_wall[PG_P_CPUS + PG_E_CPUS];
	unsigned int i, busy, e_busy;

	for (i = 0; i < PG_P_CPUS + PG_E_CPUS; i++) {
		u64 wall, idle = get_cpu_idle_time_us(cpu_at(i), &wall);

		if (idle == (u64)-1) {
			busy_held = PG_P_CPUS;
			e_busy_held = PG_E_CPUS;
			held_until = jiffies + msecs_to_jiffies(HOLD_MS);
			return;
		}
		d_idle[i] = idle - last_idle[i];
		d_wall[i] = wall - last_wall[i];
		last_idle[i] = idle;
		last_wall[i] = wall;
	}
	busy = pg_busy_count(d_idle, d_wall, PG_P_CPUS);
	e_busy = pg_busy_count(d_idle + PG_P_CPUS, d_wall + PG_P_CPUS, PG_E_CPUS);
	tick_min_busy = min(tick_min_busy, busy);
	if (pg_hold(&busy, &e_busy, busy_held, e_busy_held, time_after(jiffies, held_until)))
		held_until = jiffies + msecs_to_jiffies(HOLD_MS);
	busy_held = busy;
	e_busy_held = e_busy;
}

/* fast_lock held */
static u32 wanted_level(void)
{
	if (!smc_ok)
		return PG_BASE_LEVEL;
	return pg_allowed(base_mc, busy_held, pg_e_heat(e_busy_held, READ_ONCE(e_policy->cur)),
			  (int)limit_c * 1000, hot, level_khz);
}

static void fast_timer_fn(struct timer_list *t)
{
	unsigned long flags;
	bool drop;

	spin_lock_irqsave(&fast_lock, flags);
	sample_busy();
	tick_min = min(tick_min, policy_level());
	drop = wanted_level() < applied_level;
	spin_unlock_irqrestore(&fast_lock, flags);
	if (drop)
		queue_work(system_highpri_wq, &apply);
	/* Pinned, so this keeps it on its core; the offline callback stops it first. */
	if (READ_ONCE(*this_cpu_ptr(&fast_timer_on)))
		mod_timer(t, jiffies + msecs_to_jiffies(FAST_MS));
}

static bool p_cpu(unsigned int cpu)
{
	return cpumask_test_cpu(cpu, policy->related_cpus);
}

/* CPU hotplug: arm a P core's timer on that core when it comes online. */
static int fast_timer_online(unsigned int cpu)
{
	struct timer_list *t = per_cpu_ptr(&fast_timer, cpu);

	if (!p_cpu(cpu))
		return 0;
	timer_setup(t, fast_timer_fn, TIMER_DEFERRABLE | TIMER_PINNED);
	t->expires = jiffies + msecs_to_jiffies(FAST_MS);
	WRITE_ONCE(per_cpu(fast_timer_on, cpu), true);
	add_timer_on(t, cpu);
	return 0;
}

/* ... and stop it before the core goes, so it never migrates to another core. */
static int fast_timer_offline(unsigned int cpu)
{
	if (!p_cpu(cpu))
		return 0;
	WRITE_ONCE(per_cpu(fast_timer_on, cpu), false);
	timer_delete_sync(per_cpu_ptr(&fast_timer, cpu));
	return 0;
}

/* Apply the wanted level now if it is lower, or, from the slow path, whatever it is. */
static void update_cap(bool may_raise)
{
	unsigned long flags;
	u32 want;

	mutex_lock(&lock);
	spin_lock_irqsave(&fast_lock, flags);
	want = wanted_level();
	if (want > applied_level && !may_raise)
		want = applied_level;
	spin_unlock_irqrestore(&fast_lock, flags);
	if (want != applied_level) {
		if (want < applied_level && !may_raise)
			fast_drops++;
		else if (want > applied_level)
			raises++;
		freq_qos_update_request(&cap_req, level_khz[want]);
		spin_lock_irqsave(&fast_lock, flags);
		applied_level = want;
		spin_unlock_irqrestore(&fast_lock, flags);
	}
	mutex_unlock(&lock);
}

static void apply_work(struct work_struct *w)
{
	update_cap(false);
}

/* lock held. Whether a P core was busy through the window since the last call. */
static bool p_window_busy(void)
{
	u64 d_idle[PG_P_CPUS] = { 0 }, d_wall[PG_P_CPUS] = { 0 };
	bool accounted = true;
	unsigned int i;

	for (i = 0; i < PG_P_CPUS; i++) {
		u64 wall, idle = get_cpu_idle_time_us(p_cpus[i], &wall);

		if (idle == (u64)-1) {
			accounted = false;
			continue;
		}
		d_idle[i] = idle - win_idle[i];
		d_wall[i] = wall - win_wall[i];
		win_idle[i] = idle;
		win_wall[i] = wall;
	}
	/* Without idle accounting nothing counts as busy, so no heat is subtracted. */
	return accounted && pg_window_busy(d_idle, d_wall, PG_P_CPUS);
}

static void slow_work(struct work_struct *w)
{
	unsigned long flags;
	u32 bits;
	int ret, mc = 0;
	bool fresh, busy = false;

	mutex_lock(&lock);
	reads++;
	ret = apple_smc_read(smc, SMC_KEY(TCMz), &bits, sizeof(bits));
	if (ret == sizeof(bits))
		ret = pg_float_to_mc(bits, &mc) ? -ERANGE : 0;
	else if (ret >= 0)
		ret = -EIO;
	fresh = !ret && bits != last_bits;
	if (fresh)
		busy = p_window_busy();

	spin_lock_irqsave(&fast_lock, flags);
	/* The fast timers' backstop: a busy core that its own timer missed still counts. */
	sample_busy();
	tick_min = min(tick_min, policy_level());
	if (ret) {
		errors++;
		smc_ok = false;
	} else {
		if (fresh) {
			/* A new reading covers the window that just ended. */
			last_bits = bits;
			last_mc = mc;
			base_mc = pg_base(mc, busy, pg_lowest(recent_min_busy, recent_ticks, tick_min_busy),
					  pg_lowest(recent_min, recent_ticks, tick_min));
		}
		if (mc >= (int)hot_c * 1000)
			hot = true;
		else if (mc < (int)release_c * 1000)
			hot = false;
		smc_ok = true;
	}
	/* This tick joins the recent ones; the next starts from the current state. */
	recent_pos = pg_keep(recent_min, recent_min_busy, recent_pos, recent_ticks, tick_min, tick_min_busy);
	tick_min = policy_level();
	tick_min_busy = PG_P_CPUS;
	spin_unlock_irqrestore(&fast_lock, flags);
	mutex_unlock(&lock);
	update_cap(true);
	queue_delayed_work(system_unbound_wq, &slow, msecs_to_jiffies(period_ms));
}

static int guard_show(struct seq_file *m, void *v)
{
	unsigned long flags;

	mutex_lock(&lock);
	spin_lock_irqsave(&fast_lock, flags);
	seq_printf(m, "limit_c %u hot_c %u release_c %u period_ms %u\n", limit_c, hot_c, release_c, period_ms);
	seq_printf(m, "tcmz_mc %d base_mc %d hot %d smc_ok %d busy %u e_busy %u cap_level %u cap_khz %u\n",
		   last_mc, base_mc, hot, smc_ok, busy_held, e_busy_held, applied_level,
		   level_khz[applied_level]);
	seq_printf(m, "reads %lu errors %lu raises %lu fast_drops %lu\n", reads, errors, raises, fast_drops);
	spin_unlock_irqrestore(&fast_lock, flags);
	mutex_unlock(&lock);
	return 0;
}

/*
 * Under the zone lock: the gate is the one passive trip at 20 C, the sustained
 * trip the one at 95 C. found[0] and found[1] count them.
 */
static int find_trips(struct thermal_trip *trip, void *data)
{
	unsigned int *found = data;

	if (trip->type != THERMAL_TRIP_PASSIVE)
		return 0;
	if (trip->temperature == GATE_CLOSED_MC) {
		gate = trip;
		found[0]++;
	} else if (trip->temperature == SUSTAIN_MC) {
		sustain = trip;
		found[1]++;
	}
	return 0;
}

/* Under the zone lock, as the sysfs trip_point_N_temp store does. */
static int open_gate(struct thermal_trip *trip, void *data)
{
	if (trip == gate)
		thermal_zone_set_trip_temp(zone, trip, GATE_OPEN_MC);
	else if (trip == sustain)
		thermal_zone_set_trip_temp(zone, trip, SUSTAIN_BOOST_MC);
	return 0;
}

static const struct of_device_id smc_ids[] = {
	{ .compatible = "apple,t8132-smc" },
	{ }
};

static int __init guard_init(void)
{
	struct cpufreq_frequency_table *pos;
	struct cpuidle_driver *idle;
	struct device_node *np;
	unsigned int cpu, n = 0, i, found[2] = { 0, 0 };
	int ret;

	if (!of_machine_is_compatible("apple,t8132") || limit_c < 60 || limit_c > 105 ||
	    limit_c >= hot_c || release_c >= hot_c || hot_c > 110 || period_ms < 50 || period_ms > 1000)
		return -EINVAL;

	idle = cpuidle_get_driver();
	if (!idle || strcmp(idle->name, "j713_idle"))
		return -EPERM;

	zone = thermal_zone_get_zone_by_name(GATE_ZONE);
	if (IS_ERR(zone))
		return PTR_ERR(zone);
	thermal_zone_for_each_trip(zone, find_trips, found);
	if (found[0] != 1 || found[1] != 1)
		return -ENODEV;

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
		goto put_smc;

	/* CPU 0 is the boot P core; its policy is the P cluster. */
	policy = cpufreq_cpu_get(0);
	if (!policy)
		goto put_smc;
	cpufreq_for_each_valid_entry(pos, policy->freq_table)
		if (pos->driver_data <= PG_TOP_LEVEL)
			level_khz[pos->driver_data] = pos->frequency;
	for_each_cpu(cpu, policy->related_cpus)
		if (n < PG_P_CPUS)
			p_cpus[n++] = cpu;
	/* CPU 1 is the first E core; its policy is the E cluster. */
	e_policy = cpufreq_cpu_get(1);
	ret = -ENODEV;
	if (!e_policy)
		goto put_policy;
	i = 0;
	for_each_cpu(cpu, e_policy->related_cpus)
		if (i < PG_E_CPUS)
			e_cpus[i++] = cpu;
	if (i != PG_E_CPUS || cpumask_weight(e_policy->related_cpus) != PG_E_CPUS ||
	    cpumask_intersects(e_policy->related_cpus, policy->related_cpus))
		goto put_e_policy;
	if (n != PG_P_CPUS || cpumask_weight(policy->related_cpus) != PG_P_CPUS ||
	    level_khz[PG_BASE_LEVEL] != 1800000 || level_khz[PG_TOP_LEVEL] != 4464000 ||
	    policy->cpuinfo.max_freq != 4464000)
		goto put_e_policy;

	ret = freq_qos_add_request(&policy->constraints, &cap_req, FREQ_QOS_MAX,
				   level_khz[PG_BASE_LEVEL]);
	if (ret < 0)
		goto put_e_policy;
	if (!proc_create_single("j713_pguard", 0444, NULL, guard_show)) {
		ret = -ENOMEM;
		goto remove_req;
	}
	for (i = 0; i < PG_P_CPUS + PG_E_CPUS; i++)
		last_idle[i] = get_cpu_idle_time_us(cpu_at(i), &last_wall[i]);
	recent_ticks = min_t(unsigned int, DIV_ROUND_UP(PG_RECENT_MS, period_ms), PG_RECENT_MAX);
	for (i = 0; i < recent_ticks; i++) {
		recent_min[i] = PG_BASE_LEVEL;
		recent_min_busy[i] = PG_P_CPUS;
	}
	for (i = 0; i < PG_P_CPUS; i++)
		win_idle[i] = get_cpu_idle_time_us(p_cpus[i], &win_wall[i]);
	/* Arms the timer on every online P core now, and on any that comes online later. */
	ret = cpuhp_setup_state(CPUHP_AP_ONLINE_DYN, "j713_pguard:online", fast_timer_online, fast_timer_offline);
	if (ret < 0)
		goto remove_proc;
	queue_delayed_work(system_unbound_wq, &slow, 0);
	/* The cap is in place, so the gate can open and the sustained trip move to 100 C. */
	thermal_zone_for_each_trip(zone, open_gate, NULL);
	thermal_zone_device_update(zone, THERMAL_TRIP_CHANGED);
	pr_info("j713_pguard: boost gate open, sustained trip %u C; P cluster capped at %u kHz until the die allows more (limit %u C, hot %u C)\n",
		SUSTAIN_BOOST_MC / 1000, level_khz[PG_BASE_LEVEL], limit_c, hot_c);
	/* The policy reference, the SMC device, the timers and the hotplug state stay: the guard is permanent. */
	return 0;

remove_proc:
	remove_proc_entry("j713_pguard", NULL);
remove_req:
	freq_qos_remove_request(&cap_req);
put_e_policy:
	cpufreq_cpu_put(e_policy);
put_policy:
	cpufreq_cpu_put(policy);
put_smc:
	put_device(&smc_pdev->dev);
	return ret;
}

/* No module_exit: the guard cannot be unloaded. */
module_init(guard_init);
MODULE_DESCRIPTION("Predictive P-cluster clock guard for the Apple M4 Air");
MODULE_AUTHOR("KB2UKA");
MODULE_LICENSE("Dual MIT/GPL");
