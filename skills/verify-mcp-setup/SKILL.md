---
name: verify-mcp-setup
description: "Validate a RunWhen MCP server installation is working end-to-end. Use when: (1) First connecting to a new MCP server, (2) After deployment, upgrade, or config change, (3) The user asks to verify, validate, check, or test the MCP setup, (4) Troubleshooting connectivity or permission issues, or (5) The user says 'checklist', 'smoke test', or 'health check' about the MCP server itself."
---

# Verify MCP Setup

Quick smoke test to confirm the RunWhen MCP server is connected,
authenticated, and functional. Each step only checks for a valid response —
it does NOT need to enumerate or display the returned data.

## When to use

- First time connecting to a RunWhen MCP server
- After deploying or upgrading the server
- After changing environment variables (`RW_API_URL`, `RUNWHEN_TOKEN`, etc.)
- User asks to "verify", "validate", "test", or "check" the MCP connection
- Troubleshooting "tool not found" or timeout issues

## Procedure

Run each step. Record PASS/FAIL/WARN. **Do not stop on failure** — finish
all steps and present the summary at the end.

### Step 1 — List workspaces (auth + connectivity)

Call `list_workspaces()`.
- PASS: Returns a list with at least one workspace
- FAIL: Connection error → check `RW_API_URL`; 401/403 → check `RUNWHEN_TOKEN`

Pick a workspace for subsequent steps (use user-provided name, or the first
returned).

### Steps 2-5 — Read operations (batch in parallel)

Call all four in parallel with `limit=1` where supported:
- `get_workspace_issues(workspace_name=WS, limit=1)`
- `get_workspace_slxs(workspace_name=WS)`
- `get_run_sessions(workspace_name=WS, limit=1)`
- `get_workspace_config_index(workspace_name=WS)`

For each: PASS if valid JSON returned (empty results are fine). FAIL on
403/404/error.

### Steps 6-7 — Runner infrastructure (batch in parallel)

- `get_workspace_locations(workspace_name=WS)` — PASS if at least one
  location returned; WARN if empty
- `get_workspace_secrets(workspace_name=WS)` — PASS if valid JSON; WARN
  if empty list

### Steps 8-9 — Registry & validation (batch in parallel)

- `search_registry(search="kubernetes", max_results=1)` — PASS if results
  returned
- `validate_script(script="def main():\n    return []\n", interpreter="python", task_type="task")`
  — PASS if `valid: true`

### Steps 10-11 — Chat config (batch in parallel)

- `get_workspace_chat_config(workspace_name=WS)` — PASS if valid JSON
  with `rules` and `commands` keys
- `list_knowledge_base_articles(workspace_name=WS, limit=1)` — PASS if
  valid JSON returned

### Step 12 — Workspace chat

Call `workspace_chat(message="Briefly list the top 3 open issues", workspace_name=WS)`.
- PASS: Returns a response with `chatUrl`
- FAIL: Chat backend unreachable

### Step 13 — Live execution (only if user requests "full check")

Skip by default. Only run if the user explicitly asks for a full check.

Call `run_script_and_wait` with:
```python
def main():
    return []
```
- PASS: `finalStatus` is `SUCCEEDED`
- FAIL: Timeout or error → runner may be offline

## Presenting results

Present a compact summary table:

```
| #     | Check              | Status | Notes           |
|-------|--------------------|--------|-----------------|
| 1     | Auth + workspaces  | PASS   | 5 workspaces    |
| 2-5   | Read operations    | PASS   | issues/slxs/... |
| 6-7   | Runner infra       | PASS   | 1 location      |
| 8-9   | Registry + validate| PASS   |                 |
| 10-11 | Chat config + KB   | PASS   |                 |
| 12    | Workspace chat     | PASS   | chatUrl ok      |
| 13    | Live execution     | SKIP   | (quick mode)    |
```

Then: all PASS → "MCP server is fully operational."
Any WARN → list with recommendation. Any FAIL → list with diagnostics.

## Diagnostic reference

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Step 1 connection error | Wrong `RW_API_URL` | Verify URL and network access |
| Step 1 returns 401/403 | Bad `RUNWHEN_TOKEN` | Regenerate the token |
| Steps 2-5 return 403 | No workspace access | Check membership in RunWhen UI |
| Step 6 empty locations | No runners registered | Install a runner |
| Step 10 chat config error | PAPI chat-config proxy issue | Check PAPI version supports `/api/v3/workspaces/{ws}/chat-config/` |
| Step 12 fails | Chat backend down | Check `RUNWHEN_APP_URL` setting |
| Step 13 timeout | Runner offline or overloaded | Check runner pod health |
