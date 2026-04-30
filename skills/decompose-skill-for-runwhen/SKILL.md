---
name: decompose-skill-for-runwhen
description: "Assess an existing AI skill, agent procedure, or investigation runbook and decompose it into a RunWhen-native design (tasks + commands + rules + KB articles), with honest comparison and a phased rollout. Use when: (1) A user shares a Claude/Cursor skill, sub-agent prompt, runbook, or investigation playbook and asks how it would map onto RunWhen, (2) Evaluating whether to migrate or hybridize an existing investigation pattern, (3) Producing a working-session document or deck that compares an existing approach to a corpus + chat design, (4) Sizing the build effort and rollout for converting an investigation pattern into a RunWhen workspace, or (5) The user asks to 'decompose', 'translate', 'map', 'migrate', or 'assess' an existing skill / agent / runbook for RunWhen."
---

# Decompose an Existing Skill for RunWhen

End-to-end framework for turning an existing AI/automation pattern into a
proposed RunWhen design — with an honest, side-by-side comparison the original
owner can argue with.

The goal is **not** to convince the owner that RunWhen is better. It's to
produce a clear-eyed assessment of where each architecture wins and to give
them a phased rollout if they decide to invest.

## When to use

- A customer or teammate shares a Claude/Cursor skill, sub-agent system, runbook, or wiki investigation procedure and asks "how would we do this on RunWhen?"
- Evaluating whether an investigation pattern is a corpus problem, a task problem, or both
- Producing a working-session artifact (Linear/Confluence doc + optional deck) for the pattern's owner
- Sizing the build effort honestly so the owner can compare costs

> **Status:** this skill is a starting point. The framework reflects one
> worked example to date and is expected to evolve as more patterns are
> assessed. Treat the steps below as a strong default, not a contract.

## Step 0 — Reframe before decomposing

Investigation skills almost always look like **pull, on demand**: a question
arrives → spin up agents → fetch live → synthesize. RunWhen is **push, on
schedule**: tasks run on cadence → reports indexed + issues raised → chat
answers over the corpus. **Don't try to map agents 1:1 to tasks.**

The right question is:

> Of the data this skill fetches per question, **which slices change slowly
> enough to fetch on a schedule and search on demand?** Those become tasks.
> The rest stays in the original investigation flow (or moves to chat
> synthesis over the corpus).

| Skill phase / sub-agent | RunWhen analog |
|---|---|
| Live data-fetch agents | Scheduled **tasks** that emit indexed reports |
| Validation / second-opinion agents | A chat **rule** ("treat claims as hypotheses; cite file paths") |
| Historical lookup (tickets / past asks) | A scheduled **task** snapshotting the source of record |
| Telemetry queries | Scheduled **task(s)** cataloging metrics + a **command** for ad-hoc joins |
| Synthesis / recap | `workspace_chat` over the indexed corpus + KB articles |

If a phase doesn't fit any of these, it stays in the original skill. Most
real-world skills decompose 70–90% of their workload onto RunWhen and leave
the rest as freeform exploration.

## Step 1 — Decompose into the four RunWhen primitives

Walk through the original pattern phase by phase. For each phase, pick the
primitive(s) it maps to.

### Tasks (data layer)

- **One task per data source × cadence.** Don't bundle "all GitHub stuff" into one task — split by what changes weekly vs. daily vs. every 15 minutes.
- **Cadence matches change rate.** Code structure: weekly. Tickets: 6h–daily. Monitor state: 15 min. Metric volume: daily.
- **Report = NDJSON to stdout.** One JSON object per record, prefixed with a YAML-frontmatter-style header (`# task: …`, `# generated_at: …`, `# record_count: …`). Indexable, grep-able, deterministic.
- **Issues = signals, not data.** Bulk inventory goes in the report. Issues only fire when something **warrants a UI surface**: stalled customer ask, alerting monitor, silent metric, deleted-without-deprecation API surface.
- **ResourcePath:** `custom/<domain>/<source>/<name>` (server enforces `custom/` prefix). See `configure-resource-path` skill.

### Commands (composition layer)

- One **headline command** that mirrors the original skill end-to-end (e.g. `/product-question`, `/incident-postmortem`).
- **Narrower commands** for the sub-shapes the headline calls (e.g. `/customer-ask`, `/api-surface`, `/feature-coverage`).
- Each command **encodes the original procedure**: which task reports to search, in what order, with what freshness policy, and how to cross-reference.
- See `manage-commands` skill.

### Rules (interpretation layer)

- Translate the **guardrails** from the original skill's "synthesis" or "recap" instructions.
- Common shapes: cite-or-qualify; separate data presence from product exposure; check prior history before declaring net-new; concision over grammar; off-topic guardrail; instrumentation ≠ runtime emission.
- See `manage-rules` skill.

### Knowledge Base articles (vocabulary layer)

- The **operational vocabulary** the chat agent needs: domain glossary, naming conventions, ownership map, link templates for the source systems.
- One article per concept the chat agent must know about to interpret task reports correctly.
- See `manage-knowledge` skill.

