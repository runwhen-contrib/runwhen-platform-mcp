---
name: run-existing-slx
description: "Run an existing SLX (health check, task, or automation) that is already committed to a workspace. Use when: (1) The user asks to execute, trigger, or run an existing task, (2) Running a health check or diagnostic on demand, (3) Calling run_slx to execute a previously committed SLX, or (4) The user references a specific SLX name to run."
---

# Run an Existing SLX

Execute a committed SLX runbook on the workspace runner.

## When to use

- User asks to "run", "execute", or "trigger" a health check or task
- User references a specific SLX by name (e.g. "run k8s-pod-health")
- User wants to re-run a check to verify a fix
- User asks workspace_chat to run something (workspace_chat CANNOT execute tasks)

## Critical: workspace_chat limitations

**`workspace_chat` can search, analyze, and describe tasks — but it CANNOT execute them.**

When a user says "run task X" through workspace_chat, it will describe the task but
not actually trigger it. You MUST use `run_slx` for execution.

| Action | Correct tool |
|--------|-------------|
| Find available tasks | `search_workspace` or `workspace_chat` |
| See what a task does | `get_slx_runbook` or `workspace_chat` |
| View recent run results | `get_run_sessions` or `workspace_chat` |
| **Actually execute a task** | **`run_slx`** |
| Run an ad-hoc script (not committed) | `run_script_and_wait` |

## Workflow

1. **Find the SLX** — Use `search_workspace` or `get_workspace_slxs` to find the SLX name
2. **Verify it** (optional) — Use `get_slx_runbook` to see what tasks it will run
3. **Execute** — Call `run_slx` with the SLX short name
4. **Review results** — The tool returns pass/fail status and output

## Critical: always use `task_titles="*"`

SLX runbooks register task names as the literal Robot variable `${TASK_TITLE}`
(resolved at runtime), **not** the human-readable title shown in
`get_slx_runbook`. Passing a display title (e.g. `"GitHub Project Board Daily
Update"`) produces empty `passed_titles` — the run looks empty even though the
SLX exists. The MCP rejects such literals with `Unsupported task_titles value`.

- **Default:** omit `task_titles` or pass `task_titles="*"` (runs all tasks)
- **Advanced:** pass `task_titles="${TASK_TITLE}"` only when you know the exact
  Robot variable name stored in the runbook
- **Never:** copy the resolved title from `get_slx_runbook` into `task_titles`

## Examples

### Run all tasks in an SLX (usual case)

```
run_slx(slx_name="k8s-pod-health", workspace_name="my-workspace")
```

### Run with per-run runtime variable overrides

Use `runtime_var_overrides` for user inputs declared in the runbook's
`runtime_vars`. Still use `task_titles="*"` (or omit it).

```
run_slx(
  slx_name="github-project-board-update",
  workspace_name="my-workspace",
  task_titles="*",
  runtime_var_overrides={
    "GITHUB_OWNER": "runwhen",
    "GITHUB_REPO": "customer-project-g-research",
    "LOOKBACK_DAYS": "1",
  },
)
```

### Full discovery-to-execution flow

```
# 1. Find the SLX
search_workspace(query="pod health", workspace_name="my-workspace")

# 2. See what it does
get_slx_runbook(slx_name="k8s-pod-health", workspace_name="my-workspace")

# 3. Run it
run_slx(slx_name="k8s-pod-health", workspace_name="my-workspace")
```

## Parameters

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `slx_name` | Yes | — | SLX short name (e.g. "k8s-pod-health") |
| `workspace_name` | Yes | — | Target workspace (e.g. "t-oncall") |
| `task_titles` | No | `"*"` (all) | Almost always `"*"`. Do not pass human-readable titles from `get_slx_runbook` |
| `runtime_var_overrides` | No | — | Per-run values for runbook `runtime_vars` (name → value dict) |

## How it works

`run_slx` calls the RunRequest API:
1. Creates a staged RunRequest (`POST .../runbook/runs`)
2. Starts it (`POST .../runs/{id}/start`) — submits to the runner
3. Polls until completion (up to 5 minutes)
4. Returns output with pass/fail status and any issues found

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `Unsupported task_titles value` | Use `task_titles="*"` — never pass the display title from `get_slx_runbook` |
| `unexpected_keyword_argument` on `resource_path` | `resource_path` is only for `commit_slx` / `deploy_registry_codebundle`, not query tools like `get_workspace_config_index` |
| "SLX not found" / 404 | Verify the SLX name with `get_workspace_slxs` |
| Timeout after 300s | SLX may still be running. Check `get_run_sessions` later |
| No output returned | The runner may still be processing. Wait and check `get_run_sessions` |
| Need to run something not yet committed | Use `run_script_and_wait` for ad-hoc scripts instead |
