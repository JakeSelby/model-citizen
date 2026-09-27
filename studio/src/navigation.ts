export const NAVIGATION = [
  { label: "Hub", path: "/" },
  { label: "Configure", path: "/configure" },
  { label: "Experiments", path: "/experiments" },
  { label: "Reports", path: "/reports" },
  { label: "Activity", path: "/activity" },
] as const;

export function pageTitle(pathname: string): string {
  return NAVIGATION.find((item) => item.path === pathname)?.label ?? "Hub";
}

export function documentTitle(pathname: string): string {
  return `${pageTitle(pathname)} · Model Citizen Studio`;
}
