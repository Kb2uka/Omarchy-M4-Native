# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
# Source after the identity and chosen-property helpers. Run only in initramfs.
prepare_ssd_root() {
    read_root_config /sys/firmware/devicetree/base/chosen || return 1
    root_device=$(resolve_root "$ROOT_PARTUUID" "$ROOT_FSUUID" nvme0n1) || return 1
    efi_device=$(resolve_efi "$EFI_PARTUUID" nvme0n1) || return 1
    test "$root_device" != "$efi_device" || return 1
    test -d /vendorfw && test -f /vendorfw/.vendorfw.manifest || return 1
    # ext4 can clean up orphans even with ro,noload; protect the block device.
    test "$(blockdev --getro "$root_device")" = 0 || return 1
    blockdev --setro "$root_device" || return 1
    test "$(blockdev --getro "$root_device")" = 1 || return 1
    mount -t ext4 -o ro,noload "$root_device" /newroot || return 1
    if ! test "$(cat /newroot/etc/m4-root-id)" = "$ROOT_FSUUID"; then
        umount /newroot
        return 1
    fi
    first_boot=0
    expected=$(printf '%s\n%s\n%s\n' "$ROOT_PARTUUID" "$ROOT_FSUUID" "$EFI_PARTUUID")
    if test -f /newroot/etc/m4-install-complete; then
        recorded=$(cat /newroot/etc/m4-install-complete)
        if test "$recorded" != "$expected"; then
            umount /newroot
            return 1
        fi
    else
        first_boot=1
    fi
    umount /newroot || return 1
    blockdev --setrw "$root_device" || return 1
    test "$(blockdev --getro "$root_device")" = 0 || return 1
    # Filesystem writes follow the selected partition, filesystem,
    # parent disk, GPT type and read-only image-marker checks above.
    fsck_status=0
    if test "$first_boot" = 1; then
        # A previous attempt may have mounted writable before recording completion.
        # Growth requires a full check after that mount, even on a clean filesystem.
        e2fsck -f -p "$root_device" || fsck_status=$?
    else
        e2fsck -p "$root_device" || fsck_status=$?
    fi
    case $fsck_status in 0|1) ;; *) return 1;; esac
    # Journal recovery can reveal a completion record or a replaced systemd.
    # Recheck the recovered view before growth or first-install file writes.
    mount -t ext4 -o ro,noload "$root_device" /newroot || return 1
    if ! test -x /newroot/usr/lib/systemd/systemd; then
        umount /newroot
        return 1
    fi
    if test -f /newroot/etc/m4-install-complete; then
        recorded=$(cat /newroot/etc/m4-install-complete)
        if test "$recorded" != "$expected"; then
            umount /newroot
            return 1
        fi
        first_boot=0
    elif test "$first_boot" != 1; then
        umount /newroot
        return 1
    fi
    umount /newroot || return 1
    if test "$first_boot" = 1; then
        resize2fs "$root_device" || return 1
    fi
    mount -t ext4 -o rw "$root_device" /newroot || return 1
    cp -a /vendorfw/. /newroot/usr/lib/firmware/ || return 1
    if test "$first_boot" = 1; then
        mkdir -p /newroot/boot/efi || return 1
        {
            printf 'PARTUUID=%s / ext4 defaults,noatime 0 1\n' "$ROOT_PARTUUID"
            printf 'PARTUUID=%s /boot/efi vfat defaults,noatime,umask=0077 0 2\n' "$EFI_PARTUUID"
        } > /newroot/etc/fstab.new || return 1
        mv /newroot/etc/fstab.new /newroot/etc/fstab || return 1
        printf '%s\n%s\n%s\n' "$ROOT_PARTUUID" "$ROOT_FSUUID" "$EFI_PARTUUID" \
            > /newroot/etc/m4-install-complete.new || return 1
        # BusyBox checks per-file fsync errors, but ignores syncfs errors.
        sync /newroot/etc/fstab /newroot/etc/m4-install-complete.new /newroot/etc || return 1
        mv /newroot/etc/m4-install-complete.new /newroot/etc/m4-install-complete || return 1
        sync /newroot/etc/m4-install-complete /newroot/etc || return 1
    fi
    mkdir -p /run/m4 || return 1
    printf '%s\n%s\n%s\n' "$ROOT_PARTUUID" "$ROOT_FSUUID" "$EFI_PARTUUID" \
        > /run/m4/root-verified || return 1
    printf 'M4_SSD_ROOT_VERIFIED %s\n' "$root_device"
}
