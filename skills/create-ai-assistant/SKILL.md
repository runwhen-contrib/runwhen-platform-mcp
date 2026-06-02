---
name: create-ai-assistant
description: "Create and tailor a new AI Assistant (Agent / persona) in a RunWhen workspace. Use when: (1) The user wants a dedicated assistant for a tech stack, team, or domain (e.g. 'Azure DevOps', 'Postgres on-call'), (2) Creating, updating, or deleting an assistant via create_assistant / update_assistant / delete_assistant, (3) Tailoring an assistant's behavior with persona-scoped rules and commands, or (4) The user asks to build an agent, assistant, or persona."
---

# Create an AI Assistant

An **AI Assistant** (also called an **Agent** or, internally, a **persona**) is a
named configuration of the RunWhen workspace AI. Each assistant can be focused on
a particular tech stack, team, or workflow — e.g. an "Azure DevOps Helper" that
prioritizes pipeline and repo tasks, or a "Postgres On-Call" that knows which
replicas to inspect.

The assistant's **`short_name`** is exactly the value you pass as `persona_name`
to `workspace_chat`. So once you create `azure-devops`, you talk to it with:

```
workspace_chat(workspace_name="my-workspace", message="...", persona_name="azure-devops")
```

You tailor an assistant in two layers:

1. **The assistant itself** — display name, description, and search/filter/run
   tuning (`create_assistant` / `update_assistant`).
2. **Persona-scoped rules and commands** — behavior instructions and reusable
   procedures attached to *just this assistant* (`create_chat_rule` /
   `create_chat_command` with `scope_type="persona"`).

## When to use

- A team wants a focused assistant for a specific tech stack (Azure DevOps, GKE, Kafka…)
- Different audiences need different investigation behavior in the same workspace
- You want to bundle stack-specific noise suppression, priorities, and runbooks under one identity

If you only need to tune behavior for *everyone* in a workspace, prefer
**workspace-scoped** rules/commands (see `manage-rules`, `manage-commands`) and skip
creating a new assistant.

## Key concepts

| Concept | Tool | Notes |
|---------|------|-------|
| Assistant identity | `create_assistant` | `short_name` becomes the `persona_name` for chat |
| Assistant tuning | `create_assistant` / `update_assistant` | Filters, search filters, run config |
| Per-assistant behavior | `create_chat_rule` (`scope_type="persona"`) | 1–3 sentence interpretation rules |
| Per-assistant procedures | `create_chat_command` (`scope_type="persona"`) | Reusable slash-command workflows |
| Verify | `workspace_chat` (`persona_name=...`) | Talk to the assistant to confirm behavior |

### `create_assistant` is an upsert

Calling `create_assistant` with an existing `short_name` **replaces the full
configuration** (omitted fields reset to defaults). To change just a few fields
on an existing assistant, use `update_assistant`, which fetches the current
config and merges your changes over it.

## Workflow

### 1. Check what already exists

```
list_assistants(workspace_name="my-workspace")
```

Pick a `short_name` that isn't taken. Use lowercase kebab-case (e.g. `azure-devops`).

### 2. Create the assistant

```
create_assistant(
    workspace_name="my-workspace",
    short_name="azure-devops",
    display_name="Azure DevOps Helper",
    description="Specializes in Azure DevOps pipelines, repos, and boards. Prioritizes CI/CD and deployment health.",
    filter_codebundle_task_tags=["azure", "devops", "ci-cd"]
)
```

`filter_codebundle_task_tags` biases which tasks the assistant surfaces. Leave it
empty if you don't want a tag filter. Advanced tuning (`search_filters`,
`run_config`, confidence thresholds) is optional — the defaults are sensible.

### 3. Add persona-scoped rules (behavior)

Attach 2–3 focused rules so the assistant interprets findings the way this
audience expects. Use `scope_type="persona"` and `scope_id=<short_name>`.

```
create_chat_rule(
    name="Prioritize Pipeline Failures",
    rule_content="For Azure DevOps investigations, lead with pipeline and release failures. Treat repo policy warnings as secondary unless they block a release.",
    scope_type="persona",
    scope_id="azure-devops",
    workspace_name="my-workspace"
)
```

See the `manage-rules` skill for how to write good rules (short, specific,
"unless correlated" language).

### 4. Add persona-scoped commands (procedures)

Give the assistant reusable, repeatable investigation procedures.

```
create_chat_command(
    name="triage-pipeline",
    command_content="Triage the failing Azure DevOps pipeline: identify the failed stage, pull the error logs, check recent commits to the triggering branch, and summarize the likely cause with next steps.",
    scope_type="persona",
    scope_id="azure-devops",
    workspace_name="my-workspace"
)
```

See the `manage-commands` skill for naming and content guidance (name must be
alphanumeric / underscore / hyphen only).

### 5. Verify by talking to the assistant

```
workspace_chat(
    workspace_name="my-workspace",
    message="What's failing in my pipelines right now?",
    persona_name="azure-devops"
)
```

Confirm the assistant leads with the right priorities and that its rules and
commands are in effect. Review the resolved configuration any time with:

```
get_workspace_chat_config(workspace_name="my-workspace", persona_name="azure-devops")
```

### 6. Iterate

- Adjust a few settings without resetting everything: `update_assistant(...)`.
- Add/refine rules and commands as the team's needs become clearer.
- Remove an assistant (soft-delete) when it's no longer needed: `delete_assistant(...)`.
  Persona-scoped rules and commands are **not** deleted automatically — clean
  them up separately if you don't plan to reuse the `short_name`.

## Example: standing up an "Azure DevOps" assistant end to end

```
# 1. Create
create_assistant(
    workspace_name="my-workspace",
    short_name="azure-devops",
    display_name="Azure DevOps Helper",
    description="Azure DevOps pipelines, repos, and boards specialist.",
    filter_codebundle_task_tags=["azure", "devops"]
)

# 2. Behavior
create_chat_rule(
    name="Lead With Releases",
    rule_content="Prioritize release and pipeline health over board/work-item noise unless a work item is explicitly referenced.",
    scope_type="persona", scope_id="azure-devops", workspace_name="my-workspace"
)

# 3. Procedure
create_chat_command(
    name="triage-pipeline",
    command_content="Find the failed stage, pull logs, correlate with recent commits, summarize cause and next steps.",
    scope_type="persona", scope_id="azure-devops", workspace_name="my-workspace"
)

# 4. Verify
workspace_chat(workspace_name="my-workspace", message="Triage my latest failed pipeline", persona_name="azure-devops")
```

## Tips

- **Name for the audience, not the tool.** `platform-oncall` or `azure-devops`
  reads better than `assistant-2`.
- **Start small.** Create the assistant, add 2–3 rules and 1–2 commands, then test.
- **Reach for an assistant only when behavior must differ.** Otherwise tune the
  workspace-scoped config and keep one assistant.
- **Pair with `build-operational-context`** to grow the workspace from a single
  default assistant up through rules, commands, and knowledge.
