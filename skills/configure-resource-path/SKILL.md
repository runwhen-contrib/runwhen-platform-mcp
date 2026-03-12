---
name: configure-resource-path
description: Set the resourcePath on an SLX for workspace-chat indexing and search. Use when committing an SLX, configuring SLX metadata, or when the user asks about resourcePath, additionalContext, or SLX search/indexing.
---

# Configure Resource Path

Sets `spec.additionalContext.resourcePath` on an SLX so workspace-chat and usearch can discover and group tasks by infrastructure location.

## What resourcePath does

`resourcePath` is the **canonical identifier** for where an SLX's target resource lives. It controls:
- How workspace-chat finds and routes questions to relevant tasks
- How usearch indexes SLXs for keyword and semantic search
- How the UI groups SLXs by infrastructure location

## Format

A forward-slash-separated path, **platform prefix first**, then narrowing to the specific resource:

```
<platform>/<level-1>/<level-2>/.../<resource>
```

## Examples by platform

| Platform | resourcePath | Description |
|----------|-------------|-------------|
| Kubernetes | `kubernetes/cluster-01/namespace-a/papi` | A deployment in a namespace |
| Kubernetes | `kubernetes/cluster-01/kube-system` | A namespace |
| AWS | `aws/us-east-1/lambda/my-function` | A Lambda function |
| Azure | `azure/sub-123/rg-prod/vm-web-01` | A VM in a resource group |
| GitHub | `github` | GitHub-scoped tasks (repos, actions) |
| RunWhen | `runwhen/papi` | RunWhen platform API tasks |
| GCP | `gcp/project-x/gke/cluster-01` | A GKE cluster |

## How to set it

Pass `resource_path` when calling `commit_slx`:

```python
commit_slx(
    slx_name="my-check",
    resource_path="kubernetes/cluster-01/prod-ns/my-app",
    # ... other params
)
```

This generates in `slx.yaml`:

```yaml
spec:
  additionalContext:
    resourcePath: kubernetes/cluster-01/prod-ns/my-app
```

## Rules

1. **Always include a platform prefix** — `kubernetes/`, `aws/`, `github/`, `runwhen/`, etc.
2. **Use tag values as path segments** where possible — if you have a `cluster` tag with value `cluster-01`, the path should include `cluster-01`
3. **No trailing slashes** — the system normalizes them but keep paths clean
4. **Match existing patterns** — call `get_workspace_config_index` to see how other SLXs in the workspace define their resourcePath, and follow the same convention

## Important: commit reconciliation

After `commit_slx` succeeds, it takes time for the workspace config repo commit to reconcile through the system. The SLX will not appear in `get_workspace_config_index`, `search_workspace`, or workspace-chat results immediately. Allow 1-3 minutes for the platform to process the commit and update its indexes.
