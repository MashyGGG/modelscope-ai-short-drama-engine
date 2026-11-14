import logging
import time
from pathlib import Path

import portalocker

from core.common import GateError, canonical, file_hash
from core.model_manager import oom_advice

LOG = logging.getLogger(__name__)


class TaskQueue:
    """Durable per-stage SQLite queues, serialized by one machine-wide OS file lock."""

    def __init__(self, state, lock_path):
        self.state = state
        self.lock_path = Path(lock_path)
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)

    def lock(self):
        # OS releases the lock if a worker is killed; no stale SQLite lease needs clearing.
        return portalocker.Lock(str(self.lock_path), timeout=0)

    def run(self, job, operation, retry_failed=False):
        existing = self.state.rows("SELECT * FROM assets WHERE job_id=?", (job["id"],))
        if (
            job["status"] == "generating"
            and existing
            and all(Path(a["path"]).is_file() and file_hash(a["path"]) == a["sha256"] for a in existing)
        ):
            self.state.update_job(
                job["id"],
                status="succeeded",
                finished_at=time.time(),
                error=None,
                report=canonical([a["id"] for a in existing]),
            )
            LOG.info("Recovered completed outputs after interrupted state commit: %s", job["id"])
            return [a["id"] for a in existing]
        if job["status"] == "succeeded":
            assets = self.state.rows("SELECT * FROM assets WHERE job_id=?", (job["id"],))
            if assets and all(Path(a["path"]).is_file() and file_hash(a["path"]) == a["sha256"] for a in assets):
                LOG.info("Reusing successful job %s", job["id"])
                return [a["id"] for a in assets]
            raise GateError(f"Successful job {job['id']} has missing/modified outputs; regenerate explicitly")
        if job["status"] == "failed" and not retry_failed:
            raise GateError(f"{job['id']} failed: {job['error']}; use --resume or --retry")
        started = time.time()
        self.state.update_job(
            job["id"], status="generating", started_at=started, error=None, attempts=job["attempts"] + 1
        )
        try:
            result = operation(job)
        except BaseException as exc:
            elapsed = time.time() - started + job["elapsed"]
            # A timeout/interrupt retains prompt_id for reattachment on resume.
            status = "generating" if isinstance(exc, (TimeoutError, KeyboardInterrupt)) else "failed"
            self.state.update_job(
                job["id"], status=status, finished_at=time.time(), elapsed=elapsed, error=oom_advice(exc)
            )
            LOG.exception("Job failed owner=%s id=%s", job["owner_id"], job["id"])
            raise
        self.state.update_job(
            job["id"],
            status="succeeded",
            finished_at=time.time(),
            elapsed=time.time() - started + job["elapsed"],
            report=canonical(result),
        )
        return result

    def report(self, episode_id):
        jobs = self.state.rows("SELECT * FROM generation_jobs WHERE episode_id=?", (episode_id,))
        stages = {}
        for job in jobs:
            stages[job["stage"]] = stages.get(job["stage"], 0) + job["elapsed"]
        executions = {}
        for job in jobs:
            key = (job["owner_id"], job["stage"])
            executions[key] = executions.get(key, 0) + job["attempts"]
        return {
            "stage_seconds": stages,
            "total_generation_seconds": sum(stages.values()),
            "failed_shots": sorted({j["owner_id"] for j in jobs if j["status"] == "failed"}),
            "regeneration_count": sum(max(0, count - 1) for count in executions.values()),
            "jobs": [
                {k: j[k] for k in ("id", "owner_id", "stage", "status", "attempts", "elapsed", "error", "prompt_id")}
                for j in jobs
            ],
        }
