# SPDX-License-Identifier: MIT
"""J713 speaker protection around sleep: stop order, resume order, and fail-closed refusals."""
import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'tools/m4-j713-audio-sleep.py'
SPEC = importlib.util.spec_from_file_location('m4_j713_audio_sleep', SOURCE)
SLEEP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SLEEP)

LOW = ('stable=1 phase=0 generation=1 public_generation=0 owner=0 control_owner=0 sequence=9 deadline_ns=0 '
       'cancelled=1 faulted=0 removing=0 error=0 sensing=1 mute_ns=1 mute_mask=f gpio_hw=00070a02 '
       'gpio_cache=00070a02 gpio_error=0 gpio_low_verified=1 profile=1\n')
OWNED = ('stable=1 phase=2 generation=1 public_generation=1 owner=1 control_owner=1 sequence=9 deadline_ns=5 '
         'cancelled=0 faulted=0 removing=0 error=0 sensing=1 mute_ns=1 mute_mask=f gpio_hw=00070a03 '
         'gpio_cache=00070a03 gpio_error=0 gpio_low_verified=0 profile=1\n')
CANCELLED = LOW.replace('phase=0', 'phase=4')
LAPSED = LOW.replace('error=0', 'error=-62')


class Machine:
    """systemctl and the speaker group, as the coordinator sees them."""

    def __init__(self, root, protection=True, user_units=SLEEP.USER_UNITS):
        self.root = root
        self.protection = protection
        self.user_units = set(user_units)
        self.calls = []
        self.after_stop = LOW
        self.tx = 'closed'
        self.write_group(OWNED if protection else LOW)

    def write_group(self, text):
        path = self.root / SLEEP.GROUP
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def write_tx(self):
        path = self.root / SLEEP.TX
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.tx + '\n')

    def run(self, command, **kwargs):
        args = list(command[1:])
        if args[:1] == ['--user']:
            user, verb, units = args[2].split('@')[0], args[3], args[4:]
            self.calls.append(('user', user, verb, tuple(units)))
            if verb == 'is-active':
                out = '\n'.join('active' if u in self.user_units else 'inactive' for u in units) + '\n'
                return SimpleNamespace(returncode=0, stdout=out, stderr='')
            if verb == 'stop':
                self.user_units -= set(units)
            if verb == 'start':
                self.user_units |= set(units)
            return SimpleNamespace(returncode=0, stdout='', stderr='')
        verb, unit = args[0], args[-1]
        self.calls.append(('system', verb, unit))
        if verb == 'is-active':
            return SimpleNamespace(returncode=0 if self.protection else 3, stdout='', stderr='')
        if verb == 'stop':
            self.protection = False
            self.write_group(self.after_stop)
        if verb == 'start':
            self.protection = True
            self.write_group(OWNED)
        return SimpleNamespace(returncode=0, stdout='', stderr='')


