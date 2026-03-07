# RunWhen Tool Builder / Create Task Flow

This document describes the end-to-end flow for creating custom automation tasks
in the RunWhen platform, and how that flow is replicated for MCP-based agents.

## Overview

The Tool Builder lets users write custom Bash or Python scripts, test them against
live infrastructure via RunWhen runners, and commit them as SLXs (Service Level
eXperiences) to a workspace. Each committed script becomes a repeatable, scheduled,
or on-demand automation task.

There are two task types:

| Type | Purpose | Output |
|------|---------|--------|
| **Task** (runbook) | Troubleshooting, remediation, data collection | List of issues (`issue title`, `issue description`, `issue severity`, `issue next steps`) |
| **SLI** (indicator) | Health metric | Single float in `[0, 1]` (0 = unhealthy, 1 = healthy) |

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Author (Cursor agent, UI, or CLI)                         │
│                                                             │
│  1. Write script (bash/python)                              │
│  2. Set variables, secrets, runner location                 │
│  3. Run test  ──────────────────────┐                       │
│  4. Review output                   │                       │
│  5. Commit to SLX  ──────────┐     │                       │
└──────────────────────────────┼─────┼────────────────────────┘
                               │     │
                    ┌──────────▼─────▼──────────┐
                    │  PAPI (backend-services)   │
                    │                            │
                    │  POST /author/run           │  ← execute script
                    │  GET  /author/run/:id/status│  ← poll status
                    │  GET  /author/run/:id/output│  ← get artifacts
                    │  POST /branches/:b/slxs/:n │  ← commit SLX
                    │  GET  /secrets-keys         │  ← list secrets
                    └────────────┬───────────────┘
                                 │
                    ┌────────────▼───────────────┐
                    │  Runner (location)          │
                    │  Executes script in         │
                    │  sandboxed environment      │
                    └────────────────────────────┘
```

## Script Contract (RW Contract)

Scripts must follow a specific contract to integrate with the RunWhen runner.

### Python Task

```python
def main():
    """
    Execute your code and return a list of issues.

    Each issue MUST be a dict with the following keys:
      - "issue title"
      - "issue description"
      - "issue severity"
      - "issue next steps"
      - "issue observed at" (optional)
    """
    import os

    namespace = os.environ.get("NAMESPACE", "default")

    issues = []
    # ... your logic ...
    issues.append({
        "issue title": "Pod CrashLooping",
        "issue description": f"Pod xyz in {namespace} has restarted 15 times",
        "issue severity": 2,
        "issue next steps": "Check pod logs and events",
    })
    return issues
```

Rules:
- Must define a top-level `main()` function
- `main()` must return `List[Dict]` with issue fields (see Issue Fields below)
- Do NOT call `main()` directly (the runner calls it)
- Do NOT use `if __name__ == "__main__"`
- Use `os.environ` / `os.getenv` for configuration variables
- Secret vars are injected as env vars pointing to **file paths** (e.g. `os.environ["kubeconfig"]` is a path to the kubeconfig file, set `KUBECONFIG` to that path)

### Python SLI

```python
import os

def main():
    """Must return a float between 0 and 1."""
    # ... your health check logic ...
    return 1.0  # healthy
```

Rules: same as Task, but `main()` returns a `float` in `[0, 1]`.

### Bash Task

```bash
main() {
    # Define the issues to be created below, or keep it as [] if no issue should be created
    issues='[]'

    # Example issue:
    # issues="$(
    #     jq -n \
    #     --arg title "Descriptive issue title" \
    #     --arg desc "Issue description" \
    #     --argjson severity 2 \
    #     --arg nextsteps "Concrete remediation guidance" \
    #     --arg observedAt "$(date -u)" \
    #     '[{
    #         "issue title": $title,
    #         "issue description": $desc,
    #         "issue severity": $severity,
    #         "issue next steps": $nextsteps,
    #         "issue observed at": $observedAt
    #     }]'
    # )"

    jq -n --argjson issues "$issues" '$issues' >&3
}
```

Rules:
- Must define a `main()` function
- Write issue JSON array to FD 3 (`>&3` or `> /dev/fd/3`)
- Use `jq` for reliable JSON construction
- Do NOT call `main` directly

### Bash SLI

```bash
#!/bin/bash

