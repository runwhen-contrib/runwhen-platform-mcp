---
name: triage-capability-tools
description: "Use when a cap_* MCP tool call fails or misbehaves in build mode: a 404/403/409 from a capability-fs or capability-run endpoint, cap_write/cap_edit coming back with validation findings, cap_test never finishing, cap_submit not producing an approval card, or the vendored capability-fs tool manifest looking stale against the platform. Also use before changing a cap_* tool, to see which platform endpoint and error path it actually goes through."
---

# Triage: capability build & run tools (cap_*)

Not shipped to end users — this is a maintainer/agent debugging aid for this repo.
(This repo ships its end-user skills as the top-level `skills/` package, which is
symlinked at `.claude/skills`; a doc placed there would be packaged into the wheel
via `pyproject.toml`'s `skills = ["**/SKILL.md", ...]` data-package glob. This file
lives outside that tree on purpose — see the PR that added it.)

## Scope

Eleven MCP tools give an agent workspace-authored ("build-mode") custom-capability
access:

- **Capability tasks** (`runwhen_platform_mcp/capability_tasks.py`): `cap_list`, `cap_run`
- **Capability build tools** (`runwhen_platform_mcp/capability_fs_tools.py` +
  `capability_fs_tools.json`): `cap_ls`, `cap_read`, `cap_glob`, `cap_grep`, `cap_write`,
  `cap_edit`, `cap_test`, `cap_diff`, `cap_submit`

Both modules are pure data/logic; the actual `@mcp.tool` functions and every HTTP call
live in `runwhen_platform_mcp/server.py` under the `# Capability tasks` and
`# Capability build tools` section headers.

## 1. Tool -> platform endpoint map

| Tool | Method | Path |
|---|---|---|
| `cap_list` | GET | `/api/v4/workspaces/{workspace}/capabilities?subject={resource}` |
| `cap_run` (describe) | GET | `/api/v4/workspaces/{workspace}/capabilities/{capability}/tasks/{task}?example=true[&subject=][&render=]` |
| `cap_run` (run, async) | POST | `/api/v4/workspaces/{workspace}/capability-runs`, then poll GET `.../tasks/{task}/runs/{run_id}?wait=&includeSchemas=true` |
| `cap_run` (run, sync CLI task) | POST | `/api/v4/workspaces/{workspace}/platform-cli/{cli_name}:run` |
| `cap_ls` | GET | `/api/v4/workspaces/{workspace}/capability-fs/ls` |
| `cap_read` | GET | `/api/v4/workspaces/{workspace}/capability-fs/read` |
| `cap_glob` | GET | `/api/v4/workspaces/{workspace}/capability-fs/glob` |
| `cap_grep` | GET | `/api/v4/workspaces/{workspace}/capability-fs/grep` |
| `cap_write` | POST | `/api/v4/workspaces/{workspace}/capability-fs/write` |
| `cap_edit` | POST | `/api/v4/workspaces/{workspace}/capability-fs/edit` |
| `cap_test` | POST | `/api/v4/workspaces/{workspace}/capability-fs/test` |
| `cap_diff` | GET | `/api/v4/workspaces/{workspace}/capability-fs/diff` |
| `cap_submit` | POST | `/api/v4/workspaces/{workspace}/capability-fs/submit` |

The nine `capability-fs` paths, methods and JSON-Schema parameters come straight out
of `capability_fs_tools.json` (`tool["path"].format(workspace=ws)` in `_capfs_call`).

## 2. How the manifest is vendored — and its real gap

`capability_fs_tools.py` loads `capability_fs_tools.json` once at import
(`CAPABILITY_FS_MANIFEST`, and `CAPABILITY_FS_TOOLS` keyed by tool name). Every
`cap_ls`..`cap_submit` wrapper in `server.py` pulls its `description` and per-parameter
descriptions from that file via `tool_description()` / `param_description()`, in both
stdio mode (`@mcp.tool(description=..., annotations=...)`) and HTTP mode
(`_TOOL_DESCRIPTIONS`, `_TOOL_ANNOTATIONS`) — the two transports are tested to expose
identical descriptions and `readOnlyHint` annotations
(`tests/test_capability_fs_tools.py::TestRegistration::test_http_mode_registers_the_same_descriptions_and_annotations`).

