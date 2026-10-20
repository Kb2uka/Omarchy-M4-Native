/* SPDX-License-Identifier: GPL-2.0 OR MIT
 *
 * Host harness for the pinned Apple DockChannel HID transport.
 * The driver text spliced in at the markers below is
 * Copyright The Asahi Linux Contributors and stays under GPL-2.0 OR MIT.
 * Event init, ready, and GPIO dispatch are host mocks. The packet, ACK,
 * event, report, and checksum functions are the pinned driver.
 */
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uintptr_t dma_addr_t;

#define __packed __attribute__((packed))
#define GFP_KERNEL 0
#define HID_INPUT_REPORT 0
#define GENMASK(h, l) ((unsigned)(((1U << ((h) - (l) + 1)) - 1U) << (l)))
#define FIELD_GET(mask, reg) (((unsigned)(reg) & (unsigned)(mask)) >> __builtin_ctz((unsigned)(mask)))
#define container_of(ptr, type, member) ((type *)((char *)(ptr) - offsetof(type, member)))
#define dev_err(dev, fmt, ...) do { (void)(dev); (void)(fmt); error_count++; } while (0)
#define dev_warn(dev, fmt, ...) do { (void)(dev); (void)(fmt); warn_count++; } while (0)
#define dev_info(dev, fmt, ...) do { (void)(dev); (void)(fmt); } while (0)
#define WARN_ON_ONCE(cond) do { if (cond) warn_count++; } while (0)
#define INIT_WORK(item, fn) ((item)->func = (fn))
#define min(a, b) ((a) < (b) ? (a) : (b))

void __asan_poison_memory_region(void const volatile *addr, size_t size);
void __asan_unpoison_memory_region(void const volatile *addr, size_t size);

struct device { int unused; };
struct device_node { int unused; };
struct gpio_desc { int unused; };
struct apple_smc { int unused; };
struct dockchannel { int unused; };
struct device_link { int unused; };
struct mutex { int unused; };
struct completion { int done; };
struct workqueue_struct { int unused; };
struct hid_device { int unused; };
struct work_struct { void (*func)(struct work_struct *work); };

static int error_count;
static int warn_count;

/* ACTUAL_TYPES */

static_assert(sizeof(struct dchid_hdr) == 8, "pinned header is 8 bytes");
static_assert(sizeof(struct dchid_subhdr) == 8, "pinned subheader is 8 bytes");
static_assert(MAX_INTERFACES == 16, "pinned interface table has 16 slots");
static_assert(offsetof(struct dockchannel_hid, pkt_buf) ==
	      offsetof(struct dockchannel_hid, ifaces) + sizeof(struct dchid_iface *) * MAX_INTERFACES,
	      "interface 16 aliases the packet buffer");
static_assert(__BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__, "checksum fixture is little-endian");
static_assert(sizeof(void *) == 8, "interface slots are 8-byte pointers");

static struct device g_dev;
static struct hid_device g_hid;
static struct dockchannel_hid g_dchid;
static struct dchid_iface g_iface;
static struct dchid_iface sentinel;

static u8 rx_data[MAX_PKT_SIZE + 64];
static size_t rx_len;
static size_t rx_pos;
static int await_count;
static int live_allocs;
static int fail_alloc;
static int report_count;
static int report_type;
static size_t report_length;
static u8 report_bytes[64];
static int init_calls;
static int ready_calls;
static int gpio_calls;
static struct dchid_iface *queued_iface;

struct alloc_rec {
	void *obj;
	size_t size;
	int live;
};
static struct alloc_rec allocs[8];

static void *kzalloc(size_t n, int flags)
{
	void *obj;
	int i;

	(void)flags;
	if (fail_alloc) {
		fail_alloc--;
		return NULL;
	}
	obj = calloc(1, n + 64);
	if (!obj)
		return NULL;
	/* Poison the exact bytes the driver did not request. */
	__asan_poison_memory_region((char *)obj + n, 64);
	for (i = 0; i < 8; i++) {
		if (!allocs[i].live) {
			allocs[i].obj = obj;
			allocs[i].size = n;
			allocs[i].live = 1;
			live_allocs++;
			return obj;
		}
	}
	fprintf(stderr, "harness alloc table full\n");
	exit(2);
}

