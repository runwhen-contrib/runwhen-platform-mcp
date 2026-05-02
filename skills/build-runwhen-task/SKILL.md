---
name: build-runwhen-task
description: "Build, test, and commit a RunWhen automation task (SLX). Use when: (1) Creating a new health check or monitoring task, (2) Building a troubleshooting runbook or diagnostic script, (3) Writing and testing a bash or python script for RunWhen runners, (4) Committing an SLX with commit_slx, (5) Running run_script_and_wait or run_script to test automation, or (6) The user asks to create, build, write, or test any RunWhen task or SLX."
---

# Build RunWhen Task

End-to-end workflow for creating a RunWhen SLX from scratch.

## Before you start — check the registry

**Always search the CodeBundle Registry first** using the
`find-and-deploy-codebundle` skill. There may already be a
production-ready codebundle for the task. Only proceed here
if the registry has no suitable match.

## When to use

- Creating a new health check or monitoring task **that doesn't exist in the registry**
- Building a troubleshooting runbook for infrastructure
- Automating incident response with scheduled checks
- Adding an SLI (health metric) to an existing task

## Workflow

1. **Load context** — `get_workspace_context` (ALWAYS first — loads RUNWHEN.md rules)
2. **Discover secrets** — `get_workspace_secrets(workspace_name="my-workspace")`
3. **Write script** — Use the reference templates below as starting points
4. **Validate** — `validate_script` checks contract compliance
5. **Test** — `run_script_and_wait(workspace_name="my-workspace", ...)` with env_vars, secret_vars
6. **Iterate** — Fix based on output, re-test until issues/severity/next-steps are correct
7. **Commit** — `commit_slx(workspace_name="my-workspace", ...)` with metadata (see reference examples)
8. **Wait** — Allow 1-3 minutes for reconciliation before querying the SLX

> **Location auto-resolves.** You do NOT need to call
> `get_workspace_locations` or pass a `location` parameter. The server
> automatically picks the best runner (workspace locations preferred over
> public). Only specify `location` explicitly when the workspace has
> multiple workspace-type runners and you need to target a specific one.
> See the `discover-locations` skill for details.

## Reference templates

Read these files for complete, contract-compliant script templates:

| Template | Path | Use for |
|----------|------|---------|
| Bash task | `references/bash-task-template.sh` | Bash health checks that report issues |
| Python task | `references/python-task-template.py` | Python health checks that report issues |
| Bash SLI | `references/bash-sli-template.sh` | Bash health metric (returns 0-1) |
| Python SLI | `references/python-sli-template.py` | Python health metric (returns 0-1) |
| Commit examples | `references/commit-examples.md` | `commit_slx` call patterns for common scenarios |

## Key rules

- Secrets are **file paths** on runners — use `read_secret()` pattern or `export KUBECONFIG=$kubeconfig`
- Always `--context=$CONTEXT` and `--request-timeout=30s` with kubectl
- Severity: 1=critical (down), 2=high (degraded), 3=medium (warning), 4=low (info)
- Set `access` tag: `read-only` for monitoring, `read-write` for remediation
- Set `data` tag: `logs-bulk` | `config` | `logs-stacktrace`
- **`resource_path` MUST start with `custom/`** — the server enforces this automatically. Never place custom tasks under an existing platform resource path (see configure-resource-path skill)
- **`hierarchy` MUST start with `platform=custom`** — always include `{"name": "platform", "value": "custom"}` as the first tag and `"platform"` as the first hierarchy entry (see configure-hierarchy skill)

## Run-Time Variables (Tasks only — never SLIs)

Script variables are runtime-overridable parameters that users change per individual run:
query strings, log filters, time windows, transient target names. They are distinct from
`env_vars` (infra targets like namespace/cluster) and `secret_vars` (credentials).

### Classification rules — in order

1. IF the variable identifies WHICH cluster, namespace, or named resource to connect to
   (KUBECTL_CONTEXT, NAMESPACE, *_NAME, *_CLUSTER) → use `env_vars`
2. IF the variable is a search query, filter, pattern, time window, or per-run target →
   use `run_time_vars`
3. IF the variable name ends in *_QUERY, *_PATTERN, *_FILTER, *_WINDOW, *_TARGET →
   use `run_time_vars`
4. IF unsure → use `env_vars` (safer default; run-time vars are opt-in)

### Using run-time vars in `run_script_and_wait`

Pass override values via `run_time_var_overrides` (merged into `envVars` at test time):

```python
run_script_and_wait(
    workspace_name="my-workspace",
    script=my_script,
    env_vars={"NAMESPACE": "backend", "KUBECTL_CONTEXT": "gke-prod"},
    secret_vars={"kubeconfig": "kubeconfig"},
    run_time_var_overrides={"LOG_QUERY": "critical", "TIME_WINDOW": "30m"},
)
```

### Using run-time vars in `commit_slx`

Pass the full schema via `run_time_vars`. All four fields are **required**:

```python
commit_slx(
    workspace_name="my-workspace",
    slx_name="k8s-log-grep",
    alias="Kubernetes Log Grep",
    statement="Grep pod logs for a search term",
    script=my_script,
    interpreter="python",
    task_type="task",
    env_vars={"NAMESPACE": "backend", "KUBECTL_CONTEXT": "gke-prod"},
    secret_vars={"kubeconfig": "kubeconfig"},
    run_time_vars=[
        {
            "name": "LOG_QUERY",
            "description": "Log search string to filter entries",
            "default": "error",
            "validation": {"type": "regex", "pattern": "^.+$"},
        },
        {
            "name": "SEVERITY",
            "description": "Minimum severity level to report",
            "default": "warning",
            "validation": {"type": "enum", "values": ["debug", "warning", "error", "critical"]},
        },
    ],
)
```

**NEVER** pass `run_time_vars` when `task_type="sli"` — SLIs are automated health probes
with fixed thresholds; there is no per-run override concept for SLIs.

## Running tasks after committing

After committing an SLX, use `run_slx` to trigger execution — **not** `workspace_chat`.

`workspace_chat` can search for and describe tasks but **cannot execute them**.
See the `run-existing-slx` skill for the full execution workflow.
