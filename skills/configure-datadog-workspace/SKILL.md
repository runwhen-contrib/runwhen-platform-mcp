---
name: configure-datadog-workspace
description: "Install the Datadog pack on a RunWhen workspace that has the Datadog MCP server registered: one chat rule, two knowledge notes (vocabulary and tool routing; RunWhen↔Datadog naming) and on-demand Datadog commands, including morning/evening digests the user can schedule. The pack needs no user input. Use when: (1) a Datadog MCP server was registered on a workspace, (2) workspace chat struggles with Datadog queries, tag names, or confuses Datadog data with workspace resources, or (3) the user wants a daily Datadog digest from monitors, synthetics, hosts or APM."
---

# Install the Datadog pack

Workspace chat talks to Datadog through a generic MCP bridge (`ws_ls /mcp/<server>/tools/`, `mcp_call`). Without guidance it guesses argument names, uses Loki-style keys (`namespace:`, `container:`) that do not exist in Datadog, and concludes "no data" from its own wrong filters. This pack fixes those failure modes.

## What a pack is

`references/pack.yaml` is the whole contract:

- **Items** — rules, knowledge notes and commands, each a file installed **verbatim**. There are no placeholders: packs need no user input, so any installer can apply them without asking anything.
- **`match`** — which registered MCP servers the pack applies to: catalog ids (`datadog`) and endpoint hosts (`mcp.datadoghq.com` and the other Datadog sites). Any installer — this skill today, a registration hook or an "install pack" button later — decides applicability the same way.

Read item files with `get_skill(name="configure-datadog-workspace", reference="references/<path>")`, or from disk if your client loads skills from the filesystem.

| Kind | Item | Why |
|---|---|---|
| Rule | `datadog-mcp` | Injected on every model call, so it stays under 1,500 characters: find the server, read the guide, schema first, resolve names, treat zero results as a wrong filter, never mistake Datadog data for a workspace resource. Ignores itself when no Datadog server is registered. |
| Knowledge | `datadog-mcp-operating-guide` | Vocabulary, which tool for which question, argument traps, response-size discipline. |
| Knowledge | `datadog-runwhen-naming` | Kubernetes/GKE/GCE/GCP tag translation and resolution recipes that work without logs. |
| Command | `datadog-coverage` | Surveys what the account actually contains (logs, APM, synthetics, processes, hosts, monitors). |
| Command | `datadog-resolve` | Maps a workspace resource or name to its Datadog identity, including counterparts in another environment. |
| Command | `datadog-morning-brief`, `datadog-evening-wrap`, `datadog-monitor-hygiene` | Digests with a fixed call plan, fixed sections and a call budget. Run on demand, or schedule them. |

## Workflow

### 1. Check the pack applies

```
workspace_chat(workspace_name="<ws>", message="Run ws_ls /mcp/. For each server, ws_cat /mcp/<name> and report its endpoint_url, then ws_ls /mcp/<name>/tools/ and report the tool count, whether monitor_groups_search, search_datadog_slos, get_synthetics_tests, search_datadog_hosts and aggregate_events exist, and every tool that requires approval.")
```

The pack applies when a server's endpoint host is in `match.mcp_endpoint_hosts`, or its tools are Datadog's (`search_datadog_monitors`). The registered **name** is the customer's own label and carries no meaning.

Recommend fixes to the registration before installing:

- **Toolsets.** `https://mcp.<datadog-site>/v1/mcp?toolsets=core,alerting,synthetics`, plus `kubernetes` if Kubernetes is monitored. `core` alone lacks monitor groups, SLOs and synthetics; `toolsets=all` exposes ~253 tools (~160k tokens of definitions). Measured 2026-09-15: `core` 28 tools, `core,alerting,synthetics` 39, `all` 253.
- **Auth.** A read-only credential (`Authorization: Bearer <token>`, or `DD_API_KEY` + `DD_APPLICATION_KEY`).
- **Approval policy.** `autoApproveReadOnly` on. A scheduled command that hits a tool needing approval does not fail fast; it waits for a click that never comes, until the run times out.

### 2. Audit what is already there

```
list_chat_rules(scope_type="workspace", scope_id="<ws>")
list_chat_commands(scope_type="workspace", scope_id="<ws>")
list_knowledge_base_articles(workspace_name="<ws>")
```

Rules apply to every tool. An existing rule written for another log backend ("use container label X") will steer Datadog queries too; point it out to the user so they can scope it ("For Loki queries, ...") or retire it. If a pack item already exists by name, update it rather than creating a duplicate.

### 3. Install

For each item in `pack.yaml`, read its file and create it exactly as written:

```
create_chat_rule(name="datadog-mcp", rule_content=<file>, scope_type="workspace", scope_id="<ws>", workspace_name="<ws>")
create_knowledge_base_article(workspace_name="<ws>", title="datadog-mcp-operating-guide", content=<file>, resource_paths=[])
create_chat_command(name="datadog-morning-brief", command_content=<file>, description=<from pack.yaml>, scope_type="workspace", scope_id="<ws>")
```

Do not edit the text while installing. The query shapes in it were verified against a live Datadog MCP server.

### 4. Optional: schedule the digests

Scheduling is the user's choice and needs their inputs, so it is not part of the pack. If they want it, ask for the delivery target, the local times, and who the run should act as, then `update_chat_command` with `cron_schedule`, `sink_configs` (e.g. `{"type": "slack", "mode": "channel", "target": "<channel>"}`), `run_as_user` and `assistant_name`. Cron is evaluated in **UTC**: weekdays 09:00 and 19:00 US Eastern daylight time are `0 13 * * 1-5` and `0 23 * * 1-5`; a Monday hygiene report fits `0 14 * * 1`. Leave `auto_approve_readonly` off: it governs RunWhen tasks, and the commands already allow read-only Datadog calls explicitly.

### 5. Verify with the failure modes that motivated this pack

Run each through `workspace_chat` and read the tool calls, not just the answer:

1. `/datadog-coverage` — a products table built from tool responses, with gaps named.
2. "Check <service> logs in Datadog." — if the account has no logs, it says so after one probe instead of trying six query variants.
3. "Is <workspace deployment> healthy in Datadog?" — it resolves the name first, uses `kube_*` keys, and says whether it found the same object or a counterpart in another environment.
4. "What alerted overnight?" — `aggregate_events` on `source:alert`, not a raw event dump.

Any `namespace:`, `container:`, `sort: desc`, `time_period`, or "Datadog has no data for X" after a guessed filter means the rule is not being applied. Check it is active and scoped to this workspace.

## Design notes

- No inputs by design. Anything that needs a person (delivery targets, which cluster is production) stays an ordinary user action or an answer in chat, never a pack parameter.
- The rule carries only triggers and hard constraints; detail lives in the knowledge notes, which are read on demand.
- `references/evidence.md` records the measurements behind each choice.