static void kfree(void *p)
{
	int i;

	if (!p)
		return;
	for (i = 0; i < 8; i++) {
		if (allocs[i].live && allocs[i].obj == p) {
			__asan_unpoison_memory_region((char *)p + allocs[i].size, 64);
			allocs[i].live = 0;
			live_allocs--;
			free(p);
			return;
		}
	}
	fprintf(stderr, "harness kfree of unknown pointer\n");
	exit(2);
}

static long dockchannel_recv(struct dockchannel *dc, void *buf, size_t len)
{
	(void)dc;
	if (rx_pos + len > rx_len)
		return -1;
	memcpy(buf, rx_data + rx_pos, len);
	rx_pos += len;
	return (long)len;
}

static void complete(struct completion *c)
{
	c->done++;
}

static int dockchannel_await(struct dockchannel *dc, void (*cb)(void *, size_t), void *cookie, size_t nbytes)
{
	(void)dc;
	(void)cb;
	(void)cookie;
	(void)nbytes;
	await_count++;
	return 0;
}

static bool queue_work(struct workqueue_struct *wq, struct work_struct *work)
{
	struct dchid_work *item = container_of(work, struct dchid_work, work);

	(void)wq;
	queued_iface = item->iface;
	work->func(work);
	return 1;
}

static int hid_input_report(struct hid_device *hid, int type, void *data, size_t length, int interrupt)
{
	(void)hid;
	(void)interrupt;
	report_count++;
	report_type = type;
	report_length = length;
	if (length > sizeof(report_bytes)) {
		fprintf(stderr, "report longer than the harness capture\n");
		exit(2);
	}
	if (length)
		memcpy(report_bytes, data, length);
	return 0;
}

static void dchid_handle_init(struct dockchannel_hid *dchid, void *data, size_t length)
{
	(void)dchid;
	(void)data;
	(void)length;
	init_calls++;
}

static void dchid_handle_ready(struct dockchannel_hid *dchid, void *data, size_t length)
{
	(void)dchid;
	(void)data;
	(void)length;
	ready_calls++;
}

static void dchid_handle_gpio(struct dockchannel_hid *dchid, void *data, size_t length)
{
	(void)dchid;
	(void)data;
	(void)length;
	gpio_calls++;
}

static u32 get_unaligned_le32(const void *p)
{
	u32 value;

	memcpy(&value, p, sizeof(value));
	return value;
}

/* ACTUAL_CHECKSUM */

/* ACTUAL_RX */

static void reset_state(void)
{
	memset(&g_dchid, 0, sizeof(g_dchid));
	memset(&g_iface, 0, sizeof(g_iface));
	memset(&sentinel, 0, sizeof(sentinel));
	memset(allocs, 0, sizeof(allocs));
	memset(report_bytes, 0, sizeof(report_bytes));
	g_dchid.dev = &g_dev;
	g_iface.dchid = &g_dchid;
	g_iface.name = "fixture";
	sentinel.dchid = &g_dchid;
	rx_len = 0;
	rx_pos = 0;
	await_count = 0;
	live_allocs = 0;
	fail_alloc = 0;
	report_count = 0;
	report_type = -1;
	report_length = 0;
	init_calls = 0;
	ready_calls = 0;
	gpio_calls = 0;
	queued_iface = NULL;
	error_count = 0;
	warn_count = 0;
}

static int fail(const char *msg)
{
	fprintf(stderr, "FAIL %s\n", msg);
	return 1;
}

static int pass(const char *name)
{
	printf("PASS %s await=%d live=%d reports=%d events=%d errors=%d warns=%d\n",
	       name, await_count, live_allocs, report_count,
	       init_calls + ready_calls + gpio_calls, error_count, warn_count);
	return 0;
}

/* Build one frame whose real dchid_checksum matches the driver's accept rule. */
static int load_rx(struct dchid_hdr hdr, const void *payload, size_t length)
{
	u8 *body;
	u32 sum;
	u32 word;
	u32 check;

	if (length > 0xffffu)
		return 2;
	hdr.hdr_len = sizeof(hdr);
	hdr.length = (u16)length;
	memset(rx_data, 0, sizeof(rx_data));
	memcpy(rx_data, &hdr, sizeof(hdr));
	body = rx_data + sizeof(hdr);
	if (payload && length)
		memcpy(body, payload, length);
	/*
	 * Unaligned lengths, including 1..3, are malformed frames. Zero the
	 * length+4 window and plant the compensating checksum in the first
	 * word so the driver's accept rule still matches.
	 */
	if (length % 4 != 0)
		memset(body, 0, length + 4);
	else
		memset(body + length, 0, 4);
	sum = dchid_checksum(&hdr, sizeof(hdr));
	sum += dchid_checksum(body, length + 4);
	word = 0xffffffffu - sum;
	if (length % 4 != 0)
		memcpy(body, &word, sizeof(word));
	else
		memcpy(body + length, &word, sizeof(word));
	check = dchid_checksum(&hdr, sizeof(hdr));
	check += dchid_checksum(body, length + 4);
	if (check != 0xffffffffu)
		return 2;
	rx_len = sizeof(hdr) + length + 4;
	rx_pos = 0;
	return 0;
}

