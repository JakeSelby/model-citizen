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
import os
import subprocess
import sys
import threading
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

SCORECARD_NAME = "scorecard.json"
SCORECARD_KIND = "layer-scorecard"  # `layer_scorecard.KIND`
SCORECARD_SCHEMA = 1  # `layer_scorecard.SCHEMA`
JUDGE_FOLDER = "judge"
JUDGE_FILES = ("verdicts.jsonl", "pairs.key.json", "calibration.json")  # `layer_scorecard.load_judge`
MAX_REPORT_BYTES = 4 * 1024 * 1024
JUDGE_TIMEOUT_SECONDS = 120
JUDGE_CACHE_LIMIT = 64
# One judge child at a time, server-wide; a request that cannot get the turn within one report's
# timeout is told the report is busy rather than starting another (as `spend.SpendBusy` does).
_JUDGE_TURN = threading.Lock()
_JUDGE_CACHE: Dict[Tuple[Any, ...], Tuple[Optional[Dict[str, Any]], Optional[str]]] = {}
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
        # The child never finished: nothing the engine said, so nothing to keep.
        raise JudgeUnavailable("the judge report did not run: " + type(exc).__name__) from exc
    try:
        value = json.loads(done.stdout) if done.returncode == 0 else None
    except ValueError:
        value = None
    if not isinstance(value, dict):
        lines = (done.stderr or "").strip().splitlines()
        raise JudgeRefused(lines[-1] if lines else "the judge report printed no section")
    return value


class JudgeBusy(RuntimeError):
    """Another judge report is being read."""


class JudgeRefused(ValueError):
    """The judge's own refusal: it finished and printed no section. Kept per folder state."""


class JudgeUnavailable(ValueError):
    """The judge did not finish (timeout or launch failure). Never kept; the next read retries."""


def _judge_state(folder: Path) -> Tuple[Any, ...]:
    """The judge folder and each file's identity and size: a changed file is a new report."""
    judge = folder / JUDGE_FOLDER
    state: List[Any] = [str(judge.resolve())]
    for name in JUDGE_FILES:
        info = os.stat(judge / name, follow_symlinks=False)
        state.append((name, info.st_ino, info.st_size, info.st_mtime_ns))
    return tuple(state)


def judge_report(folder: Path) -> Dict[str, Any]:
    """The judge section for `folder`, read once per folder state and one child at a time."""
    key = _judge_state(folder)
    cached = _JUDGE_CACHE.get(key)
    if cached is None:
        if not _JUDGE_TURN.acquire(timeout=JUDGE_TIMEOUT_SECONDS):
            raise JudgeBusy("the judge report is being read; reload in a moment")
        try:
            cached = _JUDGE_CACHE.get(key)
            if cached is None:
                try:
                    cached = (_judge(folder), None)
                except JudgeRefused as exc:
                    cached = (None, str(exc))
                if len(_JUDGE_CACHE) >= JUDGE_CACHE_LIMIT:
                    _JUDGE_CACHE.clear()
                _JUDGE_CACHE[key] = cached
        finally:
            _JUDGE_TURN.release()
    value, error = cached
    if error is not None:
        raise ValueError(error)
    return dict(value or {})


def _outside(path: Path, folder: Path) -> bool:
    """True when `path`, or any folder on the way to it, resolves anywhere but where it lies:
    `folder` is lexical under the resolved repository, so a link at any component moves it."""
    lexical = Path(os.path.normpath(str(path)))
    return Path(os.path.realpath(str(lexical))) != lexical or not str(lexical).startswith(
        str(Path(os.path.normpath(str(folder)))) + os.sep)


def _judge_folder_problem(folder: Path) -> Optional[str]:
    """Why `folder`'s `judge/` cannot be read as this run's: a link, or a path outside it."""
    judge = folder / JUDGE_FOLDER
    if judge.is_symlink():
        return "the judge folder is a link; nothing is read through a link"
    try:
        if _outside(judge, folder):
            return "the judge folder resolves outside the run folder"
    except OSError:
        return "the judge folder cannot be resolved"
    return None


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
            if _outside(folder / SCORECARD_NAME, folder):
                reports["errors"].append("layer scorecard: the scorecard resolves outside the run folder")
            else:
                try:
                    reports["scorecard"] = _scorecard(folder)
                except (OSError, UnicodeError, ValueError, RecursionError) as exc:
                    reports["errors"].append("layer scorecard: " + str(exc))
        if judge_seen or not ((folder / JUDGE_FOLDER).is_dir() or (folder / JUDGE_FOLDER).is_symlink()):
            continue
        judge_seen = True
        problem = _judge_folder_problem(folder)
        if problem is not None:
            reports["errors"].append("diff-quality judge: " + problem)
        elif all(_regular(folder / JUDGE_FOLDER / name) for name in JUDGE_FILES):
            try:
                reports["judge"] = judge_report(folder)
            except (OSError, ValueError, JudgeBusy) as exc:
                reports["errors"].append("diff-quality judge: " + str(exc))
        else:
            judge_seen = False  # an incomplete folder holds no report; a farther one may
    return reports
