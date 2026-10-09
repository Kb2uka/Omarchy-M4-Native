# J713 GPU and normal-root checkpoint — October 8, 2026

The 13-inch M4 MacBook Air (`Mac16,12`, J713/T8132) reached the Omarchy
interface with the **Apple M4 GPU renderer**, normal systemd PID 1 and a UWSM
session. It then booted the same desktop from a GPT partition on a RAM disk,
selected by partition and filesystem identity. A deliberately nonexistent
partition ID stopped startup before root mounting, and the failure was captured
remotely over USB without an operator reading the screen.

These are short native experiments on one Air. They do not establish an
installed OS, a public installer, sustained stability or support for another M4
board. The internal SSD namespace remained read-only. All filesystem writes
and remount checks described here used RAM-backed storage.

## Graphics and boot inputs

The successful path uses the previously working m1n1 `c9ef56f` base with an
experimental GPU handoff port, kernel `7.1.9-m4-gpu-backport-20261008`, an
experimental `asahi_m4` module and matching Mesa runtime. The loader derives
the Air's ten performance states and hardware core mask rather than retaining
the eleven-state assumptions from the starting implementation. Its 24,800 mW
maximum is a derived firmware parameter, not a power measurement.

The module's Air bootstrap path requires an explicit `air_bootstrap_test=1`,
the J713/T8132 board identity and `mBoot-18000.120.36`. Its default firmware
policy is not relaxed. Constructor-failure resource retention remains a
bounded diagnostic measure; it is not qualified production cleanup.
The unseeded input device tree receives GPU properties from the loader.

| Qualified private artifact | SHA-256 |
| --- | --- |
| Kernel `Image.gz` | `69b92b6531b1f1c0748427a9f6975ae63352e3c3bb630517ca0f3eb0236242ea` |
| GPU module | `80cf3669575edd09aed226a93e47aaaf132e70f1070154629c413c0b40d34578` |
| Mesa runtime archive | `dd66e2252fb5007c8c912544c7c452c2c72942f3859c101980ef5fc427d69ffb` |
| Unseeded device tree | `2ce1f3f5e11d4d0d2cf70d562b1a9e617ca8c25a2ad198910284df1d6c5e9e01` |
| Positive partition-root combined payload | `38b2f0c899f3d847e69fd11c874b042fd7aecd50b3e1c2d2cd2af2e30a9aa543` |
| Deliberately wrong-ID payload | `224e3c9d7dda78968d69df42e33345854bb24bebb968d46102e886751192ce6f` |

The combined payload contains loader, command line, compressed kernel, device
tree and initramfs. It was loaded through the USB proxy into RAM. This proves
that combination can hand off to Linux; it does not prove stage 1 can load a
permanent EFI payload from the SSD. Normal-watchdog stage 1 and stage 2 builds
were prepared but have not been installed or hardware-qualified.
The bench payload preserves a once-armed 120-second watchdog. It must not be
installed as a permanent boot object.

The login path is `m4-login` → `desktop-login` → UWSM →
`wayland-wm@hyprland.desktop.service`. GPU initialization precedes login.
The session explicitly uses the matching EGL/GLES/GBM runtime,
`ASAHI_MESA_DEBUG=nocompress` and the experimental simpledrm/GBM path.
The render node reports the `asahi` driver; the panel still uses simpledrm
scanout. GLES falls back from 3.2 to 3.0, an external-texture shader remains
unavailable, and Xwayland reports software glamor. GPU rendering here is not a
claim that every application or display path is accelerated.
`idle=nop`, cursor warnings and unqualified power management remain.

Selected literal lines from the positive native capture:

```text
OpenGL core profile renderer: Apple M4 (G16G B1)
DEBUG from aquamarine ]: Renderer: Apple M4 (G16G B1)
G16: autonomous RTKit boot complete; firmware endpoints ready
G16: firmware accepted initialization graph
G16: opening control registration retired; cursors (3, 3, 3)
G16: barrier channel retired; two items consumed
```

