import { Button, Group, Select, Stack, Text, TextInput } from "@mantine/core";
import { useState } from "react";

import { useDrafts } from "./api";
import { newDraftError, pickerOptions, type ChangesDraft } from "./model";

/**
 * Create `name` through `create`, then select it. Resolves to "" on success, or to the refusal to
 * show; a name the picker can already see as invalid never reaches `create`.
 */
export async function createAndSelect(
  name: string, drafts: ChangesDraft[], create: (name: string) => Promise<ChangesDraft>,
  onChange: (name: string) => void,
): Promise<string> {
  const invalid = newDraftError(name, drafts);
  if (invalid) return invalid;
  try {
    const created = await create(name.trim());
    onChange(created.name);
    return "";
  } catch (error) {
    return error instanceof Error ? error.message : "The draft could not be created.";
  }
}

type ViewProps = {
  drafts: ChangesDraft[];
  status: "loading" | "error" | "ready";
  error: string;
  value: string;
  onChange: (name: string) => void;
  newName: string;
  onNewName: (name: string) => void;
  creating: boolean;
  createError: string;
  onCreate: () => void;
  label?: string;
};

/** The picker without its data: a list of existing drafts and a field to start a new one. */
export function DraftPickerView({ drafts, status, error, value, onChange, newName, onNewName, creating, createError, onCreate, label = "Draft" }: ViewProps) {
  const options = pickerOptions(drafts);
  return (
    <Stack className="draft-picker" gap="xs">
      <Group align="flex-end" gap="sm" wrap="wrap">
        <Select
          allowDeselect={false}
          data={options}
          description={status === "ready" && !options.length ? "No drafts yet. Start one below." : undefined}
          disabled={status !== "ready" || !options.length}
          error={status === "error" ? error || "Drafts could not be read." : undefined}
          label={label}
          onChange={(next) => { if (next) onChange(next); }}
          placeholder={status === "loading" ? "Reading drafts…" : "Pick a draft"}
          value={value || null}
          w={280}
        />
        <TextInput
          error={createError || undefined}
          label="New draft name"
          onChange={(event) => onNewName(event.currentTarget.value)}
          placeholder="my-experiment"
          value={newName}
          w={220}
        />
        <Button disabled={!newName.trim() || creating} loading={creating} onClick={onCreate} variant="default">
          Create draft
        </Button>
      </Group>
      <Text c="dimmed" size="xs">A draft is a private copy of your harness; nothing in it is live until you apply it in Changes.</Text>
    </Stack>
  );
}

type Props = {
  value: string;
  onChange: (name: string) => void;
  /** The page creating the draft, shown as its source in Changes (for example `stances`). */
  source?: string;
  label?: string;
};

/** Pick an existing draft or create one, for any page that saves its edits as a draft. */
export function DraftPicker({ value, onChange, source = "", label }: Props) {
  const { drafts, status, error, create } = useDrafts();
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");

  async function onCreate() {
    setCreating(true);
    const refusal = await createAndSelect(newName, drafts, (name) => create(name, source), onChange);
    setCreateError(refusal);
    if (!refusal) setNewName("");
    setCreating(false);
  }

  return (
    <DraftPickerView
      createError={createError} creating={creating} drafts={drafts} error={error} label={label}
      newName={newName} onChange={onChange} onCreate={() => void onCreate()}
      onNewName={(name) => { setNewName(name); setCreateError(""); }} status={status} value={value}
    />
  );
}
