#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$0")/setup_common.sh"
cd "$ENGINE_ROOT"
"$LOCK_PY" -m scripts.service_control start "$@"
