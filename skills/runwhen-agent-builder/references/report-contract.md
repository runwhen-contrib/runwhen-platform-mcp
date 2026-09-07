# Report contract — what the scheduled command produces

This is the **agent's** output contract, not the task's. The breadth task's
report (`task-contract.md` → name-carrying) names tasks and pivots so the digest
handoff works. This file governs what the agent writes for a **human** to read.

Keep them separate. The task names tasks; the report names actions.

**The report is a scan surface, not a document.** Most of the people it reaches
give it three seconds. That is the design constraint everything below serves.

---

## Delivery is invisible

The agent produces report content. That is the whole job.

**Do not explain the delivery mechanism to the agent.** Naming it — even to say
"the platform handles it" — invites delivery reasoning. Observed: an instruction
reading *"the platform handles delivery via the per-fire suffix it appends,
follow that suffix"* produced an agent announcing *"Now I need to deliver this to
Slack."*

State that the output is the report, that delivery is handled elsewhere and is
not its concern, and that the report must not be tailored to any destination.
Then say nothing further about it.

Two mechanisms exist; check what the workspace already uses:

- **`display_markdown_report` widget — preferred.** Narration and deliverable are
  separate channels, so conversational preamble cannot contaminate the report.
- **Final assistant message as plain markdown** — the deliverable shares a
  channel with narration, which is why commands using it need explicit "no
  filler, no throat-clearing" rules.

---

## The top of the report is fixed

**Two named sections, in this order, before anything else.** They carry a header
each. An unheaded opening paragraph gives a skimming eye nothing to land on, and
a reader who lands on nothing scrolls past the whole report.

```markdown
## Summary

> **`pgdata-rw-airgap-postgresql-0` is full now** — 30 MiB free of 58.9 GiB.

## What to do

| # | Action | Where | By when | Why |
|---|---|---|---|---|
| 1 | Expand or reclaim `pgdata-rw-airgap-postgresql-0` | `runwhen-env-airgap` | **now** | 99.9% full |
| 2 | Expand `data1-rw-airgap-seaweedfs-volume-0` | `runwhen-env-airgap` | **Sep 6** | 95.1%, 2.1 GiB/day, R² 0.88 |
| 3 | Expand `data1-rw-seaweedfs-volume-0` | `runwhen-env-test` | **Sep 8** | 92.8%, 1.4 GiB/day, R² 0.94 |
| 4 | Right-size CPU requests | `infra-flux-nonprod-shared` | — | 76.8 cores held, 22.5 used |
```

### The verdict line

**A blockquote, one sentence, at most 25 words, naming one phenomenon.** It states
a consequence, not an inventory. The blockquote is not decoration — it is the
only visual weight at the top of the page.

> *the `pgdata` volumes fill around the 2nd unless they are expanded*

never *"4 workloads reviewed, 2 need attention"*.

**One phenomenon means one.** If the sentence joins two subjects with *and*,
*while*, *but* or a semicolon, it is two verdicts — keep the one with the nearest
deadline and move the other into the body. Observed: a verdict reading *"the
airgap PostgreSQL volume is full and two SeaweedFS volumes will follow — while
the node-pool ceiling is driven by over-requesting"* ran to 43 words and three
phenomena. Every one of them was true and the reader still had nothing to hold.

### The decision table

Five rows maximum, ranked by deadline, **numbered** so a reply can say "doing 2
and 3".

| Column | Cap | Rule |
|---|---|---|
| `#` | — | 1..5, so rows are referenceable |
| `Action` | **8 words** | imperative verb first: Expand, Raise, Reduce, Add |
| `Where` | **6 words** | the namespace, or the repo — whichever decides *who does it* |
| `By when` | — | **`now`**, a real date, or `—`. Nothing else. |
| `Why` | **15 words** | the evidence, not the argument. Figures over sentences. |

- **Sort: `now` first, then dates ascending, then `—` last.** A table sorted by a
  column that is sometimes empty is not sorted. `now` is for something already at
  its limit; `—` is for something real with no deadline, and it sorts last
  because that is what having no deadline means.
- **Five rows maximum.** This is a scan surface, not an inventory. A sixth
  finding lives in a body band and is not an action item — if everything is an
  action item, nothing is.
- **`Why` is figures.** *"99.9% full"* beats *"the volume has reached 99.9% of
  its capacity and write failures are imminent"*. Observed caps being blown by
  141-character cells carrying two clauses on a semicolon; the row wrapped to
  four lines in email and the table stopped being a table.
- **`Where` decides who does it** — a namespace, or the repo. Where a
  recommendation comes from an advisory source that applies nothing itself (a VPA
  in `updateMode: Off`, a plan file), the change lands in the repo. Where the
  landing place is genuinely unknown, say **not established** — never guess one.
- **A column whose values come from outside the collectors needs its source named
  in the prompt.** `Where` is the one that bites: no collector emits a repository
  path, so unless the prompt names the repos — or names the KB article carrying
  the namespace-to-repo map — the agent can only ever write a namespace. Observed:
  a prompt saying *"declared in neither known repository"* named neither, and the
  agent correctly reported it could not have chosen a repo for any row.

