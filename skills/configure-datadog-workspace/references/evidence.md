# Evidence behind the Datadog workspace pack

Measured 2026-09-15 against a live Datadog MCP server (`https://mcp.datadoghq.com/v1/mcp`, US1) and RunWhen `main` (agentfarm `48ea2bd9`, 468-platform `9047113c`, runwhen-local `5238cd7`). Re-verify before relying on anything here long after that date.

## The failure this pack answers

A workspace chat session with Datadog MCP connected was asked to "check papi logs in datadog". It:

- guessed arguments and hit two schema errors (`sort: "desc"`, not `-timestamp`; `time_period`, not `from`/`to`) before reading the tool schema;
- queried `namespace:runwhen-env-sdlc container:papi`, `container:rw-sdlc-papi`, `pod:rw-sdlc-papi*`, `service:*papi*` and `service:rw-sdlc-papi`, all zero rows. Datadog's keys are `kube_namespace` / `kube_deployment` / `pod_name` / `kube_container_name`, and that workload's service is `backend-services` (the image name);
- concluded "PAPI does not ship its own logs to Datadog", which was false (`kube_deployment:rw-sdlc-papi` returns its logs);
- followed a workspace rule written for another log backend ("container label should be papi");
- never loaded Datadog's skill guides. Its registration did not expose `list_datadog_skills` / `load_datadog_skill` (105 tools listed, a strict subset of the 253 `toolsets=all` returns), although our own probes received them for every toolset setting.

## Datadog MCP server

| Fact | Measurement |
|---|---|
| Tool count by toolsets | none/`core`: 28 · `core,alerting,synthetics`: 39 · `core,alerting,synthetics,kubernetes`: 43 · `all`: 253 (~160k tokens of tool JSON) |
| Monitor groups / SLOs / synthetics | only with `alerting` / `alerting` / `synthetics` toolsets |
| `tools/list` pagination | single page for `all` |
| `-status:ok muted:false` | returns Alert + Warn + No Data monitors |
| `monitor_groups_search` `-group_status:ok` | `counts` facet by status/type/tag across all groups (14 Alert / 24 Warn / 203 No Data in one call) |
| `aggregate_events` `source:alert` by `@monitor_id,status` | works; one monitor showed 397 warn / 375 ok in 24h (flapping) |
| One `search_datadog_events` alert event | ~1.5k tokens |
| `get_datadog_metric` formulas | `week_before()` and `top()` work |
| Host that stopped reporting | absent from `by {host}` scalar output, not 0 |
| `datadog.process.per_command.cpu.total_pct` | distribution; indexed tags `command, env, integration, kube_cluster_name` (no `host`) |
| `get_synthetics_tests` results mode | requires `public_ids`; `lookback_window` in minutes, default 15 |
| Host naming on GCE | `hostname` = `<instance>.<zone>.c.<project>.internal`; aliases `<instance>.<project>`, `<instance>-<cluster>`; tags `project`, `zone`, `kube_cluster_name` |
| `env` tag reliability | seven RunWhen environments (sdlc, staging, test, lazarus, rdebug, dep-strip, airgap namespaces) on one cluster all carry `env:sdlc` |
| `service` on `kubernetes_state.*` metrics | `N/A` |

## RunWhen side

| Fact | Where |
|---|---|
| Rules injected on every model call, not conditional on MCP servers | agentfarm `plugins/chat_config_plugin.py` |
| Command body appended verbatim to the turn; cron evaluated in UTC; delivery via `display_markdown_report` | agentfarm `plugins/chat_config_plugin.py`, `webapp/scheduled_chat_service.py` |
| MCP approval decided only by the server's registered policy; no unattended guardrail for `mcp_call` | agentfarm `tools/orchestrator/mcp_call_tool.py` |
| Scheduled read-only suffix lists allowed tools without `mcp_call` | agentfarm `webapp/scheduled_chat_service.py` `_build_scheduled_suffix` |
| Global KB notes surface by title; resource-scoped notes auto-attach (≤3,200 chars); ancestor paths valid | agentfarm knowledge injection plugin; 468-platform `shared/services/sync/note_sync.py` |
| MCP `initialize.instructions`, `resources/*`, `prompts/*` not plumbed | 468-platform `papi/routers/v4/mcp_server.py` |
| No `env` tag on any of 655 SLXs in `oncall-test`; `cluster` short names; `[k8s]` label passthrough; GCP `child_resource` | `get_workspace_config_index(oncall-test)` |
| Cluster name form depends on onboarding (short, `<project>--<cluster>`, or `gke_<project>_<loc>_<cluster>`) | runwhen-local `src/gcp_utils.py`, `src/indexers/kubeapi.py` |
| No numeric project id; GCE network tags lost; `qualified_name` dropped by usearch | runwhen-local `src/indexers/gcpapi_normalizers.py`; usearch `usearch_worker/processors/utils.py` |
