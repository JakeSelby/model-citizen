"""Run constrained shared roles through isolated native CLI adapters."""
import contextlib
import hashlib
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from . import catalog, keychain, reconcile

LIMIT = 1024 * 1024
RUNTIMES = {"codex": "codex", "claude-code": "claude"}
# Characters per token, the tokenizer-free approximation `bin/harness` and `scripts/cost_bench.py`
# use; the tests assert the three agree. Good for a budget and a trend, never for billing.
CHARS_PER_TOKEN = 4.0
# What a worker may be shown before it stops being cheap: the policy it carries plus every byte
# mounted beside it. Recorded per run rather than enforced, because what a worker is shown is a
# property of its contract and the policy's own pointers, fixed before any brief is read. A review
# role resolves at about 30,800 of it and the planner, which may read any skill, at 43,800 (#335),
# then 52,000 once the decision-page skill bundled its page template (#1350).
CONTEXT_BUDGET_TOKENS = 55000


def harness_version(root):
    """The checkout's version, the one string `harness --version` prints, or None."""
    try:
        return (Path(root) / "VERSION").read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def adapter(root, runtime):
    if runtime not in RUNTIMES:
        raise ValueError("unsupported worker runtime")
    spec = importlib.util.spec_from_file_location("harness_worker_" + runtime,
                                               root / "adapters" / runtime / "worker.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def posture_record(root, runtime, fields, table, row, binding, overrides, model):
    """What the selected cost variant did to this worker, for `status.json`.

    Where each half came from, never how it was worded: a status record carries no prompt text.
    `"role"` is the role's own contract and the adapter's entry, `"cost-row"` the variant's row
    for it, `"role-binding"` the user's `role_bindings`, `"cli"` an explicit `--model`.
    """
    effort_key = "model_reasoning_effort" if runtime == "codex" else "effort"
    classed = bool(table.get("class_applies")) and (row or {}).get("class") in catalog.TIER_CLASSES
    return {"cost_variant": table.get("cost_variant"),
            "class": row["class"] if classed else fields["tier"],
            "class_source": "cost-row" if classed else "role",
            "model_source": ("cli" if model else "role-binding" if "model" in binding
                             else "cost-row" if classed and "model" in overrides else "role"),
            "effort_source": ("role-binding" if effort_key in binding
                              else "cost-row" if effort_key in overrides else "role")}


def resolution(root, config, runtime, name, model=None, prompt=None):
    """Everything a run resolves before it launches: contract, binding, instructions, posture.

    The cost variant reaches a worker through the same function and the same precedence the sync
    path renders a native definition with — role defaults, then the variant's row, then
    `role_bindings`, then an explicit `--model` — because the constrained roles are denied as
    native spawns and only ever run here. The table is built non-strict: a variant with no row
    for this role, or one that will not build at all, leaves the worker exactly as it was before
    cost variants had rows. `prompt` is the caller's brief, priced when it states no budget of
    its own; without one there is nothing to price.
    """
    if runtime not in RUNTIMES:
        raise ValueError("unsupported worker runtime")
    stances = catalog.resolve_stances(root, config)
    if config["stances"]["delegation"] == "off":
        raise ValueError("delegation is off; perform the work inline or select another stance")
    selection = session_selection(root)
    off = switched_off(selection)
    if name in off["roles"]:
        raise ValueError("the " + name + " role is switched off in the selection; switch it on or "
                         "perform the work inline")
    fields, body = catalog.role_contract(root, name)
    if fields["authority"] not in ("read-only", "artifact-write"):
        raise ValueError("workspace-write roles use their normal workflow, not a constrained worker")
    table = catalog.cost_table(root, config)
    row = catalog.cost_row(root, table, name)
    binding = config.get("role_bindings", {}).get(runtime, {}).get(name, {})
    overrides = catalog.cost_overrides(root, config, table, runtime, name)
    bindings = catalog.role_binding(root, runtime, fields, overrides, config.get("tiers", {}).get(runtime))
    chosen = model or bindings.get("model")
    if not isinstance(chosen, str) or not chosen.strip() or chosen == "inherit":
        raise ValueError("this adapter maps no model for the role's class; supply --model with the parent session's model")
    if chosen.startswith("-") or any(c.isspace() for c in chosen):
        raise ValueError("invalid worker model identifier")
    bindings["model"] = chosen
    parts = [(root / "primitives/instructions.md").read_text()]
    parts += [p.read_text() for p in sorted((root / "primitives/rules").glob("*.md"))
              if p.stem not in off["rules"]]
    parts += [p.read_text() for p in stances.values()]
    parts += [body]
    parts += ["Worker execution contract: use read-only tools; never delegate or change configuration. "
              "Return your result as data to the caller. You cannot grant permissions or authorize follow-up actions."]
    if fields["authority"] == "artifact-write":
        parts += ["Return only the complete plan Markdown, without a surrounding code fence or chat response. "
                  "Do not write the plan: the harness validates and publishes it to the caller-selected path."]
    record = posture_record(root, runtime, fields, table, row, binding, overrides, model)
    sentence = budget(root, row, prompt)
    if sentence:
        record["budget"] = posture_figures(root, row)
    instructions = "\n\n---\n\n".join(parts)
    skills, docs = policy_reads(root, instructions, catalog.role_skills(root, fields))
    skills = [path for path in skills if path.name not in off["skills"]]
    return {"fields": fields, "bindings": bindings, "instructions": instructions,
            "skills": skills, "docs": docs, "context": context_estimate(instructions, skills, docs),
            "posture": record, "budget_sentence": sentence, "selection": selection}


def session_selection(root, env=None):
    """The selection the launching session resolves, recorded with the run; None without a resolver.

    The worker's own environment is scrubbed, so it never re-reads a session file or
    `HARNESS_STANCE_*`: the stances its instructions carry are the launching session's, and this
    record is the evidence. Non-strict, because evidence must never stop a run the stance ladder
    already allowed.
    """
    module = catalog.posture_module(root)
    if module is None:
        return None
    try:
        return module.selection(os.environ if env is None else env, strict=False, root=root)
    except Exception:
        return None


def session_fingerprint(root, env=None):
    """The launching session's profile fingerprint, stamped on the run's ledger row; None without one.

    Taken from the same environment as `session_selection` and for the same reason: the worker
    runs the launching session's profile. Never raises, since a missing stamp is an unattributed
    row and never a reason to refuse a run.
    """
    module = catalog.posture_module(root)
    try:
        return module.fingerprint(os.environ if env is None else env, root=root) if module else None
    except Exception:
        return None


def switched_off(selection):
    """`{kind: units set off}` for the rules, skills and roles a worker run is built from.

    A worker is a projection like any other, so a unit the launching session switches off is
    absent from it; a selection nobody could resolve switches nothing off.
    """
    selection = selection if isinstance(selection, dict) else {}
    return {kind: {unit for unit, value in (selection.get(kind) or {}).items() if value == "off"}
            for kind in ("rules", "skills", "roles")}


POLICY_DOC = re.compile(r"docs/[a-z0-9][a-z0-9.-]*\.md")


def policy_reads(root, instructions, declared):
    """The skills and documents the resolved policy tells this worker to open, as `(skills, docs)`.

    Every rule and stance in that text points somewhere — "the `licensing-review` skill", "Lists:
    `docs/preferences.md`" — and a worker that cannot follow the pointer is being told to obey a
    policy it cannot read. So the set is derived from the text itself rather than maintained by
    hand: a stance that stops citing a skill stops paying for it on the next run. `declared` is
    the role's own `skills:` line, which adds what its body assumes but the shared text never
    names.
    """
    available = sorted(p for p in (root / "primitives" / "skills").iterdir() if (p / "SKILL.md").is_file())
    cited = [p for p in available if re.search(r"\b" + re.escape(p.name) + r"\b", instructions)]
    skills = sorted(set(cited) | set(declared))
    docs = sorted({root / name for name in POLICY_DOC.findall(instructions) if (root / name).is_file()})
    return skills, docs


def est_tokens(chars):
    """Characters to tokens, the tokenizer-free approximation. See CHARS_PER_TOKEN."""
    return int(round(chars / CHARS_PER_TOKEN))


def text_size(paths):
    """Characters of every readable text file at or under these paths."""
    total = 0
    for path in paths:
        for item in [path] + (sorted(path.rglob("*")) if path.is_dir() else []):
            if item.is_file():
                try:
                    total += len(item.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError):
                    continue
    return total


def context_estimate(instructions, skills, docs):
    """What this worker is shown, in estimated tokens: the policy plus every byte mounted with it.

    This counts what is mounted, not what a run reads: a worker opens what its brief needs. Not
    the workspace and not the brief either, since those are the caller's and vary per run. It is
    the standing cost the role's own contract fixes, which is what a review round pays four times.
    """
    reference = text_size(skills) + text_size(docs)
    return {"policy_tokens": est_tokens(len(instructions)), "reference_tokens": est_tokens(reference),
            "total_tokens": est_tokens(len(instructions) + reference),
            "budget_tokens": CONTEXT_BUDGET_TOKENS}


def posture_figures(root, row):
    """The budget figures the sentence states, as the shared resolver counts them."""
    module = catalog.posture_module(root)
    return module.budget_figures(row) if hasattr(module, "budget_figures") else {}


def budget(root, row, prompt):
    """The soft-budget sentence this brief is missing, or None; the same one a native brief gets.

    The wording and the "already priced" test are `policy/hooks/posture.py`'s, loaded by file the
    way `lifecycle.py` loads a policy, so a role worker's brief and a native spawn's cannot state
    a spend two different ways. A resolver this checkout does not carry appends nothing.
    """
    module = catalog.posture_module(root)
    if prompt is None or not hasattr(module, "budget_sentence"):
        return None
    return None if module.budget_stated(prompt) else module.budget_sentence(row)


def resolve(root, config, runtime, name, model=None):
    """The contract, native binding and shared instructions of one role worker."""
    ready = resolution(root, config, runtime, name, model)
    return ready["fields"], ready["bindings"], ready["instructions"]


def passthrough(original):
    """The caller's variables a worker keeps: authentication, locale, proxies and certificates.

    Customization and loader overrides are dropped. `environment` builds on this, and an adapter's
    `refusal` checks the same set, so the preflight sees exactly the credentials the worker gets.
    """
    exact = {"PATH", "LANG", "LC_ALL", "TERM", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR",
             "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY"}
    auth_prefixes = ("OPENAI_", "ANTHROPIC_", "AWS_", "GOOGLE_", "AZURE_")
    env = {k: v for k, v in original.items() if k in exact or k.startswith(auth_prefixes)}
    for name in ("CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY"):
        if name in original:
            env[name] = original[name]
    return env


def environment(original, work):
    env = passthrough(original)
    env.update(HOME=str(work / "home"), XDG_CONFIG_HOME=str(work / "home/.config"),
               XDG_STATE_HOME=str(work / "home/.local/state"), XDG_CACHE_HOME=str(work / "home/.cache"))
    Path(env["HOME"]).mkdir(mode=0o700)
    try:
        keychain.provision(env["HOME"])
    except OSError as exc:
        raise ValueError(str(exc))
    return env


@contextlib.contextmanager
def artifact_slot(workspace, filename):
    """Anchor the approved output directory with no-follow descriptors; never replace an artifact."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.md", filename or ""):
        raise ValueError("--artifact must be a Markdown filename, not a path")
    fds = []
    try:
        fd = os.open(str(workspace), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        fds.append(fd)
        for component in (".agent-harness", "plans"):
            try:
                os.mkdir(component, mode=0o700, dir_fd=fd)
            except FileExistsError:
                pass
            fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            fds.append(fd)
        try:
            os.stat(filename, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ValueError("artifact already exists; choose a new filename")
        yield fd, filename
    finally:
        for fd in reversed(fds):
            os.close(fd)


def publish(slot, content):
    fd, filename = slot
    temporary = ".worker-" + uuid.uuid4().hex
    try:
        handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        # link() is atomic and refuses a destination created while the worker was running.
        os.link(temporary, filename, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
        os.fsync(fd)
    finally:
        try:
            os.unlink(temporary, dir_fd=fd)
        except FileNotFoundError:
            pass


def validate_artifact(root, content, work):
    if not content.strip() or len(content.encode()) > LIMIT or content.lstrip().startswith("```"):
        raise ValueError("worker returned empty, oversized or fenced plan content")
    path = work / ".agent-harness/plans/check.md"
    path.parent.mkdir(parents=True)
    path.write_text(content)
    from . import lifecycle
    result = lifecycle.invoke("validate-plan-card", {"tool_input": {"file_path": str(path)}})
    if result:
        raise ValueError("worker plan failed the Review Card validator")


def execute(command, prompt, env, cwd, run_dir, timeout):
    with (run_dir / "stdout.log").open("w") as out, (run_dir / "stderr.log").open("w") as err:
        proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=out, stderr=err,
                                text=True, env=env, cwd=str(cwd), start_new_session=True)
        try:
            proc.communicate(prompt, timeout=timeout)
        except BaseException:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.communicate()
            raise
        return proc.returncode


def policy_reference(work, docs):
    """Copy the documents the policy cites into the worker's own directory, and return it.

    A read root is a directory, and the directory these live in is the 237,000-token `docs/`
    tree; copying the cited files is how a worker follows `docs/preferences.md` without being
    handed everything beside it.
    """
    reference = Path(work) / "policy-reference"
    reference.mkdir(exist_ok=True)
    for path in docs:
        shutil.copyfile(str(path), str(reference / path.name))
    return reference


def temporary_roots():
    """The shared temporary directories of this machine, resolved: every run's scratch lands here."""
    candidates = ["/tmp", "/var/tmp", tempfile.gettempdir(), os.environ.get("TMPDIR") or "/tmp"]
    return {Path(path).resolve() for path in candidates}


def broad_read_root(path):
    """Why `path` is too broad to grant a worker, or None when it is narrow enough.

    A read root is everything a worker may open, so a shared root hands it every other run's
    files: a blind review given `/tmp` could read the earlier report naming its planted defects
    (issue #772). Refused are `/`, the home directory, each system temporary root, and any
    directory above one of them; a dedicated subdirectory of any of these is accepted.
    """
    path = Path(path)
    home = Path.home().resolve()
    roots = [(Path("/"), "the filesystem root")]
    roots += [(temp, "a system temporary root") for temp in sorted(temporary_roots())]
    roots.append((home, "the home directory"))
    for root, what in roots:
        if path == root:
            return what
        if path in root.parents:
            return "above " + what + " " + str(root)
    return None


def granted_roots(read_dirs):
    """The caller's `--read-dir` values resolved, refusing any that is not a narrow directory."""
    roots = [Path(path).resolve(strict=True) for path in read_dirs]
    for path in roots:
        if not path.is_dir():
            raise ValueError("--read-dir must name an existing directory")
        reason = broad_read_root(path)
        if reason:
            raise ValueError("--read-dir " + str(path) + " is " + reason + ", which would let the worker "
                             "read every other run's files; put the inputs in a dedicated directory, such "
                             "as one made by `mktemp -d /tmp/harness-inputs.XXXXXX`, and grant that instead")
    return roots


def run(root, config, runtime, name, workspace, prompt, state_root, model=None, artifact=None, timeout=300, read_dirs=()):
    if os.name != "posix" or not 1 <= timeout <= 3600:
        raise ValueError("workers require POSIX and a timeout between 1 and 3600 seconds")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode()) > LIMIT:
        raise ValueError("worker prompt must be nonempty and at most 1 MiB")
    workspace = Path(workspace).resolve(strict=True)
    if not workspace.is_dir():
        raise ValueError("worker workspace must be a directory")
    read_roots = granted_roots(read_dirs)
    ready = resolution(root, config, runtime, name, model, prompt)
    fields, bindings, instructions = ready["fields"], ready["bindings"], ready["instructions"]
    skills, docs = ready["skills"], ready["docs"]
    if ready["budget_sentence"]:
        prompt = prompt.rstrip() + ready["budget_sentence"]
    if bool(artifact) != (fields["authority"] == "artifact-write"):
        raise ValueError("only artifact-write roles require --artifact")
    native = adapter(root, runtime)
    executable = shutil.which(RUNTIMES[runtime])
    if not executable:
        raise ValueError("native CLI is not installed: " + RUNTIMES[runtime])
    version = subprocess.check_output([executable, "--version"], text=True, timeout=15).strip()
    # A client that cannot authenticate is refused here, before any record or directory exists,
    # rather than launched to fail with the runtime's own login prompt (issue #759).
    refusal = getattr(native, "refusal", None)
    reason = None
    if refusal:
        with tempfile.TemporaryDirectory(prefix="harness-worker-auth-", dir="/tmp") as empty:
            reason = refusal(executable, dict(os.environ), passthrough(os.environ), empty)
    if reason:
        raise ValueError(reason)
    state_root = Path(state_root)
    if state_root.is_symlink():
        raise ValueError("worker state directory cannot be a symlink")
    state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    run_dir = state_root / uuid.uuid4().hex
    run_dir.mkdir(mode=0o700)
    record = {"schema_version": 1, "id": run_dir.name, "role": name, "runtime": runtime,
              "runtime_version": version,
              # The harness that launched this run, stamped now: the usage sweep that turns the
              # status file into a ledger row may run long after this version was replaced.
              "harness_version": harness_version(root),
              "profile_fingerprint": session_fingerprint(root),
              "model": bindings["model"], "workspace": str(workspace),
              "effort": bindings.get("model_reasoning_effort", bindings.get("effort")),
              # The documents the policy cites are mounted as copies, so the roots recorded here
              # are the ones that outlive the run: the workspace, the skills and the caller's.
              "read_roots": [str(workspace)] + [str(p) for p in skills] + list(map(str, read_roots)),
              "context": ready["context"],
              "mode": "isolated-cli", "status": "starting", "started_at": time.time(),
              # The runner supervising this worker, so a reader can tell a live run from one whose
              # process died mid-flight; `orphaned()` decides, and never without the start token.
              "pid": os.getpid(), "pid_start": process_start(os.getpid()),
              "stances": config["stances"], "selection": ready["selection"], "posture": ready["posture"],
              "policy_sha256": hashlib.sha256(instructions.encode()).hexdigest(),
              "qualification": "unqualified", "authority": "result data only; no transferred approvals"}
    status_path = run_dir / "status.json"
    reconcile.atomic_text(status_path, json.dumps(record, indent=2) + "\n")
    try:
        slot_context = artifact_slot(workspace, artifact) if artifact else contextlib.nullcontext(None)
        with slot_context as slot, tempfile.TemporaryDirectory(prefix="harness-worker-", dir="/tmp") as temporary:
            work = Path(temporary)
            original = dict(os.environ)
            env = environment(original, work)
            cwd = work / "cwd"
            cwd.mkdir()
            reference = policy_reference(work, docs)
            instructions += "\n\nProject to inspect (read-only): " + str(workspace)
            # The skills and documents the policy above cites, and no other path into the
            # checkout: a worker must be able to follow its own policy's pointers, and must not
            # be able to spend a review layer on the corpus nobody asked about (issue #335).
            if skills:
                instructions += ("\nSkill authority (read-only), the skills this role may read: "
                                 + ", ".join(str(p) for p in skills))
            else:
                instructions += "\nThis role reads no skill authority: the policy above is complete."
            if docs:
                instructions += ("\nDocuments the policy above cites, as copies under "
                                 + str(reference) + ": " + ", ".join(p.name for p in docs))
            instructions += "\nAdditional read-only inputs: " + ", ".join(map(str, read_roots))
            command = native.prepare(executable, work, root, workspace,
                                     list(skills) + ([reference] if docs else []) + read_roots,
                                     instructions, bindings, original, env)
            record["status"] = "running"
            reconcile.atomic_text(status_path, json.dumps(record, indent=2) + "\n")
            code = execute(command, prompt, env, cwd, run_dir, timeout)
            if code:
                raise ValueError("native worker exited with status " + str(code) + "; inspect its private logs")
            content = native.result(work, run_dir)
            if not isinstance(content, str) or not content.strip() or len(content.encode()) > LIMIT:
                raise ValueError("native worker returned an empty or oversized result")
            if slot:
                validate_artifact(root, content, work)
                publish(slot, content)
                record["artifact"] = str(workspace / ".agent-harness/plans" / artifact)
            reconcile.atomic_text(run_dir / "result.md", content)
            record["result_path"] = str(run_dir / "result.md")
            # What the run cost, as its own runtime reported it, so `usage --by role` counts a
            # worker beside a subagent. An adapter that reports nothing leaves the key absent
            # rather than a zero, which a report would read as a measured run that spent none.
            try:
                counts = getattr(native, "usage", lambda *_: {})(work, run_dir)
            except Exception:
                counts = {}
            if isinstance(counts, dict) and counts:
                record["usage"] = counts
        record["status"] = "completed"
    except subprocess.TimeoutExpired:
        record.update(status="timed-out", error="native worker exceeded its timeout")
    except KeyboardInterrupt:
        record.update(status="cancelled", error="worker interrupted")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        record.update(status="failed", error=str(exc))
    finally:
        record["finished_at"] = time.time()
        reconcile.atomic_text(status_path, json.dumps(record, indent=2) + "\n")
    return record


