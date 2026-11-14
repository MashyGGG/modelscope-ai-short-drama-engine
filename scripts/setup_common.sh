#!/usr/bin/env bash
set -euo pipefail
ENGINE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export DRAMA_RUNTIME="${DRAMA_RUNTIME:-$ENGINE_ROOT/.runtime}"
mkdir -p "$DRAMA_RUNTIME" "$ENGINE_ROOT/logs"
PYTHON_BIN="${PYTHON_BIN:-python3}"
LOCK_PY="$DRAMA_RUNTIME/app-venv/bin/python"
TORCH_INDEX_URL="${TORCH_INDEX_URL:-https://download.pytorch.org/whl/cu128}"
export GIT_TERMINAL_PROMPT=0
cd "$ENGINE_ROOT"

checkout_pinned() {
  local repo_url="$1" target_dir="$2" commit="$3"
  if [[ ! -d "$target_dir/.git" ]]; then
    git -c http.version=HTTP/1.1 clone --filter=blob:none --no-checkout "$repo_url" "$target_dir"
  elif [[ -n "$(git -C "$target_dir" status --porcelain --untracked-files=no)" ]]; then
    printf 'Managed checkout contains local modifications: %s\n' "$target_dir" >&2
    return 1
  fi
  git -C "$target_dir" -c http.version=HTTP/1.1 fetch --depth 1 origin "$commit"
  git -C "$target_dir" checkout --detach "$commit"
}

upstream_commit() {
  "$LOCK_PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]]["commit"])' "$ENGINE_ROOT/config/upstream-lock.json" "$1"
}
