import { Button, Group, Paper, Stack, Text, Title } from "@mantine/core";
import { useEffect, useState } from "react";
import { useLocation, useSearchParams } from "react-router-dom";

import { CommandChip, DataTable, EvidenceState, StatusBadge } from "../components/StudioKit";
import { ConfigurePage } from "../configure/ConfigurePage";
import { DraftApply } from "../configure/DraftApply";
import { DraftTest } from "../configure/DraftTest";
import { RollbackPanel, RollbackResultRegion } from "../activity/Rollback";
import type { RollbackNotice } from "../activity/rollbackModel";
import { useDrafts, useHistory } from "./api";
import "./changes.css";
import { DraftPicker } from "./DraftPicker";
import {
  STEPS, canUndo, entryLabel, inboxGroups, rollbackTarget, shortTime, statusLabel, statusTone,
  type ChangeEntry, type ChangesDraft,
} from "./model";

/** Where a draft stands: each step it has reached, the current one marked for assistive tech. */
export function DraftSteps({ draft }: { draft: ChangesDraft }) {
  const reached = STEPS.findIndex((step) => step.status === draft.status);
  const shown = draft.status === "rolled back" ? STEPS : STEPS.slice(0, 3);
  return (
    <ol aria-label={`${draft.name}: ${statusLabel(draft.status)}`} className="change-steps">
      {shown.map((step, index) => (
        <li aria-current={index === reached ? "step" : undefined} data-reached={index <= reached || undefined} key={step.status}>
          {step.label}
        </li>
      ))}
    </ol>
  );
}

type InboxProps = {
  drafts: ChangesDraft[];
  status: "loading" | "error" | "ready";
  error: string;
  selected: string;
  onSelect: (name: string) => void;
};

function DraftCard({ draft, selected, onSelect }: { draft: ChangesDraft; selected: boolean; onSelect: (name: string) => void }) {
  return (
    <Paper className="change-card" data-draft={draft.name} data-status={draft.status} p="md" radius="md">
      <Group align="flex-start" justify="space-between" wrap="wrap">
        <div>
          <Title order={3} size="h4">{draft.name}</Title>
          <Text c="dimmed" size="sm">
            {draft.source ? `From ${draft.source}` : "From you"} · started {shortTime(draft.created_at)}
            {draft.tests ? ` · ${draft.tests} test${draft.tests === 1 ? "" : "s"}` : ""}
            {draft.behind_installed ? ` · ${draft.behind_by ?? "some"} commits behind the installed version` : ""}
          </Text>
        </div>
        <StatusBadge tone={statusTone(draft.status)}>{statusLabel(draft.status)}</StatusBadge>
      </Group>
      <DraftSteps draft={draft} />
      <Group gap="sm">
        <Button aria-pressed={selected} onClick={() => onSelect(draft.name)} size="xs" variant={selected ? "filled" : "default"}>
          {selected ? `Showing ${draft.name}` : `Open ${draft.name}`}
        </Button>
      </Group>
    </Paper>
  );
}

