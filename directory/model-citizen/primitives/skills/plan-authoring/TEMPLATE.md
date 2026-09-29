# <What gets built — a noun phrase, not a sentence>

> **Verdict.** <What this builds, one sentence.> <The mechanism, one sentence.>
> **Effort** <n> · **Risk** <low/med/high — the one reason> · **Blast radius** <what it can break>

## At a glance

- **Outcome** — What is true when this is done
- **Approach** — The mechanism in one line
- **Touches** — Repos, surfaces, file count
- **New deps** — Name + license, or None
- **Not in scope** — The 2–3 things a reader would assume are included
- **Exit test** — How we know it worked
- **Open question** — The one thing still unresolved, pointing at its decision number

## System design

```text
Source ── what moves ──▶ *Transform ──▶ Sink
```

<One caption line. `*` = new or changed.>

## Steps

1. **<The cheapest thing that could invalidate the rest>** — [file.ts](src/file.ts).
   *Exit:* `pnpm test x` passes.
2. **[<Step with detail>](#step-2--title)** — <what it touches>.
   *Exit:* <a command, a render, or a passing assertion>.

## Decisions for the reviewer

> **1. <Question, stated so it can be answered by number.>**
> *Recommend* <option> — <the reason, one clause>.
> *Alternative* <option> — <its honest case>.

## Risks

- **<Trigger>** — <what we do when it fires>.

---

# Addendum

Everything the implementing agent needs and the reviewer does not. Nothing above the rule is
repeated here.

## Step 2 — <title>

## Context and background

## Evidence and verification

## Deferred, and why
