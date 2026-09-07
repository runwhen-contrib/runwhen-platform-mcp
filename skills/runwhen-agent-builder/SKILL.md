---
name: runwhen-agent-builder
description: "Turn an abstract requirement into a working, tested, scheduled RunWhen assistant — tasks, rules, commands, knowledge and a persona — plus a build ledger and a credential-gap report. Use when: (1) A customer or stakeholder has stated a use case ('certificate lifecycle automation', 'capacity forecasting') and it needs to become real RunWhen objects, (2) Standing up a new AI agent, assistant or persona for a PoC, (3) The user asks what tasks, rules or commands a use case would need, (4) Auditing whether a workspace's credentials can actually support a use case, (5) Resuming or tearing down a build started earlier, or (6) The user asks to build, scope or plan a RunWhen agent."
---

# RunWhen Agent Builder

Turns "we want AI-driven certificate management" into objects that exist, run,
and are proven to be findable.
**The mental model.** The expensive half of a two-model split: you build and
verify capabilities once, under test; the cheap runtime agent consumes them
thousands of times. A task committed without being executed is a liability.

**A requirement is a hypothesis about data that may not exist.** Most of the work
is finding what is reachable, then designing against that. You cannot tell a
design is worthless by thinking harder about it.

**The shape of every agent you build:**

```
1 breadth task per resource path      scheduled, unattended   "where should I look?"
2-4 depth tasks, pivot as runtime var  NOT scheduled          "what is wrong with this one?"
1 assistant       pinned to those tasks
<=5 rules         persona-scoped, always-on, scarce; N commands, free
KB articles       retrieved on relevance
+ build ledger, gap report, handover one-pager, teardown path
```

Depth tasks exist for the **conversation the report starts**, not the report: the
pivot is a runtime variable, not a new task per subject.

**Use this when** the user has an **objective**, not an object. If they already
know they want a task that checks PVC utilisation, build that task instead.

## References — read the one you need, when you need it

| File | For |
|---|---|
| `references/object-model.md` | exact API shapes — parameters, variable families, visibility boundary, gotchas |
| `references/task-contract.md` | breadth vs depth, resource-path boundaries, name-carrying, determinism |
| `references/investigation-surface.md` | Phase 2 step 1 — research what complete looks like |
| `references/decomposition.md` | Phase 2 — the five filters, worked examples, plan format |
| `references/report-contract.md` | Phase 5/6 — what the scheduled command's report must look like |
| `references/eval-protocol.md` | Phase 7 — writing and reading the eval |
| `references/build-ledger.md` | state during the build, manifest and teardown after |
| `references/gap-report-template.md` | the customer-facing gap report |
| `templates/handover.html` | Phase 7 — the human-readable one-pager |
| `scripts/probe-template.py` | starting point for Phase 1 recon |
| `scripts/check.sh` | acceptance checks for this skill; run before proposing edits |

**Write the ledger as you go, not at the end** (`references/build-ledger.md`) —
its required fields are what stop a phase being skipped silently.

---

## Phase 0 — Preflight and scope

`list_workspaces` → `get_workspace_context` → `get_workspace_locations` →
`get_workspace_secrets`.

Confirm a runner is `online` with a recent heartbeat and the caller has admin or
readwrite. If `auto_resolves` is true, omit `location` downstream. `no_context`
(no `RUNWHEN.md`) is not a blocker — proceed and put it in the gap report as the
cheapest quality win available.

**Stop and report if no healthy runner exists.** Every later phase executes
scripts; without one you would be committing untested code.

Then ask what only the human can answer — environment and scope, where history
lives if the requirement implies a trend, delivery target and cadence.

**Ask for context sources in your first reply.** Do not wait on the answer to
start probing; do not start designing until it arrives. Work all four and name
the ones you do not need rather than skipping them:

- [ ] **IaC / GitOps repos** — ask for *paths*, and **which repo, branch and
      path actually reconciles this environment**; the wrong overlay answers
      confidently about a cluster nobody runs.
