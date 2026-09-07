# Task contract — breadth and depth

Parameter shapes live in `object-model.md`. This file is about **what to build**,
not how to call the API.

An agent needs exactly two kinds of task:

- **Breadth** — one per use case. Answers *where should I look?* Runs on a
  schedule, unattended, and produces the digest.
- **Depth** — two to four per use case. Answers *what is actually wrong with this
  one thing?* Invoked by a human or by the agent after a breadth finding.

**The scheduled command runs breadth only.** Depth tasks exist for the
*conversation the report starts* — someone reads a summary row and wants to dig
into that one workload, volume or certificate. They are not required to produce
the report, and scheduling them would just cost tokens for findings nobody
asked about.

Anything you are tempted to build as a fifth kind is usually a runtime variable
on an existing task.

---

## How many tasks — the resource-path constraint

**One task = one resource path + one question.**

An SLX carries exactly ONE resource path. Content describing a different
resource is buried where nobody searching for it will look. So the breadth/depth
split decides a task's *shape*; the resource path decides *how many* you need. A
use case spanning Kubernetes volumes and GCS buckets needs two breadth tasks
however similar their logic — they describe different resources.

The number of APIs a task calls is irrelevant. A task may call `kubectl` *and*
`gcloud` provided everything it returns describes the same resource. The test is
never "which CLI" but "which resource does this describe".

**Tasks cannot read each other's output.** Any figure needing inputs from two
tasks must be computed by the agent. If you find yourself wanting one task to
consume another's result, that computation belongs in the agent, and any
constant it needs (a rate card, a mapping) belongs in a rule.

## Breadth task

**One per use case.** Scans the primary dimension end to end and ranks what it
finds. It is the only task the scheduled command needs to run.

Requirements:

1. **Bounded output.** Aggregate in-script. Never emit raw rows — every token of
   task output is paid for out of the agent's context window, and a 400-row dump
   crowds out the reasoning you wanted.
2. **Pre-ranked.** Sort by the thing that matters (soonest failure, largest cost,
   worst ratio) before emitting. The agent should narrate your ranking, not
   invent one.
3. **Complete over precise.** Breadth means nothing is silently skipped. If a
   region or namespace could not be read, emit a row saying so with `n/a` and a
   one-word reason. A gap you report is a finding; a gap you hide is a bug.
4. **Name-carrying.** See below. This is not optional — it is the entire
   mechanism by which a digest becomes an investigation.

Shape, in bash:

```bash
#!/usr/bin/env bash
set -euo pipefail

# 1. gather  -- one pass per source, no per-item shelling out
# 2. compute -- buckets, ranks, deltas, all here (see Determinism rule)
# 3. emit    -- bounded summary to stdout, issues to FD 3

main() {
  issues='[]'

  # ... gather and compute, appending to $issues with jq ...

  # always emit an unconditional summary so a healthy run still reports
  issues=$(echo "$issues" | jq \
    --arg t "Certificate inventory scanned" \
    --arg d "$SUMMARY" \
    --arg n "Review the table above." \
    '. + [{"issue title":$t, "issue description":$d,
           "issue severity":4, "issue next steps":$n}]')

  echo "$SUMMARY"        # stdout -> humans and debugging ONLY
  echo "$issues" >&3     # FD 3   -> what the agent actually reads
}
# no `main "$@"` — the runner invokes main() itself with FD 3 wired
```

Issue keys carry the `issue ` prefix — `"issue title"`, `"issue description"`,
`"issue severity"`, `"issue next steps"`. Bare `title`/`severity` silently
produce an issue-less run.

### The report goes in the issue, not in stdout

**The issue is the durable channel; stdout dies with the run.** An agent
executing a task in its own session can read stdout — but nothing else ever
will. `workspace_chat` searching the workspace afterwards, a human triaging next
week, and the digest email's action generator all read **issues**.

So the test is not "can anything see stdout" — it is **must this survive the
run?** A task that prints a beautiful report to stdout and emits a one-line
issue has produced nothing the digest can carry and nothing findable tomorrow.

Two channels, two jobs:

| Channel | Carries | Read by |
|---|---|---|
| **stdout** | the rich series the agent reasons over, in-session | the agent, this run only |
| **issue** | the report body, the summary, the tripwires | everyone, durably |

So the unconditional severity-4 summary issue is not a formality: **its
`issue description` must carry the whole report body**, truncated to a safe
bound. A production SLX on stg-shared does exactly this:

```python
summary = f"New: {len(new_issues)} · active: {len(active)} · blocking: {len(blocking)}\n\n{body}"
return [{
    "issue title": f"{UPDATE_TITLE} — {now.strftime('%Y-%m-%d')}",
    "issue description": summary[:15000],
    "issue severity": 4,
    "issue next steps": f"Review open issues: https://github.com/{OWNER}/{REPO}/issues",
}]
```

