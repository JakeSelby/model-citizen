import { Alert, Badge, Button, Group, NativeSelect, Stack, Table, Text } from "@mantine/core";

import { EvidenceState } from "../components/StudioKit";
import {
  draftChanges, settingValue, shortDate,
  type DraftChange, type SettingEdits, type SettingField, type SettingRow,
} from "./model";

export const NEW_PERIOD_NOTE = "Saving a change starts a new measurement period";

// Kept free of hooks so tests can call it directly and reach its handlers.
export function SettingsTable({ caption, rows, fields, edits, onEdit, onDiscard, onDraft, saving = false, saveError = null, itemHeading = "Item" }: {
  caption: string;
  rows: SettingRow[];
  fields: SettingField[];
  edits: SettingEdits;
  onEdit: (itemId: string, field: string, value: string) => void;
  onDiscard: () => void;
  /** Routes the edits to Changes as a draft; nothing applies from this tab. */
  onDraft: (changes: DraftChange[]) => void;
  saving?: boolean;
  saveError?: string | null;
  itemHeading?: string;
}) {
  const changes = draftChanges(rows, edits);
  const changed = new Set(changes.map((change) => change.item_id));
  return <Stack gap="md">
    <Alert variant="light" title={NEW_PERIOD_NOTE}>
      Edits here become a draft in Changes. Nothing applies until you approve it there, and from
      that day Efficacy measures the item under its new setting for seven days.
    </Alert>
    <Table.ScrollContainer minWidth={640}
      scrollAreaProps={{ viewportProps: { role: "region", "aria-label": caption, tabIndex: 0 } }}>
      <Table verticalSpacing="sm" aria-label={caption}>
        <Table.Caption>{caption}</Table.Caption>
        <Table.Thead>
          <Table.Tr>
            <Table.Th scope="col">{itemHeading}</Table.Th>
            {fields.map((field) => <Table.Th scope="col" key={field.key}>{field.heading}</Table.Th>)}
            <Table.Th scope="col">Last changed</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {rows.map((row) => {
            const drafted = changed.has(row.item_id);
            return <Table.Tr key={row.item_id} data-item={row.item_id} data-draft={drafted || undefined}
              bg={drafted ? "var(--mantine-color-yellow-light)" : undefined}>
              <Table.Th scope="row" fw={400}>
                <Text size="sm" fw={600}>{row.item_label}</Text>
                {row.item_detail && <Text size="xs" c="dimmed">{row.item_detail}</Text>}
              </Table.Th>
              {fields.map((field) => {
                const options = row.options?.[field.key];
                const value = settingValue(row, field.key, edits);
                const saved = row.values[field.key] ?? "";
                return <Table.Td key={field.key}>
                  {options
                    ? <NativeSelect size="xs" data={options} value={value}
                      aria-label={`${field.heading}: ${row.item_label}`}
                      onChange={(event) => onEdit(row.item_id, field.key, event.currentTarget.value)} />
                    : <Text size="sm">{saved || "not set"}</Text>}
                  {value !== saved && <Text size="xs" c="yellow">was: {saved || "not set"}</Text>}
                </Table.Td>;
              })}
              <Table.Td>
                {drafted
                  ? <Text size="sm" fw={600} c="yellow">Draft, not applied</Text>
                  : <Text size="sm">{row.last_changed ? shortDate(row.last_changed) : "not recorded"}</Text>}
                {drafted && row.last_changed && <Text size="xs" c="dimmed">was {shortDate(row.last_changed)}</Text>}
                {!drafted && row.last_change_note && <Text size="xs" c="dimmed">{row.last_change_note}</Text>}
              </Table.Td>
            </Table.Tr>;
          })}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
    {saveError && <EvidenceState kind="error" title="The draft was not saved">{saveError}</EvidenceState>}
    <Group justify="space-between" wrap="wrap" gap="sm">
      <Group gap="xs">
        {changes.length > 0 && <Badge color="yellow" variant="light" tt="none">
          {changes.length} {changes.length === 1 ? "change" : "changes"} in draft
        </Badge>}
        <Text size="sm" c="dimmed">Sending to Changes starts a new measurement period once it is approved.</Text>
      </Group>
      <Group gap="xs">
        <Button variant="default" disabled={!changes.length || saving} onClick={onDiscard}>Discard</Button>
        <Button disabled={!changes.length} loading={saving} onClick={() => onDraft(changes)}>Send to Changes</Button>
      </Group>
    </Group>
  </Stack>;
}
