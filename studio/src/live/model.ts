export type LiveEventKind = "change" | "gap" | "snapshot";

export interface LiveUpdate {
  kind: LiveEventKind;
  instance_epoch: string;
  sequence: number;
  topics: string[];
  paths: string[];
}

const MAX_PENDING_DOMAINS = 16;
const MAX_PENDING_PATHS = 256;

export class PendingUpdates {
  private domains = new Map<string, LiveUpdate>();

  get size(): number {
    return this.domains.size;
  }

  push(event: LiveUpdate): void {
    if (event.kind !== "change") {
      this.domains.clear();
      this.domains.set("*", { ...event, paths: event.paths.slice(-MAX_PENDING_PATHS) });
      return;
    }
    if (this.domains.has("*")) return;
    for (const topic of event.topics.slice(0, MAX_PENDING_DOMAINS)) {
      if (this.domains.size >= MAX_PENDING_DOMAINS && !this.domains.has(topic)) continue;
      const previous = this.domains.get(topic);
      const paths = previous?.paths.length === 0 || event.paths.length === 0
        ? []
        : [...new Set([...(previous?.paths ?? []), ...event.paths])];
      this.domains.set(topic, {
        ...event,
        topics: [topic],
        paths: paths.length > MAX_PENDING_PATHS ? [] : paths,
      });
    }
  }

  drain(): LiveUpdate | null {
    const pending = coalesceUpdates([...this.domains.values()]);
    this.domains.clear();
    return pending;
  }
}

export class OrderedEventGate {
  private epoch = "";
  private sequence = -1;

  accepts(event: LiveUpdate): boolean {
    if (!event.instance_epoch || !Number.isSafeInteger(event.sequence) || event.sequence < 0) return false;
    if (event.instance_epoch !== this.epoch) {
      this.epoch = event.instance_epoch;
      this.sequence = event.sequence;
      return true;
    }
    if (event.sequence <= this.sequence) return false;
    this.sequence = event.sequence;
    return true;
  }
}

export function parseLiveUpdate(value: string): LiveUpdate | null {
  try {
    const event = JSON.parse(value) as Partial<LiveUpdate>;
    if (!(["change", "gap", "snapshot"] as unknown[]).includes(event.kind)
        || typeof event.instance_epoch !== "string"
        || !Number.isSafeInteger(event.sequence) || Number(event.sequence) < 0
        || !Array.isArray(event.topics) || event.topics.some((item) => typeof item !== "string")
        || !Array.isArray(event.paths) || event.paths.some((item) => typeof item !== "string")) return null;
    return event as LiveUpdate;
  } catch {
    return null;
  }
}

export function coalesceUpdates(events: LiveUpdate[]): LiveUpdate | null {
  const latest = events.reduce<LiveUpdate | null>(
    (newest, event) => !newest || event.sequence > newest.sequence ? event : newest, null);
  if (!latest) return null;
  return {
    kind: events.some((event) => event.kind === "gap") ? "gap" : latest.kind,
    instance_epoch: latest.instance_epoch,
    sequence: latest.sequence,
    topics: [...new Set(events.flatMap((event) => event.topics))].sort(),
    paths: events.some((event) => event.paths.length === 0)
      ? []
      : [...new Set(events.flatMap((event) => event.paths))].sort(),
  };
}

export function updateTouchesTopics(event: LiveUpdate, topics: ReadonlySet<string>): boolean {
  return event.kind !== "change" || event.topics.some((topic) => topics.has(topic));
}

export function updateTouchesPaths(event: LiveUpdate, paths: ReadonlySet<string>): boolean {
  if (event.kind !== "change" || event.paths.length === 0) return true;
  for (const changed of event.paths) {
    for (const visible of paths) {
      if (visible === changed || visible.startsWith(`${changed}/`)) return true;
    }
  }
  return false;
}