The `G16:` lines above omit only the timestamp and device prefix. A complete
1,107,538-byte PNG was captured in the final positive test. Earlier GPU tests
also received operator confirmation that the desktop, pointer and menu worked.

## Root selection and the two early-boot failures

The root fixture is a 3 GiB ext4 image placed at the 1 MiB offset of a GPT
RAM disk. Early boot loads the loop driver, scans the loop partition table and
sources `/etc/m4-root.conf`. The experiment verifies:

1. A well-formed expected partition UUID and filesystem UUID.
2. Exactly one resolved block partition under the expected parent device.
3. Matching PARTUUID, filesystem UUID, ext4 filesystem type and Linux GPT type.
4. A root marker read after a `ro,noload` mount, before remounting the RAM root
   read-write and switching into normal systemd.

The published [identity helper](../tools/m4-root-identity.sh) performs steps
1–3 only. It does **not** mount, check the marker, change read-only flags,
partition a disk, provision accounts or install anything. Its caller must
supply trusted identities and handle failure. The private early-boot wrapper
and its configuration are not included as an installer.

The resolver uses util-linux `blkid` with caching disabled and explicit
`/dev/<parent>p*` candidates, followed by independent probing and a sysfs parent
check. It accepts only `nvme0n1` or `loop` plus decimal digits as parent names;
it does not scan other NVMe namespaces. Empty and duplicate results are
rejected. Use util-linux `blkid` rather than a BusyBox replacement. It also
requires Linux `/sys/class/block`, `readlink`, `grep` and a POSIX shell.
Pathname expansion must be enabled. Invoke it as
`device=$(resolve_root "$part_uuid" "$fs_uuid" "$parent")` to contain its
shell variables. The file also defines the two validation functions used by
the resolver. All lookup or validation failures return 1; the caller must
capture diagnostic output to distinguish missing tools from identity failures.
Only the loop-backed path has native root-boot evidence. The `nvme0n1` path is
an unqualified future storage path, not permission to mount the internal SSD.

Two actual failures were reproduced and corrected in the private image build:

| Failure | Proven cause and check |
| --- | --- |
| Missing `/etc/m4-root.conf` | The newc manifest omitted the earlier `/etc` directory entry. Parsing the actual archive failed with `Missing earlier directory /etc required by /etc/m4-root.conf`; corrected archive ordering passed. |
| Missing `libsmartcols.so.1` and `libblkid.so.1` | Dependencies were staged under `/lib`, while this ARM loader's default search path is `/usr/lib`. Actual ARM `findfs`, `blkid` and `losetup` failed with exit 127 inside an isolated chroot under QEMU. Moving dependencies to `/usr/lib` made all three execute successfully. |

These checks ran on the Linux build host. They were followed by successful
native positive boots; a syntax-only check was not treated as proof of a
usable initramfs.

## Positive and negative native evidence

The final positive capture shows the resolver probing only `/dev/loop0p1`,
then a read-only marker check and the RAM root mount. The following fixture
UUID is synthetic, not an internal SSD identifier:

```text
+ blkid -c /dev/null -t 'PARTUUID=71b71300-2026-4000-8000-000000000008' -o device /dev/loop0p1
+ mount -t ext4 -o ro,noload /dev/loop0p1 /desktop
+ cat /desktop/etc/m4-root-id
+ mount -t ext4 -o rw /dev/loop0p1 /desktop
AUTOMATIC_GPU_DESKTOP_PASS
PARTITION_IDENTITY_ROOT_PASS
NORMAL_ROOT_HANDOFF_PASS
EXT4_ROOT_WRITE_PASS
EXT4_REMOUNT_PERSISTENCE_PASS_RAM_ONLY
```

The final five lines are the capture harness's assertions, not kernel output.
The remount/persistence check used a separate disposable RAM ext4 filesystem;
it proves readback after unmount/remount, not survival of machine power loss.
All five `e2fsck` passes completed on that disposable filesystem, and normal
systemd reported no failed units.

