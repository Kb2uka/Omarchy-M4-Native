#!/usr/bin/python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Launch shipped browsers with M4 hardware decoding and retain selected profiles."""
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys

PREFIX = Path('/opt/m4-video/20261011')
SITE_EXTENSION = PREFIX / 'extensions/youtube-hardware-codecs'
BINARY = '/opt/brave-bin/brave'
CHROMIUM_BINARY = '/usr/bin/chromium'
CHROMIUM_SYSTEM_CONFIG = Path('/etc/chromium-flags.conf')
HW_FEATURES = ('VaapiIgnoreDriverChecks', 'AcceleratedVideoDecoder',
               'AcceleratedVideoDecodeLinuxGL')
VA_KEYS = ('LIBVA_DRIVER_NAME', 'LIBVA_DRIVERS_PATH',
           'LIBVA_V4L2_REQUEST_MEDIA_PATH', 'LIBVA_V4L2_REQUEST_COPY_RENDER_NODE')
READY = Path('/run/m4-video/ready')
BOOT_ID = Path('/proc/sys/kernel/random/boot_id')
RUNTIME_STATUS = Path('/sys/bus/platform/devices/3a1080000.avd/power/runtime_status')
BOOTSTRAP = Path('/usr/lib/omarchy/bootstrap-brave-widevine.py')
HELP = '''Usage: {name} [browser options] [URL ...]
{profile}
Hardware decoding is requested when the private stack and devices are available;
otherwise software decoding remains available.
  --incognito     Open a private window in this profile
  --help          Show this help without launching the browser
'''


def feature_name(value):
    return value.split('<', 1)[0].split(':', 1)[0]


def flags(config, arguments, profile, *, chromium=False):
    values = []
    for path in ([CHROMIUM_SYSTEM_CONFIG, config] if chromium else [config]):
        if path.exists():
            if chromium:
                values += shlex.split(path.read_text(), comments=True)
            else:
                values += [line.strip() for line in path.read_text().splitlines()
                           if line.strip() and not line.lstrip().startswith('#')]
    values += arguments
    ordinary, enabled, disabled, extensions = [], {}, {}, []
    i = 0
    while i < len(values):
        value = values[i]
        i += 1
        if value == '--':
            ordinary += values[i - 1:]
            break
        key, separator, argument = value.partition('=')
        if key in ('--enable-features', '--disable-features', '--user-data-dir',
                   '--hardware-video-device-path', '--render-node-override', '--load-extension'):
            if not separator:
                if i == len(values):
                    raise ValueError('missing value for ' + key)
                argument = values[i]
                i += 1
            if key == '--load-extension':
                for path in argument.split(','):
                    if path and path not in extensions:
                        extensions.append(path)
            elif key == '--user-data-dir':
                if profile is None:
                    ordinary.append('--user-data-dir=' + argument)
                elif argument != str(profile):
                    raise ValueError('this launcher requires its dedicated profile')
            elif key in ('--enable-features', '--disable-features'):
                target = enabled if key == '--enable-features' else disabled
                for feature in argument.split(','):
                    if feature:
                        target[feature_name(feature)] = feature
            # The two device selectors are supplied once by this launcher.
            continue
        if key == '--disable-software-video-decoders':
            raise ValueError('software video decoders must remain available')
        ordinary.append(value)
    return ordinary, enabled, disabled, extensions


def service_ready():
    try:
        ready_stat = READY.stat()
        if (READY.is_symlink() or not stat.S_ISREG(ready_stat.st_mode)
                or ready_stat.st_uid != 0 or ready_stat.st_mode & 0o022
                or READY.read_text().strip() != BOOT_ID.read_text().strip()
                or RUNTIME_STATUS.read_text().strip() not in ('active', 'suspended')):
            return False
    except OSError:
        return False
    return True


def hardware_available():
    if not service_ready():
        return False
    files = [PREFIX / 'mesa/lib' / name
             for name in ('libEGL.so.1', 'libGLESv2.so.2', 'libgbm.so.1',
                          'libgallium-26.3.0-devel.so', 'gbm/dri_gbm.so')]
    files.append(PREFIX / 'vaapi/v4l2_request_drv_video.so')
    files += [SITE_EXTENSION / name for name in ('manifest.json', 'codec-preference.js')]
    if not all(path.is_file() and os.access(path, os.R_OK) for path in files):
        return False
    for name in ('/dev/video0', '/dev/media0', '/dev/dri/renderD128'):
        try:
            if not stat.S_ISCHR(os.stat(name).st_mode) or not os.access(name, os.R_OK | os.W_OK):
                return False
        except OSError:
            return False
    return True


