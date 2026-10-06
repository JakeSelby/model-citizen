import {
  Alert, Badge, Button, Code, Group, MultiSelect, Paper, Progress, Select, Stack,
  Text, TextInput, Title,
} from "@mantine/core";
import { useMemo, useRef, useState } from "react";

import { previewNativeRun, startNativeRun } from "./api";
import {
  completion, mayBeginRequest, nextPreviewGeneration, phaseAfterEdit,
  previewGenerationIsCurrent, retryable, selectedClient, selectionAfterStart,
  selectionForResume,
  selectionReady, spendReady, verdictColor,
  type NativeCatalog, type NativeRun, type NativeSelection, type NativeSnapshot,
  type NativeRequestPhase, type SpendPreview, type SpendRequest,
} from "./model";

type Props = {
  catalog: NativeCatalog;
  initial: NativeSelection;
  initialSpend?: SpendRequest;
  initialPreview?: SpendPreview | null;
  snapshot?: NativeSnapshot | null;
  busy?: boolean;
  onPreview?: (selection: NativeSelection, spend: SpendRequest) => Promise<SpendPreview>;
  onStart?: (
    selection: NativeSelection, spend: SpendRequest, confirmationToken: string,
  ) => Promise<NativeRun>;
  onStarted?: (run: NativeRun) => void;
  onStarting?: () => void;
  onResume: (selection: NativeSelection) => void;
  onRetryFailed: (selection: NativeSelection, caseId: string) => void;
};

