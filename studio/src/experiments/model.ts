export type UnitTestCase = {
  id: string;
  module: string;
  class_name: string;
  test_name: string;
  label: string;
};

export type FreeSuite = {
  id: string;
  label: string;
  description: string;
  cost_class: "free";
  expected_duration_seconds: number;
  command: string;
  command_argv: string[];
  parameters: Record<string, string>;
  case_count: number;
};

export type RunCatalog = {
  schema_version: number;
  target: { kind: "installed"; label: string };
  suites: FreeSuite[];
  unit_tests: { cases: UnitTestCase[]; scopes: Record<string, string[]> };
  commands: Record<string, string>;
};

export type CaseResult = { id: string; status: "passed" | "failed" | "skipped"; detail: string };
export type LintFinding = {
  path: string;
  line: number;
  column: number | null;
  message: string;
  library_href: string | null;
};

export type RunRecord = {
  run_id: string;
  suite_id: string;
  status: string;
  exact_command: string;
  case_identities: string[];
  target: { kind: string; reference_set: boolean };
  reason?: string;
};

export type RunUpdate = {
  run: RunRecord;
  stdout: { chunk: string; cursor: number; eof: boolean };
  stderr: { chunk: string; cursor: number; eof: boolean };
  progress: {
    completed: number;
    eligible: number;
    cases: CaseResult[];
    lint_findings: LintFinding[];
  };
};

export function shellQuote(value: string): string {
  if (/^[A-Za-z0-9_./:=+-]+$/.test(value)) return value;
  return `'${value.replaceAll("'", `'"'"'`)}'`;
}

export function commandFor(suite: FreeSuite, selectedCase: string): string {
  const argv = suite.command_argv.map((item) => item === "case=all" ? `case=${selectedCase}` : item);
  return argv.map(shellQuote).join(" ");
}

export function scopeOptions(catalog: RunCatalog): Array<{ value: string; label: string }> {
  const cases = new Map(catalog.unit_tests.cases.map((item) => [item.id, item]));
  return Object.keys(catalog.unit_tests.scopes).sort((left, right) => {
    if (left === "all") return -1;
    if (right === "all") return 1;
    return left.localeCompare(right);
  }).map((value) => {
    if (value === "all") return { value, label: `All tests (${catalog.unit_tests.scopes[value].length})` };
    const item = cases.get(value);
    if (item) return { value, label: `${item.module} / ${item.class_name} / ${item.test_name}` };
    return { value, label: `${value} (${catalog.unit_tests.scopes[value].length})` };
  });
}

export function terminal(status: string): boolean {
  return ["succeeded", "failed", "cancelled", "timed_out", "orphaned", "capped", "limited"]
    .includes(status);
}

export function streamComplete(update: RunUpdate): boolean {
  return terminal(update.run.status) && update.stdout.eof && update.stderr.eof;
}

export function mergeUpdate(previous: RunUpdate, next: RunUpdate): RunUpdate {
  const cases = new Map(previous.progress.cases.map((item) => [item.id, item]));
  for (const item of next.progress.cases) cases.set(item.id, item);
  const findings = new Map(previous.progress.lint_findings.map((item) => [`${item.path}:${item.line}:${item.message}`, item]));
  for (const item of next.progress.lint_findings) findings.set(`${item.path}:${item.line}:${item.message}`, item);
  return { ...next, progress: { ...next.progress, completed: Math.max(next.progress.completed, cases.size),
    cases: [...cases.values()], lint_findings: [...findings.values()] } };
}

export function parseEventStream(body: string): RunUpdate[] {
  return body.split("\n\n").flatMap((event) => {
    const data = event.split("\n").filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trim()).join("\n");
    if (!data) return [];
    return [JSON.parse(data) as RunUpdate];
  });
}
