from core.schema import Scene


def validate_scenes(scenes):
    return [Scene.model_validate(scene).model_dump() for scene in scenes]
