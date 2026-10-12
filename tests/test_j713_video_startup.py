# SPDX-License-Identifier: MIT
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class VideoStartupTests(unittest.TestCase):
    def wait_command(self):
        helper = Path(os.environ.get(
            'J713_VIDEO_START', ROOT / 'packaging/j713-startup/m4-video-start'))
        commands = [shlex.split(line) for line in helper.read_text().splitlines()
                    if line.strip().startswith('udevadm ')]
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][0], 'udevadm')
        return commands[0]

    def wait_for_decoder(self, *, video_initialized=True, media_initialized=True):
        argv = self.wait_command()
        with tempfile.TemporaryDirectory() as temporary:
            udevadm = Path(temporary) / 'udevadm'
            udevadm.write_text('''#!/bin/sh
case "$1" in
  settle) echo 'Unrelated USB event still pending' >&2; exit 1 ;;
  wait)
    [ "$#" = 4 ] && [ "$2" = '--timeout=10' ] &&
      [ "$3" = '/dev/video0' ] && [ "$4" = '/dev/media0' ] || exit 64
    [ "$VIDEO_INITIALIZED" = yes ] && [ "$MEDIA_INITIALIZED" = yes ] && exit 0
    echo 'Decoder device did not initialize' >&2; exit 1 ;;
  *) exit 64 ;;
esac
''')
            udevadm.chmod(0o755)
            return subprocess.run(
                ['/bin/bash', '-ec', '"$@"; echo AFTER_DEVICE_WAIT',
                 'video-wait', str(udevadm), *argv[1:]],
                text=True, capture_output=True,
                env={**os.environ,
                     'VIDEO_INITIALIZED': 'yes' if video_initialized else 'no',
                     'MEDIA_INITIALIZED': 'yes' if media_initialized else 'no'})

    def test_wait_targets_both_initialized_nodes_with_a_deadline(self):
        self.assertEqual(self.wait_command(),
                         ['udevadm', 'wait', '--timeout=10',
                          '/dev/video0', '/dev/media0'])

    def test_ready_decoder_can_start_with_unrelated_device_pending(self):
        result = self.wait_for_decoder()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('AFTER_DEVICE_WAIT', result.stdout)

    def test_uninitialized_video_still_stops_startup(self):
        result = self.wait_for_decoder(video_initialized=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('AFTER_DEVICE_WAIT', result.stdout)

    def test_uninitialized_media_still_stops_startup(self):
        result = self.wait_for_decoder(media_initialized=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('AFTER_DEVICE_WAIT', result.stdout)


if __name__ == '__main__':
    unittest.main()
