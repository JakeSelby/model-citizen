import { Alert, Anchor, Badge, Stack, Text } from "@mantine/core";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { periodsQuery, usePeriods } from "../../harness/api";
import { HarnessPage } from "../../harness/HarnessPage";
import { tabSearch, type MetricColumn, type PeriodRow } from "../../harness/model";
import { saveSelectionToDraft } from "../../harness/selectedDraft";
import { useLibrarySettings } from "./librarySettings";
import {
  NEVER_INVOKED, SKILL_FIELDS, SKILL_METRICS, SKILL_READ_ONLY_NOTE, indexingText, invokedShare, invokedText,
  leadFigures, leadHeadline, presentRows, skillSelectionChanges, skillSettings, tokensByName, useOf, useSkillSummary, withSwitchDates,
  type SkillSummary,
} from "./skillsModel";
import "./skills.css";

export function useColor(use: string): string {
  if (use === "invoked") return "green";
  if (use === NEVER_INVOKED) return "yellow";
  return "gray";
}

/** The badge's words: the row already says the skill was loaded, so the never-invoked badge drops it. */
export function useLabel(use: string): string {
  return use === NEVER_INVOKED ? "never invoked" : use;
}

/** "6 of 34" beside a bar of the same share; the bar is decoration, the text carries the figure. */
export function InvokedCell({ row }: { row: PeriodRow }) {
  const share = invokedShare(row);
  return <span className="skills-invoked">
    {share !== undefined && <span className="skills-bar" aria-hidden="true">
      <span style={{ width: `${Math.round(share * 100)}%` }} />
    </span>}
    <span>{invokedText(row)}</span>
  </span>;
}

function dash(label: string) {
  return <Text span size="sm" c="dimmed"><span aria-hidden="true">–</span><span className="visually-hidden">{label}</span></Text>;
}

/** Skills' columns: figures, then the description tokens from Settings, then each period's use. */
export function skillColumns(tokens: Map<string, number> = new Map()): MetricColumn[] {
  const [sessions, invocations] = SKILL_METRICS;
  return [
    sessions,
    { key: "invoked_in", heading: "Invoked in", align: "right", render: (row) => <InvokedCell row={row} /> },
    invocations,
    ...(tokens.size ? [{
      key: "tokens", heading: "Tokens", align: "right" as const,
      render: (row: PeriodRow) => {
        const value = tokens.get(row.item_id);
        return value === undefined || row.metrics.loaded_in === 0 ? dash("not loaded") : value.toLocaleString("en-US");
      },
    }] : []),
    {
      key: "use", heading: "Use", align: "left",
      render: (row: PeriodRow) => <Badge className="harness-badge" variant="outline" tt="none" color={useColor(useOf(row))}
        title={useOf(row)}>{useLabel(useOf(row))}</Badge>,
    },
  ];
}

export const SKILL_COLUMNS: MetricColumn[] = skillColumns();

export const OBSERVATIONAL = "Observational: these counts are read from your own Claude Code transcripts on this machine. They say what ran, not what a skill is worth.";

/** What the columns mean; the basis is said here only when the lead, which says it, is not shown. */
export function efficacyNote(withBasis: boolean) {
  return <Text size="sm" c="dimmed">
    Loaded means the skill was in the session's skill listing; invoked means the session called it,
    by the Skill tool or its slash command. Tokens is the description's soft estimate,
    spent in every session that loads it.{withBasis && <> {OBSERVATIONAL}</>}
  </Text>;
}

export const SKILLS_CONTENT = {
  title: "Skills",
  question: "Which skills each session loaded, and which it actually invoked.",
  itemNoun: "Skill",
  metrics: SKILL_COLUMNS,
  fields: SKILL_FIELDS,
  settingsNote: SKILL_READ_ONLY_NOTE,
  order: presentRows,
  efficacyNote: efficacyNote(true),
};