The module docstring says this JSON is "vendored verbatim... from the same manifest
the platform API serves at its own tools-introspection route." **There is no
automated test in this repo that fetches that live route and diffs it against
`capability_fs_tools.json`.** The unit tests only check internal consistency (exactly
nine tools, GET tools are read-only, POST tools write, every path starts with
`/api/v4/workspaces/{workspace}/capability-fs/`). Drift between the vendored copy and
what the platform actually serves/accepts is a real, unmonitored risk — see the
symptom table.

## 3. Auth / workspace params

- Every `cap_*` tool takes `workspace_name`; `_resolve_workspace()` (`server.py`)
  resolves it to the platform's short name via `GET /api/v3/workspaces`, matching
  exact short name, then case-insensitive short name, then display name. An empty
  `workspace_name` with no `DEFAULT_WORKSPACE` set, or a name that matches nothing,
  raises a `ValueError` that lists every workspace the caller's token can see — that's
  expected behavior, not a bug.
- Local/stdio auth: `RUNWHEN_TOKEN` (a platform JWT or Personal Access Token) and
  `RW_API_URL`; `DEFAULT_WORKSPACE` is optional. See `.env.example`.
- Every outbound request carries `X-RunWhen-Agent: mcp` (`_headers()` in `server.py`) —
  routes that only accept a human (e.g. publishing a capability) can and do refuse
  requests carrying it. Don't drop this header when reproducing an issue with a raw
  HTTP client, or you'll see different behavior than the real tool call.
- Role gating (HTTP/remote transport only): `cap_write`, `cap_edit`, `cap_test`,
  `cap_submit` are in `authorization.WRITE_TOOLS` and need at least
  `WorkspaceRole.READ_WRITE` (`minimum_role_for_tool`); every other `cap_*` tool needs
  only `READ_ONLY`. This check (`_make_workspace_auth_check` in `server.py`) only runs
  in HTTP mode — stdio mode has no client-side role check, so a role problem there
  surfaces as a 403 straight from the platform.

## 4. Error shapes — and a key asymmetry between the two tool families

`_raise_for_papi_status` (`server.py`) turns any non-2xx response into a `ValueError`:

- `401` -> `"PAPI returned 401 Unauthorized for {path}. Your RUNWHEN_TOKEN may be expired or invalid. Get a fresh token from POST /api/v3/token/ or the RunWhen UI."`
- `403` -> `"PAPI returned 403 Forbidden for {path}. You may not have access to this workspace or resource."`
- any other error status -> `"PAPI returned {status} for {path}: {body_excerpt}"`, where
  `body_excerpt` is the response's JSON (or raw text) truncated to 1000 characters — a
  404 or 409 always carries the platform's own error body inline.

Where that exception ends up differs by tool family — this is the single most useful
thing to know before debugging a `cap_*` failure:

- `cap_ls`, `cap_read`, `cap_glob`, `cap_grep`, `cap_write`, `cap_edit`, `cap_test`,
  `cap_diff`, `cap_submit` all go through `_capfs_call`, which explicitly catches
  `(ValueError, httpx.HTTPStatusError)` and returns it as a **normal, non-error tool
  result**: a JSON string `{"error": "..."}`
  (`tests/test_capability_fs_tools.py::TestErrors`). An agent calling these tools sees
  a *successful* tool call whose body happens to be an error object — check the body,
  don't just check for a thrown exception.
- `cap_list` and `cap_run` do **not** wrap their platform calls in try/except — an
  error there propagates out of the tool function as a genuine exception, which
  surfaces as an actual tool-call failure (`isError`), not a JSON body. Don't expect a
  `{"error": ...}` payload from a `cap_list`/`cap_run` failure the way you would from
  the build tools.

