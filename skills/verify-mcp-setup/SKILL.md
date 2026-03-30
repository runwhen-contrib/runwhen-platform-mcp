---
name: verify-mcp-setup
description: "Validate a RunWhen MCP server installation is working end-to-end. Use when: (1) First connecting to a new MCP server, (2) After deployment, upgrade, or config change, (3) The user asks to verify, validate, check, or test the MCP setup, (4) Troubleshooting connectivity or permission issues, or (5) The user says 'checklist', 'smoke test', or 'health check' about the MCP server itself."
---

# Verify MCP Setup

Run a structured checklist to confirm the RunWhen MCP server is connected,
authenticated, and fully operational. Report results as a pass/fail checklist.

## When to use

- First time connecting to a RunWhen MCP server
- After deploying or upgrading the server
- After changing environment variables (`RW_API_URL`, `RUNWHEN_TOKEN`, etc.)
- User asks to "verify", "validate", "test", or "check" the MCP connection
- Troubleshooting "tool not found" or timeout issues

## Checklist procedure

Run each step below in order. Track results using a todo list. If a step
fails, record the error and continue — don't stop early. At the end,
present a summary table to the user.

### Phase 1: Core connectivity

**Step 1 — List workspaces**
Call `list_workspaces()`. This validates PAPI connectivity and token auth.
- PASS: Returns a JSON list with at least one workspace
- FAIL: Connection error → check `RW_API_URL`; 401/403 → check `RUNWHEN_TOKEN`

**Step 2 — Pick a workspace**
If the user provided a workspace name, use it. Otherwise pick the first
workspace from Step 1. All subsequent steps use this workspace.

### Phase 2: Read operations

**Step 3 — Workspace issues**
Call `get_workspace_issues(workspace_name=WS, limit=3)`.
- PASS: Returns valid JSON (even if empty list — that means no issues)
- FAIL: 403 → user lacks access to this workspace; 404 → workspace name wrong

**Step 4 — Workspace SLXs**
Call `get_workspace_slxs(workspace_name=WS)`.
- PASS: Returns valid JSON
- FAIL: Same diagnostics as Step 3

**Step 5 — Run sessions**
Call `get_run_sessions(workspace_name=WS, limit=3)`.
- PASS: Returns valid JSON

**Step 6 — Workspace config index**
Call `get_workspace_config_index(workspace_name=WS)`.
- PASS: Returns valid JSON

### Phase 3: Runner infrastructure

**Step 7 — Runner locations**
Call `get_workspace_locations(workspace_name=WS)`.
- PASS: Returns at least one location (workspace or public)
- WARN: Returns locations but none are `online`
- FAIL: Empty list → no runners configured

**Step 8 — Workspace secrets**
Call `get_workspace_secrets(workspace_name=WS)`.
- PASS: Returns valid JSON with at least one secret key
- WARN: Empty list — scripts may not have credentials available

### Phase 4: Registry & validation

**Step 9 — Registry search**
Call `search_registry(search="kubernetes", max_results=2)`.
- PASS: Returns results from the codebundle registry
- FAIL: Registry unreachable

**Step 10 — Script validation**
Call `validate_script(script="def main():\n    return []\n", interpreter="python", task_type="task")`.
- PASS: Returns `{"valid": true, ...}`
- FAIL: Validation engine broken

### Phase 5: Live execution (optional but recommended)

**Step 11 — Execute a no-op script**
Call `run_script_and_wait` with a trivial script that reports zero issues.
This proves the full pipeline: MCP → PAPI → runner → results.

For **bash**:
```bash
main() {
  echo '[]' >&3
}
```

For **python**:
```python
def main():
    return []
```

Use `workspace_name=WS` and let the location auto-resolve.
- PASS: Status is `SUCCEEDED` and issues list is empty
- FAIL: Timeout → runner may be offline; error → check runner logs

**Step 12 — Workspace chat**
Call `workspace_chat(message="What workspaces and SLXs are configured?", workspace_name=WS)`.
- PASS: Returns a response with `chatUrl`
- FAIL: Chat backend unreachable

### Phase 6: Chat configuration (quick check)

**Step 13 — Chat rules**
Call `list_chat_rules(scope_type="workspace", scope_id=WS)`.
- PASS: Returns valid JSON

**Step 14 — Chat commands**
Call `list_chat_commands(scope_type="workspace", scope_id=WS)`.
- PASS: Returns valid JSON

## Presenting results

After all steps, present a summary table:

```
| #  | Check                | Status | Notes                  |
|----|----------------------|--------|------------------------|
| 1  | List workspaces      | PASS   |                        |
| 2  | Workspace selected   | PASS   | t-oncall               |
| 3  | Issues               | PASS   |                        |
| ...| ...                  | ...    | ...                    |
| 14 | Chat commands        | PASS   |                        |
```

Then:
- If all PASS: "MCP server is fully operational."
- If any WARN: List warnings with recommendations.
- If any FAIL: List failures with specific diagnostics and next steps.

## Diagnostic reference

| Symptom | Likely cause | Next step |
|---------|-------------|-----------|
| Step 1 fails with connection error | Wrong `RW_API_URL` or server not reachable | Verify the URL and network access |
| Step 1 fails with 401/403 | Bad or expired `RUNWHEN_TOKEN` | Regenerate the token |
| Steps 3-6 return 403 | User lacks workspace access | Check workspace membership in RunWhen UI |
| Step 7 returns empty | No runners registered | Install a runner in the workspace |
| Step 11 times out | Runner is offline or overloaded | Check runner pod health; try `get_workspace_locations` for health status |
| Step 12 fails | Chat backend service issue | Check if `RUNWHEN_APP_URL` is set correctly (needed when `RW_API_URL` is internal) |

## Quick-run variant

For a fast connectivity check (skip live execution), run only Steps 1-10.
The user can request "quick check" or "skip execution" to use this variant.
