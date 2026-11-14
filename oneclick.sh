#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$0")/scripts/setup_common.sh"
cd "$ENGINE_ROOT"
MODE="${1:-install}"
if [[ "$MODE" != install && "$MODE" != --prepare-only ]]; then
  printf 'Usage: bash oneclick.sh [--prepare-only]\n' >&2
  exit 2
fi
exec > >(tee -a "$ENGINE_ROOT/logs/install.log") 2>&1
"$PYTHON_BIN" -c 'import sys; assert (3,11)<=sys.version_info<(3,13), "Use Python 3.11 or 3.12"'
for binary in git ffmpeg ffprobe; do
  command -v "$binary" >/dev/null || { printf 'Missing system tool: %s\n' "$binary" >&2; exit 1; }
done
[[ -x "$LOCK_PY" ]] || "$PYTHON_BIN" -m venv "$DRAMA_RUNTIME/app-venv"
"$LOCK_PY" -m pip install --upgrade pip
"$LOCK_PY" -m pip install -r requirements.txt
"$LOCK_PY" -m scripts.preflight
bash scripts/setup_comfyui.sh
"$LOCK_PY" -m scripts.download_models --source "${DRAMA_MODEL_SOURCE:-huggingface}"
bash scripts/setup_tts.sh
"$LOCK_PY" -m pip freeze > logs/app-pip-freeze.txt
"$LOCK_PY" generate_episode.py --plan examples/episode_45s.json
if [[ "$MODE" == --prepare-only ]]; then
  printf 'CPU PREPARATION COMPLETE; GPU generation has not run.\n'
else
  bash scripts/start_services.sh
  "$LOCK_PY" -m scripts.preflight --live --models
  printf 'Installation complete. Activate: source %s/app-venv/bin/activate\n' "$DRAMA_RUNTIME"
  printf 'Begin character generation: python generate_episode.py --stage characters\n'
fi
