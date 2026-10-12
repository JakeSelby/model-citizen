"""The shared decision-provider seam: one contract for "may this action proceed, and how".

Three operations, transport-agnostic, so a local policy file and a remote control plane answer
the same questions in the same shape:

    decide(action, counterparty, context) -> Decision
    record(action_outcome) -> None
    learn(approval_stream) -> None

`Action` carries an action class and, when the caller knows it, the grade `grade-bash.py`
already assigns a command (0 reversible, 3 irreversible). `counterparty` is the
`repo:<name>/<branch>` slug `counterparty()` derives, so a policy written against a repository
and branch matches whatever asks the question.

Two providers ship here. `none` is the default and governs nothing: every action is allowed at
autonomy level 3. `local` reads a user-level and a per-repository policy file, merges them, and
resolves a level from the result.
A provider that answers over a transport lives in `harness_core.decisions` and is imported only
when a configuration names it; `jev` is the one that ships.
Nothing in this module reaches the network. `grade-bash.py` consults the selected provider for
every Bash command it would otherwise let through, but only when `governance.provider` is not
`none`, and it imports this module only then: under `none` the hook's output is exactly what the
stance alone gives. The binding is tighten-only, so a provider can turn an allow into an ask and
never an ask into an allow, and a configured provider that raises asks rather than allows.
`docs/runtime-controls.md` describes the binding from the user's side.

`Decision.outcome` has three values — `allow`, `ask`, `deny`. Neither provider here ever denies;
`deny` exists because a provider that can refuse must have somewhere to say so, and a consumer
written against the contract should handle it from the first day.
"""
import contextlib
import importlib.util
import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]

# The classes a caller may ask about. Closed on purpose: a typo in a policy file that silently
# governs nothing is worse than a policy file that refuses to load.
ACTION_CLASSES = ("coding.shell_exec", "coding.git_commit", "coding.git_push", "coding.deploy",
                  "coding.file_write", "coding.pr_merge")
OUTCOMES = ("allow", "ask", "deny")
LEVELS = (1, 2, 3)
ACTION_OUTCOMES = ("completed", "skipped", "failed")

# What each autonomy variant implies when no policy names a level for the action. The same
# thresholds `grade-bash.py` grades under, so a repository with no policy file behaves exactly
# as the stance in force already says it should.
STANCE_LEVELS = {"execute": 3, "confirm-writes": 2, "ask": 1, "off": 3}
# Unresolvable stance: the strictest variant, for `grade-bash.py`'s reason — a gate that cannot
# read its own configuration must not widen authority on the strength of not knowing.
STRICTEST_LEVEL = 1
# A deploy is never fully autonomous, whatever a policy file says. A cap in the file may lower
# this and may not raise it.
BUILTIN_CAPS = {"coding.deploy": 2}
# An action whose grade the caller does not know is judged at 1, never at 3 — `grade-bash.py`'s
# rule for a command it cannot recognise, for the same reason.
UNKNOWN_GRADE = 1

POLICY_FILE = Path(".agent-harness") / "governance.json"
# The user-level policy sits beside `config.json`, in the same schema as the repository file.
USER_POLICY_NAME = "governance.json"
USER_LAYER = "user policy"
REPOSITORY_LAYER = "repository policy"
BUILTIN_SOURCE = "built-in"
POLICY_KEYS = ("defaults", "pairs", "caps")
# The point name a provider's own outcome rows carry in the decision ledger. Deliberately not in
# `decisions.POINTS`: that tuple names the hook points whose rows the report expects to exist.
# The rows `grade-bash.py` writes for each decision it asks a provider for carry its own
# `governance` point instead.
LEDGER_POINT = "decision-provider"


class PolicyError(ValueError):
    """A governance policy file that cannot be honoured as written.

    Raised rather than shrugged off: a malformed policy is a governance question nobody has
    answered, and reading it as "no policy" would quietly grant whatever it meant to withhold.
    Every call site turns this into one line naming the file and the fault.
    """


@dataclass(frozen=True)
class Action:
    """What is about to happen: an action class, and the grade of it where one is known."""

    action_class: str
    grade: Optional[int] = None

    def effective_grade(self) -> int:
        return UNKNOWN_GRADE if self.grade is None else self.grade

    def as_dict(self) -> Dict[str, Any]:
        return {"action_class": self.action_class, "grade": self.grade}


