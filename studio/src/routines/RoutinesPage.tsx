// Routines: the local checks launchd runs on a schedule, their last runs and their history, read
// from `GET /api/routines` (`citizen studio routine list --json`). Installing or removing one's
// launchd agent is typed and then confirmed at the Mac, as an apply is.
import { Alert, Anchor, Badge, Button, Group, Stack, Table, Text, TextInput, Title } from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";

import "../harness/harness.css";
import { CommandChip, EvidenceState } from "../components/StudioKit";
import { RoutineRefused, changeRoutine, routinesQuery, runRoutine } from "./api";
import {
  changeFor, confirmationBlocker, duration, outcomeColor, outcomeLabel, refusalText, scheduleText, triggerLabel, when,
  type RoutineRun, type RoutineStatus, type RoutinesListing,
} from "./model";

export const ROUTINE_PARAM = "routine";

function OutcomeBadge({ outcome }: { outcome: RoutineStatus["outcome"] }) {
  return <Badge className="harness-badge" variant="outline" tt="none" color={outcomeColor(outcome)}
    data-outcome={outcome}>{outcomeLabel(outcome)}</Badge>;
}

export function RoutinesTable({ routines, selected, onSelect, onRun, busy }: {
  routines: RoutineStatus[];
  selected: string;
  onSelect: (name: string) => void;
  onRun: (name: string) => void;
  busy: string;
}) {
  return <div className="harness-panel">
    <Table.ScrollContainer minWidth={720}
      scrollAreaProps={{ type: "auto", offsetScrollbars: false, viewportProps: { role: "region", "aria-label": "Routines", tabIndex: 0 } }}>
      <Table verticalSpacing="sm" aria-label="Routines" className="data-table harness-table">
        <Table.Thead>
          <Table.Tr>
            <Table.Th scope="col" className="harness-sticky">Routine</Table.Th>
            <Table.Th scope="col">Schedule</Table.Th>
            <Table.Th scope="col">Last run</Table.Th>
            <Table.Th scope="col">Next run</Table.Th>
            <Table.Th scope="col"><span className="visually-hidden">Actions</span></Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {routines.map((routine) => <Table.Tr key={routine.name} data-routine={routine.name}
            data-installed={routine.installed ? "yes" : "no"} aria-current={routine.name === selected ? "true" : undefined}>
            <Table.Th scope="row" className="harness-sticky">
              <Anchor component="button" type="button" fw={600} onClick={() => onSelect(routine.name)}>{routine.title}</Anchor>
              <Text size="xs" c="dimmed">{routine.description}</Text>
            </Table.Th>
            <Table.Td className="harness-nowrap">
              <Text size="sm">{routine.schedule.label}</Text>
              <Text size="xs" c="dimmed">{routine.installed ? "installed" : "not installed"}</Text>
            </Table.Td>
            <Table.Td className="harness-nowrap">
              <OutcomeBadge outcome={routine.outcome} />
              <Text size="xs" c="dimmed">{routine.last_run ? when(routine.last_run.started_at) : "–"}</Text>
            </Table.Td>
            <Table.Td className="harness-nowrap">
              <Text size="sm">{routine.installed ? when(routine.next_run) : "–"}</Text>
              {routine.overdue && <Badge className="harness-badge" variant="outline" color="orange" tt="none" data-overdue="yes">Overdue</Badge>}
            </Table.Td>
            <Table.Td className="harness-nowrap">
              <Button size="compact-sm" variant="default" loading={busy === `run:${routine.name}`}
                disabled={busy !== ""} onClick={() => onRun(routine.name)}
                aria-label={`Run ${routine.title} now`}>Run now</Button>
            </Table.Td>
          </Table.Tr>)}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  </div>;
}

