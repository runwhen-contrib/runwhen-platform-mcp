# Decomposition — use case to object portfolio

Phase 2. Input: an abstract objective plus the Phase 1 capability inventory.
Output: a named object list, presented for approval before anything is written.

The failure this prevents is building what the customer *said* instead of what
the workspace can actually *support*.

---

## Two shapes that never survive contact — name them before filtering

Both recur across use cases, and neither survives the reachability filter. Say so
early, before the customer has anchored on getting them.

**"Automate X" — write actions.** Collector tasks are read-only. Detecting,
classifying, raising an issue and handing over the exact next step is buildable.
*Executing* the renewal, opening the ticket, pushing the PR is a different
mechanism entirely. Say which half you are building, and never let a report
quietly stand in for the automation that was asked for. Every one of these
becomes a gap-report row with the grant that would unlock it.

**"Consolidate across tools" — external systems.** Use cases naming ServiceNow,
Jira, Splunk, Dynatrace, SharePoint, Teams or a secrets manager are asking you to
reach systems the runner may have no credential for and no network path to.
Probe reachability **and** authentication for each named system before designing
anything on it. "No integration exists yet" is a legitimate and common outcome;
it converts that half of the use case into an integration request, which is the
customer's decision, not a detail to route around.

---

## The five filters

Apply in order. Each one removes candidates; what survives is the portfolio.

### 0. Reuse — existing SLXs first, then the registry

**Two things to check, in this order.**

**a) What is already deployed in this workspace.** Enumerate the workspace's own
SLXs and find any that already touch the use case. Coverage is usually
*partial*, and the shape of the partial coverage should drive your design.

Worked case, stg-shared: 63 cert-manager Certificates live across **16**
namespaces, but the `k8s-certmanager-healthcheck` bundle is deployed to only
**2**. So the correct build was never "a certificate inventory task" — the
depth tool already existed and was under-deployed. The real gaps were extending
that bundle to the other namespaces, plus one genuinely new cross-namespace
expiry-horizon view. Designing before checking would have produced a custom task
duplicating a maintained bundle *and* missed the actual gap.

Ask: is the missing thing a **capability**, or is it **coverage**? Missing
coverage is a `deploy_registry_codebundle` loop, not an authoring job.

**b) The registry.**

`search_registry(search=<use case terms>)` **before** designing anything custom.
The registry carries production-ready, maintained codebundles; a custom task that
duplicates one is worse on every axis — you own the bugs, you own the upgrades,
and you have added a competing name to retrieval for no capability gain.

For anything that matches, use `deploy_registry_codebundle` instead of
`commit_slx`. Custom authoring is the fallback, not the default.

**Pass `resource_path` and `hierarchy` on registry deploys too.** They are
optional on `deploy_registry_codebundle` and easy to forget, and an SLX committed
without them lands under `/resources/_unscoped/` — still searchable, but outside
the resource tree and invisible to any persona `searchFilters` that keys on
`slxGroup`. Give registry deploys the same `resource_path` as the custom tasks
they sit beside.

A partial match still counts: deploy the bundle for the part it covers and build
custom only for the remainder. Record in the plan which objects came from the
registry and which are custom — the customer is entitled to know how much of
their agent is maintained upstream.

Certificates, as a worked case, already have `k8s-certmanager-healthcheck`
(auto-discoverable on `certificates.cert-manager.io`), `aws-c7n-acm-health`,
`azure-kv-health`, and SSL expiry checks inside `gcp-cloud-loadbalancer-health`.
Building a cert inventory task from scratch without checking would be waste.

### 1. Reachability

Drop every candidate Phase 1 proved unreachable. Not "we lack a documented
credential" — *we tried, and here is the denial*.

A dropped candidate is not deleted. It becomes a row in the gap report with the
exact grant that would unlock it. That report is the customer-facing half of the
deliverable; a use case that yields three working tasks and four precisely
specified missing grants is a **successful** engagement, not a partial one.

### 2. Environment-specificity

Prefer tasks that encode knowledge the model does not already have: this
cluster's naming convention, this team's threshold, this service's owner, this
tenant's layout.

Skills pay off in proportion to how sparse and procedural the underlying
knowledge is — measured gains run to roughly +52 points in domains like
healthcare against roughly +5 in software engineering, where the base model
already reasons well. A task that re-implements generic reasoning about
Kubernetes adds latency and retrieval ambiguity and very little capability.

