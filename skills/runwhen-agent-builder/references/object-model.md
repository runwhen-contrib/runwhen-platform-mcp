# Object model — what an agent is made of

Verified against platform source. **This is the contract. Do not re-derive it,
and do not guess parameter names — every name here is exact.**

A working RunWhen agent is seven object types across four APIs:

```
SLX + Runbook (task)      the capability          commit_slx
Assistant (persona)       the identity + filter   create_assistant / update_assistant
Chat rule                 always-on behaviour     create_chat_rule
Chat command              invocable procedure     create_chat_command
Scheduled command         a chat command + cron   create_chat_command(cron_schedule=...)
KB article                retrieved knowledge     create_knowledge_base_article
```

## What each container is for

| Container | Holds | Visible to | Lifetime |
|---|---|---|---|
| **Task (SLX)** | collection + reduction | — | per run |
| **stdout** | rich structured data | the agent, in-session | that run only |
| **Issues** | the report + tripwires | agent, humans, `workspace_chat` | durable, searchable |
| **Rules** (persona) | behaviour — how to reason and write | one persona, **every turn** | until edited |
| **KB articles** (workspace) | durable environment facts | every persona + humans, on relevance | until edited |
| **Command** | the job to perform | the persona running it | per invocation |

Put the report and current state in issues. Put durable facts in the KB. Put
behaviour in rules. **Never put current readings in the KB** — KB articles have
no expiry, so a stale reading becomes a confidently-stated falsehood that the
agent will trust indefinitely.

---

## commit_slx

`commit_slx` does **not** accept Robot Framework. It takes a bash or python
script, base64-encodes it into `GEN_CMD`, and embeds it in the fixed Tool Builder
wrapper (`rw-generic-codecollection/codebundles/tool-builder/runbook.robot`). You
author the script; the Robot layer is never yours to write.

Parameters:

| Param | Notes |
|---|---|
| `slx_name` | kebab-case, validated. Becomes `{workspace}--{slx_name}` |
| `alias` | short display name — **retrieval surface**, see task-contract.md |
| `statement` | what good looks like — **retrieval surface** |
| `script` | the bash/python body |
| `task_title` | human-readable task title; unresolved `{placeholders}` are rejected |
| `interpreter` | `bash` or `python` |
| `access` | `read-only` or `read-write` — nothing else |
| `data` | `logs-bulk`, `config` or `logs-stacktrace` — nothing else |
| `resource_path` | force-prefixed `custom/` by `_enforce_custom_resource_path`, even if you pass a bare path |
| `hierarchy` | list of tag names; conventionally starts `["platform", ...]` |
| `tags` | `[{name, value}]`; pair the hierarchy with `{"name":"platform","value":"custom"}` |
| `env_vars` | config variables — see Variable families |
| `secret_vars` | secret mapping — see Variable families |
| `runtime_vars` | user-supplied variables — see Variable families |
| `cron_schedule` | schedules the **task**, not a chat. Mutually exclusive with `sli_script`. Not the same thing as a scheduled command |
| `workspace_name` | required |
| `location` | **omit it** when `get_workspace_locations` reports `auto_resolves: true` |

Requires admin or readwrite role. Allow **1–3 minutes for reconciliation** before
the SLX is queryable or runnable.

### Script contract

- **bash** — define `main()`; write the issue JSON **array** to **file
  descriptor 3** (`>&3`), not stdout. Build it with `jq`, never string concat.
- **python** — define `main()` returning `List[Dict]`.
- **Never call `main` yourself.** A trailing `main "$@"` in a bash script triggers
  a preflight invocation with FD 3 read-only and produces misleading
  `Bad file descriptor` errors. The runner sources the script and calls `main()`
  itself with FD 3 wired to `run_output.json`.
- Issue keys are **prefixed** and exact — this is the single most common authoring
  error:

```
"issue title"        str
"issue description"  str
"issue severity"     int 1-4
"issue next steps"   str
"issue observed at"  optional
```

  Not `title`/`description`/`severity`/`next steps`. The `issue ` prefix is
  required on all four.
- Emit an unconditional severity-4 summary issue so a healthy run still reports.

Validation runs statically in both `validate_script` and `commit_slx`, and
dynamically over emitted issues in `run_script_and_wait`. `validate_script` is a
fast pre-check, not a gate you can skip by going straight to commit.

### The build loop