export function RunHistory({ routine, runs }: { routine: RoutineStatus; runs: RoutineRun[] }) {
  if (runs.length === 0) {
    return <EvidenceState kind="empty" title="No runs yet">
      {routine.title} has not run on this Mac. Run it now, or install it to run {routine.schedule.label.toLowerCase()}.
    </EvidenceState>;
  }
  return <div className="harness-panel">
    <Table.ScrollContainer minWidth={640}
      scrollAreaProps={{ type: "auto", offsetScrollbars: false, viewportProps: { role: "region", "aria-label": `Run history: ${routine.title}`, tabIndex: 0 } }}>
      <Table verticalSpacing="sm" aria-label={`Run history: ${routine.title}, newest first`} className="data-table harness-table">
        <Table.Thead>
          <Table.Tr>
            <Table.Th scope="col">Started</Table.Th>
            <Table.Th scope="col">Result</Table.Th>
            <Table.Th scope="col">Trigger</Table.Th>
            <Table.Th scope="col" className="harness-num">Took</Table.Th>
            <Table.Th scope="col">Summary</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {runs.map((run) => <Table.Tr key={run.run_id} data-run={run.run_id} data-outcome={run.outcome}>
            <Table.Td className="harness-nowrap"><time dateTime={run.started_at}>{when(run.started_at)}</time></Table.Td>
            <Table.Td><OutcomeBadge outcome={run.outcome} /></Table.Td>
            <Table.Td className="harness-nowrap">{triggerLabel(run.trigger)}</Table.Td>
            <Table.Td className="harness-num">{duration(run)}</Table.Td>
            <Table.Td><Text size="sm">{run.summary}</Text></Table.Td>
          </Table.Tr>)}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  </div>;
}

/** Install or remove: the routine's name typed, then the Mac's own Touch ID or password dialog. */
export function ScheduleControls({ routine, typed, busy, onType, onChange }: {
  routine: RoutineStatus;
  typed: string;
  busy: string;
  onType: (value: string) => void;
  onChange: () => void;
}) {
  const action = changeFor(routine);
  const blocker = confirmationBlocker(routine, typed);
  const blocked = blocker !== "";
  const verb = action === "install" ? "Install" : "Remove";
  const blockerId = `routine-${routine.name}-blocker`;
  return <Stack gap="sm" component="section" aria-label={`Schedule: ${routine.title}`}>
    <TextInput
      label={action === "install" ? "Confirm the routine to install" : "Confirm the routine to remove"}
      description={action === "install"
        ? `Installing writes a launchd agent that runs it ${routine.schedule.label.toLowerCase()} on this Mac. Type ${routine.name} to confirm; the Mac then asks for Touch ID or your login password.`
        : `Removing unloads and deletes its launchd agent. Type ${routine.name} to confirm; the Mac then asks for Touch ID or your login password.`}
      value={typed}
      onChange={(event) => onType(event.currentTarget.value)}
    />
    <Group>
      <Button aria-describedby={blocked ? blockerId : undefined} aria-disabled={blocked || undefined}
        data-disabled={blocked || undefined} disabled={busy !== ""} loading={busy === `${action}:${routine.name}`}
        color={action === "remove" ? "red" : undefined} variant={action === "remove" ? "outline" : "filled"}
        onClick={() => { if (!blocked) onChange(); }}>{verb} {routine.name}</Button>
      <CommandChip command={`citizen studio routine ${action} ${routine.name} --json`} label={`${verb} from the CLI`} />
    </Group>
    {blocked ? <Text c="dimmed" id={blockerId} size="sm">{blocker}</Text> : null}
  </Stack>;
}

export function RoutineDetail({ routine, runs, typed, busy, onType, onChange }: {
  routine: RoutineStatus;
  runs: RoutineRun[];
  typed: string;
  busy: string;
  onType: (value: string) => void;
  onChange: () => void;
}) {
  return <Stack gap="md" component="section" aria-label={routine.title} data-selected={routine.name}>
    <Group justify="space-between" align="flex-end">
      <div>
        <Group gap="sm" align="center">
          <Title order={2} size="h3">{routine.title}</Title>
          <OutcomeBadge outcome={routine.outcome} />
          {routine.overdue && <Badge className="harness-badge" variant="outline" color="orange" tt="none">Overdue</Badge>}
        </Group>
        <Text size="sm" c="dimmed">{scheduleText(routine)}</Text>
      </div>
      <CommandChip command={routine.command} label="Run from the CLI" />
    </Group>
    <ScheduleControls routine={routine} typed={typed} busy={busy} onType={onType} onChange={onChange} />
    <Title order={3} size="h4">Run history</Title>
    <RunHistory routine={routine} runs={runs} />
  </Stack>;
}