main() {
    # Write metric value (0-1) to file descriptor 3
    echo "1.0" >&3
}
```

Rules: same as Task, but write a single float to FD 3.

### Issue Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `issue title` | string | Yes | Short issue title |
| `issue description` | string | Yes | Detailed description |
| `issue severity` | int (1-4) | Yes | 1=critical, 2=high, 3=medium, 4=low |
| `issue next steps` | string | Yes | Recommended remediation steps |
| `issue observed at` | string (ISO 8601) | No | Timestamp of observation |

### Environment Variables

Scripts receive configuration through environment variables:
- **Variables** (`env_vars`): Non-sensitive config (namespace, context, thresholds)
- **Secrets** (`secret_vars`): Sensitive config mapped to workspace secret keys (kubeconfig, API tokens)

## API Endpoints

### 1. Execute Script

```
POST /api/v3/workspaces/{workspace}/author/run
```

**Request:**
```json
{
  "command": "def main():\n    return [...]",
  "location": "northamerica-northeast2-01",
  "run_type": "task",
  "interpreter": "python",
  "envVars": {
    "NAMESPACE": "backend-services",
    "CONTEXT": "platform-cluster-01"
  },
  "secretVars": {
    "kubeconfig": "kubeconfig"
  }
}
```

> **Important:** The `envVars` and `secretVars` keys must be camelCase in the POST body.
> The CamelCase parser converts them to `env_vars`/`secret_vars` for the serializer,
> and the CamelCase renderer converts them back to `envVars`/`secretVars` when the
> Explorer (runner) fetches the Run payload. Using snake_case will cause env vars to
> silently not be injected into the script's OS environment.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `command` | string | Yes | - | Raw script content (backend base64-encodes it) |
| `location` | string | Yes | - | Runner location identifier |
| `run_type` | string | No | `"task"` | `"task"` or `"sli"` |
| `interpreter` | string | No | `"bash"` | `"bash"` or `"python"` |
| `envVars` | object | No | `{}` | Environment variables for the script (camelCase key!) |
| `secretVars` | object | No | `{}` | Secret key mappings (env var name → workspace secret key, camelCase key!) |

**Response (202 Accepted):**
```json
{
  "runId": "uuid-string",
  "status": "RUNNING"
}
```

### 2. Poll Run Status

```
GET /api/v3/workspaces/{workspace}/author/run/{runId}/status
```

**Response:**
```json
{
  "runId": "uuid-string",
  "status": "RUNNING | SUCCEEDED | FAILED"
}
```

Poll interval: 5 seconds. Max duration: 5 minutes.

### 3. Get Run Output

```
GET /api/v3/workspaces/{workspace}/author/run/{runId}/output
```

**Response:**
```json
{
  "runId": "uuid-string",
  "status": "SUCCEEDED",
  "artifacts": [
    {
      "type": "log",
      "filename": "report.jsonl",
      "contentType": "text/plain",
      "expiresAt": "2026-03-04T00:00:00Z",
      "signedUrl": "https://storage.googleapis.com/..."
    },
    {
      "type": "issues",
      "filename": "issues.jsonl",
      "contentType": "text/plain",
      "expiresAt": "2026-03-04T00:00:00Z",
      "signedUrl": "https://storage.googleapis.com/..."
    },
    {
      "type": "debug",
      "filename": "log.html",
      "contentType": "text/html",
      "expiresAt": "2026-03-04T00:00:00Z",
      "signedUrl": "https://storage.googleapis.com/..."
    }
  ]
}
```

Artifact types:
- `log` — Execution log (report.jsonl) — JSONL where each line has an `obj` field; `"Command stdout: ..."` and `"Command stderr: ..."` prefixes identify script output
- `issues` — Discovered issues (issues.jsonl) — JSONL where each line is an issue object with `title`, `severity`, `details`, `nextSteps`, etc.
- `debug` — Debug log (log.html) — Robot Framework HTML debug log

#### MCP Server Parsed Output

The MCP server's `run_script_and_wait` and `get_run_output` tools automatically
fetch and parse the raw JSONL artifacts into a clean, human-readable structure:

```json
{
  "runId": "uuid-string",
  "finalStatus": "SUCCEEDED",
  "elapsedSeconds": 12,
  "issues": [
    {
      "title": "Pod nginx-abc not healthy",
      "severity": 2,
      "details": "Phase: CrashLoopBackOff, Ready: False",
      "nextSteps": "kubectl describe pod nginx-abc -n default",
      "taskName": "dev-test",
      "observedAt": "2026-03-06T15:22:08.789161+00:00"
    }
  ],
  "stdout": "any stdout from the script",
  "stderr": "any stderr from the script",
  "report": "additional report entries from Robot Framework (Add To Report / Add Pre To Report)"
}
```

The `stdout` and `stderr` fields contain the script's standard output/error, extracted
from `"Command stdout: ..."` / `"Command stderr: ..."` entries in the report JSONL.
The `report` field contains any other non-issue log entries (e.g. from Robot Framework's
`Add To Report` keyword).

A 2-second settle delay is applied after the run status changes before fetching artifacts,
with an automatic retry if the artifacts are empty (handles upload lag).

This allows agents to directly read issue titles, severities, details, and next steps
to iterate on scripts without having to parse raw JSONL or download signed GCS URLs.

### 4. List Workspace Secrets

```
GET /api/v3/workspaces/{workspace}/secrets-keys
```

Returns available secret key names that can be referenced in `secret_vars`.

### 5. Commit SLX

```
POST /api/v3/workspaces/{workspace}/branches/{branch}/slxs/{slxName}
```

**Request:**
```json
{
  "commit_msg": "Add k8s-pod-health-check task",
  "files": {
    "slx.yaml": "apiVersion: runwhen.com/v1\nkind: ServiceLevelX\n...",
    "runbook.yaml": "apiVersion: runwhen.com/v1\nkind: Runbook\n...",
    "sli.yaml": "apiVersion: runwhen.com/v1\nkind: ServiceLevelIndicator\n..."
  }
}
```

**Response (201 Created):**
```json
{
  "hexsha": "abc123...",
  "files": {},
  "path": "slxs/my-slx-name",
  "branch": "main"
}
```

## YAML Schemas

### slx.yaml (ServiceLevelX)

```yaml
apiVersion: runwhen.com/v1
kind: ServiceLevelX
metadata:
  name: "workspace--slx-short-name"
  labels:
    workspace: "workspace"
    slx: "workspace--slx-short-name"
  annotations:
    internal.runwhen.com/manually-created: "true"