/** Every draft: those waiting on you first, then those applied or rolled back. */
export function ChangesInbox({ drafts, status, error, selected, onSelect }: InboxProps) {
  if (status === "loading") return <EvidenceState kind="loading" title="Reading drafts" />;
  if (status === "error") return <EvidenceState kind="error" title="Drafts could not be read">{error}</EvidenceState>;
  const { waiting, settled } = inboxGroups(drafts);
  return (
    <Stack gap="lg">
      <section aria-labelledby="changes-waiting">
        <Title id="changes-waiting" mb="sm" order={2} size="h3">Waiting for you ({waiting.length})</Title>
        {waiting.length ? <Stack gap="sm">{waiting.map((draft) =>
          <DraftCard draft={draft} key={draft.draft_id} onSelect={onSelect} selected={draft.name === selected} />)}</Stack>
          : <EvidenceState kind="empty" title="Nothing waiting">Start a draft above, or save a change from a Harness page's Settings tab.</EvidenceState>}
      </section>
      {settled.length > 0 && (
        <section aria-labelledby="changes-settled">
          <Title id="changes-settled" mb="sm" order={2} size="h3">Applied or rolled back ({settled.length})</Title>
          <Stack gap="sm">{settled.map((draft) =>
            <DraftCard draft={draft} key={draft.draft_id} onSelect={onSelect} selected={draft.name === selected} />)}</Stack>
        </section>
      )}
    </Stack>
  );
}

type HistoryProps = {
  entries: ChangeEntry[];
  status: "loading" | "error" | "ready";
  error: string;
  undo: string;
  onUndo: (applyId: string) => void;
};

/** Past applies and rollbacks, newest first, each completed one with its rollback preview. */
export function ChangeHistory({ entries, status, error, undo, onUndo }: HistoryProps) {
  if (status === "loading") return <EvidenceState kind="loading" title="Reading the apply journal" />;
  if (status === "error") return <EvidenceState kind="error" title="History could not be read">{error}</EvidenceState>;
  return (
    <DataTable<ChangeEntry>
      caption="Applies and rollbacks, newest first"
      empty="Nothing has been applied yet"
      rowKey={(entry) => entry.apply_id}
      rows={entries}
      columns={[
        { key: "when", heading: "When", cell: (entry) => shortTime(entry.ts) },
        { key: "change", heading: "Change", cell: (entry) => entryLabel(entry) },
        { key: "by", heading: "By", cell: (entry) => entry.actor || "unknown" },
        { key: "state", heading: "State", cell: (entry) => <StatusBadge tone={entry.status === "completed" ? "success" : entry.status === "open" ? "warning" : "neutral"}>{entry.status}</StatusBadge> },
        { key: "undo", heading: "Undo", cell: (entry) => canUndo(entry)
          ? <Button aria-pressed={undo === entry.apply_id} onClick={() => onUndo(undo === entry.apply_id ? "" : entry.apply_id)} size="compact-xs" variant="default">
              {undo === entry.apply_id ? "Hide" : "Roll back…"}
            </Button>
          : <Text c="dimmed" size="xs">None</Text> },
      ]}
    />
  );
}

/** The selected draft: its diff and checks (review), its tests, apply and recover, and rollback. */
function DraftDetail({ draft, onChanged, onRollback }: { draft: ChangesDraft; onChanged: () => void; onRollback: (notice: RollbackNotice) => void }) {
  const target = rollbackTarget(draft);
  return (
    <Paper aria-labelledby="change-detail-title" className="settings-section" component="section" p="lg" radius="md">
      <Group justify="space-between" wrap="wrap">
        <Title id="change-detail-title" order={2} size="h3">{draft.name}</Title>
        <StatusBadge tone={statusTone(draft.status)}>{statusLabel(draft.status)}</StatusBadge>
      </Group>
      <Group gap="xs" mt="xs">
        <CommandChip command={`citizen draft diff ${draft.name}`} label="Diff from the CLI" />
      </Group>
      <Stack gap="lg" mt="md">
        <DraftTest draft={draft.name} revision={draft.revision} />
        <DraftApply draft={draft.name} onApplied={onChanged} revision={draft.revision} />
        {target && (
          <section aria-labelledby="change-rollback-title">
            <Title id="change-rollback-title" order={3} size="h4">Roll back this apply</Title>
            <RollbackPanel applyId={target} onChanged={onChanged} onResult={onRollback} />
          </section>
        )}
      </Stack>
    </Paper>
  );
}

export function ChangesPage() {
  const [params, setParams] = useSearchParams();
  const selected = params.get("draft") ?? "";
  const drafts = useDrafts();
  const history = useHistory();
  const [undo, setUndo] = useState("");
  const [notice, setNotice] = useState<RollbackNotice | null>(null);
  const current = drafts.drafts.find((draft) => draft.name === selected) ?? null;
  const location = useLocation();
  useEffect(() => {
    // A link to `/changes#governed-sync` (Home's drift item) lands on that section.
    if (location.hash) document.getElementById(location.hash.slice(1))?.scrollIntoView({ block: "start" });
  }, [location.hash]);

  function select(name: string) {
    const next = new URLSearchParams(params);
    if (name) next.set("draft", name); else next.delete("draft");
    setParams(next, { replace: true });
  }

  function changed() {
    void drafts.refresh();
  }

  function rolledBack(result: RollbackNotice) {
    setNotice(result);
    setUndo("");
    changed();
  }

  return (
    <Stack gap="xl">
      <Group align="flex-end" className="page-heading" justify="space-between" wrap="wrap">
        <div>
          <Title order={1}>Changes</Title>
          <Text c="dimmed" mt="xs">Every draft change to your harness. Nothing applies until you approve it, and anything applied can be rolled back.</Text>
        </div>
        <CommandChip command="citizen draft list --json" label="List from the CLI" />
      </Group>

      <Paper aria-labelledby="changes-new-title" component="section" p="md" radius="md">
        <Title id="changes-new-title" mb="xs" order={2} size="h4">Open or start a draft</Title>
        <DraftPicker onChange={select} value={selected} />
      </Paper>

      <RollbackResultRegion notice={notice} onDismiss={() => setNotice(null)} />

      <ChangesInbox drafts={drafts.drafts} error={drafts.error} onSelect={select} selected={selected} status={drafts.status} />

      {current && <DraftDetail draft={current} key={`${current.name}:${current.revision}`} onChanged={changed} onRollback={rolledBack} />}
      {selected && !current && drafts.status === "ready" && (
        <EvidenceState kind="empty" title={`No draft named ${selected}`}>It may have been discarded. Pick another draft above.</EvidenceState>
      )}

      <section aria-labelledby="changes-history-title">
        <Group justify="space-between" mb="sm" wrap="wrap">
          <Title id="changes-history-title" order={2} size="h3">History</Title>
          <CommandChip command="citizen draft history --json" label="History from the CLI" />
        </Group>
        <ChangeHistory
          entries={history.data?.entries ?? []}
          error={history.error instanceof Error ? history.error.message : ""}
          onUndo={setUndo}
          status={history.isError ? "error" : history.isPending ? "loading" : "ready"}
          undo={undo}
        />
        {undo && <RollbackPanel applyId={undo} key={undo} onChanged={changed} onResult={rolledBack} />}
      </section>

      <section aria-labelledby="configure-embedded-title" id="drafts">
        <ConfigurePage embedded />
      </section>
    </Stack>
  );
}
