import json

import pytest

from core.comfy_client import ComfyClient
from core.common import GateError
from core.workflow_manager import WorkflowManager, validate
from production.image_generator import ImageGenerator
from production.video_generator import VideoGenerator
from tests.conftest import approve


def generators(workspace, comfy_server):
    _, state, queue = workspace
    url, _ = comfy_server
    client = ComfyClient(url, timeout=2, poll_interval=0.01)
    workflows = WorkflowManager()
    workflows.settings["video"]["low_resolution"] = [128, 224]
    workflows.settings["image"].update(width=256, height=448)
    return ImageGenerator(state, queue, client, workflows), VideoGenerator(state, queue, client, workflows)


def test_png_history_download_and_success_is_not_regenerated(workspace, comfy_server):
    _, state, _ = workspace
    _, server = comfy_server
    images, _ = generators(workspace, comfy_server)
    first = images.character("EP001", "CHAR_001")
    assert state.asset(first[0])["status"] == "review_required"
    assert state.asset(first[0])["path"].endswith("output.png")
    assert len(server["prompts"]) == 1
    assert images.character("EP001", "CHAR_001") == first
    assert len(server["prompts"]) == 1
    new = images.character("EP001", "CHAR_001", regenerate=True)
    assert new != first
    assert len(server["prompts"]) == 2


def test_real_uploaded_image_is_bound_to_i2v_and_mp4_is_verified(workspace, comfy_server, media):
    _, state, _ = workspace
    _, server = comfy_server
    _, videos = generators(workspace, comfy_server)
    shot = state.shots("EP001")[1]
    approve(state, "CHAR_002", "identity", media["image"])
    with pytest.raises(GateError, match="approved image"):
        videos.shot("EP001", shot, low_resolution=True)
    approve(state, shot["shot_id"], "image", media["image"])
    output = videos.shot("EP001", shot, low_resolution=True)
    graph = server["prompts"][-1]["prompt"]
    assert graph["56"]["inputs"]["image"] == "drama/uploaded_1.png"
    assert media["image"].read_bytes() in server["uploads"][0]
    assert graph["6"]["inputs"]["text"] == shot["motion_prompt"]
    assert graph["55"]["inputs"]["start_image"] == ["56", 0]
    quality = json.loads(state.asset(output[0])["metadata"])["quality"]
    assert quality["frames"] == 49
    assert quality["fps"] == 16
    assert quality["duration"] == pytest.approx(49 / 16, abs=0.01)
    assert not quality["errors"]
    assert videos.shot("EP001", shot, low_resolution=True) == output
    assert len(server["prompts"]) == 1


def test_timeout_resume_reattaches_without_second_submission(workspace, comfy_server):
    _, state, _ = workspace
    _, server = comfy_server
    images, _ = generators(workspace, comfy_server)
    server["defer"] = True
    images.client.timeout = 0.02
    with pytest.raises(TimeoutError):
        images.character("EP001", "CHAR_001")
    job = state.rows("SELECT * FROM generation_jobs")[0]
    assert job["status"] == "generating"
    assert job["prompt_id"] == server["prompts"][0]["prompt_id"]
    server["history"][job["prompt_id"]] = server["deferred_history"]
    server["pending"] = []
    result = images.character("EP001", "CHAR_001", retry=True)
    assert state.asset(result[0])["status"] == "review_required"
    assert len(server["prompts"]) == 1
    assert state.rows("SELECT attempts FROM generation_jobs")[0]["attempts"] == 2


def test_oom_records_failed_shot_and_retries_only_that_job(workspace, comfy_server):
    _, state, _ = workspace
    _, server = comfy_server
    images, _ = generators(workspace, comfy_server)
    server["fail_next"] = True
    with pytest.raises(RuntimeError, match="out of memory"):
        images.character("EP001", "CHAR_001")
    job = state.rows("SELECT * FROM generation_jobs")[0]
    assert job["status"] == "failed"
    assert "384x640" in job["error"]
    with pytest.raises(GateError, match="--resume"):
        images.character("EP001", "CHAR_001")
    images.character("EP001", "CHAR_002")
    images.character("EP001", "CHAR_001", retry=True)
    assert len(server["prompts"]) == 3
    assert len(state.rows("SELECT * FROM generation_jobs")) == 2
    assert len(state.rows("SELECT * FROM generation_jobs WHERE status='succeeded'")) == 2


def test_failed_regeneration_reuses_recorded_seed_and_candidate_on_resume(workspace, comfy_server):
    _, state, _ = workspace
    _, server = comfy_server
    images, _ = generators(workspace, comfy_server)
    server["fail_next"] = True
    with pytest.raises(RuntimeError):
        images.character("EP001", "CHAR_001", regenerate=True, seed_override=4321)
    job = state.rows("SELECT * FROM generation_jobs")[0]
    params = json.loads(job["input_params"])
    result = images.character("EP001", "CHAR_001", retry=True, seed_override=params["seed"], resume_job=job)
    assert state.asset(result[0])["job_id"] == job["id"]
    assert len(state.rows("SELECT * FROM generation_jobs")) == 1
    assert all(p["prompt"]["3"]["inputs"]["seed"] == 4321 for p in server["prompts"])


def test_runtime_node_model_and_dynamic_choice_validation(comfy_server):
    url, data = comfy_server
    graph, _ = WorkflowManager().video("ref.png", "", "", 1, "x")
    validate(graph, ComfyClient(url).info())
    graph["58"]["inputs"]["format.codec"] = "not-a-codec"
    with pytest.raises(ValueError, match="dynamic choice"):
        validate(graph, data["info"])
    graph["58"]["inputs"]["format.codec"] = "h264"
    del data["info"]["Wan22ImageToVideoLatent"]
    with pytest.raises(GateError, match="Missing ComfyUI node"):
        validate(graph, data["info"])
