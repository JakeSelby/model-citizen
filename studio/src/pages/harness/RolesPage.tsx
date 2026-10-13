import { HarnessPage } from "../../harness/HarnessPage";
import { useDraftHandoff, useLibrarySettings } from "./librarySettings";
import { ROLE_FIELDS, ROLE_METRICS, roleSettings } from "./rolesWorkflowsModel";

export const ROLES_CONTENT = {
  title: "Roles",
  question: "Which roles spawned, at what tier and effort, what they cost, and how often they ran past their budget.",
  itemNoun: "Role",
  metrics: ROLE_METRICS,
  fields: ROLE_FIELDS,
};

export function RolesPage() {
  const settings = useLibrarySettings(roleSettings);
  const onDraft = useDraftHandoff("roles");
  return <HarnessPage surface="roles" {...ROLES_CONTENT} settings={settings} onDraft={onDraft} />;
}
