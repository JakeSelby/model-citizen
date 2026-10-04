#!/usr/bin/env python3
"""A blind, pairwise rubric judge on two runs' final diffs and replies, calibrated on hand labels.

Deterministic graders decide whether a task passed and which rules fired. They cannot say which of
two passing changes kept to its scope, was the better design, or ended on the clearer and more
correct reply. This module asks a pinned model those three questions, and only trusts its answer
on a dimension once it agrees with the maintainer's own labels.

**Blinding.** A pair is two runs of one task, one per arm. `export` writes the pairs file with
each pair's two runs in a seeded random order as `first` and `second`, and nothing naming the arm,
the run or the image; the identities go to a separate key file the labeller and the judge never
read. The judge then shows each pair twice, in a seeded random order and then swapped, as
"response 1" and "response 2". A dimension whose two answers disagree is a tie, flagged
inconsistent, and the inconsistency rate is reported.

**The pin.** `benchmarks/judge/judge.json` names the model, its effort, the rubric prompt in
`benchmarks/judge/rubric.md` and that file's digest; a rubric that differs from its digest is
refused, so every verdict was asked the same question. The model is asked through the replay's own
path: a fresh container of a built arm image (`replay_arms.run_command`), with nothing mounted,
the credential passed by name and the egress proxy as its only way out. No API client or keychain
is involved, and every test passes a fake `ask` instead.

**Calibration.** `export` also writes a local HTML form for the maintainer to label the same pairs;
`calibrate` computes Cohen's kappa between judge and labels per dimension, the judge's position
bias and its length bias beside the labeller's, and admits a dimension only when kappa reaches the
pre-registered floor (0.6 by default). `judge_section` turns verdicts into a win rate per arm pair
for the admitted dimensions only, with a task-clustered bootstrap interval.

What it reads of a run: the final reply from the saved stream, and the diff from a
`<task>-<arm>-<rep>.diff` beside that stream when one exists, otherwise the file edits the stream's
Edit, MultiEdit and Write calls record, which miss any change a shell command made. Standard
library only. How to use it: docs/benchmarks.md#diff-quality-judge.
"""
import argparse
import hashlib
import html
import json
import os
import random
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import replay_arms as arms  # noqa: E402  the container path every replay run takes
import replay_detect  # noqa: E402  where a set's saved streams sit, and their names
import replay_stats  # noqa: E402  the arm order, the Wilson interval and the bootstrap's percentile

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "benchmarks" / "judge" / "judge.json"
FIRST, SECOND, TIE = "first", "second", "tie"
CHOICES = (FIRST, SECOND, TIE)
SHOWN = {"1": 0, "2": 1}
SEED = 1185
JUDGE_TIMEOUT = 600
# The judge reads; it never acts. No tool is allowed, nothing is mounted, and one turn is all it gets.
DENIED_TOOLS = ("Bash", "Edit", "MultiEdit", "Write", "NotebookEdit", "Read", "Glob", "Grep", "Task",
                "WebFetch", "WebSearch")
EDIT_TOOLS = ("Edit", "MultiEdit", "Write")


# --- The pin ---------------------------------------------------------------------------------------

def load_config(path=CONFIG):
    """The pinned judge: `judge.json` with its rubric text read in as `rubric_text`. SystemExit when
    the rubric's digest differs from the pin, so no verdict is asked another question."""
    path = Path(path)
    config = json.loads(path.read_text(encoding="utf-8"))
    data = (path.parent / config["rubric"]).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != config["rubric_sha256"]:
        raise SystemExit("replay-judge: the rubric's sha256 is %s, not the pinned %s; re-pin it in %s on purpose"
                         % (digest, config["rubric_sha256"], path))
    return dict(config, rubric_text=data.decode("utf-8"))


# --- Reading a run ---------------------------------------------------------------------------------

