"""The authored 45-second acceptance story, not a generated film."""

from core.common import ROOT, write_json
from core.schema import EpisodePlan


def example():
    cast = [
        {
            "character_id": "CHAR_001",
            "name": "林辰",
            "gender": "male",
            "age": 28,
            "appearance": {
                "face_shape": "oval face with defined jaw",
                "eyes": "dark brown eyes",
                "hair": "short neat black hair",
                "skin": "natural warm skin",
                "body": "slim athletic",
            },
            "wardrobe": {"default": "charcoal tailored suit, white shirt, no tie"},
            "voice_id": "VOICE_001",
        },
        {
            "character_id": "CHAR_002",
            "name": "苏雨",
            "gender": "female",
            "age": 26,
            "appearance": {
                "face_shape": "soft oval face",
                "eyes": "almond dark brown eyes",
                "hair": "shoulder length straight black hair",
                "skin": "natural fair skin",
                "body": "slender",
            },
            "wardrobe": {"default": "ivory blazer over pale blue blouse"},
            "voice_id": "VOICE_002",
        },
        {
            "character_id": "CHAR_003",
            "name": "王总",
            "gender": "male",
            "age": 45,
            "appearance": {
                "face_shape": "square face",
                "eyes": "narrow dark brown eyes",
                "hair": "slicked back black hair with gray temples",
                "skin": "natural medium skin",
                "body": "broad shoulders",
            },
            "wardrobe": {"default": "navy double breasted suit, burgundy tie"},
            "voice_id": "VOICE_003",
        },
    ]
    beats = [
        (
            [],
            "establishing",
            "Marble lobby of a private legal office, rain on windows, unmarked divorce folder on reception desk",
            "Camera slowly pushes toward the desk",
            None,
        ),
        (
            ["CHAR_002"],
            "medium_close_up",
            "Su Yu calmly slides an unmarked folder toward an offscreen person, eye level",
            "A woman gently slides a folder, holds a cool expression",
            ("CHAR_002", "签了吧。", "cold"),
        ),
        (
            ["CHAR_001"],
            "over_the_shoulder",
            "Lin Chen in sharp focus, blurred anonymous shoulder at left edge, shot reverse shot eyeline right",
            "The man lowers his gaze then looks up, subtle breathing",
            ("CHAR_001", "你想清楚了？", "restrained"),
        ),
        (
            ["CHAR_003"],
            "medium",
            "Wang stands at the lobby entrance, a restrained condescending smile, eyeline left",
            "The older man adjusts his cuff, slight smile",
            ("CHAR_003", "她有更好的选择。", "arrogant"),
        ),
        (
            ["CHAR_001"],
            "insert",
            "Close framing on Lin Chen's hand holding a dark phone, screen turned away, suit cuff matches reference",
            "A phone vibrates once in a man's hand, restrained motion",
            None,
        ),
        (
            ["CHAR_001"],
            "close_up",
            "Lin Chen listens to a phone, soft window light reveals surprise, face stable",
            "The man listens, a small change in his eyes",
            ("CHAR_001", "爷爷的集团？", "surprised"),
        ),
        (
            ["CHAR_002"],
            "reaction",
            "Su Yu glances toward the phone, uncertainty behind composure, eyeline left",
            "The woman slightly furrows her brow, no speech",
            ("CHAR_002", "你还在演？", "skeptical"),
        ),
        (
            ["CHAR_001"],
            "medium_close_up",
            "Lin Chen sets down the phone and straightens his charcoal suit, suddenly composed",
            "The man lowers his phone and slowly straightens his shoulders",
            ("CHAR_001", "从今天起，我接手。", "confident"),
        ),
        (
            ["CHAR_003"],
            "over_the_shoulder",
            "Wang's face in focus, anonymous blurred shoulder at right, composure falters, eyeline right",
            "The older man's smile fades and his eyes widen slightly",
            ("CHAR_003", "你是林家继承人？", "shocked"),
        ),
        (
            ["CHAR_002"],
            "extreme_close_up",
            "Su Yu's eyes register disbelief and regret, realistic skin texture, cinematic shallow focus",
            "The woman's eyes widen subtly, she holds still",
            None,
        ),
        (
            ["CHAR_001"],
            "wide",
            "Lin Chen alone walks toward the bright glass lobby doors, same charcoal suit, clean silhouette",
            "A man takes two calm steps toward the glass door, camera tracks slowly",
            ("CHAR_001", "这次，是我不要了。", "resolved"),
        ),
        (
            ["CHAR_002"],
            "reaction",
            "Su Yu alone beside the unmarked folder, empty space at left, cool rain light",
            "Camera gently pulls back from the silent woman",
            None,
        ),
    ]
    shots = []
    for index, (characters, kind, image, motion, line) in enumerate(beats, 1):
        shot = {
            "shot_id": f"EP001_SH{index:03}",
            "scene_id": "SCENE_001",
            "characters": characters,
            "duration": 3.75,
            "shot_type": kind,
            "camera": "slow_dolly_in" if index != 12 else "slow_dolly_out",
            "image_prompt": image,
            "motion_prompt": motion,
            "seed": 177800 + index,
            "dialogues": [],
        }
        if line:
            character_id, text, emotion = line
            shot["dialogues"] = [
                {
                    "dialogue_id": f"EP001_D{index:03}",
                    "character_id": character_id,
                    "text": text,
                    "emotion": emotion,
                    "mode": "offscreen",
                    "offset": 0.2,
                }
            ]
        shots.append(shot)
    data = {
        "project_id": "DIVORCE_INHERITANCE",
        "episode_id": "EP001",
        "title": "离婚当天，我继承了千亿集团",
        "outline": "苏雨在律师事务所与林辰签署离婚协议，王总出言羞辱。林辰接到电话，得知继承家族集团。他平静接手，与过去告别。苏雨与王总惊愕。",
        "duration": 45,
        "script": "雨天，律师事务所。苏雨推来离婚协议：签了吧。林辰抬眼：你想清楚了？王总走近：她有更好的选择。电话震动，林辰接听：爷爷的集团？苏雨冷笑：你还在演？林辰放下电话：从今天起，我接手。王总失色：你是林家继承人？苏雨怔住。林辰起身：这次，是我不要了。他走向玻璃门。苏雨独留原地。",
        "characters": cast,
        "scenes": [
            {
                "scene_id": "SCENE_001",
                "name": "律师事务所大厅",
                "description": "modern legal office marble lobby, rain streaked tall windows, walnut desk, no signage",
                "lighting": "soft cool rainy daylight, subtle warm practical lamps, cinematic contrast",
                "sound_effects": ["rain on glass", "phone vibration", "quiet footsteps"],
            }
        ],
        "shots": shots,
    }
    return EpisodePlan.model_validate(data)


if __name__ == "__main__":
    write_json(ROOT / "examples/episode_45s.json", example().model_dump())