## 5. Draft / publish lifecycle, as seen from the tools

`/capabilities` is a tree: `README.md` (the authoring guide), `schema.json` (the
`capability.yaml` schema), and one folder per capability. Packaged capabilities are
read-only; a custom capability can have an open draft.

- Writing any file under `/capabilities/<name>/` via `cap_write` or `cap_edit` opens a
  draft automatically if one isn't already open — there is no separate "start draft"
  tool.
- `cap_ls` on a custom capability's folder shows its open draft if there is one, else
  its published version.
- `cap_write` / `cap_edit` return **validation findings** (file + line) in their JSON
  body. A `200` with findings is not success — fix the findings before testing. This
  is not an HTTP error, so `_capfs_call`'s `{"error": ...}` branch does not catch it;
  you must read the full response body.
- `cap_diff` shows pending drafts (all, or one via `path`): changed files as unified
  diffs, validation state, which tasks still need a passing test, and how the
  resources the capability applies to would change.
- `cap_test` runs one task of a draft against a real resource and is the only source
  of "test evidence" — it can run non-read-only tasks too, since it's pre-publish
  verification, not a production run.
- `cap_submit` needs a clean validation and a passing `cap_test` for every changed
  task. **It does not publish.** It shows a workspace admin an approval card in the
  UI; only their approval there publishes the draft. After a successful `cap_submit`,
  tell the person to go review and approve the card — the capability is not live yet.

## 6. `cap_test` vs `cap_run` — don't confuse them

- `cap_test` (build tool) tests a **draft**, addressed by capability folder path
  (`/capabilities/<name>`) plus a task name; it can run non-read-only tasks.
  `wait_seconds` is capped at 60 per call; pass the returned `run_id` back in to keep
  polling instead of starting a new run.
- `cap_run` (capability-task tool) runs a **published** task, addressed by
  `"capability/task"`, and only if it is read-only — `server.py` checks
  `described.get("readOnly")` and raises `"{task} is not read-only; only read-only
  tasks can be run."` otherwise. Its wait is capped at 120 seconds
  (`_CAP_MAX_WAIT_SECONDS`); a run still `queued`/`running` after that returns a
  `runId` and a message to call again with `run_id` rather than starting a second run
  (`OPEN_STATUSES = {"queued", "running"}`).
- `cap_run` also transparently supports two invocation styles: an async
  capability-run (`POST .../capability-runs`, then poll) and a synchronous
  platform-CLI-backed task (`POST .../platform-cli/{cli_name}:run`). For the latter,
  `inputs` must be exactly `{command, maxBytes?, timeoutSeconds?}`
  (`CLI_INPUT_SCHEMA`) — anything else is rejected by `cli_input_errors` before any
  HTTP call is made, and `command` must not contain pipes, redirects or `;`.

## 7. Running the tools locally against a platform

From the repo root:

```bash
pip install -e .
export RW_API_URL="https://papi.<env>.runwhen.com"   # or a self-hosted platform URL
export RUNWHEN_TOKEN="<PAT or JWT>"
export DEFAULT_WORKSPACE="<workspace-short-name>"     # optional
runwhen-platform-mcp
```

Point an MCP client (Cursor, Claude Desktop, etc.) at this stdio binary — see
README.md's "Local MCP against staging" section for a ready `mcp.json` snippet.
Restart the client's MCP connection after editing `server.py`; changes don't hot-reload.

Get a token from the RunWhen UI (Profile -> Personal Tokens) or
`POST {RW_API_URL}/api/v3/token/` with email + password.

## 8. Tests to run

```bash
pytest tests/test_capability_fs_tools.py -v   # cap_ls..cap_submit: manifest shape, registration, auth, error JSON
pytest tests/test_capability_tasks.py -v      # cap_list/cap_run: describe/run, sync-CLI path, wait/resume
pytest tests/ -v                              # full unit suite before opening a PR
```

Live smoke (needs a real token + workspace; hits the real platform, so treat it as a
last resort):

