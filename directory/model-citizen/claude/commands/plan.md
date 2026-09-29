---
description: Turn a topic or issue into a plan the reviewer approves in the native plan pane.
argument-hint: <topic or issue number>
---

# Plan

What to plan: $ARGUMENTS

1. **Invoke the `plan-authoring` skill and read it in full.** Never write the card from memory
   of its contract; a hook validates the file you write.
2. **Delegate the wide reading before plan mode.** `citizen role run planner` writes an artifact
   and plan mode permits no write but its own plan file, so run it here when delegation pays —
   active runtime, explicit session model, brief file, `--artifact <new-plan.md>`.
3. **Ask before entering plan mode**, in one line naming the topic — entering it is the user's
   call. No plan mode, or the user declines it, and step 8 is the whole command.
4. **Gather inside plan mode.** Read the issue if you were given a number and read the code the
   plan will touch; long-form research goes to the scratchpad, never beside the plan file. Ask
   the user only for what you cannot find yourself, and batch every question into one message.
5. **Write the Review Card into the plan file plan mode designated.** The runtime names that
   file and you cannot rename it while planning. The card is the file's first screen and
   everything else lives below it, under `# Addendum`.
6. **Post the review message in the skill's shape, then call `ExitPlanMode`.** Leave off its
   closing build line: the native approval is the gate, so never ask for a typed reply as well.
7. **Once approved, name the plan and hand it over.** Rename the file to a topic slug in the
   same directory, never over an existing name — take `-2` and say so — and end by invoking
   `/build <absolute path>`. The builder commits it, so it reaches the pull request.
8. **No plan mode:** write the plan under `.agent-harness/plans/` — repository root, or the
   current directory when there is no repository — named for the topic, open it for the reviewer
   with an absolute path, and end at the skill's build line.

This command needs no repository and no code. Implement nothing; create no branch and no
worktree, which happens at build. If the user comes back with changes, revise the file, refresh
its **Changed this round** line, say in chat only what changed, and call `ExitPlanMode` again.
