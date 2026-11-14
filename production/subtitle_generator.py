import textwrap
from pathlib import Path

from core.common import GateError, write_json
from production.quality_checker import QualityChecker


def stamp(seconds, ass=False):
    units = round(seconds * (100 if ass else 1000))
    scale = 100 if ass else 1000
    whole, remainder = divmod(units, scale)
    hours, remainder_seconds = divmod(whole, 3600)
    minutes, sec = divmod(remainder_seconds, 60)
    return (
        f"{hours}:{minutes:02}:{sec:02}.{remainder:02}" if ass else f"{hours:02}:{minutes:02}:{sec:02},{remainder:03}"
    )


def safe_ass(text):
    return text.replace("\\", "＼").replace("{", "｛").replace("}", "｝").replace("\r", "").replace("\n", " ")


class SubtitleGenerator:
    def timeline(self, state, episode_id):
        timeline = []
        cursor = 0.0
        for shot in state.shots(episode_id):
            local_end = 0.0
            for dialogue in shot["dialogues"]:
                asset = state.selected(dialogue["dialogue_id"], "audio")
                report = QualityChecker().audio(asset["path"], dialogue["dialogue_id"])
                if report["errors"]:
                    raise GateError(f"{dialogue['dialogue_id']}: {report['errors']}")
                start = max(dialogue["offset"], local_end)
                end = start + report["duration"]
                if end > shot["duration"] + 0.01:
                    raise GateError(
                        f"{shot['shot_id']}: dialogue ends at {end:.2f}s, beyond {shot['duration']:.2f}s; shorten speech or create a retimed plan"
                    )
                character = state.entity("characters", dialogue["character_id"])
                from core.common import digest
                from production.voice_generator import effective_profile

                metadata = __import__("json").loads(asset["metadata"])
                if metadata.get("voice_profile") != digest(effective_profile(state, character["voice_id"])):
                    raise GateError(
                        f"{dialogue['dialogue_id']}: voice profile/model changed since synthesis; regenerate and review audio"
                    )
                timeline.append(
                    {
                        **dialogue,
                        "shot_id": shot["shot_id"],
                        "character_name": character["name"],
                        "audio_path": asset["path"],
                        "audio_asset_id": asset["id"],
                        "start_time": round(cursor + start, 6),
                        "end_time": round(cursor + end, 6),
                    }
                )
                local_end = end + 0.08
            cursor += shot["duration"]
        return timeline

    def write(self, timeline, directory, settings, show_names=True):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        srt, ass = [], []
        for index, item in enumerate(timeline, 1):
            text = (item["character_name"] + "：" if show_names else "") + item["text"]
            lines = textwrap.wrap(safe_ass(text), width=17, break_long_words=True)
            if len(lines) > 2:
                raise GateError(f"{item['dialogue_id']}: subtitle exceeds two safe lines; split the dialogue")
            wrapped = "\n".join(lines)
            srt.append(f"{index}\n{stamp(item['start_time'])} --> {stamp(item['end_time'])}\n{wrapped}\n")
            content = "\\N".join(lines)
            ass.append(
                f"Dialogue: 0,{stamp(item['start_time'], True)},{stamp(item['end_time'], True)},Default,,0,0,0,,{content}"
            )
        header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {settings["width"]}
PlayResY: {settings["height"]}
WrapStyle: 2
ScaledBorderAndShadow: yes
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{settings["font"]},{settings["font_size"]},&H00FFFFFF,&H000000FF,&H00101010,&H80000000,0,0,0,0,100,100,0,0,1,3,1,2,60,60,{settings["margin_v"]},1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        (directory / "subtitles.srt").write_text("\n".join(srt), encoding="utf-8")
        (directory / "subtitles.ass").write_text(header + "\n".join(ass) + "\n", encoding="utf-8")
        write_json(directory / "timeline.json", timeline)
        return directory / "subtitles.ass"
