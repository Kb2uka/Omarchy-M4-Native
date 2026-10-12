/* Deterministic shared-SRAM race at the real metadata copy boundary. */
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint8_t u8;
typedef uint32_t u32;
typedef uint64_t u64;
typedef uint32_t smc_key;
#define SMC_MSG_GET_KEY_INFO 0x13
struct apple_smc_key_info { u8 size; u32 type_code; u8 flags; };
struct apple_smc {
    pthread_mutex_t mutex;
    struct { void *iomem; } shmem;
};
struct mutex_guard { pthread_mutex_t *mutex; };
static struct mutex_guard acquire(pthread_mutex_t *mutex)
{
    assert(pthread_mutex_lock(mutex) == 0);
    return (struct mutex_guard){ mutex };
}
static void release(struct mutex_guard *guard)
{
    if (guard->mutex)
        assert(pthread_mutex_unlock(guard->mutex) == 0);
}
#define guard(kind) struct mutex_guard scope_guard __attribute__((cleanup(release))) = acquire
static const u8 expected[6] = { 4, 'u', 'i', '3', '2', 0x80 };
static const u8 competing[6] = { 255, 'B', 'A', 'D', '!', 0x01 };
static struct apple_smc *current;
static pthread_mutex_t rendezvous = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t signaled = PTHREAD_COND_INITIALIZER;
static pthread_t writer;
static bool writer_started, writer_observed_lock, writer_reported;
static unsigned copies, commands;
static int command_result;

static void *competing_command(void *unused)
{
    (void)unused;
    int result = pthread_mutex_trylock(&current->mutex);
    assert(result == 0 || result == EBUSY);
    assert(pthread_mutex_lock(&rendezvous) == 0);
    writer_observed_lock = result == EBUSY;
    if (!writer_observed_lock) {
        memcpy(current->shmem.iomem, competing, sizeof(competing));
        assert(pthread_mutex_unlock(&current->mutex) == 0);
    }
    writer_reported = true;
    assert(pthread_cond_signal(&signaled) == 0);
    assert(pthread_mutex_unlock(&rendezvous) == 0);
    if (writer_observed_lock) {
        assert(pthread_mutex_lock(&current->mutex) == 0);
        memcpy(current->shmem.iomem, competing, sizeof(competing));
        assert(pthread_mutex_unlock(&current->mutex) == 0);
    }
    return NULL;
}
static __attribute__((unused)) void memcpy_fromio(void *destination, const void *source, size_t count)
{
    assert(source == current->shmem.iomem);
    assert(count == sizeof(expected));
    ++copies;
    assert(!writer_started);
    writer_started = true;
    assert(pthread_create(&writer, NULL, competing_command, NULL) == 0);
    assert(pthread_mutex_lock(&rendezvous) == 0);
    while (!writer_reported)
        assert(pthread_cond_wait(&signaled, &rendezvous) == 0);
    assert(pthread_mutex_unlock(&rendezvous) == 0);
    memcpy(destination, source, count);
}
static u32 get_unaligned_be32(const u8 *p)
{
    return (u32)p[0] << 24 | (u32)p[1] << 16 | (u32)p[2] << 8 | p[3];
}
static int apple_smc_cmd_locked(struct apple_smc *smc, u64 cmd, u64 key,
                                u64 size, u64 wsize, u32 *ret_data)
{
    assert(smc == current && cmd == SMC_MSG_GET_KEY_INFO);
    assert(key == 0x704b5734 && !size && !wsize && !ret_data);
    ++commands;
    if (command_result >= 0)
        memcpy(smc->shmem.iomem, expected, sizeof(expected));
    return command_result;
}
/* ACTUAL_FUNCTIONS */

int main(int argc, char **argv)
{
    assert(argc == 2);
    bool null_info = !strcmp(argv[1], "null") || !strcmp(argv[1], "null_error");
    bool error = !strcmp(argv[1], "error") || !strcmp(argv[1], "null_error");
    command_result = error ? -EIO : (!strcmp(argv[1], "positive") ? 6 : 0);
    u8 sram[6] = {0};
    struct apple_smc smc = { .mutex = PTHREAD_MUTEX_INITIALIZER, .shmem = { sram } };
    struct apple_smc_key_info info = { .size = 0x55, .type_code = 0x12345678, .flags = 0x66 };
    current = &smc;
    int result = apple_smc_get_key_info(&smc, 0x704b5734, null_info ? NULL : &info);
    assert(result == command_result);
    assert(commands == 1);
    if (writer_started)
        assert(pthread_join(writer, NULL) == 0);
    /* Every exit releases the mutex, including null-info and error paths. */
    assert(pthread_mutex_trylock(&smc.mutex) == 0);
    assert(pthread_mutex_unlock(&smc.mutex) == 0);
    if (!error && !null_info) {
        assert(copies == 1);
        assert(info.size == 4);
        assert(info.type_code == 0x75693332);
        assert(info.flags == 0x80);
        assert(writer_observed_lock);
        assert(!memcmp(sram, competing, sizeof(sram)));
    } else {
        assert(copies == 0);
        assert(info.size == 0x55 && info.type_code == 0x12345678 && info.flags == 0x66);
    }
    assert(pthread_mutex_destroy(&smc.mutex) == 0);
    puts("PASS: metadata and competing command are serialized; exit contract preserved");
    return 0;
}
