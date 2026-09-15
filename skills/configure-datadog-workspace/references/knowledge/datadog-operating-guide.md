# Datadog MCP operating guide

How to use the Datadog MCP server (`/mcp/datadog/`) from this workspace. Environment scope is in the rule "datadog-environment-boundary". Name translation is in the note "Datadog and RunWhen naming". What this particular Datadog account does and does not contain is in "Datadog estate profile". Read the estate profile before assuming a product has data.

## 1. Datadog vocabulary

| Term | Meaning | How you reach it |
|---|---|---|
| Monitor | An alerting rule. States: `OK`, `Alert`, `Warn`, `No Data`. Priority `P1` (highest) to `P5`. Can be muted or under a downtime. | `search_datadog_monitors` |
| Monitor group | One row of a multi-alert monitor, e.g. `host:web-1` or `kube_namespace:payments`. A monitor is "Alert" if any group alerts. | `monitor_groups_search` (alerting toolset) |
| Alert event | Every monitor state transition is an event with `source:alert`, tag `@monitor_id`, and a `status` (`error`, `warn`, `ok`). This is the only record of what fired *and recovered* while nobody was watching. | `aggregate_events`, `search_datadog_events` |
| Incident | A declared incident, `SEV-1` to `SEV-5`, `state` active / stable / resolved. | `search_datadog_incidents`, `get_datadog_incident` |
| SLO | Service level objective with an error budget. | `search_datadog_slos` (alerting toolset) |
| Synthetic test | A scripted check run from Datadog locations: API (HTTP, SSL, DNS, TCP, gRPC), browser, or multistep. Identified by a `public_id` like `abc-123-xyz`. Config state `live` / `paused`; status `OK`, `Alert`, `No Data`. | `get_synthetics_tests` (synthetics toolset) |
| Unified service tags | `env`, `service`, `version`. Set by the customer (labels, env vars, agent config). Nothing guarantees they are correct or even present. | tags on every product |
| Tag vs attribute | Tags are `key:value` with no prefix (`kube_namespace:web`). Attributes on logs and spans take `@` (`@http.status_code:500`). Reserved fields (`service`, `status`, `host`, `env`) never take `@`. | query syntax |
| Host / Agent | A machine running the Datadog Agent (VM or Kubernetes node) or a host seen through a cloud integration. | `search_datadog_hosts` (SQL) |
| Live Processes | Process-level data from the Agent's process collection. There is no process tool in the MCP server; use the `datadog.process.*` metrics. | `get_datadog_metric` |
| APM service / resource / span / trace | A service is an instrumented app; a resource is an endpoint or query; spans make up traces. Trace **metrics** (`trace.<operation>.hits`, `.errors`, `.duration`) count 100% of traffic. **Indexed spans** are a retention-filtered sample. | `get_datadog_metric`, `aggregate_spans`, `search_datadog_spans` |
| Watchdog | Datadog's automatic anomaly detection. Surfaces as events and change stories. | `get_change_stories` |
| Change tracking | Deploys, feature flags, config and Kubernetes changes, as events with `source:change_tracking`. | `search_datadog_events` |
| Log index / Flex / archive | Only indexed logs are searchable by default; Flex and online archives need `storage_tier`. Many accounts deliberately do not index logs. | `search_datadog_logs`, `analyze_datadog_logs` |

## 2. Which tool for which question

| Question | Tool and first call | Notes |
|---|---|---|
| How much is unhealthy right now? | `monitor_groups_search` `query: "-group_status:ok"`, `per_page: 30` | The response's `counts` block totals every group by status (`Alert`, `Warn`, `No Data`), type and tag in one cheap call. |
| What is alerting right now? | `search_datadog_monitors` `query: "-status:ok muted:false"` | Catches Alert, Warn and No Data. Each monitor carries its full message (~500-800 tokens); set `max_tokens` and request only the tags you need via `include_tags`. |
| Which hosts/namespaces inside a monitor are alerting? | `monitor_groups_search` with `monitor_id` and `query: "group_status:(alert OR warn)"` | Pass `monitor_id` as its own parameter, never inside `query`. |
| Look up specific monitors | `search_datadog_monitors` `query: "id:(123 OR 456)"` | |
| What fired and recovered in a window? Which monitors flap? | `aggregate_events` `query: "source:alert"`, `group_by.fields: ["@monitor_id","status"]`, count | Verified field names. High paired `error`/`ok` counts for one monitor = flapping. |
| Details of one alert transition | `search_datadog_events` `query: "source:alert @monitor_id:<id>"` | Each event is ~1.5k tokens. Only after aggregating. |
| Are synthetic tests failing? | `get_synthetics_tests` `mode: configs`, `test_status: Alert` | Returns currently failing tests with `public_id`. |
| Synthetic failures over the last N hours | `get_synthetics_tests` `mode: results`, `public_ids: [...]`, `result_status: failed`, `lookback_window: <minutes>` | Results mode needs `public_ids`. `lookback_window` is in **minutes** (default 15). |
| Synthetic pass rate / latency trend | `get_datadog_metric` on `synthetics.test_runs` (count), `synthetics.http.response.time` (gauge) | Check tag keys first with `get_datadog_metric_context`; the estate profile records them. |
| Host inventory, agent versions, which VMs exist | `search_datadog_hosts` SQL, e.g. `SELECT hostname, tags->'project' AS project, tags->'zone' AS zone, agent_version FROM hosts WHERE hostname LIKE 'name%'` | DDSQL: repeat expressions in `GROUP BY`; no `->>`, `ANY()`, `current_timestamp`. |
| Is a host's agent still reporting? | `get_datadog_metric` scalar `avg:datadog.agent.running{<scope>} by {host}` over a short window, compared with a longer window | A host that stopped reporting is **missing** from the result, not `0`. |
| Host CPU / memory / disk | `get_datadog_metric` `system.cpu.user`, `system.mem.pct_usable`, `system.disk.in_use` `by {host}` | Use `top(query, N, 'max', 'desc')` in `formulas`. |
| Top processes on VMs | `get_datadog_metric` `datadog.process.per_command.cpu.total_pct`, `datadog.process.per_command.memory.rss` `by {command}` | Distribution metrics. Indexed tag keys are limited (e.g. `command`, `env`, `kube_cluster_name`; often **not** `host`). Check with `get_datadog_metric_context`. |
| APM golden signals for instrumented services | `get_datadog_metric` on the `trace.*` metrics listed in the estate profile, `by {service}` | Prefer trace metrics over `aggregate_spans` for rates and error counts; spans are sampled. |
| Latency percentiles or breakdown by endpoint | `aggregate_spans` `computes: [{field:"@duration", aggregation:"p95"}]`, `group_by.fields: ["resource_name"]` | `@duration` is nanoseconds. Sampled data; say so. |
| Kubernetes workload health in Datadog | `get_datadog_metric` `kubernetes_state.deployment.replicas_available`, `kubernetes.containers.restarts`, `kubernetes.containers.state.terminated{reason:oomkilled}` | Group by `kube_cluster_name,kube_namespace,kube_deployment`. |
| What changed? | `search_datadog_events` `query: "source:change_tracking"`; `get_change_stories` for an APM service | Kubernetes events are `source:kubernetes` and do not include deploys. |
| Active incidents | `search_datadog_incidents` `query: "state:(active OR stable)"` | Group same-field values: `severity:(SEV-1 OR SEV-2)`. |
| Which service owns / depends on X | `search_datadog_entities` `entity_type: service`, `query: "name:*stem*"` | Catalog data; may be sparse. |
| Dashboards someone already built | `search_datadog_dashboards`, `get_datadog_dashboard` | Reuse their queries: they encode the account's real tag names. |

