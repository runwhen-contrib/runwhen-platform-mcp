---
name: discover-locations
description: "Discover and select runner locations for RunWhen scripts. Use when: (1) Choosing where to execute a task or script, (2) The user asks about runner locations or execution environments, (3) Multiple locations exist and you need to pick the right one, or (4) Building a task that needs a specific cluster or region for execution."
---

# Discover Locations

Find and select the right runner location for an SLX script.

## When to use

- When multiple runner locations are available and you need to choose
- When the user asks about runner locations or execution environments
- When troubleshooting a script that can't reach infrastructure
- When `_resolve_location` raises an error listing multiple options

## Auto-resolution — you often don't need this skill

The MCP server **automatically resolves the location** when you omit it
from `run_script`, `run_script_and_wait`, `commit_slx`, or
`deploy_registry_codebundle`. The resolution strategy is:

1. If only **one workspace (private) location** exists → use it
2. If **multiple workspace locations** exist → inspect existing SLX
   runbook configs to pick the most-used one; if ambiguous, raise an
   error listing the options (you should ask the user)
3. If **no workspace locations** exist → fall back to the public runner

You only need this skill when auto-resolution fails (multiple ambiguous
options) or when the user wants to understand or override the choice.

## What locations are

Runner locations are **where scripts physically execute**. They are
lightweight RunWhen agents installed in the user's infrastructure
(Kubernetes clusters, cloud VPCs, etc.) that receive and run scripts.

Each location has:
- A **name** (e.g. `location-01-us-west1`, `watcher-controlplane`)
- A **type**: `workspace` (private — has access to workspace infrastructure) or `public` (shared runner)
- A **health status**: `online`, `stale`, or `unknown`
- **Access to specific infrastructure** — a workspace runner can reach the workspace's resources; the public runner has generic internet access only

## Location types

| Type | Meaning | When to use |
|------|---------|-------------|
| `workspace` | Private runner with access to workspace infrastructure (k8s clusters, databases, etc.) | Scripts that need to reach internal resources — **always prefer this** |
| `public` | Shared public runner with generic internet access | Scripts that only need public APIs, or when no workspace runner is available |

## Discovery workflow

### Step 1: List available locations

```
get_workspace_locations(workspace_name="my-workspace")
```

Returns a list with health information:
```json
[
  {"name": "watcher-controlplane", "value": "725200aa-...", "type": "workspace", "health": "online"},
  {"name": "location-01", "value": "location-01", "type": "public", "health": "online"}
]
```

### Step 2: Choose the right location

**Prefer workspace locations over public.** Workspace runners have access
to the workspace's actual infrastructure (Kubernetes clusters, databases,
secrets). Public runners only have generic internet access.

| Script targets | Choose |
|---------------|--------|
| Kubernetes resources, internal services | Workspace location |
| AWS/GCP/Azure resources via service accounts | Workspace location with those credentials |
| Public APIs (GitHub, Slack, HTTP endpoints) | Any healthy location (workspace or public) |

### Step 3: If multiple workspace locations — verify with existing SLXs

When a workspace has multiple workspace-type locations, check what
existing SLXs use:

```
workspace_chat(message="what locations do existing SLXs use?", workspace_name="my-workspace")
```

Or inspect a specific SLX's runbook to see its `spec.location`:

```
get_slx_runbook(slx_name="some-existing-slx", workspace_name="my-workspace")
```

### Step 4: If still ambiguous — ask the user

Present the options with their health status and let the user decide:

> "I found multiple runner locations for this workspace:
> - **watcher-controlplane** (workspace, online)
> - **prod-runner-01** (workspace, online)
>
> Which location should I use for this script?"

## Common patterns

### Single workspace location (most common)
Auto-resolution handles this — no need to specify:
```
run_script_and_wait(workspace_name="my-workspace", script=my_script, ...)
```

### Explicit location override
When you know which location to use:
```
run_script_and_wait(workspace_name="my-workspace", script=my_script, location="watcher-controlplane", ...)
```

### Location in commit_slx
The location is baked into the SLX config permanently:
```
commit_slx(slx_name="my-check", workspace_name="my-workspace", location="watcher-controlplane", ...)
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| "Multiple runner locations available" error | Auto-resolution can't disambiguate | Ask the user which location to use, or check existing SLXs |
| "No runner locations found" error | No runners registered for the workspace | Check workspace runner setup in RunWhen UI |
| Script times out or can't reach resources | Wrong location — runner can't reach the target | Try a different workspace location |
| Location shows `stale` or `unknown` health | Runner pod may be down or evicted | Check runner health; wait for recovery |
| Script works in location A but not B | Different secrets/access per location | Verify the target location has the required secrets |
