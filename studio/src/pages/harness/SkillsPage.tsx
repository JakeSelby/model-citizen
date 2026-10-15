import { Alert, Badge, Box, Group, Stack, Text } from "@mantine/core";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { periodsQuery } from "../../harness/api";
import { HarnessPage } from "../../harness/HarnessPage";
import type { MetricColumn, PeriodRow } from "../../harness/model";
import { saveSelectionToDraft } from "../../harness/selectedDraft";
import { useLibrarySettings } from "./librarySettings";
import {
  NEVER_INVOKED, SKILL_FIELDS, SKILL_METRICS, SKILL_READ_ONLY_NOTE, indexingText, leadFigures, presentRows,
  skillSelectionChanges, skillSettings, useOf, useSkillSummary, type SkillSummary,
} from "./skillsModel";

export function useColor(use: string): string {
  if (use === "invoked") return "green";
  if (use === NEVER_INVOKED) return "yellow";
  return "gray";
}

/** Skills' columns, with each period's use as a badge. */
export const SKILL_COLUMNS: MetricColumn[] = [
  ...SKILL_METRICS,
  {
    key: "use", heading: "Use", align: "left",
    render: (row: PeriodRow) => <Badge className="harness-badge" variant="light" tt="none" color={useColor(useOf(row))}>{useOf(row)}</Badge>,
  },
];

export const OBSERVATIONAL = "Observational: these counts are read from your own Claude Code transcripts on this machine. They say what ran, not what a skill is worth.";

export const SKILLS_CONTENT = {
  title: "Skills",
  question: "Which skills each session loaded, and which it actually invoked.",
  itemNoun: "Skill",
  metrics: SKILL_COLUMNS,
  fields: SKILL_FIELDS,
  settingsNote: SKILL_READ_ONLY_NOTE,
  order: presentRows,
  efficacyNote: <Text size="sm" c="dimmed">
    Loaded means the skill was in the session's skill listing; invoked means the session called it,
    by the Skill tool or its slash command. {OBSERVATIONAL}
  </Text>,
};

/** The Efficacy lead: the window's figures, the skills loaded and never invoked, and the index state. */
export function SkillsLead({ summary }: { summary: SkillSummary | undefined }) {
  if (!summary) return null;
  const { invoked, loaded, never } = leadFigures(summary);
  const indexing = indexingText(summary);
  return <Stack gap="sm" component="section" aria-label="Skills loaded versus invoked">
    {indexing && <Alert className="harness-note" title="Still indexing" role="status">{indexing}</Alert>}
    <Group gap="xl" wrap="wrap" align="flex-start">
      <Box>
        <Text size="xs" c="dimmed">Sessions read, last {summary.window_days} days</Text>
        <Text fw={600} size="lg">{summary.sessions.toLocaleString("en-US")}</Text>
      </Box>
      <Box>
        <Text size="xs" c="dimmed">Invoked</Text>
        <Text fw={600} size="lg">{invoked} of {loaded} loaded</Text>
      </Box>
      <Box>
        <Text size="xs" c="dimmed">Loaded, never invoked</Text>
        <Text fw={600} size="lg">{never.length}</Text>
      </Box>
    </Group>
    {never.length > 0 && <Text size="sm">
      Loaded in this window and invoked in no session: {never.map((row) => row.skill).join(", ")}.
      Each one's description costs context in every session; the Settings tab can turn it off.
    </Text>}
    <Text size="xs" c="dimmed">{summary.basis}</Text>
  </Stack>;
}

export function SkillsPage() {
  const settings = useLibrarySettings(skillSettings);
  const summary = useSkillSummary();
  const client = useQueryClient();
  const wasIndexing = useRef(false);
  const indexing = Boolean(summary.data?.indexing);
  // The periods were read from a partial index; read them again once it is complete.
  useEffect(() => {
    if (wasIndexing.current && !indexing) void client.invalidateQueries({ queryKey: periodsQuery("skills").queryKey });
    wasIndexing.current = indexing;
  }, [client, indexing]);
  return <HarnessPage surface="skills" {...SKILLS_CONTENT} settings={settings}
    efficacyLead={<SkillsLead summary={summary.data} />}
    onDraft={(changes, draft) => saveSelectionToDraft(draft, skillSelectionChanges(changes))} />;
}
