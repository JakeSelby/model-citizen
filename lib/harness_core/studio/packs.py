"""Find the evaluator packs a Studio replay can run, through `scripts/replay_pack.py`.

A pack is its own git repository and must sit outside the harness checkout (`replay_pack`
refuses one inside it), so the harness ships none in its tree. The Studio looks for packs beside
the checkout: in the checkout's parent folder and, for a linked worktree, beside the main
checkout it belongs to, unless `HARNESS_STUDIO_PACKS` names the folders to search instead
(`os.pathsep`-separated), which is how a test points discovery at its own fixtures. Each candidate is opened the way `cost_bench.py replay --pack` opens it,
at `HEAD` through `git archive`, so the name, version, commit and digest shown are the ones a run
pins. Every production-tier set of a pack is its own choice, so a rule-targeted set or a public
outcome subset is picked by name, version, digest and set exactly as the production set is. The
browser chooses a pack by name, digest and set; the path never comes from it.
"""

import importlib.util
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

PACK_FILE = "pack.json"
# The pack the benchmark documentation names for the harness's own replay (docs/benchmarks.md).
PREFERRED = "model-citizen-evals"
TIER = "production"
PACKS_ENV = "HARNESS_STUDIO_PACKS"
MAX_CANDIDATES = 512
_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"


def _replay_pack():
    spec = importlib.util.spec_from_file_location("studio_replay_pack", str(_SCRIPTS / "replay_pack.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _main_checkout(repository: Path) -> Optional[Path]:
    try:
        done = subprocess.run(["git", "-C", str(repository), "rev-parse", "--path-format=absolute",
                               "--git-common-dir"], stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    common = done.stdout.strip()
    return Path(common).resolve().parent if done.returncode == 0 and common else None


def search_folders(repository: Path) -> List[Path]:
    """Where packs are looked for: the folders `HARNESS_STUDIO_PACKS` names, else the checkout's
    parent and its main checkout's parent."""
    configured = os.environ.get(PACKS_ENV, "")
    if configured.strip():
        return [Path(item).expanduser().resolve() for item in configured.split(os.pathsep)
                if item.strip()]
    repository = Path(repository).resolve()
    parents = [repository.parent]
    main = _main_checkout(repository)
    if main is not None and main.parent not in parents:
        parents.append(main.parent)
    return parents


def candidates(repository: Path) -> List[Path]:
    """Folders in the search folders (`search_folders`) that hold a `pack.json`."""
    parents = search_folders(repository)
    found: List[Path] = []
    for parent in parents:
        try:
            children = sorted(parent.iterdir())[:MAX_CANDIDATES]
        except OSError:
            continue
        for child in children:
            if (not child.is_symlink() and child.is_dir() and (child / PACK_FILE).is_file()
                    and child.resolve() not in found):
                found.append(child.resolve())
    return found


def _production_sets(module, pack) -> List[str]:
    """The pack's production-tier sets in name order: the production set itself, and any other set
    the pack declares at that tier, such as a rule-targeted set or a public outcome subset."""
    sets = (pack.get("document") or {}).get("sets") or {}
    return sorted(name for name, spec in sets.items()
                  if module.set_tier(name, spec or {}) == TIER)


def _entries(module, source: Path, repository: Path) -> List[Dict[str, Any]]:
    """One entry per production-tier set of the pack at `source`, each pinned as a run pins it."""
    pack = module.open_pack(source, "HEAD", None, repository)
    out = []
    try:
        for set_name in _production_sets(module, pack):
            tasks, _manifest = module.load_set(pack, set_name, TIER)
            out.append({"name": pack["name"], "version": pack["version"], "commit": pack["commit"],
                        "digest": pack["digest"], "short_digest": pack["digest"][:12],
                        "set": set_name, "source": str(source),
                        "tasks": [{"id": task["id"], "label": task["id"].replace("-", " ").title(),
                                   "long": task.get("long") is True} for task in tasks]})
    finally:
        module.close_pack(pack)
    return out


def discover(repository: Path) -> Dict[str, Any]:
    """`{packs, default_digest, skipped}`: every usable pack in a stable order, the default, and
    each candidate the pack loader refused with its reason."""
    repository = Path(repository).resolve()
    module = _replay_pack()
    packs: List[Dict[str, Any]] = []
    skipped: List[Dict[str, str]] = []
    for source in candidates(repository):
        try:
            packs.extend(_entries(module, source, repository))
        except (SystemExit, OSError, ValueError, KeyError, TypeError, AttributeError,
                subprocess.SubprocessError) as exc:
            # A pack.json or task.json that is JSON but not an object is skipped, not a crash.
            skipped.append({"source": str(source), "reason": str(exc)})
    packs.sort(key=lambda item: (item["name"], item["version"], item["source"],
                                 item["set"] != TIER, item["set"]))
    preferred = [item for item in packs if item["name"] == PREFERRED and item["set"] == TIER]
    default = (preferred or packs or [None])[0]
    return {"packs": packs, "default_digest": default["digest"] if default else None,
            "skipped": skipped}


def select(repository: Path, name: Any, digest: Any, set_name: Any = None) -> Dict[str, Any]:
    """The discovered pack set with exactly this name, digest and set; a selection naming no set
    is the production set, as a request saved before sets were offered reads. ValueError when none
    matches."""
    wanted = TIER if set_name is None else set_name
    for item in discover(repository)["packs"]:
        if item["name"] == name and item["digest"] == digest and item["set"] == wanted:
            return item
    raise ValueError("the chosen evaluator pack is not available at that digest and set")
