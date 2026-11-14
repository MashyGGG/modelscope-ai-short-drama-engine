import json
from pathlib import Path

from core.common import ROOT, GateError, canonical, digest, file_hash, write_json
from production.quality_checker import QualityChecker, command
from production.subtitle_generator import SubtitleGenerator


class EpisodeRenderer:
    def __init__(self, state, queue, settings):
        self.state, self.queue, self.settings = state, queue, settings

    def render(self, episode_id, retry=False):
        plan = self.state.entity("episodes", episode_id)
        shots = self.state.shots(episode_id)
        videos = [self.state.selected(s["shot_id"], "video") for s in shots]
        # Validate upstream gates on every render, including cached jobs.
        from production.character_manager import CharacterManager

        for shot in shots:
            CharacterManager(self.state).references(shot["characters"])
            self.state.selected(shot["shot_id"], "image")
        timeline = SubtitleGenerator().timeline(self.state, episode_id)
        tracks = []
        if plan.get("music_path"):
            tracks.append(
                {
                    "path": str(Path(plan["music_path"]).resolve()),
                    "start_time": 0,
                    "loop": True,
                    "volume": self.settings["music_volume"],
                }
            )
        for track in plan.get("effect_tracks", []):
            if set(track) - {"path", "start_time", "volume"} or float(track.get("start_time", 0)) < 0:
                raise GateError("Effect tracks require path, nonnegative start_time, and optional volume")
            tracks.append(
                {
                    "path": str(Path(track["path"]).resolve()),
                    "start_time": float(track.get("start_time", 0)),
                    "loop": False,
                    "volume": float(track.get("volume", self.settings["effects_volume"])),
                }
            )
        params = {
            "plan": plan,
            "videos": [{"id": a["id"], "sha256": a["sha256"]} for a in videos],
            "timeline": timeline,
            "audio_hashes": [file_hash(t["audio_path"]) for t in timeline],
            "settings": self.settings,
            "tracks": [{**t, "sha256": file_hash(t["path"])} for t in tracks],
        }
        version = digest(
            {
                "renderer": file_hash(Path(__file__)),
                "subtitles": file_hash(ROOT / "production/subtitle_generator.py"),
                "ffmpeg": command(["ffmpeg", "-version"]).stdout.splitlines()[0],
            }
        )
        job = self.state.job(episode_id, episode_id, "render", params, version, "ffmpeg-h264-aac")

        def operation(current):
            directory = ROOT / "projects" / episode_id / "jobs" / current["id"]
            directory.mkdir(parents=True, exist_ok=True)
            with self.state.db:
                self.state.db.execute(
                    "INSERT INTO render_jobs VALUES (?,?,?,'generating',?) ON CONFLICT(id) DO UPDATE SET status='generating'",
                    (current["id"], episode_id, str(directory / "episode.mp4"), canonical(params)),
                )
            try:
                self._compose(directory, plan, shots, videos, timeline, tracks)
                quality = QualityChecker().video(
                    directory / "episode.mp4",
                    episode_id,
                    {
                        "width": self.settings["width"],
                        "height": self.settings["height"],
                        "fps": self.settings["fps"],
                        "duration": plan["duration"],
                    },
                    final=True,
                )
                write_json(directory / "quality.json", quality)
                if quality["errors"]:
                    raise GateError(f"{episode_id}: final quality failed: {quality['errors']}")
                asset = self.state.add_asset(
                    episode_id,
                    "final",
                    directory / "episode.mp4",
                    current["id"],
                    {"quality": quality, "shot_ids": [s["shot_id"] for s in shots], "timeline": timeline},
                )
                with self.state.db:
                    self.state.db.execute(
                        "UPDATE render_jobs SET status='review_required' WHERE id=?", (current["id"],)
                    )
                return [asset]
            except BaseException:
                with self.state.db:
                    self.state.db.execute("UPDATE render_jobs SET status='failed' WHERE id=?", (current["id"],))
                raise

        return self.queue.run(job, operation, retry)

    def _compose(self, directory, plan, shots, videos, timeline, tracks):
        settings = self.settings
        commands = []

        def execute(args):
            commands.append([str(a) for a in args])
            write_json(directory / "commands.json", commands)
            result = command(args, timeout=1800, cwd=directory)
            with (directory / "ffmpeg.log").open("a", encoding="utf-8") as stream:
                stream.write(result.stderr + "\n")

        for index, (shot, asset) in enumerate(zip(shots, videos)):
            info = json.loads(asset["metadata"])["quality"]
            hold = max(0, shot["duration"] - info["duration"])
            if hold > settings["max_hold_seconds"]:
                raise GateError(
                    f"{shot['shot_id']}: video is {hold:.2f}s too short; generate more frames before rendering"
                )
            filters = f"scale={settings['width']}:{settings['height']}:force_original_aspect_ratio=increase,crop={settings['width']}:{settings['height']},setsar=1,fps={settings['fps']}"
            filters += f",tpad=stop_mode=clone:stop_duration={hold:.6f},trim=duration={shot['duration']:.6f},setpts=PTS-STARTPTS"
            execute(
                [
                    "ffmpeg",
                    "-y",
                    "-v",
                    "warning",
                    "-i",
                    asset["path"],
                    "-an",
                    "-vf",
                    filters,
                    "-c:v",
                    "libx264",
                    "-preset",
                    "medium",
                    "-crf",
                    str(settings["crf"]),
                    "-pix_fmt",
                    "yuv420p",
                    f"clip_{index:03}.mp4",
                ]
            )
        (directory / "concat.txt").write_text(
            "".join(f"file 'clip_{i:03}.mp4'\n" for i in range(len(shots))), encoding="utf-8"
        )
        execute(
            [
                "ffmpeg",
                "-y",
                "-v",
                "warning",
                "-f",
                "concat",
                "-safe",
                "1",
                "-i",
                "concat.txt",
                "-c",
                "copy",
                "picture.mp4",
            ]
        )
        args = ["ffmpeg", "-y", "-v", "warning", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        sources = [
            {"path": t["audio_path"], "start_time": t["start_time"], "volume": 1.0, "loop": False} for t in timeline
        ] + tracks
        for source in sources:
            if source["loop"]:
                args += ["-stream_loop", "-1"]
            args += ["-i", source["path"]]
        filters, labels = [], ["[0:a]"]
        for index, source in enumerate(sources, 1):
            delay = round(source["start_time"] * 1000)
            filters.append(
                f"[{index}:a]aresample=48000,aformat=channel_layouts=stereo,volume={float(source['volume'])},adelay={delay}|{delay}[a{index}]"
            )
            labels.append(f"[a{index}]")
        filters.append(
            "".join(labels) + f"amix=inputs={len(labels)}:duration=first:normalize=0,alimiter=limit=0.95:latency=1[mix]"
        )
        args += [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[mix]",
            "-t",
            str(plan["duration"]),
            "-c:a",
            "pcm_s16le",
            "mix.wav",
        ]
        execute(args)
        SubtitleGenerator().write(timeline, directory, settings)
        execute(
            [
                "ffmpeg",
                "-y",
                "-v",
                "warning",
                "-i",
                "picture.mp4",
                "-i",
                "mix.wav",
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-vf",
                "ass=subtitles.ass",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                str(settings["crf"]),
                "-pix_fmt",
                "yuv420p",
                "-r",
                str(settings["fps"]),
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-t",
                str(plan["duration"]),
                "-movflags",
                "+faststart",
                "episode.mp4",
            ]
        )
