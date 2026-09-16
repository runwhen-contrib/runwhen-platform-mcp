Produce the weekly **Datadog monitor hygiene report** for {{DD_ENV_LABEL}}: which monitors wake people up without reason, which ones stopped watching anything, and which gaps matter. The audience is the team lead who tunes alerting. The goal is fewer, better pages.

## Ground rules for this run

- Read-only Datadog tools through `mcp_call` on server `{{MCP_SERVER_NAME}}` are allowed and **required**, including when scheduled. No tool that requires approval.
- Read "Datadog estate profile" and "Datadog MCP operating guide" first. Scope with {{DD_SCOPE_FILTER}}. Window: `from: now-7d`, `to: now`.
- Budget: at most 10 Datadog calls, `max_tokens` ≤ 6000 each.

## Call plan

1. **Transition volume.** `aggregate_events`, `query: "source:alert"` plus scope tags, count grouped by `["@monitor_id","status"]`, `limit: 100`.
2. **Noisiest by day.** `aggregate_events` with the same query, count grouped by `["@monitor_id"]` with `group_by.interval: 86400000`, `limit: 20`. Shows whether noise is constant or concentrated in bursts.
3. **Names and config for the top 15 noisy monitors.** `search_datadog_monitors`, `query: "id:(<ids>)"`, `include_tags: ["team*","service*","env*"]`. Keep name, type, query, priority, and whether the message has a notification handle (`@...`). Do not quote messages.
4. **Stuck and silent.** `monitor_groups_search`, `query: "-group_status:ok"` plus scope tags, `per_page: 50`. Use `counts` for the No Data total and keep groups whose status is `No Data`. Then `search_datadog_monitors`, `query: "muted:true"` plus scope tags.
5. **Long-running alerts.** From step 1, monitors with `error` or `warn` transitions but no `ok` in 7 days. Confirm their current state with `monitor_groups_search` (`monitor_id`, `query: "group_status:alert"`) for the top 5.
6. **Synthetics hygiene.** `get_synthetics_tests` `mode: "configs"`, `test_state: "paused"`; and `test_status: "No Data"`.
7. **SLOs at risk.** `search_datadog_slos` with the scope tags.

## Classify each monitor you report as exactly one of

- **Flapping**: ≥ 10 alert/recover pairs in 7 days. Suggest a longer evaluation window, recovery threshold, or `last_Xm` change.
- **Always on**: alerting for most of the week with no recovery. Either a real unfixed problem (escalate) or a wrong threshold (retune).
- **Silent**: `No Data` for more than a day. The thing it watches is gone or renamed.
- **Muted forever**: muted with no end. Delete, fix, or schedule a downtime instead.
- **Unrouted**: priority set but no notification handle, or a P1/P2 routed nowhere.
- **Paused / No Data synthetic**.

## Output

`display_markdown_report`, under ~600 words:

```
# Datadog monitor hygiene: week of <DD Mon>

**Summary:** <N> transitions this week (<+/-%> vs typical, if known) · <n> monitors cause <x%> of all transitions · <n> silent · <n> muted forever.

## Top offenders
| Monitor | Class | Transitions (7d) | Priority | Recommendation |
<up to 10 rows, noisiest first>

## Silent and muted
<one line each: name · since when (if known) · recommendation>

## Synthetics and SLOs
<paused / no-data tests · SLOs breached or low on budget>

## One change that would cut the most noise
<the single highest-leverage fix, with the number it would remove>
```

Recommend; do not claim to have changed anything. Monitors are read-only here.
