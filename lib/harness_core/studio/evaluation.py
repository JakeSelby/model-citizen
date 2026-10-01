"""Read the Measured evaluation contracts that native replay rows and proof bundles carry.

The engines in `scripts/` own every analysis: this module only recognises which landed contract a
row answers, refuses a version it does not know by name, and carries the stamps through unchanged.
Nothing here derives a figure, a verdict or a proof status of its own.

Contracts read, each with the engine that writes it:

- the two-arm row (`cost_bench.replay`): arms `bare` and `harness`;
- the one-policy pair (`replay_pair.row_stamp`): arms `bare`, `reference`, `treatment`, and an
  `ablation` record at schema 1 naming its `factors`;
- the variable-arm ablation (`ablations.row_stamp`): `bare`, the `harness` control and the manifest's arm ids,
  and an `ablation` record at schema 2 naming those ids as its `arms`;
- the four-cell design (`unit_economy.row_stamp`): `bare` plus four cells, and a `design` record at
  result schema 1;
- the evaluator pack (`replay_pack.identity`): `pack`, `pack_version`, `pack_commit` and
  `pack_digest` on every row of a pack run;
- the proof bundle (`evidence_bundle.verify`): its result is carried whole, never re-judged.
"""

import importlib.util
import json
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

TWO_ARM = ("bare", "harness")
PAIR_ARMS = ("bare", "reference", "treatment")
PAIR_SCHEMA = 1
ABLATION_SCHEMA = 2
ABLATION_CONTROL = "harness"  # `ablations.CONTROL`: the full-selection arm
DESIGN_NAME = "unit-economy-2x2"
DESIGN_SCHEMA = 1
DESIGN_CELLS = ("base", "unit", "economy", "both")
PACK_FIELDS = ("pack", "pack_version", "pack_commit", "pack_digest")
BUNDLE_SCRIPT = ("scripts", "evidence_bundle.py")


