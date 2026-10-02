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
