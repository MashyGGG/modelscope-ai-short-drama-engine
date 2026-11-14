import os
import signal
import subprocess
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

import requests

from core.common import ROOT, GateError, digest, file_hash, read_json, runtime_root, write_json
from core.resource_monitor import ResourceMonitor
from production.quality_checker import QualityChecker, command


class TTSAdapter(Protocol):
    def synthesize(self, dialogue: dict, profile: dict, destination: Path) -> dict: ...


def effective_profile(state, voice_id):
    profile = state.entity("voice_profiles", voice_id)
    if not profile.get("authorized") or file_hash(profile["reference_path"]) != profile["sha256"]:
        raise GateError(f"{voice_id}: authorized reference is missing or changed")
    if bool(profile.get("gpt_weights")) != bool(profile.get("sovits_weights")):
        raise GateError("Specify both gpt_weights and sovits_weights, or omit both for the official pretrained v2 pair")
    default_root = runtime_root() / "GPT-SoVITS/GPT_SoVITS/pretrained_models/gsv-v2final-pretrained"
    profile["gpt_weights"] = str(
        Path(profile.get("gpt_weights", default_root / "s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt")).resolve()
    )
    profile["sovits_weights"] = str(Path(profile.get("sovits_weights", default_root / "s2G2333k.pth")).resolve())
    for key in ("gpt_weights", "sovits_weights"):
        profile[key + "_sha256"] = file_hash(profile[key])
    profile["base_manifest"] = file_hash(ROOT / "config/tts-models-lock.json")
    return profile