@dataclass(frozen=True)
class ActionOutcome:
    """How an action that was decided on actually turned out."""

    action_class: str
    counterparty: str
    outcome: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Decision:
    """One provider's answer.

    `injected_cognition` is the part a runtime may put in front of an agent or a person:
    `rule_matches` names every policy line that bore on the answer, verbatim enough to quote,
    and the two messages are text for the agent and for the user, or None when there is none.
    """

    outcome: str
    autonomy_level: int
    provider: str
    reason: str
    injected_cognition: Dict[str, Any] = field(default_factory=lambda: dict(empty_cognition()))

    def as_dict(self) -> Dict[str, Any]:
        return {"outcome": self.outcome, "autonomy_level": self.autonomy_level,
                "injected_cognition": self.injected_cognition, "provider": self.provider,
                "reason": self.reason}


def empty_cognition() -> Dict[str, Any]:
    return {"rule_matches": [], "agent_message": None, "user_message": None}


# ------------------------------------------------------------------ the shared ledger

_HOOK_MODULES: Dict[str, Any] = {}


def _hook_module(name: str, root: Optional[Path] = None):
    """A module from the hook directory, loaded by file, or None when it is not there.

    `claude/hooks` is a symlink to `policy/hooks`; both names are tried for the reason
    `catalog.posture_module` gives. Loading the hook's own file is what keeps one writer for
    the decision ledger and one derivation of the counterparty slug.
    """
    root = ROOT if root is None else Path(root)
    key = str(root) + "/" + name
    if key in _HOOK_MODULES:
        return _HOOK_MODULES[key]
    path = next((p for p in (root / "policy" / "hooks" / (name + ".py"),
                             root / "claude" / "hooks" / (name + ".py")) if p.is_file()), None)
    if path is None:
        return None
    try:
        spec = importlib.util.spec_from_file_location("harness_" + name.replace("-", "_"),
                                                      str(path))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception:
        return None
    _HOOK_MODULES[key] = module
    return module


UNKNOWN_COUNTERPARTY = "repo:unknown/local"


def counterparty(cwd: Optional[str] = None) -> str:
    """`repo:<name>/<branch>` for a working directory; `locate` says how it is derived."""
    return locate(cwd)[0]


