# Datadog and RunWhen naming

RunWhen names come from discovery and generation rules. Datadog names come from the Datadog Agent's autodiscovery, the customer's unified service tags, and cloud integration crawlers. The same object usually has different names in the two systems, and the two systems often cover different environments. Resolve first, then filter.

## 1. Kubernetes / GKE

| RunWhen (SLX tags and resource path) | Datadog tag | Matches as-is? |
|---|---|---|
| `cluster`; path `kubernetes/<cluster>/...`. Form depends on how the cluster was onboarded: short GKE name (`shared-cluster-cluster`), `<project>--<cluster>` when two projects share a cluster name, or the raw gcloud context `gke_<project>_<location>_<cluster>` from a supplied kubeconfig | `kube_cluster_name` (also `cluster_name`): always the short GKE name | Strip `gke_<project>_<location>_` and `<project>--` first. Hand-authored SLXs may use aliases (e.g. `beta-platform-cluster-01` for `platform-cluster-01`); trust the discovered path, not the alias. |
| `namespace`; path `kubernetes/<cluster>/<namespace>/...` | `kube_namespace` | Yes |
| `resource_name` with `resource_type: deployment` | `kube_deployment` | Yes |
| `resource_type: statefulset` / `daemonset` / `cronjob` / `job` | `kube_stateful_set` / `kube_daemon_set` / `kube_cronjob` / `kube_job` | Yes |
| `resource_type: service` | `kube_service` | Yes |
| `resource_type: ingress` | `kubernetes_state.ingress.*` metrics; check the tag key with recipe G | Unverified |
| pods (not indexed as SLXs) | `pod_name` (instance name with hash suffix) | n/a: use workload tags |
| containers | `kube_container_name`, `short_image`, `image_name`, `image_tag` | n/a |
| label `[k8s]app.kubernetes.io/name` / `instance` / `component` / `version` / `part-of` / `managed-by` | `kube_app_name` / `kube_app_instance` / `kube_app_component` / `kube_app_version` / `kube_app_part_of` / `kube_app_managed_by` | Yes: the Agent converts these recommended labels automatically |
| label `[k8s]tags.datadoghq.com/service` / `env` / `version` (present only if the customer sets them) | `service` / `env` / `version` | **Yes: the best bridge.** When a workload carries these labels, use their values directly. |
| label `[k8s]app` or other custom labels | not a tag unless the Agent is configured to map it | No |
| (no RunWhen equivalent) | `service`: from `tags.datadoghq.com/service` if set, otherwise usually the **image short name** on logs and APM, and `N/A` on `kubernetes_state.*` metrics | No |
| (no RunWhen equivalent) | `env`: from the pod label `tags.datadoghq.com/env` or the Agent's global tags, often one value per **cluster** | No |

Worked example: one platform component, three names. The workspace indexes `kubernetes/platform-cluster-01/backend-services/papi` and `kubernetes/shared-cluster-cluster/runwhen-env-staging/rw-staging-papi`. Datadog reports a third copy as `kube_deployment:rw-sdlc-papi`, `kube_namespace:runwhen-env-sdlc`, `service:backend-services` (image name), and tags every namespace on that cluster, staging included, `env:sdlc`. Guessing `service:papi`, `container:papi`, `namespace:...` or `service:rw-sdlc-papi` all return zero rows.

## 2. GCP

