import array
import json
import math
import subprocess
import wave
from fractions import Fraction
from pathlib import Path

from PIL import Image, ImageStat


def command(args, timeout=300, cwd=None):
    result = subprocess.run(
        [str(a) for a in args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        cwd=cwd,
    )
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed: {result.stderr[-4000:]}")
    return result


class QualityChecker:
    def image(self, path, owner_id):
        result = {
            "owner_id": owner_id,
            "errors": [],
            "warnings": [],
            "human_checks": ["identity", "anatomy", "extra people", "scene and wardrobe"],
        }
        try:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                gray = image.convert("L")
                stats = ImageStat.Stat(gray)
                result.update(width=image.width, height=image.height, brightness=stats.mean[0])
                histogram = gray.histogram()
                dark = sum(histogram[:8]) / (image.width * image.height)
                result["dark_pixel_fraction"] = dark
                if dark > 0.99:
                    result["errors"].append("image is almost entirely black")
                if min(image.size) < 128:
                    result["errors"].append("image resolution below 128 pixels")
        except Exception as exc:
            result["errors"].append(f"invalid image: {exc}")
        return result

    def probe(self, path):
        return json.loads(
            command(
                ["ffprobe", "-v", "error", "-count_frames", "-show_streams", "-show_format", "-of", "json", path]
            ).stdout
        )

    def video(self, path, owner_id, expected=None, final=False):
        result = {
            "owner_id": owner_id,
            "errors": [],
            "warnings": [],
            "human_checks": ["facial stability", "anatomy", "motion continuity"],
        }
        try:
            path = Path(path)
            result["size_bytes"] = path.stat().st_size
            if path.suffix.lower() != ".mp4" or path.stat().st_size < 1024:
                raise ValueError("MP4 missing or too small")
            probe = self.probe(path)
            video = next(s for s in probe["streams"] if s["codec_type"] == "video")
            duration = float(video.get("duration", probe["format"]["duration"]))
            result.update(
                width=video["width"],
                height=video["height"],
                fps=float(Fraction(video["avg_frame_rate"])),
                frames=int(video.get("nb_read_frames", 0)),
                duration=duration,
                codec=video["codec_name"],
            )
            command(["ffmpeg", "-v", "error", "-xerror", "-i", path, "-map", "0:v:0", "-f", "null", "-"])
            detection = command(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-i",
                    path,
                    "-an",
                    "-vf",
                    "blackdetect=d=0.15:pix_th=0.05,freezedetect=n=-50dB:d=1",
                    "-f",
                    "null",
                    "-",
                ]
            )
            events = [
                line for line in detection.stderr.splitlines() if "black_start:" in line or "freeze_start:" in line
            ]
            result["events"] = events
            if any("black_start:" in line for line in events):
                result["errors"].append("black frames detected")
            if any("freeze_start:" in line for line in events):
                result["warnings"].append("frozen frames detected; human review required")
            for key, value in (expected or {}).items():
                tolerance = 0.05 if key in {"fps", "duration"} else 0
                if key not in result or abs(result[key] - value) > tolerance:
                    result["errors"].append(f"{key}: expected {value}, got {result.get(key)}")
            if final:
                if not 30 <= duration <= 60:
                    result["errors"].append("final duration outside 30–60 seconds")
                if abs(result["width"] / result["height"] - 9 / 16) > 0.001:
                    result["errors"].append("final aspect ratio is not 9:16")
                audio = next((s for s in probe["streams"] if s["codec_type"] == "audio"), None)
                if not audio:
                    result["errors"].append("final audio stream missing")
                elif abs(float(audio.get("duration", duration)) - duration) > 0.1:
                    result["errors"].append("audio/video stream duration mismatch")
                elif audio["codec_name"] != "aac":
                    result["errors"].append("final audio must be AAC")
                if video["codec_name"] != "h264":
                    result["errors"].append("final video must be H.264")
                result["human_checks"] += [
                    "subtitles readable and within frame",
                    "speech alignment",
                    "no missing shots",
                ]
        except Exception as exc:
            result["errors"].append(str(exc))
        return result

    def audio(self, path, owner_id, expected_duration=None):
        result = {
            "owner_id": owner_id,
            "errors": [],
            "warnings": [],
            "human_checks": ["pronunciation", "voice identity", "emotion"],
        }
        try:
            with wave.open(str(path), "rb") as audio:
                if audio.getsampwidth() != 2:
                    raise ValueError("audio must be PCM16 WAV; normalize with FFmpeg first")
                samples = array.array("h", audio.readframes(audio.getnframes()))
                duration = audio.getnframes() / audio.getframerate()
                result.update(duration=duration, sample_rate=audio.getframerate(), channels=audio.getnchannels())
            if duration <= 0 or not samples:
                raise ValueError("empty audio")
            rms = math.sqrt(sum(s * s for s in samples) / len(samples)) / 32768
            clipping = sum(abs(s) >= 32760 for s in samples) / len(samples)
            result.update(rms=rms, clipping_fraction=clipping)
            if rms < 0.0001:
                result["errors"].append("silent audio")
            if clipping > 0.01:
                result["errors"].append("audio clipping exceeds 1%")
            if expected_duration is not None and abs(duration - expected_duration) > 0.05:
                result["errors"].append("audio duration does not match subtitle timeline")
        except Exception as exc:
            result["errors"].append(str(exc))
        return result