static struct dchid_hdr report_hdr(u8 iface)
{
	struct dchid_hdr hdr;

	memset(&hdr, 0, sizeof(hdr));
	hdr.channel = DCHID_CHANNEL_REPORT;
	hdr.iface = iface;
	return hdr;
}

static int case_normal_report_on(unsigned number, const char *name, size_t payload_len)
{
	struct dchid_hdr hdr = report_hdr((u8)number);
	struct dchid_subhdr sub;
	u8 body[12];
	const u8 payload[4] = {0xde, 0xad, 0xbe, 0xef};

	/* Outer body stays 12 and 4-byte aligned. payload_len is the subheader length. */
	if (payload_len == 0 || payload_len > sizeof(payload) || sizeof(sub) + payload_len > sizeof(body))
		return 2;
	reset_state();
	g_iface.hid = &g_hid;
	g_iface.open = 1;
	g_iface.index = (u8)number;
	g_dchid.ifaces[number] = &g_iface;
	memset(&sub, 0, sizeof(sub));
	sub.length = (u16)payload_len;
	memset(body, 0, sizeof(body));
	memcpy(body, &sub, sizeof(sub));
	memcpy(body + sizeof(sub), payload, payload_len);
	if (load_rx(hdr, body, sizeof(body)) != 0)
		return 2;
	dchid_handle_packet(&g_dchid, sizeof(hdr));
	if (report_count != 1 || report_type != HID_INPUT_REPORT || report_length != payload_len)
		return fail("normal report was not delivered");
	if (memcmp(report_bytes, payload, payload_len) != 0)
		return fail("normal report payload mismatch");
	if (init_calls || ready_calls || gpio_calls)
		return fail("normal report dispatched a comm event");
	if (live_allocs != 0)
		return fail("normal report leaked the worker");
	if (await_count != 1)
		return fail("normal report did not rearm receive");
	return pass(name);
}

static int case_normal_ack(void)
{
	struct dchid_hdr hdr;
	struct dchid_subhdr sub;
	u8 body[16];
	u8 response[8];
	const u8 expect[4] = {0x10, 0x20, 0x30, 0x40};

	reset_state();
	g_iface.index = 1;
	g_iface.tx_seq = 9;
	g_iface.out_flags = 0x2a;
	g_iface.out_report = 0x22;
	g_iface.resp_buf = response;
	g_iface.resp_size = sizeof(expect);
	g_dchid.ifaces[1] = &g_iface;
	memset(response, 0xee, sizeof(response));
	memset(&hdr, 0, sizeof(hdr));
	hdr.channel = DCHID_CHANNEL_CMD;
	hdr.iface = 1;
	hdr.seq = 9;
	memset(&sub, 0, sizeof(sub));
	sub.flags = 0x2a;
	sub.length = 5;
	sub.retcode = 0xa1b2c3d4u;
	memset(body, 0, sizeof(body));
	memcpy(body, &sub, sizeof(sub));
	body[8] = 0x22;
	memcpy(body + 9, expect, sizeof(expect));
	if (load_rx(hdr, body, sizeof(body)) != 0)
		return 2;
	dchid_handle_packet(&g_dchid, sizeof(hdr));
	if (report_count || init_calls || ready_calls || gpio_calls)
		return fail("ACK delivered a report or event");
	if (g_iface.out_complete.done != 1)
		return fail("ACK did not complete");
	if (g_iface.out_report != -1)
		return fail("ACK did not retire the outstanding report");
	if (g_iface.retcode != 0xa1b2c3d4u || g_iface.resp_size != 5)
		return fail("ACK result was not stored");
	if (memcmp(response, expect, sizeof(expect)) != 0)
		return fail("ACK response mismatch");
	if (live_allocs != 0)
		return fail("ACK leaked a worker");
	if (await_count != 1)
		return fail("ACK did not rearm receive");
	return pass("normal_ack");
}

