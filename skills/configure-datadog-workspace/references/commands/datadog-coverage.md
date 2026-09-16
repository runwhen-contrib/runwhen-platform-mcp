Report what the connected Datadog account actually contains, so the team (and later questions in this workspace) know which Datadog products hold data and how the account is organised. Run it after connecting Datadog, and again after big changes such as new APM services or synthetics.

## Rules for this run

- Read-only tools on the Datadog MCP server, called through `mcp_call`, only. Find the server with `ws_ls /mcp/` (its tools include `search_datadog_monitors`). No tool that requires approval.
- Do not guess. Every value you report must come from a tool response in this run; otherwise write `unknown`.
- Set `max_tokens` ≤ 6000 on every call. At most 18 Datadog calls.

## Steps

1. **Tools.** `ws_ls /mcp/<server>/tools/`. Record whether these exist: `search_datadog_monitors`, `monitor_groups_search`, `search_datadog_slos`, `get_synthetics_tests`, `search_datadog_hosts`, `aggregate_events`, `search_datadog_incidents`, `get_change_stories`, `list_datadog_skills`. Record every tool marked as requiring approval.
2. **Environments and clusters.** `get_datadog_metric`, `response_format: "scalar"`, `from: now-1h`, query object `{query:"sum:datadog.agent.running{*} by {env,kube_cluster_name}", aggregator:"last"}`. Lists the `env` values and clusters that have agents. Flag any `env` value shared by clusters or namespaces that look like different environments.
3. **Hosts.** `search_datadog_hosts`: `SELECT cloud_provider, os, tags->'project' AS project, tags->'env' AS env, COUNT(*) AS hosts FROM hosts GROUP BY cloud_provider, os, tags->'project', tags->'env' ORDER BY hosts DESC LIMIT 50`. Then `SELECT COUNT(*) AS vms FROM hosts WHERE tags->'kube_cluster_name' IS NULL` for non-Kubernetes hosts.
4. **Processes.** `search_datadog_metrics`, `name_filter: "datadog.process"`. If `datadog.process.per_command.cpu.total_pct` exists, `get_datadog_metric_context` on it for its indexed tag keys (note whether `host` is one).
5. **Logs.** `search_datadog_logs`, `query: "*"`, `from: now-24h`, `limit: 1`. Then the same with `storage_tier: "flex_and_indexes"`. Zero from both means logs are not searchable in Datadog.
6. **APM.** `search_datadog_metrics`, `name_filter: "trace hits errors duration"`, `include_top_tag_keys: true`. For at most 4 `trace.*.hits` metrics found, scalar `sum:<metric>{*} by {service,env}.as_count()` over `now-24h`. That list is the instrumented services.
7. **Synthetics.** `get_synthetics_tests` `mode: "configs"`, `summary: true` (page with `pagination_index` until empty or 5 pages). Count tests by type and state; list the main domains. Then `get_datadog_metric_context` for `synthetics.test_runs` with `include_tag_values: true`, and record the tag key that holds pass/fail and the one that holds the test name.
8. **Monitors.** `search_datadog_monitors`, `query: "*"`, `include_tags: ["env*","service*","team*","priority*"]`, `max_tokens: 6000`. Summarise: approximate count by type and priority, which tag keys monitors use for scope, and the notification handles you see (`@slack-...`, `@pagerduty-...`). Do not quote messages.
9. **SLOs.** `search_datadog_slos`, `page_size: 50`: count and naming pattern.
10. **Incidents.** `search_datadog_incidents`, `from: now-30d`, `with_facets: true`.
11. **Events.** `aggregate_events`, `query: "*"`, `from: now-7d`, count grouped by `["source"]`. Shows whether `change_tracking` and `alert` events exist.

## Output

One markdown report (use `display_markdown_report` if this is a scheduled run):

1. **Products** — a table: product | holds data? | scope | notes. Cover monitors, synthetics, APM, logs, processes, hosts, Kubernetes, SLOs, incidents, change tracking.
2. **How the account is organised** — clusters, projects and `env` values, and whether `env` can be trusted to separate environments here.
3. **Instrumented services** — the APM list, and the trace metric names they emit.
4. **Gaps worth knowing** — products with no data, missing toolsets, tools that require approval.
5. **Questions only a person can answer** — for example which of these clusters is production.

Suggest the user saves the report as a knowledge note if they want later conversations to use it.
