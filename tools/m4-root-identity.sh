# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
# Experimental Linux early-boot library; see docs/j713-gpu-normal-root.md.
# Source from a trusted caller. This helper reads identity; it never mounts.
# Call as device=$(resolve_root PARTUUID UUID PARENT) to contain shell variables.
# Pathname expansion must be enabled. All lookup/validation failures return 1;
# the caller owns diagnostic logging and any mount or recovery policy.
valid_uuid() {
    test "${#1}" -eq 36 || return 1
    printf '%s\n' "$1" | grep -Eq '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
}
validate_root_identity() {
    expected_part=$1 expected_fs=$2 expected_parent=$3
    actual_part=$4 actual_fs=$5 actual_type=$6 actual_parent=$7 actual_gpt_type=$8
    valid_uuid "$expected_part" && valid_uuid "$expected_fs" || return 1
    test "$actual_part" = "$expected_part" || return 1
    test "$actual_fs" = "$expected_fs" || return 1
    test "$actual_type" = ext4 || return 1
    test "$actual_parent" = "$expected_parent" || return 1
    test "$actual_gpt_type" = 0fc63daf-8483-4772-8e79-3d69d8477de4 || return 1
}
read_partition_identity() {
    part_uuid=$1 parent=$2
    valid_uuid "$part_uuid" || return 1
    case $parent in
        nvme0n1) ;;
        loop|loop*[!0-9]*) return 1 ;;
        loop*) ;;
        *) return 1 ;;
    esac
    device=$(blkid -c /dev/null -t "PARTUUID=$part_uuid" -o device /dev/"${parent}"p*) || return 1
    device=$(readlink -f "$device") || return 1
    test -b "$device" || return 1
    name=${device##*/}
    test -f "/sys/class/block/$name/partition" || return 1
    parent_path=$(readlink -f "/sys/class/block/$name/..") || return 1
    actual_part=$(blkid -p -s PART_ENTRY_UUID -o value "$device") || return 1
    actual_fs=$(blkid -p -s UUID -o value "$device") || return 1
    actual_type=$(blkid -p -s TYPE -o value "$device") || return 1
    actual_gpt_type=$(blkid -p -s PART_ENTRY_TYPE -o value "$device") || return 1
    test "$actual_part" = "$part_uuid" || return 1
    test "${parent_path##*/}" = "$parent" || return 1
}
resolve_root() {
    fs_uuid=$2
    valid_uuid "$fs_uuid" || return 1
    read_partition_identity "$1" "$3" || return 1
    validate_root_identity "$part_uuid" "$fs_uuid" "$parent" "$actual_part" "$actual_fs" "$actual_type" "${parent_path##*/}" "$actual_gpt_type" || return 1
    printf '%s\n' "$device"
}
resolve_efi() {
    read_partition_identity "$1" "$2" || return 1
    test "$actual_type" = vfat || return 1
    test "$actual_gpt_type" = c12a7328-f81f-11d2-ba4b-00a0c93ec93b || return 1
    printf '%s\n' "$device"
}
