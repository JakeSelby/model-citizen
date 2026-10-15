"""Claude worker tools are read-only; safe mode preserves native authentication."""
import json
import subprocess
from pathlib import Path

# Cloud providers authenticate through their own credential chains, which `claude auth status`
# does not report as a login; a worker under one of these is launched unchecked, as before.
CLOUD_PROVIDERS = ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")
TOKEN_ONLY = ("refused before launch: the Claude worker would not authenticate. This command runs "
              "inside a Claude Code session, which strips CLAUDE_CODE_OAUTH_TOKEN from its tool "
              "subprocesses, and no other credential or stored login reaches the worker. Run "
              "`harness role run` from a shell that exports CLAUDE_CODE_OAUTH_TOKEN, or log the "
              "client in with `claude auth login` so its stored login is found.")


def identity(original, env):
    """Keep the operator's home and user, so the client finds its stored login and keychain."""
    env["HOME"] = original.get("HOME", str(Path.home()))
    # Native macOS keychain lookup includes the user identity, independently of HOME.
    if "USER" in original:
        env["USER"] = original["USER"]
    if "CLAUDE_CONFIG_DIR" in original:
        env["CLAUDE_CONFIG_DIR"] = original["CLAUDE_CONFIG_DIR"]


def refusal(executable, original, env, cwd):
    """Why a worker launched from this process would fail to authenticate, or None to launch.

    Only a process inside a Claude Code session is checked (`CLAUDECODE` is set there): the client
    strips `CLAUDE_CODE_OAUTH_TOKEN` from its tool subprocesses, so a token-only session hands a
    worker nothing. The client's own `auth status` answers under the worker's environment; its
    output names the account, so it is parsed for `loggedIn` alone and never printed or kept. A
    check that cannot run refuses too: the token is never written anywhere to work around it. It
    runs from `cwd`, an empty directory like the worker's, so no project's settings answer for it.
    """
    if not original.get("CLAUDECODE") or any(env.get(name) for name in CLOUD_PROVIDERS):
        return None
    env = dict(env)
    identity(original, env)
    try:
        done = subprocess.run([executable, "auth", "status", "--json"], env=env, cwd=cwd, text=True,
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, timeout=30)
        logged_in = done.returncode == 0 and json.loads(done.stdout).get("loggedIn") is True
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
        return TOKEN_ONLY + " (`claude auth status` could not confirm a login.)"
    return None if logged_in else TOKEN_ONLY


def prepare(executable, work, root, workspace, read_roots, instructions, bindings, original, env):
    # Preserve login/keychain discovery, while safe/restricted modes suppress user customization.
    identity(original, env)
    (work / "instructions.md").write_text(instructions)
    command = [executable, "-p", "--safe-mode", "--restricted", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
               "--setting-sources", "", "--tools", "Read,Grep,Glob", "--permission-mode", "dontAsk",
               "--permission-prompts", "none", "--disable-slash-commands", "--no-chrome", "--no-session-persistence",
               # The harness checkout is deliberately not a read root: the shared policy reaches
               # the worker as the system prompt, and its skill authority arrives in read_roots.
               "--add-dir", str(workspace), "--output-format", "json",
               "--append-system-prompt-file", str(work / "instructions.md"), "--model", bindings["model"]]
    if bindings.get("effort"):
        command += ["--effort", bindings["effort"]]
    for path in read_roots:
        command += ["--add-dir", str(path)]
    return command


def usage(work, run_dir):
    """The run's token totals, from the result envelope `--output-format json` already writes.

    `--no-session-persistence` leaves no transcript to read, so the envelope is the only source;
    it reports no tool-call count, which the record therefore leaves unknown.

    `by_model` is the envelope's `modelUsage`, keyed by the full id each model ran as. The worker
    is launched by a model alias, which resolves to whatever the client's current model is, so
    only this map can price the run; the usage sweep reads it, see `worker_parts` there.
    """
    try:
        envelope = json.loads((run_dir / "stdout.log").read_text())
        counts = envelope["usage"]
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    if not isinstance(counts, dict):
        return {}
    fields = {"input": "input_tokens", "output": "output_tokens",
              "cache_read": "cache_read_input_tokens", "cache_write": "cache_creation_input_tokens"}
    out = {name: int(counts[key]) for name, key in fields.items()
           if isinstance(counts.get(key), int)}
    # The TTL split of the cache writes, which `modelUsage` does not carry. A `-p` run writes at
    # the 1-hour rate, so pricing every write at the 5-minute one would understate the run.
    split = counts.get("cache_creation")
    if isinstance(split, dict):
        for name, key in (("cache_write_5m", "ephemeral_5m_input_tokens"),
                          ("cache_write_1h", "ephemeral_1h_input_tokens")):
            if isinstance(split.get(key), int):
                out[name] = split[key]
    models = envelope.get("modelUsage")
    if isinstance(models, dict) and models:
        out["by_model"] = models
    return out


def result(work, run_dir):
    path = run_dir / "stdout.log"
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("native result exceeds 1 MiB")
    result = json.loads(path.read_text())
    if not isinstance(result, dict) or result.get("type") != "result" or result.get("subtype") != "success" or result.get("is_error") is not False:
        raise ValueError("Claude worker did not return a successful result envelope")
    return result.get("result")
