/* SPDX-License-Identifier: GPL-2.0 OR MIT
 *
 * Host harness for the pinned Apple DockChannel HID transport.
 * The driver text spliced in at the markers below is
 * Copyright The Asahi Linux Contributors and stays under GPL-2.0 OR MIT.
 * Ready and GPIO command dispatch are host mocks. Init parsing, packet,
 * ACK, event, report, and checksum functions are the pinned driver.
 * Interface lookup, HID creation, and descriptor storage are host mocks
 * that still consume C strings the way the driver helper does.
 */
#include <assert.h>
#include <stdarg.h>
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
static int product_accepted;
static int product_rejected;
static char log_sink[160];

static void note_dev_log(const char *fmt, ...) __attribute__((format(printf, 1, 2)));

static void note_dev_log(const char *fmt, ...)
{
	va_list ap;

	va_start(ap, fmt);
	/* vsnprintf reads each %s through its NUL and does not write the source. */
	vsnprintf(log_sink, sizeof(log_sink), fmt, ap);
	va_end(ap);
	if (strstr(fmt, "Product name for %s: %s"))
		product_accepted++;
	if (strstr(fmt, "Unterminated product name"))
		product_rejected++;
}

#define dev_err(dev, fmt, ...) do { (void)(dev); error_count++; note_dev_log(fmt, ##__VA_ARGS__); } while (0)
#define dev_warn(dev, fmt, ...) do { (void)(dev); warn_count++; note_dev_log(fmt, ##__VA_ARGS__); } while (0)
#define dev_info(dev, fmt, ...) do { (void)(dev); note_dev_log(fmt, ##__VA_ARGS__); } while (0)
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
static_assert(sizeof(struct dchid_init_hdr) == 22, "pinned init header is 22 bytes");
static_assert(sizeof(struct dchid_init_block_hdr) == 4, "pinned init block header is 4 bytes");
static_assert(sizeof(struct dchid_gpio_request) == 36, "pinned gpio request is 36 bytes");

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
static int get_calls;
static int create_calls;
static int desc_calls;
static int last_get_index;
static size_t desc_len_seen;
static u8 desc_copy[64];
static char last_get_name[64];
static struct dchid_iface *queued_iface;

#define E2BIG 7

/* Kernel strscpy: stop at NUL or count, and always leave a terminator. */
static long strscpy(char *dst, const char *src, size_t count)
{
	size_t i;

	if (count == 0)
		return -E2BIG;
	for (i = 0; i < count; i++) {
		dst[i] = src[i];
		if (dst[i] == '\0')
			return (long)i;
	}
	dst[count - 1] = '\0';
	return -E2BIG;
}

/*
 * Platform half of dchid_get_interface. Index is bounded first, matching the
 * helper. A new slot still consumes name as a C string, so a 16-byte
 * non-terminated name walks out of the packet when init fails to reject it.
 */
static struct dchid_iface *
dchid_get_interface(struct dockchannel_hid *dchid, int index, const char *name)
{
	size_t n = 0;

	get_calls++;
	last_get_index = index;
	if (index < 0 || index >= MAX_INTERFACES) {
		error_count++;
		return NULL;
	}
	if (dchid->ifaces[index])
		return dchid->ifaces[index];

	while (name[n] != '\0')
		n++;
	if (n >= sizeof(last_get_name))
		n = sizeof(last_get_name) - 1;
	memcpy(last_get_name, name, n);
	last_get_name[n] = '\0';

	g_iface.index = index;
	g_iface.name = last_get_name;
	g_iface.dchid = dchid;
	dchid->ifaces[index] = &g_iface;
	return &g_iface;
}

static int dchid_create_interface(struct dchid_iface *iface)
{
	create_calls++;
	iface->creating = true;
	return 0;
}

static void dchid_handle_descriptor(struct dchid_iface *iface, void *hid_desc, size_t desc_len)
{
	(void)iface;
	desc_calls++;
	desc_len_seen = desc_len;
	if (desc_len > sizeof(desc_copy))
		return;
	if (desc_len)
		memcpy(desc_copy, hid_desc, desc_len);
}

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