Keep stdout for debugging. Put the report — including the task names from the
name-carrying contract — in the issue description.

### Every task emits a `run_metadata` block

The agent **cannot see `env_vars`** (see `object-model.md` → visibility
boundary). Any config that changes how the output should be read must therefore
be echoed into the output itself:

```
run_metadata:
  window: 90d (LOOKBACK_DAYS=90)
  granularity: daily
  thresholds: {warn_days: 30, min_samples: 14, min_r2: 0.7}
  conventions: node-pool max is PER ZONE on regional clusters
  data_quality: {series_returned: 1712, excluded_below_floor: 43}
```

Without it the agent either invents the window it thinks was used, or hedges
about a threshold it cannot name. **Every issue description must likewise state
the threshold that fired it beside the observed value** — neither the agent nor
a human triaging six weeks later can look it up.

---

## Depth task

**Two to four per use case.** Each takes its pivot as a validated runtime
variable so one task covers every instance of its kind.

```json
"runtime_vars": [
  {"name": "CERT_NAME",
   "description": "Certificate to inspect, as reported by the inventory task",
   "default": "",
   "validation": {"type": "regex", "pattern": "^[a-zA-Z0-9.*_-]+$"}},
  {"name": "DETAIL_LEVEL",
   "description": "concise for a summary, detailed for full chain and history",
   "default": "concise",
   "validation": {"type": "enum", "values": ["concise", "detailed"]}}
]
```

`DETAIL_LEVEL` goes on **every** depth task. It lets the agent ask for a summary
when it is orienting and the full dump only when it is committed — the same
`response_format` lever good tool APIs expose. Default `concise`.

### The consolidation rule

> A new pivot is a runtime variable. A new *question* is a new task.

Inspecting `api.example.com` instead of `www.example.com` is a pivot — same
question, different subject. Add a value, not a task. Asking *who owns this*
instead of *when does this expire* is a different question — that earns a task.

Wrong (four thin tasks, four names competing in retrieval):

```
cert-inspect-api / cert-inspect-www / cert-inspect-admin / cert-inspect-internal
```

Right (one task, one name, a pivot):

```
cert-chain-inspect  with  CERT_NAME as a runtime var
```

More tasks is not more capability. It is more retrieval ambiguity and more
surface to maintain, and the agent has no tie-break when two tasks both look
applicable.

---

## The name-carrying report

**The rule:** the breadth task's output must name the exact task to run next for
each finding.

**Why it works:** the follow-up actions in the digest email are generated by an
LLM that is instructed to copy resource names verbatim from the report artifact.
Name the task in the report and that name rides into the generated prompt; the
forked session can then find it. Leave it out and the follow-up is a vague
paraphrase that retrieves nothing. You cannot author those actions directly (see
`object-model.md` → Handoff mechanics), so this is the only lever you have.

Weak — produces "Look into the expiring certificates":

```
3 certificates expire within 14 days.
```

Strong — produces "Inspect api.example.com chain with cert-chain-inspect":

```
3 certificates expire within 14 days. Worst: api.example.com (4d, unowned).
Next: cert-chain-inspect (CERT_NAME=api.example.com)
```

Every finding row carries: the subject, the number that makes it urgent, and the
task that investigates it.

### The contract must hold when nothing is wrong

The trap: a breadth task that emits per-finding rows only when a threshold is
breached. On a healthy estate it emits none, its next-steps degrade to
placeholders like `CERT_NAME=<name>`, and the digest's follow-up actions have no
real names to copy — so the handoff silently dies exactly when the report is
routine, which is most days.

**Always emit a FOCUS block naming the top N subjects by urgency, regardless of
whether any threshold fired**, with concrete values and the exact invocation:

```
FOCUS - closest to expiry, investigate these first
  1. wildcard-dep-strip-shared-runwhen in runwhen-env-dep-strip expires in 33d
     investigate: cert-chain-walk CERT_NAME=wildcard-dep-strip-shared-runwhen NAMESPACE=runwhen-env-dep-strip
```

Put the leading subject in the **issue title** too — the title is what the email
subject and action generator see first.

Test this deliberately: run the breadth task against a healthy estate and confirm
the report still contains real identifiers. If it only names things when
something is broken, it is broken.

---

## Access, not math — and where the line falls

A task's scarce value is **authenticated reach**: credentials, cluster access, an
API the agent cannot call. Arithmetic is not scarce. That argues for thin
collectors — but it collides with the determinism rule below, and the collision
is only apparent. The line falls in two places:

**Reproducibility.** Anything that must be identical run-to-run, and anything
that *gates an issue*, is computed in-script: thresholds, buckets, ranking,
days-remaining, slope. A model that recomputes these drifts, and a tripwire that
drifts is worse than no tripwire.

