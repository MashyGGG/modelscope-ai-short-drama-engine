import json
import os

import requests

from core.common import GateError
from core.schema import EpisodePlan
from director.storyboard_writer import direction_rules


class ScriptWriter:
    """Configurable OpenAI-compatible remote text endpoint; no text model occupies the A10."""

    def generate(
        self,
        title,
        outline,
        duration=45,
        character_count=3,
        aspect_ratio="9:16",
        genre="都市反转",
        style="真人电影摄影风格",
    ):
        endpoint = os.environ.get("DRAMA_LLM_URL")
        model = os.environ.get("DRAMA_LLM_MODEL")
        if not endpoint or not model:
            raise GateError(
                "Set DRAMA_LLM_URL (full chat/completions URL), DRAMA_LLM_MODEL and optional DRAMA_LLM_KEY; or import --plan examples/episode_45s.json"
            )
        request = {
            "title": title,
            "outline": outline,
            "duration": duration,
            "character_count": character_count,
            "aspect_ratio": aspect_ratio,
            "genre": genre,
            "style": style,
        }
        payload = {
            "model": model,
            "temperature": 0.7,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": "You are a Chinese short-drama director. Return a single JSON object matching this schema: "
                    + json.dumps(EpisodePlan.model_json_schema(), ensure_ascii=False)
                    + "\n"
                    + direction_rules(),
                },
                {"role": "user", "content": json.dumps(request, ensure_ascii=False)},
            ],
        }
        response = requests.post(
            endpoint,
            headers={"Authorization": f"Bearer {os.environ.get('DRAMA_LLM_KEY', '')}"},
            json=payload,
            timeout=(10, 180),
        )
        response.raise_for_status()
        plan = EpisodePlan.model_validate_json(response.json()["choices"][0]["message"]["content"])
        if len(plan.characters) != character_count or abs(plan.duration - duration) > 0.01:
            raise GateError("Director returned the wrong character count/duration; correct or regenerate the plan")
        return plan
