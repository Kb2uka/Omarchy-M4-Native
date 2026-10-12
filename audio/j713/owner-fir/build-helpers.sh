#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
set -euo pipefail

if [[ $(uname -s) != Darwin || $(uname -m) != arm64 ]]; then
    echo 'The helpers must be built on native arm64 macOS.' >&2
    exit 1
fi
if [[ $# != 1 || $1 != /* || -e $1 || -L $1 ]]; then
    echo 'Usage: build-helpers.sh NEW_ABSOLUTE_OUTPUT_DIRECTORY' >&2
    exit 1
fi
source_dir=$(cd -- "$(dirname -- "$0")/src" && pwd -P)
output_dir=$1
mkdir -m 700 -- "$output_dir"
for name in measure-transfer measure-memory; do
    /usr/bin/clang -arch arm64 -mmacosx-version-min=15.0 \
        -Wall -Wextra -Werror "$source_dir/$name.c" \
        -framework AudioToolbox -framework CoreFoundation \
        -o "$output_dir/$name"
done
{
    /usr/bin/sw_vers
    /usr/bin/clang --version
    /usr/bin/file "$output_dir/measure-transfer" "$output_dir/measure-memory"
    /usr/bin/otool -L "$output_dir/measure-transfer" "$output_dir/measure-memory"
    /usr/bin/shasum -a 256 "$source_dir/measure-transfer.c" "$source_dir/measure-memory.c" \
        "$output_dir/measure-transfer" "$output_dir/measure-memory"
} > "$output_dir/build-receipt.txt"
