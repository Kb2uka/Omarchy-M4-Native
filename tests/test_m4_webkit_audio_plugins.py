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
        self.session = self.home / '.config/environment.d/60-m4-webkit-audio.conf'
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
        self.assertFalse(self.home.joinpath('.config').exists())
        self.assertEqual(self.downloads, [])

    def test_installs_only_autodetect_and_pulse_for_the_user(self):
        system, report = self.run_install({'gstreamer': '1.28.7-2'})
        self.assertEqual(report['status'], 'installed; ' + MODULE.RESTART)
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

    def test_channel_package_makes_the_build_unnecessary(self):
        system, report = self.run_install({'gstreamer': '1.28.7-2', 'omarchy-m4-gst-audio': '1.28.7-1'})
        self.assertEqual(report['status'], 'not needed: omarchy-m4-gst-audio 1.28.7-1 is installed')
        self.assertEqual(self.downloads, [])
        self.assertFalse(self.plugins.exists())

    def test_channel_package_after_local_install_asks_for_removal(self):
        self.run_install({'gstreamer': '1.28.7-2'})
        report = self.run_install({'gstreamer': '1.28.7-2', 'omarchy-m4-gst-audio': '1.28.7-1'})[1]
        self.assertIn('--remove', report['note'])

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

    def test_unloadable_plugin_rolls_the_install_back(self):
        system = FakeSystem({'gstreamer': '1.28.7-2'})
        system.missing_elements.add('pulsesink')
        with self.assertRaisesRegex(RuntimeError, 'cannot load pulsesink; the plugins were removed again'):
            MODULE.install(self.env, run=system, download=self.download, apply=True)
        self.assertEqual(list(self.plugins.iterdir()), [])

    def test_installed_plugins_are_revalidated(self):
        self.run_install({'gstreamer': '1.28.7-2'})
        system = FakeSystem({'gstreamer': '1.28.7-2'})
        system.missing_elements.add('autoaudiosink')
        with self.assertRaisesRegex(RuntimeError, 'cannot load autoaudiosink'):
            MODULE.install(self.env, run=system, download=self.download, apply=True)

    def test_interrupted_install_can_be_removed(self):
        self.run_install({'gstreamer': '1.28.7-2'})
        (self.plugins / 'libgstpulseaudio.so').unlink()
        (self.plugins / 'libgstpulseaudio.so.new').write_bytes(b'partial')
        with self.assertRaisesRegex(RuntimeError, 'run --remove first'):
            self.run_install({'gstreamer': '1.28.7-2'})
        self.assertEqual(MODULE.remove(self.env)['status'], 'removed')
        self.assertEqual(list(self.plugins.iterdir()), [])

    def test_interrupted_receipt_write_is_cleared_by_remove(self):
        self.plugins.mkdir(parents=True)
        (self.plugins / '.m4-webkit-audio.json.new').write_bytes(b'{"gstr')
        with self.assertRaisesRegex(RuntimeError, 'run --remove first'):
            self.run_install({'gstreamer': '1.28.7-2'})
        self.assertEqual(MODULE.remove(self.env)['status'], 'nothing to remove')
        self.assertEqual(list(self.plugins.iterdir()), [])
        self.assertEqual(self.run_install({'gstreamer': '1.28.7-2'})[1]['status'],
                         'installed; ' + MODULE.RESTART)
        self.assertEqual(len(self.downloads), 1)

    def test_stale_temporary_link_is_cleared_without_touching_its_target(self):
        target = self.home / 'keep.txt'
        target.write_text('keep')
        self.plugins.mkdir(parents=True)
        (self.plugins / 'libgstpulseaudio.so.new').symlink_to(target)
        with self.assertRaisesRegex(RuntimeError, 'run --remove first'):
            self.run_install({'gstreamer': '1.28.7-2'})
        self.assertEqual(self.downloads, [])
        MODULE.remove(self.env)
        self.assertEqual((list(self.plugins.iterdir()), target.read_text()), ([], 'keep'))

    def test_tampered_plugin_is_not_reported_installed(self):
        self.run_install({'gstreamer': '1.28.7-2'})
        (self.plugins / 'libgstautodetect.so').write_bytes(b'edited')
        with self.assertRaisesRegex(RuntimeError, 'run --remove first'):
            self.run_install({'gstreamer': '1.28.7-2'})
        self.assertEqual((self.plugins / 'libgstautodetect.so').read_bytes(), b'edited')

    def test_receipt_is_written_before_any_plugin(self):
        order = []
        real = MODULE.write
        with patch.object(MODULE, 'write', side_effect=lambda path, data: (order.append(path.name), real(path, data))):
            self.run_install({'gstreamer': '1.28.7-2'})
        self.assertEqual(order[0], '.m4-webkit-audio.json')

    def test_missing_home_is_a_clean_error(self):
        with patch.dict(MODULE.os.environ, {}, clear=True), patch('sys.stderr'):
            self.assertEqual(MODULE.main([]), 1)

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

    def test_session_names_the_plugin_dir_for_the_webkit_sandbox(self):
        self.run_install({'gstreamer': '1.28.7-2'})
        lines = [l for l in self.session.read_text().splitlines() if not l.startswith('#')]
        self.assertEqual(lines, ['GST_PLUGIN_PATH=' + str(self.plugins) +
                                 '${GST_PLUGIN_PATH:+:$GST_PLUGIN_PATH}'])
        self.assertEqual(self.session.stat().st_mode & 0o777, 0o600)

    def test_existing_install_gets_the_session_setting_on_apply(self):
        self.run_install({'gstreamer': '1.28.7-2'})
        self.session.unlink()  # an install made before the session setting existed
        report = self.run_install({'gstreamer': '1.28.7-2'}, apply=False)[1]
        self.assertEqual(report['status'], 'plugins installed; session setting missing, run --apply')
        self.assertFalse(self.session.exists())
        report = self.run_install({'gstreamer': '1.28.7-2'})[1]
        self.assertEqual((report['status'], report['applied']),
                         ('session setting added; ' + MODULE.RESTART, True))
        self.assertTrue(self.session.is_file())
        self.assertEqual(len(self.downloads), 1)
        self.assertEqual(self.run_install({'gstreamer': '1.28.7-2'})[1]['status'], 'already installed')

    def test_foreign_session_file_is_left_alone(self):
        self.session.parent.mkdir(parents=True)
        self.session.write_text('GST_PLUGIN_PATH=/opt/mine\n')
        with self.assertRaisesRegex(RuntimeError, 'session file this tool does not own'):
            self.run_install({'gstreamer': '1.28.7-2'})
        self.assertEqual(self.downloads, [])
        self.assertFalse(self.plugins.exists())
        self.assertEqual(MODULE.remove(self.env)['status'], 'nothing to remove')
        self.assertEqual(self.session.read_text(), 'GST_PLUGIN_PATH=/opt/mine\n')

    def test_foreign_session_file_blocks_completing_an_existing_install(self):
        self.run_install({'gstreamer': '1.28.7-2'})
        self.session.write_text('GST_PLUGIN_PATH=/opt/mine\n')
        with self.assertRaisesRegex(RuntimeError, 'session file this tool does not own'):
            self.run_install({'gstreamer': '1.28.7-2'})
        MODULE.remove(self.env)
        self.assertEqual(self.session.read_text(), 'GST_PLUGIN_PATH=/opt/mine\n')
        self.assertEqual(list(self.plugins.iterdir()), [])

    def test_remove_deletes_the_session_setting(self):
        self.run_install({'gstreamer': '1.28.7-2'})
        self.assertEqual(MODULE.remove(self.env)['status'], 'removed')
        self.assertFalse(self.session.exists())

    def test_remove_clears_only_the_session_setting_when_plugins_are_gone(self):
        self.run_install({'gstreamer': '1.28.7-2'})
        for path in self.plugins.iterdir():
            path.unlink()
        self.assertEqual(MODULE.remove(self.env)['status'], 'removed')
        self.assertFalse(self.session.exists())

    def test_refused_remove_keeps_the_session_setting(self):
        self.run_install({'gstreamer': '1.28.7-2'})
        (self.plugins / 'libgstautodetect.so').write_bytes(b'edited')
        with self.assertRaisesRegex(RuntimeError, 'changed since install'):
            MODULE.remove(self.env)
        self.assertTrue(self.session.is_file())

    def test_unloadable_plugin_leaves_no_session_setting(self):
        system = FakeSystem({'gstreamer': '1.28.7-2'})
        system.missing_elements.add('pulsesink')
        with self.assertRaises(RuntimeError):
            MODULE.install(self.env, run=system, download=self.download, apply=True)
        self.assertFalse(self.session.exists())

    def test_interrupted_session_write_is_finished_by_apply(self):
        self.run_install({'gstreamer': '1.28.7-2'})
        self.session.unlink()
        temp = self.session.with_name(self.session.name + '.new')
        temp.write_text('GST_PL')
        report = self.run_install({'gstreamer': '1.28.7-2'}, apply=False)[1]
        self.assertEqual(report['status'], 'plugins installed; session setting missing, run --apply')
        self.assertTrue(temp.exists())
        report = self.run_install({'gstreamer': '1.28.7-2'})[1]
        self.assertEqual(report['status'], 'session setting added; ' + MODULE.RESTART)
        self.assertEqual(list(self.session.parent.iterdir()), [self.session])
        self.assertEqual(len(self.downloads), 1)

    def test_interrupted_session_write_is_cleared_by_remove(self):
        self.run_install({'gstreamer': '1.28.7-2'})
        self.session.unlink()
        self.session.with_name(self.session.name + '.new').write_text('GST_PL')
        MODULE.remove(self.env)
        self.assertEqual(list(self.session.parent.iterdir()), [])

    def test_dangling_session_temp_link_is_cleared_by_apply(self):
        self.run_install({'gstreamer': '1.28.7-2'})
        self.session.unlink()
        temp = self.session.with_name(self.session.name + '.new')
        temp.symlink_to(self.home / 'missing')
        self.assertEqual(self.run_install({'gstreamer': '1.28.7-2'})[1]['status'],
                         'session setting added; ' + MODULE.RESTART)
        self.assertFalse(temp.is_symlink())
        self.assertFalse((self.home / 'missing').exists())

    def test_remove_works_after_the_data_dir_moves_somewhere_unusable(self):
        self.run_install({'gstreamer': '1.28.7-2'})
        self.env['XDG_DATA_HOME'] = str(self.home / 'a:b')
        self.assertEqual(MODULE.remove(self.env)['status'], 'nothing to remove')
        self.assertTrue(self.session.is_file())  # names the old directory, so not ours here

    def test_session_link_is_not_ours_even_with_our_text(self):
        self.run_install({'gstreamer': '1.28.7-2'})
        target = self.home / 'elsewhere.conf'
        target.write_text(self.session.read_text())
        self.session.unlink()
        self.session.symlink_to(target)
        with self.assertRaisesRegex(RuntimeError, 'session file this tool does not own'):
            self.run_install({'gstreamer': '1.28.7-2'})
        MODULE.remove(self.env)
        self.assertTrue(self.session.is_symlink())
        self.assertTrue(target.is_file())

    def test_system_package_after_session_only_install_asks_for_removal(self):
        self.run_install({'gstreamer': '1.28.7-2'})
        for path in self.plugins.iterdir():
            path.unlink()
        report = self.run_install({'gstreamer': '1.28.8-1', 'gst-plugins-good': '1.28.8-1'})[1]
        self.assertIn('--remove', report['note'])

    def test_xdg_config_home_is_respected(self):
        self.env['XDG_CONFIG_HOME'] = str(self.home / 'config')
        self.run_install({'gstreamer': '1.28.7-2'})
        self.assertTrue((self.home / 'config/environment.d/60-m4-webkit-audio.conf').is_file())
        self.assertFalse(self.session.exists())

    def test_plugin_dir_environment_d_cannot_hold_is_refused(self):
        for data in ('my data', 'a:b', 'cost$x'):
            with self.subTest(data=data):
                self.env['XDG_DATA_HOME'] = str(self.home / data)
                with self.assertRaisesRegex(RuntimeError, 'environment.d cannot hold'):
                    self.run_install({'gstreamer': '1.28.7-2'})
                self.assertEqual(self.downloads, [])
                self.assertFalse(self.session.exists())

    def test_image_manifest_ships_the_plugins(self):
        manifest = SOURCE.with_name('m4-desktop.packages').read_text().split()
        self.assertIn('gst-plugins-good', manifest)


if __name__ == '__main__':
    unittest.main()
