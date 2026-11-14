import json
import shutil
import os

import pytest

from core.common import GateError, config
from core.report import build_report
from production.episode_renderer import EpisodeRenderer
from production.quality_checker import QualityChecker
from production.subtitle_generator import SubtitleGenerator, safe_ass, stamp
from production.voice_generator import GPTSoVITSAdapter, VoiceGenerator
from tests.conftest import add_voices, approve


class SyntheticTTS:
    def __init__(self, source):
        self.source = source
        self.calls = 0

    def synthesize(self, dialogue, profile, destination):
        self.calls += 1
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.source, destination)
        return {"mode": "SYNTHETIC TEST TONE, NOT AI SPEECH"}


def test_audio_timeline_uses_actual_wav_duration_and_blocks_overflow(workspace, media):
    _, state, queue = workspace
    add_voices(state, media)
    tts = SyntheticTTS(media["audio"])
    generator = VoiceGenerator(state, queue, tts)
    for shot in state.shots("EP001"):
        for dialogue in shot["dialogues"]:
            assets = generator.line("EP001", dialogue)
            state.review(assets[0], "approved", "offline-human")
            assert generator.line("EP001", dialogue) == assets
    assert tts.calls == 8
    timeline = SubtitleGenerator().timeline(state, "EP001")
    assert timeline[0]["start_time"] == 3.95
    assert timeline[0]["end_time"] == 4.55
    assert all(t["end_time"] - t["start_time"] == pytest.approx(0.6) for t in timeline)
    with state.db:
        row = state.rows("SELECT id,data FROM shots WHERE id='EP001_SH002'")[0]
        shot = json.loads(row["data"])
        shot["duration"] = 0.5
        state.db.execute("UPDATE shots SET data=? WHERE id=?", (json.dumps(shot), row["id"]))
    with pytest.raises(GateError, match="beyond"):
        SubtitleGenerator().timeline(state, "EP001")


def test_gpt_api_payload_and_emotion_controls_are_supported_fields(media, tmp_path):
    class Response:
        content = media["audio"].read_bytes()

        def raise_for_status(self):
            pass

    class Session:
        def post(self, url, json, timeout):
            self.url, self.payload = url, json
            return Response()

    from core.common import file_hash

    session = Session()
    profile = {
        "reference_path": str(media["reference"]),
        "sha256": file_hash(media["reference"]),
        "authorized": True,
        "prompt_text": "测试",
        "emotion_profiles": {"calm": {"speed_factor": 0.95, "temperature": 0.8}},
    }
    dialogue = {"dialogue_id": "D1", "text": "你好", "emotion": "calm"}
    GPTSoVITSAdapter("http://127.0.0.1:9880", session).synthesize(dialogue, profile, tmp_path / "voice.wav")
    assert session.url.endswith("/tts")
    assert session.payload["text_lang"] == "zh"
    assert session.payload["speed_factor"] == 0.95
    assert session.payload["streaming_mode"] is False
    assert "emotion" not in session.payload
    assert QualityChecker().audio(tmp_path / "voice.wav", "D1")["duration"] == pytest.approx(0.6)


def test_subtitle_escaping_and_safe_wrapping(tmp_path):
    timeline = [
        {"dialogue_id": "D1", "character_name": "林辰", "text": "你好{\\pos(0,0)}", "start_time": 0.2, "end_time": 0.8}
    ]
    settings = config("production")["render"]
    SubtitleGenerator().write(timeline, tmp_path, settings)
    content = (tmp_path / "subtitles.ass").read_text(encoding="utf-8")
    assert "{\\pos" not in content
    assert "你好" in content
    assert stamp(3599.9996) == "01:00:00,000"
    assert stamp(3.95, True) == "0:00:03.95"
    assert safe_ass("{x}") == "｛x｝"


def test_real_ffmpeg_45s_composition_with_synthetic_inputs(workspace, media):
    root, state, queue = workspace
    add_voices(state, media)
    for character in state.entity("episodes", "EP001")["characters"]:
        approve(state, character["character_id"], "identity", media["image"])
    generator = VoiceGenerator(state, queue, SyntheticTTS(media["audio"]))
    for shot in state.shots("EP001"):
        approve(state, shot["shot_id"], "image", media["image"])
        quality = QualityChecker().video(media["video"], shot["shot_id"])
        approve(state, shot["shot_id"], "video", media["video"], {"quality": quality})
        for dialogue in shot["dialogues"]:
            asset = generator.line("EP001", dialogue)[0]
            state.review(asset, "approved", "offline-human")
    settings = {**config("production")["render"], "width": 216, "height": 384, "font_size": 12, "margin_v": 30}
    renderer = EpisodeRenderer(state, queue, settings)
    assets = renderer.render("EP001")
    asset = state.asset(assets[0])
    assert asset["status"] == "review_required"
    quality = json.loads(asset["metadata"])["quality"]
    assert quality["duration"] == pytest.approx(45, abs=0.05)
    assert quality["fps"] == 24
    assert quality["width"] == 216 and quality["height"] == 384
    assert not quality["errors"]
    assert renderer.render("EP001") == assets
    assert len(state.rows("SELECT * FROM render_jobs")) == 1
    report = build_report(state, "EP001", acceptance=True)
    assert report["gpu_e2e_status"] == "NOT_PASSED"
    assert any("A10" in message for message in report["acceptance_errors"])
    assert report["output_size_bytes"] > 1024
    assert root in __import__("pathlib").Path(asset["path"]).parents
    if os.environ.get("DRAMA_TEST_ARTIFACTS"):
        directory = __import__("pathlib").Path(os.environ["DRAMA_TEST_ARTIFACTS"])
        directory.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(asset["path"], directory / "SYNTHETIC_45s_TEST_ONLY.mp4")
        (directory / "quality.json").write_text(json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8")
        (directory / "NOTICE.txt").write_text(
            "Synthetic patterns and tones for FFmpeg testing only. NOT an AI-generated drama. NOT GPU E2E. 216x384, 45 seconds.\n",
            encoding="utf-8",
        )


def test_empty_corrupt_and_black_media_are_reported(tmp_path):
    from PIL import Image

    checker = QualityChecker()
    corrupt = tmp_path / "bad.png"
    corrupt.write_bytes(b"not an image")
    assert checker.image(corrupt, "SHOT_BAD")["errors"]
    Image.new("RGB", (256, 256), "black").save(corrupt)
    assert "black" in checker.image(corrupt, "SHOT_BAD")["errors"][0]
    bad_video = tmp_path / "bad.mp4"
    bad_video.write_bytes(b"x")
    assert checker.video(bad_video, "SHOT_BAD")["owner_id"] == "SHOT_BAD"
    assert checker.video(bad_video, "SHOT_BAD")["errors"]