/* ACTUAL_INIT */

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
	get_calls = 0;
	create_calls = 0;
	desc_calls = 0;
	last_get_index = -1;
	desc_len_seen = 0;
	product_accepted = 0;
	product_rejected = 0;
	memset(desc_copy, 0, sizeof(desc_copy));
	memset(last_get_name, 0, sizeof(last_get_name));
	memset(log_sink, 0, sizeof(log_sink));
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

static int rearmed(const char *what)
{
	if (live_allocs != 0)
		return fail(what);
	if (await_count != 1)
		return fail("packet path did not rearm receive");
	return 0;
}

static void prepare_comm(int id_ready)
{
	reset_state();
	g_iface.name = "comm";
	g_iface.index = IFACE_COMM;
	g_iface.dchid = &g_dchid;
	g_dchid.ifaces[IFACE_COMM] = &g_iface;
	g_dchid.id_ready = id_ready;
}

static int send_comm_payload(const void *payload, size_t payload_len)
{
	struct dchid_hdr hdr = report_hdr(IFACE_COMM);
	struct dchid_subhdr sub;
	u8 body[256];
	size_t length;

	if (payload_len > sizeof(body) - sizeof(sub))
		return 2;
	memset(&sub, 0, sizeof(sub));
	sub.length = (u16)payload_len;
	memset(body, 0, sizeof(body));
	memcpy(body, &sub, sizeof(sub));
	if (payload && payload_len)
		memcpy(body + sizeof(sub), payload, payload_len);
	length = sizeof(sub) + payload_len;
	if (length % 4)
		length += 4 - (length % 4);
	if (load_rx(hdr, body, length) != 0)
		return 2;
	dchid_handle_packet(&g_dchid, sizeof(hdr));
	return 0;
}

static size_t put_init_hdr(u8 *payload, u8 iface, const char *name)
{
	struct dchid_init_hdr ih;

	memset(&ih, 0, sizeof(ih));
	ih.type = EVENT_INIT;
	ih.iface = iface;
	if (name)
		memcpy(ih.name, name, strlen(name) + 1);
	memcpy(payload, &ih, sizeof(ih));
	return sizeof(ih);
}

static size_t put_block(u8 *payload, size_t at, u16 type, const void *data, u16 length)
{
	struct dchid_init_block_hdr blk;

	blk.type = type;
	blk.length = length;
	memcpy(payload + at, &blk, sizeof(blk));
	if (data && length)
		memcpy(payload + at + sizeof(blk), data, length);
	return at + sizeof(blk) + length;
}

static int case_init_product_zero(int walk_off)
{
	u8 payload[64];
	size_t at;
	const char *case_name = walk_off ? "init_product_zero_walk" : "init_product_zero";

	memset(payload, 0, sizeof(payload));
	at = put_init_hdr(payload, 4, "keyboard");
	at = put_block(payload, at, INIT_PRODUCT_NAME, NULL, 0);
	/* Header 22 + block 4 = 26. Two tail bytes make the 28-byte aligned worker.
	 * Length is zero, so its high byte (the byte before the product) stays 0.
	 */
	if (at != 26 || payload[at - 1] != 0)
		return 2;
	if (walk_off) {
		payload[at++] = 0xff;
		payload[at++] = 0xff;
	} else {
		payload[at++] = 0;
		payload[at++] = 0;
	}
	if (at != 28 || (at % 4) != 0)
		return 2;
	prepare_comm(0);
	if (send_comm_payload(payload, at) != 0)
		return 2;
	if (product_accepted != 0)
		return fail("zero-length product was accepted");
	if (get_calls != 1 || strcmp(last_get_name, "keyboard") != 0)
		return fail("zero-length product skipped interface lookup");
	if (g_iface.gpio_id != 0 || g_iface.gpio_name[0] != '\0')
		return fail("zero-length product changed gpio state");
	if (!g_iface.deferred || create_calls != 0)
		return fail("zero-length product did not defer interface creation");
	if (rearmed("zero-length product leaked the worker"))
		return 1;
	return pass(case_name);
}

