import uuid
import secrets
from pathlib import Path

from core.common import ROOT, digest, file_hash
from production.image_generator import ImageGenerator


class VideoGenerator(ImageGenerator):
    def shot(
        self,
        episode_id,
        shot,
        regenerate=False,
        retry=False,
        low_resolution=False,
        frames=None,
        seed_override=None,
        resume_job=None,
    ):
        shot = dict(shot)
        if regenerate:
            shot["seed"] = secrets.randbits(48)
        if seed_override is not None:
            shot["seed"] = seed_override
        self.characters.references(shot["characters"])
        image = self.state.selected(shot["shot_id"], "image")
        settings = self.workflows.settings["video"]
        frames = frames if frames is not None else settings["frames"]
        # Validate requested parameters even if a matching task already exists.
        self.workflows.video("placeholder.png", "", "", shot["seed"], "validate", low_resolution, frames)
        params = {
            "image_id": image["id"],
            "image_sha256": image["sha256"],
            "motion": shot["motion_prompt"],
            "negative": shot["negative_prompt"],
            "seed": shot["seed"],
            "video": settings,
            "low_resolution": low_resolution,
            "frames": frames,
        }
        if regenerate:
            params["candidate"] = uuid.uuid4().hex
        workflow_version = digest(
            {
                "template": file_hash(ROOT / "workflows/api/wan22_i2v.json"),
                "builder": file_hash(ROOT / "core/workflow_manager.py"),
                "generator": file_hash(Path(__file__)),
                "shared_executor": file_hash(ROOT / "production/image_generator.py"),
            }
        )
        job = self.prepare_job(episode_id, shot["shot_id"], "videos", params, workflow_version, resume_job)

        def graph():
            uploaded = self.client.upload(image["path"])
            return self.workflows.video(
                uploaded,
                shot["motion_prompt"],
                shot["negative_prompt"],
                shot["seed"],
                f"drama/{job['id']}",
                low_resolution,
                frames,
            )[0]

        return self.queue.run(job, lambda j: self.execute(j, graph, "58", "video"), retry)