**Scope.** Anything spanning more than one task must be the agent's, because
tasks cannot read each other's output. Cross-source correlation, cost models
combining two collectors, and judgement calls needing context a single task
lacks all belong to the agent.

So: **compute within a resource path, correlate across them.** The summary
statistics you compute go in the durable issue record; the series on stdout is
what the agent reasons over. Reduce enough to fit a context window, never so
much that you have pre-decided the answer.

## Determinism rule

**Thresholds, buckets, ranking, deltas and projections are computed in the
script. The model narrates; it never calculates.**

Deterministic analysis ahead of the generative step is worth a large accuracy
multiple in practice, and it is also the difference between a digest that says
the same thing twice on the same data and one that drifts.

| Do this in-script | Not this in the prompt |
|---|---|
| `days_left = (expiry - now).days` | "work out how long until it expires" |
| bucket into `<7d / 7-30d / >30d` | "decide which ones are urgent" |
| sort by days ascending | "list the most important first" |
| flag `owner == ""` as unowned | "note any that look unowned" |

If you find yourself writing a prompt that asks the model to compute something,
that computation belongs in the task.

---

## Trend discipline — what a fit quality actually gates

Any task that projects forward needs a quality floor, and the floor is almost
always applied in the wrong place.

**R-squared gates the PROJECTION, not the DETECTION.** A poor straight-line fit
means you cannot state a date. It does not mean nothing is growing. Observed: a
bucket holding **91% of all bytes in its project**, growing 8-16 GiB/day against
a 5 GiB/day threshold, fired **no tripwire at all** because backup growth is
lumpy and its R-squared was 0.35. The single worst object in the estate was
suppressed by a quality gate meant to protect the reader.

The fix stays fully mechanical: require **two independent measures to agree**
before firing - the least-squares slope *and* the raw endpoint delta
`(last - first) / span`. Both must breach the threshold. Then print
`fired=N suppressed=N` with a reason per suppression, so a quiet run is visibly
quiet on purpose rather than quiet by accident.

**Report coverage, because a fit will span a hole without telling you.**
Least squares draws a confident line across a blackout. Observed: a volume with
62 of 90 days sampled, including one 12-day gap, reported R-squared 0.88 to four
decimals. Emit `coverage_pct`, `largest_gap_days` and a gap count beside every
trend, and carry the coverage figure into the issue - a reader who knows the fit
spans a hole can weigh it; one who does not, cannot.

**Cross-check the recent window against the whole one.** A projection built on
growth that stopped a fortnight ago reads as a commitment. Observed: a 62-day
slope giving "~3 days to full" while the last 14 days sat flat at -55 MiB/day.
Do not let this change the deterministic projection - state the divergence
beside it, and say the current level is the finding rather than the date. When
the recent window holds too few samples to check either way, say **that**;
silence there reads as agreement.

## Outputs that look complete and are not

The worst defects in a collector are not crashes. They are plausible numbers.
Each of these was found only by running the task and reading the result.

**One malformed value must not truncate a scan.** A pod requesting `32M` (SI)
threw in a parser handling only `32Mi` (binary), and the exception aborted the
whole pod loop. The task then reported CPU requests at **3.0% of allocatable**
when the truth was **93.9%** - and the wrong figure inverted the recommendation
from "right-size" to "add nodes". Guard **per item**, so one bad value costs one
item, and count the failures into data quality where a rise is visible.

**A counter's label must say what it counts.** A field named `used_series`
reported 10,450 and read as "10,450 volumes measured"; it was series *returned*,
before any join. Another printed a 27 MiB/day slope as `0.03 GiB` - three
significant figures short of useful on the number a whole projection rested on.
Name the thing you actually counted (`series_returned` beside `items_measured`),
and pick a unit that shows the value rather than rounding it away.

**Identifiers are rarely unique across scopes.** Filtering telemetry by a
resource's name alone pulls in same-named resources from other namespaces *and*
resources that no longer exist, because monitoring backends retain series after
deletion. Observed: 168 "PVCs" against a live inventory of 152. **Always
intersect metric results against the authoritative inventory** and report what
you dropped.

**Ownership queries silently omit what has no owner.** Grouping pods by
Deployment / StatefulSet / DaemonSet drops static pods entirely - a 15%
undercount that reconciled to nothing and looked like a complete answer. When
you group by owner, account for the unowned remainder explicitly and reconcile
the total against a source that counts everything.

## When a task hits a permission wall mid-build

Phase 1 cannot predict every denial — some only appear once a task reaches for a
specific verb on a specific resource. When that happens **do not abandon the
task**. Ask what weaker signal is still reachable, and whether it still detects
the failure the task exists to catch.

