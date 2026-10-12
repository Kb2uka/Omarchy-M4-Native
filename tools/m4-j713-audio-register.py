#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Register the exact J713 providers once per boot; retain every partial failure."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import time

MANIFEST = Path('/etc/omarchy/j713-audio-manifest.json')
RUNTIME = Path('/run/omarchy/j713-audio-registration')
MODULES = ('snd_soc_j713_group', 'apple_admac', 'snd_soc_apple_mca',
           'snd_soc_tas2764', 'snd_soc_macaudio', 'j713_audio_bootstrap', 'j713_audio_dt3')
ADDRESSES = ('0038', '003b', '0039', '003c')
FRESH_LOW = dict(stable='1', phase='0', generation='0', public_generation='0', owner='0',
                 control_owner='0', cancelled='1', faulted='0', removing='0', error='0',
                 sensing='0', gpio_error='0', gpio_low_verified='1', gpio_hw='00070a02',
                 gpio_cache='00070a02', profile='1', sequence='0', deadline_ns='0',
                 mute_ns='0', mute_mask='f')


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def trusted_file(path, expected=None):
    for parent in (path, *path.parents):
        info = parent.lstat()
        require(not stat.S_ISLNK(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
                'Untrusted installed path: ' + str(path))
    require(path.is_file(), 'Not a regular file: ' + str(path))
    data = path.read_bytes()
    if expected is not None:
        require(hashlib.sha256(data).hexdigest() == expected, 'Installed hash mismatch: ' + str(path))
    return data


class ProviderPending(RuntimeError):
    """Expected kernel provider has not completed binding yet."""


class Registration:
    """Filesystem and command boundaries are explicit for off-host lifecycle fixtures."""
    def __init__(self, manifest, root=Path('/'), command=None, sleeper=time.sleep):
        self.manifest = manifest
        self.root = root
        self.command = command or self._command
        self.sleep = sleeper
        self.events = []
        self.dt = self.p('/sys/firmware/devicetree/base')

    def p(self, path):
        return self.root / str(path).lstrip('/')

    def read(self, path):
        return self.p(path).read_text().strip()

    def _command(self, argv):
        result = subprocess.run(argv, capture_output=True, text=True, timeout=40)
        self.events.append(dict(argv=argv, result=result.returncode, stdout=result.stdout, stderr=result.stderr))
        require(result.returncode == 0, 'Command failed: ' + repr(argv))
        return result.stdout.strip()

    def module(self, name):
        entry = self.manifest['module_artifacts'][name]
        base = self.p('/sys/module/' + name)
        require((base / 'initstate').read_text().strip() == 'live', 'Module not live: ' + name)
        require((base / 'notes/.note.gnu.build-id').read_bytes().hex() == entry['note'], 'Module note: ' + name)

    def driver(self, device, name, module=None):
        base = self.p('/sys/bus/platform/devices/' + device)
        require((base / 'driver').resolve(strict=True) == self.p('/sys/bus/platform/drivers/' + name),
                'Provider binding: ' + device)
        if module:
            require((base / 'driver/module').resolve(strict=True) == self.p('/sys/module/' + module),
                    'Provider module: ' + device)

    def low(self):
        status = dict(item.split('=', 1) for item in self.read('/sys/bus/platform/devices/speaker-group/status').split())
        require(all(status.get(k) == v for k, v in FRESH_LOW.items()), 'Supplier is not fresh LOW: ' + repr(status))
        return status

    def preflight(self):
        m = self.manifest
        require(set(m['module_artifacts']) == set(MODULES), 'Module set mismatch')
        require(m['kernel_release'] == os.uname().release, 'Unsupported running kernel')
        require(m['board'] == 'apple,j713' and m['soc'] == 'apple,t8132', 'Unsupported package board')
        compatible = (self.dt / 'compatible').read_bytes().split(b'\0')
        require(b'apple,j713' in compatible and b'apple,t8132' in compatible, 'Unsupported live board')
        for name in MODULES:
            require(not self.p('/sys/module/' + name).exists(), 'Module already present: ' + name)
        modules = self.p('/sys/module')
        require(not list(modules.glob('m4_speaker_*')) and not list(modules.glob('j713_audio_*')),
                'Previous diagnostic or topology loader present')
        for name in ('sound', 'speaker-group', 'soc/power-management@388284000',
                     'soc/i2s@393600000', 'soc/dma-controller@393800000'):
            require(not (self.dt / name).exists(), 'Audio topology already present: ' + name)
        require(not self.p('/proc/asound/AppleJ713').exists(), 'Sound card already present')
        require(self.command(['systemctl', 'show', m['service'], '-p', 'ActiveState', '--value']) == 'inactive',
                'Protection service already active')
        for entry in m['module_artifacts'].values():
            require(self.command(['modinfo', '-F', 'vermagic', entry['path']]).startswith(
                m['kernel_release'] + ' SMP preempt mod_unload aarch64'), 'Module ABI mismatch')

    def load(self, name, *parameters):
        self.command(['insmod', self.manifest['module_artifacts'][name]['path'], *parameters])
        self.module(name)

    def provider_driver(self, device, name, module=None):
        link = self.p('/sys/bus/platform/devices/' + device + '/driver')
        if not os.path.lexists(link):
            raise ProviderPending('Provider not bound: ' + device)
        # Wrong or dangling bindings are faults, not a reason to wait.
        self.driver(device, name, module)

    def wait_providers(self):
        # udev queue completion does not join the kernel deferred-probe worker.
        # Read-only checks only: never reload a module, overlay or driver here.
        for attempt in range(101):
            try:
                return self.providers()
            except ProviderPending as error:
                if attempt == 100:
                    raise RuntimeError('Provider readiness timed out: ' + str(error)) from error
                self.sleep(.1)

    def providers(self):
        for address in ('20', '90', 'b8', 'd8', 'e8'):
            self.provider_driver('388284000.power-management:power-controller@' + address, 'apple-pmgr-pwrstate')
        self.provider_driver('390f00000.iommu', 'apple-dart')
        self.provider_driver('3ad01c000.i2c', 'i2c-apple')
        self.provider_driver('393800000.dma-controller', 'apple-admac', 'apple_admac')
        mca = self.p('/sys/bus/platform/devices/393600000.i2s')
        if not mca.exists():
            raise ProviderPending('MCA platform device absent')
        require(not os.path.lexists(mca / 'driver'), 'Unexpected pre-group MCA state')
        adapters = [p for p in self.p('/sys/bus/i2c/devices').glob('i2c-*')
                    if (p / 'of_node').resolve() == self.dt / 'soc/i2c@3ad01c000']
        if not adapters:
            raise ProviderPending('Speaker adapter absent')
        require(len(adapters) == 1, 'Speaker adapter ambiguous')
        bus = adapters[0].name[4:]
        require(not list(self.p('/sys/bus/i2c/devices').glob(bus + '-*')), 'Speaker bus is not childless')
        return bus

    def complete(self, bus):
        for _ in range(50):
            if self.p('/sys/bus/platform/devices/speaker-group/status').exists() and all(
                    self.p('/sys/bus/i2c/devices/' + bus + '-' + a + '/driver/module').exists() for a in ADDRESSES):
                break
            self.sleep(.1)
        self.low()
        self.driver('speaker-group', 'j713-speaker-group', 'snd_soc_j713_group')
        for address in ADDRESSES:
            base = self.p('/sys/bus/i2c/devices/' + bus + '-' + address)
            require((base / 'driver/module').resolve(strict=True) == self.p('/sys/module/snd_soc_tas2764'),
                    'Codec module: ' + address)
            require((base / 'of_node').resolve(strict=True) == self.dt / ('soc/i2c@3ad01c000/amplifier@' + address[-2:]),
                    'Codec identity: ' + address)
        if not self.p('/sys/bus/platform/devices/393600000.i2s/driver').exists():
            self.p('/sys/bus/platform/drivers/apple-mca/bind').write_text('393600000.i2s\n')
        self.driver('393600000.i2s', 'apple-mca', 'snd_soc_apple_mca')
        # ALSA publishes this card after registering its control and PCM devices.
        for _ in range(50):
            if self.p('/proc/asound/AppleJ713').exists():
                break
            self.sleep(.1)
        self.driver('sound', 'snd-soc-macaudio', 'snd_soc_macaudio')
        for direction in ('p', 'c'):
            require(self.read('/proc/asound/AppleJ713/pcm0' + direction + '/sub0/status') == 'closed', 'PCM already open')
        return self.low()

    def apply(self):
        self.command(['/usr/bin/python3', '/usr/lib/omarchy/m4-j713-audio-ready.py', 'invalidate'])
        self.command(['modprobe', 'snd_soc_core'])
        self.command(['modprobe', 'snd_pcm_dmaengine'])
        for name in MODULES[:5]:
            self.load(name)
        self.load('j713_audio_bootstrap', 'apply=1')
        bp = '/sys/module/j713_audio_bootstrap/parameters/'
        require(self.read(bp + 'result') == '0' and self.read(bp + 'stage') == '4'
                and int(self.read(bp + 'overlay_id')) > 0, 'Bootstrap incomplete; retained')
        bus = self.wait_providers()
        self.load('j713_audio_dt3', 'apply=1')
        bp = '/sys/module/j713_audio_dt3/parameters/'
        require(self.read(bp + 'result') == '0' and int(self.read(bp + 'overlay_id')) > 0, 'Topology incomplete; retained')
        return self.complete(bus)


def write_record(path, content):
    with path.open('x') as stream:
        json.dump(content, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def register(registration, runtime, identity):
    require(not (runtime / 'attempt.json').exists(), 'Registration already attempted this boot; no automatic retry')
    registration.preflight()
    write_record(runtime / 'attempt.json', identity)
    try:
        result = registration.apply()
        write_record(runtime / 'registered.json', dict(identity, status=result, events=registration.events))
    except BaseException as error:
        write_record(runtime / 'failure.json', dict(identity, error=repr(error), events=registration.events))
        raise


def main():
    require(os.getuid() == 0, 'Root required')
    raw = trusted_file(MANIFEST)
    manifest = json.loads(raw)
    for name, expected in manifest['files'].items():
        require(name.startswith('/') and '..' not in Path(name).parts, 'Invalid installed manifest path')
        trusted_file(Path(name), expected)
    require(manifest['modules'] == {n: e['note'] for n, e in manifest['module_artifacts'].items()}, 'Module maps disagree')
    for name, entry in manifest['module_artifacts'].items():
        require(entry['module'] == name and manifest['files'].get(entry['path']) == entry['sha256'], 'Module not pinned')
    RUNTIME.parent.mkdir(mode=0o755, exist_ok=True)
    RUNTIME.mkdir(mode=0o700, exist_ok=True)
    for directory, mode in ((RUNTIME.parent, 0o755), (RUNTIME, 0o700)):
        info = directory.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == mode, 'Runtime ownership/mode')
    fd = os.open('/run/lock/m4-audio-test.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o077, 'Untrusted lock')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        identity = dict(boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                        manifest_sha256=hashlib.sha256(raw).hexdigest(), started_ns=time.time_ns())
        register(Registration(manifest), RUNTIME, identity)
    finally:
        os.close(fd)


if __name__ == '__main__':
    main()