class ContractError(ValueError):
    """A row or bundle that names a contract version Studio cannot read."""


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _pack(row: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    present = [name for name in PACK_FIELDS if name in row]
    if not present:
        return None
    if len(present) != len(PACK_FIELDS) or not all(_text(row[name]) for name in PACK_FIELDS):
        raise ContractError("replay row carries an incomplete evaluator pack identity")
    return {name: row[name] for name in PACK_FIELDS}


def _ablation(record: Any) -> Tuple[str, Dict[str, Any], Tuple[str, ...]]:
    if not isinstance(record, dict):
        raise ContractError("replay row carries an ablation record that is not an object")
    schema = record.get("schema")
    if isinstance(schema, bool) or schema not in (PAIR_SCHEMA, ABLATION_SCHEMA):
        raise ContractError("unsupported ablation stamp schema: " + json.dumps(schema))
    if not _text(record.get("name")) or not _text(record.get("sha256")):
        raise ContractError("replay row carries an ablation record with no name or digest")
    if schema == PAIR_SCHEMA:
        return "pair", dict(record), PAIR_ARMS
    arms = record.get("arms")
    if not isinstance(arms, list) or not arms or not all(_text(arm) for arm in arms):
        raise ContractError("replay row carries an ablation record with no arms")
    return "variable-arm", dict(record), ("bare", ABLATION_CONTROL) + tuple(arms)


def _design(record: Any) -> Dict[str, Any]:
    if not isinstance(record, dict):
        raise ContractError("replay row carries a design record that is not an object")
    if record.get("name") != DESIGN_NAME:
        raise ContractError("unsupported design: " + json.dumps(record.get("name")))
    schema = record.get("schema")
    if isinstance(schema, bool) or schema != DESIGN_SCHEMA:
        raise ContractError("unsupported design result schema: " + json.dumps(schema))
    if not _text(record.get("manifest_sha256")):
        raise ContractError("replay row carries a design record with no manifest digest")
    return dict(record)


def row_contract(row: Mapping[str, Any]) -> Dict[str, Any]:
    """`{shape, arms, pack, ablation, design, registration}` for one native replay row.

    `shape` is `two-arm`, `pair`, `variable-arm` or `four-cell`. An unknown stamp version is a
    `ContractError` naming it, never a silent two-arm read. `registration` is the row's own
    `experiment_protocol` label as written (`evidence` None when the row carries none): Studio
    never promotes an exploratory or unlabelled row to a pre-registered one."""
    shape, arms = "two-arm", TWO_ARM
    ablation = design = None
    if "design" in row and row["design"] is not None:
        design = _design(row["design"])
        shape, arms = "four-cell", ("bare",) + DESIGN_CELLS
    if "ablation" in row and row["ablation"] is not None:
        kind, ablation, ablation_arms = _ablation(row["ablation"])
        if design is None:
            shape, arms = kind, ablation_arms
    if row.get("arm") not in arms:
        raise ContractError("replay row arm %s is not an arm of its %s contract"
                            % (json.dumps(row.get("arm")), shape))
    evidence = row.get("evidence")
    registration = {"evidence": evidence if isinstance(evidence, str) else None,
                    "pre_registration": row.get("pre_registration"),
                    "pre_registration_commit": row.get("pre_registration_commit")}
    return {"shape": shape, "arms": list(arms), "pack": _pack(row), "ablation": ablation,
            "design": design, "registration": registration}


def identity_fields(row: Mapping[str, Any], contract: Mapping[str, Any]) -> Dict[str, Any]:
    """The fields one row's index identity is built from.

    A two-arm row outside a pack keeps the original five fields, so rebuilding an index of rows
    written before these contracts yields the run ids it had. Each landed stamp adds the digest
    that tells two cohorts apart: the pack digest, the ablation or design manifest digest, and the
    schedule seed."""
    fields = {name: row.get(name) for name in ("task", "arm", "rep", "harness_sha", "tag")}
    if contract.get("pack"):
        fields["pack_digest"] = contract["pack"]["pack_digest"]
    if contract.get("ablation"):
        fields["ablation_sha256"] = contract["ablation"]["sha256"]
    if contract.get("design"):
        fields["design_sha256"] = contract["design"]["manifest_sha256"]
    if contract["shape"] != "two-arm" and "schedule_seed" in row:
        fields["schedule_seed"] = row.get("schedule_seed")
    return fields


def _load_bundle_verifier():
    # This checkout's verifier, as `citizen evidence verify` loads it; never the indexed tree's.
    path = Path(__file__).resolve().parents[3].joinpath(*BUNDLE_SCRIPT)
    spec = importlib.util.spec_from_file_location("studio_evidence_bundle", str(path))
    if spec is None or spec.loader is None:
        raise ContractError("the evidence bundle verifier is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_bundle(bundle: Path) -> Dict[str, Any]:
    """The verifier's own result for one bundle: what `citizen evidence verify --json` prints."""
    return _load_bundle_verifier().verify(Path(bundle))


def bound_bundles(repository: Path) -> List[str]:
    """The repository-relative bundles `product.json` binds measured claims to, in order."""
    try:
        document = json.loads((Path(repository) / "product.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    cards = document.get("evidence_cards") if isinstance(document, dict) else None
    out: List[str] = []
    for card in cards if isinstance(cards, list) else []:
        bundle = card.get("bundle") if isinstance(card, dict) else None
        if not _text(bundle):
            continue
        parsed = PurePosixPath(bundle)
        if parsed.is_absolute() or ".." in parsed.parts or "\\" in bundle:
            continue
        if bundle not in out:
            out.append(bundle)
    return out


def proof_status(result: Mapping[str, Any]) -> Dict[str, Any]:
    """The verifier's verdict, verbatim: `verified` only when it returned ok."""
    return {"status": "verified" if result.get("ok") is True else "failed",
            "bundle_id": result.get("bundle_id"),
            "errors": list(result.get("errors") or []),
            "unknown": list(result.get("unknown") or [])}


def detail_contract(record: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """The evaluation block a run detail shows, read from what the index stored."""
    value = record.get("evaluation")
    return dict(value) if isinstance(value, dict) else None

