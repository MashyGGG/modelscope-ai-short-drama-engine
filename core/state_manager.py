import json
import sqlite3
import time
import uuid
from pathlib import Path

from core.common import ROOT, GateError, canonical, digest, file_hash
from core.schema import ReviewStatus


class StateManager:
    def __init__(self, path=None):
        path = Path(path or ROOT / "database/engine.sqlite")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path.resolve()
        self.db = sqlite3.connect(path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript((ROOT / "database/schema.sql").read_text(encoding="utf-8"))

    def close(self):
        self.db.close()

    def rows(self, sql, args=()):
        return [dict(row) for row in self.db.execute(sql, args)]

    def entity(self, table, entity_id):
        if table not in {"projects", "episodes", "characters", "scenes", "shots", "dialogues", "voice_profiles"}:
            raise ValueError("unknown table")
        row = self.db.execute(f"SELECT data FROM {table} WHERE id=?", (entity_id,)).fetchone()
        if row is None:
            raise GateError(f"Unknown {table}: {entity_id}")
        return json.loads(row[0])

    def import_plan(self, plan):
        data = plan.model_dump(mode="json")
        with self.db:
            old = self.db.execute("SELECT data FROM episodes WHERE id=?", (plan.episode_id,)).fetchone()
            if old:
                from core.schema import EpisodePlan

                if EpisodePlan.model_validate(json.loads(old[0])).model_dump(mode="json") == data:
                    return
                raise GateError(
                    "Episode exists with different inputs; use a new episode_id to preserve reviews/history"
                )
            self.db.execute(
                "INSERT OR IGNORE INTO projects VALUES (?,?)", (plan.project_id, canonical({"title": plan.title}))
            )
            self.db.execute("INSERT INTO episodes VALUES (?,?,?)", (plan.episode_id, plan.project_id, canonical(data)))
            for c in plan.characters:
                prior = self.db.execute("SELECT data FROM characters WHERE id=?", (c.character_id,)).fetchone()
                if prior:
                    saved = json.loads(prior[0])
                    candidate = c.model_dump()
                    candidate["identity_locked"] = saved["identity_locked"]
                    if saved != candidate:
                        raise GateError(
                            f"Shared character {c.character_id} differs; edit character explicitly or choose another ID"
                        )
                else:
                    self.db.execute("INSERT INTO characters VALUES (?,?)", (c.character_id, canonical(c.model_dump())))
            for scene in plan.scenes:
                self.db.execute(
                    "INSERT INTO scenes VALUES (?,?,?)",
                    (f"{plan.episode_id}:{scene.scene_id}", plan.episode_id, canonical(scene.model_dump())),
                )
            for pos, shot in enumerate(plan.shots):
                self.db.execute(
                    "INSERT INTO shots(id,episode_id,position,data) VALUES (?,?,?,?)",
                    (shot.shot_id, plan.episode_id, pos, canonical(shot.model_dump())),
                )
                for dialogue in shot.dialogues:
                    self.db.execute(
                        "INSERT INTO dialogues VALUES (?,?,?)",
                        (dialogue.dialogue_id, shot.shot_id, canonical(dialogue.model_dump())),
                    )

    def shots(self, episode_id):
        return [
            json.loads(row[0])
            for row in self.db.execute("SELECT data FROM shots WHERE episode_id=? ORDER BY position", (episode_id,))
        ]

    def set_character(self, character):
        with self.db:
            self.db.execute(
                "UPDATE characters SET data=? WHERE id=?", (canonical(character), character["character_id"])
            )

    def invalidate(self, owner_id, kinds):
        with self.db:
            for kind in kinds:
                self.db.execute("UPDATE assets SET selected=0 WHERE owner_id=? AND kind=?", (owner_id, kind))
            if "image" in kinds:
                self.db.execute("UPDATE shots SET status='pending' WHERE id=?", (owner_id,))

    def add_asset(self, owner_id, kind, path, job_id=None, metadata=None):
        path = Path(path).resolve()
        asset_id = "ASSET_" + uuid.uuid4().hex[:20]
        with self.db:
            self.db.execute(
                "INSERT INTO assets VALUES (?,?,?,?,?,?,?,0,?,?)",
                (
                    asset_id,
                    owner_id,
                    kind,
                    job_id,
                    str(path),
                    file_hash(path),
                    "review_required",
                    canonical(metadata or {}),
                    time.time(),
                ),
            )
        return asset_id

    def asset(self, asset_id):
        rows = self.rows("SELECT * FROM assets WHERE id=?", (asset_id,))
        if not rows:
            raise GateError(f"Unknown asset: {asset_id}")
        ReviewStatus(rows[0]["status"])
        return rows[0]

    def selected(self, owner_id, kind, required=True):
        rows = self.rows(
            "SELECT * FROM assets WHERE owner_id=? AND kind=? AND selected=1 AND status='approved'", (owner_id, kind)
        )
        if not rows:
            if required:
                raise GateError(f"{owner_id}: approved {kind} required; run review-list and review")
            return None
        asset = rows[0]
        if not Path(asset["path"]).is_file() or file_hash(asset["path"]) != asset["sha256"]:
            raise GateError(f"{owner_id}: {kind} changed or missing after review; import and review again")
        return asset

    def review(self, asset_id, decision, reviewer, notes=""):
        if decision not in {"approved", "rejected"} or not reviewer.strip():
            raise ValueError("review requires decision and named human reviewer")
        asset = self.asset(asset_id)
        if asset["status"] not in {"review_required", "approved", "rejected"}:
            raise GateError("asset cannot be reviewed in its current state")
        if file_hash(asset["path"]) != asset["sha256"]:
            raise GateError("asset changed since generation; re-import it")
        if decision == "approved":
            report = json.loads(asset["metadata"]).get("quality", {})
            if report.get("errors"):
                raise GateError(f"Technical quality failed: {report['errors']}")
        with self.db:
            if decision == "approved":
                self.db.execute(
                    "UPDATE assets SET selected=0 WHERE owner_id=? AND kind=?", (asset["owner_id"], asset["kind"])
                )
            self.db.execute(
                "UPDATE assets SET status=?,selected=? WHERE id=?", (decision, int(decision == "approved"), asset_id)
            )
            self.db.execute(
                "INSERT INTO quality_reviews(asset_id,reviewer,decision,notes,created_at) VALUES (?,?,?,?,?)",
                (asset_id, reviewer, decision, notes, time.time()),
            )
            if asset["kind"] in {"image", "video"}:
                status = f"{asset['kind']}_approved" if decision == "approved" else "rejected"
                self.db.execute("UPDATE shots SET status=? WHERE id=?", (status, asset["owner_id"]))
        if asset["kind"] == "identity":
            character = self.entity("characters", asset["owner_id"])
            character["identity_locked"] = decision == "approved"
            self.set_character(character)
            self.invalidate_character_shots(asset["owner_id"])
        if asset["kind"] == "image":
            self.invalidate(asset["owner_id"], ["video"])
        if asset["kind"].startswith(("wardrobe_", "expression_")):
            self.invalidate_character_shots(asset["owner_id"])
        if asset["kind"] == "scene":
            episode_id, scene_id = asset["owner_id"].split(":", 1)
            for shot in self.shots(episode_id):
                if shot["scene_id"] == scene_id:
                    self.invalidate(shot["shot_id"], ["image", "video"])
        if asset["kind"] in {"image", "video", "audio", "scene", "identity"} or asset["kind"].startswith("wardrobe_"):
            # Approved finals are previews of exact inputs, and become stale whenever an upstream review changes.
            with self.db:
                self.db.execute("UPDATE assets SET selected=0 WHERE kind='final'")

    def invalidate_character_shots(self, character_id):
        for row in self.rows("SELECT id,data FROM shots"):
            if character_id in json.loads(row["data"])["characters"]:
                self.invalidate(row["id"], ["image", "video"])

    def job(self, episode_id, owner_id, stage, params, workflow_version, model_version):
        fingerprint = digest({"params": params, "workflow": workflow_version, "model": model_version})
        job_id = "JOB_" + digest([stage, owner_id, fingerprint])[:24]
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO generation_jobs(id,episode_id,owner_id,stage,input_hash,input_params,workflow_version,model_version,status) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    episode_id,
                    owner_id,
                    stage,
                    fingerprint,
                    canonical(params),
                    workflow_version,
                    model_version,
                    "pending",
                ),
            )
        return self.rows("SELECT * FROM generation_jobs WHERE id=?", (job_id,))[0]

    def update_job(self, job_id, **values):
        allowed = {"status", "prompt_id", "attempts", "started_at", "finished_at", "elapsed", "error", "report"}
        if not set(values) <= allowed:
            raise ValueError("unknown job fields")
        with self.db:
            self.db.execute(
                "UPDATE generation_jobs SET " + ",".join(f"{k}=?" for k in values) + " WHERE id=?",
                (*values.values(), job_id),
            )
