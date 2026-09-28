---
description: Merge an approved pull request, clean up its worktree and branch, then check whether a release is due.
argument-hint: <pull request number or URL> [worktree name]
---

# Land

What to land: $ARGUMENTS

**Check first, before changing anything.** This command needs a git repository, a remote, and `gh`
logged in. Resolve the pull request, the branch behind it, and the worktree that produced it; when
any of the three is missing, say which and stop. Merging is approval-gated — get an explicit
go-ahead before the merge — and this command never tags and never deploys.

1. **Verify the head that will merge.** Every required check green on the current head, not on an
   earlier push, and any issue-ownership or closing-link check the repository runs passed there too.
   A pending, failing or stale one stops the workflow, named, as does an unresolved review thread
   (GraphQL `reviewThreads`). Name each check or status that is not required, with its state, but
   never wait on it. A fix belongs to `/build`, before approval; a human resolves their own thread.
2. **Record the merge,** from the worktree after a fetch: `citizen intent merge --base origin/main`
   (the default branch) probes with `git merge-tree`, or pass `clean` or `conflicted` for a
   merge you ran. `citizen usage --conflicts` reports it weekly. A conflict stops the workflow.
3. **Merge it** with `gh pr merge --squash --delete-branch`, into the default branch.
4. **Fast-forward the shared checkout** on its default branch with `git pull --ff-only`. Anything
   other than a fast-forward means the branch diverged: stop and report it, never merge locally.
5. **Remove the worktree and its branch** with `citizen worktree remove <name> --merged`: it
   ignores the regenerable caches a gate run wrote, removes the checkout, then deletes the branch
   once it has read a merged pull request whose head commit is the branch tip, the proof that
   replaces the ancestry a squash merge destroys. Never force a removal. Modified, untracked or
   other ignored files stop the workflow with the reason and the path, so the work can be read
   first, and so does a tip that proof does not account for. Add `--also-clear <name>` for a
   regenerable top-level directory this repository writes that the built-in list misses.
6. **Audit** with `citizen worktree audit`, and report every stale or dirty checkout it names
   along with what each still holds. Leave them in place; removing them is the user's call.
7. **Check the release rule.** Read the repository's own agent instructions for when a release is
   due. Either state "no release due" with the reason those instructions give, or post a release
   card — the version, what landed since the last one, the surfaces the instructions require —
   and wait for approval. Approval belongs to the release, not to this command.

Step 5 is where a branch gets deleted, behind the merge proof. Never `git branch -D` in your own
shell, which has no such proof, and keep deletions out of compound commands: the shell-grading
hook denies a whole compound command when one segment is irreversible.

Report the merge commit, what was removed, what the audit still shows, and the release decision
with the line in the repository's instructions it came from.
