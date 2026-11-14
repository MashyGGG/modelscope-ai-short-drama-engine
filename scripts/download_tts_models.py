"""Download official pretrained v2 assets with pinned HF revisions and per-file content hashes."""

import json
import sys
import urllib.request
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.common import ROOT, read_json, runtime_root, write_json
from scripts.download_models import download


def main():
    manifest = read_json(ROOT / "config/tts-models-lock.json")
    target = runtime_root() / "GPT-SoVITS/GPT_SoVITS/pretrained_models"
    for item in manifest["models"]:
        download(item, target / item["destination"])
    write_json(runtime_root() / "tts-models-installed.json", manifest)


def lock():
    repository = "lj1995/GPT-SoVITS"
    with urllib.request.urlopen(f"https://huggingface.co/api/models/{repository}?blobs=true", timeout=60) as response:
        data = json.load(response)
    prefixes = ("chinese-roberta-wwm-ext-large/", "chinese-hubert-base/")
    exact = {
        "gsv-v2final-pretrained/s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt",
        "gsv-v2final-pretrained/s2G2333k.pth",
    }
    entries = []
    for item in data["siblings"]:
        name = item["rfilename"]
        if not (name in exact or name.startswith(prefixes)):
            continue
        if name.endswith((".md", ".gitattributes")):
            continue
        if item.get("lfs"):
            size, sha256 = item["lfs"]["size"], item["lfs"]["sha256"]
        else:
            import hashlib

            with urllib.request.urlopen(
                f"https://huggingface.co/{repository}/resolve/{data['sha']}/{name}", timeout=60
            ) as response:
                content = response.read()
            size, sha256 = len(content), hashlib.sha256(content).hexdigest()
        entries.append(
            {
                "key": "tts_" + name,
                "repo": repository,
                "revision": data["sha"],
                "file": name,
                "destination": name,
                "size": size,
                "sha256": sha256,
            }
        )
    if not exact <= {entry["file"] for entry in entries}:
        raise ValueError("Official v2 model files missing")
    write_json(ROOT / "config/tts-models-lock.json", {"models": entries})
    print("Locked TTS model metadata:", data["sha"], len(entries))


if __name__ == "__main__":
    if "--lock" in sys.argv:
        lock()
    else:
        main()
