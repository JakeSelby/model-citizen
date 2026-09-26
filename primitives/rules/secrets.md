# Secret hygiene, wherever files are tracked

- **Never put tokens, passwords, API keys, or secrets in any file that is committed to source
  control** — comments, docstrings, commit messages, story files and planning artifacts alike.
- **Read credentials from the environment, a cloud CLI profile (`--profile`) or a secret store; never inline, never a pasted key.**
- **On finding a secret in source control, stop.** Commit nothing on top; tell the user immediately
  so the credential is rotated first; then remove it, then rewrite history if warranted.
- **Add a secret-bearing file to `.gitignore` before creating it.** An already-tracked file stays
  tracked when its directory is later ignored — check `git ls-files`. Never `git add` one.
- Why, and the shell examples: `docs/how-it-works.md`.