static int case_iface(unsigned number, const char *name)
{
	struct dchid_hdr hdr = report_hdr((u8)number);
	u8 body[1920];
	void *ptr = &sentinel;
	size_t slot;
	size_t length;

	reset_state();
	sentinel.dchid = &g_dchid;
	memset(body, 0, sizeof(body));
	if (number < MAX_INTERFACES)
		return 2;
	slot = ((size_t)number - MAX_INTERFACES) * sizeof(ptr);
	if (slot + sizeof(ptr) > sizeof(body))
		return 2;
	memcpy(body + slot, &ptr, sizeof(ptr));
	length = slot + sizeof(ptr);
	if (length % 4 != 0)
		length += 4 - (length % 4);
	if (load_rx(hdr, body, length) != 0)
		return 2;
	dchid_handle_packet(&g_dchid, sizeof(hdr));
	if (queued_iface == &sentinel)
		return fail("interface was indexed");
	if (queued_iface != NULL)
		return fail("rejected interface queued work");
	if (report_count || init_calls || ready_calls || gpio_calls)
		return fail("rejected interface delivered a payload");
	if (live_allocs != 0)
		return fail("rejected interface leaked a worker");
	if (await_count != 1)
		return fail("rejected interface did not rearm receive");
	return pass(name);
}

static int case_short(size_t length, const char *name)
{
	struct dchid_hdr hdr = report_hdr(1);

	reset_state();
	g_iface.hid = &g_hid;
	g_iface.open = 1;
	g_iface.index = 1;
	g_dchid.ifaces[1] = &g_iface;
	if (load_rx(hdr, NULL, length) != 0)
		return 2;
	dchid_handle_packet(&g_dchid, sizeof(hdr));
	if (report_count || init_calls || ready_calls || gpio_calls)
		return fail("short subheader was delivered");
	if (queued_iface != NULL)
		return fail("short subheader queued work");
	if (live_allocs != 0)
		return fail("short subheader leaked the worker");
	if (await_count != 1)
		return fail("short subheader did not rearm receive");
	return pass(name);
}

static int case_empty_input(u8 iface, int with_hid, size_t length, const char *name)
{
	struct dchid_hdr hdr = report_hdr(iface);
	struct dchid_subhdr sub;
	u8 body[16];

	if (length < sizeof(sub) || length > sizeof(body) || (length % 4) != 0)
		return 2;
	reset_state();
	if (with_hid) {
		g_iface.hid = &g_hid;
		g_iface.open = 1;
		g_iface.index = iface;
	}
	g_dchid.ifaces[iface] = &g_iface;
	memset(&sub, 0, sizeof(sub));
	memset(body, 0, sizeof(body));
	memcpy(body, &sub, sizeof(sub));
	/* A zero-length payload must not consume the following byte. */
	if (length > sizeof(sub))
		body[sizeof(sub)] = EVENT_INIT;
	if (load_rx(hdr, body, length) != 0)
		return 2;
	dchid_handle_packet(&g_dchid, sizeof(hdr));
	if (init_calls || ready_calls || gpio_calls || report_count)
		return fail("zero-length input was dispatched");
	if (live_allocs != 0)
		return fail("zero-length input leaked the worker");
	if (await_count != 1)
		return fail("zero-length input did not rearm receive");
	return pass(name);
}

static int case_comm_ready(void)
{
	struct dchid_hdr hdr = report_hdr(IFACE_COMM);
	struct dchid_subhdr sub;
	u8 body[12];
	const u8 payload[2] = {EVENT_READY, 3};

	reset_state();
	g_dchid.ifaces[IFACE_COMM] = &g_iface;
	memset(&sub, 0, sizeof(sub));
	sub.length = sizeof(payload);
	memset(body, 0, sizeof(body));
	memcpy(body, &sub, sizeof(sub));
	memcpy(body + sizeof(sub), payload, sizeof(payload));
	if (load_rx(hdr, body, sizeof(body)) != 0)
		return 2;
	dchid_handle_packet(&g_dchid, sizeof(hdr));
	if (ready_calls != 1)
		return fail("READY event was not delivered once");
	if (init_calls || gpio_calls || report_count)
		return fail("READY event delivered another callback");
	if (live_allocs != 0)
		return fail("READY event leaked the worker");
	if (await_count != 1)
		return fail("READY event did not rearm receive");
	return pass("comm_ready");
}

