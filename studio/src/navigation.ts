export const NAVIGATION = [
  { label: "Hub", path: "/" },
  { label: "Configure", path: "/configure" },
  { label: "Library", path: "/library" },
  { label: "Experiments", path: "/experiments" },
  { label: "Reports", path: "/reports" },
  { label: "Activity", path: "/activity" },
] as const;

export function pageTitle(pathname: string): string {
  if (pathname === "/setup") return "First run";
  return NAVIGATION.find((item) => item.path === pathname
    || (item.path !== "/" && pathname.startsWith(`${item.path}/`)))?.label ?? "Hub";
}

export function documentTitle(pathname: string): string {
  return `${pageTitle(pathname)} · Model Citizen Studio`;
}

// The CLI command each page's evidence comes from, shown in the header beside the page title.
const COMMANDS: ReadonlyArray<readonly [string, string]> = [
  ["/setup", "citizen draft first-run"],
  ["/configure", "citizen selection"],
  ["/library", "citizen catalog --library"],
  ["/experiments", "citizen runs history"],
  ["/activity", "citizen activity"],
  ["/reports/rules", "citizen usage --rules"],
  ["/reports/usage", "citizen usage"],
  ["/reports", "citizen reports trends"],
];

export function pageCommand(pathname: string): string {
  return COMMANDS.find(([path]) => pathname === path || pathname.startsWith(`${path}/`))?.[1] ?? "citizen doctor";
}
