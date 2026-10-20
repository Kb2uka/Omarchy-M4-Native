"""Independent review coverage for the launcher's actual local file operations."""
import fcntl
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
import run_probe as rp

def _seals():
    return fcntl.F_SEAL_WRITE|fcntl.F_SEAL_GROW|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_SEAL

def _fd_count():
    return len(os.listdir('/proc/self/fd'))

class RealIO(unittest.TestCase):
    def test_known_hash_chunks(self):
        self.assertEqual(rp.hash_opened_bytes(lambda: iter([b'a',b'',b'bc'])),
                         'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')

    def test_real_fd_hash_and_rewind(self):
        host=rp.RealHost()
        with tempfile.TemporaryDirectory() as tmp:
            data=b'A'*(1024*1024)+b'last-chunk'
            p=Path(tmp)/'module.ko';p.write_bytes(data)
            fd=host.open_module(str(p))
            try:
                os.lseek(fd,17,os.SEEK_SET)
                self.assertEqual(host.hash_fd(fd),hashlib.sha256(data).hexdigest())
                self.assertEqual(os.lseek(fd,0,os.SEEK_CUR),0)
                self.assertEqual(os.read(fd,8),data[:8])
            finally: host.close_fd(fd)

    def test_reject_directory_without_fd_leak(self):
        host=rp.RealHost()
        with tempfile.TemporaryDirectory() as tmp:
            before=len(os.listdir('/proc/self/fd'))
            with self.assertRaisesRegex(rp.ProbeError,'not a regular file'):
                host.open_module(tmp)
            self.assertEqual(len(os.listdir('/proc/self/fd')),before)

    def test_hash_then_inplace_write_keeps_sealed_bytes(self):
        host=rp.RealHost()
        original=b'verified-module-bytes'
        changed=b'X'*len(original)
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'module.ko'
            p.write_bytes(original)
            before=_fd_count()
            fd=host.open_module(str(p))
            try:
                self.assertEqual(fcntl.fcntl(fd,fcntl.F_GET_SEALS)&_seals(),_seals())
                self.assertEqual(fcntl.fcntl(fd,fcntl.F_GETFL)&os.O_ACCMODE,os.O_RDONLY)
                self.assertEqual(host.hash_fd(fd),hashlib.sha256(original).hexdigest())
                self.assertEqual(os.lseek(fd,0,os.SEEK_CUR),0)
                p.write_bytes(changed)
                self.assertEqual(p.read_bytes(),changed)
                self.assertEqual(os.read(fd,len(original)),original)
            finally:
                host.close_fd(fd)
            self.assertEqual(_fd_count(),before)

    def test_path_replacement_keeps_sealed_bytes(self):
        host=rp.RealHost()
        original=b'verified-module-bytes'
        replaced=b'Y'*len(original)
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'module.ko'
            other=Path(tmp)/'other.ko'
            p.write_bytes(original)
            other.write_bytes(replaced)
            before=_fd_count()
            fd=host.open_module(str(p))
            try:
                digest=host.hash_fd(fd)
                os.replace(other,p)
                self.assertEqual(p.read_bytes(),replaced)
                self.assertEqual(host.hash_fd(fd),digest)
                self.assertEqual(os.read(fd,len(original)),original)
            finally:
                host.close_fd(fd)
            self.assertEqual(_fd_count(),before)

    def test_returned_fd_is_sealed_readonly_copy(self):
        host=rp.RealHost()
        original=b'verified-module-bytes'
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'module.ko'
            p.write_bytes(original)
            before=_fd_count()
            fd=host.open_module(str(p))
            try:
                live=os.stat(p)
                frozen=os.fstat(fd)
                self.assertNotEqual((frozen.st_dev,frozen.st_ino),(live.st_dev,live.st_ino))
                self.assertEqual(fcntl.fcntl(fd,fcntl.F_GETFL)&os.O_ACCMODE,os.O_RDONLY)
                alias=os.open('/proc/self/fd/%d'%fd,os.O_RDWR|os.O_CLOEXEC)
                try:
                    self.assertEqual(fcntl.fcntl(alias,fcntl.F_GETFL)&os.O_ACCMODE,os.O_RDWR)
                    with self.assertRaises(OSError):
                        os.write(alias,b'Z')
                finally:
                    os.close(alias)
                self.assertEqual(host.hash_fd(fd),hashlib.sha256(original).hexdigest())
            finally:
                host.close_fd(fd)
            self.assertEqual(_fd_count(),before)

    def test_partial_copy_matches_source_bytes(self):
        host=rp.RealHost()
        original=b'P'*4096+b'tail'
        real=rp.os.write
        calls={'n':0}
        def short(fd,data):
            calls['n']+=1
            if calls['n']>(len(original)*2):
                raise OSError(5,'partial write did not advance')
            view=data if isinstance(data,memoryview) else memoryview(data)
            n=1 if len(view)>1 else len(view)
            return real(fd,view[:n])
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'module.ko'
            p.write_bytes(original)
            before=_fd_count()
            rp.os.write=short
            try:
                fd=host.open_module(str(p))
            finally:
                rp.os.write=real
            try:
                self.assertGreater(calls['n'],1)
                self.assertEqual(p.read_bytes(),original)
                self.assertEqual(host.hash_fd(fd),hashlib.sha256(original).hexdigest())
                self.assertEqual(os.read(fd,len(original)),original)
                self.assertEqual(fcntl.fcntl(fd,fcntl.F_GET_SEALS)&_seals(),_seals())
            finally:
                host.close_fd(fd)
            self.assertEqual(_fd_count(),before)

    def test_open_module_failure_injections_do_not_leak_fds(self):
        host=rp.RealHost()
        original=b'verified-module-bytes'
        for stage in ('copy','seal','reopen','fstat'):
            with self.subTest(stage=stage):
                with tempfile.TemporaryDirectory() as tmp:
                    p=Path(tmp)/'module.ko'
                    p.write_bytes(original)
                    before=_fd_count()
                    restore=self._install_open_failure(stage)
                    try:
                        with self.assertRaises(OSError):
                            host.open_module(str(p))
                    finally:
                        restore()
                    self.assertEqual(_fd_count(),before)
                    self.assertEqual(p.read_bytes(),original)

    def _install_open_failure(self,stage):
        if stage=='copy':
            real=rp.os.write
            def boom(fd,data):
                raise OSError(5,'copy failed')
            rp.os.write=boom
            return lambda: setattr(rp.os,'write',real)
        if stage=='seal':
            real=rp.fcntl.fcntl
            def boom(fd,op,arg=0):
                if op==fcntl.F_ADD_SEALS:
                    raise OSError(5,'seal failed')
                return real(fd,op,arg)
            rp.fcntl.fcntl=boom
            return lambda: setattr(rp.fcntl,'fcntl',real)
        if stage=='reopen':
            real=rp.os.open
            def boom(path,flags,*args,**kwargs):
                if isinstance(path,str) and path.startswith('/proc/self/fd/'):
                    raise OSError(5,'reopen failed')
                return real(path,flags,*args,**kwargs)
            rp.os.open=boom
            return lambda: setattr(rp.os,'open',real)
        if stage=='fstat':
            real=rp.os.fstat
            state={'n':0}
            def boom(fd):
                state['n']+=1
                if state['n']>=2:
                    raise OSError(5,'fstat failed')
                return real(fd)
            rp.os.fstat=boom
            return lambda: setattr(rp.os,'fstat',real)
        raise AssertionError(stage)

if __name__=='__main__': unittest.main()
