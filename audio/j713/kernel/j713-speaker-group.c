// SPDX-License-Identifier: GPL-2.0-only
#include <linux/errno.h>
#include <linux/jiffies.h>
#include <linux/ktime.h>
#include <linux/lockdep.h>
#include <linux/module.h>
#include <linux/overflow.h>
#include <linux/string.h>
#include "j713-speaker-group.h"

static int group_admit(struct j713_speaker_group *g)
{
	int ret = 0;

	mutex_lock(&g->gpio_lock);
	if (g->removing)
		ret = -ENODEV;
	else if (!g->in_flight++)
		reinit_completion(&g->idle);
	mutex_unlock(&g->gpio_lock);
	return ret;
}

static void group_leave(struct j713_speaker_group *g)
{
	mutex_lock(&g->gpio_lock);
	if (!--g->in_flight)
		complete_all(&g->idle);
	mutex_unlock(&g->gpio_lock);
}

/* Called only under gpio_lock. LOW does not wait for operation_lock. */
static int group_low_locked(struct j713_speaker_group *g, int reason)
{
	int ret;

	g->cancelled = true;
	g->lease_valid = false;
	g->deadline_ns = 0;
	ret = g->ops->force_low(g->context);
	if (ret) {
		g->faulted = true;
		g->error = ret;
	} else if (reason && !g->error) {
		g->error = reason;
	}
	g->phase = g->removing ? J713_GROUP_REMOVING :
		   g->faulted ? J713_GROUP_FAILED : J713_GROUP_CANCELLED;
	return ret;
}

static int group_check_locked(struct j713_speaker_group *g, u64 generation)
{
	if (g->removing || g->faulted || g->cancelled ||
	    !generation || generation != g->generation)
		return -ECANCELED;
	if (!g->deadline_ns || ktime_get_ns() >= g->deadline_ns) {
		group_low_locked(g, -ETIME);
		return -ETIME;
	}
	return 0;
}

static void group_schedule_locked(struct j713_speaker_group *g)
{
	u64 now = ktime_get_ns();
	unsigned long ticks = 1;

	if (now < g->deadline_ns)
		ticks = max_t(unsigned long, 1,
			nsecs_to_jiffies(g->deadline_ns - now));
	mod_delayed_work(system_highpri_wq, &g->cutoff, ticks);
}

static void group_cutoff(struct work_struct *work)
{
	struct j713_speaker_group *g = container_of(to_delayed_work(work),
					struct j713_speaker_group, cutoff);

	mutex_lock(&g->gpio_lock);
	if (!g->removing && !g->cancelled && g->deadline_ns) {
		if (ktime_get_ns() >= g->deadline_ns)
			group_low_locked(g, -ETIME);
		else
			group_schedule_locked(g);
	}
	mutex_unlock(&g->gpio_lock);
}

static int group_deadline_locked(struct j713_speaker_group *g, u64 duration)
{
	u64 deadline;

	if (!duration || check_add_overflow(ktime_get_ns(), duration, &deadline))
		return -ERANGE;
	g->deadline_ns = deadline;
	group_schedule_locked(g);
	return 0;
}

int j713_group_init(struct j713_speaker_group *g,
		    const struct j713_group_ops *ops, void *context,
		    const struct j713_group_profile *profile)
{
	int ret;

	if (!g || !ops || !ops->force_low || !ops->release_reset ||
	    !ops->configure_muted || !ops->enter_active || !ops->verify_quiescent)
		return -EINVAL;
	if (profile && profile->accepted &&
	    (!profile->configure_ns || !profile->lease_ns ||
	     nsecs_to_jiffies64(profile->configure_ns) > MAX_JIFFY_OFFSET ||
	     nsecs_to_jiffies64(profile->lease_ns) > MAX_JIFFY_OFFSET))
		return -EINVAL;
	memset(g, 0, sizeof(*g));
	mutex_init(&g->gpio_lock);
	mutex_init(&g->operation_lock);
	init_completion(&g->idle);
	complete_all(&g->idle);
	INIT_DELAYED_WORK(&g->cutoff, group_cutoff);
	g->ops = ops;
	g->context = context;
	if (profile)
		g->profile = *profile;
	g->cancelled = true;
	ret = ops->force_low(context);
	g->faulted = !!ret;
	g->error = ret;
	g->phase = ret ? J713_GROUP_FAILED : J713_GROUP_LOW;
	return ret;
}

