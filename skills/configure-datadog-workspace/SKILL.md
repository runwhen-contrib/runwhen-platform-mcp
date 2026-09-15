---
name: configure-datadog-workspace
description: "Tune a RunWhen workspace that has the Datadog MCP server connected: install Datadog chat rules, knowledge notes (vocabulary, tool routing, RunWhen↔Datadog naming, estate profile, environment map) and chat commands, including scheduled morning/evening Datadog digests. Use when: (1) Datadog MCP was just registered on a workspace, (2) workspace chat struggles with Datadog queries, tag names, or staging-vs-production confusion, (3) the user wants a daily Datadog newsletter/digest from monitors, synthetics, hosts or APM, or (4) the Datadog account changed and the workspace guidance needs recalibrating."
---

# Configure a Datadog workspace

Workspace chat talks to Datadog through a generic MCP bridge (`ws_ls /mcp/datadog/tools/`, `mcp_call`). It knows nothing about Datadog's vocabulary, its tag names, or which environment Datadog covers. Without guidance it guesses argument names, uses Loki-style keys (`namespace:`, `container:`) that do not exist in Datadog, and concludes "no data" from its own wrong filters. This skill installs a tested pack that fixes those failure modes and turns Datadog into scheduled digests the team reads every day.

All pack content lives in `references/` and is listed in `references/pack.yaml`. Read files with `get_skill(name="configure-datadog-workspace", reference="references/<path>")`, or from disk if your client loads skills from the filesystem.

## What gets installed

| Kind | Item | Why this lever |
|---|---|---|
| Rule | `datadog-environment-boundary` | Rules are injected into every model call. This one keeps workspace (e.g. staging) and Datadog (e.g. production) facts apart. |
| Rule | `datadog-query-discipline` | Points the agent to the guide, and forces schema-first calls, name resolution and the zero-results check. Kept under 1,500 characters because it costs tokens on every call. |
| Knowledge (global) | Datadog MCP operating guide | Vocabulary, tool routing, argument traps, token discipline. Global notes appear by title; the rule makes the agent open it. |
| Knowledge (global) | Datadog and RunWhen naming | Kubernetes/GKE/GCE/GCP tag translation and resolution recipes that work without logs. |
| Knowledge (global) | Datadog estate profile | What this Datadog account actually contains. Filled in by `/datadog-calibrate`. |
| Knowledge (resource-scoped) | Datadog environment map (one per workspace cluster or project) | Auto-attaches when the agent touches resources under that path, and names the Datadog counterpart. |
| Command | `datadog-calibrate`, `datadog-resolve` | Discovery before assumptions; a name-to-identity lookup. |
| Command (scheduled) | `datadog-morning-brief`, `datadog-evening-wrap`, `datadog-monitor-hygiene` | The newsletter: fixed call plan, fixed sections, bounded cost. |

## Workflow

### 1. Preflight: the MCP registration

Ask workspace chat to list the server's tools. There is no direct tool for this.

```
workspace_chat(workspace_name="<ws>", message="Run ws_ls /mcp/datadog/tools/ and report: total tool count, whether monitor_groups_search, search_datadog_slos, get_synthetics_tests, search_datadog_hosts, aggregate_events, list_datadog_skills exist, and every tool that requires approval.")
```

Check against these recommendations. Fix the registration before installing anything:

- **Endpoint toolsets.** Use `https://mcp.<datadog-site>/v1/mcp?toolsets=core,alerting,synthetics`, and add `kubernetes` if GKE is monitored. `core` alone lacks monitor groups, SLOs and synthetics. `toolsets=all` exposes ~253 tools (~160k tokens of definitions), which slows tool browsing and invites wrong picks. Measured 2026-09-15: `core` 28 tools, `core,alerting,synthetics` 39, `all` 253.
- **Auth.** Headers `DD_API_KEY` and `DD_APPLICATION_KEY`. Use an application key scoped to read access.
- **Approval policy.** `autoApproveReadOnly` on, so Datadog's read-only tools run unattended. A scheduled command that hits a tool needing approval does not fail fast. It waits for a click that never comes, until the run times out.
- If `list_datadog_skills` is missing, that is fine: the pack does not depend on it.

### 2. Interview. These values cannot be discovered, so ask.

| Placeholder | Ask |
|---|---|
| `WORKSPACE_ENV` | "Which environment are this workspace's discovered resources?" |
| `DD_ENV_LABEL` | "Which environment does the connected Datadog account cover?" |
| `DD_SCOPE_FILTER` | "Which clusters / GCP projects / host groups make up that environment in Datadog?" Prefer `kube_cluster_name:` and `project:` scopes. **Do not rely on `env:` alone**: it is set by agent config and has been measured spanning several environments on one cluster. |
| `DATADOG_BLIND_SPOTS` | "What is deliberately not in Datadog? Logs? Which services have APM?" |
| Delivery | Slack channel or emails, the local send times and timezone, `run_as_user`, `assistant_name`. |

