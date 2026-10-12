/* Host shims for the extracted DockChannel startup functions. */
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
typedef uint8_t u8;
typedef uint32_t u32;
typedef uint64_t u64;
typedef uint32_t __le32;
typedef uintptr_t dma_addr_t;
#define __packed __attribute__((packed))
#define cpu_to_le32(v) (v)
#define GFP_KERNEL 0
#define WQ_MEM_RECLAIM 0
#define MAX_INTERFACES 16
#define MAX_GPIO_NAME 32
#define FW_MAGIC 0x46444948
#define FW_VER 1
#define CMD_RESET_INTERFACE 0x40
#define CMD_SEND_FIRMWARE 0x95
/* ACTUAL_CONSTANTS */
#define dev_info(...) ((void)0)
#define dev_warn(...) ((void)0)
#define dev_err(...) ((void)0)
#define IS_ERR_OR_NULL(p) (!(p))
#define PTR_ERR(p) (-EINVAL)
#define wmb() atomic_thread_fence(memory_order_seq_cst)
#define msecs_to_jiffies(n) (n)
struct device_node { int unused; };
struct device { struct device_node *of_node; };
struct hid_device { void *driver_data; };
struct work_struct { int unused; };
struct workqueue_struct { int unused; };
struct gpio_desc { int unused; };
struct mutex { pthread_mutex_t native; bool initialized; };
struct completion { atomic_bool done; };
struct firmware { const u8 *data; size_t size; };
static bool m4_power_v2 = true, board = true, chip = true;
static atomic_uint lock_attempts;
static void mutex_init(struct mutex *m) {
 assert(pthread_mutex_init(&m->native, NULL) == 0); m->initialized = true;
}
static __attribute__((unused)) void mutex_lock(struct mutex *m) {
 assert(m->initialized); atomic_fetch_add(&lock_attempts, 1);
 assert(pthread_mutex_lock(&m->native) == 0);
}
static __attribute__((unused)) void mutex_unlock(struct mutex *m) { assert(pthread_mutex_unlock(&m->native) == 0); }
static void init_completion(struct completion *c) { atomic_init(&c->done, false); }
static bool completion_done(struct completion *c) { return atomic_load(&c->done); }
static void complete_all(struct completion *c) { atomic_store(&c->done, true); }
static bool of_machine_is_compatible(const char *s) {
 return !strcmp(s, "apple,j713") ? board : !strcmp(s, "apple,t8132") && chip;
}
/* ACTUAL_TYPES */
struct dockchannel_hid { struct device *dev; struct dchid_iface *ifaces[MAX_INTERFACES]; };
static struct device_node node;
static struct workqueue_struct workqueue;
static unsigned requests, copies, dmas, commands, waits, fail_command, ready_wait;
static unsigned late_wait, max_waits, fail_copy, fail_dma;
static int command_error = -EIO, request_error;
static bool error_ready, concurrent;
static unsigned char packets[64][16];
static size_t packet_sizes[64];
static struct dchid_iface *active_iface;
static pthread_mutex_t wait_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t wait_cond = PTHREAD_COND_INITIALIZER;
static bool wait_entered, release_wait;
static struct { struct fw_header header; u8 payload[32]; } firmware_blob;
static struct firmware firmware_file;
static void *devm_kzalloc(struct device *d, size_t n, int g) { (void)d;(void)g;return calloc(1,n); }
static char *devm_kstrdup(struct device *d, const char *s, int g) { (void)d;(void)g;return strdup(s); }
static struct workqueue_struct *alloc_ordered_workqueue(const char *f, int g, const char *s) {
 (void)f;(void)g;(void)s;return &workqueue;
}
static const struct device_node *of_get_child_by_name(struct device_node *n, const char *s) {
 (void)n;(void)s;return &node;
}
static int of_property_read_string(const struct device_node *n, const char *s, const char **out) {
 (void)n;assert(!strcmp(s,"firmware-name"));*out="fixture.bin";return 0;
}
static int request_firmware(const struct firmware **fw, const char *n, struct device *d) {
 (void)n;(void)d;requests++;*fw=&firmware_file;assert(request_error<=0);return request_error;
}
static void release_firmware(const struct firmware *fw) { assert(fw==&firmware_file); }
static void *devm_kmemdup(struct device *d, const void *p, size_t n, int g) {
 (void)d;(void)g;copies++;if(fail_copy)return NULL;void *b=malloc(n);assert(b);memcpy(b,p,n);return b;
}
static void *dmam_alloc_coherent(struct device *d,size_t n,dma_addr_t *addr,int g) {
 (void)d;(void)g;dmas++;if(fail_dma)return NULL;void *b=malloc(n);assert(b);*addr=(uintptr_t)b;return b;
}
/* The separate AFE fixture exercises the opt-in host sequence. */
static __attribute__((unused)) int dchid_prepare_afe(struct dchid_iface *i) { (void)i;return 0; }
static __attribute__((unused)) int dchid_afe_action(struct dchid_iface *i, int state) { (void)i;(void)state;return 0; }
static int dchid_request_gpio(struct dchid_iface *i) { (void)i;return 0; }
static int dchid_comm_cmd(struct dockchannel_hid *d, void *v, size_t n) {
 (void)d;assert(commands<64 && n<=16);memcpy(packets[commands],v,n);packet_sizes[commands]=n;
 commands++;
 if(commands==fail_command) { if(error_ready)complete_all(&active_iface->ready);return command_error; }
 return 9; /* The native positive power ACK length is success, not errno 9. */
}
static unsigned long wait_for_completion_timeout(struct completion *c, unsigned long timeout) {
 assert(timeout==2000);waits++;assert(waits<=max_waits);
 if(concurrent) {
  assert(pthread_mutex_lock(&wait_lock)==0);wait_entered=true;pthread_cond_broadcast(&wait_cond);
  while(!release_wait) assert(pthread_cond_wait(&wait_cond,&wait_lock)==0);
  assert(pthread_mutex_unlock(&wait_lock)==0);complete_all(c);return 1;
 }
 if(waits==ready_wait) { complete_all(c);return 1; }
 if(waits==late_wait)complete_all(c); /* READY races the wait returning zero. */
 return 0;
}
/* ACTUAL_FUNCTIONS */
static struct hid_device fresh(const char *name) {
 static struct device dev={.of_node=&node};
 struct dockchannel_hid *d=calloc(1,sizeof(*d));assert(d);d->dev=&dev;
 active_iface=dchid_get_interface(d,7,name);assert(active_iface);
 requests=copies=dmas=commands=waits=fail_command=ready_wait=late_wait=0;
 fail_copy=fail_dma=0;command_error=-EIO;request_error=0;error_ready=concurrent=false;
 max_waits=3;wait_entered=release_wait=false;atomic_store(&lock_attempts,0);
 board=chip=m4_power_v2=true;
 firmware_blob.header=(struct fw_header){FW_MAGIC,FW_VER,sizeof(struct fw_header),32,1};
 memset(firmware_blob.payload,0x5a,32);
 firmware_file=(struct firmware){(const u8 *)&firmware_blob,sizeof(firmware_blob)};
 return (struct hid_device){.driver_data=active_iface};
}
static void full_start(unsigned offset) {
 assert(packet_sizes[offset]==16 && packets[offset][0]==CMD_SEND_FIRMWARE);
 for(unsigned k=0;k<4;k++) {
  assert(packet_sizes[offset+1+k]==9);
  assert(packets[offset+1+k][0]==CMD_RESET_INTERFACE);
  assert(packets[offset+1+k][1]==2 && packets[offset+1+k][2]==7);
  assert(packets[offset+1+k][3]==(k<2?0:2));
  assert(packets[offset+1+k][4]==k%2);
 }
}
static void first_timeout(void) {
 struct hid_device h=fresh("multi-touch");ready_wait=2;
 assert(dchid_open(&h)==0);assert(waits==2 && commands==10);
 full_start(0);full_start(5);assert(active_iface->open && !active_iface->starting);
 assert(requests==1 && copies==1 && dmas==1);
 u64 a,b;memcpy(&a,packets[0]+4,8);memcpy(&b,packets[5]+4,8);
 assert(a==b && ((u8 *)(uintptr_t)a)[1]==7);
}
static void all_timeout(void) {
 struct hid_device h=fresh("multi-touch");
 for(unsigned n=0;n<3;n++) {
  waits=commands=0;assert(dchid_open(&h)==-ETIMEDOUT);
  assert(waits==3 && commands==15 && !active_iface->starting && !active_iface->open);
  full_start(0);full_start(5);full_start(10);
 }
 assert(requests==1 && copies==1 && dmas==1);
 waits=commands=0;ready_wait=1;assert(dchid_open(&h)==0);assert(commands==5);
}
static void normal_ready(void) {
 struct hid_device h=fresh("multi-touch");ready_wait=1;
 assert(dchid_open(&h)==0 && waits==1 && commands==5 && !active_iface->starting);
 assert(dchid_open(&h)==0 && waits==1 && commands==5);
 h=fresh("multi-touch");complete_all(&active_iface->ready);
 assert(dchid_open(&h)==0 && !commands && !waits && !requests);
}
static void errors(void) {
 for(unsigned stage=1;stage<=5;stage++) {
  struct hid_device h=fresh("multi-touch");fail_command=stage;
  assert(dchid_open(&h)==-EIO && commands==stage && !waits && !active_iface->starting);
  assert(!active_iface->open);
  fail_command=0;ready_wait=1;assert(dchid_open(&h)==0 && !active_iface->starting);
  assert(requests==1 && copies==1 && dmas==1);
 }
 struct hid_device h=fresh("multi-touch");fail_command=3;command_error=-ETIMEDOUT;error_ready=true;
 assert(dchid_open(&h)==-ETIMEDOUT && commands==3 && !waits && !active_iface->open);
 h=fresh("multi-touch");request_error=-ENOENT;
 assert(dchid_open(&h)==-ENOENT && !commands && !waits && !active_iface->starting);
 h=fresh("multi-touch");firmware_blob.header.magic=0;
 assert(dchid_open(&h)==-EINVAL && !copies && !commands && !active_iface->starting);
 h=fresh("multi-touch");fail_copy=1;
 assert(dchid_open(&h)==-ENOMEM && !commands && !active_iface->starting);
 h=fresh("multi-touch");fail_dma=1;
 assert(dchid_open(&h)==-ENOMEM && !commands && !active_iface->starting);
 fail_dma=0;ready_wait=1;assert(dchid_open(&h)==0 && copies==1 && dmas==2);
}
static void late_ready(void) {
 for(unsigned last=1;last<=3;last++) {
  struct hid_device h=fresh("multi-touch");late_wait=last;
  assert(dchid_open(&h)==0 && waits==last && commands==5*last);
  assert(active_iface->open && !active_iface->starting);
 }
 struct hid_device h=fresh("multi-touch");assert(dchid_open(&h)==-ETIMEDOUT);
 complete_all(&active_iface->ready);assert(dchid_open(&h)==0 && waits==3 && commands==15);
}
static void guards(void) {
 for(unsigned g=0;g<4;g++) {
  struct hid_device h=fresh(g==3?"keyboard":"multi-touch");
  if(g==0)m4_power_v2=false;
  if(g==1)board=false;
  if(g==2)chip=false;
  max_waits=1;assert(dchid_open(&h)==-ETIMEDOUT && waits==1);
  assert(commands==(g==3?5u:3u));
  /* The legacy path has no retry or new terminal-state semantics. */
  assert(active_iface->starting && !active_iface->open);
  assert(dchid_open(&h)==-EINPROGRESS && waits==1);
 }
}
static void *open_thread(void *p) { return (void *)(intptr_t)dchid_open(p); }
static void serialized(void) {
 struct hid_device h=fresh("multi-touch");concurrent=true;pthread_t a,b;
 assert(pthread_create(&a,NULL,open_thread,&h)==0);
 assert(pthread_mutex_lock(&wait_lock)==0);
 while(!wait_entered)assert(pthread_cond_wait(&wait_cond,&wait_lock)==0);
 assert(pthread_mutex_unlock(&wait_lock)==0);
 assert(pthread_create(&b,NULL,open_thread,&h)==0);
 for(unsigned n=0;n<1000 && atomic_load(&lock_attempts)<2;n++)usleep(1000);
 assert(atomic_load(&lock_attempts)==2);
 assert(pthread_mutex_lock(&wait_lock)==0);release_wait=true;pthread_cond_broadcast(&wait_cond);
 assert(pthread_mutex_unlock(&wait_lock)==0);
 void *ar,*br;assert(!pthread_join(a,&ar) && !pthread_join(b,&br));
 assert(!ar && !br && commands==5 && waits==1 && copies==1 && dmas==1);
 assert(active_iface->open && !active_iface->starting);
}
int main(int argc,char **argv) {
 assert(argc==2);alarm(5);
 if(!strcmp(argv[1],"first_timeout"))first_timeout();
 else if(!strcmp(argv[1],"all_timeout"))all_timeout();
 else if(!strcmp(argv[1],"normal_ready"))normal_ready();
 else if(!strcmp(argv[1],"errors"))errors();
 else if(!strcmp(argv[1],"late_ready"))late_ready();
 else if(!strcmp(argv[1],"guards"))guards();
 else if(!strcmp(argv[1],"serialized"))serialized();
 else assert(0);
 printf("PASS %s\n",argv[1]);return 0;
}
