// The Sessions page's two charts: calls per 50k of context across the window, and one session's
// context per call. Both draw the cost stance's 160k and 400k lines; plain SVG, so they render
// in the unit tests and need no chart library.
import { band, count, tokens, type Bucket, type CallPoint, type ContextLines } from "./sessionsModel";

import "./sessions.css";

const WIDTH = 720;
const HEIGHT = 200;
const PAD = { top: 18, right: 12, bottom: 24, left: 44 };
const INNER_W = WIDTH - PAD.left - PAD.right;
const INNER_H = HEIGHT - PAD.top - PAD.bottom;

/** Calls per 50k bucket, the lines drawn at their place on the context axis. */
export function ContextHistogram({ buckets, lines }: { buckets: Bucket[]; lines: ContextLines }) {
  const peak = Math.max(1, ...buckets.map((b) => b.calls));
  const step = buckets.length ? (buckets[1]?.from ?? 50_000) - buckets[0].from : 50_000;
  const span = buckets.length * step;
  const slot = INNER_W / Math.max(1, buckets.length);
  const x = (tokensAt: number) => PAD.left + (Math.min(tokensAt, span) / span) * INNER_W;
  const total = buckets.reduce((sum, b) => sum + b.calls, 0);
  return <svg className="sessions-chart" role="img" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} width="100%"
    aria-label={`Context per call: ${count(total)} calls in 50k buckets, with the ${tokens(lines.warn)} and ${tokens(lines.limit)} lines`}>
    {buckets.map((b, i) => {
      const h = (b.calls / peak) * INNER_H;
      return <rect key={b.from} data-bucket={b.from} data-band={band(b.from + 1, lines)} className="sessions-bar"
        x={PAD.left + i * slot + 1} y={PAD.top + INNER_H - h} width={Math.max(1, slot - 2)} height={b.calls ? Math.max(2, h) : 0}>
        <title>{`${tokens(b.from)}${b.to === null ? "+" : ` to ${tokens(b.to)}`}: ${count(b.calls)} calls`}</title>
      </rect>;
    })}
    <Line x={x(lines.warn)} label={`${tokens(lines.warn)} line`} kind="warn" />
    <Line x={x(lines.limit)} label={`${tokens(lines.limit)} hand-off`} kind="limit" />
    {[0, 200_000, 400_000, span].map((at) => <text key={at} className="sessions-axis" x={x(at)} y={HEIGHT - 6} textAnchor="middle">
      {at === span ? `${tokens(at)}+` : tokens(at)}
    </text>)}
  </svg>;
}

function Line({ x, label, kind }: { x: number; label: string; kind: "warn" | "limit" }) {
  return <g className="sessions-line" data-line={kind}>
    <line x1={x} x2={x} y1={PAD.top - 4} y2={PAD.top + INNER_H} />
    <text x={x + 4} y={PAD.top - 6}>{label}</text>
  </g>;
}

/** One session's context per main-thread call, the two lines across it and each reset marked. */
export function ContextPerCall({ calls, lines }: { calls: CallPoint[]; lines: ContextLines }) {
  const top = Math.max(lines.limit * 1.15, ...calls.map((c) => c.context_tokens));
  const slot = INNER_W / Math.max(1, calls.length);
  const y = (n: number) => PAD.top + INNER_H - (n / top) * INNER_H;
  const marked = calls.filter((c) => c.event);
  return <svg className="sessions-chart" role="img" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} width="100%"
    aria-label={`Context per call: ${count(calls.length)} calls against the ${tokens(lines.warn)} and ${tokens(lines.limit)} lines, ${marked.length} resets marked`}>
    {calls.map((c) => <rect key={c.index} data-call={c.index} data-band={band(c.context_tokens, lines)} className="sessions-bar"
      x={PAD.left + c.index * slot} y={y(c.context_tokens)} width={Math.max(0.5, slot - (slot > 3 ? 1 : 0))}
      height={PAD.top + INNER_H - y(c.context_tokens)}>
      <title>{`Call ${c.index + 1}, turn ${c.turn + 1}: ${tokens(c.context_tokens)}`}</title>
    </rect>)}
    {([["warn", lines.warn, `${tokens(lines.warn)} line`], ["limit", lines.limit, `${tokens(lines.limit)} hand-off`]] as const).map(([kind, at, label]) =>
      <g key={kind} className="sessions-line" data-line={kind}>
        <line x1={PAD.left} x2={PAD.left + INNER_W} y1={y(at)} y2={y(at)} />
        <text x={PAD.left + INNER_W} y={y(at) - 4} textAnchor="end">{label}</text>
      </g>)}
    {marked.map((c) => <g key={`event-${c.index}`} className="sessions-event" data-event={c.event} data-at={c.index}>
      <line x1={PAD.left + c.index * slot} x2={PAD.left + c.index * slot} y1={PAD.top} y2={PAD.top + INNER_H} />
      <text x={PAD.left + c.index * slot + 3} y={PAD.top + 10}>{c.event === "reseed" ? "reseed" : "compact"}</text>
    </g>)}
    {[0, lines.warn, lines.limit].map((at) => <text key={at} className="sessions-axis" x={PAD.left - 6} y={y(at) + 4} textAnchor="end">{tokens(at)}</text>)}
  </svg>;
}
