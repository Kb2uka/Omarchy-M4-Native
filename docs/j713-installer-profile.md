# Experimental J713 installer profile

Checkpoint: October 9, 2026. Maintainer: KB2UKA.

The M4 Air's macOS container has been reduced to 110 GB, leaving approximately
384.4 GB unallocated. macOS remains bootable. The existing iSC and System
Recovery partitions remain in place. No Linux partition or permanent Omarchy
boot entry has been installed.

The [profile patch](../patches/installer/j713-recovery-radio-profile.patch)
applies to [Gravity Linux installer](https://github.com/GravityLinux/installer)
revision `6420623289fe7544cb8589b658355e57f0290fcc`. It adds an experimental
`j713ap` profile gated by the installer's existing expert mode. It does not
enable other Air models or M4 Pro/Max systems. The Mini's 26.6.2 requirement
and the shared T8132 minimum remain unchanged; only J713 selects 26.5.1.

This is a recovery and firmware packaging checkpoint, not an installable Omarchy
distribution. The complete boot chain, SSD root filesystem, owner setup,
firmware update on the installed Air and rollback still require qualification.
Do not interpret expert-mode visibility as completed installation support.

## Exact inputs

The profile selects Apple's `UniversalMac_26.5.1_25F80_Restore.ipsw` and the
J713 identity for board `0x2c`, chip `0x8132`. Recovery extraction verifies:

| Object | SHA-256 |
| --- | --- |
| `022-21678-093.dmg.aea` | `12919ca28b831ec9ff71291ac857a51cef54263f4846180c7b7311a58c51d13e` |
| Decoded `BaseSystem.dmg` | `c7e05c0261a687b256c9ee58341346fbb995dfd04f03affd874524e6bc2e615d` |

The release decryption key is Apple-published material recovered from that
archive's metadata. It is not a machine key. No firmware binaries, owner
credentials, hardware calibration blobs or disk images accompany the patch.

The radio profile selects Garden C2 firmware explicitly: four Wi-Fi binaries,
AMKOR/USI NVRAM files, and both vendors' Bluetooth binary/PTB pairs. All ten
outputs match the recorded firmware fixture. Mini aliases
continue to select Sakhalin. Missing required files stop collection.
The hash-pinned 26.5.1 recovery's `C-4388__s-C2` firmware directory contains
only module revision 4.9 for Garden WLMT, for both vendors. Its separate C0
directory has revision 4.7; this C2 profile does not select it or qualify
another module revision or another Air unit.

## Failures reproduced and corrected

- J713 was absent from the device, recovery and radio tables.
- An incompatible firmware list indexed an empty list instead of reporting
  that no qualified firmware matched.
- Entering expert mode referenced an undefined minimum-version constant.
- The first J713 profile compared SFR's `RestoreLongVersion` with a macOS
  `ProductVersion`. This rejected the actual Air despite a passing synthetic
  version test. The SFR floor and regression fixture now use the Air's real
  value, `25.6.80.0.0,0`; the immediately older value is rejected.
- Recovery 26.5.1 omits `RestoreBundlePath`. The target's installed Preboot
  directory confirms `restore`, matching the existing 26.6.2 convention.
  Unknown missing-key layouts continue to fail.
- The firmware-update CLI accepted only the Mini profile. It now uses the
  explicit machine-profile table for its choices. Collection and update now
  produce twenty identical extracted firmware files in the native scratch check.

Fixing the expert-mode crash also lets Mini users reach its existing expert
features. Menu entry is unit-tested for both profiles; Mini external-disk
installation and expert templates are not qualified by this patch.

## Reproduce the checks

Apply the zero-context patch to the exact installer revision in an isolated
checkout:

```sh
(
set -e
test "$(git rev-parse HEAD)" = 6420623289fe7544cb8589b658355e57f0290fcc || {
  echo "Use installer revision 6420623289fe7544cb8589b658355e57f0290fcc." >&2
  exit 1
}
test -z "$(git status --porcelain)" || {
  echo "Use a clean isolated installer checkout." >&2
  exit 1
}
git apply --unidiff-zero --check /path/to/j713-recovery-radio-profile.patch
git apply --unidiff-zero /path/to/j713-recovery-radio-profile.patch
PYTHONPATH=src:. python3 -m unittest discover -s tests
)
```

The opt-in native macOS check takes the Apple IPSW URL, a cached copy of its
encrypted recovery member, the supplied expected radio hashes, and a new
output directory:

```sh
PYTHONPATH=src:tests:. python3 tests/validate_j713_recovery_macos.py \
  https://updates.cdn-apple.com/2026SpringFCS/fullrestores/122-88870/E47EBB85-45F2-4E3C-B9E7-6FF7868C2FBA/UniversalMac_26.5.1_25F80_Restore.ipsw \
  /path/to/022-21678-093.dmg.aea \
  tests/fixtures/j713-radio-25F80.json \
  /path/to/new-output
```

This check reads real IPSW metadata, uses native AEA decryption and DMG
verification, mounts recovery read-only, compares the ten radio files and
extracts the generated CPIO with the host archive utility. It checks the
shared Bluetooth PTB hard link after extraction. It compares the test host's
physical `disk0` layout before and after. It never invokes the interactive
installer or changes a boot policy.

The cached AEA's size and CRC must match the selected IPSW's member metadata
before it is substituted. Checks raise explicit failures even with Python
optimization enabled. This validator exercises recovery preflight and radio
packaging; the separate full-collection scratch run below exercises the
installer's actual collection and update methods.

## Recorded results and limits

All 44 unit tests passed on leased macOS and Linux fleet hosts. Six observer
tests also passed with Python optimization enabled. The added
profile and entry-point tests were recorded failing before the fixes. Thirty-six
deliberate mutations were all rerun and caught on the final test revision, including wrong board/stepping, relaxed
version requirements, lost expert gating, swapped vendor/regulatory sources,
wrong collection dispatch, omitted ISP/ALS collection and calibration archives,
an unchecked cached-image size, an incorrect download URL, an empty firmware
candidate list and an invalid decoded-image hash. Tests also compare actual
synthetic ISP/ALS bytes in the package and camera/factory/ALS bytes in the
fallback archive, catching omitted writes and wrong archive source paths.
Distinct dext/legacy Wi-Fi fixtures verify collection and update preference;
raw Wi-Fi, Bluetooth and FUD archive members are checked byte for byte.
The host factory-data fixture is separate from the recovery mount.
One initial test coupled host and recovery versions; separating those inputs
made the relaxed-host-version mutation fail as intended.

Native recovery and radio packaging passed on the Mac mini using J713 IPSW
metadata. The resulting CPIO SHA-256 was
`1179da5fc589c3f18bfdef8e92da79117b0c1723c677906ade13d2f1f302fb37`.
The host disk layout was unchanged and the recovery images were detached.
The first archive observer mishandled CPIO hard links and called an incorrect
cache cleanup method; those observer failures were corrected and rerun.

Additional native scratch checks exercised actual `collect_firmware` and
`update_firmware` with the Air's read-only calibration capture, yielding twenty
matching files: ten radio, one trackpad, six ISP, one ASMedia, and two ALS
calibration files (`apple/aop-als-cal.bin` and an `apple/HmCA-*` factory file).
These full-collection, stub-file and APFS harnesses are private preparation
artifacts, not included in the reproduction commands above. They ran on the
macOS test host with the Air's captured data. This verifies packaging, not
operation of each peripheral or an update on the installed Air. Stub file
assembly copied 134 restore entries and mounted the exact compressed recovery;
administrator metadata was synthetic and bootable/immutable flags were recorded
rather than applied. APFS preparation on a disposable 5 GB sparse disk image
created System/Data, Preboot and Recovery roles, preserving their UUIDs on a
second call. Each check left the physical host disk layout unchanged.

The Air kernel's native log confirms `brcmfmac4388c2-pcie`, `apple,garden`, and
`WLMT` vendor `a` version `4.9`. It requested the matching board-qualified
filenames. Wi-Fi calibration arrived from the live device tree. The cited
Bluetooth PCI-enable line alone does not prove HCI startup, calibration upload
or pairing. See the [native radio evidence](j713-radio-bringup.md).
The hash fixture predates this profile patch. Five hashes are also listed in
that native-radio note. The remaining CLM, TXCAP, USI NVRAM and USI Bluetooth
binary hashes pin the extracted recovery contents; their agreement establishes
artifact consistency, not additional native boot or Bluetooth evidence.
The two vendors' Bluetooth PTB outputs share one hash, so the ten output files
have nine distinct hashes.

This proves the stated preparation path. It does not prove Apple boot-object
personalization, a complete stub installation, operation of all peripherals,
Linux SSD writes, password login, cold boot, updates, suspend or daily use.
The Air stays in macOS while the remaining installation work is prepared.

## macOS compatibility target

The release target is the current macOS release plus the previous two updates,
without requiring users to install one exact release. This is an acceptance
target, not a support claim for this experimental patch. Host macOS and the
downloaded OS-paired boot firmware are separate inputs: newer hosts must still
select the qualified 26.5.1 boot payload. Asahi describes this separation in its
[platform documentation](https://asahilinux.org/docs/platform/quirks/).

The present J713 preparation profile still requires host macOS 26.5.1 or newer
and the qualified SFR floor. Removing these limits requires qualification of
the bundled native libraries, recovery decoder and personalization tools on
each supported host release. The Air itself is running 26.5.1; the native
preparation tests ran on the Mac mini with 26.5.2.
Version-selection unit cases do not establish compatibility on another host.
The final installer must identify an unmet prerequisite before partition writes
and guide the update when one is needed.
