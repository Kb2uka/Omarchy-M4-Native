#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Turn on the front USB-C port (USB0) of the J713 boost-state DTB, matching the hinge-side port (USB1)."""
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

# The boost-state DTB (docs/j713-cpu-boost.md).
BASE_SHA256 = 'c4f036b819ce757ce790b01779a3f7532eacd5cb1c1097fa20286d1a9c9ec6ca'
# Port -> (DWC3 controller, its two DARTs). USB0 is the front port, USB1 the hinge-side one.
PORTS = {
    'usb0': ('/soc/usb@402280000', ('/soc/iommu@402f00000', '/soc/iommu@402f80000')),
    'usb1': ('/soc/usb@40a280000', ('/soc/iommu@40af00000', '/soc/iommu@40af80000')),
}
# The stream IDs the working USB1 uses on its DARTs (docs/j713-usb-eem-evidence.md), and
# the ones patch 0012 left on the disabled USB0.
STREAMS, OLD_USB0_STREAMS = (0, 1), (1, 14)
OKAY, DISABLED = b'okay\0', b'disabled\0'


def iommus(darts, streams, handles):
    return cells(*(value for dart, stream in zip(darts, streams) for value in (handles[dart], stream)))


def handles_by_path(nodes):
    return {path: handle for handle, path in phandles(nodes).items()}


def expected_nodes(old):
    """Return the exact tree the overlay must produce."""
    expected = {path: dict(props) for path, props in old.items()}
    controller, darts = PORTS['usb0']
    expected[controller].update(status=OKAY, iommus=iommus(darts, STREAMS, handles_by_path(old)))
    for dart in darts:
        expected[dart]['status'] = OKAY
    return expected


def check_base(old):
    board = old['/']['compatible'].split(b'\0')
    require(b'apple,j713' in board and b'apple,t8132' in board, 'Wrong board')
    handles = handles_by_path(old)
    for port, status, streams in (('usb0', DISABLED, OLD_USB0_STREAMS), ('usb1', OKAY, STREAMS)):
        controller, darts = PORTS[port]
        require(controller in old and all(dart in handles for dart in darts), 'No %s controller or DARTs' % port)
        require(old[controller].get('status') == status and all(old[dart].get('status') == status for dart in darts),
                'Unexpected %s status' % port)
        require(old[controller].get('iommus') == iommus(darts, streams, handles), 'Unexpected %s streams' % port)
    front, hinge = (old[PORTS[port][0]] for port in ('usb0', 'usb1'))
    require(front['compatible'] == hinge['compatible'] and
            sorted(front) == sorted(hinge) and
            all(front[name] != hinge[name] for name in ('power-domains', 'resets', 'phys')),
            'The two ports do not have the same controller layout')


def build(base, output, overlay, dtc='dtc', fdtoverlay='fdtoverlay'):
    require(base.is_file() and not base.is_symlink(), 'Base must be a regular file')
    original = base.read_bytes()
    require(sha(original) == BASE_SHA256, 'Unqualified boost-state base DTB')
    require(overlay.is_file() and not overlay.is_symlink(), 'Overlay must be a regular file')
    require(not output.exists() and not output.is_symlink(), 'Output must not exist')
    require(output.parent.is_dir(), 'Output directory must exist')
    old, reservations, cpu = tree(original)
    check_base(old)
    with tempfile.TemporaryDirectory(prefix='j713-usb0-', dir=output.parent) as scratch:
        compiled = Path(scratch) / 'usb0.dtbo'
        candidate = Path(scratch) / 'candidate.dtb'
        # iommus names the USB0 DARTs by phandle number, which dtc cannot resolve in an overlay.
        subprocess.run([dtc, '-W', 'no-iommus_property', '-I', 'dts', '-O', 'dtb',
                        '-o', str(compiled), str(overlay)], check=True)
        subprocess.run([fdtoverlay, '-i', str(base), '-o', str(candidate), str(compiled)], check=True)
        data = candidate.read_bytes()
        new, new_reservations, new_cpu = tree(data)
        require(new_reservations == reservations and new_cpu == cpu and new == expected_nodes(old),
                'Candidate changed data beyond the front USB port')
        require(base.read_bytes() == original, 'Base changed during build')
        with output.open('xb') as stream:
            stream.write(data)
    return {'base_sha256': sha(original), 'output_sha256': sha(data), 'output_bytes': len(data),
            'overlay_sha256': sha(overlay.read_bytes()),
            'enabled': [PORTS['usb0'][0], *PORTS['usb0'][1]],
            'usb0_streams': {'before': list(OLD_USB0_STREAMS), 'after': list(STREAMS)},
            'usb1_unchanged': True,
            'other_properties_reservations_and_boot_cpu_unchanged': True, 'kernel_changed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--overlay', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'patches/j713-usb0.dtso')
    args = parser.parse_args()
    print(json.dumps(build(args.base, args.output, args.overlay), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
