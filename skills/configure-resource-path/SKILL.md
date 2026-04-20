---
name: configure-resource-path
description: "Set the resourcePath on an SLX for workspace-chat indexing and search. Use when: (1) Committing an SLX and need to set its resource_path parameter, (2) Configuring SLX metadata for search and indexing, (3) The user asks about resourcePath, additionalContext, or how SLXs are found by workspace chat, or (4) Improving SLX discoverability in search results."
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

## CRITICAL: Custom tasks MUST use the `custom/` platform prefix

Tasks built via the MCP server (`commit_slx` or `deploy_registry_codebundle`) are **custom tasks** — they must **never** share a `resourcePath` with existing platform-managed resources. The MCP server enforces this automatically:

- **All resource paths are prefixed with `custom/`** — if you pass `kubernetes/cluster-01/ns`, the server rewrites it to `custom/kubernetes/cluster-01/ns`
- **If you already include `custom/`**, the path is left unchanged
- This prevents custom tasks from appearing under the same grouping as platform-discovered resources

### Why this matters

Platform-managed resources (discovered by runwhen-local, Crossplane, etc.) own their resource paths. If a custom task uses the same path, it pollutes the resource tree and creates confusing groupings in the UI and search results. The `custom/` prefix keeps MCP-authored tasks in their own namespace.

## Examples by platform

| Platform | resourcePath | Description |
|----------|-------------|-------------|
| **Custom (MCP-authored)** | `custom/kubernetes/cluster-01/namespace-a/papi` | Custom task targeting a K8s deployment |
| **Custom (MCP-authored)** | `custom/aws/us-east-1/lambda/my-function` | Custom task targeting a Lambda function |
| **Custom (MCP-authored)** | `custom/github` | Custom task for GitHub operations |
| **Custom (MCP-authored)** | `custom/runwhen/papi` | Custom task for RunWhen platform |
| **Custom (MCP-authored)** | `custom/gcp/project-x/gke/cluster-01` | Custom task targeting a GKE cluster |
| Platform-managed | `kubernetes/cluster-01/kube-system` | Discovered by runwhen-local (NOT for custom tasks) |

## How to set it

Pass `resource_path` when calling `commit_slx`. The `custom/` prefix is added automatically if omitted:

```python
commit_slx(
    slx_name="my-check",
    workspace_name="my-workspace",
    resource_path="custom/kubernetes/cluster-01/prod-ns/my-app",
    # ... other params
)
```

This generates in `slx.yaml`:

```yaml
spec:
  additionalContext:
    resourcePath: custom/kubernetes/cluster-01/prod-ns/my-app
```

> **Note:** Even if you pass `resource_path="kubernetes/cluster-01/prod-ns/my-app"` (without the prefix), the server will automatically rewrite it to `custom/kubernetes/cluster-01/prod-ns/my-app`.

## Rules

1. **Always use the `custom/` platform prefix** — this is enforced by the server; all MCP-authored tasks live under `custom/`
2. **After `custom/`, describe the target infrastructure** — e.g. `custom/kubernetes/cluster/namespace/resource`
3. **Use tag values as path segments** where possible — if you have a `cluster` tag with value `cluster-01`, the path should include `cluster-01`
4. **No trailing slashes** — the system normalizes them but keep paths clean
5. **Do NOT reuse existing resource paths** — never place a custom task under a path owned by platform-managed resources

## Important: commit reconciliation

After `commit_slx` succeeds, it takes time for the workspace config repo commit to reconcile through the system. The SLX will not appear in `get_workspace_config_index`, `search_workspace`, or workspace-chat results immediately. Allow 1-3 minutes for the platform to process the commit and update its indexes.
