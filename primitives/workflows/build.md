---
description: Implement an approved plan or issue in its own worktree, run the gate, open the pull request.
argument-hint: <plan path or issue number>
---

# Build

What to build: {{arguments}}

**Check first, before spawning anything.** Choose the applicable path for repository, push remote and logged-in `gh` availability:

- **No repository:** the caller resolves step 1's scope, implements/tests in place and runs step 3. Skip the builder/spawn and steps 4–6; report files and verification, without a commit or PR.
- **No usable remote:** the caller resolves scope on a local task branch, preserves unrelated work, implements/tests and runs step 3. Skip the builder; make/check step 4's commit, then stop before 5–6.
- **Only `gh` is unavailable:** use the normal builder through step 4, report its verified local branch, then stop before step 5.
- **All prerequisites available:** follow every numbered step below.

1. **Work from the plan path or the issue number you were given**, and say which in your first
   message. Never search for a plan: `/plan` hands over the approved path. With neither path nor
   issue, ask for one. Give the builder the absolute plan path and tell it to copy the file into its
   worktree and commit it — a worktree carries no untracked file, and the plan belongs in the PR.
2. **Spawn the `builder` agent** with the plan or issue text, the repository path, the base
   branch, and the attribution trailer your tool supplies. Its definition already carries the
   standing brief — a worktree off the base branch per `worktree-per-agent`, the repository's own
   instructions read first, tests with every change, the repository's gate, one local Conventional
   Commit — so retype none of it. Name the files it may touch and those it must leave alone.
3. **Run the gate yourself** in the worktree it names, with the repository's own commands. Never
   take an agent's word for a verifier; you read the exit status, not its account of the run. Red
   means you fix it or hand the finding back, never that you push anyway.
4. **Check the commit** before it leaves the machine: a Conventional Commit title, a body ending
   in the attribution trailer, and nothing in the diff outside the declared scope. Include
   `Closes #N` only when a real delivery issue owns the work; a topic-only plan carries its
   approved plan reference instead. Honor repositories that require an issue before implementation.
5. **Push the branch and open the pull request** with `gh pr create`, based on the default
   branch, never pushing to that branch directly. Body: a few bullets on what and why, the
   delivery issue trailer when applicable, and the generated-with line your tool supplies.
6. **Answer the review bot**, where one runs: `.coderabbit.yaml`, `greptile.json`, or past bot
   reviews. A pass is done only when its status on the head commit completes (CodeRabbit:
   `success: Review completed`), not on a thread reply's empty review. If it reads skipped or no
   review starts, request one with the bot's command, such as `@coderabbitai review`. Wait up to
   fifteen minutes; say so if none arrives. Each unresolved bot thread in GraphQL `reviewThreads` is
   a finding: fix, gate and push, or reply why not, then `resolveReviewThread`. After a fix, ask for
   one more review and wait again, two rounds at most. A human's thread is never yours to resolve.

Report the outcome and PR URL, then at most five bullets for reviews, decisions or unfinished work.
Give the gate result in one line, and any failing output in full.
