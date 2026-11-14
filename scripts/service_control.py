import argparse
import subprocess
import time

import requests

from core.comfy_client import ComfyClient
from core.common import ROOT, GateError, config, runtime_root, write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start"])
    parser.add_argument("--lowvram", action="store_true")
    args = parser.parse_args()
    client = ComfyClient(config("production")["comfy_url"])
    try:
        client.stats()
    except requests.ConnectionError:
        pass
    else:
        from core.service_identity import managed_comfy_identity

        managed_comfy_identity(client.url)
        print("Managed ComfyUI is already responding; reusing the verified process.")
        return
    root = runtime_root()
    command = [
        str(root / "comfy-venv/bin/python"),
        "main.py",
        "--listen",
        "127.0.0.1",
        "--port",
        "8188",
        "--preview-method",
        "none",
    ]
    if args.lowvram:
        command.append("--lowvram")
    (ROOT / "logs").mkdir(exist_ok=True)
    with (ROOT / "logs/comfyui.log").open("a", encoding="utf-8") as stream:
        process = subprocess.Popen(
            command, cwd=root / "ComfyUI", stdout=stream, stderr=subprocess.STDOUT, start_new_session=True
        )
    write_json(root / "comfy-service.json", {"pid": process.pid, "command": command, "started_at": time.time()})
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise GateError("ComfyUI exited; inspect logs/comfyui.log")
        try:
            client.stats()
            print("ComfyUI ready. TTS remains stopped until the audio stage.")
            return
        except requests.RequestException:
            time.sleep(1)
    process.terminate()
    process.wait(timeout=30)
    raise GateError("ComfyUI startup timeout; inspect logs/comfyui.log")


if __name__ == "__main__":
    main()
