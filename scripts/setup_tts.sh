#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$0")/setup_common.sh"
checkout_pinned https://github.com/RVC-Boss/GPT-SoVITS.git "$DRAMA_RUNTIME/GPT-SoVITS" "$(upstream_commit tts)"
[[ -x "$DRAMA_RUNTIME/tts-venv/bin/python" ]] || "$PYTHON_BIN" -m venv "$DRAMA_RUNTIME/tts-venv"
TTS_PY="$DRAMA_RUNTIME/tts-venv/bin/python"
"$TTS_PY" -m pip install --upgrade pip wheel
"$TTS_PY" -m pip install torch torchvision torchaudio --index-url "$TORCH_INDEX_URL"
"$TTS_PY" -m pip install -r "$DRAMA_RUNTIME/GPT-SoVITS/requirements.txt"
"$TTS_PY" -m pip freeze > "$ENGINE_ROOT/logs/tts-pip-freeze.txt"
"$LOCK_PY" -m scripts.download_tts_models
printf 'GPT-SoVITS is installed. The audio stage starts and stops it on demand.\n'
