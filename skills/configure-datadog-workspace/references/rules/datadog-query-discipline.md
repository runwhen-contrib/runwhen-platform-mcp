**Working with the Datadog MCP server.**

- Before the first Datadog call in a conversation, read the knowledge note "Datadog MCP operating guide". Read "Datadog and RunWhen naming" before translating any resource name.
- `ws_cat /mcp/{{MCP_SERVER_NAME}}/tools/<tool>` before first using a tool. If that path is gone, `ws_ls /mcp/` and use the Datadog-hosted server under its current name; if there is none, ignore this rule. Pass only schema fields; most tools require `telemetry: {intent}`.
- Resolve names before filtering: discover real tag values, never guess. Kubernetes keys are `kube_cluster_name`, `kube_namespace`, `kube_deployment`, `pod_name`, `kube_container_name`, not `cluster`/`namespace`/`container`.
- Zero results means a wrong filter until proven otherwise. Confirm the tag exists before saying data is missing.
- {{DATADOG_BLIND_SPOTS}}
- Prefer aggregates, `response_format: scalar` and `top()`. Fetch raw events, spans or logs only for the top few items.
