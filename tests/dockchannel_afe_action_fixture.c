/* Host shims for extracted DockChannel AFE functions; no hardware access. */
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;
typedef uint32_t __le32;
typedef uint64_t dma_addr_t;
#define UNUSED __attribute__((unused))
#define __packed __attribute__((packed))
#define cpu_to_le32(v) (v)
#define CMD_RESET_INTERFACE 0x40
#define CMD_ACK_GPIO_CMD 0xa1
#define MAX_INTERFACES 16
#define MAX_GPIO_NAME 32
#define MAX_PKT_SIZE (0xffff + 4)
#define GFP_KERNEL 0
#define GPIOD_ASIS 0
#define GPIOD_OUT_LOW 3
#define GPIO_LINE_DIRECTION_OUT 0
#define GPIO_LINE_DIRECTION_IN 1
#define EPROBE_DEFER 517
#ifndef TEST_SMC_REACHABLE
#define TEST_SMC_REACHABLE 1
#endif
#define IS_REACHABLE(config) TEST_SMC_REACHABLE
#define DL_FLAG_AUTOREMOVE_CONSUMER 1
#define APPLE_SMC_WRITABLE (1U << 6)
#define SMC_KEY(s) (((u32)(#s)[0] << 24) | ((u32)(#s)[1] << 16) | ((u32)(#s)[2] << 8) | (u32)(#s)[3])
#define IS_ERR(p) ((uintptr_t)(p) >= (uintptr_t)-4095)
#define IS_ERR_OR_NULL(p) (!(p) || IS_ERR(p))
#define PTR_ERR(p) ((int)(intptr_t)(p))
#define ERR_PTR(n) ((void *)(intptr_t)(n))
#define dev_info(...) ((void)0)
#define dev_warn(...) ((void)0)
#define dev_err(...) ((void)0)
struct device_node { const char *compatible; size_t compatible_size; };
struct device { struct device_node *of_node; };
struct platform_device { struct device dev; };
struct device_link { int unused; };
struct apple_smc { int unused; };
struct apple_smc_key_info { u32 type_code; u8 size, flags; };
struct work_struct { int unused; };
struct completion { int unused; };
struct gpio_desc { int raw; bool active_low; };
struct mutex { pthread_mutex_t native; bool initialized; };
struct of_phandle_args { struct device_node *np; int args_count; u32 args[4]; };
/* PARAMETERS */
/* ACTUAL_TYPES */

static bool board, chip, parent_present, claimed;
static bool device_present, driver_present, link_present;
static const char live_smc_compatible[] = "apple,t8132-smc\0apple,t8103-smc";
static struct device_node consumer_node, provider_node, smc_node;
static struct platform_device smc_device = {.dev = {.of_node = &smc_node}};
static struct apple_smc smc;
static struct device_link smc_link;
static struct apple_smc_key_info key_info;
static struct gpio_desc gpio, other_gpio;
static struct dchid_iface *active_iface;
static _Thread_local int held_locks;
static atomic_int lock_attempts;
static int acquisitions, parses, puts_count, firmware_lookups, uploads;
static int provider_refs, parent_refs, device_refs, device_puts, links, unlinks, actions, key_queries;
static int gpio_writes, smc_writes, commands, fail_write, fail_command;
static int acquire_error, parse_error, write_error, write_success, command_error;
static int metadata_error, action_error;
static void (*managed_cleanup)(void *);
static void *managed_data;
static int firmware_error, upload_error, dt_cells, last_flags;
static u32 dt_pin, dt_flags;
static char last_consumer[64];
static pthread_mutex_t block_mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t block_condition = PTHREAD_COND_INITIALIZER;
static bool block_acquire, acquire_entered, release_acquire;
struct event { char kind; int value, raw; u32 status; };
static struct event events[64];
static unsigned event_count;
static const void *gpio_request;
static size_t gpio_request_size;

