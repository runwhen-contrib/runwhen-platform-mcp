---
name: manage-knowledge
description: "Create, update, and manage Knowledge Base articles that provide operational context to AI assistants. Use when: (1) The user wants to add institutional knowledge, architecture notes, or ownership details, (2) Creating or updating KB articles via create_knowledge_base_article or update_knowledge_base_article, (3) Adding operational context that improves investigation quality, or (4) The user asks about knowledge base, KB articles, or documentation for the workspace."
---

# Manage Knowledge

Knowledge Base (KB) articles are durable operational facts indexed into the workspace's
Knowledge Overlay Graph. They improve investigation quality by providing context that
can't be inferred from diagnostic output alone — the kind of information that lives in
your team's heads rather than in task results.

## When to use

- User says "remember this", "note that", "add context about..."
- Investigation quality suffers because the assistant lacks institutional context
- Architecture, ownership, or process knowledge needs documenting
- Temporary operational notes for releases, incidents, or maintenance windows
- Replacing the `/remember` command from workspace chat UI with API-driven KB management

## Key concepts

### Knowledge vs Rules vs Commands

| Type | Purpose | Loaded when |
|------|---------|-------------|
| **Rules** | Short behavior directives (1-3 sentences) — noise suppression, priority framing | Every response, always |
| **Commands** | Multi-step investigation procedures | When user invokes `/command` |
| **Knowledge** | Durable facts — architecture, ownership, processes, dependencies | When relevant resource is identified (resource-scoped) or always (global) |

If it's a behavior instruction ("prioritize X over Y"), use a **Rule**.
If it's a procedure ("do steps 1-2-3"), use a **Command**.
If it's a fact ("service X depends on Y, owned by team Z"), use **Knowledge**.

### Resource scoping via resource_paths

KB articles can be scoped to specific infrastructure resources. The `resource_paths`
field links an article to canonical resource paths (same format as SLX `resourcePath`).

| Scope | `resource_paths` | When loaded |
|-------|-----------------|-------------|
| Global | `[]` (empty) | Every investigation |
| Resource-scoped | `["kubernetes/cluster-01/payments"]` | Only when the assistant investigates that resource |

Resource scoping keeps context lean — global knowledge provides the baseline,
resource-specific knowledge is loaded dynamically as the investigation narrows.

### Abstract entities

The `abstract_entities` field contains normalized tokens that improve discoverability
when searching for related concepts. Think of them as semantic tags.

Example: `["oom-killed", "memory-limits", "resource-quotas", "pod-eviction"]`

## Workflow

### 1. Audit existing knowledge

```
list_knowledge_base_articles(workspace_name="my-workspace")
```

### 2. Identify knowledge gaps

Common gaps that surface during investigations:
- "Who owns this service?" — ownership boundaries
- "Is this expected?" — known maintenance windows, expected behavior
- "What changed?" — recent architecture or deployment changes
- "What depends on this?" — service dependency maps
- "What's the process?" — release cadence, rollback procedures

### 3. Create articles

**Global knowledge (always loaded):**

```
create_knowledge_base_article(
    workspace_name="my-workspace",
    content="Team Platform owns all infrastructure in the shared-services namespace including ingress controllers, cert-manager, and external-dns. Team Backend owns all application services in the backend-services namespace. Escalation path: Platform issues go to #platform-oncall, application issues go to #backend-oncall.",
    resource_paths=[],
    abstract_entities=["ownership", "escalation", "platform-team", "backend-team"]
)
```

**Resource-scoped knowledge:**

```
create_knowledge_base_article(
    workspace_name="my-workspace",
    content="The payments service connects to payments-db (Cloud SQL) and uses Redis for session caching. During high-traffic periods (Black Friday, month-end), connection pool exhaustion is the most common failure mode. Check connection counts before investigating application errors.",
    resource_paths=["kubernetes/prod-cluster/payments"],
    abstract_entities=["payments", "connection-pool", "cloud-sql", "redis", "high-traffic"]
)
```

**Temporary knowledge (incidents / releases):**

```
create_knowledge_base_article(
    workspace_name="my-workspace",
    content="TEMPORARY (valid through March 28): The payments service is being migrated to a new database. Connection errors and increased latency are expected. Do not escalate payments-db errors during this window unless error rate exceeds 5% sustained for 15+ minutes.",
    resource_paths=["kubernetes/prod-cluster/payments"],
    abstract_entities=["payments", "migration", "database", "maintenance-window"]
)
```

### 4. Maintain over time

- **Deprecate stale articles** — `update_knowledge_base_article(note_id=..., workspace_name="my-workspace", status="deprecated")`
- **Delete temporary notes** after events pass — `delete_knowledge_base_article(note_id=..., workspace_name="my-workspace")`
- **Mark verified** after review — `update_knowledge_base_article(note_id=..., workspace_name="my-workspace", verified=True)`

## Knowledge categories and examples

### Ownership boundaries
```
content: "Team Platform owns shared-services, kube-system, and cert-manager namespaces. Team Backend owns backend-services, api-gateway, and payments namespaces."
resource_paths: []
abstract_entities: ["ownership", "teams", "namespaces"]
```

### Architecture and dependencies
```
content: "The checkout flow depends on: inventory-service (gRPC), payments-api (REST), and notification-service (async via Pub/Sub). If inventory-service is down, checkout fails with 503. If payments-api is slow, checkout times out after 30s."
resource_paths: ["kubernetes/prod-cluster/checkout"]
abstract_entities: ["checkout", "dependencies", "inventory", "payments", "pubsub"]
```

### Release process
```
content: "Release cadence: weekly on Tuesdays at 10am PT. Rollback procedure: revert the Flux GitOps commit and wait for reconciliation (typically 2-3 minutes). Freeze windows: last week of each quarter."
resource_paths: []
abstract_entities: ["releases", "rollback", "flux", "gitops", "freeze-window"]
```

### Known behaviors (environment-specific)
```
content: "The dev cluster runs with spot/preemptible nodes. Node cycling every 2-4 hours is expected. Pod disruptions from node cycling are not actionable unless they correlate with test failures."
resource_paths: ["kubernetes/dev-cluster"]
abstract_entities: ["dev-cluster", "spot-instances", "preemptible", "node-cycling"]
```

### Dynamic knowledge (pointing to tasks)
```
content: "For current deployment status of the payments service, run the deployment-status SLX. For open change requests, check the ServiceNow integration in the release-management workspace."
resource_paths: ["kubernetes/prod-cluster/payments"]
abstract_entities: ["payments", "deployment", "servicenow", "change-requests"]
```

## Authoring tips

- **Keep entries concise and operational** — not documentation, operational facts
- **Prefer "how to interpret this signal"** over narrative explanations
- **Use resource scoping** to avoid loading irrelevant context into every investigation
- **Point to Tasks or external systems** for information that changes frequently
- **Add time bounds** for temporary notes (e.g. "valid through Friday release window")
- **Remove or archive** short-term notes after the event passes
- **Use abstract_entities liberally** — they improve semantic search and concept linking
