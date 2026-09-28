import { Button, Loader, Text, Title } from "@mantine/core";
import { createContext, useContext, useId, useState, type ReactNode } from "react";
import { compareLines, intervalSummary, type EvidenceTone } from "./evidence";

export function StatusBadge({ children, tone = "neutral" }: { children: ReactNode; tone?: EvidenceTone }) {
  return <span className="status-badge" data-tone={tone}>{children}</span>;
}

export function EvidenceState({ kind, title, children, action }: {
  kind: "empty" | "loading" | "error" | "refused";
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return <div className="evidence-state" data-state={kind} role={kind === "error" || kind === "refused" ? "alert" : "status"} aria-busy={kind === "loading"}>
    <span className="state-symbol" aria-hidden="true">{kind === "loading" ? <Loader size="sm" /> : kind === "error" ? "!" : kind === "refused" ? "×" : "–"}</span>
    <div className="state-content"><Text fw={650}>{title}</Text>{children && <Text c="dimmed" size="sm">{children}</Text>}{action}</div>
  </div>;
}

export function CodeView({ children, label = "Code" }: { children: string; label?: string }) {
  return <pre className="code-view" aria-label={label} tabIndex={0}><code>{children}</code></pre>;
}

export function DiffView({ before, after, label = "Changes" }: { before: string; after: string; label?: string }) {
  return <pre className="code-view diff-view" aria-label={label} tabIndex={0}><code>{compareLines(before, after).map((line, index) =>
    <span className="diff-line" data-kind={line.kind} key={index}><span className="visually-hidden">{line.kind}: </span><span aria-hidden="true">{line.kind === "added" ? "+ " : line.kind === "removed" ? "− " : "  "}</span>{line.text}{"\n"}</span>)}</code></pre>;
}

export function IntervalDisplay({ estimate, low, high, unit, confidence = 95 }: {
  estimate: number; low: number; high: number; unit: string; confidence?: number;
}) {
  const summary = intervalSummary(estimate, low, high, unit, confidence);
  return <Text className="interval-display" size="sm">{summary}</Text>;
}

export type DataColumn<Row> = { key: string; heading: string; cell: (row: Row) => ReactNode };

export function DataTable<Row>({ caption, columns, rows, rowKey, empty = "No records yet" }: {
  caption: string; columns: DataColumn<Row>[]; rows: Row[]; rowKey: (row: Row) => string; empty?: string;
}) {
  const id = useId();
  return <div className="data-table-scroll" role="region" aria-labelledby={id} tabIndex={0}>
    <table className="data-table"><caption id={id}>{caption}</caption><thead><tr>{columns.map((column) => <th scope="col" key={column.key}>{column.heading}</th>)}</tr></thead>
      <tbody>{rows.length ? rows.map((row) => <tr key={rowKey(row)}>{columns.map((column) => <td key={column.key}>{column.cell(row)}</td>)}</tr>) : <tr><td colSpan={columns.length}><EvidenceState kind="empty" title={empty} /></td></tr>}</tbody>
    </table>
  </div>;
}

type Toast = { id: number; message: string; tone: EvidenceTone };
const ToastContext = createContext<(message: string, tone?: EvidenceTone) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  function notify(message: string, tone: EvidenceTone = "success") {
    setToasts((current) => [...current.slice(-2), { id: (current.at(-1)?.id ?? 0) + 1, message, tone }]);
  }
  return <ToastContext.Provider value={notify}>{children}<div className="toast-region" aria-label="Notifications" aria-live="polite" aria-relevant="additions text">{toasts.map((toast) =>
    <div className="studio-toast" data-tone={toast.tone} key={toast.id}><Text size="sm">{toast.message}</Text><Button variant="subtle" size="compact-sm" aria-label={`Dismiss: ${toast.message}`} onClick={() => setToasts((current) => current.filter(({ id }) => id !== toast.id))}>Dismiss</Button></div>)}</div></ToastContext.Provider>;
}

export function CommandChip({ command, label = "CLI equivalent" }: { command: string; label?: string }) {
  const notify = useContext(ToastContext);
  const [copying, setCopying] = useState(false);
  async function copy() {
    setCopying(true);
    try {
      await navigator.clipboard.writeText(command);
      notify("Command copied to clipboard.");
    } catch {
      notify("Clipboard unavailable. Select and copy the command below.", "danger");
    } finally {
      setCopying(false);
    }
  }
  return <div className="command-chip"><div className="command-heading"><Text size="sm" fw={650}>{label}</Text><Button variant="subtle" size="compact-sm" loading={copying} aria-label={`Copy ${label}`} onClick={copy}>Copy command</Button></div><CodeView label={label}>{command}</CodeView></div>;
}

export function SectionHeading({ title, children }: { title: string; children?: ReactNode }) {
  return <div className="section-heading"><Title order={2}>{title}</Title>{children}</div>;
}
