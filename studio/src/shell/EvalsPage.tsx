import { Anchor, Text } from "@mantine/core";
import { NavLink } from "react-router-dom";

import { ExperimentsPage } from "../experiments/ExperimentsPage";

/** Evals until its own page lands: the experiments launcher and history, with trends one link away. */
export function EvalsPage() {
  return (
    <>
      <Text className="evals-views" size="sm">
        <Anchor component={NavLink} to="/evals/trends">Trends by version and date</Anchor>
      </Text>
      <ExperimentsPage />
    </>
  );
}