**The actions appear exactly once.** A reader who acts on the table and stops
reading must not have missed anything, so there is no recommendations section at
the bottom. When there is genuinely nothing to do, one honest line replaces the
table — never a manufactured row, never a silent omission.

### Why this is a contract and not a preference

Observed: two capable agents, both holding this skill, wrote genuinely good
reports and buried the decision anyway. One opened on a two-sentence summary and
dispersed its rollback candidates through prose; the other opened on four raw
counts and put its single most useful sentence in the closing paragraph.

A third complied with every rule in an earlier version of this file — verdict
line, five-row table, evidence below, no trailing recommendations — and still
produced a wall, because the rules it obeyed were *"one sentence"* and *"one
clause"*, which a 43-word compound and a 141-character splice both satisfy.

**Adjectives are negotiable and word counts are not.** That is the whole reason
the caps above are numbers.

---

## The body is banded by urgency, not by resource type

Fixed headers, in this order. **A finding's band is decided by its date** — never
by what kind of resource it is. Sorting by resource type puts a volume that is
full right now in the same section, at the same visual weight, as one that fills
in three weeks.

| Band | Contains | Form | Per finding |
|---|---|---|---|
| `## Act now` | at a limit already, or dated within 7 days | one short block each | **≤ 60 words** |
| `## Within the month` | dated 7–30 days out | one short block each | **≤ 60 words** |
| `## Watch` | beyond 30 days, or undated and not urgent | **one table, no prose** | one row |
| `## Data quality` | what was not measured, and by which collector | list | one line per collector |

Rename the middle band to the window that fits the findings — `## Within the
week`, `## Before the end of September`. The name should tell a reader whether
this section is their problem today.

**Delete an empty band.** A band with nothing in it is removed, not filled with
"nothing to report here".

Anything that is a finding but not an action — a correlation, a state
disagreement, a cost note — goes in the band matching its urgency. It does not
get a section of its own; a section per topic is how six same-weight headers
happen.

### What an evidence block is for

The level, the date and the fit are **already in the decision-table row**. The
block underneath starts after them, and answers one question:

> **Why is this happening, and what else does it touch?**

That is the correlation, the mechanism, the thing that would falsify it, the
constraint on fixing it. *"The same namespace hosts the full PostgreSQL volume
above — both pressures stem from `runwhen-env-airgap`"* is worth 60 words.
Restating 99.9% and 58.85 of 58.88 GiB is not, because the reader read it four
lines ago.

---

## Use the full range of markdown

**Prose is one tool among several, and it is the heaviest one.** A report built
only from paragraphs reads as a wall no matter how good the paragraphs are.

- **Three consecutive prose paragraphs means you have a table.** Parallel items —
  five volumes, four namespaces, three buckets — are rows. Reach for the table
  before the third paragraph, not after the fifth.
- **The `Watch` band is always a table.** Its items are parallel by definition and
  nobody reads prose about something that happens in three weeks.
- **Bold every resource identifier** the reader might grep for, and set it in
  backticks. It is what their eye is hunting for.
- **A blockquote is emphasis with weight** — the verdict, and at most one other
  thing in the whole report. Two blockquotes is none.
- Horizontal rules separate the scan surface from the body, once. Not between
  every section.

Formatting is not decoration and there is no house minimalism to respect. If a
diagram, a nested list or a second table makes a finding land faster, use it.
What is forbidden is not variety — it is uniformity.

---

## Write forward, not backward

Match the report's tense to what the data supports.

- **State the finding as what happens next**, with the current value as its
  supporting evidence — *"will fill around Oct 2, from 90.9% today"*, not *"is at
  90.9% and growing at 0.35 GiB/day"*.
- **Recommendations are imperative** — "Expand", "Raise", "Reduce". Never
  "consider", "it may be worth", "you might want to".
- **Watch-list items say when they would arrive** if the trend holds, even beyond
  the window. "Reaches the ceiling around mid-December" tells the reader whether
  to care; "growing steadily" does not.

**The hard limit: never convert a present fact into a prediction.** Something
already full is full — present tense, `now` in the table, act today. Something at
a ceiling by design is configuration, not forecast. The future tense is for what
the data actually projects; using it elsewhere makes a certainty sound like a
guess.

For a detection or retrospective use case the default flips to present and past.
The rule is the same: the tense follows the evidence.

---

## Evidence-proportional language

- Give a projected date with its confidence attached — R² and sample count. Never
  a bare date.
- A single-sample anomaly is not a trend. Say "one observation" when that is all
  you have.
- Distinguish **at a ceiling by design** from **at a ceiling under pressure**.
  The first is configuration; only the second needs action, and only the second
  belongs in the decision table. State which — do not hedge with a question mark.
- Mark every estimated figure as estimated, with the date of whatever rate card
  or assumption produced it.