def messages(text):
    """The CLI's messages from a saved stream or a `json` output: one document, or one per line."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        data = None
    if isinstance(data, list):
        return [m for m in data if isinstance(m, dict)]
    if isinstance(data, dict):
        return [data]
    out = []
    for line in (text or "").splitlines():
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if isinstance(message, dict):
            out.append(message)
    return out


def final_reply(text):
    """The `result` of the last result message, or None when the stream holds none."""
    results = [m for m in messages(text) if m.get("type") == "result"]
    return None if not results else str(results[-1].get("result") or "").strip()


def _rel(path):
    path = str(path or "?")
    prefix = arms.WORKDIR.rstrip("/") + "/"
    return path[len(prefix):] if path.startswith(prefix) else path


def _hunk(old, new):
    return ["-" + line for line in (old or "").splitlines()] + ["+" + line for line in (new or "").splitlines()]


def stream_edits(text):
    """The file edits the stream's Edit, MultiEdit and Write calls record, rendered diff-like, in
    call order. A change a shell command made is not among them."""
    out = []
    for message in messages(text):
        content = (message.get("message") or {}).get("content") if message.get("type") == "assistant" else None
        for block in content if isinstance(content, list) else ():
            if not isinstance(block, dict) or block.get("type") != "tool_use" or block.get("name") not in EDIT_TOOLS:
                continue
            spec = block.get("input") or {}
            name = block["name"]
            out.append("--- %s %s" % (name.lower(), _rel(spec.get("file_path"))))
            if name == "Write":
                out.extend(_hunk("", spec.get("content")))
            elif name == "Edit":
                out.extend(_hunk(spec.get("old_string"), spec.get("new_string")))
            else:
                for edit in spec.get("edits") or ():
                    out.extend(_hunk(edit.get("old_string"), edit.get("new_string")))
    return "\n".join(out)


def find_stream(results_path, task, arm, rep):
    """The saved stream of one run, searched where `replay_detect` looks, or None."""
    name = replay_detect.raw_name(task, arm, rep)
    for directory, _ in replay_detect.search_dirs(Path(results_path).parent):
        if (directory / name).is_file():
            return directory / name
    return None


def read_run(results_path, row):
    """`{"diff", "reply", "diff_source"}` of one saved run, or None when its stream is missing or
    holds no final reply. `diff_source` is `diff` for a saved diff file, `stream-edits` otherwise."""
    stream = find_stream(results_path, row["task"], row["arm"], row["rep"])
    if stream is None:
        return None
    text = stream.read_text(encoding="utf-8", errors="replace")
    reply = final_reply(text)
    if reply is None:
        return None
    saved = stream.with_suffix(".diff")
    if saved.is_file():
        return {"diff": saved.read_text(encoding="utf-8", errors="replace"), "reply": reply, "diff_source": "diff"}
    return {"diff": stream_edits(text), "reply": reply, "diff_source": "stream-edits"}


# --- Pairs: blind by construction ------------------------------------------------------------------

def _clip(text, limit):
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[... %d more characters not shown]" % (len(text) - limit)


def task_prompts(manifest):
    """`{task id: prompt}` from a task manifest; a list prompt is joined by lines."""
    document = json.loads(Path(manifest).read_text(encoding="utf-8")) if manifest else {}
    out = {}
    for task in document.get("tasks", []) if isinstance(document, dict) else []:
        prompt = task.get("prompt", "")
        out[task.get("id")] = "\n".join(prompt) if isinstance(prompt, list) else str(prompt)
    return out


def candidates(rows, pair_arms):
    """Every (task, rep) both arms of `pair_arms` ran without an error, in sorted order."""
    runs = {}
    for row in rows:
        if row.get("arm") in pair_arms and not row.get("error"):
            runs.setdefault((row.get("task"), row.get("rep", row.get("trial"))), {})[row["arm"]] = row
    return [(key, cell) for key, cell in sorted(runs.items(), key=lambda item: (str(item[0][0]), str(item[0][1])))
            if len(cell) == len(pair_arms)]


def _spread(found, n, rng):
    """Up to `n` of `found`, taken round-robin over tasks in a seeded order, so no task dominates."""
    by_task = {}
    for item in found:
        by_task.setdefault(item[0][0], []).append(item)
    tasks = sorted(by_task, key=str)
    rng.shuffle(tasks)
    for task in tasks:
        rng.shuffle(by_task[task])
    out = []
    while len(out) < n and any(by_task[t] for t in tasks):
        for task in tasks:
            if by_task[task] and len(out) < n:
                out.append(by_task[task].pop())
    return out


def build_pairs(rows, read, prompts, pair_arms=replay_stats.ARMS, n=None, seed=SEED, max_chars=20000):
    """`(pairs, key)`. Each pair is `{"id", "task", "prompt", "first", "second"}`, where `first`
    and `second` are `{"diff", "reply"}` in a seeded random order; `key` maps each id to the arm and
    rep behind each side. `read(row)` gives a run's `{"diff", "reply", ...}` or None, and a pair
    with an unreadable side is skipped. `n` None takes every pair."""
    rng = random.Random("%s:pairs" % seed)
    found = candidates(rows, pair_arms)
    chosen = _spread(found, len(found) if n is None else n, rng)
    pairs, key = [], {}
    for (task, rep), cell in chosen:
        sides = [(arm, read(cell[arm])) for arm in pair_arms]
        if any(run is None for _, run in sides):
            continue
        rng.shuffle(sides)
        pid = "p%03d" % (len(pairs) + 1)
        pairs.append({"id": pid, "task": task, "prompt": prompts.get(task, ""),
                      "first": {"diff": _clip(sides[0][1]["diff"], max_chars),
                                "reply": _clip(sides[0][1]["reply"], max_chars)},
                      "second": {"diff": _clip(sides[1][1]["diff"], max_chars),
                                 "reply": _clip(sides[1][1]["reply"], max_chars)}})
        key[pid] = {"task": task, "rep": rep, "first": sides[0][0], "second": sides[1][0],
                    "diff_source": {sides[0][0]: sides[0][1].get("diff_source"),
                                    sides[1][0]: sides[1][1].get("diff_source")}}
    return pairs, key


def pairs_document(pairs, config, seed):
    """The blind pairs file: no arm, run or image named anywhere in it, and an empty label slot
    per dimension for the maintainer."""
    dims = config["dimensions"]
    return {"schema": 1, "seed": seed, "dimensions": dims, "rubric_sha256": config["rubric_sha256"],
            "choices": list(CHOICES),
            "pairs": [dict(pair, labels=dict((d, None) for d in dims)) for pair in pairs]}


def length(side):
    return len(side.get("diff") or "") + len(side.get("reply") or "")


# --- The judge -------------------------------------------------------------------------------------

def judge_prompt(rubric, task_prompt, one, two):
    """The one message the judge is asked: the rubric, the task, then the two responses as shown."""
    parts = [rubric.rstrip(), "", "## The task", "", task_prompt or "(no prompt recorded)"]
    for label, side in (("1", one), ("2", two)):
        parts += ["", "## Response %s" % label, "", "### Diff", "", side.get("diff") or "(no change)",
                  "", "### Final reply", "", side.get("reply") or "(no reply)"]
    return "\n".join(parts) + "\n"


def parse_verdict(text, dims):
    """`{dim: {"preference": "1" | "2" | "tie", "reason"}}` from the judge's answer. ValueError when
    the answer holds no such object for every dimension."""
    start, end = (text or "").find("{"), (text or "").rfind("}")
    if start < 0 or end < start:
        raise ValueError("the judge returned no JSON object")
    data = json.loads(text[start:end + 1])
    out = {}
    for dim in dims:
        cell = data.get(dim) if isinstance(data, dict) else None
        preference = str(cell.get("preference")).strip().lower() if isinstance(cell, dict) else None
        if preference not in ("1", "2", "tie"):
            raise ValueError("the judge gave no valid preference for %s" % dim)
        out[dim] = {"preference": preference, "reason": str(cell.get("reason") or "").strip()}
    return out


def _as_side(preference, shown):
    """A shown-order answer ("1", "2" or "tie") as the pairs file's side, given the sides shown."""
    return TIE if preference == "tie" else shown[SHOWN[preference]]


