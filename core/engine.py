import logging
import json
import time

from core.comfy_client import ComfyClient
from core.common import ROOT, GateError, config, runtime_root, write_json
from core.model_manager import ModelManager
from core.resource_monitor import snapshot
from core.task_queue import TaskQueue
from core.workflow_manager import WorkflowManager
from production.character_manager import CharacterManager
from production.episode_renderer import EpisodeRenderer
from production.image_generator import ImageGenerator
from production.video_generator import VideoGenerator
from production.voice_generator import GPTSoVITSAdapter, VoiceGenerator, audio_stage

LOG = logging.getLogger(__name__)


class Engine:
    def __init__(self, state):
        self.state = state
        self.settings = config("production")
        self.workflows = WorkflowManager()
        self.client = ComfyClient(
            self.settings["comfy_url"], self.settings["task_timeout"], self.settings["poll_interval"]
        )
        self.models = ModelManager(self.client, config("hardware"), self.settings["tts_url"])
        self.queue = TaskQueue(state, runtime_root() / "gpu.lock")
        self.image = ImageGenerator(state, self.queue, self.client, self.workflows)
        self.video = VideoGenerator(state, self.queue, self.client, self.workflows)

    def require_approved(self, episode_id, kind):
        plan = self.state.entity("episodes", episode_id)
        if kind == "identity":
            CharacterManager(self.state).references([c["character_id"] for c in plan["characters"]])
        else:
            for shot in self.state.shots(episode_id):
                self.state.selected(shot["shot_id"], kind)

    def run(
        self,
        episode_id,
        stage="all",
        shot_id=None,
        character_id=None,
        regenerate=False,
        retry=False,
        low_resolution=False,
        frames=None,
        angle="front",
        seed=None,
    ):
        if regenerate and not (shot_id or character_id):
            raise GateError(
                "--regenerate requires --shot or --character so approved batches are not regenerated accidentally"
            )
        if seed is not None and (not (shot_id or character_id) or not 0 <= seed < 2**63):
            raise GateError("--seed requires a single --shot/--character and must be in [0,2^63)")
        stages = ["characters", "images", "videos", "audio", "render"] if stage == "all" else [stage]
        plan = self.state.entity("episodes", episode_id)
        all_shots = self.state.shots(episode_id)
        shots = [s for s in all_shots if shot_id is None or s["shot_id"] == shot_id]
        if shot_id and not shots:
            raise GateError(f"Unknown shot {shot_id} in {episode_id}")
        if character_id and character_id not in {c["character_id"] for c in plan["characters"]}:
            raise GateError(f"Unknown character {character_id} in {episode_id}")
        outputs = []
        with self.queue.lock():
            for current in stages:
                resumable = {}
                if retry and not regenerate:
                    for row in self.state.rows(
                        "SELECT * FROM generation_jobs WHERE episode_id=? AND stage=? AND status IN ('failed','generating') ORDER BY started_at",
                        (episode_id, current),
                    ):
                        resumable[row["owner_id"]] = row
                if current in {"images", "videos", "audio", "render"}:
                    self.require_approved(episode_id, "identity")
                if current in {"videos", "audio", "render"}:
                    for shot in shots if current == "videos" else all_shots:
                        self.state.selected(shot["shot_id"], "image")
                if current in {"audio", "render"}:
                    self.require_approved(episode_id, "video")
                if current == "render":
                    outputs += EpisodeRenderer(self.state, self.queue, self.settings["render"]).render(
                        episode_id, retry
                    )
                    continue
                jobs = self.state.rows(
                    "SELECT prompt_id FROM generation_jobs WHERE episode_id=? AND stage=? AND prompt_id IS NOT NULL AND status IN ('generating','failed')",
                    (episode_id, current),
                )
                context = (
                    audio_stage(self.models, self.settings)
                    if current == "audio"
                    else self.models.stage(current, [j["prompt_id"] for j in jobs])
                )
                errors = []
                started = time.time()
                hardware = snapshot()
                monitor = None
                try:
                    with context as monitor:
                        if current == "characters":
                            work = [
                                c for c in plan["characters"] if not character_id or c["character_id"] == character_id
                            ]
                            work.sort(key=lambda c: c["character_id"] not in resumable)
                            for character in work:
                                try:
                                    previous = resumable.get(character["character_id"])
                                    original = json.loads(previous["input_params"]) if previous else {}
                                    if (
                                        angle == "front"
                                        and not regenerate
                                        and seed is None
                                        and not previous
                                        and self.state.selected(character["character_id"], "identity", required=False)
                                    ):
                                        continue
                                    outputs += self.image.character(
                                        episode_id,
                                        character["character_id"],
                                        regenerate,
                                        original.get("angle", angle),
                                        retry,
                                        original.get("seed", seed),
                                        previous,
                                    )
                                except (TimeoutError, KeyboardInterrupt):
                                    raise
                                except Exception as exc:
                                    errors.append(f"{character['character_id']}: {exc}")
                        elif current in {"images", "videos"}:
                            # Resume a running prompt before pending shots; this preserves the single GPU queue invariant.
                            active_owners = {
                                j["owner_id"]
                                for j in self.state.rows(
                                    "SELECT owner_id FROM generation_jobs WHERE episode_id=? AND stage=? AND status='generating'",
                                    (episode_id, current),
                                )
                            }
                            for shot in sorted(shots, key=lambda s: s["shot_id"] not in active_owners):
                                try:
                                    previous = resumable.get(shot["shot_id"])
                                    original = json.loads(previous["input_params"]) if previous else {}
                                    if current == "images":
                                        outputs += self.image.shot(
                                            episode_id, shot, regenerate, retry, original.get("seed", seed), previous
                                        )
                                    else:
                                        outputs += self.video.shot(
                                            episode_id,
                                            shot,
                                            regenerate,
                                            retry,
                                            original.get("low_resolution", low_resolution),
                                            original.get("frames", frames),
                                            original.get("seed", seed),
                                            previous,
                                        )
                                except (TimeoutError, KeyboardInterrupt):
                                    raise
                                except Exception as exc:
                                    errors.append(f"{shot['shot_id']}: {exc}")
                                    if "out of memory" in str(exc).lower() or "outofmemory" in str(exc).lower():
                                        self.models.unload()
                        elif current == "audio":
                            generator = VoiceGenerator(
                                self.state, self.queue, GPTSoVITSAdapter(self.settings["tts_url"])
                            )
                            for shot in shots:
                                for dialogue in shot["dialogues"]:
                                    try:
                                        outputs += generator.line(
                                            episode_id,
                                            dialogue,
                                            retry,
                                            regenerate,
                                            resumable.get(dialogue["dialogue_id"]),
                                        )
                                    except Exception as exc:
                                        errors.append(f"{shot['shot_id']}/{dialogue['dialogue_id']}: {exc}")
                        else:
                            raise ValueError(f"Unknown stage {current}")
                except BaseException as exc:
                    errors.append(str(exc))
                    raise
                finally:
                    write_json(
                        ROOT / "projects" / episode_id / "runs" / f"{time.time_ns()}_{current}.json",
                        {
                            "stage": current,
                            "started_at": started,
                            "elapsed": time.time() - started,
                            "hardware": hardware,
                            "comfy_service": getattr(self.models, "last_identity", None),
                            "monitor": monitor.report()
                            if monitor
                            else {
                                "gpu_peak_vram_mb": 0,
                                "system_peak_ram_mb": 0,
                                "samples": 0,
                                "monitor_errors": ["stage admission failed"],
                            },
                            "errors": errors,
                            "mode": "live",
                        },
                    )
                if errors:
                    raise GateError("Stage has failed shots; successful jobs were retained:\n" + "\n".join(errors))
                if stage == "all" and current in {"characters", "images", "videos"}:
                    self.require_approved(
                        episode_id, {"characters": "identity", "images": "image", "videos": "video"}[current]
                    )
        return outputs
