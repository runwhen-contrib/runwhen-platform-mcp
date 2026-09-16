Produce the **Datadog evening wrap**: how the day went in the connected Datadog account, what changed, and what the overnight on-call should keep an eye on. The audience is the team signing off and whoever is on call tonight.

## Ground rules for this run

- Read-only tools on the Datadog MCP server, called through `mcp_call`, are allowed and **required** in this run, including when it is scheduled. Find the server with `ws_ls /mcp/` (its tools include `search_datadog_monitors`). Never call a tool that requires approval (e.g. `execute_code`).
- Read the knowledge note "Datadog MCP operating guide" first. Do not load Datadog skill guides.
- The report covers the whole connected Datadog account. Group findings by `kube_cluster_name`, `project` or `env`; never present them as the state of a workspace resource.
- Window: `from: now-10h`, `to: now` (the working day). Baseline: the same window one week earlier.
- Budget: at most 13 Datadog calls, each with `max_tokens` ≤ 6000. Aggregate first; drill into the top 3 only.
- If a tool is missing from the server, skip that section and list it under Coverage.

## Call plan

0. **What exists.** `search_datadog_metrics`, `name_filter: "trace synthetics"`. Steps 6 and 8 use only the families this returns.
1. **Day's alert activity.** `aggregate_events`, `query: "source:alert"`, count grouped by `["@monitor_id","status"]`, `limit: 40`. Then the same over the baseline window (`from: now-178h`, `to: now-168h`) for the comparison total.
2. **Still open going into the night.** `search_datadog_monitors`, `query: "-status:ok muted:false"`, `include_tags: ["env*","service*","team*","kube_*","host*"]`. Mark which ones were already open this morning (error/warn events before the window, or no `ok` event today).
3. **Names for the busiest monitors.** Resolve monitor ids from step 1 not already named in step 2 with one `search_datadog_monitors` call, `query: "id:(<id1> OR <id2> ...)"`.
4. **Incidents today.** `search_datadog_incidents`, `from: now-10h`, plus `query: "state:(active OR stable)"`.
5. **Changes shipped today.** `search_datadog_events`, `query: "source:change_tracking"`, `max_tokens: 4000`.
6. **Did a change hurt?** For up to 3 services that deployed today and have trace metrics: `get_change_stories` for the service over the window, and a scalar comparison of error count and p95 duration for the 2 hours before vs after the deploy.
7. **SLOs.** `search_datadog_slos`. Report SLOs breached or with under 25% error budget left, if the response includes budget.
8. **Synthetics today.** `get_synthetics_tests` `mode: "configs"`, `test_status: "Alert"`; and, if `synthetics.test_runs` exists, the day's pass rate vs the baseline (find the pass/fail tag key with `get_datadog_metric_context`).
9. **Capacity trend.** Scalar `max` of `system.disk.in_use{*} by {host,device}` with `top(query0, 5, 'max', 'desc')`, and the same with `query0 - week_before(query0)` for the fastest growers.

## Output

Deliver with `display_markdown_report` in exactly this shape, under ~500 words:

```
# Datadog evening wrap: <Weekday, DD Mon>

**Day in one line:** <e.g. "Busier than last Tuesday (41 vs 18 alert transitions), no incidents, one synthetic still failing.">

## Open for tonight
<Everything still alerting or failing that on-call may be paged for, with scope, how long it has been open, and the first thing to check. "Nothing open." if empty.>

## What happened today
<Top monitors by transitions (name, count, flapping or not) · incidents · notable recoveries.>

## Changes
<Deploys and changes, and for instrumented services the before → after error and latency numbers. Say plainly when there was no measurable impact.>

## Reliability
<SLOs at risk · synthetic pass rate vs last week · failing tests.>

## Watch list for tomorrow
<Up to 3 slow-moving risks: disks filling, growing restarts, flapping monitors that need tuning. Each with the number that makes it a risk.>

## Coverage
<Products with no data in this account, and anything skipped.>
```

Style: plain and specific; no filler; correlation is not causation.
