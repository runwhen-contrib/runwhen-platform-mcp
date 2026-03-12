---
name: configure-hierarchy
description: Set the hierarchy on an SLX to define UI grouping and path construction from tags. Use when committing an SLX, organizing SLXs into groups, or when the user asks about hierarchy, SLX grouping, additionalContext, or the map view.
---

# Configure Hierarchy

Sets `spec.additionalContext.hierarchy` on an SLX to define how it is grouped in the workspace UI and how legacy path construction works from tags.

## What hierarchy does

`hierarchy` is a **list of tag names** that defines the nesting order for an SLX. The platform reads each tag name in the hierarchy, looks up its value from `spec.tags`, and uses the values to build a grouping path.

Example: hierarchy `["cluster", "namespace", "resource_name"]` with tags `cluster=cluster-01`, `namespace=prod`, `resource_name=papi` produces grouping path `cluster-01/prod/papi`.

## How it relates to resourcePath

| Field | Purpose | Source |
|-------|---------|--------|
| `resourcePath` | Canonical search/indexing path (includes platform prefix) | Set directly as a string |
| `hierarchy` | UI grouping; legacy path construction (no platform prefix) | List of tag names, values resolved from tags |

Both live under `spec.additionalContext`. Always set **both** for full functionality. The `resourcePath` is the primary identifier for search; `hierarchy` controls the UI grouping.

## Format

A YAML list of tag names that exist in `spec.tags`:

```yaml
spec:
  tags:
  - name: resource_type
    value: platform
  - name: resource_name
    value: papi-issues
  additionalContext:
    resourcePath: runwhen/papi
    hierarchy:
    - resource_type
    - resource_name
```

## Common hierarchy patterns

### Kubernetes
```yaml
hierarchy:
- cluster
- namespace
- resource_name
```
Tags: `cluster=cluster-01`, `namespace=backend-services`, `resource_name=papi`

### AWS
```yaml
hierarchy:
- region
- service
- resource_name
```
Tags: `region=us-east-1`, `service=lambda`, `resource_name=my-function`

### Platform / internal tools
```yaml
hierarchy:
- resource_type
- resource_name
```
Tags: `resource_type=platform`, `resource_name=papi-issues`

### GitHub
```yaml
hierarchy:
- resource_type
- resource_name
```
Tags: `resource_type=github`, `resource_name=infra-flux-nonprod`

## How to set it

Pass `hierarchy` when calling `commit_slx`:

```python
commit_slx(
    slx_name="my-check",
    resource_path="kubernetes/cluster-01/prod-ns/my-app",
    hierarchy=["cluster", "namespace", "resource_name"],
    tags=[
        {"name": "cluster", "value": "cluster-01"},
        {"name": "namespace", "value": "prod-ns"},
        {"name": "resource_name", "value": "my-app"},
    ],
    # ... other params
)
```

## Rules

1. **Every entry in hierarchy must be a tag name** that exists in `spec.tags` — if a hierarchy key has no matching tag, that segment is skipped in path construction
2. **Order matters** — list from broadest to most specific (e.g. cluster → namespace → resource)
3. **Keep it short** — 2-4 levels is typical; deeply nested hierarchies add no value
4. **Match existing patterns** — check other SLXs in the workspace via `get_workspace_config_index` to stay consistent

## Important: commit reconciliation

After `commit_slx` succeeds, it takes time for the workspace config repo commit to reconcile through the system. The hierarchy grouping will not appear in the UI or search results immediately. Allow 1-3 minutes for the platform to process the commit and update its indexes.
