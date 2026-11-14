from core.schema import EpisodePlan, SHOT_TYPES


def validate_storyboard(plan):
    return EpisodePlan.model_validate(plan)


def direction_rules():
    return (
        f"shot_type must be one of {SHOT_TYPES}. Use over_the_shoulder and reaction shots for dialogue reversals. "
        "Prefer one identifiable visible character per shot; blurred offscreen shoulders are acceptable. "
        "Never ask Wan for precise lip sync. Use offscreen dialogue/reaction shots. "
        "All characters must be adults. Use consistent Character IDs and wardrobe across shots. "
        "Each dialogue should fit its shot, preferably <=10 Chinese characters. "
        "Use 10-16 shots whose durations sum exactly to target duration. Output all schema fields required by the JSON schema."
    )
