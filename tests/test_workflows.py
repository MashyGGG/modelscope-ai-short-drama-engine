from copy import deepcopy

import pytest

from core.common import ROOT, GateError, read_json
from core.workflow_manager import WorkflowManager, import_ui, to_ui, validate
from scripts.validate_upstream import audit


def test_upstream_import_then_api_generation():
    original = read_json(ROOT / "docs/upstream/templates/templates/video_wan2_2_5B_ti2v.json")
    graph = import_ui(original)
    assert graph["55"]["inputs"]["start_image"] == ["56", 0]
    assert graph["3"]["inputs"]["model"] == ["48", 0]
    for path in (ROOT / "workflows/api").glob("*.json"):
        api = read_json(path)
        audit(api)
        assert import_ui(to_ui(api)) == api


def test_video_i2v_exact_bindings_and_new_codec_schema():
    graph, output = WorkflowManager().video("drama/approved.png", "slowly turns", "bad face", 1234, "EP001")
    assert output == "58"
    assert graph["56"]["inputs"]["image"] == "drama/approved.png"
    assert graph["55"]["inputs"] == {
        "vae": ["39", 0],
        "start_image": ["56", 0],
        "width": 704,
        "height": 1280,
        "length": 49,
        "batch_size": 1,
    }
    assert graph["6"]["inputs"]["text"] == "slowly turns"
    assert graph["3"]["inputs"]["seed"] == 1234
    assert graph["57"]["inputs"]["fps"] == 16
    assert graph["58"]["inputs"]["format.codec"] == "h264"
    assert "codec" not in graph["58"]["inputs"]
    audit(graph)


def test_faceid_pose_and_appearance_fields_are_upstream_inputs():
    graph, _ = WorkflowManager().image("prompt", "bad", 3, "test", [{"image": "face.png"}], ["suit.png"], "pose.png")
    audit(graph)
    assert graph["20"]["inputs"]["preset"] == "FACEID PLUS V2"
    assert graph["20"]["inputs"]["provider"] == "CPU"
    assert graph["3"]["inputs"]["positive"] == ["102", 0]
    assert graph["70"]["inputs"]["preset"] == "PLUS (high strength)"


def test_multi_character_requires_masks():
    manager = WorkflowManager()
    with pytest.raises(GateError, match="alpha masks"):
        manager.image("", "", 1, "test", [{"image": "a.png"}, {"image": "b.png"}])
    graph, _ = manager.image(
        "", "", 1, "test", [{"image": "a.png", "mask": "ma.png"}, {"image": "b.png", "mask": "mb.png"}]
    )
    assert graph["32"]["inputs"]["attn_mask"] == ["31", 1]
    assert graph["35"]["inputs"]["attn_mask"] == ["34", 1]
    audit(graph)


def test_reject_bad_links_cycles_and_frames():
    graph = read_json(ROOT / "workflows/api/wan22_i2v.json")
    broken = deepcopy(graph)
    broken["55"]["inputs"]["vae"] = ["39", 8]
    with pytest.raises(ValueError, match="slot"):
        validate(broken)
    broken = deepcopy(graph)
    broken["48"]["inputs"]["model"] = ["48", 0]
    with pytest.raises(ValueError, match="cycle"):
        validate(broken)
    with pytest.raises(ValueError, match="4n"):
        WorkflowManager().video("a.png", "", "", 1, "x", frames=50)
