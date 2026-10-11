#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Load the policy with actual WirePlumber 0.5.18 in a leased, private runtime.

The runtime contains the copied installed binaries, libraries, modules and stock
data under usr/. Hardware discovery is disabled; only a private Unix PipeWire
socket is used. This is an integration check, not a Lua/JSON parser substitute.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / 'audio/j713/policy'
CONF = POLICY / 'etc/wireplumber/wireplumber.conf.d/82-j713-protected-default.conf'
HOOK = POLICY / 'usr/share/wireplumber/scripts/default-nodes/j713-protected.lua'
REGISTERED = 'Registered hook default-nodes/j713-protected successfully'

NO_HARDWARE = '''wireplumber.profiles = {
  main = {
    hardware.audio = disabled
    hardware.bluetooth = disabled
    hardware.video-capture = disabled
    support.dbus = disabled
    support.system-dbus = disabled
    support.mpris = disabled
    support.notifications = disabled
    support.reserve-device = disabled
    support.portal-permissionstore = disabled
    support.logind = disabled
  }
}
'''


def run_case(runtime, output, fragment, qemu, expect_failure):
    output.mkdir(mode=0o700)
    config, data = output / 'config', output / 'data'
    shutil.copytree(runtime / 'usr/share/wireplumber', config)
    shutil.copytree(runtime / 'usr/share/wireplumber', data)
    fragments = config / 'wireplumber.conf.d'
    fragments.mkdir(exist_ok=True)
    (fragments / CONF.name).write_text(fragment)
    (fragments / '99-j713-test-no-hardware.conf').write_text(NO_HARDWARE)
    shutil.copyfile(HOOK, data / 'scripts/default-nodes/j713-protected.lua')
    run, state = output / 'run', output / 'state'
    run.mkdir(mode=0o700)
    state.mkdir(mode=0o700)
    env = dict(os.environ, XDG_RUNTIME_DIR=str(run), PIPEWIRE_RUNTIME_DIR=str(run),
               XDG_CONFIG_HOME=str(output / 'unused-config'), XDG_STATE_HOME=str(state),
               WIREPLUMBER_CONFIG_DIR=str(config), WIREPLUMBER_DATA_DIR=str(data),
               WIREPLUMBER_MODULE_DIR=str(runtime / 'usr/lib/wireplumber-0.5'),
               PIPEWIRE_CONFIG_DIR=str(runtime / 'usr/share/pipewire'),
               PIPEWIRE_MODULE_DIR=str(runtime / 'usr/lib/pipewire-0.3'),
               SPA_PLUGIN_DIR=str(runtime / 'usr/lib/spa-0.2'), WIREPLUMBER_DEBUG='4')

    def command(name):
        prefix = [qemu, '-L', str(runtime)] if qemu else []
        return prefix + [str(runtime / 'usr/bin' / name)]

    version = subprocess.run(command('wireplumber') + ['--version'], env=env,
                             check=True, capture_output=True, text=True).stdout
    assert 'Compiled with libwireplumber 0.5.18' in version, version
    assert 'Linked with libwireplumber 0.5.18' in version, version
    (output / 'version.txt').write_text(version)
    wp = None
    with (output / 'pipewire.log').open('w') as pwlog, (output / 'wireplumber.log').open('w') as wplog:
        pw = subprocess.Popen(command('pipewire'), env=env, stdout=pwlog, stderr=pwlog)
        try:
            until = time.monotonic() + 10
            while not (run / 'pipewire-0').exists() and pw.poll() is None and time.monotonic() < until:
                time.sleep(.05)
            assert pw.poll() is None and (run / 'pipewire-0').exists(), (output / 'pipewire.log').read_text()
            wp = subprocess.Popen(command('wireplumber'), env=env, stdout=wplog, stderr=wplog)
            until = time.monotonic() + 8
            while wp.poll() is None and time.monotonic() < until:
                time.sleep(.1)
            log = (output / 'wireplumber.log').read_text()
            if expect_failure:
                assert wp.poll() == 78, log[-10000:]
                assert "no component provides '\"policy.default-nodes\"'" in log, log[-10000:]
                assert REGISTERED not in log
                print('PASS: quoted dependency reproduces exit78/CONFIG', flush=True)
            else:
                assert wp.poll() is None, log[-10000:]
                assert REGISTERED in log, log[-10000:]
                assert 'failed to load components' not in log
                print('PASS: required policy component loaded and hook registered', flush=True)
        finally:
            for child in (wp, pw):
                if child is not None and child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--qemu', help='qemu-aarch64 path when executing the ARM64 runtime on another architecture')
    args = parser.parse_args()
    runtime = args.runtime_root.resolve(strict=True)
    output = args.output.resolve()
    output.mkdir(mode=0o700)
    fragment = CONF.read_text()
    dependency = 'requires = [ policy.default-nodes ]'
    assert fragment.count(dependency) == 1, 'Expected unquoted stock dependency token'
    broken = fragment.replace(dependency, 'requires = [ "policy.default-nodes" ]')
    run_case(runtime, output / 'quoted-control', broken, args.qemu, True)
    run_case(runtime, output / 'shipped', fragment, args.qemu, False)


if __name__ == '__main__':
    main()
