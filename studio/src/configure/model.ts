export type ReferenceValue = { source: string; name: string };

export type FieldDescriptor = {
  path: string;
  section: string;
  label: string;
  help: string;
  kind: "string" | "url" | "select" | "boolean" | "integer" | "number" |
    "string-list" | "multi-select" | "json-object" | "reference";
  provenance: string;
  required: boolean;
  options: string[];
  reference_sources: string[];
  constraints: Record<string, unknown>;
  default: unknown;
};

export type SectionDescriptor = {
  id: string;
  label: string;
  description: string;
  fields: FieldDescriptor[];
};

export type ConfigureSchema = {
  schema_version: number;
  commands: Record<"schema" | "read" | "preview" | "save", string>;
  sections: SectionDescriptor[];
};

export function commandFor(template: string, draft: string, revision: string): string {
  return template.replaceAll("{draft}", draft).replaceAll("{revision}", revision);
}
export type ValidationError = { path: string; message: string };
export const AUTOSAVE_DELAY_MS = 750;

export function fieldsOf(schema: ConfigureSchema): FieldDescriptor[] {
  return schema.sections.flatMap((section) => section.fields);
}

export function emptyValue(field: FieldDescriptor): unknown {
  if (field.kind === "boolean") return false;
  if (field.kind === "string-list" || field.kind === "multi-select") return [];
  if (field.kind === "json-object") return {};
  if (field.kind === "integer" || field.kind === "number") return 0;
  if (field.kind === "reference") {
    return { source: field.reference_sources[0] ?? "environment", name: "" };
  }
  return "";
}

export function hydrateValues(
  schema: ConfigureSchema,
  values: Record<string, unknown>,
): Record<string, unknown> {
  return Object.fromEntries(fieldsOf(schema).map((field) => [
    field.path,
    values[field.path] ?? field.default ?? emptyValue(field),
  ]));
}

export function parseJsonObject(text: string): Record<string, unknown> {
  const value: unknown = JSON.parse(text || "{}");
  if (value === null || Array.isArray(value) || typeof value !== "object") {
    throw new Error("Enter a JSON object.");
  }
  return value as Record<string, unknown>;
}

export function errorMap(errors: ValidationError[]): Record<string, string> {
  return Object.fromEntries(errors.map((error) => [error.path, error.message]));
}

export function fieldEnabled(field: FieldDescriptor, values: Record<string, unknown>): boolean {
  const dependency = field.constraints.depends_on;
  if (!dependency || typeof dependency !== "object") return true;
  const path = (dependency as { path?: unknown }).path;
  const expected = (dependency as { equals?: unknown }).equals;
  return typeof path === "string" && values[path] === expected;
}

export function remainingChanges(
  current: Record<string, unknown>,
  saved: Record<string, unknown>,
): Record<string, unknown> {
  return Object.fromEntries(Object.entries(current).filter(([path, value]) => (
    !(path in saved) || JSON.stringify(value) !== JSON.stringify(saved[path])
  )));
}
