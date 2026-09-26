# Eval UIs with version -> run -> compare (checked 2026-09-26)

Sources: GitHub API licence fields and LICENSE files, READMEs, official docs pages (WebFetch). Web searches used: 1.
"CI/stats" = does run comparison report statistical uncertainty.

| Tool | Versioning | Run evals from UI | Compare / stats | Local setup | Licence | Coding-agent config |
|---|---|---|---|---|---|---|
| promptfoo (github.com/promptfoo/promptfoo) | No UI versioning; config is YAML in git | Yes: web UI "Edit and re-run - Open in eval creator" | Yes: "Compare - Diff against another eval"; point scores only, no CIs in docs | Single command: `npx promptfoo@latest eval` then `promptfoo view` | MIT | No (generic; providers incl. agents via custom provider) |
| Inspect AI (github.com/UKGovernmentBEIS/inspect_ai) | No | inspect view: no ("Evaluations are launched via inspect eval"); VS Code extension can run tasks (unconfirmed from docs read) | View does not compare runs; docs point to EvalLog API. Metrics carry stderr, clustered SEs | Single command: `pip install inspect-ai`, `inspect view` | MIT | Not specifically; agents supported via sandboxes |
| Arize Phoenix (github.com/Arize-ai/phoenix) | Yes: prompt management "version control, tagging" | Yes: playground runs prompt over dataset, "recorded as traces and experiments" | Experiments compared; CIs not documented (unconfirmed) | Single command (pip / Docker image) | Elastic License 2.0 | No |
| Langfuse (github.com/langfuse/langfuse) | Yes: prompt management versions/labels | Yes: "Prompt Experiments" run prompt versions on datasets from UI | Side-by-side, aggregated scores; no CIs in docs | Docker Compose ("5 minutes") | MIT outside ee/ dirs; ee/ separate licence | No |
| Opik (github.com/comet-ml/opik) | Yes: Prompt Library "version control ... UI and SDK" | Yes: Prompt Playground validates against test suites; Optimization Studio runs optimizer from UI | Experiments compared; CIs unconfirmed | `./opik.sh` from clone (Docker Compose wrapper) | Apache-2.0 | No; ships MCP server for coding agents to read traces |
| Agenta (github.com/agenta-ai/agenta) | Yes: "version history of each agent configuration"; prompts/skills/tools versioned "like code" | Evaluation from UI exists in docs (test sets, revisions); whether it applies to the new agent product is unconfirmed | "compare changes"; no CIs found | Docker Compose from sparse clone | MIT outside ee/ | YES: product repositioned around agents with Claude Code, Codex, Pi harnesses, AGENTS.md, skills, MCP |
| Braintrust (braintrust.dev) | Yes (prompts in platform; not re-verified) | Yes (playground/experiments; not re-verified) | Baseline alignment, per-row score deltas, green/red regressions; no CIs/significance in docs | Hosted (self-host not in pages read; unconfirmed) | Proprietary | No |
| LangSmith + Studio (docs.langchain.com) | Prompt hub versions (not re-verified); Studio manages assistants | Studio: "iterating on prompts and running experiments over datasets" | Compare view with source experiment, red/green regressions; no CIs | LangSmith hosted (self-host enterprise, unconfirmed); Studio via `langgraph dev` local server + web UI | Proprietary (langgraph-studio desktop repo gone) | No (LangGraph agents) |
| AutoGen Studio (microsoft/autogen) | No | No evals | No | `pip install autogenstudio` | Code MIT (repo licence field CC-BY-4.0 = docs); AutoGen in maintenance mode | No |
| CrewAI AMP | Unconfirmed | Unconfirmed | Unconfirmed | Hosted platform; self-host unconfirmed | Framework MIT; AMP proprietary | No |
| Letta ADE | Agent file (.af) checkpoints only | No | No | ADE deprecated; replaced by chat.letta.com and desktop app | Letta server Apache-2.0 | No |
| OpenAI Evals dashboard | Unconfirmed | Yes, configure evals in dashboard | Not documented | Hosted only | openai/evals repo MIT per README; dashboard proprietary | No. Docs: "read-only ... October 31, 2026 ... shut down on November 30, 2026" |
| Harbor (github.com/harbor-framework/harbor) | No UI versioning (job configs) | No launch from viewer README ("browsing and inspecting jobs, trials, trajectories") | Yes: `apps/viewer/app/routes/compare.tsx`, row/column grouping; no stderr/CI terms found in compare code | Single command: `harbor view ./jobs` | Apache-2.0 | Yes: installed agents include claude_code, aider, cline, antigravity |
| Terminal-Bench (harbor-framework/terminal-bench-1) | No | No | Hosted dashboard at tbench.ai/docs/dashboard; leaderboard | Docker + uv harness; now superseded by Harbor | Apache-2.0 | Yes (benchmark for terminal agents) |

## Notes
- No tool found reports confidence intervals or significance in its comparison UI; Inspect is the only one with stderr in metrics, and its viewer does not compare.
- Harbor is the only local viewer targeting coding agents (Claude Code etc.) with a compare route, but it has no config editor/versioning.
- Agenta now versions Claude Code/Codex agent configs (AGENTS.md, skills, MCP) but its eval linkage to that is unconfirmed.
