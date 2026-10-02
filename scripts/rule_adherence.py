#!/usr/bin/env python3
"""Whether one unit's own rule held in a replay run, read offline from the run's saved stream.

A unit's manifest names its instruments; each `detector:<id>` is a detector in
`policy/hooks/rule-detectors.py`, which `replay_detect` already runs over every stream `--raw`
kept. This module only maps those detections onto one unit: a run is `hit` when any of the unit's
detectors fired, `compliant` when every one ran and none fired, and `unknown` when a detector could
not read the run and none fired, so a missing stream is never counted as compliance. A unit whose
manifest names no detector this checkout ships is `unmeasured` on every run, never zero (AD-22).

The reading is "the rule's behaviour was observed or not", whatever the arm loaded: detectors run
ungated (`replay_detect.ungated`), so a cell that does not load the unit is measured on the same
behaviour as one that does. That is what makes the unit's effect on adherence a difference.

Standard library only, and no model call. Reading and limits: docs/benchmarks.md, "Unit evals".
"""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import replay_detect  # noqa: E402  the detector registry and the stream reader

DETECTOR_PREFIX = "detector:"
COMPLIANT, HIT, UNKNOWN, UNMEASURED = "compliant", "hit", "unknown", "unmeasured"
VALUES = (COMPLIANT, HIT, UNKNOWN, UNMEASURED)
MANIFEST_FILES = ("primitives/manifests.json", "policy/hooks/manifests.json")


def unit_manifest(root, entry):
    """The manifest of `entry` (`kind/unit`) in the checkout at `root`, or None."""
    kind, _, unit = entry.partition("/")
    for name in MANIFEST_FILES:
        path = Path(root) / name
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        found = (data.get(kind) or {}).get(unit) if isinstance(data, dict) else None
        if isinstance(found, dict):
            return found
    return None


def detector_ids(manifest):
    """The detector ids a manifest's `instruments` names, in its order."""
    return tuple(item[len(DETECTOR_PREFIX):] for item in (manifest or {}).get("instruments") or []
                 if isinstance(item, str) and item.startswith(DETECTOR_PREFIX))


def registry_ids(module=None):
    """Every detector id the registry holds."""
    module = module or replay_detect.load_detectors()
    return tuple(d.id for d in replay_detect.ungated(module))


def detectors_sha256(path=replay_detect.DETECTORS_PATH):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def coverage(root, registry):
    """`{"readable": [...], "unsupported": [...]}`: every `detector:` id the checkout's manifests
    name, split by whether the registry holds it."""
    named = set()
    for name in MANIFEST_FILES:
        try:
            data = json.loads((Path(root) / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for units in (data.values() if isinstance(data, dict) else ()):
            if isinstance(units, dict):
                for manifest in units.values():
                    if isinstance(manifest, dict):
                        named.update(detector_ids(manifest))
    known = set(registry)
    return {"readable": sorted(named & known), "unsupported": sorted(named - known)}


def classify(detections, ids, registry):
    """`{"value", "hits", "unsupported"}` for one run. `detections` are its `replay_detect` rows
    (`detector`, `count`, and `error` when unread); `ids` the unit's detector ids."""
    unsupported = [d for d in ids if d not in set(registry)]
    supported = [d for d in ids if d in set(registry)]
    if not supported:
        return {"value": UNMEASURED, "hits": [], "unsupported": unsupported}
    by_id = {row.get("detector"): row for row in detections}
    hits, unread = [], []
    for ident in supported:
        count = (by_id.get(ident) or {}).get("count")
        if type(count) is not int:
            unread.append(ident)
        elif count > 0:
            hits.append(ident)
    value = HIT if hits else UNKNOWN if unread else COMPLIANT
    return {"value": value, "hits": hits, "unsupported": unsupported}


def read(raw_path, ids, reader, module=None):
    """One saved stream classified for a unit's detector `ids`; `reader` parses the stream, as
    `cost_bench.cli_messages` does. An unreadable file or stream is `unknown`, never `hit`; an
    empty `ids` is `unmeasured`."""
    module = module or replay_detect.load_detectors()
    registry = replay_detect.ungated(module)
    names = [d.id for d in registry]
    if not [d for d in ids if d in names]:
        return classify([], ids, names)
    rows = replay_detect.path_rows({}, Path(raw_path), reader, module, registry)
    return classify(rows, ids, names)


def stamp(rows, detections, ids, registry, detectors_digest=None):
    """`rows` with `rule_adherence` (one of `VALUES`) and `rule_adherence_hits` set from the set's
    own detection rows (`replay_detect.detect_saved`), matched by task, arm and rep."""
    runs = {}
    for row in detections or []:
        runs.setdefault((row.get("task"), row.get("arm"), row.get("rep")), []).append(row)
    out = []
    for row in rows:
        found = classify(runs.get((row.get("task"), row.get("arm"), row.get("rep")), []), ids, registry)
        out.append(dict(row, rule_adherence=found["value"], rule_adherence_hits=found["hits"],
                        rule_detectors_sha256=detectors_digest))
    return out


def main(argv=None):
    """Print which manifest detector ids the registry can read: the spike's exit check."""
    root = Path(__file__).resolve().parents[1]
    found = coverage(root, registry_ids())
    print("readable %d, unsupported %d" % (len(found["readable"]), len(found["unsupported"])))
    for ident in found["unsupported"]:
        print("  unsupported: %s" % ident)
    return 0 if not found["unsupported"] else 1


if __name__ == "__main__":
    sys.exit(main())
