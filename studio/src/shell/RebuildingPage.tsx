import { Stack, Text, Title } from "@mantine/core";

// A page the regrouped nav names before its content lands; each names the story that builds it.
export const REBUILDING: Record<string, { title: string; question: string; issue: number }> = {
  stances: { title: "Stances", question: "Which variant of each stance was in effect, and what it did.", issue: 1325 },
  skills: { title: "Skills", question: "Which skills loaded each session, and which were actually invoked.", issue: 1330 },
  roles: { title: "Roles", question: "Which roles spawned, at what tier and effort, and what they cost.", issue: 1326 },
  workflows: { title: "Workflows", question: "Which workflows ran, and what each run cost.", issue: 1326 },
  routines: { title: "Routines", question: "What runs on a schedule, and how its last runs went.", issue: 1335 },
};

export function RebuildingPage({ page }: { page: keyof typeof REBUILDING }) {
  const { title, question, issue } = REBUILDING[page];
  return (
    <Stack className="rebuilding-page" gap="sm">
      <div className="page-heading">
        <Title order={1}>{title}</Title>
        <Text c="dimmed" mt="xs">{question}</Text>
      </div>
      <Text>This page is being rebuilt in story #{issue}.</Text>
    </Stack>
  );
}
