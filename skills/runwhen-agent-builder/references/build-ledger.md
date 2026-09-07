# The build ledger — state during, manifest after

A build creates a dozen-plus objects across four APIs over a session or several.
One artifact serves both needs: **written incrementally as you go** so a resumed
session knows where it is, and **complete at the end** so the build is auditable,
handover-able and reversible.

A manifest written only in Phase 7 does not exist for the build that dies in
Phase 4 — which is exactly the build that needed it.

Location: `<project>/.claude/agent-builds/<use-case>/`

```
state.md          the ledger — phase status, the source of truth
scope.md          Phase 0 answers: environment, repo paths, delivery, issue policy
probe/
  findings.md     distilled: what responded, retention, ceilings, identity
  probe.py        the script actually used, so re-verify is reproducible
  raw-*.json      payloads
surface.md        Phase 2 coverage matrix
plan.md           the forks offered + who decided each, then the approved plan
command-prompt.md the scheduled command's prompt as committed
manifest.json     object inventory + teardown order (Phase 7 shape below)
gaps.md           the customer-facing gap report
handover.html     the one-pager a human reads (Phase 7, from templates/)
```

**Two of these are not for a resuming agent.** `gaps.md` and `handover.html` are
written for a human who was not there. Everything else in the directory is
written for whoever picks the build back up — which is why the one-pager has to
exist separately rather than being a nicer `state.md`. A ledger optimised for
resume is unreadable cold, and that is the correct trade for a ledger.

`command-prompt.md` is here because it is the most-edited artifact of the build
and the one most likely to be tuned later by someone who was not there. A prompt
that lives only on the platform has no history and no diff.

---

## The ledger is a gate, not a log

**Each phase writes its checkpoint before the next one starts.** This is the
point of the mechanism — more than resume.

An instruction to ask about context sources is prose an agent can skip. A
`scope.md` with four source lines that must be filled or explicitly marked
*not needed* is a slot it cannot walk past. Observed: an agent holding this skill
went straight from the use case to a probe without asking what to read, and the
human had to volunteer the repos. Prose did not prevent that; an unfilled
required field does.

The same applies to `retrieval_check` and `auth_verified` per task. A resumed
session that cannot tell whether a committed task was ever verified will assume
it was.

**And to the forks.** `plan.md` carries a `Choices offered` table before the plan
(`decomposition.md`), one row per material fork with a `By` column naming who
decided it. A build that cannot say whether the human chose the scope or the
agent assumed it has no way to answer *"why does this not cover cost?"* six weeks
later — and a fork the agent resolved itself reads, in a bare plan, exactly like
one the human asked for.

---

## When no human is available

The gate above assumes someone eventually answers. Scheduled runs, unattended
builds and agent-to-agent dispatch break that assumption, and the four context
slots are **structurally** unanswerable then — not merely hard, because the
whole reason they exist is that ceilings, intent and ownership are declared
*outside* the environment. No probe substitutes for them.

Neither "filled" nor "not needed" applies, so **`BLOCKED — human unavailable` is
a third legal state for any slot.** When you are in it:

1. Mark the slot `BLOCKED — human unavailable` and write the question you would
   have asked, verbatim.
2. Record the assumption you are proceeding under, and what it would take to
   falsify it. *"Assumed alerts means the RunWhen issue stream, the only
   alert-shaped source with a verified path. If they meant Alertmanager or
   PagerDuty, this plan is mostly wrong and the deliverable is a credential
   request, not a build."*
3. **The Phase 2 gate becomes a hard stop that delivers the plan.** It never
   becomes self-approval. An unattended run that cannot get approval produces a
   plan, a ledger and a gap report — and stops. That is a complete deliverable,
   not a failure.
4. `gaps.md` gets a **questions not asked** section, so the human who reads it
   later can answer them in one pass instead of rediscovering them.

Observed: an agent handed this skill with no human available invented exactly
this state and its per-slot assumptions, because the skill defined the gate but
not what to produce when approval can never arrive. Leaving that to improvisation
invites the two bad outcomes — stalling, or quietly self-approving.

## A capability can be unverifiable rather than unavailable

`untested` implies you could have tested it. `VERIFIED NO` implies you did.
Neither fits a capability whose *verification itself* was refused — a blocked
tool call, a denied permission on the probe rather than the target, a network
path you were not allowed to try.

Record those as **`untestable`**, with what blocked the verification. The
difference matters downstream: an untestable capability may work perfectly and
is a question for a human, whereas a `VERIFIED NO` is a gap-report row with a
known grant attached.

## state.md

