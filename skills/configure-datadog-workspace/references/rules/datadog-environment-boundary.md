**Datadog sees {{DD_ENV_LABEL}}. This workspace's discovered resources are {{WORKSPACE_ENV}}.**

- RunWhen resources, SLXs, tasks and issues here describe {{WORKSPACE_ENV}}. The Datadog MCP server (`/mcp/{{MCP_SERVER_NAME}}/`) describes {{DD_ENV_LABEL}}. Different estates, different names: never assume a workspace resource exists in Datadog under the same name.
- Decide which estate the user means before any Datadog call. Monitors, synthetics, APM, "prod", customer impact → Datadog. A resource path, SLX, task or issue from this workspace → {{WORKSPACE_ENV}}. If it is ambiguous, state your assumption in one line and continue.
- Scope Datadog queries with {{DD_SCOPE_FILTER}}. Do not trust the `env` tag alone: it is whatever the Datadog agent was configured with.
- Label every Datadog finding with its estate, e.g. "[{{DD_ENV_LABEL}} · Datadog]". Only relate it to a {{WORKSPACE_ENV}} resource through the "Datadog environment map" knowledge note, and call it the counterpart, not the same thing.
- If `/mcp/{{MCP_SERVER_NAME}}/` does not exist, first check `ws_ls /mcp/` for a server whose endpoint is a Datadog host (it may have been renamed). If there is none, ignore this rule.