class SleepCoordination(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        manager = self.root / SLEEP.RUN_USER / '1000' / 'systemd'
        manager.mkdir(parents=True)
        (manager / 'private').touch()

    def run_phase(self, machine, phase):
        machine.write_tx()
        log = io.StringIO()
        with patch.object(SLEEP.subprocess, 'run', machine.run), \
                patch.object(SLEEP.pwd, 'getpwuid', lambda uid: SimpleNamespace(pw_name='owner')), \
                patch.object(SLEEP.os, 'geteuid', lambda: 0), \
                patch.object(SLEEP, 'TX_CLOSE_SECONDS', 0), \
                contextlib.redirect_stderr(log):
            status = SLEEP.main([phase, '--root', str(self.root)])
        return status, log.getvalue()

    def test_user_audio_stops_before_protection_and_resumes_after_it(self):
        machine = Machine(self.root)
        self.assertEqual(self.run_phase(machine, 'pre')[0], 0)
        self.assertEqual(self.run_phase(machine, 'post')[0], 0)
        actions = [call for call in machine.calls if call[-2 if call[0] == 'system' else 2] in ('stop', 'start')]
        self.assertEqual(actions, [
            ('user', 'owner', 'stop', SLEEP.USER_UNITS),
            ('system', 'stop', SLEEP.PROTECTION),
            ('system', 'start', SLEEP.PROTECTION),
            ('user', 'owner', 'start', SLEEP.USER_UNITS),
        ])
        self.assertTrue(machine.protection)
        self.assertEqual(machine.user_units, set(SLEEP.USER_UNITS))
        self.assertFalse((self.root / SLEEP.STATE).exists())

    def test_only_the_user_units_that_were_running_come_back(self):
        machine = Machine(self.root, user_units=('pipewire.socket', 'pipewire.service', 'wireplumber.service'))
        self.run_phase(machine, 'pre')
        self.run_phase(machine, 'post')
        self.assertIn(('user', 'owner', 'start', ('pipewire.socket', 'wireplumber.service', 'pipewire.service')),
                      machine.calls)
        self.assertEqual(machine.user_units, {'pipewire.socket', 'pipewire.service', 'wireplumber.service'})

    def test_group_cancelled_during_sleep_keeps_protection_off(self):
        machine = Machine(self.root)
        self.run_phase(machine, 'pre')
        machine.write_group(CANCELLED)
        status, log = self.run_phase(machine, 'post')
        self.assertEqual(status, 1)
        self.assertNotIn(('system', 'start', SLEEP.PROTECTION), machine.calls)
        self.assertIn('stay off until a reboot', log)
        # User audio is still restored; the WirePlumber gate decides about the speakers.
        self.assertEqual(machine.user_units, set(SLEEP.USER_UNITS))

    def test_lapsed_lease_is_never_cleared(self):
        machine = Machine(self.root)
        machine.after_stop = LAPSED
        self.run_phase(machine, 'pre')
        status, _ = self.run_phase(machine, 'post')
        self.assertEqual(status, 1)
        self.assertNotIn(('system', 'start', SLEEP.PROTECTION), machine.calls)

    def test_speaker_output_open_at_resume_keeps_protection_off(self):
        machine = Machine(self.root)
        self.run_phase(machine, 'pre')
        machine.tx = 'state: PREPARED'
        status, _ = self.run_phase(machine, 'post')
        self.assertEqual(status, 1)
        self.assertNotIn(('system', 'start', SLEEP.PROTECTION), machine.calls)

    def test_protection_that_was_not_running_is_left_alone(self):
        machine = Machine(self.root, protection=False)
        self.run_phase(machine, 'pre')
        self.run_phase(machine, 'post')
        self.assertEqual([call for call in machine.calls if call[0] == 'user'], [])
        self.assertNotIn(('system', 'stop', SLEEP.PROTECTION), machine.calls)
        self.assertNotIn(('system', 'start', SLEEP.PROTECTION), machine.calls)

    def test_other_boards_are_untouched(self):
        machine = Machine(self.root)
        (self.root / SLEEP.GROUP).unlink()
        self.run_phase(machine, 'pre')
        self.run_phase(machine, 'post')
        self.assertEqual([call for call in machine.calls if call[1] in ('stop', 'start') or
                          (call[0] == 'user')], [])

    def test_resume_without_a_record_does_nothing(self):
        machine = Machine(self.root)
        status, _ = self.run_phase(machine, 'post')
        self.assertEqual(status, 0)
        self.assertEqual(machine.calls, [])

    def test_failed_pre_step_still_restores_user_audio(self):
        machine = Machine(self.root)
        original = machine.run

        def failing(command, **kwargs):
            if list(command[1:3]) == ['stop', SLEEP.PROTECTION]:
                machine.calls.append(('system', 'stop', SLEEP.PROTECTION))
                return SimpleNamespace(returncode=1, stdout='', stderr='timed out')
            return original(command, **kwargs)

        machine.run = failing
        self.assertEqual(self.run_phase(machine, 'pre')[0], 0)
        machine.run = original
        status, _ = self.run_phase(machine, 'post')
        self.assertEqual(status, 1)
        self.assertEqual(machine.user_units, set(SLEEP.USER_UNITS))


if __name__ == '__main__':
    unittest.main()