static void record(char kind, int value, int raw, u32 status)
{
    assert(event_count < sizeof(events) / sizeof(events[0]));
    events[event_count++] = (struct event){kind, value, raw, status};
}
static void clear_trace(void)
{
    event_count = gpio_writes = smc_writes = commands = 0;
}
static void expect_events(const char *expected)
{
    char actual[65] = {0};
    for (unsigned i = 0; i < event_count; ++i) actual[i] = events[i].kind;
    if (strcmp(actual, expected))
        fprintf(stderr, "events %s; expected %s\n", actual, expected);
    assert(!strcmp(actual, expected));
}
static bool of_machine_is_compatible(const char *name)
{
    if (!strcmp(name, "apple,j713")) return board;
    if (!strcmp(name, "apple,t8132")) return chip;
    assert(false); return false;
}
static bool afe_selected(void)
{
    return m4_afe_action && m4_power_v2 && board && chip &&
        !strcmp(active_iface->name, "multi-touch");
}
static void mutex_init(struct mutex *mutex)
{
    assert(!pthread_mutex_init(&mutex->native, NULL)); mutex->initialized = true;
}
static UNUSED void mutex_lock(struct mutex *mutex)
{
    assert(mutex->initialized);
    atomic_fetch_add(&lock_attempts, 1);
    assert(!pthread_mutex_lock(&mutex->native)); ++held_locks;
}
static UNUSED void mutex_unlock(struct mutex *mutex)
{
    assert(held_locks == 1); --held_locks;
    assert(!pthread_mutex_unlock(&mutex->native));
}
static UNUSED void lockdep_assert_held(struct mutex *mutex)
{
    assert(mutex->initialized && held_locks == 1);
}
static UNUSED int of_parse_phandle_with_args(struct device_node *node, const char *property,
                                              const char *cells, int index,
                                              struct of_phandle_args *args)
{
    assert(held_locks == 1 && node == &consumer_node && index == 0);
    assert(!strcmp(property, "apple,afe-reset-gpios") && !strcmp(cells, "#gpio-cells"));
    ++parses; record('P', 0, 0, 0);
    if (parse_error) return parse_error;
    ++provider_refs;
    *args = (struct of_phandle_args){.np = &provider_node, .args_count = dt_cells,
                                   .args = {dt_pin, dt_flags}};
    return 0;
}
static UNUSED bool of_device_is_compatible(struct device_node *node, const char *name)
{
    assert(node == &provider_node || node == &smc_node);
    for (size_t offset = 0; offset < node->compatible_size;) {
        const char *value = node->compatible + offset;
        size_t length = strnlen(value, node->compatible_size - offset);
        assert(length < node->compatible_size - offset);
        if (!strcmp(value, name)) return true;
        offset += length + 1;
    }
    return false;
}
static UNUSED void of_node_put(struct device_node *node)
{
    if (!node) return;
    if (node == &provider_node) { assert(provider_refs > 0); --provider_refs; }
    else { assert(node == &smc_node && parent_refs > 0); --parent_refs; }
    ++puts_count;
}
static UNUSED struct device_node *of_get_parent(struct device_node *node)
{
    assert(node == &provider_node && provider_refs == 1 && held_locks == 1);
    if (!parent_present) return NULL;
    ++parent_refs; return &smc_node;
}
static UNUSED struct platform_device *of_find_device_by_node(struct device_node *node)
{
    assert(node == &smc_node && parent_refs == 1 && held_locks == 1);
    if (!device_present) return NULL;
    ++device_refs; return &smc_device;
}
static UNUSED struct apple_smc *platform_get_drvdata(struct platform_device *pdev)
{
    assert(pdev == &smc_device && device_refs > 0 && held_locks == 1);
    return driver_present ? &smc : NULL;
}
static UNUSED void put_device(void *data)
{
    assert(data == &smc_device.dev && device_refs > 0); --device_refs; ++device_puts;
}
static UNUSED struct device_link *device_link_add(struct device *consumer, struct device *supplier,
                                                  unsigned flags)
{
    assert(consumer == active_iface->dchid->dev && supplier == &smc_device.dev);
    assert(flags == DL_FLAG_AUTOREMOVE_CONSUMER && held_locks == 1 && device_refs == 1);
    ++links; return link_present ? &smc_link : NULL;
}
static UNUSED void device_link_del(struct device_link *link)
{
    /* Retained to catch manual deletion of a managed link in the regression. */
    assert(link == &smc_link && held_locks == 1); ++unlinks;
}
static UNUSED int devm_add_action_or_reset(struct device *dev, void (*action)(void *), void *data)
{
    assert(dev == active_iface->dchid->dev && data == &smc_device.dev && held_locks == 1);
    assert(!managed_cleanup && device_refs == 1); ++actions;
    if (action_error) { action(data); return action_error; }
    managed_cleanup = action; managed_data = data; return 0;
}
static UNUSED int apple_smc_get_key_info(struct apple_smc *actual, u32 key,
                                        struct apple_smc_key_info *info)
{
    assert(actual == &smc && key == 0x67503163 && held_locks == 1);
    assert(managed_cleanup && device_refs == 1); ++key_queries; record('K', 0, 0, 0);
    /* Plausible fields on failure make ignoring the return code observable. */
    *info = key_info;
    return metadata_error;
}
static int gpiod_set_value_cansleep(struct gpio_desc *desc, int value)
{
    assert(desc == &gpio && (value == 0 || value == 1));
    if (afe_selected()) assert(held_locks == 1);
    ++gpio_writes; record('G', value, desc->active_low ? !value : value, 0);
    if (gpio_writes == fail_write) return write_error;
    desc->raw = desc->active_low ? !value : value; return 0;
}
static struct gpio_desc *devm_gpiod_get_index(struct device *dev, const char *name,
                                            int index, int flags)
{
    assert(dev == active_iface->dchid->dev && index == 0);
    if (afe_selected()) assert(held_locks == 1);
    ++acquisitions; last_flags = flags;
    snprintf(last_consumer, sizeof(last_consumer), "%s", name); record('C', 0, 0, 0);
    if (acquire_error) return ERR_PTR(acquire_error);
    if (block_acquire) {
        assert(!pthread_mutex_lock(&block_mutex)); acquire_entered = true;
        assert(!pthread_cond_broadcast(&block_condition));
        while (!release_acquire) assert(!pthread_cond_wait(&block_condition, &block_mutex));
        assert(!pthread_mutex_unlock(&block_mutex));
    }
    if (claimed) return ERR_PTR(-EBUSY);
    claimed = true;
    if (flags == GPIOD_OUT_LOW) (void)gpiod_set_value_cansleep(&gpio, 0);
    return &gpio;
}
static void msleep(int delay)
{
    if (afe_selected()) assert(held_locks == 1);
    record('D', delay, 0, 0);
}
static void *kzalloc(size_t size, int flags) { assert(flags == GFP_KERNEL); return calloc(1, size); }
static void kfree(void *memory) { free(memory); }
static u32 read_le32(const u8 *bytes)
{
    return bytes[0] | (u32)bytes[1] << 8 | (u32)bytes[2] << 16 | (u32)bytes[3] << 24;
}
static UNUSED u16 get_unaligned_le16(const void *data)
{
    const u8 *bytes = data; return bytes[0] | (u16)bytes[1] << 8;
}
static UNUSED u32 get_unaligned_le32(const void *data) { return read_le32(data); }
static UNUSED int apple_smc_write(struct apple_smc *actual, u32 key, const void *data, size_t size)
{
    assert(actual == &smc && key == 0x67503163 && size == 4 && held_locks == 1);
    assert(managed_cleanup && device_refs == 1 && key_queries >= 1);
    u32 payload = read_le32(data);
    ++smc_writes; record('S', payload, 0, 0);
    return smc_writes == fail_write ? write_error : write_success;
}
static int dchid_comm_cmd(struct dockchannel_hid *dchid, void *data, size_t size)
{
    const u8 *bytes = data;
    assert(dchid == active_iface->dchid && held_locks == 0);
    ++commands;
    if (bytes[0] == CMD_RESET_INTERFACE) {
        assert((size == 4 || size == 9) && bytes[2] == active_iface->index);
        assert(bytes[3] == 0 || bytes[3] == 2);
        if (size == 4) { assert(bytes[1] == 1); record('L', bytes[3], 0, 0); }
        else {
            assert(bytes[1] == 2 && bytes[4] <= 1);
            record(bytes[4] ? 'H' : 'W', bytes[3], 0, read_le32(bytes + 5));
        }
    } else {
        assert(bytes[0] == CMD_ACK_GPIO_CMD && gpio_request && size == 5 + gpio_request_size);
        assert(!memcmp(bytes + 5, gpio_request, gpio_request_size));
        record('A', 0, 0, read_le32(bytes + 1));
    }
    return commands == fail_command ? command_error : 9;
}
static int dchid_get_firmware(struct dchid_iface *iface, void **firmware, size_t *size)
{
    static u8 data;
    ++firmware_lookups; record('F', 0, 0, 0);
    *size = !strcmp(iface->name, "multi-touch") ? 1 : 0;
    *firmware = *size ? &data : NULL; return firmware_error;
}
static int dchid_send_firmware(struct dchid_iface *iface, void *firmware, size_t size)
{
    assert(iface == active_iface && firmware && size == 1);
    ++uploads; record('U', 0, 0, 0); return upload_error;
}
/* ACTUAL_FUNCTIONS */

