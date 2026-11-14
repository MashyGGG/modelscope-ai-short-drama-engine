import subprocess
from urllib.parse import urlsplit

import psutil

from core.common import ROOT, GateError, read_json, runtime_root


def managed_comfy_identity(url):
    """Tie API service ownership to the checkout whose weights are being verified."""
    root = runtime_root()
    record_path = root / "comfy-service.json"
    if not record_path.is_file():
        raise GateError("ComfyUI service ownership is unknown; start it with bash scripts/start_services.sh")
    record = read_json(record_path)
    try:
        process = psutil.Process(record["pid"])
        cwd = process.cwd()
        from pathlib import Path

        if Path(cwd).resolve() != (root / "ComfyUI").resolve() or "main.py" not in process.cmdline():
            raise GateError("Recorded ComfyUI PID no longer identifies the managed checkout")
        address = urlsplit(url)
        if address.hostname not in {"localhost", "127.0.0.1"}:
            raise GateError("First-version managed inference requires a local ComfyUI URL")
        port = address.port or 8188
        if not any(
            c.status == psutil.CONN_LISTEN and c.laddr.port == port for c in process.net_connections(kind="inet")
        ):
            raise GateError("Configured ComfyUI port is not served by the managed process")
        commit = subprocess.run(
            ["git", "-C", str(root / "ComfyUI"), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        if commit != read_json(ROOT / "config/upstream-lock.json")["comfy"]["commit"]:
            raise GateError(
                "Managed ComfyUI checkout changed; rerun setup_comfyui.sh or update the verified source lock"
            )
        return {"pid": process.pid, "cwd": cwd, "commit": commit, "port": port}
    except (psutil.Error, KeyError, subprocess.SubprocessError) as exc:
        raise GateError(f"Cannot verify managed ComfyUI process: {exc}; inspect the service record") from exc
