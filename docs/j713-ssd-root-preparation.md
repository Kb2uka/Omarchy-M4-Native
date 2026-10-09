# J713 SSD root preparation

Checkpoint: October 9, 2026. Maintainer: KB2UKA.

This adds the installer-to-initramfs root identity contract and a portable early
boot image. The Air has not booted this path from its SSD. These sources are
preparation for that test, not a downloadable, qualified installation image.
The [native GPU checkpoint](j713-gpu-normal-root.md) still describes the last
verified hardware boot: a RAM-backed root supplied through the development host.

## Installer contract

Apply [the recovery/radio profile patch](../patches/installer/j713-recovery-radio-profile.patch)
to Gravity installer revision `6420623289fe7544cb8589b658355e57f0290fcc`, then apply
[root-identity.patch](../patches/installer/root-identity.patch), followed by
[linux-partition-guid.patch](../patches/installer/linux-partition-guid.patch). The
root identity and portable boot additions remain optional. The type-alias fix
applies to every template requesting the Linux filesystem GUID, including
templates without `rootfs_uuid` or `m4_boot`.

The first physical installation on macOS 26.5.1 passed recovery validation and
created the stub, EFI and Linux partitions, then stopped before writing the root
image. The template requests the Linux filesystem GPT type GUID, while `diskutil`
reports its content as `Linux Filesystem`. The partition-selection fix treats
that GUID and the Linux alias as the same type while retaining the new-UUID and
unique-candidate checks, including when macOS inserts an `Apple_Boot` helper.
The `linux-partition-guid.patch` adds `tests/fixtures/j713-ssd-20261009.json` to the
installer tree. The regression test loads that exact October 9 Air template and
exercises the real `OSInstaller.partition_disk` and `DiskUtil.addPartition` methods. This fixture is
a captured qualification input; a future packaged template needs its own matching
integration check. Free-space entries are included alongside existing partitions.

An installation that already stopped at this point needs its existing partition
identities inspected before recovery. Do not repeat free-space installation or
assume the normal boot-authorization repair option writes the missing root image.
Recovery from this partial state currently requires maintainer assistance:

1. Keep the original `installer.log`. Record the failed `addPartition` call and
   compare the partition lists immediately before and after it.
2. Identify the stub and EFI by their `New partition:` log entries. The root may
   have no such success entry: use the later debug `Partition ...` entries to
   identify its new UUID, type, offset and size. Include any newly inserted
   `Apple_Boot` helper in the comparison; preserve pre-existing partitions.
3. Capture `diskutil list internal physical` and `diskutil info -plist <identifier>`
   for each candidate, using the identifiers found in that log. Confirm the UUID,
   parent disk, type, offset and size still match before any recovery writes.
