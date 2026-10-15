// What `GET /api/baseline` (`citizen baseline show --json`) and `POST /api/baseline/record`
// (`citizen baseline record --json`) answer, and the pure helpers the Baseline page renders with.

export type BaselinePlugin = { id: string; label: string; path: string; modules: number | string };

export type BaselineSetting = { path: string; section: string; label: string; value: unknown };

export type BaselineCurrent = {
  config_present: boolean;
  config_path: string;
  stances: Record<string, string>;
  rules: Record<string, string>;
  settings: BaselineSetting[];
  plugins: BaselinePlugin[];
};

export type RecordedBaseline = {
  id: string;
  ts: string;
  label: string;
  stances: Record<string, string>;
  rules: Record<string, string>;
  plugins: string[];
};

export type BaselineResponse = {
  schema_version: number;
  current: BaselineCurrent;
  baselines: RecordedBaseline[];
  commands: { show: string; record: string };
};

export type RecordResponse = { schema_version: number; baseline: RecordedBaseline };

export const LABEL_MAX = 80;
export const GUIDE_PARAM = "guide";
export const FIRST_RUN_HREF = `/baseline?${GUIDE_PARAM}=first-run`;

/** Whether the URL opens the guided first run inside Baseline rather than Baseline itself. */
export function showsFirstRun(search: string | URLSearchParams): boolean {
  return new URLSearchParams(search).get(GUIDE_PARAM) === "first-run";
}

/** The label rule the record route enforces: printable text of at most `LABEL_MAX` characters. */
export function labelProblem(label: string): string {
  if (label.length > LABEL_MAX) return `Keep the label to ${LABEL_MAX} characters.`;
  if (/[\u0000-\u001f\u007f]/.test(label)) return "The label cannot hold control characters.";
  return "";
}

/** A setting's value as one line: a reference by its source and name, a secret only as set. */
export function settingText(value: unknown): string {
  if (value === null || value === undefined || value === "") return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.map(settingText).join(", ");
  if (typeof value === "object") {
    const record = value as Record<string, unknown>;
    if (typeof record.source === "string" && typeof record.name === "string") return `${record.source}: ${record.name}`;
    if (Object.keys(record).length === 1 && record.configured === true) return "set (hidden)";
    return JSON.stringify(record);
  }
  return String(value);
}

/** Sorted `[name, value]` pairs, so a table reads the same on every load. */
export function sortedEntries(values: Record<string, string>): [string, string][] {
  return Object.entries(values).sort(([a], [b]) => a.localeCompare(b));
}

/** One line naming what a recorded baseline holds. */
export function baselineSummary(entry: RecordedBaseline): string {
  const stances = Object.keys(entry.stances).length;
  const plugins = entry.plugins.length;
  return `${stances} ${stances === 1 ? "stance" : "stances"} · ${plugins} ${plugins === 1 ? "plugin" : "plugins"}`;
}

/** A recorded time as `YYYY-MM-DD HH:MM UTC`. */
export function recordedAt(ts: string): string {
  const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(ts);
  return match ? `${match[1]} ${match[2]} UTC` : ts;
}