Cron for scheduled commands is evaluated in **UTC**. Convert local times; the defaults in `pack.yaml` (13:00 / 23:00 UTC on weekdays) match 09:00 / 19:00 US Eastern daylight time.

### 3. Audit what is already there

```
list_chat_rules(scope_type="workspace", scope_id="<ws>")
list_chat_commands(scope_type="workspace", scope_id="<ws>")
list_knowledge_base_articles(workspace_name="<ws>")
```

Look for existing rules that tell the agent how to query logs or name resources, for example "use container label X" written for Loki or GCP Logging. Rules apply to every tool, so such a rule steers Datadog queries too. Scope it to its own tool ("For Loki queries...") or retire it with the user's agreement. Update items that already exist by name; do not duplicate them.

### 4. Install rules and knowledge

For each item in `pack.yaml`, read the file, substitute every `{{NAME}}`, and check no `{{` remains. Then:

```
create_chat_rule(name="datadog-environment-boundary", rule_content=<text>, scope_type="workspace", scope_id="<ws>", workspace_name="<ws>")
create_knowledge_base_article(workspace_name="<ws>", title="Datadog MCP operating guide", content=<text>, resource_paths=[])
```

For the estate profile, substitute the calibration placeholders (`DD_PRODUCTS_TABLE`, `DD_APM_SERVICES`, `DD_SYNTHETICS_SUMMARY`, `DD_VM_FLEET`, `DD_MONITOR_CONVENTIONS`, `DD_TOOLSETS`, `DD_CALIBRATED_AT`) with `not yet calibrated` for now.

### 5. Calibrate

Create `datadog-calibrate` and `datadog-resolve` (no schedule), then run calibration through chat:

```
workspace_chat(workspace_name="<ws>", message="/datadog-calibrate")
```

If the command link syntax is not available through this path, send the command body as the message. Review its **Contradictions** and **Questions for a human** with the user. Write the returned values into the estate profile with `update_knowledge_base_article`. If calibration shows `DD_SCOPE_FILTER` or `DATADOG_BLIND_SPOTS` were wrong, update the two rules as well.

### 6. Environment map notes

List the workspace's top-level scopes from `get_workspace_config_index` (resource paths `kubernetes/<cluster>` and `gcp/<project>`). For each scope that has a Datadog counterpart, agree the counterpart filter and name substitutions with the user. Confirm them with `/datadog-resolve` on two or three real workloads. Then create one resource-scoped note per scope:

```
create_knowledge_base_article(workspace_name="<ws>", title="Datadog environment map: <scope>", content=<text>, resource_paths=["kubernetes/<cluster>"])
```

Ancestor paths are valid, so one note per cluster or project covers everything beneath it. Keep each note under 3,200 characters, because auto-attached notes are truncated there.

### 7. Scheduled digests

```
create_chat_command(
    name="datadog-morning-brief", command_content=<text>, description=<from pack.yaml>,
    scope_type="workspace", scope_id="<ws>",
    cron_schedule="0 13 * * 1-5", run_as_user="<member email>", assistant_name="<persona short name>",
    sink_configs=[{"type": "slack", "mode": "channel", "target": "<channel>"}],
    auto_approve_readonly=False,
)
```

Leave `auto_approve_readonly` off. It governs RunWhen task execution, not Datadog calls; the digests only read Datadog, and each command body explicitly allows read-only `mcp_call`. Repeat for `datadog-evening-wrap` and the weekly `datadog-monitor-hygiene`.

### 8. Verify with the failure modes that motivated this pack

Run each through `workspace_chat` and read the tool calls, not just the answer:

1. `/datadog-morning-brief` (unscheduled). Expect no schema errors, at most 14 Datadog calls, every section present, and a Coverage section naming the blind spots.
2. "Check <service> logs in Datadog." Expect it to honour the logs blind spot instead of guessing 6 query variants.
3. "Is <workspace deployment> healthy in Datadog?" Expect it to name the counterpart via the environment map, label the estate, and use `kube_*` keys.
4. "What alerted overnight?" Expect `aggregate_events` on `source:alert`, not a raw event dump.

Any `namespace:`, `container:`, `sort: desc`, `time_period`, or "Datadog has no data for X" after a guessed filter means the rules are not being applied. Check they are active and scoped to this workspace.

### 9. Keep it current

Re-run `/datadog-calibrate` monthly and after onboarding new APM services, synthetics or toolsets. Update environment map notes when clusters or projects are renamed.

## Design notes

- Rules carry only triggers and hard constraints; detail lives in knowledge notes. Rules are paid for on every model call; notes are read on demand.
- Commands encode verified argument shapes (tested against a live Datadog MCP server, 2026-09-15) so scheduled runs never explore.
- `references/evidence.md` records the measurements behind each choice.