```markdown
# cert-lifecycle — build state
workspace: stg-shared
use_case:  "AI-driven certificate lifecycle automation"
updated:   2026-09-03T14:02Z

| Phase | Status | Artifact | Notes |
|---|---|---|---|
| 0 preflight | done | scope.md | runner online; readwrite; no RUNWHEN.md -> gaps |
| 0 scope+sources | done | scope.md | IaC: infra-flux-nonprod-beta @ main, clusters/beta |
| 1 recon | done 2026-09-02T11:04Z | probe/ | 90d retention; Cortex empty; k8s + gcp verified |
| 2 forks offered | done 2026-09-02T14:10Z | plan.md | 3 asked, 1 self-decided (reuse) |
| 2 surface + plan | approved 2026-09-02T15:40Z | surface.md, plan.md | cost dropped - no billing |
| 3 breadth | done | tasks/ | cert-expiry-horizon |
| 4 depth | 2/3 | tasks/ | cert-consumer-trace not started |
| 5 context layer | not started | | |
| 6 assemble | not started | | |
| 7 eval + handover | not started | | |

## Tasks

| Task | kind | validated | ran live | output ok | committed | auth re-verified | retrieval |
|---|---|---|---|---|---|---|---|
| cert-expiry-horizon | breadth | yes | yes | yes | yes | yes | pass |
| cert-chain-walk | depth | yes | yes | yes | yes | yes | pass |
| cert-served-vs-declared | depth | yes | yes | yes | yes | yes | **fail** |
| cert-consumer-trace | depth | — | — | — | — | — | — |
```

`retrieval: fail` is allowed to ship only alongside a note in the handover — but
it must be *recorded*, because a task nobody can find does not exist and the next
session will not re-check it.

---

## Resuming

On invoke, look for `.claude/agent-builds/*/state.md`. If any exist, **report and
ask** — never act on a checkpoint silently:

> `cert-lifecycle` — Phase 4, 2 of 3 depth tasks done. Probe is 34h old;
> re-verify passed (same identity, all four sources responding, retention
> unchanged). `cert-served-vs-declared` has a failing retrieval check.
> Resume at the third depth task, or somewhere else?

### Probe staleness

`findings.md` carries `probed_at` and, per source, what proved it. On resume:

1. Run `probe.py --verify` — identity, one query per source, the retention edge.
   Cheap by construction; it is not a second probe.
2. **Any disagreement marks the probe stale and forces a full re-probe.** A
   changed identity, a source that stopped responding, a retention edge that
   moved.
3. Clean verify **and** under 24h old → reuse the findings.
4. Clean verify but older → say the age out loud and let the human choose.

Reusing a stale probe reintroduces the exact failure this skill exists to
prevent, so the verify step is not optional. It is also what makes reuse safe
enough to be worth having.

### Never persist credential values

Probe output legitimately carries service-account emails, project IDs, cluster
names and permission denials — all of that stays; it is the evidence. **Token
contents, kubeconfig bodies and secret file contents are never written to disk.**
The probe prints the identity, never the credential.

---

## manifest.json

Written from the ledger in Phase 7. Always — including for a build that failed
partway, where it matters more.

```json
{
  "workspace": "stg-shared",
  "use_case": "certificate-lifecycle",
  "built_at": "2026-09-02T18:22:04Z",
  "built_by": "samir.patil@runwhen.com",
  "eval_result": "pass",

  "slxs": [
    {"name": "stg-shared--cert-expiry-inventory", "kind": "breadth",
     "resource_path": "custom/certificates", "retrieval_check": "pass"},
    {"name": "stg-shared--cert-chain-inspect", "kind": "depth",
     "resource_path": "custom/certificates", "retrieval_check": "pass"},
    {"name": "stg-shared--break-glass-gcp", "kind": "break_glass",
     "resource_path": "custom/break-glass", "retrieval_check": "n/a"}
  ],

  "rules":    [{"id": 412, "name": "cert-severity-framing", "scope_type": "persona"}],
  "commands": [
    {"id": 361, "name": "cert-report",  "scheduled": true,
     "cron": "0 13 * * 1-5", "sink": "email:samir.patil@runwhen.com",
     "max_runs": 30, "schedule_paused": true},
    {"id": 362, "name": "cert-inspect", "scheduled": false}
  ],

  "assistant":   {"short_name": "cert-warden", "action": "created"},
  "kb_articles": [{"note_id": 88, "title": "Certificate ownership model"}],

  "gaps_report":    "gaps.md",
  "handover":       "handover.html",
  "command_prompt": "command-prompt.md"
}
```

Field notes:

- `kind` is `breadth`, `depth` or `break_glass`. The `break_glass` entries exist
  so a later reader can find every arbitrary-CLI task in the workspace without
  reading scripts.
- `action` on the assistant records `created` (full upsert) or `updated`
  (fetch-merge-write) — it matters if someone needs to reconstruct prior config.
- `eval_result` records the Phase 7 verdict. Record `fail` or `blocked`
  honestly; a silent failure found six weeks later costs far more than an
  admitted one.
- `schedule_paused` is recorded because a paused schedule looks identical to a
  broken one from the outside.

---

## Teardown order

**Order is not cosmetic.** `delete_assistant` is a soft-delete and does **not**
cascade — deleting it first orphans rules and commands, and orphaned
persona-scoped rules keep loading into prompts.

```
1. commands    delete every id in commands[]   (stops the schedule first)
2. rules       delete every id in rules[]      (stops prompt crowding)
3. assistant   delete by short_name
4. slxs        delete_slx for every name in slxs[]
5. kb_articles delete every note_id            (optional - KB is additive and
                                                usually worth keeping)
```

Start with commands so a scheduled fire cannot land mid-teardown.

`delete_slx` and `delete_assistant` are **soft** deletes — they tombstone rather
than remove. The object stops appearing; it is not erased. Say so when reporting
a teardown as complete.

After teardown, re-list SLXs, rules, commands and assistants and diff against the
Phase 1 inventory. Anything left over is a manifest defect — fix the schema, not
just the workspace.