def locate(cwd: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """`(counterparty, working-tree root)` for a working directory.

    Derived through `usage-log.py`'s own git helper, as the usage ledger derives its repository
    and branch, with one difference: `<name>` is the repository's, read from the common git
    directory, so a linked worktree in a directory named for its task still names the
    repository it belongs to, and a policy keyed on `repo:<name>` governs every worktree of it.
    The ledger's `repo` field keeps the worktree directory's name. Outside a repository,
    `repo:unknown/local` and no root: a policy must not silently match a directory that only
    happens to share a basename with one.
    """
    cwd = str(Path(cwd).expanduser()) if cwd else os.getcwd()
    module = _hook_module("usage-log")
    if module is None or not os.path.isdir(cwd):
        return UNKNOWN_COUNTERPARTY, None
    top = module.git(cwd, "rev-parse", "--show-toplevel")
    if not top:
        return UNKNOWN_COUNTERPARTY, None
    common = module.git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    branch = module.git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    return "repo:" + repository_name(top, common) + "/" + (branch or "unknown"), top


def repository_name(top: str, common: Optional[str]) -> str:
    """The repository's name: the directory holding its common `.git`, or a bare `<name>.git`.

    Falls back to the working tree's own directory name when the common directory is unknown,
    as it is under a git too old for `--path-format`.
    """
    if common:
        common = os.path.normpath(common)
        base = os.path.basename(common)
        if base == ".git":
            name = os.path.basename(os.path.dirname(common))
        else:
            name = base[:-4] if base.endswith(".git") else base
        if name:
            return name
    return os.path.basename(top.rstrip("/")) or "unknown"


def _ledger():
    return _hook_module("decisions")


def ledger_path(target: Optional[str] = None) -> Optional[Path]:
    module = _ledger()
    if module is None:
        return None
    return Path(target) if target else module.path()


def append_outcome(action_outcome: ActionOutcome, target: Optional[str] = None) -> Optional[str]:
    """Write one action outcome to the decision ledger. Returns its id, or None.

    The existing `decisions.jsonl`, through the existing writer, never a second file: a reader
    of the ledger sees provider activity beside the hook decisions it already holds. Never
    raises, for the reason that module gives — a log that can change an answer is worse than no
    log — so a caller gets None and carries on.
    """
    module = _ledger()
    if module is None:
        return None
    text = json.dumps({"action": action_outcome.action_class,
                       "counterparty": action_outcome.counterparty,
                       "metadata": action_outcome.metadata}, sort_keys=True)
    return module.record(LEDGER_POINT, action_outcome.outcome, text=text,
                         key=action_outcome.action_class + "|" + action_outcome.counterparty
                             + "|" + text,
                         target=str(target) if target else None)


_SUPPRESSED = []


@contextlib.contextmanager
def events_suppressed():
    """Inside this block, `append_event` writes nothing and says so.

    For a reporting command: `harness decide` asks a provider what it would answer, and a
    provider that reaches a service would otherwise leave a row behind for a question nobody
    acted on. Re-entrant, so nesting it cannot turn logging back on early.
    """
    _SUPPRESSED.append(True)
    try:
        yield
    finally:
        _SUPPRESSED.pop()


def suppressed() -> bool:
    """Whether a reporting command is holding writes open. Read by every ledger this module has."""
    return bool(_SUPPRESSED)


def append_event(name: str, detail: Dict[str, Any], target: Optional[str] = None) -> bool:
    """Write one `event` row to the decision ledger. Never raises; says whether it wrote.

    An event carries no `decision_id`, so `decisions.read_rows` skips it and
    `harness usage --by decision` never counts provider bookkeeping as a judgment nobody
    labelled. `read_events` below reads them back.
    """
    if suppressed():
        return False
    module = _ledger()
    if module is None:
        return False
    try:
        if not module.enabled():
            return False
        # The ledger's own append: one line, one write, and no second file to keep in step.
        module._append({"kind": "event", "point": LEDGER_POINT, "event": name,
                        "ts": module.now_ts(), "detail": detail,
                        "harness_version": module.harness_version()},
                       str(target) if target else None)
        return True
    except Exception:
        return False


def read_events(target: Optional[str] = None) -> List[Dict[str, Any]]:
    """Every `event` row in the ledger, oldest first. An unreadable file is no events."""
    path = ledger_path(target)
    if path is None:
        return []
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    events = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("kind") == "event":
            events.append(row)
    return events


def validate_approvals(stream: Iterable[Any]) -> List[Dict[str, Any]]:
    """The approval records in `stream`, or a `PolicyError` naming the first bad one.

    Shape only: `{action, counterparty, approved: bool, at: iso8601}`. Nothing here learns from
    them yet — the providers that ship today cannot — and validating the shape is exactly what
    keeps the seam honest: a caller that hands over garbage is told so now rather than when
    something finally reads the file.
    """
    if isinstance(stream, (str, bytes, dict)) or stream is None:
        raise PolicyError("learn: the approval stream must be an iterable of records")
    records = []
    for index, item in enumerate(stream):
        where = "learn: record %d" % index
        if not isinstance(item, dict):
            raise PolicyError(where + " is not an object")
        for name in ("action", "counterparty", "at"):
            value = item.get(name)
            if not isinstance(value, str) or not value.strip():
                raise PolicyError(where + " needs a non-empty string `" + name + "`")
        if not isinstance(item.get("approved"), bool):
            raise PolicyError(where + " needs a boolean `approved`")
        if not _iso8601(item["at"]):
            raise PolicyError(where + " has `at` that is not an ISO 8601 timestamp: "
                              + item["at"])
        records.append(dict(item))
    return records


def _iso8601(value: str) -> bool:
    """Whether `value` is an ISO 8601 instant the stdlib on this floor can read.

    Python 3.9's `fromisoformat` does not accept a trailing `Z`, which is the spelling every
    row in this repository's ledgers uses, so the one substitution is made before parsing.
    """
    import datetime

    try:
        datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return False
    return True


# ------------------------------------------------------------------ the policy file


def stance_level(variant: Optional[str] = None, root: Optional[Path] = None) -> int:
    """The level the autonomy stance implies, or the strictest when nothing resolves it.

    Resolved through `posture.py`, the same file every hook asks, so the provider and the
    command gate cannot disagree about which variant is in force.
    """
    if variant is None:
        module = _hook_module("posture", root)
        try:
            variant = None if module is None else module.selected("autonomy", None)
        except Exception:
            variant = None
    return STANCE_LEVELS.get(variant or "", STRICTEST_LEVEL)


def _level(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value not in LEVELS:
        raise PolicyError(where + " must be an autonomy level of 1, 2 or 3, not " + repr(value))
    return value


def _class_map(block: Any, where: str) -> Dict[str, int]:
    if not isinstance(block, dict):
        raise PolicyError(where + " must be an object of action class to level")
    out = {}
    for name, value in block.items():
        if name not in ACTION_CLASSES:
            raise PolicyError(where + " names an unknown action class " + repr(name)
                              + "; known classes are " + ", ".join(ACTION_CLASSES))
        out[name] = _level(value, where + "." + name)
    return out


def load_policy(path: Path) -> Dict[str, Dict[str, Any]]:
    """The policy at `path`, validated, or the empty policy when the file is not there.

    A missing file is a repository that has chosen nothing, which resolves to the stance. A
    file that exists and cannot be honoured is a `PolicyError`.
    """
    path = Path(path)
    empty: Dict[str, Dict[str, Any]] = {"defaults": {}, "pairs": {}, "caps": {}}
    if not path.is_file():
        return empty
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PolicyError("governance policy " + str(path) + " cannot be read: " + str(exc))
    if not isinstance(raw, dict):
        raise PolicyError("governance policy " + str(path) + " must be a JSON object")
    unknown = sorted(set(raw) - set(POLICY_KEYS))
    if unknown:
        raise PolicyError("governance policy " + str(path) + " has unknown key(s) "
                          + ", ".join(unknown) + "; known keys are " + ", ".join(POLICY_KEYS))
    policy = dict(empty)
    pairs = raw.get("pairs", {})
    if not isinstance(pairs, dict):
        raise PolicyError("governance policy " + str(path) + ": pairs must be an object keyed "
                          "by counterparty")
    try:
        policy["defaults"] = _class_map(raw.get("defaults", {}), "defaults")
        policy["caps"] = _class_map(raw.get("caps", {}), "caps")
        policy["pairs"] = {slug: _class_map(block, "pairs." + str(slug))
                           for slug, block in pairs.items()}
    except PolicyError as exc:
        # Two files can now be read, so a fault inside one must say which.
        raise PolicyError("governance policy " + str(path) + ": " + str(exc))
    return policy


def user_policy_file(env: Optional[Dict[str, str]] = None) -> Path:
    """Where the user-level policy lives: beside `config.json`, found the way it is found.

    `HARNESS_HOME`, then `HOME`, then the account's home, then `.config/agent-harness` — the
    same lookup `bin/harness` and `posture.py` use for `config.json`, so a temporary home moves
    both files together.
    """
    env = os.environ if env is None else env
    home = env.get("HARNESS_HOME") or env.get("HOME") or str(Path.home())
    return Path(home) / ".config" / "agent-harness" / USER_POLICY_NAME


def merge_policies(layers: Iterable[Tuple[str, Path, Dict[str, Dict[str, Any]]]]
                   ) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str]]:
    """Merge `(label, path, policy)` layers, lowest precedence first, into one policy.

    Returns `(policy, sources)`. `defaults` and each pair's classes take the later layer's value;
    `caps` take the lower value, so no layer can lift a ceiling another set. `sources` maps each
    entry's rule name (`defaults.<class>`, `pairs.<slug>.<class>`, `caps.<class>`) to the
    `"<label> <path>"` that supplied it, for the reason and the rule matches.
    """
    merged: Dict[str, Dict[str, Any]] = {"defaults": {}, "pairs": {}, "caps": {}}
    sources: Dict[str, str] = {}
    for label, path, policy in layers:
        where = label + " " + str(path)
        for name, level in policy.get("defaults", {}).items():
            merged["defaults"][name] = level
            sources["defaults." + name] = where
        for slug, block in policy.get("pairs", {}).items():
            target = merged["pairs"].setdefault(slug, {})
            for name, level in block.items():
                target[name] = level
                sources["pairs." + slug + "." + name] = where
        for name, level in policy.get("caps", {}).items():
            current = merged["caps"].get(name)
            if current is None or level <= current:
                merged["caps"][name] = level
                sources["caps." + name] = where
    return merged, sources


