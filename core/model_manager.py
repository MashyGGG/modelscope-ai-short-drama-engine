import logging
import time
from contextlib import contextmanager

import requests

from core.common import ROOT, GateError, file_hash, read_json, runtime_root
from core.resource_monitor import ResourceMonitor, snapshot

LOG = logging.getLogger(__name__)


class ModelManager:
    """ComfyUI loads models by graph; this class owns stage admission and unloading."""

    def __init__(self, client, hardware, tts_url):
        self.client = client
        self.hardware = hardware
        self.tts_url = tts_url

    def verify_service(self):
        from core.service_identity import managed_comfy_identity

        self.last_identity = managed_comfy_identity(self.client.url)
        return self.last_identity

    def assert_tts_stopped(self):
        try:
            response = requests.get(self.tts_url + "/openapi.json", timeout=2, proxies={"http": None, "https": None})
        except requests.ConnectionError:
            return
        except requests.Timeout as exc:
            raise GateError("TTS port is occupied/unresponsive; stop the TTS service before ComfyUI inference") from exc
        if response.status_code:
            raise GateError("TTS port is occupied; stop externally started GPT-SoVITS before image/video inference")

    def check_resources(self):
        data = snapshot()
        if not data["gpus"]:
            raise GateError(
                "No NVIDIA GPU detected. Only offline tests can run here; use the A10 Notebook for generation"
            )
        if data["gpus"][0]["free_mb"] < self.hardware["minimum_free_vram_mb"]:
            raise GateError(
                f"Insufficient free VRAM: {data['gpus'][0]['free_mb']:.0f} MiB; stop competing jobs/offload"
            )
        if data["ram_available_mb"] < self.hardware["minimum_available_ram_mb"]:
            raise GateError("Insufficient available system RAM; free memory before inference")
        return data

    def verify_models(self, stage):
        needed = {
            "characters": {"sdxl"},
            "images": {"sdxl", "faceid", "faceid_lora", "clip_vision"},
            "videos": {"wan", "umt5", "vae"},
        }[stage]
        for item in read_json(ROOT / "config/models-lock.json")["models"]:
            if item["key"] not in needed:
                continue
            path = runtime_root() / "ComfyUI/models" / item["destination"]
            if not path.is_file() or path.stat().st_size != item["size"] or file_hash(path) != item["sha256"]:
                raise GateError(f"Missing/changed model {item['key']}; run python -m scripts.download_models")
        if stage == "images":
            root = runtime_root() / "ComfyUI/models/insightface"
            manifest = root / "installed-manifest.json"
            if not manifest.is_file():
                raise GateError("Provision InsightFace buffalo_l using scripts/setup_comfyui.sh")
            for entry in read_json(manifest)["files"]:
                if file_hash(root / "models/buffalo_l" / entry["name"]) != entry["sha256"]:
                    raise GateError("InsightFace weights changed after installation")

    def unload(self):
        # /free only sets queue flags. Verify observed memory instead of assuming synchronous release.
        self.client.free()
        deadline = time.monotonic() + self.hardware["unload_timeout_seconds"]
        while time.monotonic() < deadline:
            self.client.require_idle()
            stats = self.client.stats()
            devices = [device for device in stats.get("devices", []) if device.get("type") == "cuda"]
            if devices and all(
                (device["vram_total"] - device["vram_free"]) / 2**20 <= self.hardware["comfy_residual_vram_limit_mb"]
                for device in devices
            ):
                return stats
            time.sleep(1)
        raise GateError(
            "ComfyUI unload was requested but VRAM did not fall below configured limit; inspect service logs"
        )

    @contextmanager
    def stage(self, stage, resume_prompt_ids=()):
        self.assert_tts_stopped()
        self.verify_service()
        queue = self.client.request("GET", "/queue")
        active = {item[1] for key in ("queue_running", "queue_pending") for item in queue.get(key, [])}
        if active:
            if not active <= set(resume_prompt_ids):
                raise GateError("ComfyUI is busy with another job; do not start a competing GPU stage")
            LOG.info("Reattaching to interrupted stage prompt(s): %s", active)
        else:
            self.unload()
            self.check_resources()
            self.verify_models(stage)
        LOG.info("Entering serial GPU stage %s", stage)
        with ResourceMonitor(self.hardware["monitor_interval_seconds"]) as monitor:
            try:
                yield monitor
            except BaseException:
                # Preserve the original failure and prompt_id. An interrupted worker can resume this prompt.
                try:
                    self.unload()
                except Exception:
                    LOG.warning("Stage cleanup deferred; inspect/resume the persisted ComfyUI prompt", exc_info=True)
                raise
            else:
                self.unload()


def oom_advice(error):
    message = str(error)
    if any(term in message.lower() for term in ("out of memory", "outofmemory", "cuda oom", "allocation on device")):
        return (
            message
            + "\nOOM: retry the failed shot with --low-resolution (384x640), --frames 33; use ComfyUI --lowvram and CPU text encoder. Do not change approved inputs silently."
        )
    return message
