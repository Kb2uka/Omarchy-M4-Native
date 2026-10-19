#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Build the plain M4 Mac mini (J773g) boot devicetree from the J713 one (docs/j773g-boot-dtb.md)."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile

SPEC = importlib.util.spec_from_file_location(
    'ecluster_dtb', Path(__file__).resolve().with_name('build-j713-ecluster-dtb.py'))
ECLUSTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ECLUSTER)
require, sha, tree, cells, phandles = ECLUSTER.require, ECLUSTER.sha, ECLUSTER.tree, ECLUSTER.cells, ECLUSTER.phandles

# The front-USB J713 devicetree (docs/j713-front-usb.md), the newest the Air boots.
BASE_SHA256 = '352bfa74351ad7d12d993f752fe9c09d682511bca0d4a60177cda0d716d1d614'
OKAY, DISABLED = b'okay\0', b'disabled\0'
J713 = b'apple,j713\0apple,t8132\0apple,arm-platform\0'
J773G = b'apple,j773g\0apple,t8132\0apple,arm-platform\0'
MODEL = b'Apple Mac mini (M4, 2024)\0'
PCIE = '/soc/pcie@1cb0000000'
PORT0, PORT2 = PCIE + '/pci@0,0', PCIE + '/pci@2,0'
RADIOS = (PORT0 + '/wifi@0,0', PORT0 + '/bluetooth@0,1')
ETHERNET = PORT2 + '/ethernet@0,0'
FRAMEBUFFER = '/chosen/framebuffer@0'
PORT2_DART = '/soc/iommu@492000000'
PORT0_DART = '/soc/iommu@490000000'
PMGR = '/soc/power-management@380700000/'
# Base nodes the overlay names by phandle number (the base has no __symbols__).
HANDLES = {
    0x0f: PMGR + 'power-controller@1e0',   # dispext0_fe: the HDMI port's display pipe
    0x6c: PMGR + 'power-controller@10000',  # disp_cpu: the J713 framebuffer's first domain
    0x6d: PMGR + 'power-controller@3c8',    # dptx_phy
    0x67: PORT0_DART,
    0x3f: '/soc/pinctrl@39a000000',            # the AP pin controller, PCIe resets
    0x65: PMGR + 'power-controller@550',      # apcie_phy_sw, the port 0 DART's (keeps apcie_gp on)
    0x0d: '/soc/interrupt-controller@381180000',
}
# The J713 keyboard and trackpad, and its keyboard backlight: not on the mini.
J713_ONLY = ('/soc/mtp@394600000', '/soc/mbox@394608000', '/soc/iommu@394800000',
             '/soc/fifo@394b14000', '/soc/pwm@3ad044000', '/led-controller')
PERST_PIN = 165  # PCIe port 2 reset (Gravity Linux t8132.dtsi port02)
DART_IRQ = 1250  # dart-apcie2 in the J773g ADT (the #136 survey)


def expected_nodes(old):
    """Return the exact tree the overlay must produce."""
    expected = {path: dict(props) for path, props in old.items()}
    dart = max(phandles(old)) + 1
    pinctrl = cells(0x3f)
    domain = cells(0x65)
    expected['/'].update(compatible=J773G, model=MODEL)
    expected[FRAMEBUFFER]['power-domains'] = cells(0x0f, 0x6d)
    for radio in RADIOS:
        expected[radio]['brcm,board-type'] = b'apple,sakhalin\0'
    expected[PORT2_DART] = {
        'compatible': b'apple,t8132-dart\0apple,t8110-dart\0', 'reg': cells(4, 0x92000000, 0, 0x4000),
        '#iommu-cells': cells(1), 'interrupt-parent': cells(0x0d),
        'interrupts': cells(0, DART_IRQ, 4), 'power-domains': domain, 'status': OKAY, 'phandle': cells(dart)}
    expected[PCIE]['iommu-map'] = old[PCIE]['iommu-map'] + cells(0x200, dart, 1, 1)
    expected[PORT2].update(status=OKAY, **{'bus-range': cells(2, 2), 'reset-gpios': pinctrl + cells(PERST_PIN, 1)})
    expected[ETHERNET] = {'reg': cells(0x20000, 0, 0, 0, 0), 'local-mac-address': bytes.fromhex('001018000000')}
    expected['/aliases']['ethernet0'] = ETHERNET.encode() + b'\0'
    for path in J713_ONLY:
        expected[path]['status'] = DISABLED
    return expected


