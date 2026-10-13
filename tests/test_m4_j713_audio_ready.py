# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
from contextlib import redirect_stdout, redirect_stderr
import copy
import configparser
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import shlex
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(os.environ.get('READINESS_SOURCE', str(Path(__file__).resolve().parents[1] / 'tools/m4-j713-audio-ready.py')))
spec = importlib.util.spec_from_file_location('ready', SOURCE)
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
FIXTURE = json.loads((Path(__file__).parent / 'fixtures/j713-readiness-pcm.json').read_text())


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name); self.now = 100_000_000_000; self.sequence = 1
        self.uid = 0; self.changer = None
        self.group = dict(x.split('=') for x in FIXTURE['status'].split())
        self.group.update(generation='7', public_generation='7')
        self.write('/sys/firmware/devicetree/base/compatible', b'apple,j713\0apple,t8132\0')
        self.write('/proc/sys/kernel/osrelease', 'qualified-kernel')
        self.write('/proc/sys/kernel/random/boot_id', 'test-boot')
        self.write(r.DAEMON, b'qualified daemon'); self.write(r.PROFILE, b'qualified profile')
        self.write('/proc/4242/exe', b'qualified daemon')
        self.proc_start = 12345; self.process()
        self.write('/proc/4242/status', 'Uid:\t0\t0\t0\t0\nTgid:\t4242\n')
        self.write('/proc/4242/cgroup', '0::/system.slice/'+r.SERVICE+'\n')
        self.write(r.PCM+'c/sub0/status', FIXTURE['rx_status'])
        self.write(r.PCM+'c/sub0/hw_params', FIXTURE['rx_hw'])
        self.write(r.PCM+'p/sub0/status', 'closed')
        self.svc = dict(ActiveState='active', SubState='running', Result='success',
                        MainPID='4242', InvocationID='a'*32, ControlGroup='/system.slice/'+r.SERVICE)
        self.manifest = dict(schema=1, service=r.SERVICE, kernel_release='qualified-kernel',
            files={r.DAEMON:hashlib.sha256(b'qualified daemon').hexdigest(), r.PROFILE:hashlib.sha256(b'qualified profile').hexdigest()},
            modules={m:'01020304' for m in r.MODULES})
        for m in r.MODULES:
            self.write('/sys/module/'+m+'/initstate', 'live')
            self.write('/sys/module/'+m+'/notes/.note.gnu.build-id', bytes.fromhex('01020304'))
        self.write('/etc/j713-manifest.json', json.dumps(self.manifest)); self.refresh()
        self.addCleanup(patch.stopall)
        actual_lstat = Path.lstat
        def root_owned(path, *args, **kwargs):
            info = list(actual_lstat(path,*args,**kwargs)); info[4] = self.uid; return os.stat_result(info)
        patch.object(Path,'lstat',root_owned).start()
        actual_fstat=os.fstat
        def root_owned_fd(fd):
            info=list(actual_fstat(fd));info[4]=self.uid;return os.stat_result(info)
        patch.object(r.os,'fstat',side_effect=root_owned_fd).start()
        patch.object(r.os,'geteuid',return_value=0).start()
        patch.object(r.os,'readlink',return_value=r.DAEMON).start()
        patch.object(r.time,'monotonic_ns',side_effect=lambda:self.now).start()
        patch.object(r.time,'sleep',side_effect=self.sleep).start()
        self.command = patch.object(r.subprocess,'check_output',side_effect=self.service).start()

    def write(self,path,data):
        p=self.root/path.lstrip('/');p.parent.mkdir(parents=True,exist_ok=True)
        p.write_bytes(data if isinstance(data,bytes) else data.encode());p.chmod(0o644)
        for directory in p.parents:
            if directory==self.root:break
            directory.chmod(0o755)
        return p

    def process(self):
        fields=['0']*40; fields[0]='S';fields[19]=str(self.proc_start)
        self.write('/proc/4242/stat','4242 (speaker daemon) '+' '.join(fields))

    def refresh(self):
        self.group.update(sequence=str(self.sequence), deadline_ns=str(self.now+50_000_000))
        self.write(r.GROUP,' '.join(k+'='+v for k,v in self.group.items()))

    def sleep(self,seconds):
        self.now+=int(seconds*1e9);self.sequence+=1;self.refresh()
        if self.changer:self.changer()

    def service(self,argv,**kwargs):
        self.assertEqual(argv[:4],['systemctl','--system','show',r.SERVICE])
        self.assertEqual(kwargs['timeout'],2)
        return '\n'.join(k+'='+v for k,v in self.svc.items())

    def cli(self,mode='startup',ready=None,publish=False):
        argv=[mode,'--manifest','/etc/j713-manifest.json']
        if publish:argv+=['--publish']
        if ready is not None:
            self.write('/run/ready.json',json.dumps(ready));argv+=['--ready','/run/ready.json']
        out,err=io.StringIO(),io.StringIO()
        with redirect_stdout(out),redirect_stderr(err):rc=r.main(argv,root=self.root)
        return rc,json.loads(out.getvalue() or err.getvalue())

    def ready(self):
        rc,value=self.cli();self.assertEqual(rc,0,value);return value

    def reject(self,*args,**kwargs):
        rc,value=self.cli(*args,**kwargs);self.assertEqual(rc,1,value);self.assertEqual(value['status'],'not-ready');return value

    def test_startup_cli_native_pcm_format_fifteen_seconds(self):
        self.svc.update(ActiveState='activating',SubState='start-post')
        value=self.ready();self.assertGreaterEqual(value['observed_seconds'],15)
        self.assertEqual(value['identity']['pid'],4242);self.assertEqual(value['identity']['generation'],7)
        self.assertGreater(value['sample']['sequence'],1)

    def test_gate_cli_mute_and_active(self):
        record=self.ready();self.assertEqual(self.cli('gate',record)[0],0)
        self.group['phase']='3';self.refresh();self.write(r.PCM+'p/sub0/status','state: RUNNING\nowner_pid: 9000')
        self.assertEqual(self.cli('gate',record)[0],0);self.reject()

    def test_unprivileged_gate_does_not_read_root_executable(self):
        record=self.ready()
        with patch.object(r.os,"geteuid",return_value=1234), patch.object(r.os,"readlink",side_effect=PermissionError):
            self.assertEqual(self.cli("gate",record)[0],0)
            self.reject()

    def test_gate_rejects_stale_record(self):
        record=self.ready()
        for key,value in [('boot','old'),('pid',4343),('start',12346),('invocation','b'*32),('generation',8),('manifest_sha256','0'*64)]:
            stale=copy.deepcopy(record);stale['identity'][key]=value
            with self.subTest(key=key):self.reject('gate',stale)

    def test_identity_changes_during_observation(self):
        def boot():self.write('/proc/sys/kernel/random/boot_id','other')
        def pid():self.svc['MainPID']='4343'
        def reused():self.proc_start+=1;self.process()
        def invocation():self.svc['InvocationID']='b'*32
        for mutate in (boot,pid,reused,invocation):
            with self.subTest(mutate=mutate.__name__):
                self.write('/proc/sys/kernel/random/boot_id','test-boot');self.svc.update(MainPID='4242',InvocationID='a'*32)
                self.changer=mutate;self.reject()
        self.changer=None

    def test_dead_daemon_and_early_manager(self):
        self.svc.update(ActiveState='inactive',SubState='dead',MainPID='0');self.reject()
        self.svc.update(ActiveState='activating',SubState='start-post',MainPID='4242')
        record=self.ready();self.reject('gate',record)
        self.svc.update(ActiveState='active',SubState='running')
        (self.root/'proc/4242/stat').unlink();self.reject()

    def test_epoch_and_nonadvancing_lease(self):
        def epoch():self.group.update(generation='8',public_generation='8');self.refresh()
        self.changer=epoch;self.reject()
        self.changer=lambda:self.write(r.GROUP,' '.join(k+'='+('1' if k=='sequence' else v) for k,v in self.group.items()))
        self.sequence=1;self.refresh();self.reject()

    def test_expired_lease(self):
        self.group['deadline_ns']=str(self.now-1);self.write(r.GROUP,' '.join(k+'='+v for k,v in self.group.items()));self.reject()

    def prelease(self):
        self.group.update(phase='2', sequence='0', sensing='0', public_generation='0',
                          mute_ns='0', deadline_ns=str(self.now+2_000_000_000))
        self.write(r.GROUP,' '.join(k+'='+v for k,v in self.group.items()))
        self.write(r.PCM+'c/sub0/status','closed')

    def test_phase_two_before_first_lease_waits_then_qualifies(self):
        self.svc.update(ActiveState='activating',SubState='start-post')
        self.prelease();began=self.now
        def trigger():
            self.group.update(sensing='1',public_generation='7',mute_ns='1')
            self.write(r.PCM+'c/sub0/status',FIXTURE['rx_status']);self.refresh()
        self.changer=trigger
        value=self.ready()
        self.assertGreaterEqual(self.now-began,15_100_000_000)
        self.assertGreater(value['sample']['sequence'],0)

    def test_clean_reused_low_waits_for_new_owned_generation(self):
        self.group.update(phase='0',generation='1',public_generation='0',owner='0',control_owner='0',
                          cancelled='1',deadline_ns='0',gpio_low_verified='1',gpio_hw='00070a02',gpio_cache='00070a02')
        self.write(r.PCM+'c/sub0/status','closed')
        self.write(r.GROUP,' '.join(k+'='+v for k,v in self.group.items()));began=self.now
        def start():
            self.group.update(phase='2',generation='2',public_generation='2',owner='1',control_owner='1',
                              cancelled='0',sensing='1',mute_ns='1')
            self.write(r.PCM+'c/sub0/status',FIXTURE['rx_status']);self.refresh()
        self.changer=start
        value=self.ready();self.assertEqual(value['identity']['generation'],2)
        self.assertGreaterEqual(self.now-began,15_100_000_000)

    def test_low_rx_open_setup_waits_only_for_same_daemon(self):
        for state in ('OPEN','SETUP','PREPARED','RUNNING'):
            with self.subTest(state=state):
                self.group.update(phase='0',generation='1',public_generation='0',owner='0',control_owner='1',
                                  cancelled='1',deadline_ns='0',gpio_low_verified='1',gpio_hw='00070a02',gpio_cache='00070a02')
                self.write(r.GROUP,' '.join(k+'='+v for k,v in self.group.items()))
                self.write(r.PCM+'c/sub0/status','state: '+state+'\nowner_pid: 4242')
                def start():
                    self.group.update(phase='2',generation='2',public_generation='2',owner='1',control_owner='1',
                                      cancelled='0',sensing='1',mute_ns='1')
                    self.write(r.PCM+'c/sub0/status',FIXTURE['rx_status']);self.refresh()
                self.changer=start;self.ready()
        self.changer=None
        self.group.update(phase='0',generation='2',public_generation='0',owner='0',control_owner='1',
                          cancelled='1',deadline_ns='0',gpio_low_verified='1',gpio_hw='00070a02',gpio_cache='00070a02')
        self.write(r.GROUP,' '.join(k+'='+v for k,v in self.group.items()))
        self.write(r.PCM+'c/sub0/status','state: OPEN\nowner_pid: 4343');self.reject()
        self.write(r.PCM+'c/sub0/status','state: XRUN\nowner_pid: 4242');self.reject()

    def test_gate_prelease_then_root_observation_and_record_wait(self):
        record=self.ready();self.prelease();began=self.now
        self.svc.update(ActiveState='activating',SubState='start-post')
        def progress():
            self.group.update(sensing='1',public_generation='7',mute_ns='1')
            self.write(r.PCM+'c/sub0/status',FIXTURE['rx_status']);self.refresh()
            if self.now-began>=300_000_000:self.svc.update(ActiveState='active',SubState='running')
            if self.now-began>=500_000_000:self.write('/run/ready.json',json.dumps(record))
        self.changer=progress
        out,err=io.StringIO(),io.StringIO()
        with redirect_stdout(out),redirect_stderr(err):
            rc=r.main(['gate','--manifest','/etc/j713-manifest.json','--ready','/run/ready.json'],root=self.root)
        self.assertEqual(rc,0,err.getvalue());self.assertGreaterEqual(self.now-began,1_500_000_000)

    def test_mute_running_before_first_renew_has_configure_deadline(self):
        self.group.update(sequence='0',deadline_ns=str(self.now+2_000_000_000))
        self.write(r.GROUP,' '.join(k+'='+v for k,v in self.group.items()));began=self.now
        value=self.ready()
        self.assertGreaterEqual(self.now-began,15_100_000_000)
        self.assertGreater(value['sample']['sequence'],0)
        self.assertLessEqual(value['sample']['deadline_ns']-value['sample']['observed_monotonic_ns'],50_000_000)

    def test_initial_pending_guards_refuse_bad_deadline_tx_and_low_gpio(self):
        self.prelease();self.group['deadline_ns']=str(self.now+2_000_000_001)
        self.write(r.GROUP,' '.join(k+'='+v for k,v in self.group.items()))
        self.assertIn('configure',self.reject()['error'])
        self.prelease();self.write(r.PCM+'p/sub0/status','state: RUNNING')
        self.assertIn('closed TX',self.reject()['error'])
        self.write(r.PCM+'p/sub0/status','closed')
        self.group.update(phase='0',generation='1',public_generation='0',owner='0',control_owner='0',
                          cancelled='1',deadline_ns='0',gpio_low_verified='1',gpio_hw='00070a03',gpio_cache='00070a02')
        self.write(r.GROUP,' '.join(k+'='+v for k,v in self.group.items()))
        def recover():
            self.group.update(phase='2',generation='2',public_generation='2',owner='1',control_owner='1',
                              cancelled='0',sensing='1',mute_ns='1',gpio_hw='00070a02')
            self.write(r.PCM+'c/sub0/status',FIXTURE['rx_status']);self.refresh()
        self.changer=recover
        self.assertIn('LOW',self.reject()['error'])

    def test_first_lease_pending_expiry_fault_and_epoch_refused(self):
        self.prelease();deadline=self.group['deadline_ns']
        def never():
            self.group.update(sequence='0',deadline_ns=deadline)
            self.write(r.GROUP,' '.join(k+'='+v for k,v in self.group.items()))
        self.changer=never
        self.assertIn('configure',self.reject()['error'])
        self.assertLessEqual(self.now,102_100_000_000)
        self.changer=None;self.prelease();self.group['faulted']='1'
        self.write(r.GROUP,' '.join(k+'='+v for k,v in self.group.items()));self.reject()
        self.group['faulted']='0';self.prelease()
        def replaced():
            self.group.update(generation='8',public_generation='8',sensing='1',mute_ns='1')
            self.write(r.PCM+'c/sub0/status',FIXTURE['rx_status']);self.refresh()
        self.changer=replaced
        self.assertIn('epoch',self.reject()['error'])

    def test_geometry_and_rx_ownership(self):
        for old,new in [('1024','512'),('2048','1024'),('48000','44100'),('S16_LE','S32_LE'),('channels: 8','channels: 4'),('RW_INTERLEAVED','MMAP_INTERLEAVED')]:
            with self.subTest(new=new):self.write(r.PCM+'c/sub0/hw_params',FIXTURE['rx_hw'].replace(old,new));self.reject()
        self.write(r.PCM+'c/sub0/hw_params',FIXTURE['rx_hw'])
        self.write(r.PCM+'c/sub0/status',FIXTURE['rx_status'].replace('4242','4343'));self.reject()

    def test_fault_and_unstable_refusal(self):
        for field in ('cancelled','faulted','removing','error','gpio_error'):
            with self.subTest(field=field):self.group[field]='1';self.refresh();self.reject();self.group[field]='0'
        self.group['stable']='0';self.refresh();self.reject()

    def test_platform_kernel_payload_and_module(self):
        for name,value in [('/sys/firmware/devicetree/base/compatible',b'apple,other\0apple,t8132\0'),('/proc/sys/kernel/osrelease','other'),(r.PROFILE,'changed'),('/sys/module/snd_soc_j713_group/initstate','going')]:
            path=self.root/name.lstrip('/');old=path.read_bytes();self.write(name,value);self.reject();self.write(name,old)

    def test_manifest_trust_and_malformed(self):
        self.uid=1001;self.reject();self.uid=0
        self.write('/etc/j713-manifest.json','{');self.reject()
        broken=copy.deepcopy(self.manifest);broken['files']=[r.DAEMON,r.PROFILE]
        self.write('/etc/j713-manifest.json',json.dumps(broken));self.reject()

    def test_service_timeout_useful_failure(self):
        self.command.side_effect=subprocess.TimeoutExpired('systemctl',2)
        self.assertIn('timed out',self.reject()['error'])

    def test_startup_waits_phase_zero_one_two_then_fifteen_seconds(self):
        self.svc.update(ActiveState='activating',SubState='start-post')
        self.group.update(phase='0',generation='0',public_generation='0',owner='0',control_owner='0',cancelled='1',
                          gpio_low_verified='1',gpio_hw='00070a02',gpio_cache='00070a02',deadline_ns='0')
        self.write(r.PCM+'c/sub0/status','closed');self.write(r.GROUP,' '.join(k+'='+v for k,v in self.group.items()));began=self.now
        def transition():
            if self.now-began>=200_000_000:
                self.group.update(phase='2',generation='7',public_generation='7',owner='1',control_owner='1',cancelled='0')
                self.write(r.PCM+'c/sub0/status',FIXTURE['rx_status'])
            else:self.group.update(phase='1',generation='7',owner='1',control_owner='1',cancelled='0')
            self.refresh()
        self.changer=transition
        result=self.ready();self.assertGreaterEqual(self.now-began,15_200_000_000)
        self.assertGreaterEqual(result['observed_seconds'],15)

    def test_gate_waits_for_system_startup_and_record(self):
        record=self.ready();began=self.now
        self.svc.update(ActiveState='activating',SubState='start-post')
        self.write('/run/ready.json',json.dumps(record));(self.root/'run/ready.json').unlink()
        def transition():
            if self.now-began>=200_000_000:
                self.svc.update(ActiveState='active',SubState='running')
                self.write('/run/ready.json',json.dumps(record))
        self.changer=transition
        out,err=io.StringIO(),io.StringIO()
        with redirect_stdout(out),redirect_stderr(err):
            rc=r.main(['gate','--manifest','/etc/j713-manifest.json','--ready','/run/ready.json'],root=self.root)
        self.assertEqual(rc,0,err.getvalue());self.assertGreaterEqual(self.now-began,1_200_000_000)

    def test_initial_wait_timeout_fault_and_identity_refused(self):
        self.group['phase']='1';self.refresh();began=self.now
        self.assertIn('timeout',self.reject()['error']);self.assertLessEqual(self.now-began,30_100_000_000)
        self.changer=lambda:self.svc.update(InvocationID='b'*32)
        self.assertIn('identity changed',self.reject()['error'])
        self.changer=lambda:self.group.update(faulted='1')
        self.assertIn('Fault',self.reject()['error'])

    def test_gate_waits_through_absent_registration_modules(self):
        record=self.ready();began=self.now
        for module in r.MODULES:
            (self.root/('sys/module/'+module+'/initstate')).unlink()
            (self.root/('sys/module/'+module+'/notes/.note.gnu.build-id')).unlink()
        self.svc.update(ActiveState='inactive',SubState='dead',MainPID='0')
        def registration():
            elapsed=self.now-began
            if elapsed>=100_000_000:
                for module in r.MODULES:
                    self.write('/sys/module/'+module+'/initstate','live')
                    self.write('/sys/module/'+module+'/notes/.note.gnu.build-id',bytes.fromhex('01020304'))
                self.svc.update(ActiveState='activating',SubState='start-post',MainPID='4242')
            if elapsed>=300_000_000:self.svc.update(ActiveState='active',SubState='running')
        self.changer=registration
        self.assertEqual(self.cli('gate',record)[0],0)
        self.assertGreaterEqual(self.now-began,1_300_000_000)

    def test_absent_registration_times_out_without_module_read(self):
        (self.root/'sys/module/snd_soc_j713_group/initstate').unlink()
        record={'status':'ready','mode':'startup','schema':1,'identity':{}}
        self.svc.update(ActiveState='inactive',SubState='dead',MainPID='0')
        began=self.now
        self.assertIn('timeout',self.reject('gate',record)['error'])
        self.assertGreaterEqual(self.now-began,30_000_000_000)

    def invalidate_cli(self):
        out,err=io.StringIO(),io.StringIO()
        with redirect_stdout(out),redirect_stderr(err):rc=r.main(['invalidate'],root=self.root)
        return rc,json.loads(out.getvalue() or err.getvalue())

    def test_publication_is_atomic_and_world_readable(self):
        original_replace=os.replace;published=[]
        def replace(source,destination):
            if Path(destination).name=='ready.json':
                self.assertFalse(Path(destination).exists())
                candidate=json.loads(Path(source).read_text())
                self.assertEqual(candidate['status'],'ready');self.assertGreaterEqual(candidate['observed_seconds'],15)
                published.append(candidate)
            original_replace(source,destination)
        with patch.object(r.os,'replace',side_effect=replace):rc,result=self.cli(publish=True)
        self.assertEqual(rc,0,result);self.assertEqual(len(published),1)
        path=self.root/r.RECORD.lstrip('/')
        self.assertEqual(path.stat().st_mode & 0o777,0o644)
        self.assertEqual(json.loads(path.read_text()),result)
        self.assertFalse(list(path.parent.glob('*.tmp')))
        with patch.object(r.os,'geteuid',return_value=1234), patch.object(r.os,'readlink',side_effect=PermissionError):
            out=io.StringIO()
            with redirect_stdout(out):self.assertEqual(r.main(['gate','--manifest','/etc/j713-manifest.json','--ready',r.RECORD],root=self.root),0)
        self.assertEqual(self.invalidate_cli()[0],0);self.assertFalse(path.exists())
        self.assertEqual(self.invalidate_cli()[0],0)

    def test_startup_failure_invalidates_previous_record(self):
        self.assertEqual(self.cli(publish=True)[0],0)
        self.write(r.PROFILE,'changed')
        self.assertEqual(self.cli(publish=True)[0],1)
        path=self.root/r.RECORD.lstrip('/')
        self.assertFalse(path.exists());self.assertFalse((path.parent/'.epoch').exists())

    def test_publication_write_failure_leaves_no_ready_record(self):
        original_replace=os.replace
        def fail(source,destination):
            if Path(destination).name=='ready.json':raise OSError('injected replace failure')
            original_replace(source,destination)
        with patch.object(r.os,'replace',side_effect=fail):
            rc,result=self.cli(publish=True)
        self.assertEqual(rc,1);self.assertIn('injected',result['error'])
        path=self.root/r.RECORD.lstrip('/')
        self.assertFalse(path.exists());self.assertFalse((path.parent/'.epoch').exists())
        self.assertFalse(list(path.parent.glob('*.tmp')))

    def test_stop_invalidation_wins_over_inflight_startup(self):
        once=[]
        def stop():
            if not once:once.append(1);r.invalidate(self.root)
        self.changer=stop
        rc,result=self.cli(publish=True)
        self.assertEqual(rc,1,result);self.assertFalse((self.root/r.RECORD.lstrip('/')).exists())

    def test_superseded_attempt_cannot_publish_or_remove_new_record(self):
        result=self.ready();old=r.begin_publication(self.root);new=r.begin_publication(self.root)
        with self.assertRaises(ValueError):r.publish(self.root,self.manifest,result,old)
        current=r.publish(self.root,self.manifest,result,new)
        self.assertEqual(r.invalidate(self.root,old)['status'],'superseded')
        self.assertEqual(json.loads((self.root/r.RECORD.lstrip('/')).read_text()),current)

    def test_runtime_symlink_and_hardlink_refused_without_target_damage(self):
        directory=r.runtime_directory(self.root,True);victim=self.write('/victim','unchanged')
        ready=directory/'ready.json';ready.symlink_to(victim)
        self.assertEqual(self.invalidate_cli()[0],1);self.assertEqual(victim.read_text(),'unchanged')
        ready.unlink();os.link(victim,ready)
        self.assertEqual(self.invalidate_cli()[0],1);self.assertTrue(ready.exists())
        ready.unlink();lock=directory/'.lock';lock.unlink();lock.symlink_to(victim)
        self.assertEqual(self.invalidate_cli()[0],1);self.assertEqual(victim.read_text(),'unchanged')

    def test_runtime_parent_symlink_refused_before_creation(self):
        victim=self.root/'elsewhere';victim.mkdir()
        (self.root/'run').mkdir(exist_ok=True);(self.root/'run').chmod(0o755);(self.root/'run/omarchy').symlink_to(victim)
        self.assertEqual(self.cli(publish=True)[0],1)
        self.assertFalse(list(victim.iterdir()))

    def test_invalidation_is_root_only_and_needs_no_manifest(self):
        with patch.object(r.os,'geteuid',return_value=1234):self.assertEqual(self.invalidate_cli()[0],1)
        self.assertEqual(self.invalidate_cli()[0],0)

    def test_shipped_service_and_user_gate_cli_contract(self):
        package=Path(__file__).resolve().parents[1]/'packaging/j713-audio'
        protection=configparser.ConfigParser(interpolation=None);protection.read(package/'speakersafetyd-j713.service')
        user=configparser.ConfigParser(interpolation=None);user.read(package/'wireplumber.service.d/80-j713-audio-ready.conf')
        self.assertEqual(protection['Unit']['Requires'],'m4-j713-audio-register.service')
        self.assertIn('m4-j713-audio-register.service',protection['Unit']['After'].split())
        self.assertEqual(protection['Service']['Type'],'simple');self.assertEqual(protection['Service']['Restart'],'no')
        self.assertIn('omarchy/j713-audio-readiness',protection['Service']['RuntimeDirectory'].split())
        self.assertEqual(protection['Service']['RuntimeDirectoryMode'],'0755')
        self.assertEqual(protection['Install']['WantedBy'],'multi-user.target')
        self.assertEqual(protection['Service']['CPUSchedulingPolicy'],'fifo')
        self.assertTrue(1<=int(protection['Service']['CPUSchedulingPriority'])<=int(protection['Service']['LimitRTPRIO']))
        self.assertNotIn('Before',protection['Unit']);self.assertFalse(user.has_section('Unit'))
        self.assertLessEqual(int(user['Service']['TimeoutStartSec']),65)
        self.write('/etc/omarchy/j713-audio-manifest.json',json.dumps(self.manifest))
        for stage in ('ExecStartPre','ExecStartPost'):
            command=shlex.split(protection['Service'][stage])
            with redirect_stdout(io.StringIO()):self.assertEqual(r.main(command[2:],root=self.root),0)
        with patch.object(r.os,'geteuid',return_value=1234),patch.object(r.os,'readlink',side_effect=PermissionError):
            with redirect_stdout(io.StringIO()):self.assertEqual(r.main(shlex.split(user['Service']['ExecStartPre'])[2:],root=self.root),0)
        with redirect_stdout(io.StringIO()):self.assertEqual(r.main(shlex.split(protection['Service']['ExecStopPost'])[2:],root=self.root),0)
        self.assertFalse((self.root/r.RECORD.lstrip('/')).exists())

    def test_gate_needs_explicit_record(self):
        with redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):r.main(['gate','--manifest','/etc/j713-manifest.json'],root=self.root)

if __name__=='__main__':unittest.main()
