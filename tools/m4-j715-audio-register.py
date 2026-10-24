#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Register one exact, qualified J715 selection per boot; retain partial failure."""
import fcntl
import importlib.util
import json
import os
import struct
import subprocess
import sys
import time
from pathlib import Path

spec = importlib.util.spec_from_file_location('_j715_session_register', Path(__file__).with_name('m4-j715-audio-session.py'))
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)
owner = s.load('m4-j713-audio-register.py')
publication = s.load('m4-j713-audio-ready.py')
require = s.require
MODULES = s.MODULES
ADDRESSES = ('0038', '003b', '0039', '003c', '003a', '003d')
FRESH_LOW = dict(owner.FRESH_LOW, mute_mask='3f')
RUNTIME = '/run/omarchy/j715-audio-registration'


def module_note(data: bytes) -> str:
    """Read the exact ELF64 AArch64 note bytes later exposed by sysfs."""
    require(data[:6] == b'\x7fELF\x02\x01' and
            struct.unpack_from('<HH', data, 16) == (1, 183), 'Not an AArch64 module')
    offset = struct.unpack_from('<Q', data, 40)[0]
    size, count, names = struct.unpack_from('<HHH', data, 58)
    require(size == 64 and 0 < names < count and offset + size * count <= len(data), 'Bad ELF sections')
    sections = [struct.unpack_from('<IIQQQQIIQQ', data, offset + i * size) for i in range(count)]
    strings = sections[names]
    table = data[strings[4]:strings[4] + strings[5]]
    notes = [entry for entry in sections if entry[1] == 7 and
             table[entry[0]:].split(b'\0', 1)[0] == b'.note.gnu.build-id']
    require(len(notes) == 1 and notes[0][4] + notes[0][5] <= len(data), 'Missing ELF build ID')
    note = data[notes[0][4]:notes[0][4] + notes[0][5]]
    name_size, id_size, kind = struct.unpack_from('<III', note)
    require(name_size == 4 and kind == 3 and id_size > 0 and note[12:16] == b'GNU\0'
            and len(note) == 16 + (id_size + 3) // 4 * 4, 'Invalid GNU build ID')
    return note.hex()


class Registration(owner.Registration):
    """Reuse provider/driver joins unchanged; specialize board and exact selection."""
    def _command(self, argv):
        result = subprocess.run(argv, capture_output=True, text=True, timeout=40,
                                env=s.command_environment(), check=False)
        self.events.append({'argv': argv, 'result': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})
        require(result.returncode == 0, 'Command failed: ' + repr(argv))
        return result.stdout.strip()

    def low(self):
        status = dict(item.split('=', 1) for item in self.read('/sys/bus/platform/devices/speaker-group/status').split())
        require(all(status.get(k) == v for k, v in FRESH_LOW.items()), 'Supplier is not fresh LOW')
        return status

    def preflight(self):
        s.selected(self.root, self.manifest)
        m = self.manifest
        require(set(m['module_artifacts']) == set(MODULES), 'Module set mismatch')
        require(m['kernel_release'] == self.read('/proc/sys/kernel/osrelease'), 'Unsupported running kernel')
        require(m['board'] == 'apple,j715' and m['soc'] == 'apple,t8132', 'Unsupported package board')
        compatible = (self.dt / 'compatible').read_bytes().split(b'\0')
        require(b'apple,j715' in compatible and b'apple,t8132' in compatible, 'Unsupported live board')
        for name in MODULES:
            require(not self.p('/sys/module/' + name).exists(), 'Module already present: ' + name)
        modules = self.p('/sys/module')
        require(not list(modules.glob('m4_speaker_*')) and not list(modules.glob('j713_audio_*')),
                'Previous diagnostic or topology loader present')
        for name in ('sound', 'speaker-group', 'soc/power-management@388284000',
                     'soc/i2s@393600000', 'soc/dma-controller@393800000'):
            require(not (self.dt / name).exists(), 'Audio topology already present: ' + name)
        require(not self.p('/proc/asound/AppleJ715').exists(), 'Sound card already present')
        require(self.command(['systemctl', 'show', m['service'], '-p', 'ActiveState', '--value']) == 'inactive',
                'Protection service already active')
        for entry in m['module_artifacts'].values():
            require(module_note(s.pinned(self.root, entry['path'])) == entry['note'], 'Module build ID mismatch')
            require(self.command(['modinfo', '-F', 'vermagic', entry['path']]) == entry['vermagic'], 'Module ABI mismatch')

    def complete(self, bus):
        for _ in range(50):
            if self.p('/sys/bus/platform/devices/speaker-group/status').exists() and all(
                    self.p('/sys/bus/i2c/devices/' + bus + '-' + a + '/driver/module').exists() for a in ADDRESSES):
                break
            self.sleep(.1)
        self.low()
        self.driver('speaker-group', 'j715-speaker-group', 'snd_soc_j713_group')
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
            if self.p('/proc/asound/AppleJ715').exists():
                break
            self.sleep(.1)
        self.driver('sound', 'snd-soc-macaudio', 'snd_soc_macaudio')
        for direction in ('p', 'c'):
            require(self.read('/proc/asound/AppleJ715/pcm0' + direction + '/sub0/status') == 'closed', 'PCM already open')
        return self.low()

    def apply(self):
        self.command(['/usr/bin/python3', s.BASE + '/m4-j715-audio-ready.py', 'invalidate'])
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



def register(registration, runtime: Path, identity) -> None:
    require(not os.path.lexists(runtime / 'attempt.json'), 'Registration already attempted this boot; no automatic retry')
    registration.preflight()
    identity = dict(identity, selection_sha256=s.hashlib.sha256(
        json.dumps(registration.manifest, sort_keys=True).encode()).hexdigest())
    publication.atomic_record(registration.root, runtime / 'selected.json', registration.manifest, 0o644)
    owner.write_record(runtime / 'attempt.json', identity)
    try:
        result = registration.apply()
        owner.write_record(runtime / 'registered.json', dict(identity, status=result, events=registration.events))
    except BaseException as error:
        # Retain interruption as well as command failure; never undo a partial load.
        owner.write_record(runtime / 'failure.json', dict(identity, error=repr(error), events=registration.events))
        raise


def main(root: Path = Path('/'), command=None) -> int:
    try:
        require(os.geteuid() == 0, 'Root required')
        runtime = s.at(root, RUNTIME)
        current = root
        s.trusted(current, root, directory=True)
        for part in runtime.relative_to(root).parts:
            current /= part
            if not os.path.lexists(current):
                current.mkdir(mode=0o755)
                current.chmod(0o755)
            s.trusted(current, root, directory=True)
        lock = s.at(root, '/run/lock/m4-audio-test.lock')
        s.trusted(lock.parent, root, directory=True)
        fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            info = os.fstat(fd)
            require(info.st_uid == 0 and info.st_nlink == 1 and not info.st_mode & 0o077, 'Untrusted common lock')
            s.trusted(lock, root)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            require(not os.path.lexists(runtime / 'attempt.json'), 'Registration already attempted; no automatic retry')
            manifest, _, _ = s.artifacts(root)
            registration = Registration(manifest, root, command)
            # The helper is invoked anew here, never a cached report or CLI ID.
            report = registration.command(['/usr/bin/python3', '-B', s.HELPER]).encode()
            registration.manifest = s.selection(root, report)
            identity = {'boot': registration.manifest['boot'], 'started_ns': time.time_ns(),
                            'artifact_manifest_sha256': registration.manifest['artifact_manifest_sha256']}
            register(registration, runtime, identity)
        finally:
            os.close(fd)
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, struct.error, subprocess.SubprocessError) as error:
        print(json.dumps({'status': 'not-registered', 'error': str(error)}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
