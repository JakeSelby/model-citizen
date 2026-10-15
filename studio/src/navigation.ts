// The Studio's pages, grouped by the question each answers rather than by the harness part it reads.
export type NavItem = { readonly key: string; readonly label: string; readonly path: string };
export type NavGroup = { readonly label: string | null; readonly items: readonly NavItem[] };

export const NAVIGATION_GROUPS: readonly NavGroup[] = [
  { label: null, items: [
    { key: "home", label: "Home", path: "/home" },
    { key: "sessions", label: "Sessions", path: "/sessions" },
  ] },
  { label: "Harness", items: [
    { key: "rules", label: "Rules", path: "/harness/rules" },
    { key: "stances", label: "Stances", path: "/harness/stances" },
    { key: "skills", label: "Skills", path: "/harness/skills" },
    { key: "hooks", label: "Hooks", path: "/harness/hooks" },
    { key: "roles", label: "Roles", path: "/harness/roles" },
    { key: "workflows", label: "Workflows", path: "/harness/workflows" },
    { key: "sources", label: "Sources", path: "/harness/sources" },
  ] },
  { label: "Operate", items: [
    { key: "routines", label: "Routines", path: "/routines" },
    { key: "changes", label: "Changes", path: "/changes" },
  ] },
  { label: "Measure", items: [
    { key: "evals", label: "Evals", path: "/evals" },
  ] },
];

// Pinned to the foot of the nav, apart from the groups.
export const FOOT_NAVIGATION: readonly NavItem[] = [
  { key: "baseline", label: "Baseline", path: "/baseline" },
];

export const NAVIGATION: readonly NavItem[] = [
  ...NAVIGATION_GROUPS.flatMap((group) => group.items),
  ...FOOT_NAVIGATION,
];

export const HOME_PATH = "/home";

const matches = (pathname: string, path: string) => pathname === path || pathname.startsWith(`${path}/`);

export function pageTitle(pathname: string): string {
  return NAVIGATION.find((item) => matches(pathname, item.path))?.label ?? "Home";
}

export function documentTitle(pathname: string): string {
  return `${pageTitle(pathname)} · Model Citizen Studio`;
}

// The CLI command each page's evidence comes from, shown in the header beside the page title.
// Longer paths come first, so a sub-page keeps its own command.
const COMMANDS: ReadonlyArray<readonly [string, string]> = [
  ["/home", "citizen home"],
  ["/sessions", "citizen sessions list"],
  ["/harness/rules", "citizen usage --rules"],
  ["/harness/stances", "citizen stances"],
  ["/harness/skills", "citizen harness skills"],
  ["/harness/hooks", "citizen activity"],
  ["/harness/roles", "citizen usage --by role"],
  ["/harness/workflows", "citizen usage --by role"],
  ["/harness/sources", "citizen catalog --library"],
  ["/changes", "citizen draft list"],
  ["/evals/trends", "citizen reports trends"],
  ["/evals", "citizen runs history"],
  ["/baseline", "citizen baseline show"],
  ["/routines", "citizen studio routine list"],
];

export function pageCommand(pathname: string): string {
  return COMMANDS.find(([path]) => matches(pathname, path))?.[1] ?? "citizen doctor";
}

// Every route the Studio served before the regrouping, and where it lands now. One table, so the
// retirement story removes the old routes in one place. A `:param` segment carries over.
export const LEGACY_REDIRECTS: ReadonlyArray<readonly [string, string]> = [
  ["/", HOME_PATH],
  ["/setup", "/baseline"],
  ["/configure", "/changes"],
  ["/library", "/harness/sources/modules"],
  ["/experiments", "/evals"],
  ["/experiments/runs/:runId", "/evals/runs/:runId"],
  ["/activity", "/harness/hooks"],
  ["/reports", "/evals"],
  ["/reports/rules", "/harness/rules"],
  ["/reports/usage", "/sessions"],
  ["/reports/trends", "/evals/trends"],
];

// The new path for an old one, keeping its parameters; null when the path was never an old route.
export function legacyTarget(pathname: string): string | null {
  const segments = pathname.split("/");
  for (const [from, to] of LEGACY_REDIRECTS) {
    const pattern = from.split("/");
    if (pattern.length !== segments.length) continue;
    const params: Record<string, string> = {};
    const hit = pattern.every((part, index) => {
      if (part.startsWith(":")) {
        if (!segments[index]) return false;
        params[part] = segments[index];
        return true;
      }
      return part === segments[index];
    });
    if (hit) return to.split("/").map((part) => params[part] ?? part).join("/");
  }
  return null;
}

// Where an old or unknown location lands: its new path, with the query and fragment carried over so
// a deep link such as `/library?path=<module>` still opens the module.
// The old Activity page lives on as the Hooks page's safety log tab.
const LEGACY_TABS: Record<string, string> = { "/activity": "log" };

export function legacyLocation({ pathname, search, hash }: { pathname: string; search: string; hash: string }) {
  const tab = LEGACY_TABS[pathname];
  if (tab) {
    const params = new URLSearchParams(search);
    if (!params.has("tab")) params.set("tab", tab);
    search = `?${params.toString()}`;
  }
  return { pathname: legacyTarget(pathname) ?? HOME_PATH, search, hash };
}
