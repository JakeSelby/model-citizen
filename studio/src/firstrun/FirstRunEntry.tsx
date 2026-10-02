import { Alert, Button } from "@mantine/core";
import { useEffect, useState } from "react";
import { Navigate, NavLink } from "react-router-dom";

import { loadFirstRun } from "./api";
import { entryCopy, FIRST_RUN_DRAFT, opensGuide, type FirstRunStatus } from "./model";

// Once per page load: leaving the guide for the Hub must not send the user straight back.
let guideOpened = false;

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

/** The Hub's way into setup: a fresh install opens the guide, any unfinished run offers it. */
export function FirstRunEntry() {
  const [status, setStatus] = useState<FirstRunStatus | null>(null);
  useEffect(() => {
    let active = true;
    loadFirstRun(FIRST_RUN_DRAFT).then((next) => { if (active) setStatus(next); }).catch(() => undefined);
    return () => { active = false; };
  }, []);
  if (!status) return null;
  if (opensGuide(status, guideOpened)) {
    guideOpened = true;
    return <Navigate replace to="/setup" />;
  }
  return <FirstRunBanner status={status} />;
}