def repository_slug(counterparty: str) -> Optional[str]:
    """`repo:<name>` for a `repo:<name>/<branch>` slug, or None when there is no branch part.

    The name ends at the first `/`, because a branch name may itself contain slashes.
    """
    if not counterparty.startswith("repo:") or "/" not in counterparty:
        return None
    return counterparty.split("/", 1)[0]


def cap_for(action_class: str, policy: Dict[str, Dict[str, Any]]) -> Optional[int]:
    """The ceiling on this action's level: the lower of the file's cap and the built-in one."""
    caps = [c for c in (policy.get("caps", {}).get(action_class),
                        BUILTIN_CAPS.get(action_class)) if c is not None]
    return min(caps) if caps else None


def outcome_for(level: int, grade: int) -> str:
    """`allow` or `ask`, by the thresholds `grade-bash.py` already grades under.

    Level 3 allows every grade; level 2 asks at grade 2 and up; level 1 asks at grade 1 and up.
    """
    if level >= 3:
        return "allow"
    return "ask" if grade >= level else "allow"


# ------------------------------------------------------------------ the providers


class DecisionProvider(ABC):
    """The contract every provider answers, whatever sits behind it."""

    name = ""

    @abstractmethod
    def decide(self, action: Action, counterparty: str,
               context: Optional[Dict[str, Any]] = None) -> Decision:
        """Whether this action may proceed, and what to put in front of the agent."""

    @abstractmethod
    def record(self, action_outcome: ActionOutcome) -> None:
        """Note how an action turned out. Never raises, never changes a decision."""

    @abstractmethod
    def learn(self, approval_stream: Iterable[Any]) -> None:
        """Take a stream of past approvals. May be a no-op; must reject a malformed stream."""


