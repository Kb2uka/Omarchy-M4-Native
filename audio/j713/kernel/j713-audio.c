// SPDX-License-Identifier: GPL-2.0-only
#include <linux/delay.h>
#include <linux/device.h>
#include <linux/err.h>
#include <linux/gpio/consumer.h>
#include <linux/gpio/driver.h>
#include <linux/i2c.h>
#include <linux/ktime.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/of_platform.h>
#include <linux/platform_device.h>
#include <linux/regmap.h>
#include <linux/slab.h>
#include <linux/sysfs.h>
#include "j713-speaker-group.h"
#include "j713-audio.h"

#define RESET_REG 0x3c
#define RESET_MASK 0x6f
#define RESET_LOW 2

struct j713_audio {
	struct device *dev;
	struct gpio_desc *reset;
	struct regmap *gpio_map;
	u32 preserved;
	struct j713_speaker_group gate;
	struct mutex members;
	struct i2c_client *codec[4];
	u8 gain[4], dvc[4];
	const struct j713_transport_ops *transport;
	void *transport_context;
	const void *control_owner;
	bool cancelled_owner;
	bool published;
	bool sensing;
	unsigned long mute_mask;
	u64 mute_ns;
	struct work_struct notify_cutoff;
	bool notifications_ready;
};

/* Output is denied unless this build names the reviewed conservative profile. */
static const struct j713_group_profile j713_profile = {
#ifdef J713_CONSERVATIVE_PROFILE_V1
	.accepted = true,
#else
	.accepted = false,
#endif
	.configure_ns = 2000 * NSEC_PER_MSEC,
	.lease_ns = 50 * NSEC_PER_MSEC,
};
static const unsigned short addresses[] = { 0x38, 0x3b, 0x39, 0x3c };
struct codec_membership { struct j713_audio *audio; struct i2c_client *client; };

static bool j713_node_at(struct device_node *node, const char *path)
{
	struct device_node *expected = of_find_node_by_path(path);
	bool match = expected && node == expected;
	of_node_put(expected);
	return match;
}

static int gpio_verify(struct j713_audio *a, unsigned int value)
{
	u32 hw, cache;
	int ret = regmap_read_bypassed(a->gpio_map, RESET_REG, &hw);
	if (ret)
		return ret;
	ret = regmap_read(a->gpio_map, RESET_REG, &cache);
	if (ret)
		return ret;
	return hw == (a->preserved | RESET_LOW | value) && cache == hw ? 0 : -EIO;
}

static int audio_low(void *context)
{
	struct j713_audio *a = context;
	int first, ret;
	if (a->gate.owner)
		WRITE_ONCE(a->cancelled_owner, true);
	first = gpiod_direction_output_raw(a->reset, 0);
	ret = regmap_write_bits(a->gpio_map, RESET_REG, RESET_MASK, RESET_LOW);
	if (!first)
		first = ret;
	ret = gpio_verify(a, 0);
	if (READ_ONCE(a->notifications_ready))
		schedule_work(&a->notify_cutoff);
	return first ?: ret;
}

static void audio_notify_cutoff(struct work_struct *work)
{
	struct j713_audio *a = container_of(work, struct j713_audio, notify_cutoff);
	/* Lifetime is joined by transport_leave; this callback only queues work. */
	mutex_lock(&a->members);
	if (a->transport && j713_audio_cancelled(a))
		a->transport->cancelled(a->transport_context);
	mutex_unlock(&a->members);
}

static int audio_high(void *context)
{
	struct j713_audio *a = context;
	int ret;
	/* Closing ownership publishes NULL before attempting LOW. */
	if (!READ_ONCE(a->control_owner) || READ_ONCE(a->control_owner) != a->gate.owner)
		return -EPERM;
	ret = gpiod_direction_output_raw(a->reset, 1);
	if (!ret)
		ret = regmap_write_bits(a->gpio_map, RESET_REG, RESET_MASK, RESET_LOW | 1);
	return ret ?: gpio_verify(a, 1);
}

