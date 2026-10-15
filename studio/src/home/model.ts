// Home's display rules: at most five queue items, figures in plain words, severity as a badge.
import type { AttentionItem, ReadyDigest, Severity } from "./api";

export const QUEUE_LIMIT = 5;

export const SEVERITY_LABELS: Record<Severity, string> = { high: "Act now", medium: "Worth a look", low: "When you can" };
export const SEVERITY_COLORS: Record<Severity, string> = { high: "red", medium: "yellow", low: "gray" };

export const SOURCE_LABELS: Record<string, string> = {
  context: "Context", drafts: "Drafts", drift: "Drift", hooks: "Hooks", rules: "Rules",
  routines: "Routines", evals: "Evals",
};

/** The queue as shown: never more than five, whatever the server sent. */
export function shownItems(items: AttentionItem[]): AttentionItem[] {
  return items.slice(0, QUEUE_LIMIT);
}

/** Tokens as "256k" above a thousand, else the count. */
export function tokens(value: number | null): string {
  if (value === null) return "none";
  return value >= 1000 ? `${Math.round(value / 1000)}k` : String(value);
}

export function money(value: number | null): string {
  return value === null ? "unpriced" : `$${value.toFixed(2)}`;
}

/** The digest's figures, in the order the strip shows them. */
export function digestFigures(digest: ReadyDigest): { label: string; value: string; detail: string; href: string; over?: boolean }[] {
  return [
    { label: "Sessions", value: String(digest.sessions), detail: `${digest.calls} model calls`, href: "/sessions" },
    {
      label: "Spend", value: money(digest.cost),
      detail: digest.cost === null ? "no call could be priced" : digest.cost_complete ? "every call priced" : "some calls unpriced",
      href: "/sessions",
    },
    {
      label: "Median context per call", value: tokens(digest.median_context),
      detail: `Your cost stance's line is ${tokens(digest.context_warn)}`, href: "/sessions",
      over: digest.median_context !== null && digest.median_context > digest.context_warn,
    },
    {
      label: `Calls past ${tokens(digest.context_limit)}`, value: String(digest.calls_over_limit),
      detail: `${digest.sessions_over_limit} of ${digest.sessions} sessions`, href: "/sessions",
      over: digest.calls_over_limit > 0,
    },
  ];
}

/** Sources that could not be read, named for the line under the queue. */
export function failedSources(sources: Record<string, string>): string[] {
  return Object.entries(sources).filter(([, state]) => state !== "ok")
    .map(([name]) => SOURCE_LABELS[name] ?? name).sort();
}
