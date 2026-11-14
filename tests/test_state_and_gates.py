import portalocker
import pytest
from PIL import Image

from core.common import GateError
from core.engine import Engine
from core.schema import EpisodePlan
from production.character_manager import CharacterManager
from scripts.create_example import example
from tests.conftest import approve


def test_plan_contract_and_immutable_episode(workspace):
    _, state, _ = workspace
    plan = example()
    assert sum(s.duration for s in plan.shots) == 45
    assert len(plan.shots) == 12
    state.import_plan(plan)
    changed = plan.model_copy(update={"title": "changed"})
    with pytest.raises(GateError, match="different inputs"):
        state.import_plan(changed)
    bad = plan.model_dump()
    bad["shots"][0]["scene_id"] = "unknown"
    with pytest.raises(ValueError, match="references"):
        EpisodePlan.model_validate(bad)


def test_no_generation_before_identity_approval(workspace):
    _, state, _ = workspace
    engine = Engine(state)
    with pytest.raises(GateError, match="locked"):
        engine.run("EP001", "images")
    assert state.rows("SELECT * FROM generation_jobs") == []


def test_image_approval_controls_video_and_replacement_invalidates(workspace, media):
    _, state, _ = workspace
    for character in example().characters:
        approve(state, character.character_id, "identity", media["image"])
    image = state.add_asset("EP001_SH002", "image", media["image"])
    with pytest.raises(GateError, match="approved image"):
        state.selected("EP001_SH002", "image")
    state.review(image, "approved", "human")
    assert state.rows("SELECT status FROM shots WHERE id='EP001_SH002'")[0]["status"] == "image_approved"
    approve(state, "EP001_SH002", "video", media["video"])
    approve(state, "CHAR_002", "identity", media["image"])
    assert state.selected("EP001_SH002", "image", required=False) is None
    assert state.selected("EP001_SH002", "video", required=False) is None
    assert len(state.rows("SELECT * FROM quality_reviews")) == 6


def test_identity_edit_unlocks_and_cannot_self_approve(workspace, media):
    _, state, _ = workspace
    approve(state, "CHAR_001", "identity", media["image"])
    manager = CharacterManager(state)
    with pytest.raises(GateError, match="only be locked"):
        manager.update("CHAR_001", {"identity_locked": True})
    updated = manager.update("CHAR_001", {"age": 29})
    assert not updated["identity_locked"]
    with pytest.raises(GateError):
        manager.references(["CHAR_001"])


def test_modified_approved_files_are_rejected(workspace, tmp_path, media):
    _, state, _ = workspace
    path = tmp_path / "identity.png"
    path.write_bytes(media["image"].read_bytes())
    asset = approve(state, "CHAR_001", "identity", path)
    Image.new("RGB", (256, 448), "red").save(path)
    with pytest.raises(GateError, match="changed"):
        state.selected("CHAR_001", "identity")
    with pytest.raises(GateError, match="changed"):
        state.review(asset, "approved", "human")


def test_machine_gpu_lock_excludes_second_worker(workspace):
    _, _, queue = workspace
    with queue.lock():
        with pytest.raises(portalocker.exceptions.LockException):
            with queue.lock():
                pytest.fail("second worker acquired GPU")
    with queue.lock():
        pass


def test_real_person_requires_likeness_authorization(workspace, media):
    _, state, _ = workspace
    character = state.entity("characters", "CHAR_001")
    character["fictional"] = False
    state.set_character(character)
    with pytest.raises(GateError, match="authorization"):
        CharacterManager(state).import_reference("CHAR_001", media["image"], authorized=True)


def test_shared_characters_reuse_reference_across_episodes(workspace, media):
    _, state, _ = workspace
    asset = approve(state, "CHAR_001", "identity", media["image"])
    other = example().model_dump()
    other["episode_id"] = "EP002"
    for shot in other["shots"]:
        shot["shot_id"] = shot["shot_id"].replace("EP001", "EP002")
        for dialogue in shot["dialogues"]:
            dialogue["dialogue_id"] = dialogue["dialogue_id"].replace("EP001", "EP002")
    state.import_plan(EpisodePlan.model_validate(other))
    assert state.selected("CHAR_001", "identity")["id"] == asset
    assert len(CharacterManager(state).used_in("CHAR_001")) == 10
    assert len(state.rows("SELECT * FROM characters")) == 3