/* Never cached. All callers own gate.operation_lock and members. */
static int amp_read(struct j713_audio *a, unsigned int index, u8 reg, u8 *value,
		    u64 generation)
{
	struct i2c_client *client = a->codec[index];
	struct i2c_msg msg[2];
	int ret = j713_group_check(&a->gate, generation);
	if (ret)
		return ret;
	if (!client)
		return -ENODEV;
	msg[0] = (struct i2c_msg){ .addr = client->addr, .len = 1, .buf = &reg };
	msg[1] = (struct i2c_msg){ .addr = client->addr, .flags = I2C_M_RD,
				.len = 1, .buf = value };
	ret = i2c_transfer(client->adapter, msg, ARRAY_SIZE(msg));
	if (ret != ARRAY_SIZE(msg))
		return ret < 0 ? ret : -EIO;
	return j713_group_check(&a->gate, generation);
}

/* Unverified store; callers must read the register back before relying on it. */
static int amp_store(struct j713_audio *a, unsigned int index, u8 reg, u8 value,
		     u64 generation)
{
	struct i2c_client *client = a->codec[index];
	u8 bytes[] = { reg, value };
	struct i2c_msg msg;
	int ret = j713_group_check(&a->gate, generation);
	if (ret)
		return ret;
	if (!client)
		return -ENODEV;
	msg = (struct i2c_msg){ .addr = client->addr, .len = sizeof(bytes), .buf = bytes };
	ret = i2c_transfer(client->adapter, &msg, 1);
	return ret == 1 ? 0 : ret < 0 ? ret : -EIO;
}

static int amp_write(struct j713_audio *a, unsigned int index, u8 reg, u8 value,
		     u64 generation)
{
	u8 got;
	int ret = amp_store(a, index, reg, value, generation);
	if (!ret)
		ret = amp_read(a, index, reg, &got, generation);
	return ret ?: got == value ? 0 : -EIO;
}

static int expect_byte(struct j713_audio *a, unsigned int index, u8 reg, u8 value,
		       u64 generation)
{
	u8 got;
	int ret = amp_read(a, index, reg, &got, generation);
	return ret ?: got == value ? 0 : -EPROTO;
}

static int verify_limits(struct j713_audio *a, unsigned int i, u8 mode, u64 gen)
{
	int ret = expect_byte(a, i, 0, 0, gen);
	if (!ret)
		ret = expect_byte(a, i, 3, a->gain[i], gen);
	if (!ret)
		ret = expect_byte(a, i, 0x1a, a->dvc[i], gen);
	if (!ret)
		ret = expect_byte(a, i, 2, mode, gen);
	return ret;
}

static int audio_configure(void *context, struct j713_speaker_group *gate, u64 gen)
{
	struct j713_audio *a = context;
	static const u8 common[][2] = {
		{8,0x48}, {0x60,0x30}, {9,2}, {0x0a,0x1a}, {0x0d,0x13},
		{0x35,0xb0}, {0x6b,0x41}, {0x76,0},
	};
	static const u8 bop[24] = {
		1,0x32,2,0x22,0x83,0x2d,0x80,2,6,0x32,0x46,0x30,
		2,6,0x38,0x40,0x30,2,6,0x3e,0x37,0x30,0xff,0xe6,
	};
	static const u8 masks[4][4] = {{0xf0,0xf0,0,0},{0x0f,0x0f,0,0},{0},{0}};
	u8 force;
	unsigned int i, n;
	int ret = 0;
	mutex_lock(&a->members);
	a->sensing = false;
	/* Physical reset release precedes access; independent cutoff remains armed. */
	usleep_range(1000, 2000);
	for (i = 0; i < 4 && !ret; i++) {
		if (!a->codec[i] || a->gain[i] > J713_GAIN_MAX ||
		    a->dvc[i] > J713_DVC_MAX) {
			ret = -EPERM;
			break;
		}
		ret = amp_write(a, i, 0, 0, gen);
		if (!ret) ret = expect_byte(a, i, 2, 0x1a, gen);
		if (!ret) ret = expect_byte(a, i, 0x7d, 0x30, gen);
	}
	for (i = 0; i < 4 && !ret; i++) {
		ret = amp_write(a, i, 3, a->gain[i], gen);
		if (!ret) ret = amp_write(a, i, 0x1a, a->dvc[i], gen);
		if (!ret) ret = amp_write(a, i, 0x0c, 0x10 | i, gen);
		for (n = 0; n < ARRAY_SIZE(common) && !ret; n++)
			ret = amp_write(a, i, common[n][0], common[n][1], gen);
		for (n = 0; n < ARRAY_SIZE(bop) && !ret; n++)
			ret = amp_write(a, i, 0x1d + n, bop[n], gen);
		if (!ret) ret = amp_write(a, i, 0x0e, 0x40 | (i * 4), gen);
		if (!ret) ret = amp_write(a, i, 0x0f, 0x42 | (i * 4), gen);
		if (!ret) ret = amp_write(a, i, 0, 1, gen);
		if (!ret) ret = amp_read(a, i, 0x45, &force, gen);
		if (!ret && (force & 0x80)) ret = -EPROTO;
		for (n = 4; n < 8 && !ret; n++)
			ret = expect_byte(a, i, 0x3d + n, 0, gen);
		for (n = 0; n < 4 && !ret; n++)
			ret = amp_write(a, i, 0x3d + n, masks[i][n], gen);
		if (!ret) ret = amp_write(a, i, 0x45, force | (i < 2 ? 0x80 : 0), gen);
		if (!ret) ret = amp_write(a, i, 0, 0, gen);
		if (!ret) ret = verify_limits(a, i, 0x1a, gen);
	}
	for (i = 0; i < 4 && !ret; i++)
		ret = amp_write(a, i, 2, 0x82, gen);
	mutex_unlock(&a->members);
	return ret;
}

