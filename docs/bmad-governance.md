# BMad repository governance

This file is the single policy source loaded by Model Citizen's BMad workflow customizations.
The repository instructions remain authoritative when they are stricter.

- **Authority is split between the issue and its story file.**
  - The GitHub issue is the delivery authority. It holds state, discussion, a summary-depth writeup and
    the acceptance evidence.
  - The issue's story file, `_bmad-output/implementation-artifacts/<BMad ID>.md`, is the design
    authority. It holds:
    - context and acceptance criteria;
    - the design, with decisions and alternatives;
    - tasks;
    - dev notes that cite their sources;
    - the dev agent record;
    - review findings.
  - Keep the issue at summary depth and put the design depth in the story.
  - Create the issue before changing tracked files. Use one delivery issue per PR, and keep one concern in
    each PR.
- Run implementation in a managed worktree. Run BMad's installed scripts from the shared checkout
  and pass the implementation worktree as an explicit input.
- Public BMad artifacts live under `_bmad-output`; never redirect this repository's artifacts to a
  private or central planning repository.
- Publish synthesized evidence and decisions, not raw conversations, tool logs, memory exports,
  secrets, private paths or irrelevant personal information.
- Label claims as implemented, validated, proposed, historical or unknown. Do not treat generated
  configuration, green unit tests or a prior release as proof of current native-client behavior.
- Every managed work item has one immutable typed BMad ID and a bidirectional GitHub mapping.
  Reparenting never changes the ID; reconstructed history must say that it is reconstructed.
- The exact `type::*` label is the repository's authoritative GitHub type projection. Native issue
  types are organization-managed and unavailable for issues in this personal-account repository;
  native sub-issue relationships remain the hierarchy projection.
- File maintainer work with `python3 scripts/bmad_issue_sync.py new --title T --kind KIND --body-file F
  [--parent N] [--milestone M]` from the implementation worktree: it labels the issue and reserves
  its ID in one step. Refuse to start or continue implementation for an issue that is absent from
  `_bmad-output/issue-map.json`; reserve it first. The required `issue-ownership` check fails a PR
  whose delivery issue is unmapped, so the reservation ships in that PR or before it.
- Community issues can enter without BMad metadata. During maintainer triage, reserve an ID with
  `python3 scripts/bmad_issue_sync.py reserve --issue N --kind KIND [--parent N]`; merge its artifact,
  then run `plan` and `apply` before implementation ownership begins.
- Preserve issue and repository history. Add amendments rather than rewriting dated evidence, and
  do not replace original issue prose when maintaining traceability metadata.
- Before review, run `python3 bin/harness lint`, `python3 -m unittest discover -s tests`, and
  `bin/harness generate --check`. Never bypass hooks.
- Ordinary issues and PRs use the repository's native voice without generated framework footers.
  README and planning documentation may credit BMad explicitly.

## Route every operation through BMad

Before an SDLC step, read and follow the BMad skill for it. Never improvise a process a skill already
defines.

| When you | Run |
| --- | --- |
| Research a question a decision depends on | `bmad-deep-recon` |
| Change who the product serves or what it is for | `bmad-product-brief`, update intent |
| Add, change or retire a requirement | `bmad-prd`, update intent |
| Change a user-facing flow, output or copy pattern | `bmad-ux`, update intent |
| Change an invariant, a boundary or a dependency direction | `bmad-architecture`, update intent |
| Break new scope into work | `bmad-create-epics-and-stories`, then `scripts/bmad_issue_sync.py new` |
| Implement a work item | `bmad-build`, with the delivery story as its spec |
| Review a change | `bmad-code-review` |
| Change direction mid-flight | `bmad-correct-course` |
| Close an epic | `bmad-retrospective` |
| Check readiness or status | `bmad-sprint-planning` |

The harness's `/plan` Review Card stays the approval surface for a build. At build time, distill its
addendum into the delivery story's Design and Dev notes. Whatever produced the plan, the story is the
durable record.

## Keep the corpus current in every change

Update the corpus in the same PR as the change that makes it stale:

- **New work.** File it with `scripts/bmad_issue_sync.py new`, which files the issue, reserves the ID and
  writes a typed story skeleton. Fill the story's required sections before the delivery PR merges. The
  `issue-ownership` check fails a typed delivery story that still holds placeholders.
- **Touching a legacy stub.** Run `scripts/bmad_issue_sync.py upgrade --id <BMad ID>` and fill the
  skeleton in the same PR.
- **Implementation.**
  - Keep the delivery story current: check off tasks, add the file list and references to the dev notes,
    and complete the dev agent record.
  - Record each review finding and how it was resolved.
  - Add a change-log line for every material change after the story was first written.
- **Changed requirements, invariants or flows.**
  - A changed requirement amends the PRD.
  - A changed invariant amends the architecture spine, either as an amended rule or a new AD.
  - A changed user-facing flow amends the UX specification.
  - Each amendment goes through the matching skill's update intent, with a memlog entry.
- **Sprint status is derived.** It is generated from `issue-map.json` by the sync tool; never edit it by
  hand.

## Parallel reservations merge through a driver

Two branches that each reserve a BMad ID both append to `items` in `_bmad-output/issue-map.json`
and bump `next_ids`, so without help the second to merge conflicts. `.gitattributes` routes the
map and the derived sprint status through the merge drivers in `scripts/bmad_merge_driver.py`:

- **The map** merges item by item. Main's map is kept, the branch's new entries are added in the
  tool's own JSON format, and `next_ids` takes the larger counter per kind. When main and the
  branch mapped the same GitHub issue under different IDs, main's entry wins and the driver names
  the dropped ID; remove that ID's story file from the branch. In a merge, main is the incoming
  side, since you merge main into your branch; in a rebase, it is the upstream side.
- **Sprint status** is rendered afresh from the merged map and the merged story files, and that
  render is the merge result, so the merge commit itself carries the regeneration. It needs the
  incoming commit, which `git merge` and `git pull` name; in a rebase or cherry-pick it reports a
  conflict instead, and you run `python3 scripts/bmad_issue_sync.py sprint-status`.
- **Anything it cannot settle exactly is a conflict**: an unparsable side, a field both sides
  changed differently, or one ID reserved for two issues. The last keeps both entries, so the
  audit's duplicate checks still fail on it. Resolve by hand, then regenerate sprint status.

`citizen worktree create` registers both drivers in the repository's git config each time it
runs, writing only a value that differs from the one it expects. It registers them only in the
repository the running `citizen` checkout belongs to, so another repository that ships a script of
the same name never has its merges routed through it. To register them in an existing checkout, run:

```sh
git config merge.bmad-issue-map.name "BMad issue map"
git config merge.bmad-issue-map.driver "python3 scripts/bmad_merge_driver.py map %O %A %B"
git config merge.bmad-sprint-status.name "BMad sprint status"
git config merge.bmad-sprint-status.driver "python3 scripts/bmad_merge_driver.py sprint-status %O %A %B"
```

Git config is shared by every worktree of a repository, so once is enough. Unregistered, git
merges both files as text, as it did before.
