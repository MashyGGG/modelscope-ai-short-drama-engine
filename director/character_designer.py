from core.schema import Character


def validate_characters(characters):
    return [Character.model_validate(character).model_dump() for character in characters]
