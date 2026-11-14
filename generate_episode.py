import argparse
import json
import logging
import sys

import portalocker

from core.common import ROOT, GateError, read_json, write_json
from core.engine import Engine
from core.schema import EpisodePlan
from core.state_manager import StateManager
from director.script_writer import ScriptWriter


def parser():
    result = argparse.ArgumentParser(description="AI 短剧生产：规划、角色、分镜、视频、配音、人工审核与合成")
    result.add_argument("--db", default=str(ROOT / "database/engine.sqlite"))
    result.add_argument("--plan", help="Import a validated episode JSON")
    result.add_argument("--title")
    result.add_argument("--outline")
    result.add_argument("--duration", type=float, default=45)
    result.add_argument("--characters", type=int, default=3)
    result.add_argument("--aspect-ratio", default="9:16", choices=["9:16"])
    result.add_argument("--genre", default="都市反转")
    result.add_argument("--style", default="真人电影摄影风格")
    result.add_argument("--episode", default="EP001")
    result.add_argument("--stage", choices=["plan", "characters", "images", "videos", "audio", "render", "all"])
    result.add_argument("--shot")
    result.add_argument("--character")
    result.add_argument(
        "--angle", default="front", choices=["front", "profile", "three_quarter", "full_body", "half_body"]
    )
    result.add_argument("--resume", action="store_true")
    result.add_argument("--retry", action="store_true")
    result.add_argument("--regenerate", action="store_true")
    result.add_argument("--low-resolution", action="store_true")
    result.add_argument("--frames", type=int)
    result.add_argument(
        "--seed", type=int, help="Fixed seed for a single shot/character; regeneration otherwise chooses a new seed"
    )
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    (ROOT / "logs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(ROOT / "logs/engine.log", encoding="utf-8")],
    )
    state = StateManager(args.db)
    try:
        engine = Engine(state)
        if args.plan or args.title:
            if args.plan:
                plan = EpisodePlan.model_validate(read_json(args.plan))
            elif args.title == "离婚当天，我继承了千亿集团" and not args.outline:
                from scripts.create_example import example

                plan = example()
                if args.duration != 45 or args.characters != 3:
                    raise GateError(
                        "Built-in story has 3 characters and lasts 45s; use --outline and configure a director provider for other inputs"
                    )
            else:
                if not args.outline:
                    raise GateError("Provide --outline for a new story")
                plan = ScriptWriter().generate(
                    args.title, args.outline, args.duration, args.characters, args.aspect_ratio, args.genre, args.style
                )
            with engine.queue.lock():
                state.import_plan(plan)
                write_json(ROOT / "projects" / plan.episode_id / "plan.json", plan.model_dump())
            args.episode = plan.episode_id
            print(f"Plan imported: {plan.episode_id}, {len(plan.shots)} shots, {plan.duration}s")
        stage = args.stage or ("all" if args.resume else "plan")
        if stage == "plan":
            plan = state.entity("episodes", args.episode)
            print(
                f"Ready: {plan['title']}; next: python generate_episode.py --episode {args.episode} --stage characters"
            )
            return 0
        assets = engine.run(
            args.episode,
            stage,
            args.shot,
            args.character,
            args.regenerate,
            args.resume or args.retry,
            args.low_resolution,
            args.frames,
            args.angle,
            args.seed,
        )
        print(
            json.dumps(
                {"stage": stage, "asset_ids": assets, "next": "python engine.py review-list --episode " + args.episode},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except (GateError, ValueError, RuntimeError, OSError, portalocker.exceptions.LockException) as exc:
        logging.error("%s", exc)
        print("Review/status: python engine.py review-list --episode " + args.episode, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted; persisted jobs can be resumed with --resume", file=sys.stderr)
        return 130
    finally:
        state.close()


if __name__ == "__main__":
    raise SystemExit(main())
