from core.common import GateError


def build(shot, characters, scene, style):
    parts = [style, f"{shot['shot_type']} shot, camera: {shot['camera']}", scene["description"], scene["lighting"]]
    for character in characters:
        key = shot.get("wardrobe", {}).get(character["character_id"], "default")
        if key not in character["wardrobe"]:
            raise GateError(f"Unknown wardrobe {key} for {character['character_id']}")
        parts += [
            f"{character['name']}, {character['gender']}, age {character['age']}, "
            + ", ".join(character["appearance"].values()),
            f"wearing {character['wardrobe'][key]}",
        ]
        expression = shot.get("expressions", {}).get(character["character_id"])
        if expression:
            if expression not in character["expressions"]:
                raise GateError(f"Unknown expression {expression} for {character['character_id']}")
            parts.append(character["expressions"][expression])
    parts += [
        shot["image_prompt"],
        "natural human proportions, realistic skin, consistent facial identity, no text, no subtitles",
    ]
    return ". ".join(parts)