/** The page body for a loaded listing. Kept free of hooks so tests render it directly. */
export function RoutinesView({ listing, selected, typed = "", busy = "", message = null, onSelect = () => {}, onRun = () => {}, onType = () => {}, onChange = () => {} }: {
  listing: RoutinesListing;
  selected: string;
  typed?: string;
  busy?: string;
  message?: { tone: "error" | "info"; text: string } | null;
  onSelect?: (name: string) => void;
  onRun?: (name: string) => void;
  onType?: (value: string) => void;
  onChange?: () => void;
}) {
  const routine = listing.routines.find((row) => row.name === selected) ?? listing.routines[0];
  return <Stack gap="lg">
    <div className="page-heading">
      <Title order={1}>Routines</Title>
      <Text c="dimmed" mt="xs">Checks that run on a schedule on this Mac. A routine reads; it never changes your setup.</Text>
    </div>
    <Text size="sm">Scheduled with launchd, so your transcripts never leave the machine. A run missed while the Mac sleeps runs when it wakes.</Text>
    {message && <Alert color={message.tone === "error" ? "red" : "teal"} variant="light" role={message.tone === "error" ? "alert" : "status"}>{message.text}</Alert>}
    <RoutinesTable routines={listing.routines} selected={routine?.name ?? ""} onSelect={onSelect} onRun={onRun} busy={busy} />
    {routine && <RoutineDetail routine={routine} runs={listing.history[routine.name] ?? []} typed={typed}
      busy={busy} onType={onType} onChange={onChange} />}
  </Stack>;
}

export function RoutinesPage() {
  const query = useQuery(routinesQuery);
  const client = useQueryClient();
  const [search, setSearch] = useSearchParams();
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState<{ tone: "error" | "info"; text: string } | null>(null);
  if (query.isPending) return <EvidenceState kind="loading" title="Reading routines">Run history is kept on this Mac.</EvidenceState>;
  if (query.isError) return <EvidenceState kind="error" title="Routines could not be read">{query.error.message}</EvidenceState>;
  const listing = query.data;
  const selected = search.get(ROUTINE_PARAM) ?? listing.routines[0]?.name ?? "";
  const routine = listing.routines.find((row) => row.name === selected) ?? listing.routines[0];

  const failed = (error: unknown) => setMessage({
    tone: "error",
    text: error instanceof RoutineRefused ? (error.reason || refusalText(error.code)) : String((error as Error)?.message ?? error),
  });
  const settle = async () => { setBusy(""); await client.invalidateQueries({ queryKey: routinesQuery.queryKey }); };

  const run = async (name: string) => {
    setBusy(`run:${name}`); setMessage(null);
    try {
      const done = await runRoutine(name);
      setMessage({ tone: done.outcome === "failed" ? "error" : "info", text: `${name}: ${outcomeLabel(done.outcome)}. ${done.summary}` });
    } catch (error) { failed(error); }
    await settle();
  };
  const change = async () => {
    if (!routine) return;
    const action = changeFor(routine);
    setBusy(`${action}:${routine.name}`); setMessage(null);
    try {
      const after = await changeRoutine(action, routine.name, typed);
      setTyped("");
      setMessage({ tone: "info", text: after.installed ? `${routine.title} is installed; next run ${when(after.next_run)}.` : `${routine.title} is removed.` });
    } catch (error) { failed(error); }
    await settle();
  };
  const select = (name: string) => {
    setTyped("");
    const next = new URLSearchParams(search);
    next.set(ROUTINE_PARAM, name);
    setSearch(next, { replace: true });
  };
  return <RoutinesView listing={listing} selected={selected} typed={typed} busy={busy} message={message}
    onSelect={select} onRun={(name) => { void run(name); }} onType={setTyped} onChange={() => { void change(); }} />;
}
