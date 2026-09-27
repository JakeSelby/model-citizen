import { useEffect, useRef } from "react";
import {
  Alert,
  AppShell,
  Badge,
  Button,
  Card,
  Group,
  NavLink as MantineNavLink,
  SimpleGrid,
  Stack,
  Text,
  Title,
} from "@mantine/core";
import { useDisclosure, useMediaQuery } from "@mantine/hooks";
import { useQuery } from "@tanstack/react-query";
import { json } from "@codemirror/lang-json";
import { EditorState } from "@codemirror/state";
import { EditorView, keymap } from "@codemirror/view";
import { NavLink, Route, Routes } from "react-router-dom";
import {
  CategoryScale,
  Chart as ChartJS,
  Filler,
  Legend,
  LinearScale,
  LineElement,
  PointElement,
  Tooltip,
} from "chart.js";
import { Line } from "react-chartjs-2";

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Filler, Legend, Tooltip);

const performance = {
  labels: ["R-41", "R-42", "R-43", "R-44"],
  datasets: [{
    label: "Pass rate",
    data: [82, 86, 84, 91],
    borderColor: "#087f5b",
    backgroundColor: "#c3fae8",
    fill: true,
    tension: 0.3,
  }],
};

function DraftEditor() {
  const host = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!host.current) return;
    const state = EditorState.create({
      doc: '{\n  "draft": "studio-bundle-spike",\n  "status": "review"\n}',
      extensions: [json(), keymap.of([]), EditorView.lineWrapping],
    });
    const view = new EditorView({ state, parent: host.current });
    return () => view.destroy();
  }, []);

  return <div aria-label="Draft JSON" className="editor" ref={host} />;
}

function Hub() {
  const [opened, { toggle }] = useDisclosure(false);
  const compact = useMediaQuery("(max-width: 48rem)");
  const health = useQuery({
    queryKey: ["health-summary"],
    queryFn: async () => ({ passing: 91, alerts: 2, trend: "+7 points" }),
  });

  return (
    <Stack gap="lg">
      <div>
        <Badge color="teal" variant="light">Operational</Badge>
        <Title order={1}>Harness briefing</Title>
        <Text c="dimmed">A deterministic fixture that exercises the candidate Studio stack.</Text>
      </div>
      <Alert color="yellow" title="Two hooks need investigation">
        Failure volume rose after the latest qualification run.
      </Alert>
      <SimpleGrid cols={{ base: 1, sm: 3 }}>
        <Card withBorder><Text size="sm">Pass rate</Text><Title order={2}>{health.data?.passing ?? "—"}%</Title></Card>
        <Card withBorder><Text size="sm">Open alerts</Text><Title order={2}>{health.data?.alerts ?? "—"}</Title></Card>
        <Card withBorder><Text size="sm">Recent trend</Text><Title order={2}>{health.data?.trend ?? "—"}</Title></Card>
      </SimpleGrid>
      <Card withBorder>
        <Group justify="space-between" mb="md">
          <Title order={2}>Run efficacy</Title>
          <Button variant="light" onClick={toggle}>{opened ? "Hide" : "Show"} draft</Button>
        </Group>
        <div className="chart" aria-label="Run efficacy trend">
          <Line data={performance} options={{ maintainAspectRatio: false, scales: { y: { min: 0, max: 100 } } }} />
        </div>
      </Card>
      {opened && <Card withBorder><Text mb="sm">{compact ? "Compact draft" : "Draft configuration"}</Text><DraftEditor /></Card>}
    </Stack>
  );
}

function Placeholder({ title }: { title: string }) {
  return <Card withBorder><Title order={1}>{title}</Title><Text c="dimmed">Candidate route verified by the bundle spike.</Text></Card>;
}

export function StudioApp() {
  const links = ["Hub", "Configure", "Experiments", "Reports", "Activity"];
  return (
    <AppShell header={{ height: 68 }} padding="lg">
      <AppShell.Header>
        <Group h="100%" px="lg" justify="space-between">
          <Text fw={700}>Model Citizen Studio</Text>
          <Group gap="xs" component="nav" aria-label="Studio">
            {links.map((label) => (
              <MantineNavLink
                component={NavLink}
                key={label}
                label={label}
                to={label === "Hub" ? "/" : `/${label.toLowerCase()}`}
              />
            ))}
          </Group>
        </Group>
      </AppShell.Header>
      <AppShell.Main>
        <Routes>
          <Route path="/" element={<Hub />} />
          {links.slice(1).map((label) => <Route key={label} path={`/${label.toLowerCase()}`} element={<Placeholder title={label} />} />)}
        </Routes>
      </AppShell.Main>
    </AppShell>
  );
}
