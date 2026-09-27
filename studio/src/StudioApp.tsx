import {
  Anchor,
  Badge,
  Button,
  Card,
  Container,
  Group,
  Paper,
  SimpleGrid,
  Stack,
  Text,
  ThemeIcon,
  Title,
} from "@mantine/core";
import { type MouseEvent, useEffect } from "react";
import { Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";

import { documentTitle, NAVIGATION, pageTitle } from "./navigation";
import { ConfigurePage } from "./configure/ConfigurePage";

const reportCards = [
  {
    label: "System health",
    measure: "Awaiting evidence",
    detail: "Diagnostics will appear after the first local snapshot.",
    href: "/reports#system-health",
  },
  {
    label: "Hook performance",
    measure: "No timing window",
    detail: "Invocation counts and latency stay linked to their source window.",
    href: "/reports#hook-performance",
  },
  {
    label: "Efficacy",
    measure: "No comparison yet",
    detail: "Run a paired experiment before judging a draft.",
    href: "/reports#efficacy",
  },
  {
    label: "Usage",
    measure: "No local total",
    detail: "Estimated and unpriced usage are reported separately.",
    href: "/reports#usage",
  },
] as const;

function ReportCard({ label, measure, detail, href }: (typeof reportCards)[number]) {
  return (
    <Card className="report-card" component={NavLink} to={href} withBorder>
      <Text className="eyebrow">{label}</Text>
      <Text className="report-measure" fw={650}>{measure}</Text>
      <Text c="dimmed" size="sm">{detail}</Text>
      <Text className="card-link" fw={600} size="sm">Open report <span aria-hidden="true">↗</span></Text>
    </Card>
  );
}

function Hub() {
  return (
    <Stack gap="xl">
      <Group align="flex-end" className="page-heading" justify="space-between">
        <div>
          <Text className="eyebrow">Studio / Hub</Text>
          <Title order={1}>Your harness at a glance.</Title>
          <Text c="dimmed" mt="xs">Local evidence, current work, and the next useful action.</Text>
        </div>
        <Button component={NavLink} to="/experiments" variant="filled">Open experiments</Button>
      </Group>

      <section aria-labelledby="overview-title" className="hub-grid">
        <Paper className="overview-card" p="xl" withBorder>
          <Group justify="space-between">
            <Title id="overview-title" order={2}>AI health overview</Title>
            <Badge color="gray" variant="light">Off</Badge>
          </Group>
          <Text className="overview-lead" mt="lg">Enable a read-only assessment when you want one.</Text>
          <Text c="dimmed" mt="sm">
            Studio makes no model calls until you opt in. The provider, model, evidence scope,
            refresh policy, and daily cap stay visible with every assessment.
          </Text>
          <Group mt="xl">
            <Button component={NavLink} to="/configure#ai-overview" variant="light">Review AI settings</Button>
            <Anchor component={NavLink} to="/reports">Browse deterministic reports</Anchor>
          </Group>
        </Paper>
        <SimpleGrid className="report-grid" cols={{ base: 1, xs: 2 }} spacing="md">
          {reportCards.map((card) => <ReportCard key={card.label} {...card} />)}
        </SimpleGrid>
      </section>

      <SimpleGrid cols={{ base: 1, md: 2 }} spacing="lg">
        <Paper p="xl" withBorder>
          <Group justify="space-between">
            <Title order={2}>Drafts & active runs</Title>
            <Anchor component={NavLink} to="/experiments">All experiments</Anchor>
          </Group>
          <div className="empty-state">
            <ThemeIcon color="gray" size="lg" variant="light" aria-hidden="true">–</ThemeIcon>
            <div>
              <Text fw={650}>Evidence not loaded</Text>
              <Text c="dimmed" size="sm">Draft and run state will appear when the core endpoint is available.</Text>
            </div>
          </div>
        </Paper>
        <Paper p="xl" withBorder>
          <Group justify="space-between">
            <Title order={2}>Alerts & notifications</Title>
            <Badge color="gray" variant="light">Unavailable</Badge>
          </Group>
          <div className="empty-state">
            <ThemeIcon color="gray" size="lg" variant="light" aria-hidden="true">–</ThemeIcon>
            <div>
              <Text fw={650}>Evidence not loaded</Text>
              <Text c="dimmed" size="sm">No health claim is shown until authoritative alerts are available.</Text>
            </div>
          </div>
        </Paper>
      </SimpleGrid>
    </Stack>
  );
}

function FoundationPage({ title, description }: { title: string; description: string }) {
  return (
    <Stack gap="lg">
      <div>
        <Text className="eyebrow">Studio / {title}</Text>
        <Title order={1}>{title}</Title>
        <Text c="dimmed" mt="xs">{description}</Text>
      </div>
      <Paper className="foundation-panel" p="xl" withBorder>
        <Badge color="gray" variant="light">Preview</Badge>
        <Title mt="md" order={2}>The interface is available.</Title>
        <Text c="dimmed" mt="xs">
          This route is part of the committed Studio bundle. Operational evidence remains unavailable
          until its versioned core endpoint is implemented.
        </Text>
      </Paper>
    </Stack>
  );
}

const pages = {
  configure: "Inspect the effective selection and make changes inside a named draft.",
  experiments: "Choose an allowlisted suite, a target, and a bounded run.",
  reports: "Trace health, performance, efficacy, and usage back to immutable evidence.",
  activity: "Review what the harness decided and changed without rewriting its history.",
} as const;

function StudioFrame() {
  const location = useLocation();
  const title = pageTitle(location.pathname);

  function skipNavigation(event: MouseEvent<HTMLAnchorElement>) {
    event.preventDefault();
    const main = document.getElementById("main-content");
    main?.focus({ preventScroll: true });
    main?.scrollIntoView({ block: "start" });
  }

  useEffect(() => {
    document.title = documentTitle(location.pathname);
  }, [location.pathname]);

  return (
    <div className="studio-frame">
      <a className="skip-link" href="#main-content" onClick={skipNavigation}>Skip navigation</a>
      <header className="studio-header">
        <Container className="header-inner" size="xl">
          <Anchor className="brand" component={NavLink} to="/" underline="never">
            <span className="brand-mark" aria-hidden="true">mc</span>
            <span>Model Citizen <small>Studio</small></span>
          </Anchor>
          <nav aria-label="Studio">
            <Group className="primary-navigation" gap="xs">
              {NAVIGATION.map((item) => (
                <Anchor
                  aria-label={item.label}
                  className="nav-link"
                  component={NavLink}
                  end={item.path === "/"}
                  key={item.path}
                  to={item.path}
                  underline="never"
                >
                  {item.label}
                </Anchor>
              ))}
            </Group>
          </nav>
        </Container>
      </header>
      <div className="workspace-bar">
        <Container className="workspace-inner" size="xl">
          <Text fw={600}>Personal workspace <Text c="dimmed" component="span" fw={400}>/ local</Text></Text>
          <Button component={NavLink} size="compact-md" to="/configure#drafts" variant="default">Drafts</Button>
        </Container>
      </div>
      <Container component="main" id="main-content" py={{ base: "xl", sm: 40 }} size="xl" tabIndex={-1}>
        <Text className="visually-hidden" component="span">Current page: {title}</Text>
        <Routes>
          <Route path="/" element={<Hub />} />
          <Route path="/configure" element={<ConfigurePage />} />
          {Object.entries(pages).filter(([path]) => path !== "configure").map(([path, description]) => (
            <Route
              element={<FoundationPage description={description} title={pageTitle(`/${path}`)} />}
              key={path}
              path={`/${path}`}
            />
          ))}
          <Route path="*" element={<Navigate replace to="/" />} />
        </Routes>
      </Container>
    </div>
  );
}

export function StudioApp() {
  return <StudioFrame />;
}
