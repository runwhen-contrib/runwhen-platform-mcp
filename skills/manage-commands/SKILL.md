---
name: manage-commands
description: Create, update, and manage workspace chat commands (slash-commands) that package reusable investigation procedures. Use when the user wants to create repeatable diagnostic workflows, onboarding flows, or standardized investigation patterns.
---

# Manage Commands

Commands package multi-step investigations into one named operation. Users invoke
them in workspace chat as `/command-name`. Every engineer gets the same quality
investigation without crafting perfect prompts.

## When to use

- The same diagnostic sequence is repeated by multiple engineers
- "Tribal" investigation steps are known only by senior responders
- Variable quality between responders for the same issue type
- User asks to create an "investigation", "runbook", "procedure", or "workflow" for chat
- Onboarding new engineers who need guided entry points

## Key concepts

### Scoping

| Scope | Effect | `scope_type` | `scope_id` |
|-------|--------|--------------|------------|
| Workspace | All users in the workspace can invoke | `workspace` | workspace name |
| Persona | Only available when chatting with a specific assistant | `persona` | persona name |

### Command vs Task

| Concept | What it is | Invoked by |
|---------|-----------|------------|
| **Command** | Instructions for the AI assistant — what to investigate, how to present results | Users typing `/name` in chat |
| **Task (SLX)** | A script that runs on a runner against live infrastructure | `run_slx` or the platform scheduler |

Commands can **reference Tasks**. Instead of hardcoding diagnostic details, a command
can instruct the assistant to run or consult a Task for live data. This keeps commands
current without manual updates.

### Naming rules

Command names must be **alphanumeric, underscore, or hyphen only**. No spaces.
Users invoke them as `/command-name` in workspace chat.

## Workflow

### 1. Audit existing commands

```
list_chat_commands(scope_type="workspace", scope_id="my-workspace")
```

### 2. Identify repeatable patterns

Look for investigation steps your team does repeatedly:
- "Check pods, then events, then logs in namespace X"
- "Compare config between dev and prod"
- "What changed since last deployment?"

### 3. Create the command

```
create_chat_command(
    name="investigate-namespace",
    command_content="""Investigate the health of a Kubernetes namespace. Follow this sequence:

1. **Pod status** — Check for crash loops, pending pods, and recent restarts
2. **Warning events** — Surface Kubernetes warning events from the last hour
3. **Error logs** — Check application logs for error patterns
4. **Resource pressure** — Note any resource quota exhaustion
5. **Dependencies** — Check if upstream/downstream services are healthy

Present findings grouped by severity. For each issue found, include specific next steps.
If the namespace is healthy, confirm it explicitly and note any recent recoveries.""",
    scope_type="workspace",
    scope_id="my-workspace",
    description="Comprehensive namespace health investigation"
)
```

### 4. Test it

Use `workspace_chat(workspace_name="my-workspace", message="...")` with the command name to verify it produces the expected investigation flow.

## Command categories and examples

### Investigation commands

```
name: "investigate-namespace"
content: "Investigate pod status, events, error logs, resource pressure, and dependencies for the specified namespace. Group findings by severity."
```

### Comparison commands

```
name: "compare-environments"
content: "Compare configuration and health between the dev, staging, and production deployments of the specified service. Highlight drift, recent changes, and any environment-specific issues."
```

### Onboarding commands

```
name: "onboard-me"
content: "Welcome the user to this workspace. Explain:
1. What infrastructure this workspace monitors (clusters, services, environments)
2. Current health hotspots — top 3 active issues
3. Key services and their ownership
4. Recommended first prompts for common tasks
Keep it concise and actionable."
```

### Pre-deploy validation

```
name: "pre-deploy-check"
content: "Run pre-deployment validation for the specified service:
1. Check resource quotas and available headroom
2. Look for pending pod disruption budgets
3. Review recent error rate trends (last 2 hours)
4. Check if any dependencies are currently unhealthy
5. Verify the last deployment completed successfully
Flag any blockers and provide a go/no-go recommendation."
```

### Release-scoped commands (temporary)

```
name: "release-health"
content: "Check the health of services affected by the current release. Focus on:
- Deployment rollout status
- Error rate changes since deployment
- Latency changes since deployment
- Any new crash loops or OOM events
Compare to pre-release baseline."
```

Mark temporary commands as `is_active=False` when the release stabilizes.

## Design tips

- **Scope each command to a clear user goal** — "investigate namespace health" not "do everything"
- **Prefer deterministic checks before open-ended analysis** — structured findings first, interpretation second
- **Include output expectations** — tell the assistant what "good" vs "bad" looks like
- **Reference existing Tasks** for dynamic data rather than hardcoding details that go stale
- **Keep instructions actionable** — every finding should have a recommended next step
- **Name commands with verb-noun pattern** — `investigate-namespace`, `compare-environments`, `pre-deploy-check`