spec:
  alias: "My Custom Health Check"
  statement: "Pods should be healthy and running"
  owners:
    - user@example.com
  tags:
    - name: resource_name
      value: my-service
    - name: resource_type
      value: deployment
```

### runbook.yaml (Runbook / Task)

```yaml
apiVersion: runwhen.com/v1
kind: Runbook
metadata:
  name: "workspace--slx-short-name"
  labels:
    workspace: "workspace"
    slx: "workspace--slx-short-name"
spec:
  location: "location-01-us-west1"
  codeBundle:
    repoUrl: "https://github.com/runwhen-contrib/rw-generic-codecollection.git"
    ref: "main"
    pathToRobot: "codebundles/tool-builder/runbook.robot"
  configProvided:
    - name: TASK_TITLE
      value: "Check Pod Health"
    - name: GEN_CMD
      value: "base64-encoded-script-content"
    - name: INTERPRETER
      value: "bash"
    - name: CONFIG_ENV_MAP
      value: '{"NAMESPACE":"online-boutique-dev","CONTEXT":"sandbox-cluster-1"}'
    - name: SECRET_ENV_MAP
      value: '["kubeconfig"]'
    - name: NAMESPACE
      value: "online-boutique-dev"
    - name: CONTEXT
      value: "sandbox-cluster-1"
  secretsProvided:
    - name: kubeconfig
      workspaceKey: kubeconfig
```

### sli.yaml (ServiceLevelIndicator)

```yaml
apiVersion: runwhen.com/v1
kind: ServiceLevelIndicator
metadata:
  name: "workspace--slx-short-name"
  labels:
    workspace: "workspace"
    slx: "workspace--slx-short-name"