static int case_init_product_empty(void)
{
	u8 payload[64];
	u8 text[1] = {0};
	size_t at;

	memset(payload, 0, sizeof(payload));
	at = put_init_hdr(payload, 4, "keyboard");
	at = put_block(payload, at, INIT_PRODUCT_NAME, text, sizeof(text));
	prepare_comm(0);
	if (send_comm_payload(payload, at) != 0)
		return 2;
	if (get_calls != 1 || strcmp(last_get_name, "keyboard") != 0)
		return fail("empty product skipped interface lookup");
	if (product_accepted != 1 || strstr(log_sink, "keyboard: ") == NULL)
		return fail("empty product was not logged");
	if (product_rejected != 0)
		return fail("empty product was treated as unterminated");
	if (!g_iface.deferred || create_calls != 0)
		return fail("empty product did not defer interface creation");
	if (rearmed("empty product leaked the worker"))
		return 1;
	return pass("init_product_empty");
}

static int case_init_product_text(void)
{
	u8 payload[64];
	const char text[] = "Key";
	size_t at;

	memset(payload, 0, sizeof(payload));
	at = put_init_hdr(payload, 4, "keyboard");
	at = put_block(payload, at, INIT_PRODUCT_NAME, text, sizeof(text));
	prepare_comm(0);
	if (send_comm_payload(payload, at) != 0)
		return 2;
	if (product_accepted != 1 || strstr(log_sink, "keyboard: Key") == NULL)
		return fail("product text was not logged");
	if (product_rejected != 0)
		return fail("product text was treated as unterminated");
	if (strcmp(last_get_name, "keyboard") != 0 || !g_iface.deferred || create_calls != 0)
		return fail("product text did not defer the looked-up interface");
	if (rearmed("product text leaked the worker"))
		return 1;
	return pass("init_product_text");
}

static int case_init_product_unterminated(void)
{
	u8 payload[64];
	const char text[] = {'A', 'B', 'C', 'D'};
	size_t at;

	memset(payload, 0, sizeof(payload));
	at = put_init_hdr(payload, 4, "keyboard");
	at = put_block(payload, at, INIT_PRODUCT_NAME, text, sizeof(text));
	prepare_comm(1);
	if (send_comm_payload(payload, at) != 0)
		return 2;
	if (product_accepted != 0)
		return fail("unterminated product was logged as accepted");
	if (product_rejected != 1)
		return fail("unterminated product was not rejected");
	if (create_calls != 1)
		return fail("unterminated product skipped creation of a known interface");
	if (rearmed("unterminated product leaked the worker"))
		return 1;
	return pass("init_product_unterminated");
}

static int case_init_gpio_short(void)
{
	u8 payload[96];
	u8 owned[4];
	size_t at;
	u16 id = 0x00B7;

	memset(payload, 0, sizeof(payload));
	memset(owned, 0, sizeof(owned));
	memcpy(owned + 2, &id, sizeof(id));
	at = put_init_hdr(payload, 4, "keyboard");
	at = put_block(payload, at, INIT_GPIO_REQUEST, owned, sizeof(owned));
	memcpy(payload + at, "LEAKED", 6);
	at += 40;
	prepare_comm(0);
	if (send_comm_payload(payload, at) != 0)
		return 2;
	if (g_iface.gpio_id != 0 || g_iface.gpio_name[0] != '\0')
		return fail("short gpio block consumed the following bytes");
	if (create_calls != 0 || desc_calls != 0)
		return fail("short gpio block created or described an interface");
	if (rearmed("short gpio block leaked the worker"))
		return 1;
	return pass("init_gpio_short");
}

