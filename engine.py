"""CLI review and asset management. The same StateManager API can serve a future UI."""

import argparse
import json
import shutil
from pathlib import Path

import portalocker

from core.common import ROOT, GateError, file_hash, read_json, runtime_root, write_json
from core.report import build_report
from core.state_manager import StateManager
from core.task_queue import TaskQueue
from production.character_manager import CharacterManager
from production.quality_checker import QualityChecker


def main(argv=None):
    parser = argparse.ArgumentParser(description="短剧审核与资产管理")
    parser.add_argument("--db", default=str(ROOT / "database/engine.sqlite"))
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "review-list", "report"):
        sub = commands.add_parser(name)
        sub.add_argument("--episode", default="EP001")
        if name == "report":
            sub.add_argument("--gpu-acceptance", action="store_true")
    review = commands.add_parser("review")
    review.add_argument("--asset", required=True)
    review.add_argument("--decision", required=True, choices=["approved", "rejected"])
    review.add_argument("--reviewer", required=True)
    review.add_argument("--notes", default="")
    for name in ("character-create", "voice-import"):
        sub = commands.add_parser(name)
        sub.add_argument("--json", required=True)
    edit = commands.add_parser("character-edit")
    edit.add_argument("--character", required=True)
    edit.add_argument("--json", required=True)
    ref = commands.add_parser("character-import")
    ref.add_argument("--character", required=True)
    ref.add_argument("--file", required=True)
    ref.add_argument("--kind", default="identity")
    ref.add_argument("--authorized", action="store_true")
    used = commands.add_parser("character-shots")
    used.add_argument("--character", required=True)
    imported = commands.add_parser("scene-import")
    imported.add_argument("--episode", default="EP001")
    imported.add_argument("--scene", required=True)
    imported.add_argument("--file", required=True)
    exported = commands.add_parser("plan-export")
    exported.add_argument("--episode", default="EP001")
    exported.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    state = StateManager(args.db)
    manager = CharacterManager(state)
    try:
        if args.command in {"review-list", "status"}:
            plan = state.entity("episodes", args.episode)
            owners = [c["character_id"] for c in plan["characters"]] + [args.episode]
            owners += [s["shot_id"] for s in plan["shots"]]
            owners += [d["dialogue_id"] for s in plan["shots"] for d in s["dialogues"]]
            owners += [f"{args.episode}:{s['scene_id']}" for s in plan["scenes"]]
            slots = ",".join("?" for _ in owners)
            assets = state.rows(
                f"SELECT id,owner_id,kind,status,selected,path,metadata FROM assets WHERE owner_id IN ({slots}) ORDER BY created_at",
                owners,
            )
            for asset in assets:
                asset["quality"] = json.loads(asset.pop("metadata")).get("quality")
            result = {"episode": args.episode, "assets": assets}
            if args.command == "status":
                result["jobs"] = state.rows(
                    "SELECT id,owner_id,stage,status,attempts,error FROM generation_jobs WHERE episode_id=?",
                    (args.episode,),
                )
        elif args.command == "character-shots":
            result = manager.used_in(args.character)
        elif args.command == "report":
            result = build_report(state, args.episode, args.gpu_acceptance)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1 if args.gpu_acceptance and result["acceptance_errors"] else 0
        elif args.command == "plan-export":
            result = state.entity("episodes", args.episode)
            write_json(args.output, result)
        else:
            with TaskQueue(state, runtime_root() / "gpu.lock").lock():
                if args.command == "review":
                    state.review(args.asset, args.decision, args.reviewer, args.notes)
                    result = state.asset(args.asset)
                elif args.command == "character-create":
                    result = manager.create(read_json(args.json))
                elif args.command == "character-edit":
                    result = manager.update(args.character, read_json(args.json))
                elif args.command == "voice-import":
                    result = manager.voice(read_json(args.json))
                elif args.command == "character-import":
                    result = manager.import_reference(args.character, args.file, args.kind, args.authorized)
                elif args.command == "scene-import":
                    owner = f"{args.episode}:{args.scene}"
                    state.entity("scenes", owner)
                    report = QualityChecker().image(args.file, owner)
                    if report["errors"]:
                        raise GateError(str(report["errors"]))
                    source = Path(args.file).resolve()
                    path = (
                        ROOT
                        / "assets/scenes"
                        / args.episode
                        / args.scene
                        / (file_hash(source)[:16] + source.suffix.lower())
                    )
                    path.parent.mkdir(parents=True, exist_ok=True)
                    if path != source:
                        shutil.copyfile(source, path)
                    result = state.add_asset(owner, "scene", path, metadata={"quality": report})
                else:
                    raise ValueError("Unknown command")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (GateError, ValueError, RuntimeError, OSError, portalocker.exceptions.LockException) as exc:
        print(f"ERROR: {exc}")
        return 2
    finally:
        state.close()


if __name__ == "__main__":
    raise SystemExit(main())
