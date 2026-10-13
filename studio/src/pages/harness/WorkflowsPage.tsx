import { HarnessPage } from "../../harness/HarnessPage";
import { saveLibrarySwitches, useLibrarySettings } from "./librarySettings";
import { WORKFLOW_FIELDS, WORKFLOW_METRICS, workflowSettings } from "./rolesWorkflowsModel";

export const WORKFLOWS_CONTENT = {
  title: "Workflows",
  question: "How many Workflow-tool runs there were, how many agents each started, and what they cost.",
  itemNoun: "Workflow",
  metrics: WORKFLOW_METRICS,
  fields: WORKFLOW_FIELDS,
};

export function WorkflowsPage() {
  const settings = useLibrarySettings(workflowSettings);
  return <HarnessPage surface="workflows" {...WORKFLOWS_CONTENT} settings={settings}
    onDraft={(changes, draft) => saveLibrarySwitches("workflows", changes, draft)} />;
}
