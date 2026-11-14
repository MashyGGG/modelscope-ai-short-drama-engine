import uuid
import secrets
from pathlib import Path

from core.common import ROOT, GateError, digest, file_hash, read_json, write_json
from core.workflow_manager import validate
from director.prompt_builder import build
from production.character_manager import CharacterManager
from production.quality_checker import QualityChecker


class ImageGenerator:
    def __init__(self, state, queue, client, workflows):
        self.state, self.queue, self.client, self.workflows = state, queue, client, workflows
        self.characters = CharacterManager(state)

    def model_version(self):
        return digest(read_json(ROOT / "config/models-lock.json"))

    def workflow_version(self):
        return digest(
            {
                "templates": [
                    file_hash(ROOT / f"workflows/api/{name}.json") for name in ("sdxl_faceid", "realistic_txt2img")
                ],
                "builder": file_hash(ROOT / "core/workflow_manager.py"),
                "settings": self.workflows.settings,
                "generator": file_hash(Path(__file__)),
                "prompt_builder": file_hash(ROOT / "director/prompt_builder.py"),
                "upstream": read_json(ROOT / "config/upstream-lock.json"),
            }
        )

    def prepare_job(self, episode_id, owner_id, stage, params, version, resume_job=None):
        if resume_job:
            original = __import__("json").loads(resume_job["input_params"])
            if "candidate" in original:
                params["candidate"] = original["candidate"]
        job = self.state.job(episode_id, owner_id, stage, params, version, self.model_version())
        if resume_job and job["id"] != resume_job["id"]:
            self.state.update_job(
                resume_job["id"], status="superseded", error="Inputs/workflow/models changed before resume"
            )
        return job

    def execute(self, job, graph_builder, output_node, kind):
        pid = job["prompt_id"]
        if pid:
            entry = self.client.request("GET", f"/history/{pid}").get(pid, {})
            if entry.get("status", {}).get("status_str") == "error":
                pid = None
        if pid and not self.client.prompt_present(pid):
            # History was cleared or the server restarted; user-requested retry can submit again.
            pid = None
        if not pid:
            self.client.require_idle()
            graph = graph_builder()
            validate(graph, self.client.info())
            write_json(ROOT / "projects" / job["episode_id"] / "jobs" / job["id"] / "submitted.json", graph)
            # The pinned ComfyUI accepts client-provided UUID prompt IDs. Persist before submission.
            pid = str(uuid.uuid5(uuid.NAMESPACE_URL, job["id"] + ":" + str(job["attempts"])))
            self.state.update_job(job["id"], prompt_id=pid)
            self.client.submit(graph, prompt_id=pid)
        history = self.client.wait(pid)
        directory = ROOT / "projects" / job["episode_id"] / "jobs" / job["id"]
        write_json(directory / "history.json", history)
        extension = ".mp4" if kind == "video" else ".png"
        path = directory / ("output" + extension)
        output_record = self.client.download_output(history, output_node, path, extension)
        if kind == "video":
            params = __import__("json").loads(job["input_params"])
            settings = params["video"]
            width, height = (
                settings["low_resolution"] if params["low_resolution"] else (settings["width"], settings["height"])
            )
            quality = QualityChecker().video(
                path,
                job["owner_id"],
                {"width": width, "height": height, "fps": settings["fps"], "frames": params["frames"]},
            )
        else:
            quality = QualityChecker().image(path, job["owner_id"])
            settings = self.workflows.settings["image"]
            if (quality.get("width"), quality.get("height")) != (settings["width"], settings["height"]):
                quality["errors"].append(f"Image dimensions must match {settings['width']}x{settings['height']}")
        write_json(directory / "quality.json", quality)
        if quality["errors"]:
            raise GateError(f"{job['owner_id']}: quality check failed: {quality['errors']}")
        asset_id = self.state.add_asset(
            job["owner_id"],
            kind,
            path,
            job["id"],
            {
                "quality": quality,
                "prompt_id": pid,
                "seed": __import__("json").loads(job["input_params"])["seed"],
                "output": output_record,
            },
        )
        return [asset_id]

    def character(
        self,
        episode_id,
        character_id,
        regenerate=False,
        angle="front",
        retry=False,
        seed_override=None,
        resume_job=None,
    ):
        character = self.state.entity("characters", character_id)
        if not character["fictional"] and not character["likeness_authorized"]:
            raise GateError("Likeness authorization required")
        kind = "identity" if angle == "front" else f"angle_{angle}"
        refs = self.characters.references([character_id]) if angle != "front" else []
        prompt = (
            f"Photorealistic casting portrait, {angle} view, single adult person, neutral studio background, natural lighting, "
            + ", ".join(character["appearance"].values())
        )
        prompt += f", {character['gender']}, {character['age']} years old, wearing {character['wardrobe']['default']}"
        seed = int(digest([character_id, angle])[:12], 16)
        if regenerate:
            seed = secrets.randbits(48)
        if seed_override is not None:
            seed = seed_override
        params = {
            "character": {**character, "identity_locked": False},
            "angle": angle,
            "seed": seed,
            "refs": [r["asset"]["sha256"] for r in refs],
        }
        if regenerate:
            params["candidate"] = uuid.uuid4().hex
        job = self.prepare_job(episode_id, character_id, "characters", params, self.workflow_version(), resume_job)

        def graph():
            references = [{"image": self.client.upload(r["asset"]["path"])} for r in refs]
            return self.workflows.image(
                prompt, "distorted face, extra people, text", seed, f"drama/{job['id']}", references
            )[0]

        return self.queue.run(job, lambda j: self.execute(j, graph, "9", kind), retry)

    def shot(self, episode_id, shot, regenerate=False, retry=False, seed_override=None, resume_job=None):
        shot = dict(shot)
        if regenerate:
            shot["seed"] = secrets.randbits(48)
        if seed_override is not None:
            shot["seed"] = seed_override
        plan = self.state.entity("episodes", episode_id)
        refs = self.characters.references(shot["characters"])
        scene = self.state.entity("scenes", f"{episode_id}:{shot['scene_id']}")
        prompt = build(shot, [r["character"] for r in refs], scene, plan["style"])
        appearance = []
        for ref in refs:
            key = shot.get("wardrobe", {}).get(ref["character"]["character_id"], "default")
            asset = self.state.selected(ref["character"]["character_id"], f"wardrobe_{key}", required=False)
            if asset:
                appearance.append(asset)
            expression = shot.get("expressions", {}).get(ref["character"]["character_id"])
            if expression:
                expression_ref = self.state.selected(
                    ref["character"]["character_id"], f"expression_{expression}", required=False
                )
                if expression_ref:
                    appearance.append(expression_ref)
        scene_ref = self.state.selected(f"{episode_id}:{shot['scene_id']}", "scene", required=False)
        if scene_ref:
            appearance.append(scene_ref)
        additional = list(shot.get("reference_images", []))
        masks = shot.get("identity_masks", {})
        if len(refs) > 1 and any(r["character"]["character_id"] not in masks for r in refs):
            raise GateError(
                f"{shot['shot_id']}: multiple visible characters need identity_masks; use separate reverse shots or add masks"
            )
        local_files = [*additional, *masks.values()]
        from PIL import Image

        for mask in masks.values():
            with Image.open(mask) as image:
                expected = (self.workflows.settings["image"]["width"], self.workflows.settings["image"]["height"])
                if (
                    "A" not in image.getbands()
                    or image.size != expected
                    or image.getchannel("A").getextrema() != (0, 255)
                ):
                    raise GateError(
                        f"{shot['shot_id']}: identity mask must be RGBA {expected}, with transparent and opaque regions"
                    )
        if shot.get("pose_reference"):
            local_files.append(shot["pose_reference"])
        params = {
            "shot": shot,
            "prompt": prompt,
            "seed": shot["seed"],
            "identities": [r["asset"]["sha256"] for r in refs],
            "appearance": [a["sha256"] for a in appearance],
            "files": {str(p): file_hash(p) for p in local_files},
        }
        if regenerate:
            params["candidate"] = uuid.uuid4().hex
        job = self.prepare_job(episode_id, shot["shot_id"], "images", params, self.workflow_version(), resume_job)

        def graph():
            uploaded = []
            for ref in refs:
                item = {"image": self.client.upload(ref["asset"]["path"])}
                mask = masks.get(ref["character"]["character_id"])
                if mask:
                    item["mask"] = self.client.upload(mask)
                uploaded.append(item)
            other = [self.client.upload(a["path"]) for a in appearance] + [
                self.client.upload(Path(p)) for p in additional
            ]
            pose = self.client.upload(shot["pose_reference"]) if shot.get("pose_reference") else None
            return self.workflows.image(
                prompt, shot["negative_prompt"], shot["seed"], f"drama/{job['id']}", uploaded, other, pose
            )[0]

        return self.queue.run(job, lambda j: self.execute(j, graph, "9", "image"), retry)
