---
name: run-existing-slx
description: Run an existing SLX (health check, task, or automation) that is already committed to a workspace. Use when the user asks to execute, trigger, or run a task that already exists.
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

## Examples

### Run all tasks in an SLX

```
run_slx(slx_name="k8s-pod-health", workspace_name="my-workspace")
```

### Run specific tasks within a runbook

```
run_slx(slx_name="k8s-namespace-check", workspace_name="my-workspace", task_titles="Check Pod Status||Check Pod Restarts")
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
| `task_titles` | No | `"*"` (all) | Which tasks to run. `"*"` for all, or `"||"`-separated titles |

## How it works

`run_slx` calls the RunRequest API:
1. Creates a staged RunRequest (`POST .../runbook/runs`)
2. Starts it (`POST .../runs/{id}/start`) — submits to the runner
3. Polls until completion (up to 5 minutes)
4. Returns output with pass/fail status and any issues found

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "SLX not found" / 404 | Verify the SLX name with `get_workspace_slxs` |
| Timeout after 300s | SLX may still be running. Check `get_run_sessions` later |
| No output returned | The runner may still be processing. Wait and check `get_run_sessions` |
| Need to run something not yet committed | Use `run_script_and_wait` for ad-hoc scripts instead |