| Weak candidate | Strong candidate |
|---|---|
| "Explain what a CrashLoopBackOff means" | "List pods crash-looping in the three namespaces this team owns, with the on-call rota for each" |
| "Check if a certificate is valid" | "Cross-reference every serving certificate against the CMDB owner field and flag the unowned ones" |

### 3. Consolidation

Merge candidates that share a workflow. A pivot becomes a runtime variable, not a
new task. Target **1 breadth + 2–4 depth**.

Fewer, fatter tasks beat many thin ones: the agent pays per response token, and
every additional similar name makes retrieval harder. If two candidates would
share 80% of their script, they are one task with a runtime variable.

### 4. Disjointness

Check the surviving set for overlapping applicability — against each other and
against the SLXs already in the workspace from Phase 1c.

When two tasks both look applicable there is no tie-break beyond embedding
similarity. Two tasks that could each plausibly answer the same question is a
design defect, not redundancy. Merge them or sharpen the `statement` of each
until a reader could say which one answers which question.

---

## Worked example: certificate lifecycle

**Objective as stated:** "Proactively manage certificate lifecycle and reduce
operational risk — visibility into inventory and upcoming expirations, automated
validation, stakeholder communication, renewal workflows, change requests."

**Candidates before filtering:** inventory certs; check expiry; validate chains;
notify owners; raise change requests; trigger renewal; verify post-renewal;
report compliance.

**Filter 0 — Reuse.** `search_registry("certificate expiry TLS SSL")` returns
`k8s-certmanager-healthcheck`, `aws-c7n-acm-health`, `azure-kv-health` and
`gcp-cloud-loadbalancer-health`. If the estate runs cert-manager, deploy the
first rather than writing an inventory task. What follows assumes no registry
bundle fits the reachable surface — always verify that assumption before
accepting it.

**Filter 1 — Reachability.** Renewal, change-request creation and notification
all require write credentials to a CA, a ticketing system and a mail path. If
Phase 1 found none, they leave the portfolio and enter the gap report. What
remains is everything read-only: inventory, expiry, chain validation, ownership.

**Filter 2 — Environment-specificity.** "Validate a chain" is generic — the model
can reason about a chain it is shown. "Which of *our* certificates are unowned"
is not, and it is the one that predicts outages: orphaned certificates, the ones
with no named owner, are what actually causes them. Roughly 40% of expired
certificates go undetected without active discovery, so the inventory must be
discovery-driven rather than reading a maintained list.

**Filter 3 — Consolidation.** "Check expiry" and "inventory" are one pass, not
two. Per-certificate inspection is one task with `CERT_NAME` as a pivot.

**Filter 4 — Disjointness.** Inventory answers *which ones*; inspection answers
*what about this one*; ownership answers *who do I tell*. No overlap.

**Portfolio:**

| Kind | Name | Question it answers |
|---|---|---|
| breadth | `cert-expiry-inventory` | Which certificates expire soon, and which are unowned? |
| depth | `cert-chain-inspect` | For one certificate: chain, issuer, SANs, validity. |
| depth | `cert-consumer-trace` | For one certificate: what serves it, what depends on it. |

**To the gap report:** renewal automation (needs CA write), change requests
(needs ticketing write), owner notification (needs a mail path).

---

## Worked example: capacity forecasting

**Objective as stated:** "Predict future resource consumption based on historical
usage patterns; help plan scaling and avoid performance issues."

**Filter 1 — Reachability.** Forecasting needs history. If the metrics backend is
unreachable or retains only hours, forecasting is not buildable — say so at the
gate rather than shipping a task that extrapolates from noise. Current
utilisation may still be reachable, which is a different and honest deliverable.

**Filter 2 — Environment-specificity.** Generic trend maths is not where the
value is; knowing which workloads are elastic, which are pinned, and what the
team's actual headroom policy is, is.

**Filter 3 — Consolidation.** Per-resource-type forecasts are one task with a
`RESOURCE_TYPE` enum pivot, not one task per type.

**Filter 4 — Disjointness.** Sharpen against any cost or utilisation SLXs already
in the workspace — this use case overlaps FinOps tasks more often than not.

**Portfolio:**

| Kind | Name | Question it answers |
|---|---|---|
| breadth | `capacity-headroom-scan` | Where is headroom projected to run out first? |
| depth | `capacity-workload-trend` | For one workload: usage history, growth rate, projected exhaustion. |

---

## Offer the choices before you draft the plan

**A plan is one option wearing the clothes of an answer.** By the time you have
written `BUILD PLAN`, every fork has already been taken — by you, silently, using
judgement the human never saw and cannot audit. Their only remaining lever is to
reject a finished thing, which almost nobody does.