```bash
RUNWHEN_TOKEN=... RW_API_URL=https://papi.<env>.runwhen.com RW_SMOKE_WORKSPACE=<workspace> \
  pytest tests/test_papi_live_smoke.py -m integration -v
```

(`RW_SMOKE_WORKSPACE` defaults to `t-oncall`; the test module skips automatically if
`RUNWHEN_TOKEN`/`RW_API_URL` aren't set.) `pre-commit run --all-files` runs the same
Ruff check + format CI runs.

## 9. Symptom -> check -> fix

| Symptom | Check | Fix |
|---|---|---|
| `cap_ls`/`cap_read`/... returns `{"error": "PAPI returned 404 for ..."}` | Path typo, or the capability doesn't exist at that path yet | `cap_ls` the parent folder first — capability paths are always under `/capabilities/<name>/...` |
| `{"error": "PAPI returned 409 for ...: <body>"}` on `cap_write`/`cap_edit`/`cap_submit` | Read the body excerpt (truncated to 1000 chars) — usually a draft conflict | Call `cap_diff` first to see the current draft state before writing or submitting again |
| `cap_write`/`cap_edit` "succeeds" (no `{"error": ...}`) but nothing looks fixed | The response can still carry `valid: false` and file/line findings — only a raised `ValueError` produces `{"error": ...}` | Always inspect the full JSON body of `cap_write`/`cap_edit` results, not just whether the call raised |
| `cap_test` never returns a final status | `wait_seconds` is capped at 60 per call | Re-call `cap_test` with the same `path`/`task` and the returned `run_id` to keep polling; don't start a new run |
| `cap_list`/`cap_run` blow up instead of returning `{"error": ...}` | These two don't go through `_capfs_call`'s try/except | Expect a genuine tool-call failure from `cap_list`/`cap_run`, not a JSON error body, and handle it accordingly |
| `cap_run` says `"... is not read-only; only read-only tasks can be run."` | The task's `readOnly` flag (from `cap_list`, or `cap_run` describe) is false | Use `cap_test` against the draft instead, or have an admin run it |
| `cap_run(operation="run")` on a sync CLI task rejects `inputs` | `inputs` has a key other than `command`/`maxBytes`/`timeoutSeconds`, or is missing `command` | Match `CLI_INPUT_SCHEMA` exactly: one `command` string, no pipes/redirects/`;` |
| Vendored tool description or parameters look stale vs. what the platform accepts | No automated test compares `capability_fs_tools.json` against the platform's live tool manifest (see section 2) | Compare `capability_fs_tools.json`'s tool entries (name/method/path/parameters) by hand against the platform's own introspection response for the same workspace; edit the JSON file, not `server.py`, and re-run `tests/test_capability_fs_tools.py` |
| `workspace_name is required` / `Workspace '<x>' not found` | `_resolve_workspace` couldn't match the given name | Use one of the short names listed in the error — it lists every workspace visible to the token |
| Every call returns 401 | Token expired or invalid | Get a fresh `RUNWHEN_TOKEN` (a Personal Access Token lasts up to 180 days; an email/password token lasts 1 day) |

## References

- `runwhen_platform_mcp/capability_tasks.py` — `cap_list`/`cap_run` result shaping, `CLI_INPUT_SCHEMA`, `split_task`/`task_endpoint`
- `runwhen_platform_mcp/capability_fs_tools.py` + `capability_fs_tools.json` — the vendored nine-tool manifest
- `runwhen_platform_mcp/server.py` — `_papi_get`/`_papi_post`/`_raise_for_papi_status`/`_headers`/`_resolve_workspace`, and the `cap_*` tool functions (`# Capability tasks` / `# Capability build tools` sections)
- `runwhen_platform_mcp/authorization.py` — `WorkspaceRole`, `WRITE_TOOLS`, `minimum_role_for_tool`
- `tests/test_capability_tasks.py`, `tests/test_capability_fs_tools.py`
- `README.md` — "Tools" (Capability tasks / Capability build tools) and "Development and testing"
