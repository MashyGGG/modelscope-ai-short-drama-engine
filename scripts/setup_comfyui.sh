#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$0")/setup_common.sh"
checkout_pinned https://github.com/comfyanonymous/ComfyUI.git "$DRAMA_RUNTIME/ComfyUI" "$(upstream_commit comfy)"
checkout_pinned https://github.com/cubiq/ComfyUI_IPAdapter_plus.git "$DRAMA_RUNTIME/ComfyUI/custom_nodes/ComfyUI_IPAdapter_plus" "$(upstream_commit ipadapter)"
[[ -x "$DRAMA_RUNTIME/comfy-venv/bin/python" ]] || "$PYTHON_BIN" -m venv "$DRAMA_RUNTIME/comfy-venv"
COMFY_PY="$DRAMA_RUNTIME/comfy-venv/bin/python"
"$COMFY_PY" -m pip install --upgrade pip wheel
"$COMFY_PY" -m pip install torch torchvision torchaudio --index-url "$TORCH_INDEX_URL"
"$COMFY_PY" -m pip install -r "$DRAMA_RUNTIME/ComfyUI/requirements.txt"
"$COMFY_PY" -m pip install 'numpy<2' 'insightface==0.7.3' onnxruntime
"$COMFY_PY" "$ENGINE_ROOT/scripts/setup_insightface.py" "$DRAMA_RUNTIME/ComfyUI/models/insightface"
"$COMFY_PY" -m pip freeze > "$ENGINE_ROOT/logs/comfy-pip-freeze.txt"
"$LOCK_PY" -m core.workflow_manager
mkdir -p "$DRAMA_RUNTIME/ComfyUI/user/default/workflows/short-drama"
cp "$ENGINE_ROOT"/workflows/ui/*.json "$DRAMA_RUNTIME/ComfyUI/user/default/workflows/short-drama/"
