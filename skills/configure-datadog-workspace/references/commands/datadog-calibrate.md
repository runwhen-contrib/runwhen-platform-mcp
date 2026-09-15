Calibrate this workspace's knowledge of its Datadog account. Discover what the Datadog MCP server can actually see, so the "Datadog estate profile" knowledge note describes reality instead of assumptions. Run this once after connecting Datadog, and again after big changes (new toolsets, new APM services, new synthetics).

## Rules for this run

- Read-only Datadog tools through `mcp_call` on server `datadog` only. No tool that requires approval.
- Do not guess. Every value you report must come from a tool response in this run; otherwise write `unknown`.
- Set `max_tokens` ≤ 6000 on every call. At most 18 Datadog calls.
- Current assumptions: Datadog covers {{DD_ENV_LABEL}}, scoped by {{DD_SCOPE_FILTER}}; workspace resources are {{WORKSPACE_ENV}}. Report evidence that contradicts these.

## Steps

1. **Tools.** `ws_ls /mcp/datadog/tools/`. Record whether these exist: `search_datadog_monitors`, `monitor_groups_search`, `search_datadog_slos`, `get_synthetics_tests`, `search_datadog_hosts`, `aggregate_events`, `search_datadog_incidents`, `get_change_stories`, `list_datadog_skills`, `load_datadog_skill`. Record every tool marked as requiring approval.
2. **Environments and clusters.** `get_datadog_metric`, `response_format: "scalar"`, `from: now-1h`, query object `{query:"sum:datadog.agent.running{*} by {env,kube_cluster_name}", aggregator:"last"}`. Lists the `env` values and clusters that have agents. Note clusters whose `env` does not match their real purpose.
3. **Hosts.** `search_datadog_hosts`: `SELECT cloud_provider, os, tags->'project' AS project, tags->'env' AS env, COUNT(*) AS hosts FROM hosts GROUP BY cloud_provider, os, tags->'project', tags->'env' ORDER BY hosts DESC LIMIT 50`. Then `SELECT COUNT(*) AS vms FROM hosts WHERE tags->'kube_cluster_name' IS NULL` for non-Kubernetes hosts.
4. **Processes.** `search_datadog_metrics`, `name_filter: "datadog.process"`. If `datadog.process.per_command.cpu.total_pct` exists, `get_datadog_metric_context` on it for its indexed tag keys (note whether `host` is one).
5. **Logs.** `search_datadog_logs`, `query: "*"`, `from: now-24h`, `limit: 1`. Then the same with `storage_tier: "flex_and_indexes"`. Zero from both means logs are not searchable in Datadog.
6. **APM.** `search_datadog_metrics`, `name_filter: "trace hits errors duration"`, `include_top_tag_keys: true`. For at most 4 `trace.*.hits` metrics found, scalar `sum:<metric>{*} by {service,env}.as_count()` over `now-24h`. That list is the APM-covered services.
7. **Synthetics.** `get_synthetics_tests` `mode: "configs"`, `summary: true` (page with `pagination_index` until empty or 5 pages). Count tests by type and state; list the main domains. Then `get_datadog_metric_context` for `synthetics.test_runs` with `include_tag_values: true`, and record the tag key that holds pass/fail and the one that holds the test name.
8. **Monitors.** `search_datadog_monitors`, `query: "*"`, `include_tags: ["env*","service*","team*","priority*"]`, `max_tokens: 6000`. Summarise: approximate count by type and priority, which tag keys monitors use for scope, and the notification handles you see (`@slack-...`, `@pagerduty-...`). Do not quote messages.
9. **SLOs.** `search_datadog_slos`, `page_size: 50`: count and naming pattern.
10. **Incidents.** `search_datadog_incidents`, `from: now-30d`, `with_facets: true`.
11. **Events.** `aggregate_events`, `query: "*"`, `from: now-7d`, count grouped by `["source"]`. This shows whether `change_tracking` and `alert` events exist.

## Output

Finish with one markdown report (use `display_markdown_report` if this is a scheduled run). It must contain:

1. **Findings**: a short narrative of what Datadog covers, with the evidence behind each claim.
2. **Contradictions**: anything that disagrees with the current assumptions above (e.g. an `env` tag that spans environments).
3. **Estate profile values**: a fenced block of `NAME: value` lines for `DD_SCOPE_FILTER`, `DATADOG_BLIND_SPOTS`, `DD_TOOLSETS`, `DD_PRODUCTS_TABLE` (a markdown table: product | in use | scope | notes), `DD_APM_SERVICES`, `DD_SYNTHETICS_SUMMARY`, `DD_VM_FLEET`, `DD_MONITOR_CONVENTIONS`, `DD_CALIBRATED_AT` (today's date). The workspace admin pastes these into the "Datadog estate profile" note.
4. **Questions for a human**: facts the tools cannot establish (for example which `env` value is production, or whether a monitor's team tag is authoritative).