## Step 2 — Apply two structural patterns

### Pattern A — Issues vs. Report

| Belongs in the report (stdout) | Belongs as an issue (FD3 / return) |
|---|---|
| Bulk inventory — every surface, model, ticket, metric | A genuine signal — alerting monitor, stalled ask, silent metric |
| Counts and breakdowns | Threshold breach with a clear "next steps" |
| Reference catalogs | Contract change (API removal, schema break) |
| Snapshots that change slowly | Anomaly relative to the prior run |

> If you find yourself reaching for sev4 to "make data queryable", stop —
> bulk data goes in the report. Issues should be silent in steady state.

### Pattern B — Dual-mode SLXs (latency-sensitive only)

When chat invokes `run_slx` mid-question, **aim for <60s; past ~2 min users
context-switch.** Not a hard cap — scheduled runs are uncapped — just a
guideline for interactive responsiveness.

For tasks whose **scheduled** mode legitimately takes longer (whole-org
GitHub scans, full Datadog metric catalog), commit **two SLXs from the same
script** with different env-var bindings:

| | Scheduled `-full` mode | On-demand `-fast` mode |
|---|---|---|
| Scope | Whole inventory | Single repo / single metric / single keyword |
| Aggregation | Full per-repo summaries, 30d cross-cuts | Single answer, no aggregation |
| Tools | `git clone` + `rg` | `gh search code` only — no clone |
| Result cap | Unbounded | `MAX_RESULTS=50` style |
| ResourcePath | `…-full` | `…-fast` |

Don't pre-emptively dual-mode every task. If the scheduled run already fits
under 60s (most Linear / Datadog API-only tasks), one SLX is fine.

## Step 3 — Build the honest comparison

Produce a table comparing the original pattern vs. the proposed RunWhen
design. **Score every row** — do not let RunWhen win every column.

Standard dimensions to evaluate:

| Dimension | What to compare |
|---|---|
| Time to answer (P50) | Wall-clock per question |
| Latency budget on the on-demand path | Cap that exists vs. scheduled-corpus that's already there |
| Data freshness | Live vs. cadence-bounded |
| LLM cost per question | Multi-agent investigation vs. synthesis turn + amortized backend indexing |
| Reproducibility | Can two engineers asking the same question get the same cited evidence? |
| Audit trail | Persistent run reports vs. ephemeral agent transcripts |
| Failure mode | Hard fail per question vs. graceful degradation to last-good corpus |
| Concurrency | Per-asker fetches vs. shared-corpus reuse |
| Quality of evidence | Bounded by inventory completeness vs. agent prompt + API state |
| Open-ended exploration | Pivoting investigations vs. known-shape questions |
| Extending to a new question shape | New skill / sub-agent vs. new command + 1–2 KB articles |
| Workflow integration | Lives in Claude/Cursor vs. UI / Slack / any PAPI consumer |
| Surfacing genuine alerts | Read-only skill vs. continuous issues feed |
| Build effort | One markdown file vs. ~9 tasks + 5 commands + 7 rules + 6 KB |

The original wins **at least** open-ended exploration and build effort —
say so explicitly. The point is to give the owner a real choice, not to
sell.

### Recommend a hybrid by default

The standing recommendation is rarely "replace". It's **"absorb the
recurring 80% so the original is free to handle the novel 20% it does
best."** State this explicitly, with concrete examples of which question
shapes go to which.

## Step 4 — Frame setup complexity honestly

Build a side-by-side complexity table:

| | Original skill | RunWhen design |
|---|---|---|
| Net-new artifacts | 1 markdown file | N tasks + M commands + K rules + L KB |
| Lines of code | ~XX lines of prompt | ~1500–2500 lines of bash/python authored by agent from prompts |
| Required infra | Existing AI stack + sub-agents | RunWhen workspace + runner with outbound + secrets |
| How the building gets done | Hand-author the markdown | `runwhen-task-builder` agent writes scripts from prompts; engineer reviews/tunes |
| Time to first answer | Hours | **~1 engineer-week with AI-assisted authoring; same-day MVP**. Without AI, multiply ~2× |
| Maintenance | Re-tune per upstream API change | Schema-stable; touch tasks on upstream API change |
| Onboarding new team | Re-deploy skill, ensure stack | Provision workspace, point at sources, copy commands+KB |

> **Always include the AI-assisted authoring assumption.** Without it, the
> RunWhen design reads as ~5–10× the build effort. With `runwhen-task-builder`,
> the gap is closer to ~3×, paid back through shared use.

## Step 5 — Phased rollout

Don't ship all tasks at once. Sequence them so each phase **answers one
question shape end-to-end** before broadening.

Standard phase template:

