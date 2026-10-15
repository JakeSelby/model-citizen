import { Alert, Badge, Button, Group, NativeSelect, Stack, Switch, Table, Text } from "@mantine/core";
import type { ReactNode } from "react";

import { EvidenceState } from "../components/StudioKit";
import "./harness.css";
import {
  draftChanges, settingValue, shortDate,
  type DraftChange, type SettingEdits, type SettingField, type SettingRow,
} from "./model";

export const NEW_PERIOD_NOTE = "Saving a change starts a new measurement period";

const SWITCH = ["on", "off"];

/** A field whose only choices are on and off is a switch, not a list. */
export function isSwitch(options: string[] | undefined): boolean {
  return Boolean(options && options.length === 2 && SWITCH.every((value) => options.includes(value)));
}

function InfoIcon() {
  return <svg aria-hidden="true" viewBox="0 0 24 24" width="1em" height="1em" fill="none" stroke="currentColor"
    strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="9" /><path d="M12 11v5" /><path d="M12 8h.01" />
  </svg>;
}

/** An empty value reads as a dash, and as "not set" to a screen reader. */
function Value({ value }: { value: string }) {
  return value
    ? <Text size="sm">{value}</Text>
    : <Text size="sm" c="dimmed"><span aria-hidden="true">–</span><span className="visually-hidden">not set</span></Text>;
}

// Kept free of hooks so tests can call it directly and reach its handlers.
export function SettingsTable({ caption, rows, fields, edits, onEdit, onDiscard, onDraft, saving = false, saveError = null, itemHeading = "Item", draftControls, footerNote }: {
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
  /** The draft picker, shown in the footer beside the save actions. */
  draftControls?: ReactNode;
  /** One line under the table that holds for every row, such as which fields are read-only. */
  footerNote?: ReactNode;
}) {
  const changes = draftChanges(rows, edits);
  const changed = new Set(changes.map((change) => change.item_id));
  return <Stack gap="md">
    <Alert variant="light" color="gray" icon={<InfoIcon />} title={NEW_PERIOD_NOTE} className="harness-note" role="note">
      Edits here become a draft in Changes. Nothing applies until you approve it there, and from
      that day Efficacy measures the item under its new setting for seven days.
    </Alert>
    <div className="harness-panel">
      <Table.ScrollContainer minWidth={640}
        scrollAreaProps={{ type: "auto", offsetScrollbars: false, viewportProps: { role: "region", "aria-label": caption, tabIndex: 0 } }}>
        <Table verticalSpacing="sm" aria-label={caption} className="data-table harness-table">
          <Table.Caption className="visually-hidden">{caption}</Table.Caption>
          <Table.Thead>
            <Table.Tr>
              <Table.Th scope="col" className="harness-item harness-sticky">{itemHeading}</Table.Th>
              {fields.map((field) => <Table.Th scope="col" key={field.key}>{field.heading}</Table.Th>)}
              <Table.Th scope="col">Last changed</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((row) => {
              const drafted = changed.has(row.item_id);
              return <Table.Tr key={row.item_id} data-item={row.item_id} data-draft={drafted || undefined}
                bg={drafted ? "var(--mantine-color-yellow-light)" : undefined}>
                <Table.Th scope="row" fw={400} className="harness-sticky">
                  <Text size="sm" fw={600}>{row.item_label}</Text>
                  {row.item_detail && <Text size="xs" c="dimmed" lineClamp={2} title={row.item_detail_title}>{row.item_detail}</Text>}
                </Table.Th>
                {fields.map((field) => {
                  const options = row.options?.[field.key];
                  const value = settingValue(row, field.key, edits);
                  const saved = row.values[field.key] ?? "";
                  const label = `${field.heading}: ${row.item_label}`;
                  // A short value never breaks mid-word in a narrow column; long ones wrap.
                  return <Table.Td key={field.key} className={saved.length <= 24 ? "harness-nowrap" : undefined}>
                    {isSwitch(options)
                      ? <Switch size="sm" checked={value === "on"} aria-label={label}
                        onChange={(event) => onEdit(row.item_id, field.key, event.currentTarget.checked ? "on" : "off")} />
                      : options
                        ? <NativeSelect size="xs" data={options} value={value} aria-label={label}
                          onChange={(event) => onEdit(row.item_id, field.key, event.currentTarget.value)} />
                        // Read-only on/off is a status badge: a disabled switch hides which way it is set.
                        : saved === "on" || saved === "off"
                          ? <Badge className="harness-badge" variant="dot" tt="none" color={saved === "on" ? "green" : "gray"}>{saved === "on" ? "On" : "Off"}</Badge>
                          : <Value value={saved} />}
                    {value !== saved && <Text size="xs" c="yellow">was: {saved || "not set"}</Text>}
                  </Table.Td>;
                })}
                <Table.Td>
                  {drafted
                    ? <Text size="sm" fw={600} c="yellow">Draft, not applied</Text>
                    : row.last_changed
                      ? <Text size="sm" className="harness-nowrap">{shortDate(row.last_changed)}</Text>
                      : <Text size="sm" c="dimmed"><span aria-hidden="true">–</span><span className="visually-hidden">not recorded</span></Text>}
                  {drafted && row.last_changed && <Text size="xs" c="dimmed">was {shortDate(row.last_changed)}</Text>}
                  {!drafted && row.last_change_note && <Text size="xs" c="dimmed" lineClamp={2}>{row.last_change_note}</Text>}
                </Table.Td>
              </Table.Tr>;
            })}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      {(draftControls || footerNote || changes.length > 0) && <div className="harness-footer">
        <Group justify="space-between" align="flex-end" wrap="wrap" gap="sm">
          <Stack gap="xs">
            {draftControls}
            {footerNote && <Text size="sm" c="dimmed">{footerNote}</Text>}
          </Stack>
          {changes.length > 0 && <Group gap="xs">
            <Badge color="yellow" variant="light" tt="none">
              {changes.length} {changes.length === 1 ? "change" : "changes"} in draft
            </Badge>
            <Button variant="default" disabled={saving} onClick={onDiscard}>Discard</Button>
            <Button loading={saving} onClick={() => onDraft(changes)}>Send to Changes</Button>
          </Group>}
        </Group>
      </div>}
    </div>
    {saveError && <EvidenceState kind="error" title="The draft was not saved">{saveError}</EvidenceState>}
  </Stack>;
}
