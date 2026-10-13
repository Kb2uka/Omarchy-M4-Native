"""Local GStreamer audio plugin helper, with a fake package manager and build."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'tools/m4-webkit-audio-plugins.py'
SPEC = importlib.util.spec_from_file_location('webkit_audio_plugins', SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def tarball(version):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:xz') as stream:
        data = b'project("gst-plugins-good")\n'
        info = tarfile.TarInfo(f'gst-plugins-good-{version}/meson.build')
        info.size = len(data)
        stream.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class FakeSystem:
    def __init__(self, packages):
        self.packages = packages
        self.commands = []
        self.missing_elements = set()

    def __call__(self, command, **options):
        self.commands.append(command)
        name = Path(command[0]).name
        if name == 'pacman':
            version = self.packages.get(command[2])
            if version is None:
                return subprocess.CompletedProcess(command, 1, '', 'error: package not found\n')
            return subprocess.CompletedProcess(command, 0, f'{command[2]} {version}\n', '')
        if name == 'ninja':
            output = Path(command[2])
            for plugin, subdir in MODULE.PLUGINS.items():
                (output / subdir).mkdir(parents=True, exist_ok=True)
                (output / subdir / plugin).write_bytes(b'ELF ' + plugin.encode())
        if name == 'gst-inspect-1.0':
            return subprocess.CompletedProcess(command, int(command[2] in self.missing_elements))
        return subprocess.CompletedProcess(command, 0)


class WebkitAudioPlugins(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        self.env = {'HOME': str(self.home)}
        self.plugins = self.home / '.local/share/gstreamer-1.0/plugins'
        self.source = tarball('1.28.7')
        patcher = patch.dict(MODULE.SOURCES, {'1.28.7': hashlib.sha256(self.source).hexdigest()})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.downloads = []

    def download(self, url):
        self.downloads.append(url)
        return self.source

    def run_install(self, packages, apply=True):
        system = FakeSystem(packages)
        return system, MODULE.install(self.env, run=system, download=self.download, apply=apply)

    def test_inspection_writes_nothing(self):
        system, report = self.run_install({'gstreamer': '1.28.7-2'}, apply=False)
        self.assertEqual(report['status'], 'build available')
        self.assertFalse(self.home.joinpath('.local').exists())
        self.assertEqual(self.downloads, [])

    def test_installs_only_autodetect_and_pulse_for_the_user(self):
        system, report = self.run_install({'gstreamer': '1.28.7-2'})
        self.assertEqual(report['status'], 'installed; restart apps to get sound')
        self.assertEqual(sorted(p.name for p in self.plugins.iterdir()),
                         ['.m4-webkit-audio.json', 'libgstautodetect.so', 'libgstpulseaudio.so'])
        self.assertEqual(self.downloads, [MODULE.SOURCE_URL.format('1.28.7')])
        meson = next(c for c in system.commands if Path(c[0]).name == 'meson')
        self.assertIn('-Dauto_features=disabled', meson)
        self.assertEqual(sorted(o for o in meson if o.endswith('=enabled')),
                         ['-Dautodetect=enabled', '-Dpulse=enabled'])
        pip = next(c for c in system.commands if Path(c[0]).name == 'pip')
        self.assertIn('--require-hashes', pip)
        self.assertIn(['gst-inspect-1.0', '--exists', 'autoaudiosink'], system.commands)
        self.assertIn(['gst-inspect-1.0', '--exists', 'pulsesink'], system.commands)
        receipt = json.loads((self.plugins / '.m4-webkit-audio.json').read_text())
        self.assertEqual(receipt['gstreamer'], '1.28.7-2')
        self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in self.plugins.iterdir()))

    def test_second_run_is_a_no_op(self):
        self.run_install({'gstreamer': '1.28.7-2'})
        system, report = self.run_install({'gstreamer': '1.28.7-2'})
        self.assertEqual(report['status'], 'already installed')
        self.assertEqual(len(self.downloads), 1)

    def test_system_package_makes_the_build_unnecessary(self):
        system, report = self.run_install({'gstreamer': '1.28.8-1', 'gst-plugins-good': '1.28.8-1'})
        self.assertTrue(report['status'].startswith('not needed'))
        self.assertEqual(self.downloads, [])
        self.assertNotIn('note', report)

    def test_system_package_after_local_install_asks_for_removal(self):
        self.run_install({'gstreamer': '1.28.7-2'})
        system, report = self.run_install({'gstreamer': '1.28.8-1', 'gst-plugins-good': '1.28.8-1'})
        self.assertIn('--remove', report['note'])

    def test_unpinned_gstreamer_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, 'No pinned source for GStreamer 1.30.0'):
            self.run_install({'gstreamer': '1.30.0-1'})
        self.assertEqual(self.downloads, [])

    def test_checksum_mismatch_installs_nothing(self):
        self.source = tarball('1.28.7') + b'tampered'
        with self.assertRaisesRegex(RuntimeError, 'checksum mismatch'):
            self.run_install({'gstreamer': '1.28.7-2'})
        self.assertFalse(self.plugins.exists())

    def test_foreign_plugin_file_is_left_alone(self):
        self.plugins.mkdir(parents=True)
        (self.plugins / 'libgstpulseaudio.so').write_bytes(b'someone else')
        with self.assertRaisesRegex(RuntimeError, 'does not own'):
            self.run_install({'gstreamer': '1.28.7-2'})
        self.assertEqual((self.plugins / 'libgstpulseaudio.so').read_bytes(), b'someone else')

    def test_unloadable_plugin_fails_the_install(self):
        system = FakeSystem({'gstreamer': '1.28.7-2'})
        system.missing_elements.add('pulsesink')
        with self.assertRaisesRegex(RuntimeError, 'cannot load pulsesink'):
            MODULE.install(self.env, run=system, download=self.download, apply=True)

    def test_remove_deletes_only_unchanged_plugins(self):
        self.run_install({'gstreamer': '1.28.7-2'})
        (self.plugins / 'libgstautodetect.so').write_bytes(b'edited')
        with self.assertRaisesRegex(RuntimeError, 'changed since install'):
            MODULE.remove(self.env)
        self.assertTrue((self.plugins / 'libgstpulseaudio.so').exists())
        (self.plugins / 'libgstautodetect.so').write_bytes(b'ELF libgstautodetect.so')
        self.assertEqual(MODULE.remove(self.env)['status'], 'removed')
        self.assertEqual(list(self.plugins.iterdir()), [])
        self.assertEqual(MODULE.remove(self.env)['status'], 'nothing to remove')

    def test_xdg_data_home_is_respected(self):
        self.env['XDG_DATA_HOME'] = str(self.home / 'data')
        self.run_install({'gstreamer': '1.28.7-2'})
        self.assertTrue((self.home / 'data/gstreamer-1.0/plugins/libgstautodetect.so').is_file())

    def test_root_is_refused(self):
        with patch.object(MODULE.os, 'getuid', return_value=0):
            with self.assertRaisesRegex(RuntimeError, 'without sudo'):
                self.run_install({'gstreamer': '1.28.7-2'})

    def test_image_manifest_ships_the_plugins(self):
        manifest = SOURCE.with_name('m4-desktop.packages').read_text().split()
        self.assertIn('gst-plugins-good', manifest)


if __name__ == '__main__':
    unittest.main()
