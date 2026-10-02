import { Alert, Button } from "@mantine/core";
import { useEffect, useState } from "react";
import { Navigate, NavLink } from "react-router-dom";

import { loadFirstRun } from "./api";
import { claimGuideOpen, entryCopy, FIRST_RUN_DRAFT, type FirstRunStatus } from "./model";

export function FirstRunBanner({ status }: { status: FirstRunStatus }) {
  const copy = entryCopy(status);
  if (!copy) return null;
  return (
    <Alert className="first-run-banner" color={status.state === "interrupted" ? "yellow" : "blue"} title={copy.title}>
      {copy.body}{" "}
      <Button component={NavLink} mt="sm" size="compact-md" to="/setup">{copy.action}</Button>
    </Alert>
  );
}

export function FirstRunEntryView({ status, opened }: { status: FirstRunStatus; opened: boolean }) {
  return opened ? <Navigate replace to="/setup" /> : <FirstRunBanner status={status} />;
}

/** The Hub's way into setup: a fresh install opens the guide, any unfinished run offers it. */
export function FirstRunEntry() {
  const [entry, setEntry] = useState<{ status: FirstRunStatus; opened: boolean } | null>(null);
  useEffect(() => {
    let active = true;
    loadFirstRun(FIRST_RUN_DRAFT).then((next) => {
      // Claimed here, once, never during render: a repeated render must not re-decide it.
      if (active) setEntry({ status: next, opened: claimGuideOpen(next) });
    }).catch(() => undefined);
    return () => { active = false; };
  }, []);
  if (!entry) return null;
  return <FirstRunEntryView opened={entry.opened} status={entry.status} />;
}
