---
name: build-runwhen-task
description: Build, test, and commit a RunWhen automation task (SLX). Use when creating health checks, troubleshooting scripts, or scheduled monitoring for infrastructure.
---

# Build RunWhen Task

End-to-end workflow for creating a RunWhen SLX from scratch.

## When to use

- Creating a new health check or monitoring task
- Building a troubleshooting runbook for infrastructure
- Automating incident response with scheduled checks
- Adding an SLI (health metric) to an existing task

## Instructions

Follow these steps in order:

### 1. Load context

Call `get_workspace_context` to read the project's RUNWHEN.md file. This contains infrastructure conventions, database access rules, naming patterns, and constraints that your script must follow. Do not skip this step.

### 2. Discover configuration

Call `get_workspace_secrets` and `get_workspace_locations` to find available secrets (e.g. kubeconfig) and runner locations for the target workspace.

### 3. Write the script

Write a bash or python script following the RunWhen contract:

**Python task** — `main()` returns `List[Dict]` with issue keys:
```python
def main():
    import os, subprocess
    issues = []
    # Your logic here — use os.environ for config, subprocess for kubectl
    issues.append({
        "issue title": "Descriptive title",
        "issue description": "Details about the problem",
        "issue severity": 2,
        "issue next steps": "Concrete remediation guidance",
    })
    return issues
```

**Bash task** — `main()` writes issue JSON to FD 3:
```bash
main() {
    # Your logic here
    issues='[]'
    jq -n --argjson issues "$issues" '$issues' >&3
}
```

Apply rules from RUNWHEN.md (e.g. replica targeting, kubectl flags, auth patterns).

### 4. Validate

Call `validate_script` with the script to check contract compliance before testing.

### 5. Test

Call `run_script_and_wait` with the script, a runner location, and any required env_vars / secret_vars. Review the returned issues, stdout, stderr, and report.

### 6. Iterate

If output is wrong or incomplete, fix the script and re-test. Repeat until the issues, severity levels, and next steps are correct.

### 7. Commit

Call `commit_slx` with:
- `slx_name`: lowercase-kebab-case (e.g. `postgres-replication-lag`)
- `alias`: human-readable name
- `statement`: what should be true (e.g. "Replication lag should be under 30s")
- `access`: `read-only` for monitoring, `read-write` for remediation
- `data`: `logs-bulk` for command output, `config` for config checks, `logs-stacktrace` for stack traces

Optionally add an SLI:
- `sli_script` for a custom health metric (returns 0-1)
- `cron_schedule` for time-based triggering (e.g. `"0 * * * *"` for hourly)