- Never fabricate a number. If a collector failed or returned nothing, keep the
  row, say **n/a**, and give a one-word reason. A dropped row reads as "fine",
  which is the one thing it is not known to be.

---

## Never put internal mechanics in the report

**No URLs, citation links, task IDs, SLX names, run IDs or issue IDs.** The
reader wants infrastructure, not plumbing. Cite sources in chat if useful, never
in the report. Bare issue IDs (`#nnn`) are acceptable only when correlating to an
existing tracked issue.

Never use the internal vocabulary "open" or "closed" for issues.

---

## Length

The bands and the per-finding caps set the length; nothing else needs to.

A calm day is four lines and an empty table replaced by one honest sentence. A
day with three volumes about to fill is longer because `Act now` has three
blocks in it — not because the paragraphs grew.

**The one bound worth stating to the agent:** the `Summary` and `What to do`
sections must be readable in three seconds. Everything below them is reference
material for whoever acts on them, and is allowed to be as long as it has
findings to carry — and no longer.

A short report on a calm day is a feature: it is what keeps the reader trusting
the long ones.

---

## Lead with the correlation

If two tasks together say something neither says alone, that is the verdict.
*"Memory requests sit at 83% while actual utilisation is 29% — the cluster is not
full, its requests are misallocated"* beats reciting both numbers separately. A
recital of independent figures is what the tasks already emitted.

This is also the reason correlation belongs to the agent and not to a task
(`task-contract.md` → Access, not math): no single task can see it.

---

## Be honest about gaps

If a task returned warnings, excluded resources below a sample floor, or came
back without its `run_metadata` block, say so and name which collector. A reader
who knows what was not measured can weigh the rest; one who does not, cannot.

---

## Writing the command prompt that enforces all this

The contract above is what the report must be. **The scheduled command's prompt
is the only place it is ever enforced**, and it is a build artifact you write —
not a pointer to this file, which the runtime agent cannot read.

Six parts, in this order. Anything you drop, the agent invents.

**1. Role and remit** — two sentences. What environment, and what question the
report answers. Then the split: *the collectors gather and reduce; you do the
forecasting, correlation and judgement — they cannot see each other's output, so
anything spanning two of them is yours alone.*

**2. Pass 1 — collect.** Name the breadth tasks exactly as they are titled, and
say *read-only, nothing else is needed*. Name the two blocks every task emits and
require reading them **before** the data: `run_metadata` (the window and
thresholds that run used — the agent's only view of config, per the visibility
boundary in `object-model.md`) and the data-quality block. Then: **do not run the
drill-down tasks while building the report** — they answer questions about one
subject and nobody has asked one yet.

**3. Pass 2 — correlate.** Two or three concrete cross-task questions for *this*
environment, not generic advice. *Is the pool near its ceiling because demand is
real, or because requests are inflated?* Then the **flag table** — every flag a
task can put on a row, and what it licenses the agent to claim:

| Flag | What it means | What you may say |
|---|---|---|
| `(unreliable)` | fit below the R² or sample floor | the level, never the date |
| `STALLED` | long trend positive, recent window flat | the trend has stopped; lead with the level |
| `DRAINED` | emptied, not shrinking on a trend | it was reset; never project it forward |

Without this table the agent treats every flag as a hedge and writes "possibly".

**4. Pass 3 — write.** The skeleton from *The top of the report is fixed*,
inline and verbatim, with the caps as numbers. Then the band names, with the
middle band's window already chosen for this use case. Do not paraphrase the
caps and do not link to this file.

**Scrub the example rows against the rest of the prompt before shipping it.** An
example in a prompt is **normative, not illustrative** — agents copy its verbs,
its hedges and its phrasing far more faithfully than they follow the prose above
it. Observed: a skeleton row reading *"Expand `pgdata-…`"* sat in a prompt whose
own drill-down section said expansion cannot be confirmed from this runner. The
agent kept the verb and moved the caveat into data quality. Either make the
example obey every constraint the prompt states, or write it with a placeholder
verb that cannot be copied wrongly.

**5. Voice.** Two sentences from *Write forward, not backward*. Not the section.

**6. A numbered self-check**, ten items or fewer, each one checkable by looking
at the finished text. *"Verdict is one sentence under 25 words naming one
phenomenon"* is checkable. *"The report is terse"* is not.

Two things the prompt must also carry, because they are environment facts and not
report rules: what is **already covered elsewhere** and must not be recomputed —
and **name it the way the report is allowed to name it**. *"The existing project
cost report"* works; *"the GCP Project Cost Health SLX"* is a direct contradiction
of the no-internal-mechanics rule three sections earlier, and the agent has to
resolve it at write time. Second, the
**drill-down section** — which follow-up runs which depth task with which pivot,
run *here, in this session, with the report as context*. The follow-up happens in
the conversation the report started; a second command splits one workflow in two.

**Save the finished prompt into the build ledger** alongside the manifest. It is
the most-edited artifact of the build and the one most likely to be tuned later
by someone who was not there.