- [ ] **Application repos** — only for code-level behaviour. Say so when not.
- [ ] **Docs, runbooks, KB notes, `RUNWHEN.md`, prior agents** — someone may have
      written down what you are about to rediscover badly.
- [ ] **Anything else in scope** — dashboards, tickets, a design doc.

The probe sees only what is reachable from inside; ceilings, intent and ownership
are declared outside it. **What you are told is a claim, not ground truth** — the
probe verifies it like anything else. In one reference build the documented
observability stack turned out to be empty.

## Phase 1 — Recon

Establish what the workspace can actually *reach*, not what the customer says they
own. Recon runs on **ephemeral `run_script_and_wait` scripts**, never committed
tasks — nothing is written to the workspace here. Start from
`scripts/probe-template.py`.

**1a. Existing inventory.** `get_workspace_slxs` (page papi directly at scale),
plus rules, commands, assistants and KB. First, because it is where working
credentials are visible, it is the teardown baseline, and Phase 2 checks
disjointness against it.

**1b. Merged secret inventory.** Required output. Two sources disagree: the
**vault** (`get_workspace_secrets` — advisory, it *synthesises* workspaceKeys that
may not exist) and **runner-side k8s secrets** (`secretsProvided` on the SLXs from
1a — authoritative, invisible to the vault). Merge into one table keyed by
workspaceKey with provenance and a verified column, probe each key once, design
only from verified rows. A key in the vault but in no SLX looks available and
usually is not. Procedure in `references/object-model.md`.

**1c. Probe reach** with verified credentials, recording **every permission denial
verbatim** — the difference between "needs more access" and an actionable grant
request. **Never conclude a platform is unreachable from a probe that did not
carry its credential**, nor from a query you have not validated returns what you
think (*Queries that lie*, `references/object-model.md`). Print the identity
before blaming permissions — a valid credential in the wrong project returns 403
and reads exactly like a missing grant.

**Test the retention ladder** at −7d, −30d, −90d, −365d for anything
trend-shaped. It decides whether a trend claim is possible at all, and no amount
of reasoning substitutes for asking. Expect the first probe to fail on auth or
syntax — that is the probe working.

## Phase 2 — Decompose, then STOP

**Build the investigation surface first.** Research each reachable platform's own
troubleshooting docs and derive what *complete* looks like — existing coverage
tells you what you have, never what is missing, and your own recall is the weakest
source available. Coverage matrix, one row per investigation step, marked
deployed / registry / **GAP**. Method and the **layer trap** in
`references/investigation-surface.md`.

**Then existing coverage, then the registry.** Coverage is usually partial, which
means the job is `deploy_registry_codebundle` to the uncovered scope, not
authoring. Mark in the plan which objects are which.

Apply the five filters to the **GAP rows** (`references/decomposition.md`):
Reuse → Reachability → Environment-specificity → Consolidation → Disjointness.
Name the two shapes that never survive contact — write actions, and external
systems — before the customer anchors on getting them.

**Two beats, not one.** First put the material forks to the human as concrete
options — coverage, breadth-vs-depth, reuse-vs-extend, detail, issue policy,
confidence floor. Two to four questions, each option carrying what it costs, one
recommended. **A plan is one option wearing the clothes of an answer**: by the
time it is written every fork is silently taken, and their only lever is to
reject a finished thing. Then build the plan from the answers in that file's
format — kind, name, the question it answers, the credential it needs — recording
what was offered and who decided each fork.

**STOP. Write nothing until the plan is approved.** After approval the build runs
to completion unattended, so this is the user's chance to redirect.

**With no human available** the context slots and every fork become `BLOCKED —
human unavailable` with a written question and assumption each, and this gate is
a **hard stop that delivers the plan** — never self-approval. See
`references/build-ledger.md`.

**Re-gate on material change.** An approval covers the plan that was approved. If
anything later changes the portfolio by more than one object — a platform turns
out reachable, coverage is wider than you thought, a credential fails mid-build —
present the revised plan and get it approved again. A stale approval is worse
than none, because it looks like consent. Unattended, a re-gate **drops that
object and continues**, recorded in the ledger and gap report.

