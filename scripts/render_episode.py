import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from generate_episode import main

if __name__ == "__main__":
    raise SystemExit(main(["--stage", "render", *sys.argv[1:]]))
