---
name: build-operational-context
description: Build operational context in a RunWhen workspace by adding Rules, Commands, and Knowledge. Use when the user wants to improve assistant investigation quality, tune workspace behavior, onboard engineers, or follow the operational context maturity model.
---

# Build Operational Context

Guide for progressing through the RunWhen operational context maturity model.
Reference: https://docs.runwhen.com/guides/building-operational-context/

## Maturity levels

| Level | What to add | Tools | Skill |
|-------|------------|-------|-------|
| **1 — Foundation** | Tasks against live infrastructure | `commit_slx`, `run_slx` | `build-runwhen-task`, `run-existing-slx` |
| **2 — Signal** | Rules for noise suppression + priority framing | `create_chat_rule`, `update_chat_rule` | `manage-rules` |
| **3 — Standardization** | Commands for repeatable procedures + Knowledge for institutional context | `create_chat_command`, `create_knowledge_base_article` | `manage-commands`, `manage-knowledge` |
| **4 — Integration** | Automated context from CI/CD, incidents, change management | All API tools | (engineering integration) |

## Quick workflow

### Step 1: Capture a baseline

Run a prompt your team asks often through `workspace_chat`:

```
workspace_chat(workspace_name="my-workspace", message="What's unhealthy in my-namespace?")
```

Note the quality: Is it focused? Are expected issues over-emphasized?
Are next steps specific? This is your Level 1 baseline.

### Step 2: Add Rules (→ Level 2)

See the `manage-rules` skill for detailed guidance.

Start with 2-3 rules addressing the biggest noise sources:

```
create_chat_rule(
    name="Deprioritize Node Pressure",
    rule_content="Node pressure in shared lab clusters is expected. Mention briefly but prioritize app-level issues first.",
    scope_type="workspace",
    scope_id="my-workspace"
)
```

Re-run your baseline prompt. Compare focus, clarity, and actionability.

### Step 3: Add Commands (→ Level 3)

See the `manage-commands` skill for detailed guidance.

Create 1-2 commands for your most common investigation patterns:

```
create_chat_command(
    name="investigate-namespace",
    command_content="Investigate pod status, events, error logs, resource pressure, and dependencies. Group findings by severity with specific next steps.",
    scope_type="workspace",
    scope_id="my-workspace"
)
```

### Step 4: Add Knowledge (→ Level 3 complete)

See the `manage-knowledge` skill for detailed guidance.

Add 3-5 durable facts that investigations keep missing:

```
create_knowledge_base_article(
    workspace_name="my-workspace",
    content="Team Platform owns shared-services namespace. Team Backend owns backend-services. Escalation: #platform-oncall for infra, #backend-oncall for apps.",
    abstract_entities=["ownership", "escalation", "teams"]
)
```

### Step 5: Compare

Re-run the exact baseline prompt from Step 1:

```
workspace_chat(workspace_name="my-workspace", message="What's unhealthy in my-namespace?")
```

Compare: less noise, cleaner grouping, clearer remediation paths.

## Rollout timeline

| When | What | Who | Time |
|------|------|-----|------|
| Day 1 | 2-3 Rules for known noise | 1 engineer | ~30 min |
| Week 1-2 | 1-2 Commands for common investigations | 1 engineer | ~1 hr |
| Week 2-3 | 3-5 Knowledge entries (ownership, architecture, releases) | 1-2 engineers | ~1-2 hrs |
| Week 4 | Review + adjust Rules, capture new Knowledge | 1 engineer | ~30 min |
| Ongoing | Capture context during normal investigations | Anyone | Minutes per entry |

## Context type decision guide

| Content type | Use when | Example |
|-------------|----------|---------|
| **Rule** | Behavior instruction (1-3 sentences) | "Deprioritize node pressure in dev cluster" |
| **Command** | Repeatable multi-step procedure | "Investigate namespace health: pods → events → logs" |
| **Knowledge** | Durable operational fact | "Payments service depends on Cloud SQL and Redis" |
| **Temporary Knowledge** | Time-bounded context | "Release window March 25-28: expect payments errors" |
| **Task (SLX)** | Live diagnostic script | Health check that runs on runner infrastructure |

## Combining durable and temporary context

| Type | Time horizon | Examples | Where to encode |
|------|-------------|----------|-----------------|
| **Durable** | Months/quarters | Ownership, release process, architecture | Rules + Commands + Knowledge |
| **Short-term** | Days/weeks | Active release window, incident context | Knowledge updates + temporary Commands |

Use `status="deprecated"` or `is_active=False` to retire temporary context
when events pass. Delete fully when no longer needed.

## Tool reference

| Action | Tool | Notes |
|--------|------|-------|
| Baseline investigation | `workspace_chat` | Search and analyze only — cannot execute tasks |
| List rules | `list_chat_rules` | Filter by `scope_type` and `scope_id` |
| Create rule | `create_chat_rule` | Keep to 1-3 sentences |
| List commands | `list_chat_commands` | Filter by scope |
| Create command | `create_chat_command` | Name: alphanumeric/underscore/hyphen only |
| List KB articles | `list_knowledge_base_articles` | Search and filter by status |
| Create KB article | `create_knowledge_base_article` | Use `resource_paths` for scoping |
| Update KB article | `update_knowledge_base_article` | Partial updates supported |
| Delete KB article | `delete_knowledge_base_article` | Removes from KB and search index |
| Run existing task | `run_slx` | Execute a committed SLX |
| Review what's configured | `get_workspace_chat_config` | See all resolved rules + commands |