## Phase 3 — Breadth task, to completion

`validate_script` → `run_script_and_wait` → **verify the output contract** →
`commit_slx` → wait 1–3 minutes → **re-run through the committed SLX** →
**retrieval check**.

Verifying the output contract means: `run_metadata` present, payload a sane size,
the right issues fired, and the report body in the severity-4 issue rather than
only on stdout. **`run_slx` can return an empty `issues` array while they are
still landing** — judge by the SLX's issue list, never the run call. The re-run
after commit is not ceremony: inline `secret_vars` and resolved `secretsProvided`
are different mechanisms, and auth is unproven until the committed object runs.

Write the script to a file and pass `script_path`. Cap output with `MAX_ROWS`-style
config from the first run, not after it overflows.

**Do not start Phase 4 until this passes.** Capabilities committed without
execution-verification measurably perform *worse* than having none. Proving one
task end to end also proves the credential, the runner, the secret mapping and
the script pattern Phase 4 will copy.

## Phase 4 — Depth tasks, in parallel

Now fan out — one subagent per depth task. Give each the working breadth task as a
template, the confirmed `secret_vars` mapping, and its pivot. Every depth task
takes its pivot as a validated runtime variable, plus `DETAIL_LEVEL` as an enum
(`concise`/`detailed`, default `concise`), and gets its own retrieval check after
commit. **None of them go on the schedule.**

**Break-glass tasks are built here, not in recon** — a deliverable for the
runtime agent, not a build instrument. One per verified credential platform, CLI
invocation as a runtime variable, with the Non-negotiables mitigations.

Registry deploys need `resource_path` and `hierarchy`; both are optional on
`deploy_registry_codebundle`, and an SLX without them lands outside the resource
tree under `/resources/_unscoped/`.

## Phase 5 — Context layer

- **KB articles** — durable facts, scoped by `resource_paths` to your `custom/`
  paths. Unlimited, retrieved on relevance. Never current readings.
- **Rules** — **≤5, `scope_type=persona`, no exceptions.** Every active in-scope
  rule is concatenated into the prompt every turn with no cap, ranking or
  truncation, so rules crowd each other and every other assistant. **Never name
  an `env_var` in one** — the agent cannot see config.
- **Commands** — persona-scoped, **one per job, not one per task.** The report
  command carries the drill-down guidance too: the follow-up happens in the
  session the report started, and a second command splits one workflow in two.

**Write the command prompt from the six-part recipe in
`references/report-contract.md`.** The report contract is enforced only there —
the runtime agent cannot read this skill. Named `Summary` and `What to do`
sections, word caps as numbers not adjectives, body banded by urgency rather than
by resource type, and a flag table saying what each flag licenses a claim to be.
Save the finished prompt into the ledger. Say nothing about delivery.

## Phase 6 — Assemble

`create_assistant` on a first run, **`update_assistant` if the persona exists** —
it is a full upsert that silently resets omitted fields. Pin `searchFilters` to
your SLX group and task tags, excluding break-glass.

Then the scheduled breadth command: `cron_schedule`, `sink_configs`, `run_as_user`,
`assistant_name`, **`auto_approve_readonly: true`** (false means the scheduled
agent refuses to run tasks and only recommends), and **`schedule_paused: true`** —
unpausing starts outward-facing delivery and is the human's call.

## Phase 7 — Evaluate, then leave it behind

Run the eval in `references/eval-protocol.md`: 3–5 realistic questions through
`workspace_chat(persona_name=...)`, never naming the task. Then
complete the ledger into a manifest, write the gap report, and render
`templates/handover.html` into the build directory — always, including for a
build that failed partway, where they matter more. The ledger is written for a
resuming agent; the one-pager is the only artifact written for a human.

---

## Non-negotiables