| RunWhen | Datadog | Notes |
|---|---|---|
| `project_id` / `gcp_project_id`; path `gcp/<project_id>/...` | `project_id` (GCP integration metrics); `project` on Agent hosts; `numeric_project_id` | Project **id**. RunWhen never records the numeric project number, so `numeric_project_id` cannot be bridged |
| GCP labels → `gcp_label_<key>` (dots and dashes become underscores) | the label as `<key>:<value>` on GCP integration metrics | Key spelling can differ (`_` vs `-`); check with recipe G |
| GCE network (firewall) tags | not recorded by RunWhen discovery | Cannot be bridged |
| `gcp_region` | `region` | GKE SLXs have been seen with `gcp_region: us` and `gcp_zone: us-west1`, so treat these two fields as unreliable |
| `gcp_zone` | `zone` (Agent hosts), `availability-zone` | |
| `resource_type: compute_instance`, `resource_name: <instance>` | `host: <instance>.<zone>.c.<project>.internal` (Agent on GCE); aliases include `<instance>.<project>`; `instance-id` | Resolve with host SQL (recipe C) |
| `resource_type: gcp_container_clusters`, `child_resource: <cluster>` | `kube_cluster_name`, `cluster_name` (Agent), `cluster_name` on `gcp.container.*` | Project-level SLXs put the real object in `child_resource`, not `resource_name` |
| `resource_type: gcp_storage_buckets`, `child_resource: <bucket>` | `bucket_name` on `gcp.storage.*` | Confirm with `get_datadog_metric_context` |
| Cloud SQL instance | `database_id: <project>:<instance>` on `gcp.cloudsql.*` | Confirm with `get_datadog_metric_context` |
| RunWhen tag `service` (`iam`, `gke`, `gcs`, `billing`, `compute_vm`) | **not** Datadog `service` | Same key, different meaning. Never copy it into a Datadog filter. |

## 3. Things that are never valid Datadog filters

- SLX names such as `oncall-test--pstgrsql-clstr-ndxng-jb-t-417` (vowels stripped, truncated, hashed).
- SLX aliases and task titles (human text).
- RunWhen resource paths as a single string (`kubernetes/<cluster>/<ns>/<name>`). Split them into `kube_cluster_name`, `kube_namespace`, and the workload tag.
- RunWhen `service`, `category`, `access`, `platform` tag values.
- `additionalContext.qualified_name`: it is not carried into search, so do not expect it on resources.

## 4. Resolution recipes (cheapest first, no logs needed)

**A. Kubernetes workload name or stem → Datadog identity**

`get_datadog_metric`, `response_format: scalar`, `from: now-1h`, query object `{name:"avail", query:"avg:kubernetes_state.deployment.replicas_available{kube_deployment:*<stem>*} by {kube_cluster_name,kube_namespace,kube_deployment,env}", aggregator:"last"}`. Every copy of the workload comes back with its real cluster, namespace and `env`. Use `kubernetes_state.statefulset.replicas_ready` with `kube_stateful_set` for StatefulSets.

**B. Workspace resource → its Datadog counterpart**

Datadog may watch a different environment from this workspace (for example, production while the workspace discovered staging), so the same service often has a differently named twin. Run recipe A on the name stem with no cluster filter and present every candidate with its `kube_cluster_name`, `kube_namespace` and `env`. Say which one you use and why (matching namespace pattern, the only production cluster). If the candidates are ambiguous, list them and ask; never pick one silently, and never describe the Datadog twin as the workspace resource itself.

**C. VM or instance name → Datadog host**

`search_datadog_hosts`: `SELECT hostname, hostname_aliases, tags->'project' AS project, tags->'zone' AS zone, agent_version FROM hosts WHERE hostname LIKE '<instance>%'`. No row means no Agent on that VM (or the host name differs; retry on `hostname_aliases`).

**D. Which Datadog service does a workload emit as?**

For APM: `search_datadog_entities` `query: "name:*<stem>*"`. For logs, if logs are indexed: `search_datadog_logs` with `query: "kube_deployment:<name>"`, `limit: 1`, `extra_fields: ["service"]`.

**E. Which monitors cover a thing?**

`search_datadog_monitors` with `query: "tag:\"kube_namespace:<ns>\""` or `title:<stem>`. Multi-alert monitors cover things by group, so also run `monitor_groups_search` with the relevant tag.

**F. Which synthetic tests hit an endpoint?**

`get_synthetics_tests` `mode: configs`, `domain: <host or *.domain>` (add `path` for HTTP tests).

**G. Which tag keys does a metric actually carry?**

`get_datadog_metric_context` `metric_name: <m>`, `include_tag_values: true`, optionally `tag_filter`. Do this before grouping by a key you have not seen on that metric.