static int case_init_gpio_valid(void)
{
	u8 payload[96];
	struct dchid_gpio_request req;
	size_t at;

	memset(payload, 0, sizeof(payload));
	memset(&req, 0, sizeof(req));
	req.id = 7;
	memcpy(req.name, "reset", 6);
	at = put_init_hdr(payload, 4, "keyboard");
	at = put_block(payload, at, INIT_GPIO_REQUEST, &req, sizeof(req));
	payload[at++] = 0xff;
	prepare_comm(0);
	if (send_comm_payload(payload, at) != 0)
		return 2;
	if (g_iface.gpio_id != 7 || strcmp(g_iface.gpio_name, "reset") != 0)
		return fail("gpio request was not stored");
	if (get_calls != 1 || strcmp(last_get_name, "keyboard") != 0)
		return fail("gpio request skipped interface lookup");
	if (!g_iface.deferred || create_calls != 0)
		return fail("gpio request did not defer interface creation");
	if (rearmed("gpio request leaked the worker"))
		return 1;
	return pass("init_gpio_valid");
}

static int case_init_name_unterminated(void)
{
	u8 payload[24];

	memset(payload, 0x41, sizeof(payload));
	payload[0] = EVENT_INIT;
	payload[3] = 4;
	prepare_comm(0);
	if (send_comm_payload(payload, sizeof(payload)) != 0)
		return 2;
	if (get_calls != 0)
		return fail("unterminated interface name reached lookup");
	if (create_calls != 0 || desc_calls != 0 || product_accepted != 0)
		return fail("unterminated interface name was accepted");
	if (rearmed("unterminated interface name leaked the worker"))
		return 1;
	return pass("init_name_unterminated");
}

static int case_init_name(const char *name, int stm, const char *case_name)
{
	u8 payload[64];
	size_t at;

	memset(payload, 0, sizeof(payload));
	at = put_init_hdr(payload, 4, name);
	at = put_block(payload, at, INIT_TERMINATOR, NULL, 0);
	prepare_comm(0);
	if (send_comm_payload(payload, at) != 0)
		return 2;
	if (get_calls != 1 || strcmp(last_get_name, name) != 0 || last_get_index != 4)
		return fail("interface name was not looked up");
	if (stm) {
		if (create_calls != 1 || g_iface.deferred)
			return fail("stm interface was not created");
	} else if (create_calls != 0 || !g_iface.deferred) {
		return fail("named interface was not deferred");
	}
	if (rearmed("interface name leaked the worker"))
		return 1;
	return pass(case_name);
}

static int case_init_header_truncated(void)
{
	u8 payload[8];

	memset(payload, 0x5a, sizeof(payload));
	payload[0] = EVENT_INIT;
	prepare_comm(1);
	if (send_comm_payload(payload, sizeof(payload)) != 0)
		return 2;
	if (get_calls != 0 || create_calls != 0 || desc_calls != 0)
		return fail("truncated init header was parsed");
	if (rearmed("truncated init header leaked the worker"))
		return 1;
	return pass("init_header_truncated");
}

static int case_init_block_oversized(void)
{
	u8 payload[64];
	u8 bytes[8] = {1, 2, 3, 4, 5, 6, 7, 8};
	size_t at;
	struct dchid_init_block_hdr blk;

	memset(payload, 0, sizeof(payload));
	at = put_init_hdr(payload, 4, "keyboard");
	blk.type = INIT_HID_DESCRIPTOR;
	blk.length = 500;
	memcpy(payload + at, &blk, sizeof(blk));
	memcpy(payload + at + sizeof(blk), bytes, sizeof(bytes));
	at += sizeof(blk) + sizeof(bytes);
	prepare_comm(1);
	if (send_comm_payload(payload, at) != 0)
		return 2;
	if (desc_calls != 0 || desc_len_seen != 0)
		return fail("oversized block was stored as a descriptor");
	if (get_calls != 1)
		return fail("oversized block skipped a valid interface name");
	if (rearmed("oversized block leaked the worker"))
		return 1;
	return pass("init_block_oversized");
}

