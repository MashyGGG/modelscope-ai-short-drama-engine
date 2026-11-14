import argparse
import os
import sys
import time
import urllib.parse
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests

from core.common import ROOT, GateError, config, file_hash, read_json, runtime_root


def download(item, destination, source="huggingface"):
    destination = Path(destination)
    if (
        destination.is_file()
        and destination.stat().st_size == item["size"]
        and file_hash(destination) == item["sha256"]
    ):
        print(f"Verified existing model: {item['key']}", flush=True)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    repo, filename = item["repo"], item["file"]
    if source == "modelscope":
        repo = config("models").get("modelscope_repos", {}).get(repo, repo)
        # Mirror git revisions differ. Accept a mirror download only if its bytes match the pinned original SHA256.
        query = urllib.parse.urlencode({"Revision": "master", "FilePath": filename})
        url = f"https://modelscope.cn/api/v1/models/{repo}/repo?{query}"
    else:
        endpoint = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")
        url = f"{endpoint}/{repo}/resolve/{item['revision']}/{filename}"
    for attempt in range(3):
        try:
            offset = partial.stat().st_size if partial.is_file() else 0
            if offset > item["size"]:
                partial.unlink()
                offset = 0
            headers = {"Range": f"bytes={offset}-"} if offset else {}
            with requests.get(url, headers=headers, stream=True, timeout=(20, 120)) as response:
                response.raise_for_status()
                if response.status_code == 206 and not response.headers.get("Content-Range", "").startswith(
                    f"bytes {offset}-"
                ):
                    raise GateError("Download server returned an inconsistent Content-Range")
                mode = "ab" if response.status_code == 206 and offset else "wb"
                with partial.open(mode) as stream:
                    for chunk in response.iter_content(8 * 1024 * 1024):
                        stream.write(chunk)
            if partial.stat().st_size != item["size"] or file_hash(partial) != item["sha256"]:
                partial.unlink()
                raise GateError(f"Hash/size mismatch for {item['key']}; download rejected")
            partial.replace(destination)
            print(f"Downloaded and SHA256 verified: {item['key']}", flush=True)
            return
        except (requests.RequestException, OSError, GateError):
            if attempt == 2:
                raise
            time.sleep(2**attempt)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["huggingface", "modelscope"], default="huggingface")
    parser.add_argument(
        "--optional", action="store_true", help="Also download wardrobe/style IPAdapter and SDXL OpenPose ControlNet"
    )
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    for item in read_json(ROOT / "config/models-lock.json")["models"]:
        if not item["required"] and not args.optional:
            continue
        target = runtime_root() / "ComfyUI/models" / item["destination"]
        if args.verify_only:
            if not target.is_file() or target.stat().st_size != item["size"] or file_hash(target) != item["sha256"]:
                raise GateError(f"Missing/corrupt {item['key']}: {target}")
        else:
            download(item, target, args.source)


if __name__ == "__main__":
    main()
