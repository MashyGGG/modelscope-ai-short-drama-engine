from typing import Literal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

SHOT_TYPES = (
    "establishing",
    "wide",
    "medium",
    "medium_close_up",
    "close_up",
    "extreme_close_up",
    "over_the_shoulder",
    "reaction",
    "insert",
)


class ReviewStatus(str, Enum):
    PENDING = "pending"
    GENERATING = "generating"
    GENERATED = "generated"
    REVIEW_REQUIRED = "review_required"
    APPROVED = "approved"
    REJECTED = "rejected"
    FAILED = "failed"


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Character(Record):
    character_id: str = Field(pattern=r"^CHAR_[A-Za-z0-9_]+$")
    name: str
    gender: str
    age: int = Field(ge=18)
    appearance: dict[str, str]
    wardrobe: dict[str, str] = Field(default_factory=lambda: {"default": "contemporary formal clothing"})
    expressions: dict[str, str] = Field(default_factory=dict)
    voice_id: str
    fictional: bool = True
    likeness_authorized: bool = False
    identity_locked: bool = False


class Scene(Record):
    scene_id: str = Field(pattern=r"^SCENE_[A-Za-z0-9_]+$")
    name: str
    description: str
    lighting: str
    sound_effects: list[str] = Field(default_factory=list)


class Dialogue(Record):
    dialogue_id: str = Field(pattern=r"^[A-Za-z0-9_]+$")
    character_id: str
    text: str = Field(min_length=1)
    emotion: str = "neutral"
    mode: Literal["offscreen", "reaction", "narration"] = "offscreen"
    offset: float = Field(default=0, ge=0)


class Shot(Record):
    shot_id: str = Field(pattern=r"^[A-Za-z0-9_]+$")
    scene_id: str
    characters: list[str]
    duration: float = Field(gt=0, le=10)
    shot_type: str
    camera: str
    image_prompt: str
    motion_prompt: str
    negative_prompt: str = "distorted face, extra fingers, text, subtitles, watermark, extra people"
    reference_images: list[str] = Field(default_factory=list)
    wardrobe: dict[str, str] = Field(default_factory=dict)
    pose_reference: str | None = None
    identity_masks: dict[str, str] = Field(default_factory=dict)
    expressions: dict[str, str] = Field(default_factory=dict)
    dialogues: list[Dialogue] = Field(default_factory=list)
    seed: int = Field(ge=0, lt=2**63)
    status: str = "pending"

    @model_validator(mode="after")
    def shot_type_valid(self):
        if self.shot_type not in SHOT_TYPES:
            raise ValueError(f"shot_type must be one of {SHOT_TYPES}")
        if len(self.characters) != len(set(self.characters)):
            raise ValueError("duplicate character in shot")
        return self


class EpisodePlan(Record):
    project_id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    episode_id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    title: str
    outline: str
    genre: str = "都市反转"
    duration: float = Field(ge=30, le=60)
    aspect_ratio: Literal["9:16"] = "9:16"
    style: str = "photorealistic cinematic film photography, natural skin texture, 35mm lens"
    script: str
    characters: list[Character] = Field(min_length=2, max_length=4)
    scenes: list[Scene] = Field(min_length=1)
    shots: list[Shot] = Field(min_length=10, max_length=16)
    music_path: str | None = None
    effect_tracks: list[dict] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_relations(self):
        chars = {c.character_id for c in self.characters}
        scenes = {s.scene_id for s in self.scenes}
        if len(chars) != len(self.characters) or len(scenes) != len(self.scenes):
            raise ValueError("duplicate character/scene IDs")
        if abs(sum(s.duration for s in self.shots) - self.duration) > 0.05:
            raise ValueError("shot durations must sum to episode duration")
        ids = [s.shot_id for s in self.shots]
        lines = [d.dialogue_id for s in self.shots for d in s.dialogues]
        if len(ids) != len(set(ids)) or len(lines) != len(set(lines)):
            raise ValueError("duplicate shot/dialogue IDs")
        for shot in self.shots:
            if shot.scene_id not in scenes or not set(shot.characters) <= chars:
                raise ValueError(f"unknown references in {shot.shot_id}")
            if not set(shot.wardrobe) <= set(shot.characters):
                raise ValueError(f"wardrobe character not visible: {shot.shot_id}")
            for character_id, key in shot.wardrobe.items():
                character = next(c for c in self.characters if c.character_id == character_id)
                if key not in character.wardrobe:
                    raise ValueError(f"unknown wardrobe {key}")
            for dialogue in shot.dialogues:
                if dialogue.character_id not in chars or dialogue.offset >= shot.duration:
                    raise ValueError(f"invalid dialogue {dialogue.dialogue_id}")
        return self
