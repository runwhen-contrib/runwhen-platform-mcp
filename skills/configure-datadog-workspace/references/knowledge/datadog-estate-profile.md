# Datadog estate profile

What this workspace's Datadog account actually contains. Last calibrated: {{DD_CALIBRATED_AT}} (via `/datadog-calibrate`). Treat anything not listed here as unknown, not absent. Check with a cheap query before relying on it.

## Scope

- Datadog MCP server covers: **{{DD_ENV_LABEL}}**. Scope every query with: {{DD_SCOPE_FILTER}}
- This workspace's discovered resources are **{{WORKSPACE_ENV}}**. See "Datadog environment map" notes for counterparts.
- Registered toolsets: {{DD_TOOLSETS}}

## Blind spots (do not query these)

{{DATADOG_BLIND_SPOTS}}

## Products in use

{{DD_PRODUCTS_TABLE}}

## APM coverage

Coverage is partial. Only these services emit trace metrics. A service missing from this list is uninstrumented, not healthy.

{{DD_APM_SERVICES}}

## Synthetic tests

{{DD_SYNTHETICS_SUMMARY}}

## VM fleet and process monitoring

{{DD_VM_FLEET}}

## Monitor conventions

{{DD_MONITOR_CONVENTIONS}}
