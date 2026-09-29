---
description: Write the repository handoff file for the next session, and record any durable learning.
argument-hint: [note to carry into the next session]
---

# Handoff

Note to carry over: $ARGUMENTS

1. **Overwrite `.agent-harness/progress.md`** at the repository root, or in the current directory when
   there is no repository. It is a snapshot, never append; session start reads its first eighty lines. Use exactly these headings:

   ```markdown
   # Handoff <ISO date>

   ## Done
   ## Open
   ## Next command
   ## Decisions needed
   ## Learnings
   ```

   Fill them from this session, not from the file you are replacing. **Done** and **Open** are
   short bullets naming files and commands. **Next command** is one runnable line and nothing
   else. **Decisions needed** is what only the user can settle. **Learnings** is what you would
   have wanted to know when this session started.

2. **Save the shared task contract.** Read `citizen task show` first. Write a JSON input with
   `objective`, `next_steps`, `decisions`, and `artifacts`; add the framework checkout and
   baseline when a planning framework owns the artifacts. Save with `citizen task save --input <file> --runtime <runtime>
   --revision <current-revision>`. The next runtime reads the same data, rechecks the tree,
   and establishes its own permissions. A handoff never transfers an approval.

3. **Promote anything durable.** If a learning would help a future session in this repository —
   a fix that generalizes, a trap worth avoiding, a command that actually works — append it as
   a dated bullet to `docs/solutions/<yyyy-mm-dd>-<slug>.md` under the repository root, or under
   `.agent-harness/` when there is no repository, creating the folder when it is missing. One or two sentences, carrying the command or the path, so a correction becomes an
   artifact instead of a prompt the user has to repeat. Nothing durable, no file.

End the turn with the two paths: the progress file, and the solutions file if you wrote one.
