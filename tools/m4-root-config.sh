# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
# Source after m4-root-identity.sh. These values come from the selected boot object.
read_chosen_uuid() {
    chosen_dir=$1 property=$2
    file=$chosen_dir/$property
    test -f "$file" || return 1
    test "$(wc -c < "$file")" -eq 37 || return 1
    test "$(tail -c 1 "$file" | od -An -tu1 | tr -d '[:space:]')" = 0 || return 1
    value=$(tr -d '\000' < "$file") || return 1
    valid_uuid "$value" || return 1
    printf '%s\n' "$value"
}

read_root_config() {
    chosen_dir=$1
    ROOT_PARTUUID=$(read_chosen_uuid "$chosen_dir" 'omarchy,root-partition') || return 1
    ROOT_FSUUID=$(read_chosen_uuid "$chosen_dir" 'omarchy,root-filesystem') || return 1
    EFI_PARTUUID=$(read_chosen_uuid "$chosen_dir" 'gravity,efi-system-partition') || return 1
    test "$ROOT_PARTUUID" != "$EFI_PARTUUID" || return 1
}
