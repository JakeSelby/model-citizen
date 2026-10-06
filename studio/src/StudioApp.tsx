import {
  Anchor,
  AppShell,
  Badge,
  Button,
  Paper,
  Stack,
  Text,
  Title,
  NativeSelect,
  useMantineColorScheme,
} from "@mantine/core";
import { type ChangeEvent, type MouseEvent, useEffect, useState } from "react";
import { Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";

import { documentTitle, NAVIGATION, pageCommand, pageTitle } from "./navigation";
import { ConfigurePage } from "./configure/ConfigurePage";
import { ToastProvider } from "./components/StudioKit";
import { LibraryPage } from "./library/LibraryPage";
import { OverviewPage } from "./overview/OverviewPage";
import { ExperimentsPage } from "./experiments/ExperimentsPage";
import { RunDetailPage } from "./experiments/history/RunDetailPage";
import { ActivityPage } from "./activity/ActivityPage";
import { RuleHealthPage } from "./reports/rules/RuleHealthPage";
import { TrendsPage } from "./reports/trends/TrendsPage";
import { SpendPage } from "./spend/SpendPage";
import { FirstRunEntry } from "./firstrun/FirstRunEntry";
import { FirstRunPage } from "./firstrun/FirstRunPage";
import { version } from "../package.json";
import { loadColorScheme, saveColorScheme, type ColorScheme } from "./preferences";
import { LiveUpdateControls, LiveUpdatesProvider } from "./live/LiveUpdates";

function FoundationPage({ title, description }: { title: string; description: string }) {
  return (
    <Stack gap="lg">
      <div className="page-heading">
        <Text className="eyebrow">Studio / {title}</Text>
        <Title order={1}>{title}</Title>
        <Text c="dimmed" mt="xs">{description}</Text>
      </div>
      <Paper className="foundation-panel">
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
} as const;

function StudioFrame() {
  const location = useLocation();
  const title = pageTitle(location.pathname);
  const command = pageCommand(location.pathname);
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
    <AppShell
      className="studio-frame studio-shell"
      header={{ height: 52 }}
      layout="alt"
      mode="static"
      navbar={{ width: 220, breakpoint: "sm" }}
      padding={0}
    >
      <a className="skip-link" href="#main-content" onClick={skipNavigation}>Skip navigation</a>
      <AppShell.Navbar aria-label="Studio" className="studio-navbar">
        <Anchor className="brand" component={NavLink} to="/" underline="never">
          Model Citizen <small>Studio</small>
        </Anchor>
        <div className="primary-navigation">
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
        </div>
      </AppShell.Navbar>
      <AppShell.Header className="studio-header">
        <div className="header-title">
          <Text className="header-page" component="span">{title}</Text>
          <Text className="header-context" component="span">
            <span className="version-label">Studio {version}</span>
            <span aria-hidden="true"> · </span>
            <span>Personal workspace / local</span>
          </Text>
        </div>
        <div className="workspace-tools">
          <code className="page-command">{command}</code>
          <LiveUpdateControls />
          <NativeSelect aria-label="Color theme" className="theme-picker" size="xs" disabled={!themeReady} value={colorScheme} onChange={changeTheme} data={[{ value: "auto", label: "System theme" }, { value: "light", label: "Light theme" }, { value: "dark", label: "Dark theme" }]} />
          <Button component={NavLink} size="compact-sm" to="/configure#drafts" variant="default">Drafts</Button>
        </div>
      </AppShell.Header>
      <AppShell.Main className="main-shell">
        <div className="main-content" id="main-content" tabIndex={-1}>
          <Text className="visually-hidden" component="span">Current page: {title}</Text>
          <Routes>
            <Route path="/" element={<><FirstRunEntry /><OverviewPage /></>} />
            <Route path="/setup" element={<FirstRunPage />} />
            <Route path="/configure" element={<ConfigurePage />} />
            <Route path="/library" element={<LibraryPage />} />
            <Route path="/experiments" element={<ExperimentsPage />} />
            <Route path="/experiments/runs/:runId" element={<RunDetailPage />} />
            <Route path="/activity" element={<ActivityPage />} />
            <Route path="/reports/rules" element={<RuleHealthPage />} />
            <Route path="/reports/usage" element={<SpendPage />} />
            <Route path="/reports/trends" element={<TrendsPage />} />
            {Object.entries(pages).filter(([path]) => path !== "configure").map(([path, description]) => (
              <Route
                element={<FoundationPage description={description} title={pageTitle(`/${path}`)} />}
                key={path}
                path={`/${path}`}
              />
            ))}
            <Route path="*" element={<Navigate replace to="/" />} />
          </Routes>
        </div>
      </AppShell.Main>
      {/* Outside <main>, so the footer keeps its contentinfo landmark. */}
      <AppShell.Footer className="studio-footer"><Text size="xs">Local workspace · Evidence stays linked to its source</Text><Text size="xs">Model Citizen Studio {version}</Text></AppShell.Footer>
    </AppShell>
  );
}

export function StudioApp() {
  return <LiveUpdatesProvider><ToastProvider><StudioFrame /></ToastProvider></LiveUpdatesProvider>;
}
