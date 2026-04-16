---
name: configure-hierarchy
description: "Set the hierarchy on an SLX to define UI grouping and path construction from tags. Use when: (1) Committing an SLX and need to set its hierarchy parameter, (2) Organizing SLXs into groups for the map view, (3) The user asks about hierarchy, SLX grouping, additionalContext, or map view organization, or (4) Configuring how SLXs appear in the workspace UI tree."
---

# Configure Hierarchy

Sets `spec.additionalContext.hierarchy` on an SLX to define how it is grouped in the workspace UI and how legacy path construction works from tags.

## What hierarchy does

`hierarchy` is a **list of tag names** that defines the nesting order for an SLX. The platform reads each tag name in the hierarchy, looks up its value from `spec.tags`, and uses the values to build a grouping path.

Example: hierarchy `["cluster", "namespace", "resource_name"]` with tags `cluster=cluster-01`, `namespace=prod`, `resource_name=papi` produces grouping path `cluster-01/prod/papi`.

## How it relates to resourcePath

| Field | Purpose | Source |
|-------|---------|--------|
| `resourcePath` | Canonical search/indexing path (includes `custom/` platform prefix for MCP tasks) | Set directly as a string |
| `hierarchy` | UI grouping; legacy path construction (no platform prefix) | List of tag names, values resolved from tags |

Both live under `spec.additionalContext`. Always set **both** for full functionality. The `resourcePath` is the primary identifier for search; `hierarchy` controls the UI grouping.

**Important:** For MCP-authored tasks, `resourcePath` must always start with `custom/` (the server enforces this automatically). The `hierarchy` defines the grouping **within** the custom tree. Always include `"platform"` as the first hierarchy entry with a tag value of `"custom"` so that the UI groups these tasks under the custom namespace.

## Format

A YAML list of tag names that exist in `spec.tags`. For MCP-authored tasks, always start with `platform=custom`:

```yaml
spec:
  tags:
  - name: platform
    value: custom
  - name: resource_type
    value: platform
  - name: resource_name
    value: papi-issues
  additionalContext:
    resourcePath: custom/runwhen/papi
    hierarchy:
    - platform
    - resource_type
    - resource_name
```

## Common hierarchy patterns

All MCP-authored tasks should start the hierarchy with `platform` → `custom`.

### Custom Kubernetes task
```yaml
hierarchy:
- platform
- cluster
- namespace
- resource_name
```
Tags: `platform=custom`, `cluster=cluster-01`, `namespace=backend-services`, `resource_name=papi`

### Custom AWS task
```yaml
hierarchy:
- platform
- region
- service
- resource_name
```
Tags: `platform=custom`, `region=us-east-1`, `service=lambda`, `resource_name=my-function`

### Custom platform / internal tools
```yaml
hierarchy:
- platform
- resource_type
- resource_name
```
Tags: `platform=custom`, `resource_type=platform`, `resource_name=papi-issues`

### Custom GitHub task
```yaml
hierarchy:
- platform
- resource_type
- resource_name
```
Tags: `platform=custom`, `resource_type=github`, `resource_name=infra-flux-nonprod`

## How to set it

Pass `hierarchy` when calling `commit_slx`. Always include `platform=custom` as the first level:

```python
commit_slx(
    slx_name="my-check",
    workspace_name="my-workspace",
    resource_path="custom/kubernetes/cluster-01/prod-ns/my-app",
    hierarchy=["platform", "cluster", "namespace", "resource_name"],
    tags=[
        {"name": "platform", "value": "custom"},
        {"name": "cluster", "value": "cluster-01"},
        {"name": "namespace", "value": "prod-ns"},
        {"name": "resource_name", "value": "my-app"},
    ],
    # ... other params
)
```

## Rules

1. **Always start with `platform=custom`** — every MCP-authored task hierarchy must begin with `["platform", ...]` and include a tag `{"name": "platform", "value": "custom"}`
2. **Every entry in hierarchy must be a tag name** that exists in `spec.tags` — if a hierarchy key has no matching tag, that segment is skipped in path construction
3. **Order matters** — list from broadest to most specific (platform → cluster → namespace → resource)
4. **Keep it short** — 3-5 levels is typical; deeply nested hierarchies add no value
5. **Do NOT reuse hierarchy paths of existing resources** — custom tasks must be grouped separately from platform-managed resources

## Important: commit reconciliation

After `commit_slx` succeeds, it takes time for the workspace config repo commit to reconcile through the system. The hierarchy grouping will not appear in the UI or search results immediately. Allow 1-3 minutes for the platform to process the commit and update its indexes.
