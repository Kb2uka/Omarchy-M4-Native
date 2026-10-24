#!/bin/bash
# SPDX-License-Identifier: MIT
# Stage verified transport unchanged and compile a separate J715 client. Never install.
set -euo pipefail
if [ "$#" != 3 ]; then
    echo "usage: $0 MODULE_STAGING KERNEL_BUILD QUALIFIED_DISPLAY_PROFILE" >&2
    exit 2
fi
: "${TMPDIR:?}" "${NODE_COMPILE_CACHE:?}" "${BUN_RUNTIME_TRANSPILER_CACHE_PATH:?}"
export TMPDIR NODE_COMPILE_CACHE BUN_RUNTIME_TRANSPILER_CACHE_PATH PYTHONDONTWRITEBYTECODE=1 GIT_MASTER=1
here=$(dirname "$(realpath "$0")")
module=$(realpath -m "$1") kernel=$(realpath "$2") profile=$(realpath "$3")
python3 -B "$here/j715_display_profile.py" "$profile"
: "${J715_BUILD_IMAGE_ID:?immutable builder image digest required}"
[ ! -e "$module" ] && [ ! -L "$module" ]
[ -f "$kernel/Module.symvers" ] && [ -f "$kernel/include/generated/utsrelease.h" ]
llvm=/usr/lib/llvm-21/bin
"$llvm/clang" --version | grep -Fxq 'Ubuntu clang version 21.1.8 (6ubuntu1)'
grep -Fxq 'CONFIG_CC_VERSION_TEXT="Ubuntu clang version 21.1.8 (6ubuntu1)"' "$kernel/.config"
scratch=$(mktemp -d "$TMPDIR/j715-module.XXXXXXXX")
trap 'rm -rf -- "$scratch"' EXIT
python3 -B "$here/j715_display_profile.py" "$profile" --stage "$scratch/verified"
mkdir "$scratch/module"
cp "$scratch/verified/display/j713/backlight/"{afk.c,afk.h,native-wire.h,NOTICE} "$scratch/module/"
cp -r "$scratch/verified/display/j713/backlight/LICENSES" "$scratch/module/"
cp "$here/../display/j715/backlight/"{Kbuild,client.c,profile.h} "$scratch/module/"
cp "$scratch/verified/j715-profile-generated.h" "$scratch/module/"
export PATH="$llvm:$PATH"
make -C "$kernel" ARCH=arm64 LLVM="$llvm/" LOCALVERSION= KCFLAGS=-Werror M="$scratch/module" -j2 modules
modinfo -F vermagic "$scratch/module/j715_dcp_backlight.ko"
mkdir "$module"
cp "$scratch/module/j715_dcp_backlight.ko" "$module/"
cp "$scratch/verified/"{source-receipt.json,profile.json} "$module/"
cp -r "$scratch/verified/evidence" "$module/"
python3 -B "$here/j715_display_profile.py" "$profile" --receipt "$module" --kernel "$kernel"
sha256sum "$module/j715_dcp_backlight.ko"
