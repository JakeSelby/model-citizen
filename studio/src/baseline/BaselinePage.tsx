// Baseline: start from what you have. Shows the config, stances and plugins in effect now, the
// baselines recorded so far, and records a new one; the guided first run stays one link away.
import { Alert, Anchor, Badge, Button, Code, Group, Stack, Table, Text, TextInput, Title } from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";

import { EvidenceState } from "../components/StudioKit";
import { FirstRunPage } from "../firstrun/FirstRunPage";
import { loadOverview } from "../overview/api";
import { SystemSummary } from "../overview/OverviewPage";
import "../harness/harness.css";
import { baselineQuery, recordBaseline } from "./api";
import {
  FIRST_RUN_HREF, LABEL_MAX, baselineSummary, labelProblem, recordedAt, settingText, showsFirstRun, sortedEntries,
  type BaselineCurrent, type BaselineResponse, type RecordedBaseline,
} from "./model";

/** A hairline-edged table in the Harness pages' look, with an accessible scroll region. */
function Hairline({ caption, head, children }: { caption: string; head: string[]; children: ReactNode }) {
  return <div className="harness-panel">
    <Table.ScrollContainer minWidth={480}
      scrollAreaProps={{ type: "auto", offsetScrollbars: false, viewportProps: { role: "region", "aria-label": caption, tabIndex: 0 } }}>
      <Table verticalSpacing="sm" aria-label={caption} className="data-table harness-table">
        <Table.Caption className="visually-hidden">{caption}</Table.Caption>
        <Table.Thead>
          <Table.Tr>{head.map((heading, index) =>
            <Table.Th scope="col" key={heading} className={index === 0 ? "harness-item" : undefined}>{heading}</Table.Th>)}</Table.Tr>
        </Table.Thead>
        <Table.Tbody>{children}</Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  </div>;
}

function Section({ title, lead, children, label }: { title: string; lead?: ReactNode; children: ReactNode; label: string }) {
  return <Stack gap="xs" component="section" aria-label={label}>
    <Title order={2} size="h4">{title}</Title>
    {lead && <Text size="sm" c="dimmed">{lead}</Text>}
    {children}
  </Stack>;
}

