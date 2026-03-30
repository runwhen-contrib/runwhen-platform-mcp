---
name: discover-locations
description: "Discover and select runner locations for RunWhen scripts. Use when: (1) Choosing where to execute a task or script, (2) The user asks about runner locations or execution environments, (3) get_workspace_locations returns an empty list and you need guidance, or (4) Building a task that needs a specific cluster or region for execution."
---

# Discover Locations

Find and select the right runner location for an SLX script.

## When to use

- Before running a script with `run_script` or `run_script_and_wait`
- Before committing an SLX with `commit_slx`
- When the user asks about runner locations or execution environments
- When troubleshooting a script that can't reach infrastructure

## What locations are

Runner locations are **where scripts physically execute**. They are lightweight RunWhen agents installed in the user's infrastructure (Kubernetes clusters, cloud VPCs, etc.) that receive and run scripts.

Each location has:
- A **name** (e.g. `location-01-us-west1`, `northamerica-northeast2-01`)
- A **status** (healthy, degraded, etc.)
- **Access to specific infrastructure** — a location in cluster-A can reach cluster-A resources but not cluster-B

## Discovery workflow

### Step 1: List available locations

```python
get_workspace_locations(workspace_name="my-workspace")
```

Returns a list like:
```json
[
  {"location": "location-01-us-west1", "status": "HEALTHY", "lastUpdated": "..."},
  {"location": "northamerica-northeast2-01", "status": "HEALTHY", "lastUpdated": "..."}
]
```

### Step 2: Match location to target infrastructure

Choose a location based on what the script needs to reach:

| Script targets | Choose location in |
|---------------|-------------------|
| Kubernetes cluster-A resources | Same cluster or VPC as cluster-A |
| AWS resources in us-east-1 | A location with AWS credentials for that region |
| GCP resources | A location with GCP service account access |
| Public APIs (GitHub, Slack) | Any healthy location with internet access |

### Step 3: Verify with existing SLXs

Call `get_workspace_config_index` or `search_workspace` to find SLXs targeting similar infrastructure. Their `location` field reveals which runner locations are proven to work for that target.

### Step 4: Check location health

Only use locations with a healthy status. If a location shows degraded or unknown status, choose an alternative or wait.

## Common patterns

### Single-cluster workspace
Most workspaces have one primary location. Use it for all scripts:
```python
run_script_and_wait(workspace_name="my-workspace", script=my_script, location="location-01-us-west1", ...)
```

### Multi-cluster workspace
Match the location to the cluster your script targets. The location name often hints at its region or purpose:
- `us-west1-prod-01` → production cluster in us-west1
- `eu-central1-staging` → staging cluster in eu-central1

### Location in commit_slx
The location is baked into the SLX config. The script always runs on that location:
```python
commit_slx(slx_name="my-check", workspace_name="my-workspace", location="location-01-us-west1", ...)
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `get_workspace_locations` returns empty list | No runners registered or debugslx not configured | Check workspace runner setup in RunWhen UI |
| Script times out or can't reach resources | Wrong location — runner can't reach the target | Try a different location closer to the target infrastructure |
| Location shows unhealthy status | Runner pod may be down or evicted | Check runner health in the target cluster; wait for recovery |
| Script works in location A but not B | Different secrets/access per location | Verify the target location has the required secrets provisioned |
