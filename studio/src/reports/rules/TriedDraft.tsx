import { Anchor, Paper, Stack, Text, Title } from "@mantine/core";
import { NavLink } from "react-router-dom";

import { CommandChip, EvidenceState } from "../../components/StudioKit";
import { DraftTest } from "../../configure/DraftTest";
import type { TryWithoutResult } from "./model";

/** What "Try without it" leaves: the draft, its CLI steps and its test, ready to run. */
export function TriedDraft({ result }: { result: TryWithoutResult }) {
  const switched = result.status === "switched";
  return <Paper>
    <Stack gap="sm">
      <Title order={2}>Draft {result.draft.name}</Title>
      {switched
        ? <Text>{result.message} Test it against its base below, then decide in Changes whether to apply it.</Text>
        : <EvidenceState kind="refused" title={`Draft ${result.draft.name} was created, but the rule is still on in it`}>{result.message}</EvidenceState>}
      {result.commands.map((command, index) => <CommandChip command={command} key={command} label={`CLI step ${index + 1}`} />)}
      <Anchor component={NavLink} to="/changes#drafts">Open drafts in Changes</Anchor>
      {switched && <DraftTest draft={result.draft.name} revision={result.draft.revision} />}
    </Stack>
  </Paper>;
}
