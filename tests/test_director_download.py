import hashlib

import pytest

from core.common import GateError
from director.script_writer import ScriptWriter
from scripts.create_example import example
from scripts.download_models import download


def test_director_validates_provider_output_and_requested_constraints(monkeypatch):
    monkeypatch.setenv("DRAMA_LLM_URL", "https://provider.invalid/v1/chat/completions")
    monkeypatch.setenv("DRAMA_LLM_MODEL", "test-director")

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": example().model_dump_json()}}]}

    calls = []

    def post(url, **kwargs):
        calls.append(kwargs["json"])
        return Response()

    monkeypatch.setattr("director.script_writer.requests.post", post)
    plan = ScriptWriter().generate("离婚当天，我继承了千亿集团", "outline")
    assert len(plan.shots) == 12
    assert "precise lip sync" in calls[0]["messages"][0]["content"]
    with pytest.raises(GateError, match="wrong character count"):
        ScriptWriter().generate("title", "outline", character_count=2)


def test_unknown_outline_without_provider_is_not_faked(monkeypatch):
    monkeypatch.delenv("DRAMA_LLM_URL", raising=False)
    with pytest.raises(GateError, match="DRAMA_LLM_URL"):
        ScriptWriter().generate("新故事", "新大纲")


def test_download_resumes_range_and_verifies_content(tmp_path, monkeypatch):
    content = b"model-test-content"
    path = tmp_path / "model.safetensors"
    path.with_suffix(".safetensors.part").write_bytes(content[:5])
    item = {
        "key": "test",
        "repo": "org/repo",
        "file": "model.safetensors",
        "revision": "abc",
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    calls = []

    class Response:
        status_code = 206
        headers = {"Content-Range": f"bytes 5-{len(content) - 1}/{len(content)}"}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def raise_for_status(self):
            pass

        def iter_content(self, _):
            yield content[5:]

    def get(url, **kwargs):
        calls.append(kwargs["headers"])
        return Response()

    monkeypatch.setattr("scripts.download_models.requests.get", get)
    download(item, path)
    assert path.read_bytes() == content
    assert calls == [{"Range": "bytes=5-"}]
    download(item, path)
    assert len(calls) == 1
