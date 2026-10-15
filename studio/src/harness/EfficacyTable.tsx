import { Anchor, Badge, Stack, Table, Text } from "@mantine/core";
import { Link } from "react-router-dom";

import "./harness.css";
import {
  DEFAULT_EVALS_HREF, NO_VALUE, groupRows, isNumericColumn, measuringText, metricText, periodRole, periodText,
  type MetricColumn, type PeriodRow,
} from "./model";

/** A metric's text, or a dash for a missing figure so a column of figures still scans. */
function Metric({ text }: { text: string }) {
  if (!NO_VALUE.has(text)) return <>{text}</>;
  return <Text span size="sm" c="dimmed"><span aria-hidden="true">–</span><span className="visually-hidden">{text || "none"}</span></Text>;
}

/** A measured period no session ran under says so, rather than "measured" over a row of zeros. */
export function statusText(row: PeriodRow): string {
  return row.status === "measured" && row.metrics.sessions === 0 ? "no sessions" : measuringText(row);
}

// Kept free of hooks so tests can call it directly and reach its handlers.
export function EfficacyTable({ caption, rows, columns, itemHeading = "Item" }: {
  caption: string;
  rows: PeriodRow[];
  columns: MetricColumn[];
  itemHeading?: string;
}) {
  const numeric = new Set(columns.filter((column) => (column.align ?? (isNumericColumn(rows, column.key) ? "right" : "left")) === "right")
    .map((column) => column.key));
  // Figures never wrap; a short text metric (a model, a date) reads better on one line too.
  // Status earns its column only when some row is measuring or ran no session.
  const showStatus = rows.some((row) => statusText(row) !== "measured");
  const align = (key: string) => numeric.has(key) ? "harness-num" : "harness-nowrap";
  return <div className="harness-panel">
    <Table.ScrollContainer minWidth={640}
      scrollAreaProps={{ type: "auto", offsetScrollbars: false, viewportProps: { role: "region", "aria-label": caption, tabIndex: 0 } }}>
      <Table verticalSpacing="sm" aria-label={caption} className="data-table harness-table">
        <Table.Caption className="visually-hidden">{caption}</Table.Caption>
        <Table.Thead>
          <Table.Tr>
            <Table.Th scope="col" className="harness-sticky">{itemHeading}</Table.Th>
            <Table.Th scope="col">Setting in effect</Table.Th>
            <Table.Th scope="col">Period</Table.Th>
            {columns.map((column) => <Table.Th scope="col" key={column.key} className={align(column.key)}>{column.heading}</Table.Th>)}
            {showStatus && <Table.Th scope="col">Status</Table.Th>}
          </Table.Tr>
        </Table.Thead>
        {groupRows(rows).map((group) => <Table.Tbody key={group.item_id} data-item={group.item_id}>
          {group.rows.map((row, index) => {
            const role = periodRole(group.rows, index);
            const measuring = row.status === "measuring";
            return <Table.Tr
              key={`${row.period.start}-${index}`}
              data-period={role ?? "whole"}
              data-status={row.status}
              bg={measuring ? "var(--mantine-primary-color-light)" : undefined}
            >
              {index === 0 && <Table.Th scope="rowgroup" rowSpan={group.rows.length} fw={600} className="harness-sticky"><span className="harness-name" title={group.item_label} data-words={/\s/.test(group.item_label) || undefined}>{group.item_label}</span></Table.Th>}
              <Table.Td>{row.setting_label}</Table.Td>
              <Table.Td className="harness-nowrap">
                {role && <Text size="sm" fw={600}>{role}</Text>}
                <Text size="sm" c={role ? "dimmed" : undefined}>{periodText(row.period)}</Text>
              </Table.Td>
              {columns.map((column) => <Table.Td key={column.key} className={align(column.key)}>
                {column.render ? column.render(row) : <Metric text={metricText(row.metrics[column.key], column.key)} />}
              </Table.Td>)}
              {showStatus && <Table.Td className="harness-nowrap">
                {measuring
                  ? <Stack gap="xs" align="flex-start">
                    <Badge className="harness-badge" color="blue" variant="dot" tt="none">{measuringText(row)}</Badge>
                    <Anchor component={Link} to={row.evals_href ?? DEFAULT_EVALS_HREF} size="sm"
                      aria-label={`Before and after in Evals: ${row.item_label}`}>Before and after in Evals</Anchor>
                  </Stack>
                  : <Badge className="harness-badge" color="gray" variant="dot" tt="none">{statusText(row)}</Badge>}
              </Table.Td>}
            </Table.Tr>;
          })}
        </Table.Tbody>)}
      </Table>
    </Table.ScrollContainer>
  </div>;
}