static int audio_active(void *context, struct j713_speaker_group *gate, u64 gen)
{
	struct j713_audio *a = context;
	unsigned int i;
	int ret;
	mutex_lock(&a->members);
	if (!a->transport || !a->sensing)
		ret = -EPERM;
	else if (READ_ONCE(a->mute_mask))
		ret = J713_GROUP_ACTIVATE_DEFERRED;
	else {
		ret = a->transport->running(a->transport_context);
		/* STOP publishes its veto before waiting for operation_lock. No
		 * register operation has occurred, so keep the sensing lease. */
		if (ret == -EAGAIN)
			ret = J713_GROUP_ACTIVATE_DEFERRED;
	}
	for (i = 0; i < 4 && !ret; i++)
		ret = verify_limits(a, i, 0x81, gen);
	for (i = 0; i < 4 && !ret; i++)
		ret = amp_write(a, i, 2, 0x80, gen);
	mutex_unlock(&a->members);
	return ret;
}

static int audio_quiescent(void *context)
{
	struct j713_audio *a = context;
	unsigned int i;
	int ret = 0;
	mutex_lock(&a->members);
	for (i = 0; i < 4; i++)
		if (!a->codec[i]) ret = -ENODEV;
	if (!ret)
		ret = a->transport ? a->transport->quiescent(a->transport_context) : -ENODEV;
	mutex_unlock(&a->members);
	return ret;
}

static const struct j713_group_ops gate_ops = {
	.force_low = audio_low, .release_reset = audio_high,
	.configure_muted = audio_configure, .enter_active = audio_active,
	.verify_quiescent = audio_quiescent,
};

struct j713_audio *devm_j713_audio_get(struct device *consumer)
{
	struct device_node *node;
	struct platform_device *pdev;
	struct j713_audio *a;
	struct device_link *link;
	if (!of_machine_is_compatible("apple,j713") ||
	    !of_machine_is_compatible("apple,t8132")) return ERR_PTR(-ENODEV);
	node = of_parse_phandle(consumer->of_node, "apple,speaker-group", 0);
	if (!node) return ERR_PTR(-EINVAL);
	pdev = of_find_device_by_node(node);
	of_node_put(node);
	if (!pdev) return ERR_PTR(-EPROBE_DEFER);
	device_lock(&pdev->dev);
	a = platform_get_drvdata(pdev);
	if (!pdev->dev.driver || !a || !a->published) {
		a = ERR_PTR(-EPROBE_DEFER);
		goto out;
	}
	link = device_link_add(consumer, &pdev->dev, DL_FLAG_AUTOREMOVE_CONSUMER);
	if (!link) a = ERR_PTR(-EINVAL);
out:
	device_unlock(&pdev->dev);
	put_device(&pdev->dev);
	return a;
}
EXPORT_SYMBOL_GPL(devm_j713_audio_get);