spec:
  location: "location-01-us-west1"
  locations:
    - "location-01-us-west1"
  displayUnitsLong: "OK"
  displayUnitsShort: "ok"
  intervalSeconds: 300
  intervalStrategy: "intermezzo"
  alertConfig:
    tasks:
      persona: "eager-edgar"
      sessionTTL: "10m"
  codeBundle:
    repoUrl: "https://github.com/runwhen-contrib/rw-generic-codecollection.git"
    ref: "main"
    pathToRobot: "codebundles/tool-builder/sli.robot"
  configProvided:
    - name: GEN_CMD
      value: "base64-encoded-script-content"
    - name: INTERPRETER
      value: "python"
    - name: CONFIG_ENV_MAP
      value: '{"NAMESPACE":"online-boutique-dev"}'
    - name: SECRET_ENV_MAP
      value: '["kubeconfig"]'
  secretsProvided:
    - name: kubeconfig
      workspaceKey: kubeconfig
```

## Code Bundle Constants

Tool Builder scripts use the generic code collection:

| Constant | Value |
|----------|-------|
| Repo URL | `https://github.com/runwhen-contrib/rw-generic-codecollection.git` |
| Ref | `main` |
| Runbook path | `codebundles/tool-builder/runbook.robot` |
| SLI path | `codebundles/tool-builder/sli.robot` |

Cron-scheduler SLI uses the workspace utilities collection:

| Constant | Value |
|----------|-------|
| Repo URL | `https://github.com/runwhen-contrib/rw-workspace-utils.git` |
| Ref | `main` |
| SLI path | `codebundles/cron-scheduler-sli/sli.robot` |

## End-to-End Flow (UI)

1. **Open Tool Builder** — User clicks "Create Task" in Studio → opens `/workspace/{ws}/tool-builder`
2. **Write script** — Bash or Python in the code editor
3. **Configure** — Set variables, secrets, runner location in side panels
4. **Test** — Click "Run" → `POST /author/run` → poll status → fetch output artifacts
5. **Review** — Optional AI code review; inspect issues/metrics output
6. **Commit** — Click "Commit to SLX" → fill in alias, statement, owners, tags → `POST /branches/main/slxs/{name}`
7. **Live** — Backend writes YAML to workspace Git repo; ModelSync propagates to DB; SLX appears in Studio

## End-to-End Flow (MCP Agent)

The MCP server replicates this flow for AI coding assistants:

0. **Load context** — Agent calls `get_workspace_context` to read the project's `RUNWHEN.md` file.
   This provides infrastructure conventions, database access rules, naming patterns, and
   other constraints the script must follow. **Always do this before writing a script.**
1. **Write script** — Agent writes a bash/python script locally (in Cursor, etc.),
   following the rules and patterns from `RUNWHEN.md`
2. **Validate** — Agent validates script follows RW contract (main function, correct return type)
3. **List secrets** — Agent queries available workspace secrets via `get_workspace_secrets`
4. **Test** — Agent calls `run_script` → polls with `get_run_status` → fetches results with `get_run_output`
5. **Iterate** — Agent reviews output, fixes script, re-tests
6. **Commit** — Agent calls `commit_slx` with script, metadata, and configuration
7. **Verify** — Agent confirms SLX was created via existing `get_workspace_slxs` or `search_workspace`

**Workspace chat and task execution:** The `workspace_chat` tool talks to the RunWhen AI assistant, which can discover tasks, suggest which to run, and analyze run session output. It **cannot** execute tasks (e.g. "run T-0"); task execution is gated through the RunWhen UI for security. Run sessions can be triggered from the UI or other platform mechanisms—the MCP server does not trigger them.

### Key Differences from UI Flow

| Aspect | UI | MCP Agent |
|--------|-----|-----------|
| Script editing | Monaco editor | Agent writes locally |
| Variable input | Side panel forms | Tool parameters |
| Test execution | Click "Run" button | `run_script` tool call |
| Output review | Rendered in UI | Agent reads artifacts |
| Commit | Dialog form | `commit_slx` tool call |
| Code review | AI review button | Agent self-reviews |

## Workspace Context (RUNWHEN.md)

The `RUNWHEN.md` file is a project-level document that provides domain-specific
knowledge for agents building RunWhen tasks. It captures the kind of tribal knowledge
that a human engineer would share when onboarding someone to monitor a system:

- Which database replicas to query (and how to connect)
- Naming patterns for pods, services, and labels
- Environment variables scripts need
- Severity guidelines for issues
- Known gotchas and edge cases

### How It Works

1. The user places a `RUNWHEN.md` file in their project root
2. The MCP server auto-discovers it by walking up from the current working directory
   (same convention as `.editorconfig` or `CLAUDE.md`; override with `RUNWHEN_CONTEXT_FILE` if needed)
