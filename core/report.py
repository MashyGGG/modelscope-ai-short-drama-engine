import json
import time

from core.common import ROOT, GateError, read_json, write_json
from core.resource_monitor import snapshot
from core.task_queue import TaskQueue
from production.quality_checker import QualityChecker


def build_report(state, episode_id, acceptance=False):
    plan = state.entity("episodes", episode_id)
    report = TaskQueue(state, state.path.with_suffix(".lock")).report(episode_id)
    runs = [read_json(path) for path in (ROOT / "projects" / episode_id / "runs").glob("*.json")]
    report.update(
        episode_id=episode_id,
        title=plan["title"],
        target_duration=plan["duration"],
        gpu_e2e_status="NOT_RUN",
        generated_at=time.time(),
        gpu_peak_vram_mb=max((r["monitor"]["gpu_peak_vram_mb"] for r in runs), default=0),
        system_peak_ram_mb=max((r["monitor"]["system_peak_ram_mb"] for r in runs), default=0),
        production_wall_seconds=sum(r["elapsed"] for r in runs) + report["stage_seconds"].get("render", 0),
        acceptance_errors=[],
    )
    finals = state.rows(
        "SELECT * FROM assets WHERE owner_id=? AND kind='final' ORDER BY created_at DESC", (episode_id,)
    )
    if finals:
        report["final_preview"] = finals[0]["path"]
        report["final_review_status"] = finals[0]["status"]
        quality = json.loads(finals[0]["metadata"])["quality"]
        report["final_duration"] = quality["duration"]
        report["output_size_bytes"] = quality["size_bytes"]
    if acceptance:
        errors = report["acceptance_errors"]
        gpus = snapshot()["gpus"]
        if not any("A10" in gpu["name"] for gpu in gpus):
            errors.append("GPU acceptance must run on the actual A10 Notebook")
        for stage in ("characters", "images", "videos", "audio"):
            if not any(
                r["stage"] == stage
                and r["mode"] == "live"
                and any("A10" in g["name"] for g in r["hardware"]["gpus"])
                and r.get("comfy_service")
                and r["monitor"]["gpu_peak_vram_mb"] > 0
                and not r["monitor"]["monitor_errors"]
                for r in runs
            ):
                errors.append(f"Missing real A10 execution evidence for {stage}")
        required = [(c["character_id"], "identity") for c in plan["characters"]]
        for shot in state.shots(episode_id):
            required += [(shot["shot_id"], "image"), (shot["shot_id"], "video")]
            required += [(d["dialogue_id"], "audio") for d in shot["dialogues"]]
        required.append((episode_id, "final"))
        for owner, kind in required:
            try:
                asset = state.selected(owner, kind)
                if not asset["job_id"]:
                    raise GateError(f"{owner}/{kind}: no real generation job; imported media cannot establish GPU E2E")
                job = state.rows("SELECT * FROM generation_jobs WHERE id=?", (asset["job_id"],))[0]
                if job["status"] != "succeeded":
                    raise GateError(f"{owner}/{kind}: generation did not succeed")
                if kind in {"identity", "image", "video"}:
                    if not job["prompt_id"]:
                        raise GateError("Missing actual ComfyUI prompt_id")
                    history = read_json(ROOT / "projects" / job["episode_id"] / "jobs" / job["id"] / "history.json")
                    if not history.get("status", {}).get("completed"):
                        raise GateError("Incomplete ComfyUI history")
                quality = json.loads(asset["metadata"]).get("quality", {})
                if quality.get("errors"):
                    raise GateError(f"{owner}: failed quality")
                if kind == "final":
                    verified = QualityChecker().video(
                        asset["path"],
                        owner,
                        {"width": 1080, "height": 1920, "fps": 24, "duration": plan["duration"]},
                        final=True,
                    )
                    if verified["errors"]:
                        errors += verified["errors"]
            except (GateError, OSError, IndexError) as exc:
                errors.append(str(exc))
        report["gpu_e2e_status"] = "GPU E2E PASSED" if not errors else "NOT_PASSED"
    write_json(ROOT / "reports" / f"{episode_id}.json", report)
    return report