static int case_init_descriptor_terminator(void)
{
	u8 payload[96];
	struct dchid_gpio_request req;
	const u8 desc[4] = {0xde, 0xad, 0xbe, 0xef};
	size_t at;

	memset(payload, 0, sizeof(payload));
	memset(&req, 0, sizeof(req));
	req.id = 99;
	memcpy(req.name, "nope", 5);
	at = put_init_hdr(payload, 4, "keyboard");
	at = put_block(payload, at, INIT_HID_DESCRIPTOR, desc, sizeof(desc));
	at = put_block(payload, at, INIT_TERMINATOR, NULL, 0);
	at = put_block(payload, at, INIT_GPIO_REQUEST, &req, sizeof(req));
	prepare_comm(1);
	if (send_comm_payload(payload, at) != 0)
		return 2;
	if (desc_calls != 1 || desc_len_seen != sizeof(desc) || memcmp(desc_copy, desc, sizeof(desc)) != 0)
		return fail("descriptor block was not stored");
	if (g_iface.gpio_id != 0 || g_iface.gpio_name[0] != '\0')
		return fail("terminator did not stop the following gpio block");
	if (create_calls != 1)
		return fail("terminated init did not create the interface");
	if (rearmed("descriptor init leaked the worker"))
		return 1;
	return pass("init_descriptor_terminator");
}

static int case_init_unknown_block(void)
{
	u8 payload[64];
	const u8 body[4] = {9, 9, 9, 9};
	size_t at;

	memset(payload, 0, sizeof(payload));
	at = put_init_hdr(payload, 4, "keyboard");
	at = put_block(payload, at, 0x1111, body, sizeof(body));
	prepare_comm(0);
	if (send_comm_payload(payload, at) != 0)
		return 2;
	if (warn_count == 0)
		return fail("unknown init block was silent");
	if (desc_calls != 0 || g_iface.gpio_id != 0 || product_accepted != 0)
		return fail("unknown init block changed device state");
	if (!g_iface.deferred || create_calls != 0)
		return fail("unknown init block did not defer the interface");
	if (rearmed("unknown init block leaked the worker"))
		return 1;
	return pass("init_unknown_block");
}

static int case_all_invalid_ifaces(void)
{
	unsigned i;
	int rc;

	for (i = MAX_INTERFACES; i <= 255; i++) {
		rc = case_iface(i, "all_invalid_ifaces");
		if (rc != 0)
			return rc;
	}
	return pass("all_invalid_ifaces");
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
	if (!strcmp(argv[1], "all_invalid_ifaces"))
		return case_all_invalid_ifaces();
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
	if (!strcmp(argv[1], "init_product_zero"))
		return case_init_product_zero(0);
	if (!strcmp(argv[1], "init_product_zero_walk"))
		return case_init_product_zero(1);
	if (!strcmp(argv[1], "init_product_empty"))
		return case_init_product_empty();
	if (!strcmp(argv[1], "init_product_text"))
		return case_init_product_text();
	if (!strcmp(argv[1], "init_product_unterminated"))
		return case_init_product_unterminated();
	if (!strcmp(argv[1], "init_gpio_short"))
		return case_init_gpio_short();
	if (!strcmp(argv[1], "init_gpio_valid"))
		return case_init_gpio_valid();
	if (!strcmp(argv[1], "init_name_unterminated"))
		return case_init_name_unterminated();
	if (!strcmp(argv[1], "init_name_valid"))
		return case_init_name("keyboard", 0, "init_name_valid");
	if (!strcmp(argv[1], "init_name_15"))
		return case_init_name("abcdefghijklmno", 0, "init_name_15");
	if (!strcmp(argv[1], "init_stm_creates"))
		return case_init_name("stm", 1, "init_stm_creates");
	if (!strcmp(argv[1], "init_header_truncated"))
		return case_init_header_truncated();
	if (!strcmp(argv[1], "init_block_oversized"))
		return case_init_block_oversized();
	if (!strcmp(argv[1], "init_descriptor_terminator"))
		return case_init_descriptor_terminator();
	if (!strcmp(argv[1], "init_unknown_block"))
		return case_init_unknown_block();
	fprintf(stderr, "unknown case %s\n", argv[1]);
	return 2;
}
