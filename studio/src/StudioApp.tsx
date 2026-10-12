import {
  AppShell,
  Button,
  Text,
  NativeSelect,
  useMantineColorScheme,
} from "@mantine/core";
import { type ChangeEvent, type MouseEvent, useEffect, useState } from "react";
import { NavLink, Route, Routes, useLocation } from "react-router-dom";

import { documentTitle, pageCommand, pageTitle } from "./navigation";
import { ConfigurePage } from "./configure/ConfigurePage";
import { ToastProvider } from "./components/StudioKit";
import { LibraryPage } from "./library/LibraryPage";
import { OverviewPage } from "./overview/OverviewPage";
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
import { EvalsPage } from "./shell/EvalsPage";
import { LegacyRedirect } from "./shell/LegacyRedirect";
import { StudioNav } from "./shell/StudioNav";
import { RebuildingPage } from "./shell/RebuildingPage";

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
        <StudioNav />
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
          <Button component={NavLink} size="compact-sm" to="/changes#drafts" variant="default">Drafts</Button>
        </div>
      </AppShell.Header>
      <AppShell.Main className="main-shell">
        <div className="main-content" id="main-content" tabIndex={-1}>
          <Text className="visually-hidden" component="span">Current page: {title}</Text>
          <Routes>
            <Route path="/home" element={<><FirstRunEntry /><OverviewPage /></>} />
            <Route path="/sessions" element={<SpendPage />} />
            <Route path="/harness/rules" element={<RuleHealthPage />} />
            {/* Each placeholder is keyed, so moving between two of them mounts a new page. */}
            <Route path="/harness/stances" element={<RebuildingPage key="stances" page="stances" />} />
            <Route path="/harness/skills" element={<RebuildingPage key="skills" page="skills" />} />
            <Route path="/harness/hooks" element={<ActivityPage />} />
            <Route path="/harness/roles" element={<RebuildingPage key="roles" page="roles" />} />
            <Route path="/harness/workflows" element={<RebuildingPage key="workflows" page="workflows" />} />
            <Route path="/harness/sources" element={<LibraryPage />} />
            <Route path="/routines" element={<RebuildingPage key="routines" page="routines" />} />
            <Route path="/changes" element={<ConfigurePage />} />
            <Route path="/evals" element={<EvalsPage />} />
            <Route path="/evals/runs/:runId" element={<RunDetailPage />} />
            <Route path="/evals/trends" element={<TrendsPage />} />
            <Route path="/baseline" element={<FirstRunPage />} />
            {/* Every old route and any unknown one: see LEGACY_REDIRECTS in navigation.ts. */}
            <Route path="*" element={<LegacyRedirect />} />
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
