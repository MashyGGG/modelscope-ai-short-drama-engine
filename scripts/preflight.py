import argparse
import importlib.metadata
import shutil
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.comfy_client import ComfyClient
from core.common import ROOT, config, file_hash, read_json, runtime_root, write_json
from core.resource_monitor import snapshot
from core.schema import EpisodePlan
from core.workflow_manager import validate
from production.quality_checker import command


def check(live=False, models=False):
    errors, warnings = [], []
    report = {"mode": "live_preflight" if live else "offline_preflight", "gpu_e2e_status": "NOT_RUN"}
    for binary in ("ffmpeg", "ffprobe"):
        if not shutil.which(binary):
            errors.append(f"Missing {binary}")
    if shutil.which("ffmpeg"):
        filters = command(["ffmpeg", "-hide_banner", "-filters"]).stdout
        if " ass " not in filters:
            errors.append("FFmpeg lacks libass subtitle filter")
        report["ffmpeg"] = command(["ffmpeg", "-version"]).stdout.splitlines()[0]
    for repo in read_json(ROOT / "config/upstream-lock.json").values():
        for entry in repo["files"]:
            if file_hash(ROOT / entry["path"]) != entry["sha256"]:
                errors.append(f"Upstream source snapshot changed: {entry['path']}")
    for path in (ROOT / "workflows/api").glob("*.json"):
        validate(read_json(path))
        from scripts.validate_upstream import audit

        audit(read_json(path))
    EpisodePlan.model_validate(read_json(ROOT / "examples/episode_45s.json"))
    report["hardware"] = snapshot()
    report["dependencies"] = {
        name: importlib.metadata.version(name)
        for name in ("requests", "PyYAML", "Pillow", "pydantic", "portalocker", "psutil")
    }
    if not report["hardware"]["gpus"]:
        warnings.append("No NVIDIA GPU detected; real inference and GPU E2E acceptance have not run")
        if live:
            errors.append("Live generation requires NVIDIA GPU")
    if models:
        for item in read_json(ROOT / "config/models-lock.json")["models"]:
            path = runtime_root() / "ComfyUI/models" / item["destination"]
            if item["required"] and (
                not path.is_file() or path.stat().st_size != item["size"] or file_hash(path) != item["sha256"]
            ):
                errors.append(f"Missing/corrupt model: {item['key']}")
        insightface = runtime_root() / "ComfyUI/models/insightface/models/buffalo_l"
        if not all((insightface / name).is_file() for name in ("det_10g.onnx", "w600k_r50.onnx")):
            errors.append("InsightFace buffalo_l detection/recognition weights not provisioned")
    if live:
        client = ComfyClient(config("production")["comfy_url"])
        try:
            info = client.info()
            required = {
                n["class_type"] for path in (ROOT / "workflows/api").glob("*.json") for n in read_json(path).values()
            }
            errors += [f"Missing runtime node: {kind}" for kind in required - set(info)]
            report["comfy_stats"] = client.stats()
            from core.service_identity import managed_comfy_identity

            report["managed_service"] = managed_comfy_identity(client.url)
        except Exception as exc:
            errors.append(f"ComfyUI unavailable: {exc}")
    report.update(errors=errors, warnings=warnings, passed=not errors)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--models", action="store_true")
    args = parser.parse_args()
    report = check(args.live, args.models)
    write_json(ROOT / "reports/preflight.json", report)
    print(__import__("json").dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