| Phase | Scope | Why this order | Time (with AI-assisted authoring) |
|---|---|---|---|
| **0 — Prereqs** | Runner + secrets + decisions | Unblocks everything | ~half day |
| **1 — Cheapest, highest-value source** (often a tickets / Linear / Jira snapshot) | 1–2 tasks + 1 command + 2 KB | Fastest path to a usable answer; **MVP boundary** | ~half day |
| **2 — Lightweight runtime signal** (often monitor state + dashboard catalog) | 2–3 tasks + 1 command + 2 KB | Easy wins on continuous-signal value | ~1 day |
| **3 — The bulk** (often code-scan inventory tasks) | 3 tasks (often dual-mode) + 2 commands + 2 rules + 2 KB | Where the engineering effort sits | ~2 days |
| **4 — Synthesis + glue** | Headline command + remaining rules + threshold tuning | Composes prior phases | ~half day |
| **5 — Adoption** | Slack wiring + handbook + threshold tuning | Ongoing | ongoing |

Total: **~4 working days** with AI-assisted authoring; **same-day MVP**
through Phase 1. Without AI, double it.

State the **MVP boundary explicitly** — usually after Phase 1 or 2. Tell the
owner: "don't commit to the full build until the MVP proves the corpus model
works for your team."

## Step 6 — Author task-builder prompts

For each proposed task, produce a **one-paragraph prompt** that the
`runwhen-task-builder` agent (or `claude --skill build-runwhen-task`) can
consume directly. This is what makes the timing estimates real — the
operator hands the agent prompts, not blank scripts.

Each prompt must include:

| Element | Example |
|---|---|
| What to emit | "one NDJSON record per surface with: repo, file_path, surface_kind, …" |
| Header lines | "Prefix with `# task: …, # generated_at, # record_count`" |
| What fires issues | "sev3 when surface present in prior run is gone without a deprecation marker; sev2 on scan failure" |
| CLI requirements | "`gh, rg, jq, git`" |
| Secret env vars | "`github_token` — fine-grained PAT, read-only" |
| ResourcePath | "`custom/<domain>/<source>/<name>-full`" |
| Dual-mode trigger (if applicable) | "Also commit a `-fast` sibling: `REPO_LIST=one-repo + …_FILTER`, swap clone for `gh search code`, finishes in <60s" |

Keep prompts at one tight paragraph per task — the agent enforces the rest
of the contract automatically (return type, FD3 issue format, severity
schema). See `build-runwhen-task` for the contract details the agent
already knows.

## Step 7 — Produce deliverables

Produce **at least** the document. The deck is optional but strongly
preferred for a working session with the original owner.

| Deliverable | When | What it contains |
|---|---|---|
| Linear / Confluence doc | Always | The full assessment: reframe, decomposition, structural patterns, comparison, complexity, rollout, prompts, original embedded as appendix |
| Working-session deck (HTML) | When the audience is the original owner | Same structure, slide-form, one slide per major section. Include the comparison table, dual-mode latency story, and rollout timeline |
| Open-questions list | Always | Decisions that need the owner in the room (scope, sources of truth, threshold defaults) |

## Anti-patterns

| Don't | Do instead |
|---|---|
| Map every sub-agent to a task | Reframe first — most sub-agents fold into chat or rules, not tasks |
| Use sev4 issues to make data queryable | Put bulk data in the report; reserve issues for genuine signals |
| Pre-emptively dual-mode every task | Only when scheduled run >60s and the task is on the on-demand path |
| Claim "zero LLM cost" | Be precise: no LLM cost on the data-fetch path; chat synthesis + backend indexing still cost — but amortized across all askers |
| Frame timeouts as a "hard ceiling" | Treat <60s and ~2 min as guidelines for interactive responsiveness; scheduled runs are uncapped |
| Estimate timelines without AI-assisted authoring | Always state the assumption; without `runwhen-task-builder`, timings ~2× |
| Pitch RunWhen as the obvious replacement | Recommend a hybrid; the original keeps earning its keep on novel deep-dives |

## Skill chain (downstream)

After producing the assessment, the owner (or their AI agent) typically
proceeds through:

| Step | Skill |
|---|---|
| Search registry for existing codebundles | `find-and-deploy-codebundle` |
| Build each proposed task | `build-runwhen-task` (one task per task-builder prompt) |
| Configure metadata for indexing | `configure-resource-path`, `configure-hierarchy` |
| Wire commands, rules, KB | `manage-commands`, `manage-rules`, `manage-knowledge` |
| Verify end-to-end | `verify-mcp-setup`, then run-by-run via `run-existing-slx` |

## Definition of done

The assessment is finished when the owner can answer **yes** to all of
these:

- [ ] I can name 2–3 question shapes my skill answers most often
- [ ] I see which shapes the corpus answers in <10s and which still need the original
- [ ] I have a side-by-side comparison where the RunWhen design does **not** win every row
- [ ] I have a phased rollout with an explicit MVP boundary
- [ ] I have a one-paragraph task-builder prompt for each proposed task
- [ ] I know what the open decisions are before Phase 0
- [ ] I have a hybrid recommendation, not a replacement pitch