static void codec_leave(void *data)
{
	struct codec_membership *member = data;
	struct i2c_client *client = member->client;
	struct j713_audio *a = member->audio;
	unsigned int i;
	/* Managed supplier link keeps the provider alive through this action. */
	j713_group_revoke(&a->gate);
	mutex_lock(&a->gate.operation_lock);
	mutex_lock(&a->members);
	for (i = 0; i < 4; i++)
		if (a->codec[i] == client) a->codec[i] = NULL;
	mutex_unlock(&a->members);
	mutex_unlock(&a->gate.operation_lock);
}

int devm_j713_codec_join(struct j713_audio *a, struct i2c_client *client,
			unsigned int *index)
{
	struct codec_membership *member;
	unsigned int i;
	int ret = -EINVAL;
	if (!of_device_is_compatible(client->dev.of_node, "ti,sn012776") ||
	    !client->adapter->dev.of_node ||
	    !j713_node_at(client->adapter->dev.of_node, "/soc/i2c@3ad01c000")) return -ENODEV;
	member = devm_kzalloc(&client->dev, sizeof(*member), GFP_KERNEL);
	if (!member) return -ENOMEM;
	member->audio = a;
	member->client = client;
	mutex_lock(&a->members);
	for (i = 0; i < 4; i++)
		if (a->codec[i] && a->codec[i]->adapter != client->adapter) {
			ret = -EXDEV;
			goto out;
		}
	for (i = 0; i < 4; i++) {
		if (client->addr != addresses[i]) continue;
		if (a->codec[i]) { ret = -EBUSY; break; }
		a->codec[i] = client;
		*index = i;
		ret = 0;
		break;
	}
out:
	mutex_unlock(&a->members);
	if (ret) return ret;
	return devm_add_action_or_reset(&client->dev, codec_leave, member);
}
EXPORT_SYMBOL_GPL(devm_j713_codec_join);

static void transport_leave(void *data)
{
	struct j713_audio *a = data;
	j713_group_revoke(&a->gate);
	mutex_lock(&a->gate.operation_lock);
	mutex_lock(&a->members);
	a->transport = NULL;
	a->transport_context = NULL;
	mutex_unlock(&a->members);
	mutex_unlock(&a->gate.operation_lock);
}
int devm_j713_transport_join(struct j713_audio *a, struct device *consumer,
			    const struct j713_transport_ops *ops, void *context)
{
	int ret = 0;
	if (!ops || !ops->quiescent || !ops->running || !ops->sensing ||
	    !ops->cancelled) return -EINVAL;
	mutex_lock(&a->members);
	if (a->transport) ret = -EBUSY;
	else { a->transport = ops; a->transport_context = context; }
	mutex_unlock(&a->members);
	if (ret) return ret;
	return devm_add_action_or_reset(consumer, transport_leave, a);
}
EXPORT_SYMBOL_GPL(devm_j713_transport_join);