3. Agents call `get_workspace_context` before writing scripts
4. The file content is returned as structured JSON the agent can reference

### Recommended Sections

| Section | Purpose |
|---------|---------|
| **Infrastructure Overview** | Clusters, namespaces, cloud accounts |
| **Architecture & Components** | Deployments, StatefulSets, their relationships |
| **Database Access Rules** | Replica targeting, connection methods, read-only constraints |
| **Naming Conventions** | Labels, service names, resource patterns |
| **Common Environment Variables** | Variables scripts typically need |
| **Task Authoring Rules** | kubectl flags, error handling, security constraints |
| **SLX Conventions** | Naming, tagging, scheduling patterns |
| **Known Gotchas** | Pitfalls, edge cases, non-obvious behaviors |

See [RUNWHEN.md.template](RUNWHEN.md.template) for a blank template and
[RUNWHEN.md.example](RUNWHEN.md.example) for a real-world example.

### Why This Matters

Without a `RUNWHEN.md`, agents will make reasonable-but-wrong assumptions:
- Querying the primary database instead of a replica
- Using application database users that lack socket auth
- Looking for Kubernetes Jobs when the system uses Celery
- Missing required kubectl flags like `--context` or `--request-timeout`
- Choosing incorrect severity levels or data tags

The file acts as a guardrail, ensuring scripts follow the same conventions that
existing (manually authored) tasks follow.

## SLI Patterns

When committing an SLX, you can include an SLI alongside the task runbook. There are
two patterns:

### Custom SLI Script

Write a script that returns a health metric (0-1). Useful for quantitative health
checks that run on an interval.

```
commit_slx(
    slx_name="my-health-check",
    task_type="task",
    script="...",                    # task (runbook) script
    sli_script="def main(): ...",    # custom SLI script (returns 0-1)
    sli_interval_seconds=300,        # run every 5 minutes
)
```

This generates `slx.yaml`, `runbook.yaml`, AND `sli.yaml`. The SLI uses the
tool-builder codebundle with the custom SLI script.

### Cron-Scheduler SLI

Trigger the runbook on a cron schedule. Useful for running tasks at specific times
rather than on a fixed interval.

```
commit_slx(
    slx_name="my-health-check",
    task_type="task",
    script="...",                    # task (runbook) script
    cron_schedule="0 */2 * * *",     # run every 2 hours
    sli_interval_seconds=60,         # SLI checks every 60s if it's time
)
```

This generates `slx.yaml`, `runbook.yaml`, AND `sli.yaml`. The SLI uses the
[cron-scheduler-sli](https://github.com/runwhen-contrib/rw-workspace-utils/tree/main/codebundles/cron-scheduler-sli)
codebundle, which checks the cron schedule on each interval and triggers the runbook
when it's time. The SLI self-schedules (triggers the runbook of its own SLX).

Cron expression format: `minute hour day month weekday`

Common examples:
- `0 * * * *` — every hour
- `*/15 * * * *` — every 15 minutes
- `0 */2 * * *` — every 2 hours
- `0 9 * * 1-5` — 9 AM weekdays
- `0 0 * * 0` — Sunday midnight

### Cron-Scheduler SLI YAML

When `cron_schedule` is provided, the generated `sli.yaml` uses:

```yaml
apiVersion: runwhen.com/v1
kind: ServiceLevelIndicator
metadata:
  name: "workspace--slx-short-name"
  labels:
    workspace: "workspace"
    slx: "workspace--slx-short-name"
spec:
  location: "location-01-us-west1"
  locations:
    - "location-01-us-west1"
  displayUnitsLong: "OK"
  displayUnitsShort: "ok"
  intervalSeconds: 60
  intervalStrategy: "intermezzo"
  alertConfig:
    tasks:
      persona: "eager-edgar"
      sessionTTL: "10m"
  codeBundle:
    repoUrl: "https://github.com/runwhen-contrib/rw-workspace-utils.git"
    ref: "main"
    pathToRobot: "codebundles/cron-scheduler-sli/sli.robot"
  configProvided:
    - name: CRON_SCHEDULE
      value: "0 */2 * * *"
    - name: DRY_RUN
      value: "false"
```

## Runner Locations

Runner locations are named identifiers for where scripts execute. Common values:
- `location-01-us-west1`
- `location-01-us-central1`

The available locations depend on the workspace configuration.