For the negative boot, only the configured partition ID changed to end in
`0099`; the actual fixture still ended in `0008`. Startup reported
`M4_ROOT_BOOT_FAILED: root-identity-not-found` before root mounting. The early
USB link-local HTTP endpoint captured 56,929 bytes, and the observer confirmed:

```text
MISSING_PARTUUID_REJECTED_BEFORE_ROOT_MOUNT
HEADLESS_EARLY_FAILURE_CAPTURE_PASS 56929
```

This unauthenticated diagnostic endpoint was restricted to the bench USB
link-local address and removed during the positive root handoff. It is not a
public network service or production recovery interface. The captured failure
and mount ordering were checked; completeness of every HTTP report is not
claimed.

| Native experiment | Capture | Original proxy recovery |
| --- | --- | --- |
| Matching partition | Exit 0, GPU desktop and root assertions pass | 126.321 seconds; watchdog control 0 |
| Wrong partition ID | Exit 0, expected pre-mount rejection captured | 126.572 seconds; watchdog control 0 |

The intervals include USB recovery and re-enumeration after the 120-second
hardware timeout. Each experiment armed the watchdog once; it was not extended.
The original development proxy was verified again after each experiment.

| Retained evidence | SHA-256 |
| --- | --- |
| Positive state capture | `34387f818cae83a8ef83319078a1aa79baa516da70bd535292c54a5c2b6068e6` |
| Positive desktop PNG | `68119fec4e5aae51d9d0d81369d36cf1139133716dbca95bc71777852bb69480` |
| Negative early report | `a5bea007b5beca63c1934bfcc4acc930a274e61cbc7e2d43ee80331fb10cea59` |

Raw logs, firmware, bench credentials and boot images remain private. The
hashes identify retained evidence; they are not downloadable build inputs.
This checkpoint does not publish the complete experimental GPU stack or a
reproducible desktop image.

## Offline regression commands

On a Linux test host, from this repository:

```sh
python3 tests/test_m4_root_identity.py
python3 tests/test_m4_root_resolve.py
```

These tests use synthetic inputs and mocked shell commands, not real block
devices or network sockets. The identity test covers valid input, malformed
UUIDs, partition/filesystem mismatches, APFS, another namespace and an EFI GPT
type, including matching malformed expected and actual identities. Five base
UUID-format cases cover a valid value, multiline values and correctly shaped
non-hex text. Another 72 cases reject a foreign character and a character
from the wrong UUID class at every position: a hex digit in a separator
position, or a separator in a hex position.
Deleting each of five identity guards or the UUID length guard causes a
fixture failure. The published length and whole-value parent guards harden
the v5 helper after the native tests; the captures above used the earlier
guards. Fourteen resolver fixtures cover success, missing/duplicate matches,
malformed parents, non-partition candidates and mismatches in each probed
identity. Two malformed-UUID fixtures require rejection before any device
query. A separate fixture relocates device paths into a private temporary
directory and uses real shell expansion to verify selection across multiple
partitions without touching host devices. That directory contains spaces;
its path is passed as the subprocess working directory, never pasted into
shell source. Native evidence above separately
covers the real loop device, util-linux tools and error transport.

## Remaining installation work

The private root still contains a fixed bench account, bench SSH setup and
machine-specific state. It must be sanitized and provisioned for its owner.
The packaged provisioning flow assumes an SDDM login path and has conditional
Limine maintenance; the demonstrated M4 login uses UWSM directly. Enabling
that provisioning flow unchanged has not been qualified.

The recovery image for macOS 26.5.1 build 25F80 was extracted and verified on
a Mac host, and its radio firmware matched the known runtime inputs. The
current upstream installer checkout lacks an exact J713 profile for this
firmware. That profile and per-install firmware collection remain work to do;
no board or firmware guard has been globally disabled.

Permanent installation also needs allocated SSD space, a verified stage 1
storage boot, owner-authorized Recovery/boot-policy steps, a boot fallback and
an update strategy. There is no allocated Linux target yet. Nothing in these
RAM tests validates an internal SSD write, cold boot or upgrade.
Base M4 Mini and M4 Pro systems require separate board and firmware
qualification; this Air result is not proof they work.
