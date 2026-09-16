Resolve the thing the user named (a RunWhen resource path, SLX, workload, VM, service or URL) to its Datadog identity, and show what Datadog knows about it. Use this before any Datadog investigation of a specific object.

## Steps

1. **Pin the RunWhen side.** If the input is a resource path, SLX or name from this workspace, look it up with `ws_search` / `ws_cat` and read its tags: `platform`, `cluster`, `namespace`, `resource_type`, `resource_name`, `child_resource`, `project_id`, `gcp_zone`, and any `[k8s]tags.datadoghq.com/*` or `[k8s]app.kubernetes.io/*` labels.
2. **Find the Datadog server** with `ws_ls /mcp/` (its tools include `search_datadog_monitors`). Remember that Datadog may watch a different environment from this workspace: what you find may be a counterpart, not the same object.
3. **Resolve in Datadog** with the recipes in the knowledge note `datadog-runwhen-naming` (`ws_cat /.runwhen/knowledge/datadog-runwhen-naming`), at most 5 calls, `max_tokens` ≤ 4000:
   - Kubernetes workload → recipe A (scalar `kubernetes_state.*` grouped by `kube_cluster_name,kube_namespace,<workload tag>,env`), run on the name stem across all clusters.
   - VM / compute instance → recipe C (host SQL on `hostname LIKE '<name>%'`).
   - Application / service name → recipe D (`search_datadog_entities`), plus a scalar check of `trace.*` metrics grouped `by {service}`.
   - URL or domain → recipe F (`get_synthetics_tests` `mode: configs`, `domain`).
4. **Coverage for the resolved identity** (2 calls max): monitors that reference it (`search_datadog_monitors` with `tag:"<key>:<value>"` or a title search) and, for Kubernetes, current `replicas_available` and 24h restarts.

## Output

An identity card, then coverage:

```
**<input>** → Datadog
| Datadog tag | Value | How confirmed |
| kube_cluster_name / host / service / ... | ... | tool + query that returned it |
Match: exact | counterpart in another environment | candidates (name stem only)
Monitors: <n> (<names, status>) · Synthetics: <n> · APM: yes/no
Ready-to-use filter: `<tag:value tag:value>`
```

If several candidates come back, list them all with their cluster and namespace and say which one you would use and why. If nothing resolves, say which lookups ran and what they returned. Do not invent a filter.