export function NativeAcceptancePanel({
  catalog, initial,
  initialSpend = { max_budget_usd: "0.20", spend_cap_usd: "1.00", pricing_source: "api_credit" },
  initialPreview = null, snapshot = null, busy = false,
  onPreview = previewNativeRun, onStart = startNativeRun, onStarted = () => {},
  onStarting = () => {},
  onResume, onRetryFailed,
}: Props) {
  const [selection, setSelection] = useState(initial);
  const [spend, setSpend] = useState(initialSpend);
  const generation = useRef(0);
  const [preview, setPreview] = useState(
    initialPreview ? { value: initialPreview, generation: 0 } : null,
  );
  const [phase, setPhase] = useState<NativeRequestPhase>("idle");
  const phaseRef = useRef<NativeRequestPhase>("idle");
  const requesting = phase !== "idle";
  const [error, setError] = useState("");
  const client = selectedClient(catalog, selection.client);
  const ready = selectionReady(catalog, selection) && spendReady(spend);
  const failed = snapshot ? retryable(snapshot) : [];
  const caseDescriptions = useMemo(() => new Map(
    catalog.cases.map((item) => [item.id, item.description]),
  ), [catalog]);

  function change<T extends keyof NativeSelection>(name: T, value: NativeSelection[T]) {
    generation.current = nextPreviewGeneration(generation.current);
    setSelection((current) => ({ ...current, [name]: value }));
    setPreview(null);
    const next = phaseAfterEdit(phaseRef.current);
    phaseRef.current = next;
    setPhase(next);
  }

  function changeSpend<T extends keyof SpendRequest>(name: T, value: SpendRequest[T]) {
    generation.current = nextPreviewGeneration(generation.current);
    setSpend((current) => ({ ...current, [name]: value }));
    setPreview(null);
    const next = phaseAfterEdit(phaseRef.current);
    phaseRef.current = next;
    setPhase(next);
  }

  function changeTarget(name: "target_kind" | "target_ref", value: string) {
    generation.current = nextPreviewGeneration(generation.current);
    setSelection((current) => ({ ...current, [name]: value, source_commit: "" } as NativeSelection));
    setPreview(null);
    const next = phaseAfterEdit(phaseRef.current);
    phaseRef.current = next;
    setPhase(next);
  }

  async function review() {
    if (!mayBeginRequest(phaseRef.current)) return;
    const ticket = nextPreviewGeneration(generation.current);
    generation.current = ticket;
    setError("");
    setPreview(null);
    phaseRef.current = "previewing";
    setPhase("previewing");
    try {
      const value = await onPreview(selection, spend);
      if (previewGenerationIsCurrent(ticket, generation.current)) {
        setPreview({ value, generation: ticket });
      }
    } catch (reason) {
      if (previewGenerationIsCurrent(ticket, generation.current)) {
        setPreview(null);
        setError(reason instanceof Error ? reason.message : "Spend preview failed closed.");
      }
    } finally {
      if (previewGenerationIsCurrent(ticket, generation.current)) {
        phaseRef.current = "idle";
        setPhase("idle");
      }
    }
  }

  async function launch() {
    if (!mayBeginRequest(phaseRef.current)) return;
    if (!preview || !previewGenerationIsCurrent(preview.generation, generation.current)) {
      setError("Inputs changed after preview; review spend again.");
      return;
    }
    setError("");
    phaseRef.current = "starting";
    setPhase("starting");
    onStarting();
    try {
      const run = await onStart(selection, spend, preview.value.confirmation_token);
      setSelection(selectionAfterStart(run));
      onStarted(run);
      generation.current = nextPreviewGeneration(generation.current);
      setPreview(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Native acceptance launch was refused.");
    } finally {
      phaseRef.current = "idle";
      setPhase("idle");
    }
  }

  return (
    <Stack gap="xl">
      <Group align="flex-start" justify="space-between">
        <div>
          <Text className="eyebrow">Experiments / Native acceptance</Text>
          <Title order={2}>Check the native cases this change touches.</Title>
          <Text c="dimmed" mt="xs">
            Each case runs in its own disposable client home. Finished evidence survives interruption.
          </Text>
        </div>
        <Badge color="yellow" variant="light">Model usage</Badge>
      </Group>

      <Paper p="xl" withBorder>
        <Stack gap="lg">
          <Select
            disabled={requesting}
            data={catalog.clients.map((item) => ({ value: item.id, label: `${item.id} · ${item.platform}` }))}
            description="Clients without an enforceable in-flight cap remain unavailable."
            label="Client"
            value={selection.client}
            onChange={(value) => change("client", value ?? "")}
          />
          {client && !client.spend_cap_supported && (
            <Alert color="red" title="Launch refused">{client.unavailable_reason}</Alert>
          )}
          <TextInput
            disabled={requesting}
            description="The model passed to each native client turn."
            label="Model"
            value={selection.model}
            onChange={(event) => change("model", event.currentTarget.value)}
          />
          <MultiSelect
            disabled={requesting}
            data={catalog.cases.map((item) => ({ value: item.id, label: item.id }))}
            description="Only selected cases are eligible to run."
            label="Cases"
            searchable
            maxValues={3}
            value={selection.cases}
            onChange={(value) => change("cases", value)}
          />
          <Group grow align="flex-start">
            <Select
              disabled={requesting}
              data={[
                { value: "installed", label: "Installed checkout" },
                { value: "release", label: "Release tag" },
                { value: "branch", label: "Branch or commit" },
                { value: "worktree", label: "Managed worktree" },
                { value: "draft", label: "Saved draft" },
              ]}
              description="The target service snapshots this selection before launch."
              label="Target kind"
              value={selection.target_kind}
              onChange={(value) => changeTarget("target_kind", value ?? "installed")}
            />
            <TextInput
              disabled={requesting}
              description="Use current, a tag, branch/commit, worktree path, or draft name."
              label="Target reference"
              value={selection.target_ref}
              onChange={(event) => changeTarget("target_ref", event.currentTarget.value)}
            />
          </Group>
          <Group grow align="flex-start">
            <TextInput
              disabled={requesting}
              description="Native limit passed to every model turn."
              label="Maximum per turn (USD)"
              value={spend.max_budget_usd}
              onChange={(event) => changeSpend("max_budget_usd", event.currentTarget.value)}
            />
            <TextInput
              disabled={requesting}
              description="The set stops before its next case at this amount."
              label="Whole-set cap (USD)"
              value={spend.spend_cap_usd}
              onChange={(event) => changeSpend("spend_cap_usd", event.currentTarget.value)}
            />
          </Group>
          <Select
            disabled={requesting}
            data={[
              { value: "api_credit", label: "API credit · money charged" },
              { value: "subscription", label: "Subscription · list-price equivalent" },
            ]}
            description="Records how the displayed dollars should be interpreted."
            label="Pricing basis"
            value={spend.pricing_source}
            onChange={(value) => changeSpend(
              "pricing_source", (value ?? "api_credit") as SpendRequest["pricing_source"],
            )}
          />
          <Paper bg="var(--studio-surface-canvas)" p="sm" withBorder>
            <Text fw={600} size="sm">Immutable target</Text>
            <Text c="dimmed" size="xs">The target service resolves and snapshots the selection before launch.</Text>
            <Code mt="xs">{selection.source_commit || `${selection.target_kind}:${selection.target_ref} · commit resolves at launch`}</Code>
          </Paper>
          <Group justify="space-between">
            <Text c="dimmed" size="sm">Spend estimate and caps appear before any process starts.</Text>
            <Button disabled={!ready || busy || requesting} loading={requesting} onClick={review}>
              Review spend and run
            </Button>
          </Group>
          {error && <Alert color="red" title="Launch refused">{error}</Alert>}
          {preview && (
            <Paper aria-label="Native acceptance spend preview" p="md" withBorder>
              <Group align="flex-start" justify="space-between">
                <div>
                  <Text fw={650}>Spend preview</Text>
                  <Text c="dimmed" size="sm">
                    {preview.value.estimate.amount_usd === null
                      ? "No comparable completed run; estimate unavailable."
                      : `Estimated $${preview.value.estimate.amount_usd.toFixed(4)} from ${preview.value.estimate.sample_count} comparable run(s).`}
                  </Text>
                  <Text mt="xs" size="sm">
                    ${preview.value.caps.max_budget_usd} per turn · ${preview.value.caps.spend_cap_usd} set cap · {preview.value.pricing.basis}
                  </Text>
                  <Text c="dimmed" mt="xs" size="xs">
                    {preview.value.case_identities.length} selected cases for {selection.target_kind}:{selection.target_ref}. Confirmation is bound to this exact request.
                  </Text>
                </div>
                <Button disabled={busy || requesting} loading={requesting} onClick={launch}>
                  Confirm and launch
                </Button>
              </Group>
            </Paper>
          )}
        </Stack>
      </Paper>

      {snapshot && phase !== "starting" && (
        <Paper aria-labelledby="native-progress-title" p="xl" withBorder>
          <Group justify="space-between">
            <div>
              <Title id="native-progress-title" order={3}>Case evidence</Title>
              <Text c="dimmed" size="sm">
                {snapshot.settled_count} of {snapshot.eligible_count} cases have reusable verdicts.
              </Text>
            </div>
            {snapshot.interrupted && <Badge color="yellow" variant="light">interrupted · evidence kept</Badge>}
          </Group>
          <Progress aria-label="Settled native acceptance cases" mt="lg" value={completion(snapshot)} />
          {snapshot.warnings.map((warning) => (
            <Alert color="yellow" key={warning} mt="md" title="Progress limitation">{warning}</Alert>
          ))}
          <Stack gap="sm" mt="lg">
            {snapshot.cases.map((item) => (
              <Paper key={item.id} p="md" withBorder>
                <Group align="flex-start" justify="space-between" wrap="nowrap">
                  <div>
                    <Group gap="xs">
                      <Text fw={650}>{item.id}</Text>
                      <Badge color={verdictColor(item.status)} variant="light">{item.status}</Badge>
                    </Group>
                    <Text c="dimmed" mt="xs" size="sm">{caseDescriptions.get(item.id)}</Text>
                    {item.observation && <Text mt="sm">{item.observation}</Text>}
                    {(item.seconds !== null || item.sessions !== null || item.spend_usd !== null) && (
                      <Text c="dimmed" mt="xs" size="xs">
                        {item.seconds !== null ? `${item.seconds}s` : "duration unknown"} · {item.sessions ?? "?"} sessions · {item.spend_usd !== null ? `$${item.spend_usd.toFixed(4)}${item.price_as_of ? ` · priced ${item.price_as_of}` : ""}` : "spend unknown"}
                      </Text>
                    )}
                  </div>
                  {item.status === "failed" && (
                    <Button size="xs" variant="default" onClick={() => onRetryFailed(snapshot.selection, item.id)}>
                      Retry with fresh log
                    </Button>
                  )}
                </Group>
              </Paper>
            ))}
          </Stack>
          <Group justify="space-between" mt="lg">
            <Code>{snapshot.command}</Code>
            <Button
              disabled={!snapshot.resume_cases.length || busy}
              variant="default"
              onClick={() => {
                const stored = selectionForResume(snapshot);
                setSelection(stored); onResume(stored);
              }}
            >Resume {snapshot.resume_cases.length} eligible</Button>
          </Group>
          {failed.length > 0 && (
            <Text c="dimmed" mt="sm" size="xs">Failed verdicts stay in a resumed log; retry uses a fresh log.</Text>
          )}
        </Paper>
      )}
    </Stack>
  );
}
