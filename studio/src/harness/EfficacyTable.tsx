import { Anchor, Badge, Stack, Table, Text } from "@mantine/core";
import { Link } from "react-router-dom";

import {
  DEFAULT_EVALS_HREF, groupRows, measuringText, metricText, periodRole, periodText,
  type MetricColumn, type PeriodRow,
} from "./model";

// Kept free of hooks so tests can call it directly and reach its handlers.
export function EfficacyTable({ caption, rows, columns, itemHeading = "Item" }: {
  caption: string;
  rows: PeriodRow[];
  columns: MetricColumn[];
  itemHeading?: string;
}) {
  return <Table.ScrollContainer minWidth={640}>
    <Table verticalSpacing="sm" aria-label={caption}>
      <Table.Caption>{caption}</Table.Caption>
      <Table.Thead>
        <Table.Tr>
          <Table.Th scope="col">{itemHeading}</Table.Th>
          <Table.Th scope="col">Setting in effect</Table.Th>
          <Table.Th scope="col">Period</Table.Th>
          {columns.map((column) => <Table.Th scope="col" key={column.key}>{column.heading}</Table.Th>)}
          <Table.Th scope="col">Status</Table.Th>
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
            {index === 0 && <Table.Th scope="rowgroup" rowSpan={group.rows.length} fw={600}>{group.item_label}</Table.Th>}
            <Table.Td>{row.setting_label}</Table.Td>
            <Table.Td>
              {role && <Text size="sm" fw={600}>{role}</Text>}
              <Text size="sm" c={role ? "dimmed" : undefined}>{periodText(row.period)}</Text>
            </Table.Td>
            {columns.map((column) => <Table.Td key={column.key}>{metricText(row.metrics[column.key])}</Table.Td>)}
            <Table.Td>
              {measuring
                ? <Stack gap={4} align="flex-start">
                  <Badge color="blue" variant="light" tt="none">{measuringText(row)}</Badge>
                  <Anchor component={Link} to={row.evals_href ?? DEFAULT_EVALS_HREF} size="sm"
                    aria-label={`Before and after in Evals: ${row.item_label}`}>Before and after in Evals</Anchor>
                </Stack>
                : <Badge color="gray" variant="light" tt="none">{measuringText(row)}</Badge>}
            </Table.Td>
          </Table.Tr>;
        })}
      </Table.Tbody>)}
    </Table>
  </Table.ScrollContainer>;
}
