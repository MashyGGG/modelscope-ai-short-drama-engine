import pytest
import requests

from core.common import GateError, config
from core.engine import Engine
from core.model_manager import ModelManager
from tests.conftest import approve


def test_unload_waits_for_observed_memory_instead_of_http_ack(monkeypatch):
    class Client:
        stats_calls = 0
        free_calls = 0

        def require_idle(self):
            pass

        def free(self):
            self.free_calls += 1

        def stats(self):
            self.stats_calls += 1
            free = 2 if self.stats_calls == 1 else 23
            return {"devices": [{"type": "cuda", "vram_total": 24 * 2**30, "vram_free": free * 2**30}]}

    client = Client()
    monkeypatch.setattr("core.model_manager.time.sleep", lambda _: None)
    manager = ModelManager(client, config("hardware"), "http://127.0.0.1:9880")
    manager.unload()
    assert client.free_calls == 1
    assert client.stats_calls == 2


def test_external_tts_blocks_comfy_inference(monkeypatch):
    class Response:
        status_code = 200

    monkeypatch.setattr("core.model_manager.requests.get", lambda *a, **k: Response())
    manager = ModelManager(None, config("hardware"), "http://127.0.0.1:9880")
    with pytest.raises(GateError, match="TTS port"):
        manager.assert_tts_stopped()
    monkeypatch.setattr(
        "core.model_manager.requests.get", lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError())
    )
    manager.assert_tts_stopped()


def test_completed_asset_survives_worker_crash_before_success_commit(workspace, media):
    _, state, queue = workspace
    job = state.job("EP001", "CHAR_001", "characters", {"seed": 1}, "w1", "m1")
    state.update_job(job["id"], status="generating", attempts=1)
    asset = state.add_asset("CHAR_001", "identity", media["image"], job["id"])
    resumed = state.rows("SELECT * FROM generation_jobs WHERE id=?", (job["id"],))[0]

    def forbidden(_):
        pytest.fail("Recovered output was regenerated")

    assert queue.run(resumed, forbidden) == [asset]
    assert state.rows("SELECT status FROM generation_jobs")[0]["status"] == "succeeded"


def test_audio_cannot_start_before_video_approval(workspace, media, monkeypatch):
    _, state, _ = workspace
    for character in state.entity("episodes", "EP001")["characters"]:
        approve(state, character["character_id"], "identity", media["image"])
    for shot in state.shots("EP001"):
        approve(state, shot["shot_id"], "image", media["image"])
    monkeypatch.setattr("core.engine.audio_stage", lambda *a: pytest.fail("TTS started before video review"))
    with pytest.raises(GateError, match="approved video"):
        Engine(state).run("EP001", "audio")


def test_no_gpu_environment_is_not_allowed_to_generate(monkeypatch):
    monkeypatch.setattr("core.model_manager.snapshot", lambda: {"gpus": [], "ram_available_mb": 32000})
    with pytest.raises(GateError, match="No NVIDIA GPU"):
        ModelManager(None, config("hardware"), "").check_resources()