static void deliver_gpio(struct dchid_iface *iface, void *data, size_t size)
{
    gpio_request = data; gpio_request_size = size;
    dchid_handle_gpio(iface->dchid, data, size);
    gpio_request = NULL;
}

static struct dchid_iface *fresh(const char *name, bool enabled)
{
    if (managed_cleanup) { managed_cleanup(managed_data); managed_cleanup = NULL; }
    assert(!provider_refs && !parent_refs && !device_refs);
    static struct device dev = {.of_node = &consumer_node};
    struct dockchannel_hid *dchid = calloc(1, sizeof(*dchid));
    struct dchid_iface *iface = calloc(1, sizeof(*iface));
    assert(dchid && iface); dchid->dev = &dev;
#if HAVE_AFE_ACTION
    mutex_init(&dchid->afe_mutex);
#endif
    mutex_init(&iface->start_mutex);
    iface->dchid = dchid; iface->index = 1; iface->name = name;
    dchid->ifaces[1] = iface; active_iface = iface;
    m4_afe_action = enabled; m4_power_v2 = board = chip = true;
    parent_present = device_present = driver_present = link_present = true;
    provider_node = (struct device_node){"apple,smc-gpio", sizeof("apple,smc-gpio")};
    smc_node = (struct device_node){live_smc_compatible, sizeof(live_smc_compatible)};
    claimed = false; gpio = (struct gpio_desc){.active_low = true};
    acquisitions = parses = puts_count = firmware_lookups = uploads = 0;
    device_puts = links = unlinks = actions = key_queries = 0;
    acquire_error = parse_error = firmware_error = upload_error = 0;
    metadata_error = action_error = write_success = 0;
    key_info = (struct apple_smc_key_info){.size = 4, .flags = APPLE_SMC_WRITABLE};
    write_error = -EACCES; command_error = -ETIMEDOUT;
    fail_write = fail_command = 0; dt_cells = 2; dt_pin = 28; dt_flags = 1;
    block_acquire = acquire_entered = release_acquire = false;
    atomic_store(&lock_attempts, 0); assert(held_locks == 0); clear_trace(); return iface;
}
static int prepare(struct dchid_iface *iface)
{
#if HAVE_AFE_ACTION
    return dchid_prepare_afe(iface);
#else
    (void)iface; return 0;
#endif
}
static void sequence(void)
{
    struct dchid_iface *iface = fresh("multi-touch", true);
    assert(!prepare(iface)); clear_trace();
    assert(dchid_reset_interface(iface, 0) == 9);
    assert(dchid_reset_interface(iface, 2) == 9);
    expect_events("WSDHWSH");
    assert(events[1].value == 0x00010001 && events[2].value == 50);
    assert(events[5].value == 0x00010000 && !gpio_writes);
    for (unsigned i = 0; i < event_count; ++i) assert(events[i].status == 0);
#if HAVE_AFE_ACTION
    clear_trace(); write_success = 4;
    assert(dchid_reset_interface(iface, 0) == 9 && dchid_reset_interface(iface, 2) == 9);
    expect_events("WSDHWSH");
    clear_trace(); assert(dchid_afe_action(iface, 1) == -EINVAL); expect_events("");
    mutex_lock(&iface->dchid->afe_mutex);
    assert(dchid_afe_write(iface->dchid, 2) == -EINVAL);
    iface->dchid->afe_smc = NULL;
    assert(dchid_afe_write(iface->dchid, 1) == -EINVAL);
    iface->dchid->afe_smc = &smc;
    mutex_unlock(&iface->dchid->afe_mutex); expect_events("");
#endif
}
static void startup(void)
{
    struct dchid_iface *iface = fresh("multi-touch", true);
    assert(!dchid_start_interface(iface)); expect_events("PCKFUWSDHWSH");
    assert(acquisitions == 1 && puts_count == 2 && smc_writes == 2 && !gpio_writes);
    assert(!provider_refs && !parent_refs && device_refs == 1 && actions == 1 && links == 1);
    assert(last_flags == GPIOD_ASIS && !strcmp(last_consumer, "apple,afe-reset"));
    assert(iface->starting);
    clear_trace(); assert(dchid_start_interface(iface) == -EINPROGRESS); expect_events("");
}
static void live_parent(void)
{
    struct dchid_iface *iface = fresh("multi-touch", true);
    assert(of_device_is_compatible(&smc_node, "apple,t8132-smc"));
    assert(of_device_is_compatible(&smc_node, "apple,t8103-smc"));
    assert(!of_device_is_compatible(&smc_node, "apple,smc"));
    int ret = prepare(iface);
    fprintf(stderr, "live SMC parent: prepare=%d key_queries=%d acquisitions=%d\n",
            ret, key_queries, acquisitions);
    assert(!ret && key_queries == 1 && acquisitions == 1);
    assert(!provider_refs && !parent_refs && device_refs == 1 && !smc_writes && !gpio_writes);
}
static void guards(void)
{
    for (int variant = 0; variant < 5; ++variant) {
        struct dchid_iface *iface = fresh("multi-touch", true);
        if (variant == 0) m4_afe_action = false;
        if (variant == 1) m4_power_v2 = false;
        if (variant == 2) board = false;
        if (variant == 3) chip = false;
        if (variant == 4) iface->name = "keyboard";
        parse_error = -ENOENT;
        assert(!prepare(iface) && !parses && !acquisitions && !key_queries);
        assert(dchid_reset_interface(iface, 0) == 9 && dchid_reset_interface(iface, 2) == 9);
        expect_events(variant >= 1 && variant <= 3 ? "LL" : "WHWH");
        assert(!gpio_writes && !smc_writes && !device_refs);
    }
}
static void preflight(void)
{
    for (int variant = 0; variant < 10; ++variant) {
        struct dchid_iface *iface = fresh("multi-touch", true);
        if (variant == 0) parse_error = -ENOENT;
        if (variant == 1) dt_cells = 1;
        if (variant == 2) dt_pin = 29;
        if (variant == 3) dt_flags = 0;
        if (variant == 4) provider_node = (struct device_node){"apple,other-gpio", sizeof("apple,other-gpio")};
        if (variant == 5) dt_flags = 3;
        if (variant == 6) parent_present = false;
        if (variant == 7) smc_node = (struct device_node){"apple,other-smc", sizeof("apple,other-smc")};
        if (variant == 8) smc_node = (struct device_node){"apple,smc", sizeof("apple,smc")};
        if (variant == 9) smc_node = (struct device_node){"apple,t8103-smc", sizeof("apple,t8103-smc")};
        assert(dchid_start_interface(iface) == (variant == 0 ? -ENOENT : -EINVAL));
        assert(!iface->starting && !acquisitions && !firmware_lookups && !uploads);
        assert(!commands && !gpio_writes && !smc_writes && !key_queries);
        assert(!provider_refs && !parent_refs && !device_refs);
    }
    for (int variant = 0; variant < 2; ++variant) {
        struct dchid_iface *iface = fresh("multi-touch", true);
        if (variant == 0) device_present = false;
        else driver_present = false;
        assert(dchid_start_interface(iface) == -EPROBE_DEFER && !iface->starting);
        assert(!device_refs && !provider_refs && !parent_refs && !acquisitions);
        assert(!firmware_lookups && !commands && !smc_writes);
        device_present = driver_present = true;
        assert(!dchid_start_interface(iface));
    }
    const int errors[] = {-EPROBE_DEFER, -EBUSY, -ENOMEM};
    for (unsigned i = 0; i < sizeof(errors) / sizeof(errors[0]); ++i) {
        struct dchid_iface *iface = fresh("multi-touch", true); acquire_error = errors[i];
        assert(dchid_start_interface(iface) == errors[i] && !iface->starting);
        assert(!firmware_lookups && !commands && !gpio_writes && !smc_writes && !device_refs);
        acquire_error = 0; assert(!dchid_start_interface(iface)); assert(acquisitions == 2);
    }
    for (int variant = 0; variant < 2; ++variant) {
        struct dchid_iface *iface = fresh("multi-touch", true);
        if (variant == 0) link_present = false;
        else action_error = -ENOMEM;
        assert(dchid_start_interface(iface) == -ENOMEM && !iface->starting);
        assert(acquisitions == 1 && !device_refs && device_puts == 1);
        assert(!unlinks && !managed_cleanup && !key_queries);
        assert(!firmware_lookups && !commands && !gpio_writes && !smc_writes);
        link_present = true; action_error = 0;
        assert(!dchid_start_interface(iface) && acquisitions == 1);
    }
    struct dchid_iface *iface = fresh("keyboard", true); acquire_error = -EIO;
    assert(!dchid_start_interface(iface) && !acquisitions && !gpio_writes && !smc_writes);
}
static void metadata(void)
{
    for (int variant = 0; variant < 4; ++variant) {
        struct dchid_iface *iface = fresh("multi-touch", true);
        if (variant == 0) metadata_error = -ETIMEDOUT;
        if (variant == 1) key_info.size = 1;
        if (variant == 2) key_info.size = 8;
        if (variant == 3) key_info.flags = 0x80;
        assert(dchid_start_interface(iface) == (variant == 0 ? -ETIMEDOUT : -EINVAL));
        assert(!iface->starting && key_queries == 1 && !firmware_lookups && !uploads);
        assert(!commands && !gpio_writes && !smc_writes);
        assert(device_refs == 1 && actions == 1 && links == 1 && acquisitions == 1);
        clear_trace();
        assert(dchid_reset_interface(iface, 0) == -EINVAL); expect_events("WH");
        assert(events[1].status == 0xe00002bc);
        key_info.size = 4; key_info.flags = APPLE_SMC_WRITABLE; metadata_error = 0;
        clear_trace(); assert(!dchid_start_interface(iface)); expect_events("KFUWSDHWSH");
        assert(key_queries == 2 && acquisitions == 1 && links == 1 && actions == 1);
    }
}
static void failures(void)
{
    for (int stage = 1; stage <= 4; ++stage) {
        struct dchid_iface *iface = fresh("multi-touch", true);
        assert(!prepare(iface)); clear_trace(); fail_command = stage;
        assert(dchid_start_interface(iface) == -ETIMEDOUT && !iface->starting);
        assert(commands == stage && smc_writes == (stage == 1 ? 0 : stage == 4 ? 2 : 1));
        assert(!gpio_writes);
    }
    for (int stage = 1; stage <= 2; ++stage) {
        struct dchid_iface *iface = fresh("multi-touch", true);
        assert(!prepare(iface)); clear_trace(); fail_write = stage;
        assert(dchid_start_interface(iface) == -EACCES && !iface->starting);
        expect_events(stage == 1 ? "FUWSH" : "FUWSDHWSH");
        assert(events[event_count - 1].status == 0xe00002bc);
        assert(commands == stage * 2 && smc_writes == stage && !gpio_writes);
    }
    struct dchid_iface *iface = fresh("multi-touch", true);
    assert(!prepare(iface)); clear_trace(); fail_write = 1; fail_command = 2;
    assert(dchid_reset_interface(iface, 0) == -EACCES); expect_events("WSH");
    assert(events[2].status == 0xe00002bc);
    iface = fresh("multi-touch", true);
    assert(dchid_reset_interface(iface, 0) == -EINVAL); expect_events("WH");
    assert(events[1].status == 0xe00002bc);
    iface = fresh("multi-touch", true); firmware_error = -ENOENT;
    assert(dchid_start_interface(iface) == -ENOENT && !iface->starting && !uploads && !commands);
    iface = fresh("multi-touch", true); upload_error = -EIO;
    assert(dchid_start_interface(iface) == -EIO && !iface->starting && !commands);
}
static void ownership(void)
{
    struct dchid_iface *iface = fresh("multi-touch", true);
    assert(!prepare(iface) && !prepare(iface));
    assert(acquisitions == 1 && key_queries == 1 && !gpio_writes && !smc_writes);
    assert(!provider_refs && !parent_refs && device_refs == 1 && actions == 1 && links == 1);
    strcpy(iface->gpio_name, "afe-reset"); iface->gpio_id = 7;
    assert(!dchid_request_gpio(iface) && iface->gpio == &gpio && acquisitions == 1);
    assert(!dchid_request_gpio(iface) && acquisitions == 1 && key_queries == 1);
    strcpy(iface->gpio_name, "stm-reset"); clear_trace();
    assert(dchid_request_gpio(iface) == -EINVAL); expect_events("");
    iface->gpio = &other_gpio; assert(dchid_request_gpio(iface) == -EINVAL);
    managed_cleanup(managed_data); managed_cleanup = NULL;
    assert(!device_refs && device_puts == 1);
    iface = fresh("keyboard", true); strcpy(iface->gpio_name, "stm-reset");
    assert(!dchid_request_gpio(iface) && !strcmp(last_consumer, "apple,stm-reset"));
    assert(last_flags == GPIOD_OUT_LOW && !dchid_request_gpio(iface) && acquisitions == 1);
}
static void callback(void)
{
    for (int failure = 0; failure <= 2; ++failure) {
        struct dchid_iface *iface = fresh("multi-touch", true);
        assert(!prepare(iface)); strcpy(iface->gpio_name, "afe-reset"); iface->gpio_id = 7;
        u8 cmd[20] = {0xa0, 1, 7, 0, 3};
        clear_trace(); fail_write = failure;
        deliver_gpio(iface, cmd, sizeof(cmd));
        expect_events(failure == 1 ? "SA" : "SDSA");
        assert(acquisitions == 1 && events[0].value == 0x00010001 && !gpio_writes);
        if (failure != 1) assert(events[1].value == 50 && events[2].value == 0x00010000);
        assert(events[event_count - 1].status == (failure ? 0xe00002bc : 0));
    }
    for (int variant = 0; variant < 2; ++variant) {
        struct dchid_iface *iface = fresh(variant ? "keyboard" : "multi-touch", variant);
        strcpy(iface->gpio_name, variant ? "stm-reset" : "afe-reset"); iface->gpio_id = 7;
        assert(!dchid_request_gpio(iface)); clear_trace(); fail_write = 1;
        struct dchid_gpio_cmd cmd = {.type = 0xa0, .iface = 1, .gpio = 7, .cmd = 3};
        deliver_gpio(iface, &cmd, sizeof(cmd));
        expect_events("GDGA");
        assert(events[0].value == 1 && events[1].value == 10 && events[2].value == 0);
        assert(events[3].status == 0 && acquisitions == 1 && !smc_writes);
    }
}
static void framing(void)
{
    for (int variant = 0; variant < 6; ++variant) {
        struct dchid_iface *iface = fresh("multi-touch", true);
        strcpy(iface->gpio_name, "afe-reset"); iface->gpio_id = 7;
        u8 cmd[20] = {0xa0, 1, 7, 0, 3}; size_t size = sizeof(cmd);
        if (variant == 0) size = 5;
        if (variant == 1) size = 19;
        if (variant == 2) cmd[3] = 1;
        if (variant == 3) cmd[5] = 1;
        if (variant == 4) cmd[4] = 2;
        if (variant == 5) cmd[7] = 1;
        parse_error = -EIO;
        deliver_gpio(iface, cmd, size); expect_events("A");
        assert(events[0].status != 0 && !parses && !acquisitions && !key_queries);
        assert(!smc_writes && !gpio_writes && !device_refs);
    }
    struct dchid_iface *iface = fresh("multi-touch", true);
    strcpy(iface->gpio_name, "afe-reset"); iface->gpio_id = 0x107;
    u8 cmd[20] = {0xa0, 1, 7, 1, 3};
    deliver_gpio(iface, cmd, sizeof(cmd)); expect_events("PCKSDSA");
    assert(events[3].value == 0x10001 && events[4].value == 50 && events[5].value == 0x10000);
    assert(events[6].status == 0 && !gpio_writes);
    clear_trace(); deliver_gpio(iface, cmd, 4); expect_events("");
}
static void config(void)
{
    struct dchid_iface *iface = fresh("multi-touch", true);
    assert(!TEST_SMC_REACHABLE);
    assert(dchid_start_interface(iface) == -EOPNOTSUPP && !iface->starting);
    expect_events("");
    assert(!parses && !acquisitions && !device_refs && !key_queries);
#if HAVE_AFE_ACTION
    mutex_lock(&iface->dchid->afe_mutex);
    assert(dchid_afe_write(iface->dchid, 1) == -EOPNOTSUPP);
    mutex_unlock(&iface->dchid->afe_mutex);
#endif
    m4_afe_action = false;
    assert(!dchid_start_interface(iface)); expect_events("FUWHWH");
}
static void *prepare_thread(void *value)
{
    return (void *)(intptr_t)prepare(value);
}
static void serialized(void)
{
    struct dchid_iface *iface = fresh("multi-touch", true);
    pthread_t first, second; void *result;
    block_acquire = true;
    assert(!pthread_create(&first, NULL, prepare_thread, iface));
    assert(!pthread_mutex_lock(&block_mutex));
    while (!acquire_entered) assert(!pthread_cond_wait(&block_condition, &block_mutex));
    assert(!pthread_mutex_unlock(&block_mutex));
    assert(!pthread_create(&second, NULL, prepare_thread, iface));
    for (int i = 0; i < 1000 && atomic_load(&lock_attempts) < 2; ++i) usleep(1000);
    assert(atomic_load(&lock_attempts) == 2 && acquisitions == 1);
    assert(!pthread_mutex_lock(&block_mutex)); release_acquire = true;
    assert(!pthread_cond_broadcast(&block_condition)); assert(!pthread_mutex_unlock(&block_mutex));
    assert(!pthread_join(first, &result) && !result);
    assert(!pthread_join(second, &result) && !result);
    assert(acquisitions == 1 && key_queries == 1 && !gpio_writes && !smc_writes);
    assert(!provider_refs && !parent_refs && device_refs == 1 && actions == 1);
}
int main(int argc, char **argv)
{
    assert(argc == 2); alarm(5);
    if (!strcmp(argv[1], "default")) assert(!m4_afe_action);
    else if (!strcmp(argv[1], "sequence")) sequence();
    else if (!strcmp(argv[1], "startup")) startup();
    else if (!strcmp(argv[1], "live-parent")) live_parent();
    else if (!strcmp(argv[1], "guards")) guards();
    else if (!strcmp(argv[1], "preflight")) preflight();
    else if (!strcmp(argv[1], "metadata")) metadata();
    else if (!strcmp(argv[1], "failures")) failures();
    else if (!strcmp(argv[1], "ownership")) ownership();
    else if (!strcmp(argv[1], "callback")) callback();
    else if (!strcmp(argv[1], "framing")) framing();
    else if (!strcmp(argv[1], "serialized")) serialized();
    else if (!strcmp(argv[1], "config")) config();
    else assert(false);
    printf("PASS %s\n", argv[1]); return 0;
}
