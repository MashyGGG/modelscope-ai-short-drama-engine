"""Provision official buffalo_l through InsightFace, then record a local integrity manifest."""

import hashlib
import json
import sys
from pathlib import Path


def main():
    from insightface.app import FaceAnalysis

    root = Path(sys.argv[1]).resolve()
    # Upstream IPAdapter uses provider=CPU; use exactly the same model and root layout.
    model = FaceAnalysis(name="buffalo_l", root=str(root), providers=["CPUExecutionProvider"])
    model.prepare(ctx_id=-1, det_size=(640, 640))
    entries = []
    for path in sorted((root / "models/buffalo_l").glob("*.onnx")):
        entries.append({"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    (root / "installed-manifest.json").write_text(
        json.dumps(
            {"source": "official InsightFace v0.7 buffalo_l release via SDK; first-download hashes", "files": entries},
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
