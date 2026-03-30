# commit_slx Examples

> **Location auto-resolves.** You can omit `location` — the server picks the
> best runner automatically (workspace runners preferred over public). Only
> specify `location` when the workspace has multiple workspace-type runners
> and you need to target a specific one.

## Basic task (read-only monitoring)

```python
commit_slx(
    slx_name="k8s-pod-health",
    alias="Pod Health Check",
    statement="All pods should be in Running state",
    script=my_script,
    task_title="Check Pod Health in Namespace",
    interpreter="bash",
    access="read-only",
    data="logs-bulk",
    resource_path="kubernetes/cluster-01/prod-ns",
    hierarchy=["cluster", "namespace", "resource_name"],
    tags=[
        {"name": "cluster", "value": "cluster-01"},
        {"name": "namespace", "value": "prod-ns"},
        {"name": "resource_name", "value": "pod-health"},
        {"name": "resource_type", "value": "kubernetes"},
    ],
    secret_vars={"kubeconfig": "kubeconfig"},
    env_vars={"NAMESPACE": "prod-ns", "CONTEXT": "cluster-01"},
)
```

## Task + custom SLI

```python
commit_slx(
    slx_name="db-replication-lag",
    alias="DB Replication Lag",
    statement="Replication lag should be under 30 seconds",
    script=task_script,
    task_title="Check Database Replication Lag",
    interpreter="python",
    access="read-only",
    data="logs-bulk",
    sli_script=sli_script,          # custom SLI alongside the task
    sli_interval_seconds=120,       # check every 2 minutes
    secret_vars={"kubeconfig": "kubeconfig"},
)
```

## Task + cron-scheduled SLI

```python
commit_slx(
    slx_name="daily-cert-check",
    alias="TLS Certificate Expiry",
    statement="All TLS certificates should have >30 days until expiry",
    script=task_script,
    task_title="Check TLS Certificate Expiry",
    interpreter="bash",
    access="read-only",
    data="config",
    cron_schedule="0 8 * * *",      # run daily at 8am
    secret_vars={"kubeconfig": "kubeconfig"},
)
```

## Remediation task (read-write)

```python
commit_slx(
    slx_name="restart-crashed-pods",
    alias="Restart CrashLoop Pods",
    statement="No pods should be in CrashLoopBackOff for more than 10 minutes",
    script=remediation_script,
    task_title="Restart CrashLoopBackOff Pods",
    interpreter="bash",
    access="read-write",            # modifies resources
    data="logs-bulk",
    secret_vars={"kubeconfig": "kubeconfig"},
)
```

## Explicit location override

Only use when the workspace has multiple workspace-type runners:

```python
commit_slx(
    slx_name="k8s-pod-health",
    alias="Pod Health Check",
    statement="All pods should be in Running state",
    script=my_script,
    task_title="Check Pod Health in Namespace",
    location="watcher-controlplane",  # explicit override
    interpreter="bash",
    access="read-only",
    data="logs-bulk",
    secret_vars={"kubeconfig": "kubeconfig"},
)
```