| Rule | Why |
|---|---|
| Ask for context sources in the first reply | The probe cannot see what is declared outside the environment |
| Probe before designing; build the plan on verified rows only | A design on assumed data is worthless and looks fine |
| One gate, at Phase 2. Nothing written before it | The user's only chance to redirect |
| `search_registry` before authoring anything custom | A duplicate of a maintained bundle is pure liability |
| Phase 3 completes before Phase 4 fans out | Unverified generated capabilities perform worse than none |
| Re-run through the committed SLX before trusting auth | Inline `secret_vars` and resolved `secretsProvided` differ |
| Every committed task gets a retrieval check | A task that cannot be found does not exist |
| Retrieval misses are fixed in `alias`/`statement` | Never spend a rule slot papering over metadata |
| Every task emits `run_metadata`; every issue states its threshold | The agent cannot see `env_vars`, nor can a human triaging later |
| Thresholds live in `env_vars`, never runtime vars | Otherwise the agent can manufacture or suppress its own issues |
| No tripwire that is true tomorrow for today's reason | A daily identical issue destroys the stream's credibility |
| Rules ≤5, persona-scoped, never naming an `env_var` | No cap exists in the platform; crowding is silent |
| `update_assistant` on re-runs | `create_assistant` is a destructive upsert |
| Scheduled commands are created paused | Unpausing starts outward-facing delivery |
| Depth tasks are never scheduled | They serve the conversation, not the report |
| Nothing about delivery reaches the agent | Naming the mechanism invites reasoning about it |
| Phase 1 emits a merged secret inventory, both sources, each verified | Vault keys are synthesised guesses |
| Break-glass: narrow `statement`, tag excluded from the persona filter, manifest entry, gap-report note | It can do anything its credential permits |
| Re-gate when findings change the portfolio | A stale approval looks like consent |
| Forks offered as options before the plan is drafted | A plan presented first has already taken every fork |
| Ledger written per phase; manifest and gap report always | A dozen objects across four APIs is not reversible from memory |
| `max_runs` ≤ 30 stated in the handover | Every schedule expires; nobody remembers this |

## Red flags — stop

- "I'll assume a standard setup" → you are inventing the environment
- Reaching Phase 1 without having asked what to read, or Phase 3 without forks
- Concluding a platform is unreachable from a probe that carried no credential
- Calling a 403 a permissions problem before printing which identity you are
- Taking the vault listing as the set of credentials that exist
- Reasoning from the `head` of a filtered list
- Committing a task you have not run, or trusting unverified auth
- A rule that names an `env_var`
- Any mention of sinks, Slack, email or recipients in agent-facing instructions
- A report whose most useful sentence is in its closing paragraph
- An unheaded opening paragraph, or six same-weight headers ordered by topic
- Reading an empty `issues` array from `run_slx` as "the task emitted nothing"
- A quality floor that suppresses detection rather than only the projection
- Two tasks where one needs the other's output

## Rationalizations

| Excuse | Reality |
|---|---|
| "I know this stack, the probe is overhead" | The reference builds' biggest findings were all surprises. Minutes. |
| "I can discover the context sources myself" | Only what is reachable from inside. Ceilings and intent are declared outside. |
| "They told me what the stack is, so I know" | That is a claim. One reference build's documented stack was empty. |
| "The probe authenticated, so the SLX will" | Different mechanism. Inline `secret_vars` vs resolved `secretsProvided`. |
| "The script is obviously correct" | Every runtime defect in the reference builds looked correct. |
| "The fit is poor, so nothing is growing" | R² gates the date, not the finding. One estate's biggest object fired nothing. |
| "A command per depth task, as documented" | One per job. The drill-down happens in the session the report started. |
| "A report satisfies the automation ask" | It does not. Say which half you built and let the human decide. |
| "Leading with a summary is leading with the verdict" | A summary says how many. A verdict says what happens and what to do. |
| "They can just redirect me if the plan is wrong" | Almost nobody rejects a finished plan. Offer the forks while they are still forks. |
| "I'll write the manifest at the end" | The build that dies in Phase 4 is the one that needed it. |
