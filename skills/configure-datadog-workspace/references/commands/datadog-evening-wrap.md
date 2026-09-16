Produce the **{{DD_ENV_LABEL}} evening wrap** from Datadog: how the day went, what changed, and what the overnight on-call should keep an eye on. The audience is the team signing off and whoever is on call tonight.

## Ground rules for this run

- Read-only Datadog tools through `mcp_call` on server `{{MCP_SERVER_NAME}}` are allowed and **required** in this run, including when it is scheduled. Never call a tool that requires approval (e.g. `execute_code`).
- Read the knowledge notes "Datadog estate profile" and "Datadog MCP operating guide" first. Skip products the profile lists under blind spots. Do not load Datadog skill guides.
- Scope every query with {{DD_SCOPE_FILTER}}. Window: `from: now-10h`, `to: now` (the working day). Baseline: the same window one week earlier.
- Budget: at most 12 Datadog calls, each with `max_tokens` ≤ 6000. Aggregate first; drill into the top 3 only.
- If a tool is missing from `/mcp/{{MCP_SERVER_NAME}}/tools/`, skip that section and list it under Coverage.

## Call plan

1. **Day's alert activity.** `aggregate_events`, `query: "source:alert"` plus scope tags, count grouped by `["@monitor_id","status"]`, `limit: 40`. Then the same over the baseline window (`from: now-178h`, `to: now-168h`) for the comparison total.
2. **Still open going into the night.** `search_datadog_monitors`, `query: "-status:ok muted:false"` plus scope tags, `include_tags: ["env*","service*","team*","kube_*","host*"]`. Mark which ones were already open this morning (they had `error`/`warn` events before the window, or no `ok` event today).
3. **Names for the busiest monitors.** Resolve monitor ids from step 1 that are not already named in step 2 with one `search_datadog_monitors` call, `query: "id:(<id1> OR <id2> ...)"`.
4. **Incidents today.** `search_datadog_incidents`, `from: now-10h`, plus `query: "state:(active OR stable)"`.
5. **Changes shipped today.** `search_datadog_events`, `query: "source:change_tracking"` plus scope tags, `max_tokens: 4000`.
6. **Did a change hurt?** For up to 3 APM-covered services that deployed today (estate profile lists trace metrics): `get_change_stories` for the service over the window, and a scalar comparison of error count and p95 duration for the 2 hours before vs after the deploy time.
7. **SLOs.** `search_datadog_slos` with the scope tags. Report SLOs whose status is breached or whose remaining error budget is under 25%, if the response includes budget.
8. **Synthetics today.** `get_synthetics_tests` `mode: "configs"`, `test_status: "Alert"`; and the scalar `synthetics.test_runs` pass rate for the day vs the baseline (tag key from the estate profile).
9. **Capacity trend.** Scalar `max` of `system.disk.in_use{<scope>} by {host,device}` with `top(query0, 5, 'max', 'desc')`, and the same with `query0 - week_before(query0)` to find the fastest growers.

## Output

Deliver with `display_markdown_report` in exactly this shape, under ~500 words:

```
# {{DD_ENV_LABEL}} evening wrap: <Weekday, DD Mon>

**Day in one line:** <e.g. "Busier than last Tuesday (41 vs 18 alert transitions), no incidents, one synthetic still failing.">

## Open for tonight
<Everything still alerting or failing that on-call may be paged for, with scope, how long it has been open, and the first thing to check. "Nothing open." if empty.>

## What happened today
<Top monitors by transitions (name, count, flapping or not) · incidents · notable recoveries.>

## Changes
<Deploys and changes, and for covered services the before → after error and latency numbers. Say plainly when there was no measurable impact.>

## Reliability
<SLOs at risk · synthetic pass rate vs last week · failing tests.>

## Watch list for tomorrow
<Up to 3 slow-moving risks: disks filling, growing restarts, flapping monitors that need tuning. Each with the number that makes it a risk.>

## Coverage
<Blind spots and anything skipped.>
```

Style: plain and specific; no filler; correlation is not causation. Keep {{WORKSPACE_ENV}} resources out unless the environment map links them, and label them if so.
