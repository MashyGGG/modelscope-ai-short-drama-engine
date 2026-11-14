"""Reproducible offline evidence. This never grants GPU E2E acceptance."""

import os
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from core.common import ROOT, write_json


def main():
    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)
    (ROOT / "logs").mkdir(exist_ok=True)
    log_path = ROOT / "logs/offline-validation.log"
    environment = {**os.environ, "PYTHONUTF8": "1", "DRAMA_TEST_ARTIFACTS": str(reports / "offline_media")}
    commands = [
        [sys.executable, "-m", "pip", "check"],
        [sys.executable, "-m", "ruff", "check", "."],
        [
            sys.executable,
            "-m",
            "compileall",
            "-q",
            "core",
            "director",
            "production",
            "scripts",
            "tests",
            "engine.py",
            "generate_episode.py",
        ],
        [sys.executable, "-m", "scripts.validate_upstream"],
        [sys.executable, "-m", "scripts.preflight"],
        [sys.executable, "-m", "pytest", "-q", "--junitxml=reports/pytest.xml"],
    ]
    bash = shutil.which("bash")
    git_bash = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
    if os.name == "nt" and git_bash.is_file():
        bash = str(git_bash)
    if bash:
        commands += [
            [bash, "-n", str(path.relative_to(ROOT))]
            for path in [ROOT / "oneclick.sh", *sorted((ROOT / "scripts").glob("*.sh"))]
        ]
    started = time.time()
    evidence = []
    with log_path.open("w", encoding="utf-8") as log:
        log.write("OFFLINE VALIDATION — MOCK COMFYUI + SYNTHETIC MEDIA; NO GPU INFERENCE\n")
        for args in commands:
            heading = "COMMAND: " + " ".join(args)
            print(heading, flush=True)
            log.write(heading + "\n")
            process = subprocess.run(
                args,
                cwd=ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=600,
            )
            print(process.stdout, end="", flush=True)
            log.write(process.stdout + f"EXIT: {process.returncode}\n")
            log.flush()
            evidence.append({"command": args, "exit_code": process.returncode})
            if process.returncode:
                break
    passed = len(evidence) == len(commands) and all(e["exit_code"] == 0 for e in evidence)
    report = {
        "status": "OFFLINE PASSED" if passed else "OFFLINE FAILED",
        "gpu_e2e_status": "NOT_RUN",
        "elapsed_seconds": time.time() - started,
        "evidence": evidence,
        "shell_syntax_checked": bool(bash),
        "media_kind": "synthetic test patterns and tones; not a generated short drama",
    }
    junit = reports / "pytest.xml"
    if junit.is_file():
        suite = ET.parse(junit).getroot().find("testsuite")
        report["pytest"] = dict(suite.attrib)
    write_json(reports / "offline-validation.json", report)
    print(report["status"] + "; GPU E2E NOT RUN")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