Worked case: `cert-served-vs-declared` was designed to compare the certificate
serial in the TLS Secret against the serial served on the wire. The runner service
account turned out to be denied `get`/`list` on Secrets
(`system:serviceaccount:runwhen-local:workspace-builder cannot list resource
"secrets"`). Serial comparison was impossible. But `Certificate.status.notAfter`
*is* readable, and comparing served expiry against declared expiry still catches
the renewed-but-not-reloaded case, which is the failure the task was built for.

The rule:

1. Confirm the denial with `kubectl auth can-i` (or the platform equivalent) —
   never infer it from an empty result.
2. Find the weakest signal that still detects the target failure.
3. **Say so in the task's own report**, in one line, so the reader knows the
   fidelity they are getting.
4. Put the exact grant in the gap report, with what it would upgrade.

A degraded task that names its own limitation beats both a missing task and a
task that quietly checks less than it claims.

## Build economy

A full build is dozens of platform calls, several of which return very large
results. Budget for it from the start rather than discovering it at the end.

- **Write scripts to a file and pass `script_path`.** Inlining a 5 KB script into
  every `validate_script` / `run_script_and_wait` / `commit_slx` call repeats it
  three times or more. The file is also what you iterate on.
- **Cap output from the first run, not after it overflows.** Give every breadth
  task a `MAX_ROWS` / `MAX_HOSTS` style config variable and set it before the
  first execution.
- **Never re-run a task just to look at its output again.** The result you
  already have is the result.
- **Batch independent writes.** Registry deploys and other independent calls can
  be issued together rather than one per round trip.

## Authoring constraints that are easy to trip over

- **Do not name a runtime variable after a shell-provided variable.** `HOSTNAME`
  collides with the runner's own environment and silently takes the wrong value;
  use a distinct name such as `HOSTNAME_TARGET`.
- **Avoid the literal tokens `placeholder`, `TODO`, `FIXME` and `lorem` in report
  prose.** The pre-commit quality check substring-matches them and will flag
  legitimate wording — "the controller serves its built-in placeholder
  certificate" trips it. Reword rather than suppress; the check is right more
  often than it is wrong.
- **Runtime var names must not collide with `env_vars` or `secret_vars`.** The
  commit is rejected if they do.

## Issues contract

- Every run emits at least one **severity-4 summary issue**, unconditionally, so
  a healthy run still reports. Its description carries the report body.
- Tripwires (severity 2/3) fire on **mechanical arithmetic against config
  thresholds**. No judgement, no model in the loop.
- Every issue description **states the threshold that fired it** beside the
  observed value.

**Tripwire hygiene: exclude conditions that are true by design.** A resource
pinned at its ceiling deliberately — an HPA with `min == max`, a pool capped on
purpose — will otherwise fire an identical issue every day forever, and the whole
stream loses credibility. Observed: a `min==max` HPA firing severity-2 daily,
reporting a configuration choice as a scaling emergency.

The test before shipping a tripwire: **will this be true tomorrow for the same
reason it is true today?** If yes, it is a fact about the environment — report it
in the summary, do not fire on it.

## Retrieval check

**A committed task that cannot be found does not exist.** Run this after every
commit, once reconciliation has settled (1–3 minutes).

1. Write down the natural-language question a user would actually ask.
2. `workspace_chat(workspace_name=..., message=<that question>)`.
3. Assert the intended SLX is what comes back.

If it misses, the fix is the **`alias` and `statement`**, not a new rule. Those
two fields are the retrieval surface — they are what the semantic index matches
against. Write them as the sentence a user would type:

| Field | Weak | Strong |
|---|---|---|
| `alias` | `Cert Task 1` | `Certificate expiry inventory` |
| `statement` | `Checks certs` | `All TLS certificates should have more than 30 days before expiry and a named owner` |

Retrieval failure is the single most common way a correct task ends up useless.
Budget the check into every task, not just the first.

**A depth task whose pivot is a runtime variable will lose scope-anchored
questions, and no wording fixes it.** Its resource path cannot contain the
namespace, cluster or tenant that the pivot selects, so a question naming that
scope matches the incumbent SLXs that *do* carry it in their path and titles.
Observed: a right-sizing drill-down won "are any workloads over-provisioned?"
outright - the agent called it "a dedicated SLX for exactly this" - and lost
"which workloads in kube-system are over-provisioned?" to a namespace-scoped
incumbent.

Rewrite `alias` and `statement` in the capability vocabulary a user would type,
which is what wins the queries you can win. Then **accept the rest**: the
designed route to a depth task is the breadth report naming it with its pivot,
and that route does not depend on retrieval at all. Record the partial in the
manifest rather than reporting a clean pass, and do not quietly move a mandated
resource path to game the index.