def launch(arguments, environment, *, normal_profile=False, browser_name='brave'):
    env = dict(environment)
    home = Path(env['HOME'])
    profile = None if normal_profile else home / '.config/BraveSoftware/Brave-Browser-M4'
    chromium = browser_name == 'chromium'
    config = Path(env.get('XDG_CONFIG_HOME') or home / '.config') / (browser_name + '-flags.conf')
    ordinary, enabled, disabled, extensions = flags(config, arguments, profile, chromium=chromium)
    requested_software = any(arg.partition('=')[0] in
                             ('--disable-accelerated-video-decode', '--disable-gpu')
                             for arg in ordinary)
    requested_software |= any(name in disabled for name in HW_FEATURES)
    stack_ready = hardware_available()
    # The shipped ARM Chromium build has V4L2 but no VA-API backend. Its direct
    # decoder is not qualified with AVD; retain GPU rendering and CPU decoding.
    hardware = not chromium and not requested_software and stack_ready
    extensions = [path for path in extensions if path.rstrip('/') != str(SITE_EXTENSION)]
    if hardware:
        extensions.append(str(SITE_EXTENSION))
    for key in VA_KEYS:
        env.pop(key, None)
    disabled['OnBeginFrameThrottleVideo'] = 'OnBeginFrameThrottleVideo'
    switches = [] if normal_profile else ['--user-data-dir=' + str(profile)]
    if stack_ready and '--disable-gpu' not in ordinary:
        env.update({
            'LD_LIBRARY_PATH': str(PREFIX / 'mesa/lib'),
            'GBM_BACKENDS_PATH': str(PREFIX / 'mesa/lib/gbm'),
            'LD_BIND_NOW': '1', 'ASAHI_MESA_DEBUG': 'nocompress',
        })
    if hardware:
        for name in HW_FEATURES:
            enabled.setdefault(name, name)
        env.update({
            'LIBVA_DRIVER_NAME': 'v4l2_request',
            'LIBVA_DRIVERS_PATH': str(PREFIX / 'vaapi'),
            'LIBVA_V4L2_REQUEST_MEDIA_PATH': '/dev/media0',
            'LIBVA_V4L2_REQUEST_COPY_RENDER_NODE': '/dev/dri/renderD128',
        })
        switches += ['--hardware-video-device-path=/dev/dri/renderD128',
                     '--render-node-override=/dev/dri/renderD128']
    else:
        switches.append('--disable-accelerated-video-decode')
        for name in HW_FEATURES:
            enabled.pop(name, None)
    for name in disabled:
        enabled.pop(name, None)
    if enabled:
        switches.append('--enable-features=' + ','.join(enabled.values()))
    if disabled:
        switches.append('--disable-features=' + ','.join(disabled.values()))
    if extensions:
        switches.append('--load-extension=' + ','.join(extensions))
    if not chromium:
        env['CHROME_VERSION_EXTRA'] = 'stable'
    env['CHROME_DESKTOP'] = ('chromium.desktop' if chromium else
                           'brave-browser.desktop' if normal_profile else 'brave-m4-hardware.desktop')
    return [CHROMIUM_BINARY if chromium else BINARY] + switches + ordinary, env, hardware


def owner_media(argv, environment, normal_profile, browser_name='brave'):
    if any(arg in ('--version', '--product-version') for arg in argv):
        return
    if normal_profile and (any(arg.startswith('--user-data-dir=') for arg in argv)
                           or environment.get('CHROME_USER_DATA_DIR')
                           or environment.get('CHROME_CONFIG_HOME')
                           or environment.get('BRAVE_USER_DATA_DIR')):
        return
    profile = ('Chromium' if browser_name == 'chromium' else
               'Brave-Browser' if normal_profile else 'Brave-Browser-M4')
    marker = Path(environment['HOME']) / '.config/omarchy/pending' / ('widevine-' + profile + '.json')
    if not BOOTSTRAP.is_file() or not (marker.exists() or marker.is_symlink()):
        return
    try:
        # Only a prior owner request can enable the component; failure keeps it pending.
        subprocess.run(['/usr/bin/python3', str(BOOTSTRAP), '--profile', profile],
                       env=environment, timeout=22, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        print('Protected-video setup remains pending: ' + str(error), file=sys.stderr)


def main(arguments=None):
    arguments = sys.argv[1:] if arguments is None else arguments
    name = Path(sys.argv[0]).name
    browser_name = 'chromium' if name == 'chromium' else 'brave'
    normal_profile = name in ('brave', 'chromium')
    if '--help' in arguments or '-h' in arguments:
        print(HELP.format(name=browser_name if normal_profile else 'brave-m4-hardware',
                          profile='Keeps the normal browser profile and explicit profile options.'
                          if normal_profile else 'Uses a separate persistent Brave-Browser-M4 profile.'), end='')
        return 0
    try:
        argv, env, hardware = launch(arguments, os.environ, normal_profile=normal_profile,
                                     browser_name=browser_name)
        owner_media(argv, os.environ, normal_profile, browser_name)
        if not hardware:
            print(name + ': using software video decoding', file=sys.stderr)
        os.execve(argv[0], argv, env)
    except (OSError, ValueError, KeyError) as error:
        print(name + ': ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
