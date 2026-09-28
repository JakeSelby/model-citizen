import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MantineProvider, Paper, Stack, Text, Title } from "@mantine/core";
import { studioTheme } from "../src/theme.ts";
import { CodeView, CommandChip, DataTable, DiffView, EvidenceState, IntervalDisplay, StatusBadge, ToastProvider } from "../src/components/StudioKit.tsx";

const rows = [
  { id: "1", name: "Stop gate timing", status: "Ready", tone: "success" },
  { id: "2", name: "Configuration comparison with a deliberately long descriptive report name", status: "Inconclusive", tone: "warning" },
  { id: "3", name: "A".repeat(120), status: "Refused", tone: "danger" },
];
const markup = renderToStaticMarkup(h(MantineProvider, { theme: studioTheme }, h(ToastProvider, {}, h(Stack, { gap: "lg" },
  h(Stack, { gap: "var(--studio-space-3)" },
    h(Title, { order: 1 }, "Shared evidence components"),
    h(Text, {}, "Deterministic test fixtures · component state and content extremes")),
  h(Paper, { p: "lg", withBorder: true }, h(DataTable, {
    caption: "Run evidence", rows, rowKey: (row) => row.id,
    columns: [{ key: "name", heading: "Run", cell: (row) => row.name }, { key: "status", heading: "Status", cell: (row) => h(StatusBadge, { tone: row.tone }, row.status) }],
  }), h(IntervalDisplay, { estimate: 2.1, low: -1.4, high: 5.6, unit: "pts" })),
  h(Paper, { p: "lg", withBorder: true }, h(CommandChip, { label: "Preview a draft", command: "citizen draft settings preview 'review first' --changes changes.json --json" })),
  h(Paper, { p: "lg", withBorder: true }, h(Stack, { gap: "var(--studio-space-3)" }, h(Title, { order: 2 }, "Readable changes"), h(DiffView, { before: '{\n  "enabled": false\n}', after: '{\n  "enabled": true\n}' }), h(CodeView, { label: "Long code example" }, "Long evidence identifier: " + "abcdef".repeat(50)))),
  h(Paper, { p: "lg", withBorder: true }, ...["empty", "loading", "error", "refused"].map((kind) => h(EvidenceState, { key: kind, kind, title: { empty: "No completed runs", loading: "Loading evidence", error: "Evidence could not be read", refused: "Action refused by policy" }[kind] }, "The source and the next action remain visible."))),
))));
process.stdout.write(markup.replace(/<style\b[^>]*>[\s\S]*?<\/style>/g, ""));