/** What you already run: stances, rule switches, settings and enabled plugins, kept as they are. */
export function CurrentSetup({ current }: { current: BaselineCurrent }) {
  const stances = sortedEntries(current.stances);
  const rules = sortedEntries(current.rules);
  return <Section label="What you already run" title="What you already run"
    lead={<>Read from <Code>{current.config_path}</Code> and Claude Code's enabled plugins. Nothing here is rewritten or moved.</>}>
    {stances.length
      ? <Hairline caption="Stances in effect" head={["Dimension", "Variant"]}>
        {stances.map(([name, value]) => <Table.Tr key={name} data-stance={name}>
          <Table.Th scope="row" fw={600}>{name}</Table.Th><Table.Td>{value}</Table.Td>
        </Table.Tr>)}
      </Hairline>
      : <EvidenceState kind="empty" title="No stances chosen">Every dimension runs its default variant.</EvidenceState>}
    {rules.length > 0 && <Hairline caption="Rule switches" head={["Rule", "Switch"]}>
      {rules.map(([name, value]) => <Table.Tr key={name} data-rule={name}>
        <Table.Th scope="row" fw={600}>{name}</Table.Th><Table.Td>{value}</Table.Td>
      </Table.Tr>)}
    </Hairline>}
    {current.plugins.length
      ? <Hairline caption="Enabled plugins" head={["Plugin", "Components", "Installed at"]}>
        {current.plugins.map((plugin) => <Table.Tr key={plugin.id} data-plugin={plugin.label}>
          <Table.Th scope="row" fw={600}>{plugin.label}</Table.Th>
          <Table.Td className="harness-num">{String(plugin.modules)}</Table.Td>
          <Table.Td><Text size="sm" c="dimmed" lineClamp={1} title={plugin.path}>{plugin.path}</Text></Table.Td>
        </Table.Tr>)}
      </Hairline>
      : <EvidenceState kind="empty" title="No third-party plugins enabled">Only the harness itself loads.</EvidenceState>}
    {current.settings.length > 0 && <Hairline caption="Settings" head={["Setting", "Value"]}>
      {current.settings.map((setting) => <Table.Tr key={setting.path} data-setting={setting.path}>
        <Table.Th scope="row" fw={600}>{setting.label}</Table.Th>
        <Table.Td>{settingText(setting.value)}</Table.Td>
      </Table.Tr>)}
    </Hairline>}
  </Section>;
}

/** Recorded baselines, newest first; each starts a period Evals compares against. */
export function RecordedBaselines({ baselines }: { baselines: RecordedBaseline[] }) {
  if (!baselines.length) {
    return <EvidenceState kind="empty" title="No baseline recorded yet">
      Record one to start a period that later changes are measured against.
    </EvidenceState>;
  }
  return <Hairline caption="Recorded baselines, newest first" head={["Recorded", "Label", "Holds", "Status"]}>
    {baselines.map((entry, index) => <Table.Tr key={entry.id} data-baseline={entry.id}>
      <Table.Th scope="row" fw={400} className="harness-nowrap"><time dateTime={entry.ts}>{recordedAt(entry.ts)}</time></Table.Th>
      <Table.Td>{entry.label || <Text span size="sm" c="dimmed">unlabelled</Text>}</Table.Td>
      <Table.Td className="harness-nowrap">{baselineSummary(entry)}</Table.Td>
      <Table.Td className="harness-nowrap">
        <Group gap="xs" wrap="nowrap">
          <Badge className="harness-badge" variant="outline" color={index === 0 ? "blue" : "gray"} tt="none">
            {index === 0 ? "latest" : "recorded"}
          </Badge>
          <Anchor component={Link} to="/evals" size="sm" aria-label={`Compare in Evals: baseline of ${recordedAt(entry.ts)}`}>Compare in Evals</Anchor>
        </Group>
      </Table.Td>
    </Table.Tr>)}
  </Hairline>;
}

export function RecordForm({ label, onLabel, onRecord, recording = false, error = "", command }: {
  label: string;
  onLabel: (label: string) => void;
  onRecord: () => void;
  recording?: boolean;
  error?: string;
  command: string;
}) {
  const problem = labelProblem(label);
  return <Stack gap="xs">
    <Group align="flex-end" gap="sm">
      <TextInput label="Label (optional)" placeholder="before the context stance" value={label} maxLength={LABEL_MAX}
        error={problem || undefined} onChange={(event) => onLabel(event.currentTarget.value)} w={320} />
      <Button onClick={onRecord} loading={recording} disabled={Boolean(problem)}>Record baseline</Button>
    </Group>
    <Text size="xs" c="dimmed">Recording writes only Studio state, never your config. Headless: <Code>{command}</Code></Text>
    {error && <Alert color="red" variant="light" title="The baseline was not recorded" role="alert">{error}</Alert>}
  </Stack>;
}

/** The way into the guided first run, for a harness set up from scratch. */
export function FirstRunLink({ configPresent }: { configPresent: boolean }) {
  return <Section label="Set up from scratch" title="Set up from scratch"
    lead={configPresent
      ? "Starting over, or setting up another machine? The guided first run takes a draft from a fresh install to an applied, doctor-checked harness."
      : "There is no harness configuration here yet, so there is little to start from. The guided first run sets one up."}>
    <div><Button component={Link} to={FIRST_RUN_HREF} variant={configPresent ? "default" : "filled"}>Open the guided first run</Button></div>
  </Section>;
}

/** The page body for a loaded baseline. Kept free of hooks so tests render it directly. */
export function BaselineView({ data, label, onLabel, onRecord, recording = false, error = "", installed }: {
  data: BaselineResponse;
  label: string;
  onLabel: (label: string) => void;
  onRecord: () => void;
  recording?: boolean;
  error?: string;
  installed?: ReactNode;
}) {
  return <Stack gap="lg">
    <div>
      <Title order={1}>Start from what you have</Title>
      <Text c="dimmed" size="sm">
        Studio reads the setup you already run. Record it as a baseline and every Harness page and Evals start a
        period there, so each later change has a before to compare against.
      </Text>
    </div>
    {!data.current.config_present && <FirstRunLink configPresent={false} />}
    {installed && <Section label="What you have now" title="What you have now"
      lead="The installed harness, its doctor checks and projection drift, read as citizen doctor reads them.">
      {installed}
    </Section>}
    <CurrentSetup current={data.current} />
    <Section label="Your baselines" title="Your baselines" lead="Each baseline holds the config and plugins it saw.">
      <RecordForm label={label} onLabel={onLabel} onRecord={onRecord} recording={recording} error={error} command={data.commands.record} />
      <RecordedBaselines baselines={data.baselines} />
    </Section>
    {data.current.config_present && <FirstRunLink configPresent />}
  </Stack>;
}

/** The guided first run, shown inside Baseline with a way back. */
export function FirstRunGuide() {
  return <Stack gap="md">
    <Text size="sm" component="nav" aria-label="Breadcrumb">
      <Anchor component={Link} to="/baseline">Baseline</Anchor> / Guided first run
    </Text>
    <FirstRunPage />
  </Stack>;
}

export function BaselinePage() {
  const location = useLocation();
  const queryClient = useQueryClient();
  const query = useQuery({ ...baselineQuery, enabled: !showsFirstRun(location.search) });
  const [label, setLabel] = useState("");
  const record = useMutation({
    mutationFn: () => recordBaseline(label),
    onSuccess: () => {
      setLabel("");
      void queryClient.invalidateQueries({ queryKey: baselineQuery.queryKey });
      void queryClient.invalidateQueries({ queryKey: ["harness-periods"] });
    },
  });
  if (showsFirstRun(location.search)) return <FirstRunGuide />;
  if (query.isPending) return <EvidenceState kind="loading" title="Reading what you have">Config and plugins are read from this machine.</EvidenceState>;
  if (query.isError) return <EvidenceState kind="error" title="Baseline could not be read">{query.error.message}</EvidenceState>;
  return <BaselineView data={query.data} label={label} onLabel={setLabel} onRecord={() => record.mutate()}
    recording={record.isPending} error={record.error?.message ?? ""} installed={<InstalledSystem />} />;
}

/** Hub's installed-system summary, read from the same overview the Hub reads. */
function InstalledSystem() {
  const overview = useQuery({ queryKey: ["overview"], queryFn: loadOverview });
  if (overview.isPending) return <EvidenceState kind="loading" title="Reading the installed harness">The same local sources as citizen doctor, diff and catalog.</EvidenceState>;
  if (overview.isError) return <EvidenceState kind="error" title="Installed system unavailable">{overview.error.message}</EvidenceState>;
  return <SystemSummary overview={overview.data} />;
}