So there are **two beats at Phase 2, not one**: choices first, then the plan
built from the answers.

```
probe findings  ->  coverage matrix  ->  CHOICES  ->  BUILD PLAN  ->  approval gate
                                          ^                            ^
                                     they decide                  they confirm
```

### Which forks are worth asking about

A fork is **material** when all three hold. If any fails, decide it yourself and
say so in one line — asking about routine things trains the human to skim.

1. **It changes what gets built**, not how it is built. Scope, not implementation.
2. **The probe cannot settle it.** Both branches are defensible on the evidence
   you have. If the data picks a winner, there is no question — there is a
   finding.
3. **They know something you do not.** What the PoC is for, which team already
   owns this, what they would actually act on at 07:00.

### The forks that recur

Ask the two or three that are live for this build. Never all six.

| Fork | The question | Why it is theirs |
|---|---|---|
| **Coverage** | The probe reached compute, volumes, buckets and cost. Which of these matter? | You cannot tell which are already someone else's dashboard |
| **Breadth vs depth** | More collectors (wider report) or more drill-downs (better conversation)? | Depends whether they read reports or investigate |
| **Reuse vs extend** | An existing SLX covers ~70% of this. Extend it, or build alongside? | Extending touches something that already has users |
| **Detail** | Everything measured, or a summary plus the critical tail? | Sets the report's length before the report exists |
| **Issue policy** | Report-only, or raise issues too? Issues are durable and searchable; they also accumulate | Their alert fatigue, not yours |
| **Confidence floor** | Report a weak trend on a nearly-full resource, or stay quiet until the fit holds? | False negatives and false alarms cost them differently |

### How to ask

- **Two to four questions, each with two to four concrete options.** Not open
  prompts — an open question hands the work back.
- **Every option carries what it costs and what it gives up.** *"Summary plus the
  critical tail — fits on a screen; a slow-growing volume outside the top 10 goes
  unmentioned until it is not slow any more."* An option with only upside is a
  recommendation in disguise.
- **Recommend one, and say why in a clause.** Not recommending is not neutrality;
  it is making them do your job. Mark it and let them overrule you.
- If your harness has a multiple-choice question tool, use it — options laid out
  side by side get compared, options in prose get skimmed.
- **Do not block on the answers to keep working.** Anything independent of the
  fork continues while you wait.

### Record what was offered

Write both halves into the ledger — `plan.md`, above the plan itself:

```markdown
## Choices offered 2026-09-03T10:20Z

| Fork | Options | Chosen | By |
|---|---|---|---|
| Coverage | compute / volumes / buckets / cost | all four | human |
| Detail | everything · summary + critical tail | summary + tail | human |
| Issue policy | report-only · issues too | issues too, sev 3-4 only | human |
| Reuse | extend cost SLX · build alongside | reuse as-is, cite it | **me** — probe showed it already reports by service |
```

The `By` column is the point. Six weeks later, *"why does this not cover cost?"*
has an answer, and a fork you resolved yourself is visibly yours rather than
quietly theirs. **With no human available**, every row is `By: assumed` with the
assumption written out — see `build-ledger.md`.

---

## Plan output format

Present exactly this at the gate. Nothing is written until it is approved.

```
BUILD PLAN — <use case> — workspace <ws>

TASKS
  breadth  cert-expiry-inventory   Which certs expire soon / are unowned    [gcp-sa]
  depth    cert-chain-inspect      Chain, issuer, SANs for one cert         [gcp-sa]
  depth    cert-consumer-trace     What serves and depends on one cert      [gcp-sa]
  utility  break-glass-gcp         Arbitrary gcloud CLI (build + recon)     [gcp-sa]
  registry k8s-certmanager-healthcheck   deployed from registry, not custom  [in-cluster]

RULES (persona-scoped, 5 max)          COMMANDS (persona-scoped)
  2 of 5 used                            /cert-report   scheduled 0 13 * * 1-5 -> email
                                         /cert-inspect  manual

ASSISTANT   cert-warden      searchFilters pinned to slxGroup=custom/certificates
KB          2 articles       scoped to custom/certificates/*

GAPS -> phase 2   renewal (CA write) · change requests (ticketing write) · owner notification (mail)

Approve, edit, or reject.
```

Four columns matter and no more: kind, name, the question it answers, the
credential it needs. If a reader cannot tell from the name and question why an
object exists, rename it before asking for approval.
