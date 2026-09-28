---
description: Close a finished session: sweep for unfinished work, ask once, land, file the follow-ups, hand off, tell the sessions that depend on this one, then archive.
argument-hint: [what is finishing, and "archive" if it should archive without asking again]
---

# Close out

What is finishing: $ARGUMENTS

**Sweep before you change anything.** A close-out that opens with a merge has already skipped the
question it exists to ask. Outside a repository steps 3 and 5 do not apply; say so.

1. **Name what is still open.** Uncommitted and untracked files in every checkout this session
   touched, `citizen worktree audit`, the pull requests this session opened and the state of their
   checks, background work still running, and the decisions you parked for the user. Report that
   list first; an empty sweep is a result, so say it and move on.
2. **Batch the follow-ups, then ask once.** One line each, title and why, for what this session
   found and did not do; never quietly fix one here instead. Ask for one explicit go-ahead naming
   them, the pull requests step 3 would merge, and any step 4 would open and then merge. Act
   only on what it approves.
3. **Land what is ready** with the `land` workflow, never by inlining its steps: the merge proof
   and its refusals are the point of it. Step 2's go-ahead is the one it asks for. Anything not
   green, not approved, or not yours to merge stays open and goes in the report.
4. **File the approved follow-ups**; step 2's go-ahead covers filing them and landing what they
   write. When filing writes tracked files, such as an issue map or a story file, run it in a new
   worktree off the updated default branch, put what it wrote in its own pull request under the
   repository's pull request rules, and land that with the `land` workflow once checks pass.
5. **Hand off** with the `handoff` workflow, and only when work in this repository continues past
   this session. A finished piece of work needs no progress file.
6. **Tell the sessions that depend on this one**, where the client can list and message them: a
   branch one was waiting on, a file one holds open, a conclusion that reverses its premise.
   Sharing a group or a repository is not a dependency; where the client cannot, use the handoff.
7. **Archive, or stop.** Archive when the invocation already asked for it, provided
   every pull request step 4 opened has merged; otherwise end on the checklist and wait.
   Never clear or compact first: archiving ends the session, so both only burn the context you
   still need. Clearing belongs to carrying on in the same session, the opposite of this workflow.

Log the close-out before the archive call, which ends the turn, and report what landed, what you
filed, whom you told, and what you are leaving open.