def check_base(old):
    require(old['/'].get('compatible') == J713, 'Wrong board')
    handles = phandles(old)
    for handle, path in HANDLES.items():
        require(handles.get(handle) == path, 'Unexpected phandle 0x%x' % handle)
    require(old[FRAMEBUFFER].get('power-domains') == cells(0x6c, 0x6d), 'Unexpected framebuffer power domains')
    require(all(old[radio].get('brcm,board-type') == b'apple,garden\0' for radio in RADIOS), 'Unexpected radios')
    require(old[PCIE].get('iommu-map') == cells(0x100, 0x67, 1, 1, 0x101, 0x67, 2, 1), 'Unexpected PCIe map')
    require(old[PORT0_DART].get('reg') == cells(4, 0x90000000, 0, 0x4000) and
            old[PORT0_DART].get('status') == OKAY, 'Unexpected port 0 DART')
    require(PORT2_DART not in old and ETHERNET not in old and 'ethernet0' not in old['/aliases'],
            'Ethernet is already wired')
    require(old[PORT2].get('status') == DISABLED and
            not {'reset-gpios', 'bus-range'} & set(old[PORT2]), 'Unexpected PCIe port 2')
    require(old[PORT0].get('reset-gpios', b'')[:4] == cells(0x3f) and
            old[PORT0_DART].get('power-domains') == cells(0x65) and
            old[PORT0_DART].get('interrupt-parent') == cells(0x0d), 'Unexpected PCIe port 0 wiring')
    for path in J713_ONLY:
        require(path in old and old[path].get('status', OKAY) == OKAY, 'Unexpected J713-only node: ' + path)


def build(base, output, overlay, dtc='dtc', fdtoverlay='fdtoverlay'):
    require(base.is_file() and not base.is_symlink(), 'Base must be a regular file')
    original = base.read_bytes()
    require(sha(original) == BASE_SHA256, 'Unqualified J713 base DTB')
    require(overlay.is_file() and not overlay.is_symlink(), 'Overlay must be a regular file')
    require(not output.exists() and not output.is_symlink(), 'Output must not exist')
    require(output.parent.is_dir(), 'Output directory must exist')
    old, reservations, cpu = tree(original)
    check_base(old)
    with tempfile.TemporaryDirectory(prefix='j773g-dtb-', dir=output.parent) as scratch:
        compiled = Path(scratch) / 'j773g.dtbo'
        candidate = Path(scratch) / 'candidate.dtb'
        # The overlay names base nodes by phandle number and adds nodes whose parents' cell
        # sizes live in the base, which dtc cannot see; build() checks the merged tree instead.
        subprocess.run([dtc, '-q', '-I', 'dts', '-O', 'dtb', '-o', str(compiled), str(overlay)], check=True)
        subprocess.run([fdtoverlay, '-i', str(base), '-o', str(candidate), str(compiled)], check=True)
        data = candidate.read_bytes()
        new, new_reservations, new_cpu = tree(data)
        require(new_reservations == reservations and new_cpu == cpu and new == expected_nodes(old),
                'Candidate changed data beyond the J773g board wiring')
        require(base.read_bytes() == original, 'Base changed during build')
        with output.open('xb') as stream:
            stream.write(data)
    return {'base_sha256': sha(original), 'output_sha256': sha(data), 'output_bytes': len(data),
            'overlay_sha256': sha(overlay.read_bytes()), 'board': 'apple,j773g',
            'framebuffer_power_domains': [HANDLES[0x0f], HANDLES[0x6d]],
            'radios': 'apple,sakhalin', 'ethernet': ETHERNET, 'added_dart': PORT2_DART,
            'disabled': list(J713_ONLY),
            'other_properties_reservations_and_boot_cpu_unchanged': True, 'kernel_changed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--overlay', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'patches/j773g-board.dtso')
    args = parser.parse_args()
    print(json.dumps(build(args.base, args.output, args.overlay), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