class NullProvider(DecisionProvider):
    """Governs nothing: every action is allowed at level 3.

    The default, and the behaviour of a harness with no governance at all — which is what every
    installation has today. It still records outcomes, so the ledger is populated before any
    policy exists to be measured against it.
    """

    name = "none"

    def __init__(self, target: Optional[str] = None):
        self.target = target

    def decide(self, action, counterparty, context=None):
        return Decision(outcome="allow", autonomy_level=3, provider=self.name,
                        reason="governance: none", injected_cognition=empty_cognition())

    def record(self, action_outcome):
        append_outcome(action_outcome, self.target)

    def learn(self, approval_stream):
        return None


class LocalProvider(DecisionProvider):
    """A user-level and a per-repository policy file, resolved against the autonomy stance.

    Both files share one schema. `.agent-harness/governance.json` in the repository, and
    `governance.json` beside `config.json` for the user:

        {"defaults": {"coding.git_push": 2},
         "pairs": {"repo:agent-harness/main": {"coding.git_push": 1},
                   "repo:agent-harness": {"coding.pr_merge": 2}},
         "caps": {"coding.deploy": 2}}

    The repository file wins over the user file for `defaults` and pair entries; `caps` combine
    by the lower value. Resolution is the exact `repo:<name>/<branch>` pair, then the
    whole-repository `repo:<name>` pair, then the class default, then the level the autonomy
    stance implies. A cap is a ceiling the resolved level never exceeds, and `coding.deploy`
    carries a built-in cap of 2 that a file may lower and may not raise: a deploy is never fully
    autonomous. The reason and every rule match name the file that supplied the level.
    """

    name = "local"

    def __init__(self, root: Optional[str] = None, policy_path: Optional[str] = None,
                 variant: Optional[str] = None, target: Optional[str] = None,
                 user_policy_path: Optional[str] = None):
        self.root = Path(root) if root else Path.cwd()
        self.policy_path = Path(policy_path) if policy_path else self.root / POLICY_FILE
        self.user_policy_path = (Path(user_policy_path) if user_policy_path
                                 else user_policy_file())
        self.variant = variant
        self.target = target
        self._policy = None
        self._sources: Dict[str, str] = {}

    def policy_files(self) -> List[Path]:
        """The files this provider reads, lowest precedence first."""
        return [self.user_policy_path, self.policy_path]

    def policy(self) -> Dict[str, Dict[str, Any]]:
        if self._policy is None:
            layers = [(USER_LAYER, self.user_policy_path, load_policy(self.user_policy_path)),
                      (REPOSITORY_LAYER, self.policy_path, load_policy(self.policy_path))]
            self._policy, self._sources = merge_policies(layers)
        return self._policy

    def decide(self, action, counterparty, context=None):
        policy = self.policy()
        name = action.action_class
        matches = []
        keys = [counterparty]
        whole = repository_slug(counterparty)
        if whole is not None and whole != counterparty:
            keys.append(whole)
        source = None
        for key in keys:
            level = policy["pairs"].get(key, {}).get(name)
            if level is not None:
                source = "pairs." + key + "." + name
                break
        if source is None and policy["defaults"].get(name) is not None:
            level, source = policy["defaults"][name], "defaults." + name
        if source is None:
            level = stance_level(self.variant, self.root)
            source, origin = "autonomy stance", None
        else:
            origin = self._sources.get(source)
        matches.append(source + " = " + str(level) + (" (" + origin + ")" if origin else ""))
        cap = cap_for(name, policy)
        capped = None
        if cap is not None and level > cap:
            file_cap = policy["caps"].get(name)
            cap_origin = (self._sources.get("caps." + name)
                          if file_cap is not None and file_cap == cap else BUILTIN_SOURCE)
            capped = "caps." + name + " = " + str(cap) + " (" + cap_origin + ")"
            matches.append(capped)
            level = cap
        grade = action.effective_grade()
        outcome = outcome_for(level, grade)
        described = source + (" in " + origin if origin else "")
        if capped:
            # The level reported is the cap's, so the reason names the cap, not only the rule
            # it lowered.
            described += ", capped by " + capped
        reason = ("governance: local, level %d, grade %s -> %s (%s)"
                  % (level, "unknown" if action.grade is None else str(grade), outcome,
                     described))
        cognition = empty_cognition()
        cognition["rule_matches"] = matches
        if outcome == "ask":
            cognition["agent_message"] = (
                name + " on " + counterparty + " is level " + str(level)
                + ": state the exact command and wait for an explicit yes.")
        return Decision(outcome=outcome, autonomy_level=level, provider=self.name,
                        reason=reason, injected_cognition=cognition)

    def record(self, action_outcome):
        append_outcome(action_outcome, self.target)

    def learn(self, approval_stream):
        records = validate_approvals(approval_stream)
        append_event("learn", {"records": len(records), "provider": self.name}, self.target)
        return None


