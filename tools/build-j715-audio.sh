#!/bin/bash
# SPDX-License-Identifier: MIT
# Stage only. Never installs or loads a module.
set -euo pipefail
if [ "$#" != 8 ]; then
    echo 'usage: build-j715-audio.sh VERIFIED_DONOR KERNEL_BUILD SOUND_EXPORTS SYSROOT CARGO_HOME KERNEL_IMAGE_ID DAEMON_IMAGE_ID NEW_OUTPUT' >&2
    exit 2
fi
: "${TMPDIR:?}" "${NODE_COMPILE_CACHE:?}" "${BUN_RUNTIME_TRANSPILER_CACHE_PATH:?}"
export TMPDIR NODE_COMPILE_CACHE BUN_RUNTIME_TRANSPILER_CACHE_PATH
export PYTHONDONTWRITEBYTECODE=1 GIT_MASTER=1
exec python3 -B "$(dirname "$0")/prepare-j715-audio-build.py" --build "$@"