class GPTSoVITSAdapter:
    def __init__(self, url, session=None):
        self.url = url.rstrip("/")
        self.session = session or requests.Session()
        self.session.trust_env = False

    def synthesize(self, dialogue, profile, destination):
        reference = Path(profile["reference_path"])
        if not profile.get("authorized") or file_hash(reference) != profile["sha256"]:
            raise GateError("Voice reference missing, changed, or unauthorized")
        for key, route in (("gpt_weights", "/set_gpt_weights"), ("sovits_weights", "/set_sovits_weights")):
            if profile.get(key):
                response = self.session.get(self.url + route, params={"weights_path": profile[key]}, timeout=(5, 180))
                response.raise_for_status()
        # GPT-SoVITS api_v2 has no generic 'emotion' field. Use authorized emotional references and supported controls.
        emotion = profile.get("emotion_profiles", {}).get(dialogue["emotion"], {})
        controls = {key: emotion[key] for key in ("speed_factor", "temperature", "top_p", "top_k") if key in emotion}
        payload = {
            "text": dialogue["text"],
            "text_lang": "zh",
            "ref_audio_path": str(reference),
            "prompt_text": profile["prompt_text"],
            "prompt_lang": profile.get("prompt_lang", "zh"),
            "text_split_method": "cut5",
            "batch_size": 1,
            "media_type": "wav",
            "streaming_mode": False,
            "parallel_infer": False,
            "seed": int(digest(dialogue["dialogue_id"])[:8], 16),
            **controls,
        }
        response = self.session.post(self.url + "/tts", json=payload, timeout=(5, 600))
        response.raise_for_status()
        if not response.content.startswith(b"RIFF") or response.content[8:12] != b"WAVE":
            raise RuntimeError(f"GPT-SoVITS did not return WAV: {response.content[:200]!r}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        raw = destination.with_name("response.wav")
        raw.write_bytes(response.content)
        command(
            ["ffmpeg", "-y", "-v", "error", "-i", raw, "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", destination]
        )
        return {"request": payload, "backend": "GPT-SoVITS api_v2", "emotion_control": controls}


class CosyVoiceAdapter:
    """Extension interface; install a backend and implement its verified transport before selecting it."""

    def synthesize(self, dialogue, profile, destination):
        raise NotImplementedError(
            "CosyVoice transport is not configured; implement TTSAdapter for the deployed version"
        )


class ManagedTTS:
    def __init__(self, url, runtime=None):
        self.url = url
        self.runtime = Path(runtime or runtime_root())
        self.process = None
        self.log = None

    def __enter__(self):
        try:
            # An existing external service is outside our unload/stop ownership.
            requests.get(self.url + "/openapi.json", timeout=2).raise_for_status()
        except requests.ConnectionError:
            pass
        else:
            raise GateError(
                "Stop the external TTS service; this engine starts/stops its own isolated process per audio stage"
            )
        python = self.runtime / "tts-venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        repository = self.runtime / "GPT-SoVITS"
        if not python.is_file() or not (repository / "api_v2.py").is_file():
            raise GateError("Run bash scripts/setup_tts.sh before the audio stage")
        from urllib.parse import urlsplit

        address = urlsplit(self.url)
        if address.hostname not in {"127.0.0.1", "localhost"}:
            raise GateError("Managed TTS requires a local shared-filesystem URL")
        args = [
            str(python),
            "api_v2.py",
            "-a",
            "127.0.0.1",
            "-p",
            str(address.port or 9880),
            "-c",
            os.environ.get("DRAMA_TTS_CONFIG", "GPT_SoVITS/configs/tts_infer.yaml"),
        ]
        (ROOT / "logs").mkdir(exist_ok=True)
        self.log = (ROOT / "logs/tts.log").open("a", encoding="utf-8")
        self.process = subprocess.Popen(
            args,
            cwd=repository,
            stdout=self.log,
            stderr=subprocess.STDOUT,
            start_new_session=os.name != "nt",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise GateError(
                        "GPT-SoVITS exited during startup; see logs/tts.log (pretrained models/config required)"
                    )
                try:
                    schema = requests.get(self.url + "/openapi.json", timeout=2)
                    schema.raise_for_status()
                    if "/tts" in schema.json().get("paths", {}):
                        return self
                except (requests.RequestException, ValueError):
                    pass
                time.sleep(1)
            raise GateError("GPT-SoVITS startup timed out; see logs/tts.log")
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        if self.process is not None and self.process.poll() is None:
            if os.name == "nt":
                self.process.terminate()
            else:
                os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    self.process.kill()
                else:
                    os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=15)
        if self.log:
            self.log.close()


class VoiceGenerator:
    def __init__(self, state, queue, adapter):
        self.state, self.queue, self.adapter = state, queue, adapter

    def line(self, episode_id, dialogue, retry=False, regenerate=False, resume_job=None):
        character = self.state.entity("characters", dialogue["character_id"])
        profile = effective_profile(self.state, character["voice_id"])
        params = {"dialogue": dialogue, "profile": profile, "seed": int(digest(dialogue["dialogue_id"])[:8], 16)}
        if regenerate:
            params["candidate"] = uuid.uuid4().hex
        elif resume_job:
            original = __import__("json").loads(resume_job["input_params"])
            if "candidate" in original:
                params["candidate"] = original["candidate"]
        version = digest(
            {"adapter": file_hash(Path(__file__)), "upstream": read_json(ROOT / "config/upstream-lock.json")["tts"]}
        )
        job = self.state.job(episode_id, dialogue["dialogue_id"], "audio", params, version, digest(profile))
        if resume_job and job["id"] != resume_job["id"]:
            self.state.update_job(resume_job["id"], status="superseded", error="Inputs/models changed before resume")

        def operation(current):
            directory = ROOT / "projects" / episode_id / "jobs" / current["id"]
            path = directory / "output.wav"
            report = self.adapter.synthesize(dialogue, profile, path)
            quality = QualityChecker().audio(path, dialogue["dialogue_id"])
            write_json(directory / "quality.json", quality)
            write_json(directory / "request.json", report)
            if quality["errors"]:
                raise GateError(f"{dialogue['dialogue_id']}: {quality['errors']}")
            return [
                self.state.add_asset(
                    dialogue["dialogue_id"],
                    "audio",
                    path,
                    current["id"],
                    {"quality": quality, "voice_profile": digest(profile)},
                )
            ]

        return self.queue.run(job, operation, retry)


@contextmanager
def audio_stage(models, settings):
    models.assert_tts_stopped()
    models.verify_service()
    models.unload()
    models.check_resources()
    for item in read_json(ROOT / "config/tts-models-lock.json")["models"]:
        path = runtime_root() / "GPT-SoVITS/GPT_SoVITS/pretrained_models" / item["destination"]
        if not path.is_file() or path.stat().st_size != item["size"] or file_hash(path) != item["sha256"]:
            raise GateError(f"Missing/changed TTS model {item['key']}; run python -m scripts.download_tts_models")
    with ResourceMonitor() as monitor:
        with ManagedTTS(settings["tts_url"]):
            yield monitor
    # ManagedTTS waits for process exit, so no TTS weights can overlap the next image/video stage.
