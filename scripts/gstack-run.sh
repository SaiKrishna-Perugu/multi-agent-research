#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/bin:/bin:$PATH"
_script_path=$(cygpath -u "$0")
source "${_script_path%/*}/gstack-env.sh"
exec "$GSTACK_BIN/$1" "${@:2}"
