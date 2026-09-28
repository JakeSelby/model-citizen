import {
  Anchor,
  Badge,
  Button,
  Container,
  Group,
  Paper,
  Stack,
  Text,
  Title,
  NativeSelect,
  useMantineColorScheme,
} from "@mantine/core";
import { type ChangeEvent, type MouseEvent, useEffect, useState } from "react";
import { Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";

import { documentTitle, NAVIGATION, pageTitle } from "./navigation";
import { ConfigurePage } from "./configure/ConfigurePage";
import { ToastProvider } from "./components/StudioKit";
import { LibraryPage } from "./library/LibraryPage";
import { OverviewPage } from "./overview/OverviewPage";
import { ExperimentsPage } from "./experiments/ExperimentsPage";
import { version } from "../package.json";
import { loadColorScheme, saveColorScheme, type ColorScheme } from "./preferences";
import { LiveUpdateControls, LiveUpdatesProvider } from "./live/LiveUpdates";

function FoundationPage({ title, description }: { title: string; description: string }) {
  return (
    <Stack gap="lg">
      <div>
        <Text className="eyebrow">Studio / {title}</Text>
        <Title order={1}>{title}</Title>
        <Text c="dimmed" mt="xs">{description}</Text>
      </div>
      <Paper className="foundation-panel" px={{ base: "var(--studio-space-5)", xs: "xl" }} py="xl" withBorder>
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
  reports: "Trace health, performance, efficacy, and usage back to immutable evidence.",
  activity: "Review what the harness decided and changed without rewriting its history.",
} as const;

function StudioFrame() {
  const location = useLocation();
  const title = pageTitle(location.pathname);
  const { colorScheme, setColorScheme } = useMantineColorScheme();
  const [themeReady, setThemeReady] = useState(false);

  function skipNavigation(event: MouseEvent<HTMLAnchorElement>) {
    event.preventDefault();
    const main = document.getElementById("main-content");
    main?.focus({ preventScroll: true });
    main?.scrollIntoView({ block: "start" });
  }

  useEffect(() => {
    document.title = documentTitle(location.pathname);
  }, [location.pathname]);

  useEffect(() => {
    let active = true;
    void loadColorScheme()
      .then((saved) => { if (active) setColorScheme(saved); })
      .catch(() => undefined)
      .finally(() => { if (active) setThemeReady(true); });
    return () => { active = false; };
  }, [setColorScheme]);

  function changeTheme(event: ChangeEvent<HTMLSelectElement>) {
    const selected = event.currentTarget.value as ColorScheme;
    setColorScheme(selected);
    void saveColorScheme(selected).catch(() => undefined);
  }

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
          <div className="workspace-tools">
            <Text className="version-label" size="xs">Studio {version}</Text>
            <LiveUpdateControls />
            <NativeSelect aria-label="Color theme" className="theme-picker" disabled={!themeReady} value={colorScheme} onChange={changeTheme} data={[{ value: "auto", label: "System theme" }, { value: "light", label: "Light theme" }, { value: "dark", label: "Dark theme" }]} />
            <Button component={NavLink} size="compact-md" to="/configure#drafts" variant="default">Drafts</Button>
          </div>
        </Container>
      </div>
      <Container component="main" id="main-content" className="main-content" size="xl" tabIndex={-1}>
        <Text className="visually-hidden" component="span">Current page: {title}</Text>
        <Routes>
          <Route path="/" element={<OverviewPage />} />
          <Route path="/configure" element={<ConfigurePage />} />
          <Route path="/library" element={<LibraryPage />} />
          <Route path="/experiments" element={<ExperimentsPage />} />
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
      <Container component="footer" className="studio-footer" size="xl"><Text size="xs">Local workspace · Evidence stays linked to its source</Text><Text size="xs">Model Citizen Studio {version}</Text></Container>
    </div>
  );
}

export function StudioApp() {
  return <LiveUpdatesProvider><ToastProvider><StudioFrame /></ToastProvider></LiveUpdatesProvider>;
}