LIVE = ("starting", "running")
ORPHANED = "the worker process ended without reporting a result"


def process_start(pid):
    """A token naming this pid's incarnation, or None when the platform will not say.

    Recorded beside the pid so a recycled pid cannot be mistaken for the original process: a
    reused number carries a different start time. Linux reads field 22 of `/proc/<pid>/stat`,
    counted after the comm field's closing parenthesis, which may itself contain spaces; every
    other POSIX platform asks `ps`, whose second-resolution timestamp is enough to separate two
    processes that happened to receive the same number.
    """
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    try:
        stat = Path("/proc/" + str(pid) + "/stat")
        if stat.exists():
            return stat.read_text().rsplit(")", 1)[1].split()[19]
        # A fixed locale, so a reader under different LC_TIME settings prints the same token.
        out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, text=True, timeout=10,
                             env=dict(os.environ, LC_ALL="C"))
    except (OSError, IndexError, ValueError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


def running(pid, token):
    """True if the recorded process still runs, False if it is gone, None if it cannot be told.

    None is every case the harness cannot decide — a record from a release that stored no pid, a
    platform that reports no start time, a stat call refused — and the caller must read it as the
    status already on file rather than as a terminal state.
    """
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    except OSError:
        return None
    current = process_start(pid)
    if token is None or current is None:
        return True
    return current == token


def orphaned(record, run_dir):
    """Report a run whose supervising process died without a result as the terminal `orphaned`.

    Only the live statuses are reclassified, and only when nothing was reported: a result on disk
    means the run spoke for itself even if the process died before its final write. The new state
    is persisted into `status.json` alone, every other key and every other file left as they are,
    and a state directory that cannot be written still reports honestly to this caller.
    """
    if record.get("status") not in LIVE:
        return record
    if record.get("result_path") or (run_dir / "result.md").exists():
        return record
    if running(record.get("pid"), record.get("pid_start")) is not False:
        return record
    updated = dict(record, status="orphaned", error=ORPHANED)
    with contextlib.suppress(OSError):
        reconcile.atomic_text(run_dir / "status.json", json.dumps(updated, indent=2) + "\n")
    return updated


def status(state_root, worker_id=None):
    if worker_id and not re.fullmatch(r"[a-f0-9]{32}", worker_id):
        raise ValueError("invalid worker id")
    paths = [Path(state_root) / worker_id / "status.json"] if worker_id else sorted(Path(state_root).glob("*/status.json"))
    return [orphaned(json.loads(path.read_text()), path.parent) for path in paths]