PROVIDERS = {NullProvider.name: NullProvider, LocalProvider.name: LocalProvider}
# A provider that answers over a transport lives in `harness_core.decisions` and imports this
# module, so it is named here and loaded only when a configuration asks for it.
TRANSPORT_PROVIDERS = {"jev": ("harness_core.decisions.jev", "JevProvider")}


def provider_class(name: str):
    """The class a provider name selects, importing a transport provider on demand."""
    if name in PROVIDERS:
        return PROVIDERS[name]
    if name in TRANSPORT_PROVIDERS:
        import importlib

        module_name, attribute = TRANSPORT_PROVIDERS[name]
        try:
            return getattr(importlib.import_module(module_name), attribute)
        except Exception as exc:
            raise PolicyError("governance.provider " + repr(name) + " cannot be loaded: "
                              + str(exc))
    raise PolicyError("governance.provider " + repr(name) + " is not a provider; known "
                      "providers are " + ", ".join(sorted(set(PROVIDERS) | set(TRANSPORT_PROVIDERS))))


def select_provider(config: Optional[Dict[str, Any]] = None, **kwargs) -> DecisionProvider:
    """The provider `governance.provider` names, `none` by default.

    An unknown name is refused rather than defaulted: a configuration that asks for governance
    nobody can supply must not come back as governance nobody applied.
    """
    block = (config or {}).get("governance")
    name = block.get("provider") if isinstance(block, dict) else None
    name = name if isinstance(name, str) and name.strip() else NullProvider.name
    cls = provider_class(name)
    if cls is NullProvider:
        kwargs.pop("root", None)
        kwargs.pop("policy_path", None)
        kwargs.pop("user_policy_path", None)
        kwargs.pop("variant", None)
    if name in TRANSPORT_PROVIDERS:
        # A provider that leaves the machine reads its own opt-in block, so selecting it is
        # never on its own enough to make it call anything.
        kwargs.setdefault("config", config or {})
    return cls(**kwargs)
