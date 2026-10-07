from pathlib import Path
import argparse,subprocess,tempfile
parser=argparse.ArgumentParser(description='Compile and check the actual before/after power-request helpers.')
parser.add_argument('--before',type=Path,required=True)
parser.add_argument('--after',type=Path,required=True)
args=parser.parse_args()
prefix=r'''
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <assert.h>
#include <stdio.h>
typedef uint8_t u8;
typedef uint32_t __le32;
#define __packed __attribute__((packed))
#define cpu_to_le32(x) (x)
#define CMD_RESET_INTERFACE 0x40
#define dev_info(...) ((void)0)
struct dockchannel_hid { void *dev; };
struct dchid_iface { unsigned index; struct dockchannel_hid *dchid; };
static bool m4_power_v2;
static bool board=true,chip=true;
static unsigned calls,fail_call;
static int success_result;
static size_t sizes[4];
static unsigned char packets[4][16];
static __attribute__((unused)) bool of_machine_is_compatible(const char *s) {
 return !strcmp(s,"apple,j713") ? board : !strcmp(s,"apple,t8132") && chip;
}
static int dchid_comm_cmd(struct dockchannel_hid *d, void *v, size_t n) {
 (void)d; assert(calls<4 && n<=16); sizes[calls]=n;
 memcpy(packets[calls],v,n); calls++;
 return calls==fail_call ? -5 : success_result;
}
static void clear(void) { calls=0;fail_call=0; memset(packets,0,sizeof(packets)); }
'''
suffix=r'''
int main(void) {
 struct dockchannel_hid d={0}; struct dchid_iface i={.index=7,.dchid=&d};
 const unsigned char v1[]={0x40,1,7,2};
 const unsigned char off0[]={0x40,2,7,0,0,0,0,0,0};
 const unsigned char off1[]={0x40,2,7,0,1,0,0,0,0};
 const unsigned char on0[]={0x40,2,7,2,0,0,0,0,0};
 const unsigned char on1[]={0x40,2,7,2,1,0,0,0,0};
 m4_power_v2=false;clear();assert(dchid_reset_interface(&i,2)==0);
 assert(calls==1 && sizes[0]==4 && !memcmp(packets[0],v1,4));
 m4_power_v2=true;board=false;clear();assert(dchid_reset_interface(&i,2)==0);
 assert(calls==1 && sizes[0]==4 && !memcmp(packets[0],v1,4));
 board=true;chip=false;clear();assert(dchid_reset_interface(&i,2)==0);
 assert(calls==1 && sizes[0]==4 && !memcmp(packets[0],v1,4));
 chip=true;clear();assert(dchid_reset_interface(&i,0)==0);
 assert(calls==2 && sizes[0]==9 && sizes[1]==9);
 assert(!memcmp(packets[0],off0,9) && !memcmp(packets[1],off1,9));
 clear();assert(dchid_reset_interface(&i,2)==0);
 assert(calls==2 && sizes[0]==9 && sizes[1]==9);
 assert(!memcmp(packets[0],on0,9) && !memcmp(packets[1],on1,9));
 clear();success_result=9;assert(dchid_reset_interface(&i,2)==9 && calls==2);
 success_result=0;
 clear();fail_call=1;assert(dchid_reset_interface(&i,0)==-5 && calls==1);
 clear();fail_call=2;assert(dchid_reset_interface(&i,2)==-5 && calls==2);
 puts("PASS: legacy/default, board guards, exact off/on phase bytes, error short-circuit");
 return 0;
}
'''
def extract(s):
 a=s.index('static int dchid_reset_interface(');b=s.index('\nstatic int dchid_send_firmware',a);return s[a:b]
before=extract(args.before.read_text());after=extract(args.after.read_text())
variants={'before':before,'after':after,'version_mutant':after.replace('.version = 2,','.version = 1,'),'phase_mutant':after.replace('request.phase = 1;','request.phase = 0;'),'packing_mutant':after.replace('} __packed request','} request'),'flag_mutant':after.replace('!m4_power_v2 ||','false ||')}
with tempfile.TemporaryDirectory(prefix='m4-input-wire-') as temp:
 t=Path(temp)
 for name,body in variants.items():
  c=t/(name+'.c');exe=t/name;c.write_text(prefix+body+suffix)
  subprocess.run(['cc','-O2','-Wall','-Wextra','-Werror',str(c),'-o',str(exe)],check=True)
  result=subprocess.run([str(exe)],capture_output=True,text=True)
  if name=='after':assert result.returncode==0,(name,result.stderr);print(name,result.stdout.strip(),flush=True)
  else:assert result.returncode!=0,name;print(name,'rejected',result.returncode,result.stderr.strip()[:200],flush=True)
print('All temporary executables and sources removed.')
