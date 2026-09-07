# Eval protocol

Phase 7. This is not a smoke test. A smoke test proves the objects exist; an eval
proves the agent *uses* them when a real person asks a real question.

There is no dry-run tool in the MCP, so the eval runs through `workspace_chat`
against the persona — the same path a user takes.

---

## Writing the questions

Three to five questions, grounded in the workflow the customer actually described.
Each must require more than one step: find something, then say something about it.

**Strong** — the agent must select a task, run it, and interpret:

- "Which certificates expire in the next 30 days, and who owns them?"
- "api.example.com is expiring — what serves it and what breaks if it lapses?"
- "Are there any certificates with no named owner? Rank them by expiry."

**Weak** — tests the tool name, not the agent:

- "Run the cert task."
- "Execute cert-expiry-inventory."

A question that names the task cannot detect a retrieval failure, which is the
most common defect. Never name the task in the question.

Include at least one question the portfolio **cannot** answer — something that
landed in the gap report. The agent should say so plainly rather than inventing
an answer or reaching for the break-glass task.

---

## Running the eval

**If `workspace_chat` is unavailable, the eval cannot run.** Observed on
stg-shared: `Scrubbing system unavailable for this workspace; request blocked`.
Retrieval checks depend on the same path. Do not quietly skip them — record the
agent as **unverified**, say so in the manifest (`eval_result: blocked`) and in
the handover, and re-run when the service returns. An unverified agent may still
be delivered; it may not be described as working.

One session per question:

```
workspace_chat(workspace_name=<ws>, persona_name=<short_name>, message=<question>)
```

Record per question: the answer, which task was selected, how many tool calls it
took, and elapsed time. Keep the `chatUrl` — it is the evidence trail for the
handover.

---

## Reading the transcript

Four failure signatures, in the order they matter:

| Signature | What it means | Fix |
|---|---|---|
| Wrong task selected | two tasks look applicable | sharpen both `statement`s until they are disjoint |
| No task selected, generic answer | retrieval missed entirely | rewrite `alias`/`statement` as the sentence a user would type |
| Break-glass task selected | a purpose-built task should have won | narrow the break-glass `statement`; check it is excluded from the persona's task tags |
| Model doing arithmetic | the task returned raw data | move the computation in-script |

A retrieval miss sends you back to the SLX fields. **Do not fix it by adding a
rule** — that spends a scarce always-on slot to paper over a metadata problem,
and the next task will have the same defect.

---

## Pass bar

All of:

- every answerable question answered from the intended task
- no retrieval miss
- the break-glass task not selected while a purpose-built task existed
- the unanswerable question refused honestly, with the missing grant named
- no arithmetic performed by the model that the task should have done

Anything short of this goes back to Phase 3 or 5 before the agent is handed over.
Record the result in the manifest — an agent that shipped with a known eval
failure is worth knowing about six weeks later.
