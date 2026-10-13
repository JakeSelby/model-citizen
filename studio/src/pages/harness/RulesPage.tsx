import { Stack, Text } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { usePeriods } from "../../harness/api";
import { HarnessPage, type SettingsState } from "../../harness/HarnessPage";
import type { DraftChange, PeriodRow } from "../../harness/model";
import { loadRuleHealth, tryWithout } from "../../reports/rules/api";
import type { RuleHealth, TryWithoutResult } from "../../reports/rules/model";
import { TriedDraft } from "../../reports/rules/RuleHealthPage";
import { loadSelection } from "../../selection/api";
import type { SelectionReport } from "../../selection/model";
import { draftRuleChanges, HITS_NOTE, RULE_FIELDS, RULE_METRICS, RULES_SURFACE, ruleSettingRows } from "./rulesModel";

export const ruleHealthQuery = {
  queryKey: ["rule-health"] as const,
  queryFn: () => loadRuleHealth(),
  staleTime: 30_000,
};

/** The selection in force, read from the directory the Studio was started in. */
export const ruleSelectionQuery = {
  queryKey: ["selection", "", ""] as const,
  queryFn: () => loadSelection("", ""),
  staleTime: 30_000,
};

type Read<T> = { isPending: boolean; isError: boolean; error: Error | null; data: T | undefined };

/** Settings is ready once both the selection and the rule report are read; either error shows. */
export function ruleSettingsState(selection: Read<SelectionReport>, health: Read<RuleHealth>, periods: PeriodRow[] = []): SettingsState {
  for (const read of [selection, health]) {
    if (read.isError) return { state: "error", message: read.error?.message ?? "The request failed." };
  }
  if (!selection.data || !health.data) return { state: "loading" };
  return { state: "ready", rows: ruleSettingRows(selection.data, health.data, periods) };
}

/** The drafts a Settings save made, each with its CLI steps, a link to Changes and its test. */
export function TriedDrafts({ results }: { results: TryWithoutResult[] }) {
  if (!results.length) return null;
  return <Stack gap="md">
    <Text aria-live="polite" role="status" size="sm">
      {results.length === 1 ? "1 draft made" : `${results.length} drafts made`}; nothing live changed.
    </Text>
    {results.map((result) => <TriedDraft key={result.draft.name} result={result} />)}
  </Stack>;
}

export function RulesPage() {
  const health = useQuery(ruleHealthQuery);
  const selection = useQuery(ruleSelectionQuery);
  const periods = usePeriods(RULES_SURFACE);
  const [tried, setTried] = useState<TryWithoutResult[]>([]);

  async function onDraft(changes: DraftChange[]) {
    const { results, error } = await draftRuleChanges(changes, tryWithout);
    setTried(results);
    if (error) throw new Error(error);
  }

  return <Stack gap="lg">
    <HarnessPage surface={RULES_SURFACE} title="Rules"
      question="Guidance the model reads every session, measured against the setting that was in effect."
      itemNoun="Rule" metrics={RULE_METRICS} fields={RULE_FIELDS}
      settings={ruleSettingsState(selection, health, periods.data?.rows)} onDraft={onDraft} />
    <TriedDrafts results={tried} />
    <Text size="sm" c="dimmed">{HITS_NOTE}</Text>
  </Stack>;
}
