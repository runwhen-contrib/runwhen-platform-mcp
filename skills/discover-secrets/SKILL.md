---
name: discover-secrets
description: "Discover and configure secrets for RunWhen SLX scripts. Use when: (1) Choosing which secrets to map for a task via secret_vars, (2) The user asks about secret configuration, authentication, or credentials, (3) Building a task that needs API tokens, kubeconfigs, or service account keys, or (4) Understanding how secrets are injected as file paths on runners."
---

# Discover Secrets

Find and configure the right secrets for an SLX script by searching available workspace secrets and inferring from platform tags and existing SLX configurations.

## How secrets work in RunWhen

Most secrets are **not stored in RunWhen** — they are configured locally on runner locations (private runners). The workspace secret list (`get_workspace_secrets`) returns **key names** that map to secrets already provisioned on the runner's filesystem or environment.

When a script runs, mapped secrets are injected as **file paths** in environment variables:

```python
# In your script
token_path = os.environ.get("USER_TOKEN")  # This is a FILE PATH, not the value
with open(token_path) as f:
    token = f.read().strip()
```

The `read_secret` helper pattern handles both file-based (runner) and direct-value (local testing) cases:

```python
def read_secret(env_var):
    val = os.environ.get(env_var, "")
    if val and os.path.isfile(val):
        with open(val) as f:
            return f.read().strip()
    return val.strip()
```

## Discovery workflow

### Step 1: List available secrets

```python
get_workspace_secrets(workspace_name="my-workspace")
```

This returns key names like `kubeconfig`, `BETA-USER_TOKEN`, `slack`, `ops-suite-sa`. These are the names you reference in `secret_vars` when calling `run_script` or `commit_slx`.

### Step 2: Infer from platform and context

Secret names often follow naming conventions tied to the platform or environment:

| Platform / Use Case | Likely secret names | Notes |
|---------------------|-------------------|-------|
| Kubernetes | `kubeconfig` | Almost always present; the standard name |
| RunWhen PAPI (beta) | `BETA-USER_TOKEN`, `beta_tok1` | JWT tokens for beta environment |
| RunWhen PAPI (prod) | `USER_TOKEN`, `PROD-USER_TOKEN` | JWT tokens for production |
| GitHub | `*-REPO-TOKEN`, `RUNWHEN-REPO-TOKEN` | PATs for repo access |
| Slack | `slack` | Webhook URLs or bot tokens |
| GCP | `*-sa`, `ops-suite-sa`, `gcp-*` | Service account JSON keys |
| Azure | `*-clientId`, `*-clientSecret`, `*-tenantId`, `*-subscriptionId` | Azure SP credentials (often as a set of 4) |

### Step 3: Search existing SLXs with similar tags

Call `get_workspace_config_index` or `search_workspace` to find SLXs with similar `resource_type` or platform tags. Their `secret_vars` configurations reveal which secrets are commonly used for that platform.

For example, if you're building a Kubernetes task:
1. Search for SLXs with `resource_type=kubernetes` or `cluster=<your-cluster>`
2. Check their runbook YAML for `secretsProvided` entries
3. Use the same secret key names for your new task

### Step 4: Match secrets to runner locations

Different runner locations may have different secrets provisioned. Location auto-resolves (workspace runners are preferred over public), but if a secret exists in the workspace list and the script gets empty/missing values at runtime, the secret likely isn't provisioned on that particular runner. Use `get_workspace_locations` to see available runners and try a different one if needed.

## Configuring secrets in commit_slx

Map environment variable names to workspace secret keys:

```python
commit_slx(
    slx_name="my-check",
    workspace_name="my-workspace",
    secret_vars={
        "kubeconfig": "kubeconfig",          # env var name → workspace secret key
        "USER_TOKEN": "BETA-USER_TOKEN",     # can differ if naming conventions vary
    },
    # ... other params
)
```

This generates in the runbook YAML:

```yaml
spec:
  secretsProvided:
  - name: kubeconfig
    workspaceKey: kubeconfig
  - name: USER_TOKEN
    workspaceKey: BETA-USER_TOKEN
```

## Common patterns

### Kubernetes tasks
```python
secret_vars={"kubeconfig": "kubeconfig"}
```

### PAPI query tasks
```python
secret_vars={"USER_TOKEN": "BETA-USER_TOKEN"}  # or PROD-USER_TOKEN
```

### Multi-cloud tasks
```python
secret_vars={
    "kubeconfig": "kubeconfig",
    "AZURE_CLIENT_ID": "runwhen-nonprod-azure-clientId",
    "AZURE_CLIENT_SECRET": "runwhen-nonprod-azure-clientSecret",
}
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Script gets empty string for secret | Secret not provisioned on that runner location | Try a different location, or verify the secret exists on the runner |
| `FileNotFoundError` when reading secret | Script treating secret value as literal instead of file path | Use the `read_secret` helper pattern above |
| 401/403 from API | Wrong secret key, expired token, or wrong auth scheme | Check if token needs `Bearer` vs `Token` prefix; verify the secret key name matches |

## Important: commit reconciliation

After `commit_slx` succeeds, it takes time for the workspace config repo commit to reconcile through the system. Secret mappings in the runbook YAML will not take effect until the platform processes the commit. Allow 1-3 minutes for reconciliation before running the SLX from the workspace UI.