def judge_pair(pair, ask, config, seed=SEED):
    """One verdict: the pair asked twice, in a seeded random order and then swapped.

    `ask(prompt)` returns the model's text. Per dimension the preference is `first`, `second` or
    `tie` in the pairs file's terms; two answers that disagree make a tie, flagged inconsistent. A
    pass whose answer cannot be read leaves the verdict with an `error` and no dimensions."""
    dims = config["dimensions"]
    order = [FIRST, SECOND]
    random.Random("%s:%s" % (seed, pair["id"])).shuffle(order)
    verdict = {"id": pair["id"], "task": pair["task"], "seed": seed, "model": config["model"],
               "effort": config["effort"], "rubric_sha256": config["rubric_sha256"], "order": order,
               "lengths": {FIRST: length(pair[FIRST]), SECOND: length(pair[SECOND])},
               "passes": [], "dimensions": None, "error": None}
    for shown in (order, order[::-1]):
        prompt = judge_prompt(config["rubric_text"], pair.get("prompt"), pair[shown[0]], pair[shown[1]])
        try:
            answer = parse_verdict(ask(prompt), dims)
        except Exception as exc:  # an unreadable answer is unknown, never a tie
            verdict["error"] = "%s: %s" % (type(exc).__name__, exc)
            return verdict
        verdict["passes"].append({"shown": list(shown), "answer": answer})
    out = {}
    for dim in dims:
        sides = [_as_side(p["answer"][dim]["preference"], p["shown"]) for p in verdict["passes"]]
        consistent = sides[0] == sides[1]
        out[dim] = {"preference": sides[0] if consistent else TIE, "consistent": consistent,
                    "reasons": [p["answer"][dim]["reason"] for p in verdict["passes"]]}
    verdict["dimensions"] = out
    return verdict