int j713_audio_revoke(struct j713_audio *a)
{
	int ret = -EBUSY;
	/* A late control unlock must not spoil a completed, ownerless stop. */
	mutex_lock(&a->gate.gpio_lock);
	if (!a->gate.owner && a->gate.phase == J713_GROUP_LOW &&
	    !a->gate.faulted && !a->gate.removing)
		ret = gpio_verify(a, 0);
	mutex_unlock(&a->gate.gpio_lock);
	return ret ? j713_group_revoke(&a->gate) : 0;
}
EXPORT_SYMBOL_GPL(j713_audio_revoke);
int j713_audio_prepare(struct j713_audio *a, const void *owner)
{
	u64 generation;
	/* Control owner remains stable under the card's ownership mutex at entry. */
	if (!owner) owner = READ_ONCE(a->control_owner);
	if (!owner || owner != READ_ONCE(a->control_owner) || READ_ONCE(a->cancelled_owner)) return -EPERM;
	return j713_group_start(&a->gate, owner, &generation);
}
EXPORT_SYMBOL_GPL(j713_audio_prepare);
bool j713_audio_owned(struct j713_audio *a)
{
	return READ_ONCE(a->control_owner) && !READ_ONCE(a->cancelled_owner);
}
EXPORT_SYMBOL_GPL(j713_audio_owned);
bool j713_audio_cancelled(struct j713_audio *a)
{
	bool cancelled;
	mutex_lock(&a->gate.gpio_lock);
	cancelled = a->gate.generation && a->gate.cancelled;
	mutex_unlock(&a->gate.gpio_lock);
	return cancelled;
}
EXPORT_SYMBOL_GPL(j713_audio_cancelled);
int j713_audio_low_ready(struct j713_audio *a)
{
	int ret;
	mutex_lock(&a->gate.gpio_lock);
	ret = a->gate.phase != J713_GROUP_LOW || a->gate.owner ||
		a->gate.faulted || a->gate.removing ? -EBUSY : gpio_verify(a, 0);
	mutex_unlock(&a->gate.gpio_lock);
	return ret;
}
EXPORT_SYMBOL_GPL(j713_audio_low_ready);
int j713_audio_set_owner(struct j713_audio *a, const void *owner, bool claim)
{
	int ret = 0;
	if (!owner) return -EINVAL;
	if (!claim) {
		if (cmpxchg(&a->control_owner, owner, NULL) != owner) return -EPERM;
		return j713_audio_revoke(a);
	}
	mutex_lock(&a->gate.operation_lock);
	if (cmpxchg(&a->control_owner, NULL, owner)) ret = -EBUSY;
	else WRITE_ONCE(a->cancelled_owner, false);
	mutex_unlock(&a->gate.operation_lock);
	return ret;
}
EXPORT_SYMBOL_GPL(j713_audio_set_owner);
static u64 audio_epoch(struct j713_audio *a)
{
	u64 gen;
	mutex_lock(&a->gate.gpio_lock);
	gen = a->gate.cancelled || a->gate.removing ? 0 : a->gate.generation;
	mutex_unlock(&a->gate.gpio_lock);
	return gen;
}
u64 j713_audio_generation(struct j713_audio *a)
{
	return smp_load_acquire(&a->sensing) ? audio_epoch(a) : 0;
}
EXPORT_SYMBOL_GPL(j713_audio_generation);

int j713_audio_sensing(struct j713_audio *a)
{
	u64 gen;
	unsigned int i;
	int ret;
	mutex_lock(&a->gate.operation_lock);
	mutex_lock(&a->members);
	gen = audio_epoch(a);
	ret = !gen || !a->transport ? -EPERM : a->transport->sensing(a->transport_context);
	if (!ret && !a->sensing) {
		for (i = 0; i < 4 && !ret; i++) ret = verify_limits(a, i, 0x82, gen);
		for (i = 0; i < 4 && !ret; i++) ret = amp_write(a, i, 2, 0x81, gen);
		if (!ret) {
			a->mute_ns = ktime_get_ns();
			smp_store_release(&a->sensing, true);
		}
	}
	mutex_unlock(&a->members);
	mutex_unlock(&a->gate.operation_lock);
	if (ret) j713_group_revoke(&a->gate);
	return ret;
}
EXPORT_SYMBOL_GPL(j713_audio_sensing);

int j713_audio_playback_mute(struct j713_audio *a)
{
	u64 gen;
	unsigned int i;
	bool active;
	int ret = 0;
	/* MCA marks TX stopping before entry, so no later activation can race it. */
	mutex_lock(&a->gate.operation_lock);
	mutex_lock(&a->members);
	gen = audio_epoch(a);
	mutex_lock(&a->gate.gpio_lock);
	active = a->gate.phase == J713_GROUP_ACTIVE;
	mutex_unlock(&a->gate.gpio_lock);
	if (gen && active) {
		for (i = 0; i < 4 && !ret; i++) ret = verify_limits(a, i, 0x80, gen);
		for (i = 0; i < 4 && !ret; i++) ret = amp_write(a, i, 2, 0x81, gen);
		if (!ret) ret = j713_group_check(&a->gate, gen);
		if (!ret) {
			mutex_lock(&a->gate.gpio_lock);
			if (a->gate.cancelled || gen != a->gate.generation) ret = -ECANCELED;
			else { a->gate.phase = J713_GROUP_SENSING; a->mute_ns = ktime_get_ns(); }
			mutex_unlock(&a->gate.gpio_lock);
		}
	}
	mutex_unlock(&a->members);
	mutex_unlock(&a->gate.operation_lock);
	if (ret) j713_group_revoke(&a->gate);
	return ret;
}
EXPORT_SYMBOL_GPL(j713_audio_playback_mute);

