---
title: "Sprint change proposal: combine the Model Citizen rename and package fix in v0.14.1"
status: approved
created: 2026-09-27
approved: 2026-09-27
trigger_story: AH-S323
related_issues:
  - 1037
  - 1040
related_pull_requests:
  - 1038
---

# Sprint change proposal: combine the Model Citizen rename and package fix in v0.14.1

## Issue summary

AH-S323 originally prepared a documentation-only v0.14.1 release by carrying v0.14.0 native
qualification evidence through the one-release bootstrap exception. During implementation, Jake
approved including the runtime-distributed package fix in #1038 in the same patch release. That
change is outside the carry-forward allowlist, so the prepared no-rerun release state is no longer
valid and v0.14.1 needs one fresh native qualification round after #1038 lands.

## Impact analysis

- **Epic:** AH-E014's goal remains unchanged: one qualification round whose claims match its
  evidence. The release sequence changes, not the epic scope.
- **Stories:** AH-S323 becomes a preparatory branding/docs PR. #1037/#1038 remains the runtime fix.
  Final version, catalog, migration, changelog assembly, qualification, tag, and publication happen
  only on their combined source revision.
- **PRD and architecture:** no edits are required. PRD FR-51/FR-52 and architecture AD-4 already
  require source-pinned evidence and a fresh round when runtime source changes.
- **Technical and deployment:** remove AH-S323's candidate `VERSION`, released catalog,
  carry-forward limitation, migration, freeze, release-note assembly, and compatibility-validator
  changes. Keep living-brand corrections, regression coverage, and an Unreleased changelog
  fragment. Do not tag, publish, or deploy release-pinned sites from the branding-only commit.
- **UX:** no interface or user-flow change.

## Recommended approach

Use a direct adjustment with moderate coordination risk and low implementation effort:

1. Amend AH-S323 so it corrects living branding without claiming v0.14.1 is released or qualified.
2. Restore the v0.14.0 release state and both pending changelog fragments on the branding branch.
3. Land or stack #1038 on the branding commit, then freeze the combined candidate.
4. Run the model-free smoke tier and one fresh native qualification round on that exact commit.
5. Assemble v0.14.1 metadata, tag it, and deploy every release surface only after the round passes.

The alternative—shipping the carry-forward release before #1038—would create two patch releases
and contradict the approved bundling decision. Widening the bootstrap exception is not viable
because #1038 changes runtime-distributed paths.

## Detailed change proposals

### Story AH-S323: approach

**Old:** Prepare v0.14.1 through the one-release carry-forward exception and keep #1037 out until
after the tag.

**New:** Prepare branding corrections as unreleased work, include #1038 in v0.14.1, and perform one
fresh native qualification round after both changes are on the frozen candidate.

### Story AH-S323: acceptance

**Old:** Release tools accept carried v0.14.0 evidence and preflight proves the candidate stays
inside the bootstrap allowlist.

**New:** The branding PR leaves released metadata at v0.14.0, adds an Unreleased fragment, and does
not claim qualification or publication. The combined candidate must pass fresh qualification
before v0.14.1 metadata, tag, GitHub release, About sync, or site deployment.

### PRD, architecture, and UX

No text changes. Existing release and evidence contracts govern the corrected sequence.

## Implementation handoff

This is a **moderate** change because it reorganizes two in-flight PRs and the release boundary.

- **AH-S323 developer:** remove obsolete carry-forward release state, preserve the branding/docs
  changes, add the #1040 changelog fragment, verify, review, and publish the preparatory PR.
- **#1038 developer:** update the package-fix branch onto the accepted AH-S323 commit and provide
  the combined candidate revision.
- **Release maintainer:** run one fresh qualification round on the frozen combined revision, then
  complete all five release surfaces and the dependent website deployments.

Success means the branding PR makes no premature stable-release claim, #1038 is included in the
v0.14.1 candidate, qualification evidence names the exact combined commit, and every public surface
deploys from the resulting tag.

## Checklist result

- [x] Trigger and evidence identified: Jake approved bundling #1038; its paths invalidate reuse.
- [x] Epic, future-story, PRD, architecture, UX, testing, documentation, and deployment impacts assessed.
- [x] Direct adjustment selected over rollback or MVP redefinition.
- [x] Before/after story edits and implementation handoff defined.
- [x] User approval recorded by the instruction: “Approve and continue.”