```
validate_script  ->  run_script_and_wait  ->  iterate  ->  commit_slx  ->  wait 1-3 min  ->  retrieval check
```

Never commit a script that has not completed a successful `run_script_and_wait`.

`commit_slx` is **idempotent on `slx_name`** — re-committing an existing name
reports `slx: unchanged, runbook: updated`. Safe to iterate on.

---

## Variable families

Three families. Name collisions **across** families are rejected at commit time.

| Family | Param | Bound when | Who supplies it |
|---|---|---|---|
| Config | `env_vars: dict[str,str]` | commit time, then fixed | task author |
| Secret | `secret_vars: dict[str,str]` | commit time, then fixed | task author |
| Runtime | `runtime_vars: list[dict]` | **declared** at commit, **valued per invocation** | end user or the agent |

### The visibility boundary

**The agent sees the task name and its runtime vars. It cannot see `env_vars`.**

Two consequences, both non-obvious:

1. A rule or command that names a config variable (*"never project below
   `MIN_SAMPLES`"*) is unactionable — the agent cannot read that value.
2. Any config that changes how output should be *interpreted* must be **echoed
   into the task's own output**. Emit a `run_metadata` block: the window actually
   used, thresholds applied, conventions in force, data-quality counts. See
   `task-contract.md`.

**Thresholds belong in `env_vars`, specifically because they gate issue
creation.** If the agent could retune them per run it could manufacture or
suppress its own issues, and day-over-day comparison would stop meaning
anything. Runtime vars are for what the agent legitimately tunes: lookback
window, granularity, result count, a pivot or filter.

Runtime var `description` fields are the agent's **only** interface
documentation. Write them as instructions to the agent (*"Use 90 for the daily
brief; drop to 7-14 when investigating a recent change"*), not as definitions
for a human.

### Runtime var schema

```json
{"name": "CERT_NAME",
 "description": "Certificate to inspect",
 "default": "",
 "validation": {"type": "regex", "pattern": "^[a-z0-9.-]+$"}}
```

- `validation.type` is `regex` (with `pattern`) or `enum` (with `values`).
- `default` must be present. `""` is valid and means "leave unset".
- Runtime vars are **task-only** — rejected for `task_type='sli'`.
- Stored on the runbook as `runtime_vars_provided`; resolved values land on
  `run_requests.runtime_var_values` per invocation.
- Override at run time with `runtime_var_overrides` on `run_slx` or
  `run_script_and_wait`. Unknown keys are rejected; `validation` is enforced.

### Secrets are file paths, not values

A secret env var holds a **path to a file** containing the secret. Read it:

```python
v = os.environ.get("NAME")
secret = open(v).read().strip() if os.path.isfile(v) else v
```

### Two sources of secrets, and only one is trustworthy

There are **two independent places** a secret can come from, and they do not
agree with each other:

| Source | How you see it | Trust |
|---|---|---|
| **Workspace vault** | `get_workspace_secrets` → `secrets` / `secret_vars_for_author_run` | **advisory only** |
| **Runner-side k8s secrets** | `secretsProvided` on an already-committed SLX, as `k8s:file@...` or `k8s:value@...` | **authoritative** |

**A workspaceKey takes more than one form, and both are valid.** Observed on the
same workspace:

```
k8s:file@secret/gcp-sa:gcp-sa                 <- runner-side k8s secret
ROHIT_CLASSIC_GITHUB_PAT_FOR_STOXX_TRACKER    <- bare vault name
```

So do not apply a rule like "always use the `k8s:file@` form". **Copy the
`workspaceKey` verbatim from a working SLX** — the form is a property of how that
secret was provisioned, not something you can derive.

`get_workspace_secrets` synthesises a workspaceKey from each vault name — it
turns `ops-suite-sa` into `k8s:file@secret/ops-suite-sa:ops-suite-sa`. **That
synthesised key is a guess.** If no k8s secret of that name exists on the runner,
the run dies during secret import, before `main()`, with completely empty stdout
and stderr.

**Always prefer copying `secretsProvided` verbatim from a committed SLX that
runs successfully.** Find one with `get_slx_runbook`, take both the `name` and
the `workspaceKey` exactly as written.

Two qualifications that the word "authoritative" hides:

- **It is authoritative for that SLX, not for your inline run.** The asymmetry
  cuts both ways: a key proven inline can still fail once committed (different
  resolution mechanism), *and* a key working on a committed SLX can fail inline.
  Neither direction is evidence about the other — only a run in the same mode
  is.
- **"Exactly as written" is not what gets sent.** The MCP rewrites short keys,
  e.g. `Expanded secret_vars['GITHUB_TOKEN']='GITHUB_TOKEN' to workspaceKey
  'k8s:file@secret/GITHUB_TOKEN:GITHUB_TOKEN'`. **Read `secret_vars_resolved`
  in the response** and confirm it is the key you intended before treating a
  failure as a permissions problem. Runner-side secrets exist only there —
they are invisible to the vault listing.

Observed on stg-shared: the vault advertised `ops-suite-sa`, which fails to
import. The GCP secret that actually works is
`{"gcp_credentials": "k8s:file@secret/gcp-sa:gcp-sa"}` — a different secret
name *and* a different env-var name, discoverable only by reading a working SLX.

Env-var names follow the neighbouring SLX, not your preference: `gcp_credentials`
and `kubeconfig` stay lowercase because that is what the codebundles expect.

### Building the merged secret inventory

Phase 1 must produce **one table reconciling both sources**, not a vault listing.
Build it like this:

1. **Vault side.** `get_workspace_secrets(workspace_name)` → take `secrets`
   (the names) and `platform_groups`. Treat every `secret_vars_for_author_run`
   value as a *candidate*, never as fact.

2. **Runner side — and the tool you reach for first is usually the wrong one.**
   You need `secretsProvided`, which lives only on a runbook.

   | Tool | Returns `secretsProvided`? |
   |---|---|
   | `get_workspace_slxs` | yes, but **can drop the whole MCP connection** above a few hundred SLXs |
   | `workspace_chat` | **no** — verbatim: *"the `secretsProvided` / `workspaceKey` fields are internal SLX config attributes that the chat API does not expose in the SLX detail views, search results, or task listings"* |
   | `get_workspace_config_index` | **no** — carries zero secrets |
   | `get_slx_runbook` | **yes**, but takes only `slx_name`, so you need names first |

   **The scale-safe path is `get_workspace_config_index` for the names, then
   `get_slx_runbook` per candidate.** Reaching for `workspace_chat` because it
   is the documented alternative for *enumeration* wastes a pass — it cannot
   answer this question at all.

   **Only harvest from an SLX that has actually RUN.** Require
   `lastRunCode: CODE_RUN_STATUS_COMPLETED` and a non-null `lastRunAt`. A key
   declared on an SLX that never executed is exactly as unproven as a
   vault-synthesised one, and the failure looks identical. Observed: a build
   copied `ops-suite-sa` verbatim from a committed SLX with `lastRunAt: null`
   and lost a probe run to
   `Import Secret gcp_credentials: Secret at key 'ops-suite-sa' not found`.
   Record `lastRunAt` beside every row of the inventory so an unproven source
   is visible rather than inferred.

   Note `search_workspace` is an autocomplete shim and returns 503 when that
   service is down.

   See **Queries that lie** below before drawing any conclusion from an
   enumeration. Collect every
   `secretsProvided` entry as `{name, workspaceKey}`. Prefer SLXs across
   different platforms so you cover the estate; you do not need all of them.
   Dedupe by `workspaceKey` — many SLXs share one.

3. **Merge and mark provenance.** One row per distinct `workspaceKey`:

```
ENV VAR              WORKSPACE KEY                          SOURCE             VERIFIED
gcp_credentials      k8s:file@secret/gcp-sa:gcp-sa          slx:gcp-prj-cst    yes (2410 bytes)
STOXX_GITHUB_TOKEN   ROHIT_CLASSIC_GITHUB_PAT_FOR_STOXX...  slx:stoxx-repo-upd yes (bare vault form)
ops-suite-sa         k8s:file@secret/ops-suite-sa:...       vault (synth)      NO - import fails
mailgun              k8s:file@secret/mailgun:mailgun        vault (synth)      untested
```

4. **Verify each distinct key once**, with a probe that only reads the file:

```bash
main() {
    echo "path=${gcp_credentials:-unset}"
    [ -f "${gcp_credentials:-/nonexistent}" ] \
      && echo "OK $(wc -c < "$gcp_credentials") bytes" || echo "UNREADABLE"
    echo '[{"issue title":"Secret probe","issue description":"Verifying one workspace secret imports and is readable on the runner.","issue severity":4,"issue next steps":"Use only verified keys when authoring tasks."}]' >&3
}
```

   **One key per script**, run in parallel — never several keys in one script.
   A bad key dies during secret import *before* `main()` with completely empty
   stdout, so one bad key in a shared script destroys the evidence for every
   other key in it.

5. **Decide from the table, not from the vault.**
   - `VERIFIED yes` → usable; copy the env-var name verbatim too.
   - `VERIFIED NO` → the capability that needed it is **blocked**; it becomes a
     gap-report row naming the secret and the observed import error.
   - `untested` → do not design around it. Either probe it or treat it as blocked.

A row that appears **only** in the vault and nowhere in any SLX is the highest-risk
kind: it looks available and usually is not.

### Raw scripts need the TOOL's variable names, not the SLX's

A committed SLX maps secrets to whatever the codebundle expects - commonly
`kubeconfig` and `gcp_credentials`, lowercase, because the Robot layer imports
them and wires the tools itself. **A raw script authored through
`run_script_and_wait` gets none of that wiring.** It needs the names the tools
themselves read:

```
KUBECONFIG                       <- k8s:file@secret/kubeconfig:kubeconfig
GOOGLE_APPLICATION_CREDENTIALS   <- k8s:file@secret/gcp-sa:gcp-sa
```

Copy the *workspaceKey* verbatim from a working SLX, but map it to the env-var
name your script's tooling expects, not the neighbour's.

**And `gcloud` does not use `GOOGLE_APPLICATION_CREDENTIALS` for CLI calls.**
Without an explicit
`gcloud auth activate-service-account --key-file "$GOOGLE_APPLICATION_CREDENTIALS"`
it silently falls back to the pod's Workload Identity and returns 403 on
everything - indistinguishable from a missing grant, and it will send you
hunting for an IAM problem that does not exist.

**Activate before the first call, not lazily.** Observed: activation hidden
inside a `token()` helper meant the two `gcloud` sections that ran before the
first API call authenticated as the wrong principal and 403'd, while everything
after them succeeded. A partial-success pattern like that reads as a permissions
boundary rather than an ordering bug.

### Which identity does the credential carry?

The load-bearing question, and the one that disguises itself when wrong. A
credential can be present, valid, importable **and belong to a service account
in a different project from the one you are querying** — which returns 403 and
reads exactly like a missing permission.

Observed: a runner service account living in a *shared* project, querying a
*beta* project's Cloud Monitoring. Sending the beta project as
`x-goog-user-project` returned 403 on every metric. The credential was fine.

Print the identity before concluding anything about permissions —
`gcloud auth list`, `kubectl auth whoami`, `aws sts get-caller-identity`.

### Auth is not proven until after commit

`run_script_and_wait` takes `secret_vars` **inline**; a committed SLX declares
`secretsProvided` and resolves them through the workspace. Different mechanism,
different failure mode. A passing probe does not prove a committed task will
authenticate — re-run it through the committed SLX before you trust it.

### Verify before you design

For every secret the plan depends on, run a five-line probe that reads the file
and prints its size **before** designing a task around it. A credential that is
listed but unreadable is a gap-report row, not a capability.

### Precedence at run time

```
workspace config_provided -> SLX config_provided -> runbook config_provided -> runtime values LAST
```

Runtime values overwrite matching keys. Nothing overwrites them.

---

## Assistant

`create_assistant` is a **full upsert**. Calling it on an existing short name
replaces the whole config and silently resets every field you omitted. On a
re-run use `update_assistant`, which fetch-merge-writes.

Fields that matter:

| Field | Effect |
|---|---|
| `filterCodebundleTaskTags` | which task tags this assistant will surface |
| `searchFilters` | e.g. `{"codebundleTaskTags": [...], "slxGroup": [...]}` — how you pin an assistant to the SLXs you built |
| `filterConfidenceThreshold` | issue-surfacing confidence floor |
| `runConfidenceThreshold`, `runConfig` | autonomous execution tuning |

**A tight `searchFilters` pin defeats your own reuse decision.** Pinning a
persona to the resource path of the SLXs you just built hides everything you
deliberately chose *not* to rebuild. Observed: a plan called for pinning to
`custom/capacity`, which would have hidden the existing cost SLX that the whole
design depends on citing. If Filter 0 sent you to an existing bundle, the
persona must still be able to see it - filter by access tag rather than by your
own resource path, and let `alias`/`statement` do the retrieval work.

An assistant **owns nothing by foreign key**. Rules and commands attach by string
match on `scope_id == "{workspace}--{short_name}"`. `delete_assistant` is a
soft-delete and does **not** cascade — orphaned rules and commands survive it.

The LLM model is **not** a persona field. It resolves by `(category, workspace)`
on `LLMConfigModel`. Do not promise per-assistant model selection.

---

## Rules vs commands

| | Rule | Command |
|---|---|---|
| Loaded | **every turn**, all in-scope rules concatenated | only when cited as `[name](http://chat-command/{id})` |
| Crowding | **no cap, no ranking, no truncation** | none — uncited commands cost nothing |
| Content | `rule_content`, 1–3 sentences | `command_content`, a full procedure |
| Name | free text | alphanumeric, `_`, `-` only |
| Schedulable | no | yes |

Scope resolution is workspace → persona → user, with more specific scopes
overriding on conflict.

**Consequence:** rules are a scarce, shared resource. A workspace-scoped rule
loads into every other assistant's prompt too. Keep rules persona-scoped and
capped at five. Put "when to call what" in command content, where it costs
nothing until invoked.

---

## Scheduled command

Not a separate object. It is a chat command with scheduling fields set:

| Field | Notes |
|---|---|
| `cron_schedule` | croniter-validated at write time |
| `sink_configs` | `[{type: email\|slack, mode: user\|all-workspace-users\|channel\|webhook, target, options}]` |
| `run_as_user` | **required** when scheduling; the session runs as this email |
| `assistant_name` | must equal `scope_id` for persona-scoped commands |
| `max_runs` | **API-bounded 1–30.** Increments on any `SUCCEEDED` run — **manual invocations included, even while `schedule_paused` is true** |
| `auto_approve_readonly` | **the breadth/depth switch** |
| `schedule_paused` | pauses cron without losing config; distinct from `is_active`. **Create with `true`** — unpausing starts outward-facing delivery and is the human's call |

Two traps:

1. **`auto_approve_readonly: false` means the scheduled agent will not run tasks
   at all** — it is instructed to skip `run_task` and only recommend. A breadth
   command that is supposed to gather data needs this `true`.
2. **`max_runs` ≤ 30 means every schedule expires — sooner than the cron implies.**
   The counter increments on **manual runs too**, and it does so while
   `schedule_paused` is `true`. Observed: a paused command showed
   `runsCompleted: 1` and a `lastFiredAt` after a human invoked it once by hand.
   Two consequences: the budget is shared between the humans testing it and the
   scheduler, so a daily cron lasts *fewer* than 30 days; and **a rising
   `runsCompleted` on a paused command is not evidence the pause is leaking** —
   check for manual runs before reporting a platform fault. Say both in the
   handover.

Do not confuse this with `commit_slx(cron_schedule=...)`, which schedules a
task's own runbook and involves no chat, no assistant and no sink.

---

## Knowledge base

`create_knowledge_base_article(content, workspace_name, title, resource_paths,
abstract_entities)`. `content` ≤ 20000 chars, `title` ≤ 255 and strongly
recommended for search weighting.

**KB articles cannot be persona-scoped.** They scope to the workspace plus
`resource_paths`/`resource_selectors` only. The one lever available is to scope
articles to the `custom/...` resource paths your tasks use.

Retrieved semantically at chat time, so KB is the right home for anything that
should be available *when relevant* rather than on every turn.

Two conventions that bite:

- KB `resource_paths` use `kubernetes/<cluster>/<ns>/<workload>` — **no
  `/resources/` prefix**, unlike SLX resource paths.
- `list_knowledge_base_articles(search=...)` is a **literal** match, not
  semantic. Semantic KB search happens through `workspace_chat`.

---

## Queries that lie

The most expensive mistakes in this workflow share one shape: **a query returns
plausible data instead of an error, and you conclude something false from it.**
Every instance below was observed on a real build, and each one produced a
confident wrong answer that survived several steps before being caught.

| What was run | What came back | What was wrongly concluded |
|---|---|---|
| `kubectl` with no kubeconfig mapped | connection refused to `localhost:8080` | "Kubernetes is unreachable" — it was fully reachable |
| `grep ... \| head -20` on an SLX list | the first 20 matches | "2 of 16 namespaces covered" — it was 10 |
| papi `/slxs?page=N` | page 1, every time | "1000 SLXs" — 1000 rows, 100 unique, true count 736 |
| `get_workspace_secrets` vault entry | a synthesised `k8s:file@...` key | "the GCP credential is available" — it fails to import |

**The rule: never conclude absence, or capability, from a query you have not
validated returns what you think it returns.**

Three habits that catch all of these:

1. **Cross-check counts against the source's own total.** If the API reports
   `count: 736`, your unique row count must equal 736. `sort -u | wc -l`.
2. **Never `head` a filtered list and treat the head as the whole set.** If you
   truncate for readability, say "first N of M" — never reason from the head.
3. **Never infer a capability gap from a probe that did not carry the
   credential.** Confirm every denial with an explicit authorisation check
   (`kubectl auth can-i`, or the platform equivalent) before it becomes a
   gap-report row.

Specifics worth knowing for SLX enumeration: `get_workspace_slxs` can exceed the
MCP transport on a large workspace. It does not always fail politely - observed
on a ~735-SLX workspace, it returned `Connection closed` **and took the entire
MCP server down with it**, requiring a manual `/mcp` reconnect before any tool
was usable again. Treat it as unsafe above a few hundred SLXs: page papi
directly with `GET /api/v3/workspaces/{ws}/slxs?limit=100&offset=N`, or use
`workspace_chat` when you only need a filtered view rather than an enumeration. **Pagination
is `limit`/`offset` — a `page=` parameter is silently ignored.** The `next` URL is
emitted as `http://` and 308-redirects to an HTML error page if you do not follow
redirects.

## `run_slx` returning no issues does not mean no issues

**`run_slx` can return `"issues": []` while the run's issues are still being
written.** Observed twice in one build, independently: a post-commit run
reported an empty array, and 52 seconds later the summary issue existed and five
tripwires had each incremented to two occurrences. A second task saw its issues
land 7-17 seconds after the call returned.

This is the most dangerous false negative in the workflow, because it lands
exactly on the gate that is supposed to catch problems: you re-run through the
committed SLX, see no issues, and conclude the task is broken - or worse, that
it is fine and emits nothing.

**Confirm against the SLX's own issue list before drawing any conclusion**, via
`workspace_chat` on `/slxs/<name>/issues` or by re-reading after a pause. Judge
by what the SLX holds, never by what the run call returned.

## Debugging a FAILED run

`run_script_and_wait` returning `finalStatus: FAILED` with **empty `stdout`,
empty `stderr`, and zero issues** means the run died before `main()` executed.
The returned `debug_log_snippet` is the head of Robot's `log.html` — i.e. CSS.
It never contains the error. Do not read it.

Bisect in this order:

1. **Re-run the identical script with `secret_vars` removed.** If it now
   succeeds, the fault is a secret mapping, not your code. This single test
   resolves most empty-output failures.
2. **Fetch the real error.** `artifact_urls.debug` is a presigned `log.html`
   (valid ~10 minutes). Download it and grep for `Error:`:

```bash
curl -s "<artifact_urls.debug>" -o log.html
grep -oE 'Error: [^"<]{0,200}' log.html | sort -u
```

   A failed secret import reads:
   `Error: Import Secret <VAR>: Kubernetes API request failed`.

3. **Only then suspect the script.** Reduce to `echo` plus one issue on FD 3 and
   add back one construct at a time.

Note also that `finalStatus: FAILED` on an author/debug run does **not** always
mean failure — debug runs derive status from `passed_titles` against a hardcoded
`TASK_TITLE='dev-test'`. Judge by whether `stdout` and `issues` are populated.

## Handoff mechanics

How a scheduled breadth run becomes a deep investigation:

1. Cron fires. The **same** root agent as interactive chat runs, seeded with the
   command content.
2. A separate agent generates **exactly three** follow-up actions:
   `{label: ≤6 words, prompt: ≤25 words}`, along three fixed angles — drill into
   the lead / verify-or-correlate / compare-to-history. It is instructed to copy
   a resource name and time window **verbatim from the artifact**.
3. Malformed output is dropped to `[]` and the email falls back to a generic
   "Continue in chat" button.
4. Clicking forks the session — deep-copying **all events and artifacts** — and
   auto-submits the prompt into an ordinary interactive session.

**The three actions are not authorable.** The only control surface is what the
breadth report says. This is why the report must name its own follow-up tasks —
see `task-contract.md`.

**There is no dry-run tool in the MCP.** The endpoint exists in the platform but
is not exposed here. Verify a command by running its content through
`workspace_chat`, not by waiting for a real fire.
