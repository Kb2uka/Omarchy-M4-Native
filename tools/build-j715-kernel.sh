#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Kazuhide Takahashi
# Rebuild the J715 tester kit's kernel (build 18 with patches-kernel/j715-input) to
# the exact bytes the kit pins. Run as an ordinary user in build 18's environment
# (docs/j715-test-kit.md): Clang/LLD/LLVM 21.1.8 and Rust 1.93.1 at its packaged path,
# with the tree at /job/kernel-backport, because Rust metadata records absolute paths.
# m4-gravity-builder:20261008 needs patch added (apt-get install patch).
# Usage: build-j715-kernel.sh KERNEL_CONTROLLER_TAR_GZ
set -euo pipefail
if [ "$#" != 1 ]; then
    echo "usage: $0 KERNEL_CONTROLLER_TAR_GZ" >&2
    exit 2
fi
command -v patch >/dev/null || { echo "patch is required (in m4-gravity-builder: apt-get install patch)" >&2; exit 1; }
archive=$(realpath "$1")
repo=$(cd "$(dirname "$0")/.." && pwd)
tree=/job/kernel-backport
check() {
    [ "$(sha256sum "$2" | cut -d' ' -f1)" = "$1" ] || { echo "unexpected $2" >&2; exit 1; }
}
check 810339d85aa3171fb1a982b71d01c0bf4de816f81ebbe9149d0dd3268e98ce57 "$archive"
[ ! -e "$tree" ] || { echo "$tree exists; remove it first" >&2; exit 1; }
[ "$(rustc --print sysroot)" = /usr/lib/rust-1.93 ] || { echo "rustc is not Rust 1.93 at /usr/lib/rust-1.93" >&2; exit 1; }

mkdir -p "$tree"
cd "$tree"
tar xzf "$archive" --strip-components=1
check 14b4437b2b195e912a2ab614d28761acd3dc79f886d4487964aa0912616a3a6e .config
for patch in j713-input/dockchannel-start-retry j713-input/macsmc-key-info-lock \
    j713-input/dockchannel-afe-action j715-input/dockchannel-hid-accept-j715; do
    patch -s -p1 --forward <"$repo/patches-kernel/$patch.patch"
done
check 6fea03db95269488b821a6d7cfaa862b403e094900304704cb543e87f5fa300c drivers/hid/dockchannel-hid/dockchannel-hid.c
check 0cbe1eaf11e473336bfc24d5036a4a15586f21c868e8fedd21aeb1ce8bcaa9d6 drivers/mfd/macsmc.c

# The kit kernel's built-in module certificate, without its private key: this
# configuration does not sign modules (CONFIG_MODULE_SIG_ALL is off), so the build
# only extracts the certificate. The saved command keeps kbuild from generating a
# new key in its place.
cp "$repo/patches-kernel/j715-input/build-certificate.pem" certs/signing_key.pem
check bf669f2bba53b097426a9406f07357346d3e07b017987afa7a8a1977db246c19 certs/signing_key.pem
printf '%s\n' 'savedcmd_certs/signing_key.pem := openssl req -new -nodes -utf8 -sha512 -days 36500 -batch -x509 -config certs/x509.genkey -outform PEM -out certs/signing_key.pem -keyout certs/signing_key.pem  2>&1' \
    >certs/.signing_key.pem.cmd

# The archive carries x86-64 host tools and Rust libraries from its build host; rebuild them.
# An x86-64 ELF: the ELF magic, little-endian, e_machine 0x3e.
x86_64_elf() {
    local header
    header=$(od -An -tx1 -N20 "$1" | tr -d ' \n')
    [[ ${header:0:8} == 7f454c46 && ${header:10:2} == 01 && ${header:36:4} == 3e00 ]]
}
find . -type f -perm -u+x -size +1k -print0 | while IFS= read -r -d '' file; do
    if x86_64_elf "$file"; then
        rm -f "$file"
    fi
done
rm -f rust/*.so rust/*.rlib rust/*.rmeta

make=(make ARCH=arm64 LLVM=1 LOCALVERSION= KBUILD_BUILD_VERSION=18 KBUILD_BUILD_USER=ubuntu
      KBUILD_BUILD_HOST=aa4fae7caf7b "-j$(nproc)")
# Build 18's built-in initramfs dates from the tree's first build, 2026-10-07.
"${make[@]}" KBUILD_BUILD_TIMESTAMP="2026-10-07 12:23:00 UTC" usr/
# Build 18 itself ran without KBUILD_BUILD_TIMESTAMP: version.o carries an empty
# timestamp and version-timestamp.o the output of date. Keep the initramfs as built.
sed -i 's/ -d "2026-10-07 12:23:00 UTC"//' usr/.initramfs_data.cpio.cmd
if grep -q -- ' -d ' usr/.initramfs_data.cpio.cmd; then
    echo "initramfs command still has -d" >&2
    exit 1
fi
dates=$(mktemp -d)
trap 'rm -rf "$dates"' EXIT
printf '%s\n' '#!/bin/sh' '[ $# = 0 ] && { echo "Mon Oct 12 20:24:06 UTC 2026"; exit 0; }' 'exec /usr/bin/date "$@"' \
    >"$dates/date"
chmod 755 "$dates/date"
PATH="$dates:$PATH" "${make[@]}" Image.gz

check 7a29612258cf884262d94364fc5a16055c64651c45c7d32a5e75704a5e083eb8 certs/signing_key.x509
check 291c4b95d3f1fbbffc0e11af3421b3125c3155050c90e97867cad6e3d244c05f arch/arm64/boot/Image.gz
echo "$tree/arch/arm64/boot/Image.gz"