int j713_group_start(struct j713_speaker_group *g, const void *owner,
		     u64 *generation)
{
	int ret;
	u64 token = 0;

	if (!owner || !generation)
		return -EINVAL;
	ret = group_admit(g);
	if (ret)
		return ret;
	mutex_lock(&g->operation_lock);
	ret = g->ops->verify_quiescent(g->context);
	if (ret)
		goto leave_operation;
	mutex_lock(&g->gpio_lock);
	if (g->removing || g->faulted || g->owner || g->phase != J713_GROUP_LOW) {
		ret = -EBUSY;
		goto unlock;
	}
	if (!g->profile.accepted || !g->profile.configure_ns || !g->profile.lease_ns) {
		ret = -EACCES;
		goto unlock;
	}
	if (g->generation == S64_MAX) {
		ret = -EOVERFLOW;
		goto unlock;
	}
	g->owner = owner;
	token = ++g->generation;
	g->sequence = 0;
	g->error = 0;
	g->cancelled = false;
	g->phase = J713_GROUP_CONFIGURING;
	ret = group_deadline_locked(g, g->profile.configure_ns);
	if (!ret)
		ret = group_check_locked(g, token);
	if (!ret)
		ret = g->ops->release_reset(g->context);
	if (ret) {
		g->faulted = true;
		group_low_locked(g, ret);
		goto unlock;
	}
	mutex_unlock(&g->gpio_lock);
	ret = g->ops->configure_muted(g->context, g, token);
	mutex_lock(&g->gpio_lock);
	if (ret) {
		g->faulted = true;
		group_low_locked(g, ret);
	} else {
		ret = group_check_locked(g, token);
		if (!ret) {
			g->phase = J713_GROUP_SENSING;
			*generation = token;
		}
	}
unlock:
	mutex_unlock(&g->gpio_lock);
leave_operation:
	mutex_unlock(&g->operation_lock);
	group_leave(g);
	return ret;
}

int j713_group_renew(struct j713_speaker_group *g, const void *owner,
		     s64 generation, s64 sequence)
{
	int ret = group_admit(g);

	if (ret)
		return ret;
	mutex_lock(&g->gpio_lock);
	if (!owner || owner != g->owner) {
		ret = -EPERM;
		goto out;
	}
	ret = group_check_locked(g, g->generation);
	if (ret)
		goto out;
	if (generation <= 0 || (u64)generation != g->generation || sequence <= 0 ||
	    (u64)sequence <= g->sequence ||
	    (g->phase != J713_GROUP_SENSING && g->phase != J713_GROUP_ACTIVE)) {
		group_low_locked(g, -ESTALE);
		ret = -ESTALE;
		goto out;
	}
	ret = group_deadline_locked(g, g->profile.lease_ns);
	if (ret) {
		group_low_locked(g, ret);
		goto out;
	}
	g->sequence = sequence;
	g->lease_valid = true;
out:
	mutex_unlock(&g->gpio_lock);
	group_leave(g);
	return ret;
}

int j713_group_check(struct j713_speaker_group *g, u64 generation)
{
	int ret = group_admit(g);

	if (ret)
		return ret;
	mutex_lock(&g->gpio_lock);
	ret = group_check_locked(g, generation);
	mutex_unlock(&g->gpio_lock);
	group_leave(g);
	return ret;
}

