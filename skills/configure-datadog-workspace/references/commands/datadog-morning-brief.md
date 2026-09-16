Produce the **Datadog morning brief**: what happened overnight in the connected Datadog account and what needs a person this morning. The audience is the engineering team at the start of the day. They should know within 30 seconds whether they need to act.

## Ground rules for this run

- Read-only tools on the Datadog MCP server, called through `mcp_call`, are allowed and **required** in this run, including when it is scheduled. Find the server with `ws_ls /mcp/` (its tools include `search_datadog_monitors`). Never call a tool that requires approval (e.g. `execute_code`).
- Read the knowledge note first: `ws_cat /.runwhen/knowledge/datadog-mcp-operating-guide`. Do not load Datadog skill guides; the query shapes below are enough.
- The report covers the whole connected Datadog account. Datadog may watch a different environment from this workspace's resources, so group findings by `kube_cluster_name`, `project` or `env` and never present them as the state of a workspace resource.
- Window: `from: now-14h`, `to: now` (since the evening wrap). Baseline: the same window one week earlier.
- Budget: at most 16 Datadog calls. Always set `max_tokens` (≤ 6000). Aggregate first; open raw events or test results only for the top 3 items.
- If a tool is missing from the server, skip that section and list it under Coverage.

## Call plan

0. **What exists.** `search_datadog_metrics`, `name_filter: "trace synthetics datadog.process"`, `include_top_tag_keys: true`. Sections 7, 10 and 11 run only for the metric families this returns.
1. **Alerting now.** `monitor_groups_search`, `query: "-group_status:ok"`, `per_page: 30`. Its `counts` give totals by status (Alert / Warn / No Data); `groups` lists the unhealthy groups. Then `search_datadog_monitors`, `query: "-status:ok muted:false"`, `include_tags: ["env*","service*","team*","kube_*","host*"]`, `max_tokens: 6000`. Keep id, name, status, priority, type and scope. Do not quote monitor messages.
2. **Which groups.** For the top 5 Alert/Warn monitors (priority first) not already explained by step 1's groups: `monitor_groups_search` with `monitor_id` and `query: "group_status:(alert OR warn)"`. Report a large No Data count as one line, not per group.
3. **Overnight transitions.** `aggregate_events`, `query: "source:alert"`, `computes: [{field:"*", aggregation:"count", output:"n", sort:"desc"}]`, `group_by: {fields:["@monitor_id","status"], limit: 40}`. Derive: fired and recovered (has `error`/`warn` and `ok`), still open, flapping (≥ 6 transitions).
4. **Incidents.** `search_datadog_incidents`, `query: "state:(active OR stable)"`; and `from: now-14h` with the default query to catch resolved ones.
5. **Changes.** `search_datadog_events`, `query: "source:change_tracking"`, `max_tokens: 4000`. Keep service, version and time only.
6. **Synthetics.** `get_synthetics_tests`, `mode: "configs"`, `test_status: "Alert"`, `summary: true`. If it finds tests: `get_synthetics_tests`, `mode: "results"`, `public_ids: [<up to 10>]`, `result_status: "failed"`, `lookback_window: 840`, and record the failing assertion or step and the locations.
7. **Synthetic pass rate** (only if `synthetics.test_runs` exists). `get_datadog_metric_context` on it to find the tag key holding pass/fail, then a scalar `sum` of `synthetics.test_runs` grouped by that key, with `week_before()` of the same.
8. **Agents that stopped reporting.** Two scalar queries of `avg:datadog.agent.running{*} by {host}` with `aggregator: "last"`: one over `now-15m`, one over `now-24h`. Hosts present in the second and missing from the first stopped reporting.
9. **Host pressure.** Scalar, `formulas: ["top(query0, 5, 'max', 'desc')"]`, for `avg:system.cpu.user{*} by {host}`, `max:system.disk.in_use{*} by {host,device}`, and `min:system.mem.pct_usable{*} by {host}` (use `'min','asc'` there). Report only hosts over 85% CPU, 85% disk, or under 10% usable memory.
10. **Processes** (only if `datadog.process.*` exists). Scalar `p95` of `datadog.process.per_command.cpu.total_pct` and `max` of `datadog.process.per_command.memory.rss` `by {command}`, with `top(..., 5, ...)` and a `week_before()` comparison. Report only commands that grew more than 30% or are new.
11. **APM** (only for the `trace.*` metrics found in step 0). Scalar `sum` of hits and errors `by {service}` using `.as_count()`, and `p95` of duration, each with `week_before()`. Report services whose error rate rose by more than 1 percentage point or whose p95 rose more than 25%. Services without trace metrics are unmeasured, not healthy.
12. **Kubernetes** (if the account has clusters). Scalar `sum:kubernetes.containers.restarts{*} by {kube_cluster_name,kube_namespace,kube_deployment}` with `top(query0 - week_before(query0), 5, 'max', 'desc')`, and `max:kubernetes.containers.state.terminated{reason:oomkilled} by {kube_cluster_name,kube_namespace,kube_deployment}`.

## Output

Deliver with `display_markdown_report` in exactly this shape, under ~600 words. Every number carries its window. Link Datadog explorer URLs from tool responses where they help.

```
# Datadog morning brief: <Weekday, DD Mon>

**Bottom line:** <QUIET | WATCH | ACT>: <one sentence a manager could forward>

## Needs attention now
<Ranked, P1 first. Each: what is wrong · scope (cluster / project / monitor group / host / test / service) · since when · why it matters · suggested next step. "Nothing needs attention." if empty.>

## Overnight
<Fired and recovered (count + the notable ones) · flapping monitors · incidents opened or resolved · deploys and changes, and whether anything broke after them.>

## Synthetics
<Pass rate vs last week · failing tests with the failing step/assertion and locations · tests that recovered.>

## Infrastructure
<Agents that stopped reporting · hosts over thresholds · process growth. One line each.>

## Services
<APM regressions only, with before → after numbers. "No regressions in instrumented services." if none.>

## Coverage
<What this brief could not see: products with no data in this account (e.g. no logs, no APM), skipped sections, tools unavailable, truncated results.>
```

Style: plain, specific, no filler, no meta-narration. Do not speculate about root cause beyond what the data shows. Say "correlates with the 02:10 deploy of X", not "caused by".
