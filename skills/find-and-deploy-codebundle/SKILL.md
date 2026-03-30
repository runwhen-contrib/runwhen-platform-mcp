---
name: find-and-deploy-codebundle
description: "Search the RunWhen CodeBundle Registry for production-ready automation and deploy it as an SLX. Use when: (1) Adding monitoring or health checks for Kubernetes, databases, or cloud services, (2) The user asks to create a health check and a registry codebundle may already exist, (3) Deploying pre-built automation instead of writing custom scripts, or (4) Searching for existing codebundles by keyword or platform."
---

# Find and Deploy a CodeBundle from the Registry

Search the RunWhen CodeBundle Registry for production-ready automation
**before** writing a custom script. The registry contains hundreds of
pre-built, tested codebundles for Kubernetes, cloud providers, databases,
and more.

## When to use this skill

- The user asks to "add monitoring for X" or "create a health check for Y"
- The user describes a task that sounds like common operational automation
- Before starting a custom `build-runwhen-task` workflow — always check the
  registry first

## Key concept: two different deployment paths

| Path | Tool | When to use |
|------|------|-------------|
| **Registry codebundle** | `deploy_registry_codebundle` | Pre-built automation from the registry. Points to the codebundle's own git repo + robot file. No inline script. |
| **Custom script** | `commit_slx` | Hand-written bash/python script. Uses the Tool Builder codebundle with an inline base64-encoded script. |

These are **not interchangeable** — the YAML structure is fundamentally
different. Registry codebundles use the codebundle's own `runbook.robot` /
`sli.robot` files from its codecollection git repo.

## Workflow

### 1. Search the registry

```
search_registry(search="kubernetes pod health", platform="Kubernetes")
```

Search tips:
- Use natural language: "postgres connection pool", "gcp bucket permissions"
- Filter by `platform` when you know it: "Kubernetes", "GCP", "AWS"
- Filter by `tags` for specifics: "GKE,KUBERNETES" or "EKS"
- Review `tasks` and `slis` in results to confirm the codebundle covers
  what the user needs

### 2. Get full codebundle details

If a result looks promising, fetch the full details:

```
get_registry_codebundle(
    collection_slug="rw-cli-codecollection",
    codebundle_slug="k8s-namespace-healthcheck"
)
```

Key fields to examine:
- **tasks** / **slis**: what the codebundle actually does
- **user_variables**: the config vars you need to provide (NAMESPACE, CONTEXT, etc.)
- **support_tags**: which platforms and services it covers
- **access_level**: "read-only" vs "read-write"
- **runbook_path** / **sli_path**: confirms whether runbook and/or SLI exist
- **codecollection.git_url**: the repo URL needed for deployment
- **runbook_source_url**: link to source code on GitHub

### 3. Decide: deploy existing OR build custom

| Signal | Action |
|--------|--------|
| Registry has an exact match with the tasks/SLIs the user needs | Deploy it (step 4) |
| Registry has a partial match — covers most needs | Deploy it + note any gaps |
| Registry has nothing relevant | Switch to `build-runwhen-task` skill |

### 4. Deploy with deploy_registry_codebundle

> **Location auto-resolves.** You do NOT need to call
> `get_workspace_locations` first. If you omit `location`, the server
> automatically picks the best runner (workspace locations preferred
> over public). Only specify it when targeting a specific runner.

```
deploy_registry_codebundle(
    slx_name="k8s-ns-health-prod",
    alias="Production Namespace Health",
    statement="All pods in the production namespace should be healthy",
    repo_url="https://github.com/runwhen-contrib/rw-cli-codecollection",
    codebundle_path="codebundles/k8s-namespace-healthcheck",
    workspace_name="my-workspace",
    config_vars={
        "NAMESPACE": "production",
        "CONTEXT": "prod-cluster",
        "KUBERNETES_DISTRIBUTION_BINARY": "kubectl",
        "ANOMALY_THRESHOLD": "3.0"
    },
    secret_vars={"kubeconfig": "kubeconfig"},
    deploy_runbook=True,
    deploy_sli=True,
    sli_interval_seconds=180,
    access="read-only",
    data="config"
)
```

How to fill in the parameters:
- **repo_url** → from `codecollection.git_url` in registry response
- **codebundle_path** → derive from `runbook_path` (strip `/runbook.robot`)
  or from `runbook_source_url` (the path after `/tree/main/`)
- **config_vars** → from `user_variables` in registry response
- **deploy_runbook** → True if `runbook_path` is not null
- **deploy_sli** → True if `sli_path` is not null
- **access** → from `access_level` in registry response
- **secret_vars** → usually `{"kubeconfig": "kubeconfig"}` for Kubernetes
  codebundles; check the template for other secrets
- **location** → optional; auto-resolved from workspace config if omitted

### 5. Tell the user what you found

Always summarize:
- What you searched for
- How many results came back
- Which codebundle you selected and why
- What tasks/SLIs it provides
- The `user_variables` you configured and why
- Any gaps that would need custom work

## What NOT to do

- **Don't use `commit_slx` for registry codebundles** — it generates the
  wrong YAML structure (Tool Builder inline scripts vs registry codebundle
  references)
- Don't skip the registry search and jump straight to custom scripts
- Don't try to install or clone the codebundle's git repo
- Don't assume the registry has everything — if no match, build custom
- Don't deploy without confirming config_vars with the user

## Example conversation flow

**User**: "Add a health check for our Kubernetes pods in the production namespace"

**Agent**:
1. `search_registry(search="kubernetes namespace health")`
2. Finds `k8s-namespace-healthcheck` — 9 tasks + 4 SLIs covering events,
   restarts, pending pods, workload conditions
3. `get_registry_codebundle(collection_slug="rw-cli-codecollection",
   codebundle_slug="k8s-namespace-healthcheck")`
4. Reviews `user_variables`: needs NAMESPACE, CONTEXT, KUBERNETES_DISTRIBUTION_BINARY
5. `get_workspace_secrets(workspace_name="my-workspace")` to confirm available secrets
6. Tells user: "I found a production-ready codebundle with 9 tasks and 4 SLIs.
   It needs NAMESPACE, CONTEXT, and a kubeconfig secret. Shall I deploy it?"
7. On confirmation: `deploy_registry_codebundle(...)` with config (location auto-resolves)
