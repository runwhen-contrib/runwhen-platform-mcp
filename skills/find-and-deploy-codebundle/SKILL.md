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
    codebundle_slug="k8s-podresources-health"
)
```

Key fields to check:
- **tasks** / **slis**: what the codebundle actually does
- **support_tags**: which platforms and services it covers
- **access_level**: "read-only" vs "read-write"
- **runbook_source_url**: link to the source code on GitHub
- **configuration_type**: whether it is auto-discoverable or manual

### 3. Decide: deploy existing OR build custom

| Signal | Action |
|--------|--------|
| Registry has an exact match with the tasks/SLIs the user needs | Deploy it (step 4) |
| Registry has a partial match — covers most needs | Deploy it + note any gaps |
| Registry has nothing relevant | Switch to `build-runwhen-task` skill |

### 4. Deploy via commit_slx

Registry codebundles are pre-packaged — but to deploy one to a workspace
you still use `commit_slx`. The key difference: you'll use the codebundle's
**source scripts** (from GitHub) rather than writing your own.

Typical flow:
1. Read the codebundle source from `runbook_source_url`
2. Identify the main task/SLI scripts and required env vars
3. `get_workspace_secrets` — map required secrets (e.g. `kubeconfig`)
4. `get_workspace_locations` — pick a runner location
5. `run_script_and_wait` — test the script with appropriate env/secret vars
6. `commit_slx` — commit with proper tags, env vars, and secret mappings

### 5. Tell the user what you found

Always summarize:
- What you searched for
- How many results came back
- Which codebundle you selected and why
- What tasks/SLIs it provides
- Any gaps that would need custom work

## What NOT to do

- Don't skip the registry search and jump straight to custom scripts
- Don't try to install or clone the codebundle's git repo — the registry
  is for discovery, and deployment goes through `commit_slx`
- Don't assume the registry has everything — if no match, build custom
- Don't deploy a codebundle without confirming it covers the user's needs

## Example conversation flow

**User**: "Add a health check for our Kubernetes pods in the production namespace"

**Agent**:
1. `search_registry(search="kubernetes pod health")`
2. Finds `k8s-podresources-health` with 4 tasks covering resource limits,
   utilization, and VPA recommendations
3. `get_registry_codebundle(collection_slug="rw-cli-codecollection", codebundle_slug="k8s-podresources-health")`
4. Reviews the full details and confirms it matches the user's needs
5. Tells user: "I found an existing codebundle that checks pod resource
   health. It includes 4 tasks: [list]. Shall I deploy it to your workspace?"
6. On confirmation, proceeds with the deploy workflow