Tools that require approval (for example `execute_code`) must never be used in scheduled runs: nobody is there to approve, and the run stalls until it times out. Available tools depend on the toolsets the server was registered with. If a tool in this table is missing from `ws_ls /mcp/datadog/tools/`, say so and use the next-best tool.

## 3. Datadog's own skill guides

Some registrations expose `list_datadog_skills` and `load_datadog_skill`. They are ordinary tools.

- Interactive investigation in a domain this guide does not cover (database monitoring, RUM, CI, Kafka, security): call `list_datadog_skills` with a `query`, then `load_datadog_skill` with the exact returned name. Never guess skill names.
- Useful names seen so far: `datadog/querying-patterns`, `datadog/metrics`, `datadog/incidents-and-alerting`, `datadog/services-and-infrastructure`, `datadog/change-tracking`, `datadog/ddsql` (required before `analyze_datadog_logs`).
- Scheduled digests: do not load skills. The command already carries the query shapes, and each skill costs 2-4k tokens.
- If the tools are absent, this guide is the reference.

## 4. Argument traps (each one has broken a real session)

- Time ranges are `from` / `to`, never `time_period`. Relative forms: `now-15m`, `now-24h`, `now-7d`. Events need the `now-` prefix. Synthetics results use `lookback_window` minutes.
- Log and span `sort` is `-timestamp` or `timestamp`, not `desc`.
- `get_datadog_metric` with `response_format: scalar` needs structured query objects with an `aggregator` (`avg`, `sum`, `min`, `max`, `last`). The `avg:` prefix inside the query is the *space* aggregator, not the time aggregator.
- Metric grouping comes before modifiers: `sum:x{a:b} by {c}.as_count()`.
- Inside `{}` do not mix comma-separated filters with boolean operators. Use `{(service:a OR service:b) AND kube_namespace:c}`.
- Same-field alternatives are grouped: `status:(alert OR warn)`, not `status:alert OR status:warn`.
- `search_datadog_logs` is for raw lines and patterns only; counts go through `analyze_datadog_logs` (DDSQL).
- Wildcards do not work inside quotes.
- Tag terms in `search_datadog_monitors` and `monitor_groups_search` match the **monitor's own tags**, not the group values it alerts on. A multi-alert monitor tagged only `integration:kubernetes` will not match `kube_cluster_name:prod`. If monitors are not tagged by environment (see the estate profile), search without the scope term and keep only groups whose group string contains the scope.

## 5. Keeping responses small

- One `search_datadog_events` item is ~1.5k tokens; one monitor ~500-800 tokens; a timeseries returns 20 bins per series. Aggregate first, then drill into the top 3-5.
- Use `response_format: scalar` for "current value" or "total over the window" questions. Use `formulas` like `top(query0, 10, 'max', 'desc')` and `query0 - week_before(query0)` to let Datadog do the ranking and comparison.
- Always set `max_tokens`. If a response says it was truncated, narrow the query before paging.

## 6. Reading results honestly

- No row for a host or group means it stopped reporting, not that the value is zero.
- `No Data` on a monitor is a finding in its own right (broken agent, renamed metric, decommissioned host).
- `service:N/A` on infrastructure metrics is normal. Unified service tags often exist only on APM data and logs.
- A tag being absent on a metric does not mean the object is absent. Metrics only carry their *indexed* tag keys (`get_datadog_metric_context` shows them).
- APM coverage is partial in most accounts. A service with no trace metrics is uninstrumented, not healthy.
- Every Datadog number you report carries its time window and scope filter.
