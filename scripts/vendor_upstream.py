"""Download small, immutable upstream source evidence (never model weights)."""

import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def get(url):
    request = urllib.request.Request(url, headers={"User-Agent": "short-drama-engine/0.1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def main():
    repos = {
        "comfy": (
            "comfyanonymous/ComfyUI",
            "master",
            [
                "LICENSE",
                "server.py",
                "nodes.py",
                "comfy_extras/nodes_wan.py",
                "comfy_extras/nodes_video.py",
                "comfy_extras/nodes_model_advanced.py",
                "comfy_extras/nodes_mask.py",
                "comfy_api/latest/_io.py",
                "tests-unit/execution_test/dynamic_inputs_test.py",
            ],
        ),
        "templates": ("Comfy-Org/workflow_templates", "main", ["LICENSE", "templates/video_wan2_2_5B_ti2v.json"]),
        "ipadapter": ("cubiq/ComfyUI_IPAdapter_plus", "main", ["LICENSE", "README.md", "IPAdapterPlus.py", "utils.py"]),
        "tts": (
            "RVC-Boss/GPT-SoVITS",
            "main",
            ["LICENSE", "api_v2.py", "requirements.txt", "GPT_SoVITS/configs/tts_infer.yaml"],
        ),
    }
    manifest = {}
    for key, (repo, branch, files) in repos.items():
        commit = json.loads(get(f"https://api.github.com/repos/{repo}/commits/{branch}"))["sha"]
        if key == "ipadapter":
            tree = json.loads(get(f"https://api.github.com/repos/{repo}/git/trees/{commit}?recursive=1"))
            files += [
                item["path"]
                for item in tree["tree"]
                if item["path"].startswith("examples/")
                and "faceid" in item["path"].lower()
                and item["path"].endswith(".json")
            ]
        entries = []
        for name in files:
            url = f"https://raw.githubusercontent.com/{repo}/{commit}/{name}"
            data = get(url)
            path = ROOT / "docs/upstream" / key / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            entries.append(
                {
                    "path": str(path.relative_to(ROOT)).replace("\\", "/"),
                    "url": url,
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
        manifest[key] = {"repo": repo, "commit": commit, "files": entries}
        print(key, commit, len(entries), flush=True)
    path = ROOT / "config/upstream-lock.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