int j713_audio_codec_mute(struct j713_audio *a, unsigned int index, bool mute)
{
	if (index >= 4) return -EINVAL;
	if (mute) {
		set_bit(index, &a->mute_mask);
		return j713_audio_playback_mute(a);
	}
	clear_bit(index, &a->mute_mask);
	return 0;
}
EXPORT_SYMBOL_GPL(j713_audio_codec_mute);

int j713_audio_renew(struct j713_audio *a, const void *owner, s64 gen, s64 seq)
{
	bool active, sensing, settled, playback;
	unsigned int i;
	u8 mode;
	int ret;
	mutex_lock(&a->gate.operation_lock);
	mutex_lock(&a->members);
	sensing = a->sensing;
	ret = a->transport ? a->transport->running(a->transport_context) : -ENODEV;
	playback = !ret && !READ_ONCE(a->mute_mask);
	if (ret && ret != -EAGAIN) goto out;
	settled = sensing && ktime_get_ns() - a->mute_ns >= 33 * NSEC_PER_MSEC;
	mutex_lock(&a->gate.gpio_lock);
	active = a->gate.phase == J713_GROUP_ACTIVE;
	mutex_unlock(&a->gate.gpio_lock);
	mode = active ? 0x80 : 0x81;
	ret = !sensing || READ_ONCE(a->control_owner) != owner ? -EPERM :
		j713_group_check(&a->gate, gen);
	for (i = 0; i < 4 && !ret; i++) ret = verify_limits(a, i, mode, gen);
	if (!ret) ret = j713_group_renew(&a->gate, owner, gen, seq);
out:
	mutex_unlock(&a->members);
	if (!ret && playback && settled && !active)
		ret = j713_group_activate_locked(&a->gate, owner, gen);
	mutex_unlock(&a->gate.operation_lock);
	if (ret) {
		j713_group_revoke(&a->gate);
		return ret;
	}
	return 0;
}
EXPORT_SYMBOL_GPL(j713_audio_renew);

int j713_audio_stop(struct j713_audio *a, const void *owner)
{
	return j713_group_stop(&a->gate, owner);
}
EXPORT_SYMBOL_GPL(j713_audio_stop);
int j713_audio_quiesced(struct j713_audio *a)
{
	const void *owner;
	int ret;
	mutex_lock(&a->gate.gpio_lock);
	owner = a->gate.owner;
	mutex_unlock(&a->gate.gpio_lock);
	if (owner) return j713_group_stop(&a->gate, owner);
	mutex_lock(&a->gate.operation_lock);
	ret = audio_quiescent(a);
	mutex_lock(&a->gate.gpio_lock);
	if (!ret && (a->gate.owner || a->gate.faulted || a->gate.removing)) ret = -EBUSY;
	if (!ret) ret = gpio_verify(a, 0);
	if (!ret) a->gate.phase = J713_GROUP_LOW;
	mutex_unlock(&a->gate.gpio_lock);
	mutex_unlock(&a->gate.operation_lock);
	return ret;
}
EXPORT_SYMBOL_GPL(j713_audio_quiesced);

int j713_audio_level(struct j713_audio *a, unsigned int index,
		     unsigned int reg, unsigned int *value, bool write)
{
	u8 *stored, old, mode;
	u64 gen;
	int ret = 0;
	if (index >= 4 || (reg != 3 && reg != 0x1a)) return -EINVAL;
	if (write && ((reg == 3 && (*value > J713_GAIN_MAX || (*value & 1))) ||
	    (reg == 0x1a && (*value > J713_DVC_MAX)))) return -ERANGE;
	mutex_lock(&a->gate.operation_lock);
	mutex_lock(&a->members);
	stored = reg == 3 ? &a->gain[index] : &a->dvc[index];
	gen = audio_epoch(a);
	if (!a->codec[index]) { ret = -ENODEV; goto out; }
	if (!gen) {
		if (write) *stored = *value;
		else *value = *stored;
		goto out;
	}
	/* The next lease validates hardware again; unchanged puts perform no write. */
	if (write && *value == *stored) goto out;
	mutex_lock(&a->gate.gpio_lock);
	mode = a->gate.phase == J713_GROUP_ACTIVE ? 0x80 : a->sensing ? 0x81 : 0x82;
	mutex_unlock(&a->gate.gpio_lock);
	/*
	 * verify_limits() reads page, gain, DVC and mode back against the stored
	 * values, the new one included. A separate read-back of the written
	 * register would repeat that check and costs a bus transfer per amp in the
	 * protection daemon's renewal window.
	 */
	old = *stored;
	if (write) ret = amp_store(a, index, reg, *value, gen);
	if (!ret && write) *stored = *value;
	if (!ret) ret = verify_limits(a, index, mode, gen);
	if (ret && write) *stored = old;
	if (!ret) *value = *stored;
out:
	mutex_unlock(&a->members);
	mutex_unlock(&a->gate.operation_lock);
	if (ret) j713_group_revoke(&a->gate);
	return ret;
}
EXPORT_SYMBOL_GPL(j713_audio_level);

