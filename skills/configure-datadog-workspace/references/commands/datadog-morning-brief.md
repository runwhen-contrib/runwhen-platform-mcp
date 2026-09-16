Produce the **{{DD_ENV_LABEL}} morning brief** from Datadog: what happened overnight and what needs a person this morning. The audience is the engineering team reading it at the start of the day. They should know within 30 seconds whether they need to act.

## Ground rules for this run

- Read-only Datadog tools through `mcp_call` on server `{{MCP_SERVER_NAME}}` are allowed and **required** in this run, including when it is scheduled. Never call a tool that requires approval (e.g. `execute_code`).
- Read the knowledge notes "Datadog estate profile" and "Datadog MCP operating guide" first. Skip every product the profile lists under blind spots. Do not load Datadog skill guides; the query shapes below are enough.
- Scope every query with {{DD_SCOPE_FILTER}}. Window: `from: now-14h`, `to: now` (since the evening wrap). Baseline: same window one week earlier.
- Budget: at most 14 Datadog calls. Always set `max_tokens` (≤ 6000). Aggregate first; open raw events or test results only for the top 3 items.
- If a tool is missing from `/mcp/{{MCP_SERVER_NAME}}/tools/`, skip that section and list it under Coverage.

## Call plan

1. **Alerting now.** `monitor_groups_search`, `query: "-group_status:ok"` plus the scope tags, `per_page: 30`. Its `counts` give totals by status (Alert / Warn / No Data); `groups` lists the unhealthy groups. Then `search_datadog_monitors`, `query: "-status:ok muted:false"` plus the scope tags, `include_tags: ["env*","service*","team*","kube_*","host*"]`, `max_tokens: 6000`. Keep id, name, status, priority, type and scope. Do not quote monitor messages.
2. **Which groups.** For the top 5 Alert/Warn monitors (priority first) not already explained by step 1's groups: `monitor_groups_search` with `monitor_id` and `query: "group_status:(alert OR warn)"`. Report a large No Data count as one line, not per group.
3. **Overnight transitions.** `aggregate_events`, `query: "source:alert"` plus scope tags, `computes: [{field:"*", aggregation:"count", output:"n", sort:"desc"}]`, `group_by: {fields:["@monitor_id","status"], limit: 40}`. From this derive: fired and recovered (has `error`/`warn` and `ok`), still open, flapping (≥ 6 transitions).
4. **Incidents.** `search_datadog_incidents`, `query: "state:(active OR stable)"`; and `from: now-14h` with the default query to catch resolved ones.
5. **Changes.** `search_datadog_events`, `query: "source:change_tracking"` plus scope tags, `max_tokens: 4000`. Keep service, version and time only.
6. **Synthetics failing now.** `get_synthetics_tests`, `mode: "configs"`, `test_status: "Alert"`, `summary: true`.
7. **Synthetic failures overnight.** If step 6 found tests, `get_synthetics_tests`, `mode: "results"`, `public_ids: [<up to 10>]`, `result_status: "failed"`, `lookback_window: 840`. Record the failing assertion or step and the locations.
8. **Synthetic pass rate.** `get_datadog_metric`, `response_format: "scalar"`, a `sum` of `synthetics.test_runs` grouped by the result/status tag key recorded in the estate profile, plus the `week_before()` of the same. Skip if the profile has no tag key.
9. **Agents that stopped reporting.** Two scalar queries of `avg:datadog.agent.running{<scope>} by {host}` with `aggregator: "last"`: one over `now-15m`, one over `now-24h`. Hosts present in the second and missing from the first stopped reporting.
10. **Host pressure.** `get_datadog_metric`, scalar `max` over the window, `formulas: ["top(query0, 5, 'max', 'desc')"]` for `avg:system.cpu.user{<scope>} by {host}`, `max:system.disk.in_use{<scope>} by {host,device}`, and `min:system.mem.pct_usable{<scope>} by {host}` (use `'min','asc'` for that one). Report only hosts over 85% CPU, 85% disk, or under 10% usable memory.
11. **Processes on VMs.** Only if the estate profile lists process metrics: scalar `p95` of `datadog.process.per_command.cpu.total_pct` and `max` of `datadog.process.per_command.memory.rss` `by {command}`, with `top(..., 5, ...)` and the same `week_before()` comparison. Report only commands that grew more than 30% or are new.
12. **APM services.** For the trace metrics listed in the estate profile only: scalar `sum` of hits and errors `by {service}` using `.as_count()`, and `p95` of duration, each with `week_before()`. Report services whose error rate rose by more than 1 percentage point or whose p95 rose more than 25%.
13. **Kubernetes (if in scope).** Scalar `sum:kubernetes.containers.restarts{<scope>} by {kube_cluster_name,kube_namespace,kube_deployment}` with `top(query0 - week_before(query0), 5, 'max', 'desc')`, and `max:kubernetes.containers.state.terminated{reason:oomkilled,<scope>} by {kube_namespace,kube_deployment}`.

## Output

Deliver with `display_markdown_report` in exactly this shape. Keep the whole report under ~600 words. Every number carries its window. Link Datadog explorer URLs from tool responses where they help.

```
# {{DD_ENV_LABEL}} morning brief: <Weekday, DD Mon>

**Bottom line:** <QUIET | WATCH | ACT>: <one sentence a manager could forward>

## Needs attention now
<Ranked, P1 first. Each: what is wrong · scope (monitor group / host / test / service) · since when · why it matters · suggested next step. "Nothing needs attention." if empty.>

## Overnight
<Fired and recovered (count + the notable ones) · flapping monitors · incidents opened or resolved · deploys and changes, and whether anything broke after them.>

## Synthetics
<Pass rate vs last week · failing tests with the failing step/assertion and locations · tests that recovered.>

## Infrastructure and VMs
<Agents that stopped reporting · hosts over thresholds · process growth. One line each.>

## Services (APM-covered only)
<Regressions only, with the before → after numbers. "No regressions in covered services." if none.>

## Coverage
<What this brief could not see: blind spots from the estate profile, skipped sections, tools unavailable, truncated results.>
```

Style: plain, specific, no filler, no meta-narration. Do not speculate about root cause beyond what the data shows. Say "correlates with the 02:10 deploy of X", not "caused by". Never describe {{WORKSPACE_ENV}} resources in this report unless the environment map links them, and label them if you do.
