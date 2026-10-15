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

// Old top-level paths still found in bookmarks and evidence links, and the page each now lives on.
// The query and fragment carry over, so `/library?path=<module>` still opens that module.
export const OLD_PATHS: Readonly<Record<string, string>> = {
  "/": HOME_PATH,
  "/setup": "/baseline",
  "/configure": "/changes",
  "/library": "/harness/sources/modules",
  "/experiments": "/evals",
  "/activity": "/harness/hooks",
  "/reports": "/evals",
  "/reports/rules": "/harness/rules",
  "/reports/usage": "/sessions",
  "/reports/trends": "/evals/trends",
};

// The old Activity page lives on as the Hooks page's safety log tab.
const OLD_TABS: Readonly<Record<string, string>> = { "/activity": "log" };

// Where an old or unknown location lands: its new page, or Home.
export function fallbackLocation({ pathname, search, hash }: { pathname: string; search: string; hash: string }) {
  const tab = OLD_TABS[pathname];
  if (tab) {
    const params = new URLSearchParams(search);
    if (!params.has("tab")) params.set("tab", tab);
    search = `?${params.toString()}`;
  }
  return { pathname: Object.hasOwn(OLD_PATHS, pathname) ? OLD_PATHS[pathname] : HOME_PATH, search, hash };
}