int j713_audio_sensor(struct j713_audio *a, unsigned int index, bool is_current,
		      unsigned int *enabled)
{
	u64 gen;
	u8 mode;
	int ret = 0;
	if (index >= 4) return -EINVAL;
	*enabled = 0;
	mutex_lock(&a->gate.operation_lock);
	mutex_lock(&a->members);
	gen = audio_epoch(a);
	if (gen && a->sensing) {
		ret = expect_byte(a, index, 0, 0, gen);
		if (!ret) ret = amp_read(a, index, 2, &mode, gen);
		if (!ret && mode != 0x80 && mode != 0x81) ret = -EPROTO;
		if (!ret) *enabled = !(mode & (is_current ? BIT(3) : BIT(4)));
	}
	mutex_unlock(&a->members);
	mutex_unlock(&a->gate.operation_lock);
	if (ret) j713_group_revoke(&a->gate);
	return ret;
}
EXPORT_SYMBOL_GPL(j713_audio_sensor);

struct j713_status_snapshot {
	u64 generation, sequence, deadline_ns, mute_ns;
	unsigned long mute_mask;
	int phase, error;
	bool owner, control_owner, cancelled, faulted, removing, sensing;
};
static void j713_status_snapshot(struct j713_audio *a, struct j713_status_snapshot *s)
{
	memset(s, 0, sizeof(*s));
	mutex_lock(&a->gate.gpio_lock);
	s->generation = a->gate.generation;
	s->sequence = a->gate.sequence;
	s->deadline_ns = a->gate.deadline_ns;
	s->phase = a->gate.phase;
	s->error = a->gate.error;
	s->owner = !!a->gate.owner;
	s->control_owner = !!READ_ONCE(a->control_owner);
	s->cancelled = a->gate.cancelled;
	s->faulted = a->gate.faulted;
	s->removing = a->gate.removing;
	s->sensing = READ_ONCE(a->sensing);
	s->mute_ns = READ_ONCE(a->mute_ns);
	s->mute_mask = READ_ONCE(a->mute_mask);
	mutex_unlock(&a->gate.gpio_lock);
}
static ssize_t status_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct j713_audio *a = dev_get_drvdata(dev);
	struct j713_status_snapshot before, after;
	u32 hw = 0, cache = 0;
	bool stable, low;
	int ret;
	if (!a || !READ_ONCE(a->published)) return -ENODEV;
	j713_status_snapshot(a, &before);
	/* MMIO/cache reads never hold the GPIO mutex that serves the cutoff. */
	ret = regmap_read_bypassed(a->gpio_map, RESET_REG, &hw);
	if (!ret) ret = regmap_read(a->gpio_map, RESET_REG, &cache);
	j713_status_snapshot(a, &after);
	stable = !memcmp(&before, &after, sizeof(before));
	low = stable && !ret && hw == (a->preserved | RESET_LOW) && cache == hw;
	return sysfs_emit(buf, "stable=%u phase=%d generation=%llu public_generation=%llu owner=%u control_owner=%u sequence=%llu deadline_ns=%llu cancelled=%u faulted=%u removing=%u error=%d sensing=%u mute_ns=%llu mute_mask=%lx gpio_hw=%08x gpio_cache=%08x gpio_error=%d gpio_low_verified=%u profile=%u\n",
		stable, after.phase, (unsigned long long)after.generation,
		(unsigned long long)(after.sensing && !after.cancelled && !after.removing ? after.generation : 0),
		after.owner, after.control_owner, (unsigned long long)after.sequence,
		(unsigned long long)after.deadline_ns, after.cancelled, after.faulted,
		after.removing, after.error, after.sensing, (unsigned long long)after.mute_ns,
		after.mute_mask, hw, cache, ret, low, a->gate.profile.accepted);
}
static DEVICE_ATTR_RO(status);
static struct attribute *j713_attrs[] = { &dev_attr_status.attr, NULL };
ATTRIBUTE_GROUPS(j713);

