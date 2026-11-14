import logging
import time
import uuid
from pathlib import Path

import requests

LOG = logging.getLogger(__name__)


class ComfyClient:
    def __init__(self, url, timeout=3600, poll_interval=2, session=None):
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.client_id = str(uuid.uuid4())

    def request(self, method, route, **kwargs):
        response = self.session.request(method, self.url + route, timeout=(5, 60), **kwargs)
        if not response.ok:
            raise RuntimeError(f"ComfyUI {route}: HTTP {response.status_code}: {response.text[:2000]}")
        return response.json() if response.content.strip() else {}

    def info(self):
        return self.request("GET", "/object_info")

    def stats(self):
        return self.request("GET", "/system_stats")

    def require_idle(self):
        data = self.request("GET", "/queue")
        if data.get("queue_running") or data.get("queue_pending"):
            raise RuntimeError("ComfyUI has another running/pending job; wait for it before running production")

    def submit(self, graph, prompt_id=None):
        payload = {"prompt": graph, "client_id": self.client_id}
        if prompt_id:
            payload["prompt_id"] = prompt_id
        result = self.request("POST", "/prompt", json=payload)
        if not result.get("prompt_id") or result.get("node_errors") or result.get("error"):
            raise RuntimeError(f"ComfyUI rejected workflow: {result}")
        LOG.info("Submitted prompt_id=%s", result["prompt_id"])
        return result["prompt_id"]

    def wait(self, prompt_id):
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            history = self.request("GET", f"/history/{prompt_id}")
            if prompt_id in history:
                result = history[prompt_id]
                status = result.get("status", {})
                errors = [
                    payload
                    for event, payload in status.get("messages", [])
                    if event in {"execution_error", "execution_interrupted"}
                ]
                if status.get("status_str") == "error" or errors:
                    raise RuntimeError(f"ComfyUI execution failed {prompt_id}: {errors or status}")
                if status.get("completed"):
                    return result
            time.sleep(self.poll_interval)
        # Leave prompt_id persisted: next invocation reattaches instead of creating duplicate inference.
        raise TimeoutError(f"{prompt_id}: exceeded {self.timeout}s; resume to reattach to the same prompt")

    def prompt_present(self, prompt_id):
        if prompt_id in self.request("GET", f"/history/{prompt_id}"):
            return True
        queue = self.request("GET", "/queue")
        return any(item[1] == prompt_id for key in ("queue_running", "queue_pending") for item in queue.get(key, []))

    def upload(self, path, subfolder="drama"):
        path = Path(path)
        with path.open("rb") as stream:
            result = self.request(
                "POST",
                "/upload/image",
                files={"image": (path.name, stream)},
                data={"type": "input", "subfolder": subfolder, "overwrite": "false"},
            )
        if result.get("type") != "input" or not result.get("name"):
            raise RuntimeError(f"Invalid image upload response: {result}")
        return "/".join(filter(None, [result.get("subfolder"), result["name"]]))

    def download_output(self, history, output_node, destination, extension):
        # Native SaveVideo uses 'images' UI records in some versions, 'videos' in others.
        output = history.get("outputs", {}).get(str(output_node), {})
        candidates = [
            entry
            for key in ("images", "videos", "gifs")
            for entry in output.get(key, [])
            if isinstance(entry, dict) and str(entry.get("filename", "")).lower().endswith(extension)
        ]
        if len(candidates) != 1:
            raise RuntimeError(f"Expected one {extension} from output node {output_node}; got {output}")
        entry = candidates[0]
        if entry.get("type") != "output":
            raise RuntimeError("Output must be a saved output file")
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".part")
        with self.session.get(
            self.url + "/view",
            params={key: entry.get(key, "") for key in ("filename", "subfolder", "type")},
            stream=True,
            timeout=(5, 120),
        ) as response:
            response.raise_for_status()
            with temporary.open("wb") as stream:
                for chunk in response.iter_content(1024 * 1024):
                    stream.write(chunk)
        if temporary.stat().st_size == 0:
            raise RuntimeError("ComfyUI returned an empty output")
        temporary.replace(destination)
        return entry

    def free(self):
        self.require_idle()
        return self.request("POST", "/free", json={"unload_models": True, "free_memory": True})