static int group_activate_locked(struct j713_speaker_group *g, const void *owner,
				 u64 generation)
{
	int ret;

	lockdep_assert_held(&g->operation_lock);
	mutex_lock(&g->gpio_lock);
	ret = group_check_locked(g, generation);
	if (!ret && (!owner || owner != g->owner || !g->lease_valid ||
		     g->phase != J713_GROUP_SENSING))
		ret = -EACCES;
	mutex_unlock(&g->gpio_lock);
	if (!ret) {
		ret = g->ops->enter_active(g->context, g, generation);
		mutex_lock(&g->gpio_lock);
		if (ret == J713_GROUP_ACTIVATE_DEFERRED) {
			ret = group_check_locked(g, generation);
			if (!ret && (owner != g->owner || !g->lease_valid ||
				     g->phase != J713_GROUP_SENSING))
				ret = -EACCES;
		} else if (ret) {
			g->faulted = true;
			group_low_locked(g, ret);
		} else {
			ret = group_check_locked(g, generation);
			if (!ret)
				g->phase = J713_GROUP_ACTIVE;
		}
		mutex_unlock(&g->gpio_lock);
	}
	return ret;
}

int j713_group_activate_locked(struct j713_speaker_group *g, const void *owner,
			       u64 generation)
{
	int ret = group_admit(g);

	if (ret)
		return ret;
	ret = group_activate_locked(g, owner, generation);
	group_leave(g);
	return ret;
}

int j713_group_activate(struct j713_speaker_group *g, const void *owner,
			u64 generation)
{
	int ret = group_admit(g);

	if (ret)
		return ret;
	mutex_lock(&g->operation_lock);
	ret = group_activate_locked(g, owner, generation);
	mutex_unlock(&g->operation_lock);
	group_leave(g);
	return ret;
}

int j713_group_revoke(struct j713_speaker_group *g)
{
	int ret = group_admit(g);

	if (ret)
		return ret;
	mutex_lock(&g->gpio_lock);
	ret = group_low_locked(g, 0);
	mutex_unlock(&g->gpio_lock);
	group_leave(g);
	return ret;
}

int j713_group_stop(struct j713_speaker_group *g, const void *owner)
{
	int ret = group_admit(g);
	int quiescent;
	u64 generation;
	const void *bound_owner;

	if (ret)
		return ret;
	mutex_lock(&g->gpio_lock);
	if (g->owner && g->owner != owner) {
		mutex_unlock(&g->gpio_lock);
		group_leave(g);
		return -EPERM;
	}
	generation = g->generation;
	bound_owner = g->owner;
	ret = group_low_locked(g, 0);
	mutex_unlock(&g->gpio_lock);
	mutex_lock(&g->operation_lock);
	mutex_lock(&g->gpio_lock);
	if (g->generation != generation || g->owner != bound_owner) {
		ret = -ESTALE;
		goto unlock_stop;
	}
	mutex_unlock(&g->gpio_lock);
	quiescent = g->ops->verify_quiescent(g->context);
	mutex_lock(&g->gpio_lock);
	if (!ret && quiescent)
		ret = quiescent;
	if (!ret && g->faulted)
		ret = g->error ? g->error : -EIO;
	if (!ret && !g->faulted && !g->removing) {
		g->owner = NULL;
		g->phase = J713_GROUP_LOW;
	}
unlock_stop:
	mutex_unlock(&g->gpio_lock);
	mutex_unlock(&g->operation_lock);
	group_leave(g);
	return ret;
}

int j713_group_destroy(struct j713_speaker_group *g)
{
	int ret;

	mutex_lock(&g->gpio_lock);
	g->removing = true;
	ret = group_low_locked(g, 0);
	mutex_unlock(&g->gpio_lock);
	cancel_delayed_work_sync(&g->cutoff);
	wait_for_completion(&g->idle);
	return ret;
}

MODULE_DESCRIPTION("J713 speaker generation and cutoff foundation");
MODULE_LICENSE("GPL");
