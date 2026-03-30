---
name: manage-rules
description: "Create, update, and manage workspace chat rules that shape how AI assistants interpret infrastructure findings. Use when: (1) The user wants to suppress noise or adjust investigation priorities, (2) Adding operational context or tuning assistant behavior, (3) Creating or editing chat rules via create_chat_rule or update_chat_rule, or (4) The user asks about rules, assistant behavior, or investigation tuning."
---

# Manage Rules

Rules are loaded into the assistant's prompt context on every response. They shape how
findings are interpreted — what to de-prioritize, what to highlight, and how to frame
expected behavior versus actionable risk.

## When to use

- Assistant responses are technically correct but operationally noisy
- Known infrastructure churn keeps dominating investigations
- Important application issues are buried in platform noise
- Severity language is inconsistent across investigations
- User asks to "tune", "adjust", or "configure" the assistant's behavior

## Key concepts

### Scoping

| Scope | Effect | `scope_type` | `scope_id` |
|-------|--------|--------------|------------|
| Workspace | All assistants in the workspace see the rule | `workspace` | workspace name (e.g. `my-workspace`) |
| Persona (assistant) | Only a specific assistant sees the rule | `persona` | persona name |
| Platform | All workspaces in the organization | `platform` | `null` |

Most rules should be **workspace-scoped**. Use persona scope only when different
assistants need different interpretation behavior.

### Rule anatomy

Good rules are **short, explicit instructions with clear priority direction**:

```
Acknowledge that GKE node preemptions are expected in this cluster.
Do not treat preemptions as root cause unless clearly correlated with
user-reported symptoms.
```

Bad rules are vague or too broad:

```
Be better at analyzing Kubernetes issues.
```

## Workflow

### 1. Audit existing rules

```
list_chat_rules(scope_type="workspace", scope_id="my-workspace")
```

### 2. Identify noise patterns

Use `workspace_chat(workspace_name="my-workspace", message="...")` to run a baseline prompt your team asks often. Note which findings
are repeated noise vs actual issues. Common noise sources:

- Node preemptions / spot instance recycling
- CNI flapping in shared clusters
- Resource pressure on non-critical namespaces
- Platform controller reconciliation events
- Expected CronJob failures in dev/test

### 3. Create rules

```
create_chat_rule(
    name="Deprioritize Node Pressure",
    rule_content="Node memory and CPU pressure in shared lab clusters is expected background noise. Mention it briefly but prioritize application-level issues (crash loops, error logs, failed dependencies) first. Only escalate node pressure if it correlates with pod evictions affecting user workloads.",
    scope_type="workspace",
    scope_id="my-workspace"
)
```

### 4. Re-test with the same baseline prompt

Run the same `workspace_chat(workspace_name="my-workspace", message="...")` prompt and compare. Focus on:
- Is known noise still dominating?
- Are real issues surfaced earlier?
- Are next steps more actionable?

### 5. Iterate

Update rules based on results. Over-suppressed something? Adjust with "unless correlated"
language. Still too noisy? Add another targeted rule.

## Rule categories and examples

### Noise suppression

```
name: "Acknowledge Lab Preempts"
content: "GKE preemptible node cycling is expected in dev/lab clusters. Acknowledge it briefly but do not treat it as root cause unless correlated with user-reported symptoms."
```

### Priority framing

```
name: "Application Issues First"
content: "Always prioritize application-level findings (crash loops, OOM kills, error rate spikes, dependency failures) over infrastructure-level observations. Infrastructure findings should support app-level analysis, not lead it."
```

### Environment context

```
name: "Dev Cluster Expectations"
content: "The dev-cluster runs with reduced resource quotas and no autoscaling. Resource constraint warnings are expected and should not be flagged as critical unless pods are actually evicted."
```

### Temporary rules (incidents / releases)

```
name: "Release Window Q1-Sprint3"
content: "From March 25-28, the payments service is being migrated to a new database. Connection errors and increased latency from payments-api are expected during this window. Focus on non-payments services for actionable issues."
```

Mark temporary rules with `is_active=False` when the event passes, or delete them.

## Writing tips

- **1-3 sentences max.** If it needs a paragraph, it belongs in Knowledge instead.
- **Use "unless correlated" language** to avoid creating blind spots.
- **Be specific** — name the service, cluster, namespace, or error pattern.
- **Start with 2-3 rules**, re-test, then iterate. Over-ruling degrades quality.
- **Review quarterly** — rules about flaky services become waste once stabilized.
- **Name rules clearly** — the name should describe what the rule does at a glance.
