import shutil
from pathlib import Path

from core.common import ROOT, GateError, digest, file_hash
from core.schema import Character
from production.quality_checker import QualityChecker


class CharacterManager:
    def __init__(self, state):
        self.state = state

    def create(self, data):
        character = Character.model_validate(data).model_dump()
        with self.state.db:
            self.state.db.execute(
                "INSERT INTO characters VALUES (?,?)", (character["character_id"], __import__("json").dumps(character))
            )
        return character

    def update(self, character_id, changes):
        if "identity_locked" in changes:
            raise GateError("Identity can only be locked by approving a reference image")
        current = self.state.entity("characters", character_id)
        data = Character.model_validate({**current, **changes, "character_id": character_id}).model_dump()
        if data != current:
            data["identity_locked"] = False
            self.state.set_character(data)
            self.state.invalidate(character_id, ["identity"])
            self.state.invalidate_character_shots(character_id)
        return data

    def import_reference(self, character_id, source, kind="identity", authorized=False):
        import re

        character = self.state.entity("characters", character_id)
        if not character["fictional"] and not (authorized and character["likeness_authorized"]):
            raise GateError("Real-person references require documented likeness authorization and --authorized")
        if not re.fullmatch(r"identity|(?:angle|wardrobe|expression)_[A-Za-z0-9_]+", kind):
            raise ValueError("unsupported character reference kind")
        source = Path(source).resolve()
        report = QualityChecker().image(source, character_id)
        if report["errors"]:
            raise GateError(str(report["errors"]))
        destination = (
            ROOT / "assets/characters" / character_id / kind / (file_hash(source)[:16] + source.suffix.lower())
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination != source:
            shutil.copyfile(source, destination)
        asset_id = self.state.add_asset(
            character_id, kind, destination, metadata={"quality": report, "authorization_attested": authorized}
        )
        return asset_id

    def references(self, character_ids):
        references = []
        for character_id in character_ids:
            character = self.state.entity("characters", character_id)
            if not character["identity_locked"]:
                raise GateError(f"{character_id}: character identity must be reviewed and locked first")
            if not character["fictional"] and not character["likeness_authorized"]:
                raise GateError(f"{character_id}: likeness authorization required")
            asset = self.state.selected(character_id, "identity")
            references.append({"character": character, "asset": asset})
        return references

    def used_in(self, character_id):
        return [
            row["id"]
            for row in self.state.rows("SELECT id,data FROM shots")
            if character_id in __import__("json").loads(row["data"])["characters"]
        ]

    def voice(self, profile):
        required = {"voice_id", "reference_path", "prompt_text", "authorized", "license_note"}
        if not required <= set(profile) or profile["authorized"] is not True or not profile["license_note"].strip():
            raise GateError("Voice profile requires authorized reference audio, transcript, and license/consent note")
        import re

        if not re.fullmatch(r"VOICE_[A-Za-z0-9_]+", profile["voice_id"]):
            raise ValueError("voice_id must match VOICE_[A-Za-z0-9_]+")
        source = Path(profile["reference_path"]).resolve()
        # The managed TTS shares this filesystem; HTTP path upload is not a GPT-SoVITS api_v2 feature.
        destination = ROOT / "assets/voices" / profile["voice_id"] / (file_hash(source)[:16] + ".wav")
        destination.parent.mkdir(parents=True, exist_ok=True)
        from production.quality_checker import command

        command(
            ["ffmpeg", "-y", "-v", "error", "-i", source, "-ac", "1", "-ar", "32000", "-c:a", "pcm_s16le", destination]
        )
        quality = QualityChecker().audio(destination, profile["voice_id"])
        if quality["errors"] or not 3 <= quality["duration"] <= 10:
            raise GateError("Voice reference must be 3–10 seconds of non-silent, unclipped audio")
        profile = {**profile, "reference_path": str(destination), "sha256": file_hash(destination)}
        from core.common import canonical

        with self.state.db:
            self.state.db.execute(
                "INSERT INTO voice_profiles VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (profile["voice_id"], canonical(profile)),
            )
            for row in self.state.rows("SELECT id,data FROM dialogues"):
                import json

                dialogue = json.loads(row["data"])
                character = self.state.entity("characters", dialogue["character_id"])
                if character["voice_id"] == profile["voice_id"]:
                    self.state.db.execute(
                        "UPDATE assets SET selected=0 WHERE owner_id=? AND kind='audio'", (row["id"],)
                    )
            self.state.db.execute("UPDATE assets SET selected=0 WHERE kind='final'")
        return digest(profile)
