/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef J713_SPEAKER_GROUP_H
#define J713_SPEAKER_GROUP_H

#include <linux/completion.h>
#include <linux/mutex.h>
#include <linux/types.h>
#include <linux/workqueue.h>

enum j713_group_phase {
	J713_GROUP_LOW,
	J713_GROUP_CONFIGURING,
	J713_GROUP_SENSING,
	J713_GROUP_ACTIVE,
	J713_GROUP_CANCELLED,
	J713_GROUP_FAILED,
	J713_GROUP_REMOVING,
};

/* Only a reviewed board profile may supply nonzero bounds. No default exists. */
struct j713_group_profile {
	bool accepted;
	u64 configure_ns;
	u64 lease_ns;
};

struct j713_speaker_group;

/* enter_active may defer only before any amplifier register access. */
#define J713_GROUP_ACTIVATE_DEFERRED 1

struct j713_group_ops {
	/* Short GPIO/regmap operations; must not take codec/I2C/clock locks. */
	int (*force_low)(void *context);
	int (*release_reset)(void *context);
	/* These may sleep, but must never release reset or own the GPIO lock. */
	int (*configure_muted)(void *context, struct j713_speaker_group *group,
			       u64 generation);
	int (*enter_active)(void *context, struct j713_speaker_group *group,
			    u64 generation);
	/* Caller has joined DMA/clock operations and restored shared routing. */
	int (*verify_quiescent)(void *context);
};

struct j713_speaker_group {
	struct mutex gpio_lock;
	struct mutex operation_lock;
	struct completion idle;
	struct delayed_work cutoff;
	struct j713_group_profile profile;
	const struct j713_group_ops *ops;
	void *context;
	const void *owner;
	u64 generation;
	u64 sequence;
	u64 deadline_ns;
	unsigned int in_flight;
	enum j713_group_phase phase;
	bool removing;
	bool cancelled;
	bool lease_valid;
	bool faulted;
	int error;
};

/* All entrypoints require process context and an externally held group lifetime. */
int j713_group_init(struct j713_speaker_group *group,
		    const struct j713_group_ops *ops, void *context,
		    const struct j713_group_profile *profile);
int j713_group_start(struct j713_speaker_group *group, const void *owner,
		     u64 *generation);
int j713_group_renew(struct j713_speaker_group *group, const void *owner,
		     s64 generation, s64 sequence);
int j713_group_activate(struct j713_speaker_group *group, const void *owner,
			u64 generation);
/* Caller holds operation_lock from its eligibility decision through this call. */
int j713_group_activate_locked(struct j713_speaker_group *group, const void *owner,
			       u64 generation);
int j713_group_check(struct j713_speaker_group *group, u64 generation);
int j713_group_revoke(struct j713_speaker_group *group);
/* Stop joins admitted operations; external DMA teardown must proceed on error. */
int j713_group_stop(struct j713_speaker_group *group, const void *owner);
/* Unpublish callers first. Waits for admitted callbacks; does not veto removal. */
int j713_group_destroy(struct j713_speaker_group *group);

#endif
