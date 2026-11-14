import json
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PIL import Image

from core.common import ROOT, canonical, file_hash
from core.state_manager import StateManager
from core.task_queue import TaskQueue
from core.workflow_manager import OUTPUTS, WorkflowManager
from production.quality_checker import command
from scripts.create_example import example


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / "engine"
    root.mkdir()
    for name in ("config", "workflows", "core", "director", "production"):
        shutil.copytree(ROOT / name, root / name, ignore=shutil.ignore_patterns("__pycache__"))
    for module in (
        "production.character_manager",
        "production.image_generator",
        "production.video_generator",
        "production.voice_generator",
        "production.episode_renderer",
        "core.report",
        "core.engine",
    ):
        monkeypatch.setattr(module + ".ROOT", root)
    monkeypatch.setenv("DRAMA_RUNTIME", str(root / ".runtime"))
    state = StateManager(tmp_path / "test.sqlite")
    state.import_plan(example())
    yield root, state, TaskQueue(state, tmp_path / "gpu.lock")
    state.close()


@pytest.fixture(scope="session")
def media(tmp_path_factory):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg tools are required for real media validation tests")
    root = tmp_path_factory.mktemp("synthetic_media")
    image = root / "synthetic.png"
    Image.new("RGB", (256, 448), (102, 125, 153)).save(image)
    video = root / "synthetic.mp4"
    command(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=128x224:rate=16",
            "-frames:v",
            "49",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            video,
        ]
    )
    audio = root / "synthetic.wav"
    command(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.6",
            "-ar",
            "48000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            audio,
        ]
    )
    reference = root / "synthetic_reference.wav"
    command(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=220:duration=5",
            "-ar",
            "32000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            reference,
        ]
    )
    return {"image": image, "video": video, "audio": audio, "reference": reference}


def info_for_graphs(graphs):
    info = {}
    link_types = {
        (kind, name): OUTPUTS[graph[str(value[0])]["class_type"]][value[1]]
        for graph in graphs
        for item in graph.values()
        for kind in [item["class_type"]]
        for name, value in item["inputs"].items()
        if isinstance(value, list)
    }
    for graph in graphs:
        for item in graph.values():
            kind = item["class_type"]
            entry = info.setdefault(kind, {"input": {"required": {}, "optional": {}}, "output": OUTPUTS[kind]})
            for name, value in item["inputs"].items():
                if "." in name:
                    continue
                dtype = link_types.get(
                    (kind, name), "INT" if type(value) is int else "FLOAT" if type(value) is float else "STRING"
                )
                entry["input"]["required"][name] = [dtype, {}]
    # Actual upstream DynamicCombo schema shape, independently fixed for the pinned SaveVideo.
    if "SaveVideo" in info:
        encoding = {"encoding": ["COMFY_DYNAMICCOMBO_V3", {"options": [{"key": "auto", "inputs": {}}]}]}
        codec = {"codec": ["COMFY_DYNAMICCOMBO_V3", {"options": [{"key": "h264", "inputs": {"optional": encoding}}]}]}
        info["SaveVideo"]["input"]["required"]["format"] = [
            "COMFY_DYNAMICCOMBO_V3",
            {"options": [{"key": "mp4", "inputs": {"required": codec}}]},
        ]
    return info


@pytest.fixture
def comfy_server(media):
    workflows = WorkflowManager()
    image = workflows.image("portrait", "bad", 1, "test", [{"image": "ref.png"}])[0]
    video = workflows.video("ref.png", "move", "bad", 1, "test", low_resolution=True)[0]
    data = {
        "prompts": [],
        "history": {},
        "uploads": [],
        "free_calls": 0,
        "info": info_for_graphs([image, video]),
        "fail_next": False,
        "defer": False,
        "pending": [],
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, payload, content_type="application/json", status=200):
            body = json.dumps(payload).encode() if content_type == "application/json" else payload
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/object_info":
                self.send(data["info"])
            elif self.path == "/system_stats":
                self.send({"devices": [{"type": "cuda", "vram_total": 24 * 2**30, "vram_free": 23 * 2**30}]})
            elif self.path == "/queue":
                self.send({"queue_running": data["pending"], "queue_pending": []})
            elif self.path.startswith("/history/"):
                pid = self.path.rsplit("/", 1)[-1]
                self.send({pid: data["history"][pid]} if pid in data["history"] else {})
            elif self.path.startswith("/view?"):
                is_video = "synthetic.mp4" in self.path
                self.send(
                    media["video" if is_video else "image"].read_bytes(), "video/mp4" if is_video else "image/png"
                )
            else:
                self.send({"error": "not found"}, status=404)

        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            if self.path == "/upload/image":
                data["uploads"].append(raw)
                self.send({"name": f"uploaded_{len(data['uploads'])}.png", "type": "input", "subfolder": "drama"})
            elif self.path == "/free":
                data["free_calls"] += 1
                self.send({})
            elif self.path == "/prompt":
                payload = json.loads(raw)
                data["prompts"].append(payload)
                pid = payload["prompt_id"]
                is_video = "58" in payload["prompt"]
                history = {
                    "status": {"completed": True, "status_str": "success", "messages": []},
                    "outputs": {
                        "58" if is_video else "9": {
                            "images": [
                                {
                                    "filename": "synthetic.mp4" if is_video else "synthetic.png",
                                    "subfolder": "",
                                    "type": "output",
                                }
                            ]
                        }
                    },
                }
                if data["fail_next"]:
                    data["fail_next"] = False
                    history["status"].update(
                        status_str="error", messages=[["execution_error", {"exception_message": "CUDA out of memory"}]]
                    )
                if data["defer"]:
                    data["deferred_history"] = history
                    data["pending"] = [[1, pid]]
                else:
                    data["history"][pid] = history
                self.send({"prompt_id": pid, "node_errors": {}})
            else:
                self.send({}, status=404)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", data
    server.shutdown()
    server.server_close()
    thread.join()


def approve(state, owner, kind, path, metadata=None):
    asset = state.add_asset(owner, kind, path, metadata=metadata)
    state.review(asset, "approved", "offline-test-human", "SYNTHETIC FIXTURE ONLY")
    return asset


def add_voices(state, media):
    fake_weights = media["audio"]
    for index in range(1, 4):
        profile = {
            "voice_id": f"VOICE_{index:03}",
            "reference_path": str(media["reference"]),
            "prompt_text": "合成测试信号",
            "authorized": True,
            "license_note": "synthetic tone, no real voice",
            "sha256": file_hash(media["reference"]),
            "gpt_weights": str(fake_weights),
            "sovits_weights": str(fake_weights),
        }
        with state.db:
            state.db.execute("INSERT INTO voice_profiles VALUES (?,?)", (profile["voice_id"], canonical(profile)))
