**Datadog MCP server.**

- Find it with `ws_ls /mcp/`: the server whose tools include `search_datadog_monitors`, or whose `endpoint_url` (`ws_cat /mcp/<name>`) is a Datadog host. If there is none, ignore this rule.
- Before the first Datadog call in a conversation, `ws_cat /.runwhen/knowledge/datadog-mcp-operating-guide`. Read `/.runwhen/knowledge/datadog-runwhen-naming` before translating any resource name.
- `ws_cat` a tool's schema before first using it. Pass only schema fields; most tools require `telemetry: {intent}`.
- Datadog may cover a different environment from this workspace's resources. Never assume a workspace resource exists in Datadog under the same name, and never present Datadog data as the state of a workspace resource unless a lookup ties them. Label Datadog findings as Datadog, with their cluster or project.
- Resolve names before filtering; never guess. Kubernetes keys are `kube_cluster_name`, `kube_namespace`, `kube_deployment`, `pod_name`, `kube_container_name`. Do not trust `env` alone.
- Zero results means a wrong filter until proven otherwise. Confirm the tag exists, and that the product holds data at all (logs and APM are often absent), before saying data is missing.
- Prefer aggregates, `response_format: scalar` and `top()`. Fetch raw events, spans or logs only for the top few items.
