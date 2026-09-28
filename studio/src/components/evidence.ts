export type EvidenceTone = "neutral" | "success" | "warning" | "danger" | "info";

export function intervalSummary(estimate: number, low: number, high: number, unit: string, confidence = 95): string {
  if (![estimate, low, high, confidence].every(Number.isFinite) || low > high || estimate < low || estimate > high || confidence <= 0 || confidence >= 100) {
    return "Interval unavailable";
  }
  const format = (value: number) => `${value > 0 ? "+" : ""}${value.toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
  return `${format(estimate)} ${unit}; ${confidence}% interval ${format(low)} to ${format(high)}${low <= 0 && high >= 0 ? "; inconclusive, interval crosses zero" : ""}`;
}

export type DiffLine = { kind: "context" | "added" | "removed"; text: string };

export function compareLines(before: string, after: string): DiffLine[] {
  if (before === after) return before.split("\n").map((text) => ({ kind: "context", text }));
  const original = before.split("\n");
  const next = after.split("\n");
  let prefix = 0;
  while (prefix < original.length && prefix < next.length && original[prefix] === next[prefix]) prefix++;
  let suffix = 0;
  while (suffix < original.length - prefix && suffix < next.length - prefix && original[original.length - 1 - suffix] === next[next.length - 1 - suffix]) suffix++;
  return [
    ...original.slice(0, prefix).map((text): DiffLine => ({ kind: "context", text })),
    ...original.slice(prefix, original.length - suffix).map((text): DiffLine => ({ kind: "removed", text })),
    ...next.slice(prefix, next.length - suffix).map((text): DiffLine => ({ kind: "added", text })),
    ...next.slice(next.length - suffix).map((text): DiffLine => ({ kind: "context", text })),
  ];
}
