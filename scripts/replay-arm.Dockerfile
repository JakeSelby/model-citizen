# check=skip=InvalidDefaultArgInFrom
# The two replay arms, each a fresh image holding exactly its declared components. How they are
# built, checked and run is in docs/benchmarks.md; `scripts/replay_arms.py` drives this file.
#
# Both arms start from the Linux qualification image's inputs: `BASE_IMAGE` and
# `CLAUDE_CODE_VERSION` are read out of `scripts/linux-target.Dockerfile` by the runner and passed
# here, so the two files cannot drift apart. Only Claude Code is installed. The base template ships
# a Codex client of its own, which is removed: an arm holds its declared components and no other
# agent client, and the manifest's `cli_packages` shows what is left. Git trusts /work, where the
# runner mounts a snapshot made by whichever user runs it; the file this writes is in the manifest.
#
#   bare     the base image, Claude Code and the shared observation-only recorder
#   harness  the bare stage plus this repository at one commit, synced for the agent user
#
# The build context carries the declared observer in both arms and a `harness/` clone of the one
# commit only for the harness arm. Nothing comes from the host's home directory.
ARG BASE_IMAGE
FROM ${BASE_IMAGE} AS bare
ARG CLAUDE_CODE_VERSION
USER agent
COPY --chown=agent:agent observer /opt/model-citizen-observer
RUN test -n "${CLAUDE_CODE_VERSION}" \
    && npm uninstall -g @openai/codex \
    && npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
    && npm cache clean --force \
    && ! command -v codex \
    && git config --global --add safe.directory /work
CMD ["bash"]

FROM bare AS harness
ARG HARNESS_COMMIT
COPY --chown=agent:agent harness /opt/model-citizen
# The sync reads no configuration, since the image has none: the arm gets the commit's defaults.
# The run mounts its snapshot at /work, so that is the one root the stop-gate hook is trusted in.
# No bytecode is written at build time: a .pyc carries its build time, so two builds would differ.
RUN cd /opt/model-citizen \
    && test "$(git rev-parse HEAD)" = "${HARNESS_COMMIT}" \
    && PYTHONDONTWRITEBYTECODE=1 python3 bin/harness sync \
    && PYTHONDONTWRITEBYTECODE=1 python3 bin/harness trust /work
CMD ["bash"]

# A harness arm with a declared selection (an ablation arm): the same commit, with the runner's
# `selection.json` installed as the agent user's configuration before the sync, so the sync
# withholds what it switches off and the session resolves the same selection. The file's digest
# is the declaration's `selection` component, and admission accepts it only when they match.
FROM bare AS harness-selected
ARG HARNESS_COMMIT
COPY --chown=agent:agent harness /opt/model-citizen
COPY --chown=agent:agent selection.json /tmp/model-citizen-selection.json
RUN mkdir -p "$HOME/.config/agent-harness" \
    && mv /tmp/model-citizen-selection.json "$HOME/.config/agent-harness/config.json" \
    && cd /opt/model-citizen \
    && test "$(git rev-parse HEAD)" = "${HARNESS_COMMIT}" \
    && PYTHONDONTWRITEBYTECODE=1 python3 bin/harness sync \
    && PYTHONDONTWRITEBYTECODE=1 python3 bin/harness trust /work
# A selection holding `"instructions": {"CLAUDE.md": "off"}` withholds the user-level CLAUDE.md,
# which no selection switch removes and the sync always writes: this step deletes it, and fails
# when there is none to delete. `scripts/ablations.py` (`WITHHOLDABLE`) names the selection and
# the path, and its parity check admits the arm only when that file is its one difference.
RUN if python3 -c 'import json, os, sys; c = json.load(open(os.path.expanduser("~/.config/agent-harness/config.json"))); sys.exit(0 if (c.get("instructions") or {}).get("CLAUDE.md") == "off" else 1)'; \
    then { test -L "$HOME/.claude/CLAUDE.md" || test -e "$HOME/.claude/CLAUDE.md"; } && rm "$HOME/.claude/CLAUDE.md"; fi
CMD ["bash"]