def judge_command(config, cap=None):
    """The judge's command line inside the container: the pinned model and effort, one turn, no
    tool, no MCP server, no session kept, the prompt on standard input."""
    settings = {"permissions": {"deny": list(DENIED_TOOLS)}}
    return ["claude", "-p", "--model", config["model"], "--effort", config["effort"], "--output-format", "json",
            "--max-turns", "1", "--strict-mcp-config", "--no-session-persistence",
            "--max-budget-usd", "%g" % (config["run_cap_usd"] if cap is None else cap),
            "--settings", json.dumps(settings, sort_keys=True)]


def container_ask(image, config, network, proxy, launch=subprocess.run, client=None):
    """An `ask` that runs the judge in a fresh container of `image`, as a replay runs an arm: nothing
    mounted, the credential by name, the egress network its one way out. RuntimeError on a failed
    or errored run."""
    env = arms.arm_env(proxy)
    counter = [0]

    def ask(prompt):
        counter[0] += 1
        name = "model-citizen-judge-%d-%d" % (os.getpid(), counter[0])
        command = arms.run_command(image, None, judge_command(config), network, env, name, stdin=True)
        try:
            done = launch(command, input=prompt, env=client or arms.client_env(), timeout=JUDGE_TIMEOUT,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        except subprocess.TimeoutExpired:
            launch(arms.kill_command(name), env=client or arms.client_env(), stdout=subprocess.PIPE,
                   stderr=subprocess.PIPE, universal_newlines=True)
            raise RuntimeError("the judge timed out")
        results = [m for m in messages(done.stdout) if m.get("type") == "result"]
        if done.returncode or not results or results[-1].get("is_error"):
            raise RuntimeError("the judge's run failed (exit %s)" % done.returncode)
        return str(results[-1].get("result") or "")
    return ask


# --- Calibration -----------------------------------------------------------------------------------

def cohen_kappa(first, second, categories=CHOICES):
    """Cohen's kappa of two raters over the same items; None when there are none or chance
    agreement is total, where kappa is undefined."""
    if len(first) != len(second):
        raise ValueError("kappa needs the same items from both raters")
    n = len(first)
    if not n:
        return None
    observed = sum(1 for a, b in zip(first, second) if a == b) / n
    chance = sum((first.count(c) / n) * (second.count(c) / n) for c in categories)
    if chance >= 1:
        return None
    return (observed - chance) / (1 - chance)


def read_labels(document, dims):
    """`{pair id: {dim: choice}}` from a filled pairs file (`pairs[].labels`) or the form's download
    (`{"labels": {id: {dim: choice}}}`). A blank or unknown choice is left out. ValueError on
    neither shape."""
    if isinstance(document.get("labels"), dict):
        raw = document["labels"]
    elif isinstance(document.get("pairs"), list):
        raw = dict((p.get("id"), p.get("labels") or {}) for p in document["pairs"])
    else:
        raise ValueError("labels must be a filled pairs file or the form's labels download")
    out = {}
    for pid, cell in raw.items():
        chosen = dict((d, cell.get(d)) for d in dims if isinstance(cell, dict) and cell.get(d) in CHOICES)
        if chosen:
            out[pid] = chosen
    return out


def position_bias(verdicts, dims):
    """Per dimension, how often a decided answer chose the response shown first, over every pass,
    with its Wilson interval; `biased` when the interval excludes one half."""
    out = {}
    for dim in dims:
        firsts = decided = 0
        for verdict in verdicts:
            for p in verdict.get("passes") or ():
                choice = p["answer"][dim]["preference"]
                if choice != "tie":
                    decided += 1
                    firsts += 1 if choice == "1" else 0
        low, high = replay_stats.wilson(firsts, decided)
        out[dim] = {"decided": decided, "shown_first": firsts,
                    "rate": firsts / decided if decided else None,
                    "interval": None if low is None else [low, high],
                    "biased": low is not None and (low > 0.5 or high < 0.5)}
    return out


def _longer_rate(choices, lengths):
    """(longer chosen, decided with unequal lengths) over `{id: choice}`."""
    longer = decided = 0
    for pid, choice in choices.items():
        size = lengths.get(pid)
        if choice == TIE or size is None or size[FIRST] == size[SECOND]:
            continue
        decided += 1
        longer += 1 if (size[FIRST] > size[SECOND]) == (choice == FIRST) else 0
    return longer, decided


def length_bias(verdicts, labels, dims):
    """Per dimension, how often the judge's decided preference went to the longer response, beside
    how often the labeller's did over the same pairs; the gap is the judge's own length bias."""
    lengths = dict((v["id"], v["lengths"]) for v in verdicts if not v.get("error"))
    out = {}
    for dim in dims:
        judged = dict((v["id"], v["dimensions"][dim]["preference"]) for v in verdicts if not v.get("error"))
        human = dict((pid, cell[dim]) for pid, cell in labels.items() if dim in cell and pid in judged)
        j_longer, j_n = _longer_rate(dict((pid, judged[pid]) for pid in human), lengths)
        h_longer, h_n = _longer_rate(human, lengths)
        judge_rate = j_longer / j_n if j_n else None
        human_rate = h_longer / h_n if h_n else None
        out[dim] = {"judge_longer": j_longer, "judge_decided": j_n, "judge_rate": judge_rate,
                    "human_longer": h_longer, "human_decided": h_n, "human_rate": human_rate,
                    "gap": None if judge_rate is None or human_rate is None else judge_rate - human_rate}
    return out


def inconsistency(verdicts, dims):
    """Per dimension, the share of read verdicts whose two passes disagreed, and the count of
    verdicts that could not be read."""
    read = [v for v in verdicts if not v.get("error")]
    out = {}
    for dim in dims:
        flipped = sum(1 for v in read if not v["dimensions"][dim]["consistent"])
        out[dim] = {"pairs": len(read), "inconsistent": flipped, "rate": flipped / len(read) if read else None}
    return {"dimensions": out, "errors": len(verdicts) - len(read)}


def calibrate(verdicts, labels, config, kappa_floor=None):
    """Judge-versus-labeller agreement per dimension, the bias audits and the admission verdict.

    A dimension is admitted only when kappa over the pairs both rated reaches `kappa_floor` (the
    pinned floor by default); an undefined kappa is never admitted."""
    dims = config["dimensions"]
    floor = config["kappa_floor"] if kappa_floor is None else kappa_floor
    judged = dict((v["id"], v) for v in verdicts if not v.get("error"))
    agreement = {}
    for dim in dims:
        ids = sorted(pid for pid, cell in labels.items() if dim in cell and pid in judged)
        human = [labels[pid][dim] for pid in ids]
        judge = [judged[pid]["dimensions"][dim]["preference"] for pid in ids]
        kappa = cohen_kappa(judge, human)
        confusion = dict((j, dict((h, 0) for h in CHOICES)) for j in CHOICES)
        for j, h in zip(judge, human):
            confusion[j][h] += 1
        agreement[dim] = {"pairs": len(ids), "kappa": kappa,
                          "agreement": sum(1 for j, h in zip(judge, human) if j == h) / len(ids) if ids else None,
                          "confusion": confusion, "admitted": kappa is not None and kappa >= floor}
    return {"schema": 1, "model": config["model"], "effort": config["effort"],
            "rubric_sha256": config["rubric_sha256"], "kappa_floor": floor,
            "admitted": [d for d in dims if agreement[d]["admitted"]],
            "agreement": agreement, "position_bias": position_bias(list(judged.values()), dims),
            "length_bias": length_bias(list(judged.values()), labels, dims),
            "inconsistency": inconsistency(verdicts, dims)}


def _num(value):
    return "undefined" if value is None else "%.3f" % value


def render_calibration(result):
    lines = ["Judge calibration: %s at %s effort, kappa floor %s" % (result["model"], result["effort"],
                                                                     _num(result["kappa_floor"]))]
    for dim, cell in result["agreement"].items():
        bias = result["position_bias"][dim]
        size = result["length_bias"][dim]
        flips = result["inconsistency"]["dimensions"][dim]
        lines.append("  %s: kappa %s over %d pair(s), %s; agreement %s" % (
            dim, _num(cell["kappa"]), cell["pairs"], "admitted" if cell["admitted"] else "not admitted",
            _num(cell["agreement"])))
        lines.append("    position: shown-first chosen %s of %d decided answer(s)%s" % (
            _num(bias["rate"]), bias["decided"], ", biased" if bias["biased"] else ""))
        lines.append("    length: longer chosen by the judge %s, by the labeller %s, gap %s" % (
            _num(size["judge_rate"]), _num(size["human_rate"]), _num(size["gap"])))
        lines.append("    order swap: %d of %d pair(s) inconsistent (%s), counted as ties" % (
            flips["inconsistent"], flips["pairs"], _num(flips["rate"])))
    if result["inconsistency"]["errors"]:
        lines.append("  %d verdict(s) could not be read and are left out" % result["inconsistency"]["errors"])
    return lines


# --- The summary section ---------------------------------------------------------------------------

def _ordered(pair_arms):
    known = [a for a in replay_stats.ARMS if a in pair_arms]
    return tuple(known + sorted((a for a in pair_arms if a not in known), key=str))


def _cluster_interval(scores_by_task, seed, resamples):
    tasks = sorted(scores_by_task, key=str)
    rng = random.Random(seed)
    alpha = (1 - replay_stats.CONFIDENCE) / 2
    means = []
    for _ in range(resamples):
        picks = [tasks[rng.randrange(len(tasks))] for _ in tasks]
        pooled = [s for t in picks for s in scores_by_task[t]]
        means.append(sum(pooled) / len(pooled))
    means.sort()
    return [replay_stats._rank(means, alpha), replay_stats._rank(means, 1 - alpha)]


def judge_section(verdicts, key, calibration, seed=replay_stats.SEED, resamples=replay_stats.RESAMPLES):
    """`(section, text)`: per arm pair, the treatment arm's win rate over the reference on each
    dimension calibration admitted, a tie counting one half, with a task-clustered percentile
    bootstrap interval. A dimension not admitted is listed and given no rate."""
    admitted = list(calibration.get("admitted") or [])
    cells = {}
    for verdict in verdicts:
        if verdict.get("error") or verdict["id"] not in key:
            continue
        sides = key[verdict["id"]]
        ref, treat = _ordered((sides[FIRST], sides[SECOND]))
        cell = cells.setdefault((ref, treat), {})
        for dim in admitted:
            choice = verdict["dimensions"][dim]["preference"]
            score = 0.5 if choice == TIE else (1.0 if sides[choice] == treat else 0.0)
            cell.setdefault(dim, {}).setdefault(sides["task"], []).append(score)
    pairs = []
    for (ref, treat), cell in sorted(cells.items()):
        dims = {}
        for dim in admitted:
            by_task = cell.get(dim, {})
            scores = [s for values in by_task.values() for s in values]
            dims[dim] = {"pairs": len(scores), "tasks": len(by_task),
                         "wins": scores.count(1.0), "ties": scores.count(0.5), "losses": scores.count(0.0),
                         "win_rate": sum(scores) / len(scores) if scores else None,
                         "interval": _cluster_interval(by_task, seed, resamples) if scores else None}
        pairs.append({"reference": ref, "treatment": treat, "dimensions": dims})
    section = {"model": calibration.get("model"), "rubric_sha256": calibration.get("rubric_sha256"),
               "admitted": admitted, "not_admitted": sorted(set(calibration.get("agreement", {})) - set(admitted)),
               "pairs": pairs, "inconsistency": inconsistency(verdicts, admitted),
               "method": "task-clustered percentile bootstrap", "seed": seed}
    lines = ["", "Diff-quality judge: the treatment's win rate over the reference, a tie counting one half"]
    if not admitted:
        lines.append("  no dimension is admitted; calibrate the judge before reading it")
    for pair in pairs:
        lines.append("  %s over %s:" % (pair["treatment"], pair["reference"]))
        for dim, cell in pair["dimensions"].items():
            interval = cell["interval"]
            lines.append("    %s: %s, interval %s; %d win(s), %d tie(s), %d loss(es) over %d task(s)" % (
                dim, _num(cell["win_rate"]),
                "undefined" if interval is None else "[%s, %s]" % (_num(interval[0]), _num(interval[1])),
                cell["wins"], cell["ties"], cell["losses"], cell["tasks"]))
    for dim, cell in section["inconsistency"]["dimensions"].items():
        lines.append("  %s: %d of %d pair(s) changed answer when the order swapped (%s), counted as ties"
                     % (dim, cell["inconsistent"], cell["pairs"], _num(cell["rate"])))
    if section["inconsistency"]["errors"]:
        lines.append("  %d verdict(s) could not be read and are left out" % section["inconsistency"]["errors"])
    if section["not_admitted"]:
        lines.append("  not admitted, so not reported: %s" % ", ".join(section["not_admitted"]))
    return section, "\n".join(lines) + "\n"


# --- The labelling form ----------------------------------------------------------------------------

FORM = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Diff-quality labels</title>
<style>body{font:15px/1.45 system-ui,sans-serif;max-width:72rem;margin:2rem auto;padding:0 1rem}
.pair{border-top:2px solid #333;margin-top:2rem}.sides{display:grid;grid-template-columns:1fr 1fr;gap:1rem}
pre{white-space:pre-wrap;background:#f4f4f4;padding:.5rem;max-height:30rem;overflow:auto;font-size:13px}
fieldset{margin:.5rem 0}</style></head><body>
<h1>Diff-quality labels</h1>
<p>For each pair and dimension choose the better response, or tie. Nothing here says which arm
produced which response. The rubric is below; when done, save the labels and pass the file to
<code>replay_judge.py calibrate --labels</code>.</p>
<pre id="rubric"></pre><div id="pairs"></div>
<p><button id="save">Save labels</button></p>
<script id="data" type="application/json">%s</script>
<script>
const doc = JSON.parse(document.getElementById("data").textContent);
document.getElementById("rubric").textContent = doc.rubric;
const root = document.getElementById("pairs");
function el(tag, text) { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; return e; }
for (const pair of doc.pairs) {
  const box = el("section"); box.className = "pair";
  box.appendChild(el("h2", pair.id + " (task " + pair.task + ")"));
  box.appendChild(el("pre", pair.prompt));
  const sides = el("div"); sides.className = "sides";
  for (const side of ["first", "second"]) {
    const col = el("div"); col.appendChild(el("h3", side));
    col.appendChild(el("h4", "Diff")); col.appendChild(el("pre", pair[side].diff));
    col.appendChild(el("h4", "Final reply")); col.appendChild(el("pre", pair[side].reply));
    sides.appendChild(col);
  }
  box.appendChild(sides);
  for (const dim of doc.dimensions) {
    const set = el("fieldset"); set.appendChild(el("legend", dim));
    for (const choice of doc.choices) {
      const label = el("label"); const input = el("input");
      input.type = "radio"; input.name = pair.id + ":" + dim; input.value = choice;
      label.appendChild(input); label.appendChild(document.createTextNode(" " + choice + " "));
      set.appendChild(label);
    }
    box.appendChild(set);
  }
  root.appendChild(box);
}
document.getElementById("save").onclick = () => {
  const labels = {};
  for (const pair of doc.pairs) {
    labels[pair.id] = {};
    for (const dim of doc.dimensions) {
      const picked = document.querySelector('input[name="' + pair.id + ":" + dim + '"]:checked');
      labels[pair.id][dim] = picked ? picked.value : null;
    }
  }
  const blob = new Blob([JSON.stringify({schema: 1, seed: doc.seed, labels: labels}, null, 1)], {type: "application/json"});
  const a = el("a"); a.href = URL.createObjectURL(blob); a.download = "labels.json"; a.click();
};
</script></body></html>
"""


def form_html(document, rubric):
    """A self-contained local form over the blind pairs; its data is embedded as JSON and only ever
    set as text, so nothing in a diff or reply runs."""
    data = json.dumps(dict(document, rubric=rubric), sort_keys=True).replace("</", "<\\/")
    return FORM % data


# --- Command line ----------------------------------------------------------------------------------

def _write(path, text):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text, encoding="utf-8")


def _jsonl(path):
    return replay_detect.read_jsonl(path)


def cmd_export(args, config):
    rows = _jsonl(args.results)
    pair_arms = tuple(args.arms.split(","))
    if len(pair_arms) != 2:
        raise SystemExit("replay-judge: --arms names two arms, reference then treatment")
    pairs, key = build_pairs(rows, lambda row: read_run(args.results, row), task_prompts(args.tasks), pair_arms,
                             None if args.all else args.n, args.seed, config["max_chars"])
    out = Path(args.out)
    document = pairs_document(pairs, config, args.seed)
    _write(out / "pairs.json", json.dumps(document, indent=1, sort_keys=True) + "\n")
    _write(out / "pairs.key.json", json.dumps({"schema": 1, "seed": args.seed, "arms": list(pair_arms),
                                               "pairs": key}, indent=1, sort_keys=True) + "\n")
    _write(out / "label.html", form_html(document, config["rubric_text"]))
    print("replay-judge: %d pair(s) to %s; label with label.html, keep pairs.key.json from the labeller"
          % (len(pairs), out))
    return 0


def cmd_run(args, config):
    import experiment_protocol  # the evidence label every paid run carries
    pairs = json.loads(Path(args.pairs).read_text(encoding="utf-8"))["pairs"]
    if args.dry_run:
        print("replay-judge: would ask %s %d time(s), each pair twice:" % (config["model"], 2 * len(pairs)))
        print("  " + " ".join(arms.run_command(args.image, None, judge_command(config), "<egress>",
                                               arms.arm_env("<proxy>"), "<name>", stdin=True)))
        return 0
    stamp = experiment_protocol.admit(args.pre_registration, args.exploratory, ROOT, "replay-judge")
    if not os.environ.get(arms.CREDENTIAL):
        raise SystemExit("replay-judge: %s is not set; the judge authenticates with it, passed by name"
                         % arms.CREDENTIAL)
    client = arms.client_env({arms.CREDENTIAL: os.environ[arms.CREDENTIAL]})
    out = []
    with arms.egress(args.image) as net:
        ask = container_ask(args.image, config, net["network"], net["url"], client=client)
        for pair in pairs:
            out.append(dict(judge_pair(pair, ask, config, args.seed), **stamp))
    _write(args.out, "".join(json.dumps(v, sort_keys=True) + "\n" for v in out))
    print("replay-judge: %d verdict(s) to %s, %d unreadable" % (len(out), args.out,
                                                               sum(1 for v in out if v.get("error"))))
    return 0


def cmd_calibrate(args, config):
    labels = read_labels(json.loads(Path(args.labels).read_text(encoding="utf-8")), config["dimensions"])
    result = calibrate(_jsonl(args.verdicts), labels, config, args.kappa_floor)
    if args.out:
        _write(args.out, json.dumps(result, indent=1, sort_keys=True) + "\n")
    print("\n".join(render_calibration(result)))
    return 0


def cmd_report(args, config):
    key = json.loads(Path(args.key).read_text(encoding="utf-8"))["pairs"]
    calibration = json.loads(Path(args.calibration).read_text(encoding="utf-8"))
    section, text = judge_section(_jsonl(args.verdicts), key, calibration)
    print(json.dumps(section, indent=1, sort_keys=True) if args.json else text, end="" if not args.json else "\n")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="replay_judge.py", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export", help="write blind pairs, their key and a local labelling form")
    export.add_argument("--results", required=True, help="a saved set's results.jsonl")
    export.add_argument("--tasks", help="the task manifest the set ran, for each task's prompt")
    export.add_argument("--out", required=True, help="the directory to write into")
    export.add_argument("--arms", default=",".join(replay_stats.ARMS), help="reference,treatment")
    export.add_argument("--n", type=int, default=None, help="pairs to take; the pinned calibration size by default")
    export.add_argument("--all", action="store_true", help="every pair, for a full judged evaluation")
    export.add_argument("--seed", type=int, default=SEED)
    run = sub.add_parser("run", help="judge a pairs file in fresh containers of a built arm image")
    run.add_argument("--pairs", required=True)
    run.add_argument("--image", required=True, help="a built arm image, the bare arm's by convention")
    run.add_argument("--out", help="the verdicts file to write")
    run.add_argument("--seed", type=int, default=SEED)
    run.add_argument("--dry-run", action="store_true", help="print the command line and call no model")
    run.add_argument("--pre-registration")
    run.add_argument("--exploratory", action="store_true")
    cal = sub.add_parser("calibrate", help="agreement with hand labels, bias audits and admission")
    cal.add_argument("--verdicts", required=True)
    cal.add_argument("--labels", required=True)
    cal.add_argument("--kappa-floor", type=float, default=None, help="the pre-registered floor; pinned by default")
    cal.add_argument("--out")
    rep = sub.add_parser("report", help="the judge section: win rates on admitted dimensions")
    rep.add_argument("--verdicts", required=True)
    rep.add_argument("--key", required=True)
    rep.add_argument("--calibration", required=True)
    rep.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    config = load_config()
    if args.command == "export" and args.n is None:
        args.n = config["calibration_pairs"]
    if args.command == "run" and not args.dry_run and not args.out:
        parser.error("run needs --out unless --dry-run")
    return {"export": cmd_export, "run": cmd_run, "calibrate": cmd_calibrate, "report": cmd_report}[args.command](
        args, config)


if __name__ == "__main__":
    sys.exit(main())