4. Contact the maintainer who supplied the experimental installer, using the
   same channel, to arrange sharing the log and disk information and preparing
   a continuation for that exact layout. Repository collaborators can also use
   the [issue tracker](https://github.com/Kb2uka/Omarchy-M4-Native/issues); the
   repository is currently private. If ownership or installation progress is uncertain,
   stop. These notes do not authorize deleting partitions or overwriting a root
   image that may already contain an installation.

The SSD template declares `rootfs_uuid` on exactly one Linux filesystem GPT
partition (`0fc63daf-8483-4772-8e79-3d69d8477de4`). That partition needs an `image`
member in the OS package. It also requires exactly one FAT EFI partition.
Before partition creation, package loading checks the ext superblock magic and
filesystem UUID, image alignment and initial partition capacity. It also requires
the clean-state flag, no filesystem-error flag, and a last-check timestamp at
least as recent as the last mount. Otherwise a clean quick check can skip work
and `resize2fs` can refuse first-boot growth. These are header checks; a separate
full filesystem check of the completed image remains a build requirement. If
the image was populated through a writable mount, unmount it and run a writable
full check (`e2fsck -f -p`), accepting only status 0 or 1, before packaging it.

The optional `m4_boot` object names four ZIP members: `loader`, `kernel`, `dtb`
and `initramfs`. Each is an object containing `file` and `sha256`. The remaining
field, `bootargs`, is one printable ASCII line of at most 1,024 characters.
This mode requires `next_object: "omarchy/boot.bin"` and `copy_firmware` on EFI.
All four inputs are size-bounded and hash-checked before installation. Kernel
and base initramfs use gzip; the device tree has a checked FDT header and size.

The installer combines the portable early root with firmware collected locally
by its existing `FWPackage`. It writes the resulting loader/kernel/device-tree/
initramfs payload to `omarchy/boot.bin` on the selected EFI partition. The file
is replaced after a flushed temporary write. No owner firmware belongs in the
portable image or source repository.

Stage 1 adds these properties to the selected boot object's chosen variables:

| Property | Source |
| --- | --- |
| `omarchy,root-partition` | Newly created root partition UUID |
| `omarchy,root-filesystem` | Template's checked filesystem UUID |
| `gravity,efi-system-partition` | Existing installer's EFI UUID variable |

The first two are new. Root and EFI partition UUIDs must differ. Stage 1 appends
chosen variables after the complete EFI file, then its own terminator. The EFI
file must not contain an additional four-zero terminator after its initramfs:
that would stop stage 2 before it reads the appended root identity. A regression
test reproduces this exact offset contract.

## Early root

[`m4-ssd-init`](../tools/m4-ssd-init) runs as PID 1 in the initramfs. Its libraries
are intended to be sourced there, not invoked against a running installation.
It loads the Apple NVMe module, waits for the selected partitions, then calls
[`m4-ssd-root.sh`](../tools/m4-ssd-root.sh).

The helpers require canonical UUID properties with an exact 37-byte device-tree
encoding, including the terminating NUL. They do not source `/etc/m4-root.conf`.
Root selection verifies PARTUUID, filesystem UUID, ext4 type, Linux GPT type and
the `nvme0n1` parent. EFI selection independently verifies PARTUUID, FAT type,
EFI GPT type and the same parent. Numbered loop parents are supported by the
read-only resolver for isolated Linux tests; the production init uses NVMe.

Preparation first checks that the selected root block device is writable, then
sets and verifies its block read-only flag. This is necessary because ext4 can
clean up orphaned inodes even on a `ro,noload` mount. It mounts the protected
root read-only with journal replay disabled and checks
`/etc/m4-root-id` against the filesystem UUID. An
existing `/etc/m4-install-complete` must match all three identities. Only after
these checks and a successful unmount does it restore and verify the selected
partition's writable flag and check the filesystem, accepting exit status
0 or 1. Without a completion record it runs `e2fsck -f -p` before `resize2fs`.
The full check also covers a previous attempt interrupted after its writable
mount but before recording completion. A second read-only mount after the check
verifies the recovered systemd executable and completion record. If journal
recovery reveals an existing record, it must match and growth is skipped;
a changed or unexpectedly missing record stops that boot. This is not a durable
deletion guard: if a record is manually removed, the next boot without it takes
the first-install path and rewrites fstab. Preserve the completion record.
Recognized existing
installations use `e2fsck -p` and skip growth. A record hidden until journal
recovery instead incurs the full check, while still preserving fstab.

After mounting read-write, it copies the locally collected firmware, writes a
first-install fstab using root and EFI PARTUUIDs, and records the installation
identity. It uses BusyBox's checked per-file `fsync` form to flush fstab, the
temporary record and their directory before rename, then the final record and
directory before handoff. Either command failing stops handoff. The shipped
BusyBox's `sync -f` ignored an injected writeback error; its per-file form
reported it correctly in the isolated runtime check.
These flushes are not a claim of qualified power-loss
recovery on the Air. Existing installations retain their fstab. A verified marker under
`/run/m4` accompanies the handoff to systemd and `graphical.target`. An early
preparation failure after console setup prints `M4_SSD_BOOT_FAILED` and waits.
Failures before that trap or inside the final `switch_root` can exit PID 1;
the qualification boot arguments use `panic=0` to keep a panic on screen.
Cold boot, native disk writes, owner setup and error recovery on the Air still
need qualification.

An unreadable identity marker in the initial unreplayed view can prevent all
later boots until filesystem recovery is performed from a separate Linux
rescue environment. Confirm the selected root's recorded GPT and filesystem
UUIDs before manual repair; repeating installation is not a recovery step.

## Build the portable initramfs

The builder requires a Linux build host with `readelf`, a matching kernel's
`gen_init_cpio`, static AArch64 BusyBox, the matching Apple NVMe module, and a
complete AArch64 root containing `blkid`, `e2fsck` and `resize2fs`:

```sh
python3 tools/build-m4-initramfs.py \
  --root /path/to/candidate-root \
  --busybox /path/to/static-aarch64-busybox \
  --nvme-module /path/to/nvme-apple.ko \
  --gen-init-cpio /path/to/kernel/usr/gen_init_cpio \
  --output /path/to/new-output-directory
```

The builder recursively includes ELF interpreters and `NEEDED` libraries from
the candidate root. Symlinks resolve inside that root; cycles, escapes and
missing files fail. Its output manifest records every included file's hash.
The generated `/lib/firmware` points to `/vendorfw`, populated by the installer's
second CPIO archive. Linux permits compressed and uncompressed CPIO archives
in one [initramfs buffer](https://www.kernel.org/doc/html/latest/driver-api/early-userspace/buffer-format.html).

The ARM runtime check extracted the actual generated archive and ran `blkid`,
`e2fsck` and `resize2fs` through QEMU in a chroot. All three loaded successfully.
Removing `libblkid.so.1` made the builder reject the incomplete input. This
checks library closure and shell syntax, not a native kernel boot.

## ARM offline owner provisioning

[offline-node-arm64.patch](../patches/omarchy/offline-node-arm64.patch) applies to
Omarchy 4.0.4, commit `c668141e9c42b13c80c9ca4ea108e11708c5e8a5`.
It maps `aarch64` to the offline `linux-arm64` Node archive and retains `x86_64`
selection of `linux-x64`. Unknown offline architectures fail explicitly.
Missing-archive and existing-runtime behavior are retained.

The recorded before/after check ran the pinned upstream script using the test's
`OWNER_NODE_SOURCE` override; the stock script fails ARM archive selection.
The default suite runs the patched fixture. Real first-user finalization also completed
in the private full ARM root, reporting Node `v26.11.1` and `arm64`. This does
not prove the native graphical owner wizard or password login.

## Reproduce source checks

On a leased test host, in a fresh copy of this revision:

```sh
python3 tests/test_m4_root_identity.py
python3 tests/test_m4_root_resolve.py
python3 -m unittest discover -s tests -p 'test_m4_*.py'
python3 -m unittest discover -s tests -p test_mise_offline_node.py
```

In a separate clean Gravity installer checkout at the pinned revision:

```sh
git apply --unidiff-zero /path/to/j713-recovery-radio-profile.patch
git apply /path/to/root-identity.patch
git apply /path/to/linux-partition-guid.patch
PYTHONPATH=src:tests:. python3 -m unittest discover -s tests
```

The installer suite currently has 69 tests. Linux and macOS runs passed.
Twenty initial deliberate mutations were caught, covering identity selection, image
validation, component checksums/limits, payload offsets, local firmware,
filesystem check handling, growth target, fstab preservation and ARM selection.
These tests do not qualify another Mac, another firmware version or updates.

Round-two probes exposed additional coverage gaps; those tests caught
18 targeted mutants on both Linux and macOS, including clean-image checks, CPIO
alignment, DTB length, installer prerequisites and the early-init handoff.
The actual static ARM BusyBox also passed the chosen-property, root-helper and
init handoff suites under emulation. The handoff fixture checks replacement of
the shell process, the exact root/systemd arguments, delayed storage discovery
and removal of inherited systemd overrides. These checks
do not replace the native SSD boot.