static int case_oversized(void)
{
	struct dchid_hdr hdr = report_hdr(1);
	struct dchid_subhdr sub;
	u8 body[16];

	reset_state();
	g_iface.hid = &g_hid;
	g_iface.open = 1;
	g_iface.index = 1;
	g_dchid.ifaces[1] = &g_iface;
	memset(&sub, 0, sizeof(sub));
	sub.length = 100;
	memset(body, 0, sizeof(body));
	memcpy(body, &sub, sizeof(sub));
	if (load_rx(hdr, body, sizeof(body)) != 0)
		return 2;
	dchid_handle_packet(&g_dchid, sizeof(hdr));
	if (report_count || init_calls || ready_calls || gpio_calls)
		return fail("oversized subpayload was delivered");
	if (live_allocs != 0)
		return fail("oversized subpayload did not free the worker");
	if (await_count != 1)
		return fail("oversized subpayload did not rearm receive");
	return pass("oversized_subpayload");
}

static int case_alloc_fail(void)
{
	struct dchid_hdr hdr = report_hdr(1);
	struct dchid_subhdr sub;
	u8 body[12];

	reset_state();
	g_iface.hid = &g_hid;
	g_iface.open = 1;
	g_iface.index = 1;
	g_dchid.ifaces[1] = &g_iface;
	memset(&sub, 0, sizeof(sub));
	sub.length = 4;
	memset(body, 0x5a, sizeof(body));
	memcpy(body, &sub, sizeof(sub));
	if (load_rx(hdr, body, sizeof(body)) != 0)
		return 2;
	fail_alloc = 1;
	dchid_handle_packet(&g_dchid, sizeof(hdr));
	if (report_count || init_calls || ready_calls || gpio_calls)
		return fail("allocation failure still delivered a payload");
	if (queued_iface != NULL)
		return fail("allocation failure still queued work");
	if (live_allocs != 0)
		return fail("allocation failure leaked a worker");
	if (await_count != 1)
		return fail("allocation failure did not rearm receive");
	return pass("alloc_fail_rearm");
}

int main(int argc, char **argv)
{
	if (argc != 2) {
		fprintf(stderr, "usage: dockchannel_bounds_fixture CASE\n");
		return 2;
	}
	if (!strcmp(argv[1], "normal_report"))
		return case_normal_report_on(1, "normal_report", 4);
	if (!strcmp(argv[1], "iface_15"))
		return case_normal_report_on(15, "iface_15", 4);
	if (!strcmp(argv[1], "one_byte_report"))
		return case_normal_report_on(1, "one_byte_report", 1);
	if (!strcmp(argv[1], "normal_ack"))
		return case_normal_ack();
	if (!strcmp(argv[1], "iface_16"))
		return case_iface(16, "iface_16");
	if (!strcmp(argv[1], "iface_255"))
		return case_iface(255, "iface_255");
	if (!strcmp(argv[1], "short_0"))
		return case_short(0, "short_0");
	if (!strcmp(argv[1], "short_1"))
		return case_short(1, "short_1");
	if (!strcmp(argv[1], "short_2"))
		return case_short(2, "short_2");
	if (!strcmp(argv[1], "short_3"))
		return case_short(3, "short_3");
	if (!strcmp(argv[1], "short_4"))
		return case_short(4, "short_4");
	if (!strcmp(argv[1], "short_5"))
		return case_short(5, "short_5");
	if (!strcmp(argv[1], "short_6"))
		return case_short(6, "short_6");
	if (!strcmp(argv[1], "short_7"))
		return case_short(7, "short_7");
	if (!strcmp(argv[1], "empty_comm_event"))
		return case_empty_input(IFACE_COMM, 0, 16, "empty_comm_event");
	if (!strcmp(argv[1], "empty_comm_event_8"))
		return case_empty_input(IFACE_COMM, 0, 8, "empty_comm_event_8");
	if (!strcmp(argv[1], "empty_input_report"))
		return case_empty_input(1, 1, 12, "empty_input_report");
	if (!strcmp(argv[1], "comm_ready"))
		return case_comm_ready();
	if (!strcmp(argv[1], "oversized_subpayload"))
		return case_oversized();
	if (!strcmp(argv[1], "alloc_fail_rearm"))
		return case_alloc_fail();
	fprintf(stderr, "unknown case %s\n", argv[1]);
	return 2;
}