static int j713_probe(struct platform_device *pdev)
{
	struct j713_audio *a;
	struct gpio_chip *chip;
	u32 hw, cache;
	unsigned int i;
	int ret;
	if (!of_machine_is_compatible("apple,j713") ||
	    !of_machine_is_compatible("apple,t8132")) return -ENODEV;
	a = devm_kzalloc(&pdev->dev, sizeof(*a), GFP_KERNEL);
	if (!a) return -ENOMEM;
	a->dev = &pdev->dev;
	mutex_init(&a->members);
	a->mute_mask = 0xf;
	INIT_WORK(&a->notify_cutoff, audio_notify_cutoff);
	a->reset = devm_gpiod_get(&pdev->dev, "reset", GPIOD_ASIS);
	if (IS_ERR(a->reset)) return PTR_ERR(a->reset);
	chip = gpiod_to_chip(a->reset);
	if (!chip || !chip->parent || chip->can_sleep || chip->ngpio != 224 ||
	    strcmp(chip->label, "39a000000.pinctrl") ||
	    gpiod_is_active_low(a->reset) ||
	    gpio_device_get_desc(gpiod_to_gpio_device(a->reset), 15) != a->reset ||
	    !of_device_is_compatible(chip->parent->of_node, "apple,t8132-pinctrl")) return -ENODEV;
	a->gpio_map = dev_get_regmap(chip->parent, NULL);
	if (!a->gpio_map || regmap_get_val_bytes(a->gpio_map) != 4 ||
	    regmap_get_reg_stride(a->gpio_map) != 4) return -ENODEV;
	ret = regmap_read_bypassed(a->gpio_map, RESET_REG, &hw);
	if (!ret) ret = regmap_read(a->gpio_map, RESET_REG, &cache);
	if (ret || hw != cache) return ret ?: -ESTALE;
	a->preserved = hw & ~RESET_MASK;
	for (i = 0; i < 4; i++) { a->gain[i] = J713_GAIN_MAX; a->dvc[i] = J713_DVC_MIN; }
	ret = j713_group_init(&a->gate, &gate_ops, a, &j713_profile);
	if (ret) return ret;
	a->notifications_ready = true;
	platform_set_drvdata(pdev, a);
	a->published = true;
	return 0;
}

static void j713_remove(struct platform_device *pdev)
{
	struct j713_audio *a = platform_get_drvdata(pdev);
	/* Driver core unbinds managed consumers before this supplier callback. */
	a->published = false;
	if (j713_group_destroy(&a->gate))
		dev_crit(&pdev->dev, "shared reset LOW could not be verified on remove\n");
	WRITE_ONCE(a->notifications_ready, false);
	cancel_work_sync(&a->notify_cutoff);
}
static int j713_suspend(struct device *dev)
{
	return j713_group_revoke(&((struct j713_audio *)dev_get_drvdata(dev))->gate);
}
static const struct dev_pm_ops j713_pm = { .suspend = j713_suspend, .freeze = j713_suspend, .poweroff = j713_suspend };
static const struct of_device_id j713_match[] = {{ .compatible = "apple,j713-speaker-group" }, {}};
MODULE_DEVICE_TABLE(of, j713_match);
static struct platform_driver j713_driver = {
	.probe = j713_probe, .remove = j713_remove,
	.driver = { .name = "j713-speaker-group", .of_match_table = j713_match, .pm = &j713_pm, .dev_groups = j713_groups },
};
module_platform_driver(j713_driver);
MODULE_DESCRIPTION("J713 shared speaker reset and protected codec group");
MODULE_LICENSE("GPL");
