"""The definitive evaluation's engine reports, read for one imported run's detail.

A definitive evaluation (#1175) leaves engine documents beside its result rows. Two are shown on
run detail exactly as the engine wrote or printed them, and nothing here reads a figure out of
either one:

- the layer scorecard, `scorecard.json`, which `scripts/layer_scorecard.py --out` writes
  (`kind` `layer-scorecard`, `schema` 1); and
- the diff-quality judge's section, printed by `scripts/replay_judge.py report --json` over a
  `judge/` folder holding the three files `replay_judge` writes.

Each is looked for in the results file's own folder, then in each parent up to `benchmarks/`, so
a stratum's rows (`<tag>/<model>/results.jsonl`) find the documents written for their tag. A
document that is missing is `None`; one that is unreadable, of another kind or schema, or that the
judge's report refuses is named in `errors`, never shown as if it were the engine's output.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional

SCORECARD_NAME = "scorecard.json"
SCORECARD_KIND = "layer-scorecard"  # `layer_scorecard.KIND`
SCORECARD_SCHEMA = 1  # `layer_scorecard.SCHEMA`
JUDGE_FOLDER = "judge"
JUDGE_FILES = ("verdicts.jsonl", "pairs.key.json", "calibration.json")  # `layer_scorecard.load_judge`
MAX_REPORT_BYTES = 4 * 1024 * 1024
JUDGE_TIMEOUT_SECONDS = 120
BENCHMARKS = "benchmarks"
# This checkout's judge, as `citizen evidence verify` loads its verifier; never the indexed tree's.
_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"


def _folders(repository: Path, relative: str) -> List[Path]:
    """The results file's folder and each parent up to `benchmarks/`, nearest first."""
    parsed = PurePosixPath(relative)
    if parsed.is_absolute() or ".." in parsed.parts or not parsed.parts or parsed.parts[0] != BENCHMARKS:
        return []
    root = Path(repository).resolve()
    out: List[Path] = []
    parts = parsed.parts[:-1]
    while len(parts) > 1:
        out.append(root.joinpath(*parts))
        parts = parts[:-1]
    return out


def _regular(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def _scorecard(folder: Path) -> Dict[str, Any]:
    path = folder / SCORECARD_NAME
    if path.stat().st_size > MAX_REPORT_BYTES:
        raise ValueError("the layer scorecard is larger than the Studio reads")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("kind") != SCORECARD_KIND:
        raise ValueError("%s is not a layer scorecard" % SCORECARD_NAME)
    if value.get("schema") != SCORECARD_SCHEMA:
        raise ValueError("unsupported layer scorecard schema: " + json.dumps(value.get("schema")))
    return value


def judge_command(folder: Path) -> List[str]:
    """The exact `replay_judge.py report --json` command a judge folder is read with."""
    judge = folder / JUDGE_FOLDER
    return [sys.executable, str(_SCRIPTS / "replay_judge.py"), "report",
            "--verdicts", str(judge / JUDGE_FILES[0]), "--key", str(judge / JUDGE_FILES[1]),
            "--calibration", str(judge / JUDGE_FILES[2]), "--json"]


def _judge(folder: Path) -> Dict[str, Any]:
    try:
        done = subprocess.run(judge_command(folder), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, timeout=JUDGE_TIMEOUT_SECONDS,
                              check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("the judge report did not run: " + type(exc).__name__) from exc
    try:
        value = json.loads(done.stdout) if done.returncode == 0 else None
    except ValueError:
        value = None
    if not isinstance(value, dict):
        lines = (done.stderr or "").strip().splitlines()
        raise ValueError(lines[-1] if lines else "the judge report printed no section")
    return value


def engine_reports(repository: Optional[Path], relative: Any) -> Dict[str, Any]:
    """`{scorecard, judge, errors}` for the run whose rows came from `relative`."""
    reports: Dict[str, Any] = {"scorecard": None, "judge": None, "errors": []}
    if repository is None or not isinstance(relative, str):
        return reports
    # The nearest folder holding a document decides it: a broken one is an error, never a
    # reason to show a farther folder's document instead.
    scorecard_seen = judge_seen = False
    for folder in _folders(Path(repository), relative):
        if not scorecard_seen and _regular(folder / SCORECARD_NAME):
            scorecard_seen = True
            try:
                reports["scorecard"] = _scorecard(folder)
            except (OSError, UnicodeError, ValueError, RecursionError) as exc:
                reports["errors"].append("layer scorecard: " + str(exc))
        if (not judge_seen and (folder / JUDGE_FOLDER).is_dir()
                and all(_regular(folder / JUDGE_FOLDER / name) for name in JUDGE_FILES)):
            judge_seen = True
            try:
                reports["judge"] = _judge(folder)
            except ValueError as exc:
                reports["errors"].append("diff-quality judge: " + str(exc))
    return reports
