Resolve the thing the user named (a RunWhen resource path, SLX, workload, VM, service or URL) to its Datadog identity, and show what Datadog knows about it. Use this before any Datadog investigation of a specific object.

## Steps

1. **Pin the RunWhen side.** If the input is a resource path, SLX or name from this workspace, look it up with `ws_search` / `ws_cat` and read its tags: `platform`, `cluster`, `namespace`, `resource_type`, `resource_name`, `child_resource`, `project_id`, `gcp_zone`, and any `[k8s]tags.datadoghq.com/*` or `[k8s]app.kubernetes.io/*` labels. Note any "Datadog environment map" note attached to the result.
2. **Pick the estate.** This workspace's resources are {{WORKSPACE_ENV}}; Datadog covers {{DD_ENV_LABEL}}. If the resource is {{WORKSPACE_ENV}}, you are resolving its **counterpart**: apply the environment map's substitutions and say so. If there is no map entry, resolve the name stem and present candidates.
3. **Resolve in Datadog** with the recipes in "Datadog and RunWhen naming", at most 5 calls, `max_tokens` ≤ 4000, scoped with {{DD_SCOPE_FILTER}}:
   - Kubernetes workload → recipe A (scalar `kubernetes_state.*` grouped by `kube_cluster_name,kube_namespace,<workload tag>,env`).
   - VM / compute instance → recipe C (host SQL on `hostname LIKE '<name>%'`).
   - Application / service name → recipe D (`search_datadog_entities`), plus a scalar check of the APM trace metrics listed in the estate profile, grouped `by {service}`.
   - URL or domain → recipe F (`get_synthetics_tests` `mode: configs`, `domain`).
4. **Coverage for the resolved identity** (2 calls max): monitors that reference it (`search_datadog_monitors` with `tag:"<key>:<value>"` or a title search) and, for Kubernetes, current `replicas_available` and 24h restarts.

## Output

An identity card, then coverage:

```
**<input>** → Datadog [{{DD_ENV_LABEL}}]
| Datadog tag | Value | How confirmed |
| kube_cluster_name / host / service / ... | ... | tool + query that returned it |
Confidence: exact | counterpart (via environment map) | candidate (name stem only)
Monitors: <n> (<names, status>) · Synthetics: <n> · APM: yes/no · Logs: <per estate profile>
Ready-to-use filter: `<tag:value tag:value>`
```

If nothing resolves, say which lookups ran and what they returned. Do not invent a filter.
