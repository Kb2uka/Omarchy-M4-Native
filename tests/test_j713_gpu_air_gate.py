# SPDX-License-Identifier: MIT
"""Air GPU qualification gate in gpu/j713/asahi/g16.rs (issue #57).

The pure policy functions are compiled and run on the host with rustc; the
kernel call site is checked against the source text, since it cannot be built
without the kernel tree.
"""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get('J713_GPU_G16', ROOT / 'gpu/j713/asahi/g16.rs'))

CASES = r'''
const FW: &[u8] = b"mBoot-18000.120.36\0";
const BOARD: &[u8] = b"apple,j713\0apple,t8132\0apple,arm-platform\0";

fn gate(mask: u32, groups: usize, mhz: u32, mw: u32) -> Option<&'static str> {
    air_platform_rejection(mask, groups, mhz, mw)
}

// Derived limits of three measured Airs: 1000, 995 and 980 mV top states.
#[test] fn measured_airs_accepted() {
    for mw in [24800, 24676, 24304] { assert_eq!(gate(0x3ff, 10, 1470, mw), None, "{}", mw); }
}
// The measured eight-core Air: mask 0x3bd, ten groups, 24155 mW.
#[test] fn measured_eight_core_air_accepted() {
    assert_eq!(gate(0x3bd, 10, 1470, 24155), None);
    for mw in [24800, 24304] { assert_eq!(gate(0x3bd, 10, 1470, mw), None, "{}", mw); }
}
// Any two of the ten positions may be fused off: all 45 eight-core masks.
#[test] fn every_eight_core_mask_accepted() {
    let masks: Vec<u32> = (0..0x400u32).filter(|m| m.count_ones() == 8).collect();
    assert_eq!(masks.len(), 45);
    for mask in masks { assert_eq!(gate(mask, 10, 1470, 24155), None, "{:#x}", mask); }
}
// Every other mask: zero, nine cores, fewer than eight, or a bit past the ten positions.
#[test] fn other_core_masks_rejected() {
    let other = (0..0x800u32).filter(|m| !(m & !0x3ff == 0 && matches!(m.count_ones(), 8 | 10)));
    let mut count = 0;
    for mask in other.chain([0x3bd | 1 << 31, 0xffff_ffff]) {
        assert_eq!(gate(mask, 10, 1470, 24155), Some("core mask"), "{:#x}", mask);
        count += 1;
    }
    assert_eq!(count, 0x800 - 46 + 2);
    for mask in [0, 0x3fe, 0x3bf, 0x2bd, 0x7f, 0x7ff, 0x4ff] {
        assert_eq!(gate(mask, 10, 1470, 24155), Some("core mask"), "{:#x}", mask);
    }
}
#[test] fn power_above_or_at_zero_rejected() {
    for mask in [0x3ff, 0x3bd, 0xff, 0x3fc] {
        for mw in [0, 24801, 30071, 37000, u32::MAX] {
            assert_eq!(gate(mask, 10, 1470, mw), Some("maximum power"), "{:#x} {}", mask, mw);
        }
    }
}
#[test] fn other_platform_checks_unchanged() {
    // The mask is checked first, whatever the other values.
    assert_eq!(gate(0x3fe, 11, 1469, 0), Some("core mask"));
    assert_eq!(gate(0x3ff, 11, 1470, 24800), Some("performance groups"));
    assert_eq!(gate(0x3ff, 9, 1470, 24800), Some("performance groups"));
    assert_eq!(gate(0x3bd, 8, 1470, 24155), Some("performance groups"));
    assert_eq!(gate(0xff, 11, 1470, 24155), Some("performance groups"));
    assert_eq!(gate(0x3bd, 10, 1469, 24155), Some("maximum frequency"));
    assert_eq!(gate(0x3fc, 10, 1471, 24155), Some("maximum frequency"));
    assert_eq!(gate(0x3ff, 10, 1469, 24800), Some("maximum frequency"));
    assert_eq!(gate(0x3ff, 10, 1471, 24800), Some("maximum frequency"));
}
#[test] fn firmware_policy_unchanged() {
    assert!(air_bootstrap_allowed(FW, BOARD, 1, 0));
    for mode in [0, 2, u32::MAX] { assert!(!air_bootstrap_allowed(FW, BOARD, mode, 0)); }
    for probe in [1, 2, u32::MAX] { assert!(!air_bootstrap_allowed(FW, BOARD, 1, probe)); }
    for b in [b"apple,j773\0apple,t8132\0".as_slice(), b"apple,j713\0", b"apple,t8132\0", b""] {
        assert!(!air_bootstrap_allowed(FW, b, 1, 0));
    }
    for f in [b"mBoot-18000.161.10\0".as_slice(), b"mBoot-18000.120.37\0", b"mBoot-18000.120.36", b""] {
        assert!(!air_bootstrap_allowed(f, BOARD, 1, 0));
    }
    assert!(!firmware_allowed(FW, BOARD, false));
    assert!(firmware_allowed(b"mBoot-18000.161.10\0", BOARD, false));
}
'''


def item(source, start):
    begin = source.index(start)
    end = source.index('\n}\n', begin) + 3 if start.startswith('fn ') else source.index(';\n', begin) + 2
    return source[begin:end]


def bootstrap_new(source):
    begin = source.index('impl Bootstrap {')
    return source[begin:source.index('use core::mem::ManuallyDrop;', begin)]


class AirGpuGateTests(unittest.TestCase):
    def test_policy_functions(self):
        rustc = shutil.which('rustc')
        if rustc is None:
            if os.environ.get('CI'):
                self.fail('rustc is required in CI')
            self.skipTest('rustc not installed')
        source = SOURCE.read_text()
        parts = [item(source, s) for s in ('fn firmware_allowed(', 'fn air_bootstrap_allowed(',
                                           'const AIR_MAX_POWER_MW', 'fn air_core_mask_allowed(',
                                           'fn air_platform_rejection(')]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            (path / 'gate.rs').write_text('\n'.join(parts) + CASES)
            subprocess.run([rustc, '--edition=2021', '--test', str(path / 'gate.rs'),
                            '-o', str(path / 'gate')], check=True, capture_output=True)
            result = subprocess.run([str(path / 'gate')], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_bootstrap_applies_gate_and_logs_rejections(self):
        body = bootstrap_new(SOURCE.read_text())
        self.assertNotIn('24800', body)
        gate = re.search(r'if qualification == 1 \{\s*let max_mhz = platform\.maximum_frequency_mhz\(\);'
                         r'\s*if let Some\(field\) = air_platform_rejection\(platform\.core_mask, '
                         r'platform\.freq_a\.len\(\),\s*max_mhz, platform\.maximum_power\) \{'
                         r'\s*dev_err!\((?:.|\n)*?\);\s*return Err\(EINVAL\);', body)
        self.assertIsNotNone(gate, 'Air platform gate must reject with a log line')
        self.assertLess(body.index('Platform::new'), gate.start())
        refusal = re.search(r'air_bootstrap_allowed\((?:.|\n)*?\{\s*dev_err!\((?:.|\n)*?\);'
                            r'\s*return Err\(ENOTSUPP\);', body)
        self.assertIsNotNone(refusal, 'firmware or board refusal must log')


if __name__ == '__main__':
    unittest.main()