function tokensText(tokens: number | undefined): string | undefined {
  return tokens === undefined ? undefined : `${tokens.toLocaleString("en-US")} description tokens a session`;
}

/** The Efficacy lead: one sentence, the window's figures as a strip, and the reading's basis. */
export function SkillsLead({ summary, tokens = new Map(), settingsHref }: {
  summary: SkillSummary | undefined;
  tokens?: Map<string, number>;
  settingsHref?: string;
}) {
  if (!summary) return null;
  const indexing = indexingText(summary);
  const indexingAlert = indexing && <Alert className="harness-note" title="Still indexing" role="status">{indexing}</Alert>;
  if (!summary.sessions) return indexingAlert || null;
  const figures = leadFigures(summary, tokens);
  const neverTokens = tokensText(figures.neverTokens);
  return <Stack gap="md" component="section" aria-labelledby="skills-lead-title" className="skills-lead">
    {indexingAlert}
    <div>
      <Text id="skills-lead-title" fw={600} size="md" component="h2" className="skills-lead-title">{leadHeadline(summary, figures)}</Text>
      {figures.never.length > 0 && <Text size="sm" c="dimmed" maw="80ch">
        Each one's description is in the context of every session that loads it{neverTokens ? `, ${neverTokens} between them` : ""}.
        {settingsHref && <> <Anchor component={Link} to={settingsHref} size="sm">Review them in Settings</Anchor></>}
      </Text>}
    </div>
    <dl className="number-strip skills-strip">
      <div className="strip-item">
        <dt>Sessions read</dt>
        <dd className="strip-value">{summary.sessions.toLocaleString("en-US")}</dd>
        <dd className="strip-note">last {summary.window_days} days</dd>
      </div>
      <div className="strip-item">
        <dt>Invoked</dt>
        <dd className="strip-value">{figures.invoked} of {figures.loaded}</dd>
        <dd className="strip-note">loaded skills, in at least one session</dd>
      </div>
      <div className="strip-item">
        <dt>Loaded, never invoked</dt>
        <dd className="strip-value">{figures.never.length}</dd>
        <dd className="strip-note">{neverTokens ?? "in no session this window"}</dd>
      </div>
      {figures.loadedTokens !== undefined && <div className="strip-item">
        <dt>Description tokens</dt>
        <dd className="strip-value">{figures.loadedTokens.toLocaleString("en-US")}</dd>
        <dd className="strip-note">a session, every loaded skill</dd>
      </div>}
    </dl>
    <Text size="xs" c="dimmed">{summary.basis}</Text>
  </Stack>;
}

export function SkillsPage() {
  const library = useLibrarySettings(skillSettings);
  const periods = usePeriods("skills");
  const settings = library.state === "ready" ? { ...library, rows: withSwitchDates(library.rows, periods.data?.rows) } : library;
  const summary = useSkillSummary();
  const leadShown = Boolean(summary.data?.sessions);
  const client = useQueryClient();
  const [searchParams] = useSearchParams();
  const wasIndexing = useRef(false);
  const indexing = Boolean(summary.data?.indexing);
  const tokens = tokensByName(settings.state === "ready" ? settings.rows : undefined);
  // The periods were read from a partial index; read them again once it is complete.
  useEffect(() => {
    if (wasIndexing.current && !indexing) void client.invalidateQueries({ queryKey: periodsQuery("skills").queryKey });
    wasIndexing.current = indexing;
  }, [client, indexing]);
  return <div className="skills-page">
    <HarnessPage surface="skills" {...SKILLS_CONTENT} metrics={skillColumns(tokens)} settings={settings}
      efficacyNote={efficacyNote(!leadShown)}
      efficacyLead={<SkillsLead summary={summary.data} tokens={tokens} settingsHref={`?${tabSearch(searchParams, "settings").toString()}`} />}
      onDraft={(changes, draft) => saveSelectionToDraft(draft, skillSelectionChanges(changes))} />
  </div>;
}
