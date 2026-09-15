# Datadog environment map: {{ENV_MAP_SCOPE}}

This note is attached to {{WORKSPACE_ENV}} resources under {{ENV_MAP_SCOPE}}. Datadog does **not** monitor these resources. Datadog covers {{DD_ENV_LABEL}}.

When a question about a resource here needs Datadog data (monitors, synthetics, APM, host or process metrics), use its {{DD_ENV_LABEL}} counterpart and say that you did:

- Datadog scope for the counterpart: {{ENV_MAP_COUNTERPART}}
- Name substitutions from this estate to Datadog:

{{ENV_MAP_SUBSTITUTIONS}}

- No Datadog counterpart exists for: {{ENV_MAP_NOT_IN_DATADOG}}

Confirm every substituted name with a live lookup (recipe A or C in "Datadog and RunWhen naming") before filtering on it. If the lookup returns several candidates, list them. Never treat {{DD_ENV_LABEL}} data as the state of the {{WORKSPACE_ENV}} resource.
