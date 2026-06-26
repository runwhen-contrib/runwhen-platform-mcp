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
- **`task_title` MUST be a static literal string** — never use `${VAR}` placeholders.
  Robot Framework resolves `${...}` at suite-parse time, before env vars are injected,
  which crashes suite setup. The MCP rejects placeholder titles at commit time.
- **Bash: do not append `main "$@"`** and **Python: do not include `if __name__ == "__main__":`** —
  the runner sources the file and calls `main()` directly. Both constructs are now
  auto-stripped before submission, but include neither in source for clarity.

## Always emit a summary issue (severity 4)

Investigation tasks that only raise issues on threshold failures are invisible
in `workspace_chat` and run-session search when everything looks healthy — stdout
is not indexed. **Always emit one final severity-4 "Summary" issue unconditionally**
at the end of the task with the actual numbers (counts, breakdowns, p99s, etc.).

```python
def main():
    issues = []
    # ... investigation logic that may append severity 1-3 issues ...
    issues.append({
        "issue title": f"{TASK_TITLE} — Summary",
        "issue description": (
            f"Examined {total} resources; {failed} failed; "
            f"top callers: {top_callers}; lookback={LOOKBACK_DAYS}d."
        ),
        "issue severity": 4,
        "issue next steps": "Informational. Review numbers in description.",
    })
    return issues
```

## Issue payload quality bar — strictly enforced

`workspace_chat` and search surface **only issues**, not stdout/stderr. An
issue with an empty or stub description is invisible to operators. The MCP
now both **statically** scans your script (in `validate_script` and
`commit_slx`) and **dynamically** inspects emitted issues (in
`run_script_and_wait`) for these violations:

| Field | Minimum | Must include |
|---|---|---|
| `issue title` | ≥ 8 chars, non-empty | What was checked + what state was observed |
| `issue description` | ≥ 40 chars, non-empty | Observed numbers, names, timestamps, thresholds — interpolated from runtime values |
| `issue next steps` | ≥ 20 chars, non-empty | Concrete remediation: a kubectl/CLI command, runbook URL, owner, or escalation path |
| `issue severity` | 1, 2, 3, or 4 | Use the scale: 1=critical, 2=high, 3=medium, 4=informational |

Common anti-patterns the MCP will warn on:

- ❌ `"issue description": ""` — empty literal
- ❌ `"issue title": "Issue found"` — no signal
- ❌ `"issue next steps": "Investigate."` — no actionable hint
- ❌ Issue dict with no f-string or string interpolation anywhere — that means
  no runtime data is being captured. workspace_chat sees only the static literal.
- ❌ Placeholder tokens: `TODO`, `FIXME`, `XXX`, `lorem ipsum`, `placeholder`, `TBD`

### Good vs bad — Python task

```python
# BAD — runner shows "no signal" forever
issues.append({
    "issue title": "Pod issue",
    "issue description": "",
    "issue severity": 2,
    "issue next steps": "Check pods.",
})

# GOOD — operator can act without re-running the task
issues.append({
    "issue title": f"Pod {pod_name} restarted {restart_count} times in {NAMESPACE}",
    "issue description": (
        f"Pod {pod_name} in namespace {NAMESPACE} (cluster {CONTEXT}) has "
        f"restarted {restart_count} times in the last {LOOKBACK_MIN} minutes. "
        f"Last termination reason: {last_reason}. Container image: {image}. "
        f"Owner: {owner_label or 'unknown'}."
    ),
    "issue severity": 2,
    "issue next steps": (
        f"kubectl --context={CONTEXT} -n {NAMESPACE} describe pod {pod_name} | "
        f"head -50; then check container logs: kubectl --context={CONTEXT} -n "
        f"{NAMESPACE} logs {pod_name} -p --tail=200"
    ),
})
```

## Script size and transport limits

MCP HTTP intermediaries impose payload limits (~13KB base64 observed in the
wild). The MCP now applies size guards:

| Threshold | Behavior |
|---|---|
| ≤ `RUNWHEN_SCRIPT_SOFT_MAX_BYTES` (10KB default) | Silent — ship it |
| Soft threshold to hard cap | Advisory warning surfaced in `validate_script` and `run_script_and_wait` |
| > `RUNWHEN_SCRIPT_HARD_MAX_BYTES` (64KB default) | Hard reject — `commit_slx` / `run_script*` return an error |

If you hit the soft warning or hard cap, prefer:

1. **Use a registry codebundle** — search with `search_registry` first.
2. **Split into a custom codebundle** in a git repo and deploy with `deploy_registry_codebundle` (no inline script needed).
3. **In stdio mode**, pass `script_path=/local/path/to/script.py` instead of inline `script` / `script_base64`.
4. **Raise the cap** with `RUNWHEN_SCRIPT_HARD_MAX_BYTES` if your transport is known-good.

## Cloud-specific secret requirements

Some cloud platforms require a canonical secret name in `secret_vars` for the
runner's suite-setup step to succeed. Missing it causes the task to pass with
0 issues in ~5 seconds (because suite setup short-circuits).

| Platform | Required `secret_vars` entry |
|----------|-------------------------------|
| Azure    | `{"azure_credentials": "azure:sp@cli"}` (or your workspace's Azure SP secret key) |
| GCP      | `{"gcp_credentials_json": "<workspace-gcp-key>"}` for service-account auth |
| AWS      | `{"aws_credentials": "<workspace-aws-creds-key>"}` for CLI auth |
| Kubernetes | `{"kubeconfig": "kubeconfig"}` |

The MCP server now refuses to commit an Azure-flavored SLX without
`azure_credentials` in `secret_vars` (with an explanatory error). See
`discover-secrets` skill to find the right key names for your workspace.

## Runtime Variables (Tasks only — never SLIs)

Script variables are runtime-overridable parameters that users change per individual run:
query strings, log filters, time windows, transient target names. They are distinct from
`env_vars` (infra targets like namespace/cluster) and `secret_vars` (credentials).

### Classification rules — in order

1. IF the variable identifies WHICH cluster, namespace, or named resource to connect to
   (KUBECTL_CONTEXT, NAMESPACE, *_NAME, *_CLUSTER) → use `env_vars`
2. IF the variable is a search query, filter, pattern, time window, or per-run target →
   use `runtime_vars`
3. IF the variable name ends in *_QUERY, *_PATTERN, *_FILTER, *_WINDOW, *_TARGET →
   use `runtime_vars`
4. IF unsure → use `env_vars` (safer default; runtime vars are opt-in)

### Using runtime vars in `run_script_and_wait`

Pass override values via `runtime_var_overrides` (merged into `envVars` at test time):

```python
run_script_and_wait(
    workspace_name="my-workspace",
    script=my_script,
    env_vars={"NAMESPACE": "backend", "KUBECTL_CONTEXT": "gke-prod"},
    secret_vars={"kubeconfig": "kubeconfig"},
    runtime_var_overrides={"LOG_QUERY": "critical", "TIME_WINDOW": "30m"},
)
```

### Using runtime vars in `commit_slx`

Pass the full schema via `runtime_vars`. All four fields are **required**:

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
    runtime_vars=[
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

**NEVER** pass `runtime_vars` when `task_type="sli"` — SLIs are automated health probes
with fixed thresholds; there is no per-run override concept for SLIs.

## Running tasks after committing

After committing an SLX, use `run_slx` to trigger execution — **not** `workspace_chat`.

`workspace_chat` can search for and describe tasks but **cannot execute them**.
See the `run-existing-slx` skill for the full execution workflow.
