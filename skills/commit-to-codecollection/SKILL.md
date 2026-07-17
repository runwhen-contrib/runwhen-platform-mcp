---
name: commit-to-codecollection
description: "Render a tested tool-builder task as a private Custom Discovery CodeCollection for GitOps. Use when: (1) The user wants version-controlled storage instead of commit_slx to a workspace, (2) Publishing tool-builder output to a private git repo shaped like simple-private-codecollection, (3) After run_script_and_wait succeeds and the user chooses GitOps over inline workspace SLX, (4) The user asks to commit to a codecollection, private codecollection, or generation-rule repo, or (5) You need to confirm secrets, env vars, and runtime repo URL before baking them into TaskSet templates."
---

# Commit to CodeCollection (GitOps)

Render a tested tool-builder task into the **Custom Discovery CodeCollection** layout
documented at https://docs.runwhen.com/guides/custom-discovery-codecollection/

This is the GitOps alternative to `commit_slx` (which writes inline scripts into the workspace repo).

## When to use each path

| Goal | Tool |
|------|------|
| Fast iteration in a dev workspace | `commit_slx` |
| Version-controlled, reusable bundle in **your** git repo | `render_codecollection_skill` |
| Deploy a registry Skill Template | `deploy_registry_codebundle` |

## Prerequisites

1. Task already **tested** via `run_script_and_wait` (same bar as `commit_slx`)
2. `get_workspace_context` loaded before authoring
3. **Platform format confirmed with the user** (see below)
4. runwhen-local release with the **matching platform indexer** on the target runner
   - `platform: runwhen` → RW-1355 runwhen indexer
   - `platform: kubernetes|azure|aws|gcp` → that cloud indexer enabled in `workspaceInfo.yaml`

## Choose output format (ask the user first)

**Do not call `render_codecollection_skill` until the user confirms platform and scope.**

1. Call `list_discovery_platforms()` and present the two main paths:

| User goal | `platform` | Typical scope |
|-----------|------------|---------------|
| One health-check SLX for the whole workspace (MCP tool-builder) | `runwhen` | `resource_types: [workspace]` |
| One SLX per K8s namespace/deployment/CRD/etc. | `kubernetes` | e.g. `[namespace]`, `[deployment]` |
| One SLX per Azure/AWS/GCP resource | `azure` / `aws` / `gcp` | CloudQuery table names from catalog |

2. **Ask clarifying questions** when ambiguous:
   - "Should this run once per workspace, or once per Kubernetes namespace/cluster/resource?"
   - "Which cloud account scope — individual resources, resource groups, or subscriptions?"
   - For Kubernetes: "Which namespace pattern or resource type should drive SLX creation?"

3. Call `list_indexed_resource_types(platform=<chosen>, search=<hint>)` and **only**
   offer `resource_types` values returned by that tool (plus Kubernetes CRD
   `plural.group[/version]` syntax when the user names a CRD).

   **Airgap:** catalog lookup is fully offline — bundled JSON + markdown ship with
   the MCP package. For azure/aws/gcp you **must** pass `search=` (≥2 chars).
   Do not reference docs.runwhen.com; use `get_skill('author-generation-rules')`
   for narrative guides.

4. Confirm `match_rules` and `slx_qualifiers` with the user before render.

Bundled catalogs live under `author-generation-rules/references/catalogs/` (no network).

## Workflow

1. **Build and test** — follow `build-runwhen-task` through validation and `run_script_and_wait`
2. **Collect template inputs** — see checklist below (secrets, env vars, runtime vars, runtime repo URL)
3. **Render** — call `render_codecollection_skill` with the **same** script + metadata used in step 1
4. **Review** — open `codebundles/<bundle>/.runwhen/SKILL_TEMPLATE.md` and
   `codebundles/<bundle>/.runwhen/raw_script.{py,sh}` (decoded script + plain-English match rules)
5. **Commit locally** — `git add`, `git commit`, `git push` to your private codecollection repo
6. **Wire runner** — add the repo to `workspaceInfo.yaml` `codeCollections` and reconcile runwhen-local

## Before render — collect template inputs (ask the user)

The TaskSet template bakes configuration into YAML. **Do not render until these are
confirmed** — missing secrets or wrong repo URLs fail silently at runtime.

### 1. Secrets (`secret_vars`)

Follow **`discover-secrets`**:

```text
get_workspace_secrets(workspace_name="<workspace>")
```

- List every secret the script reads (`os.environ["kubeconfig"]`, `USER_TOKEN`, etc.)
- Map **env var name → workspaceKey** (same shape as `commit_slx` / `run_script_and_wait`)
- **Ask the user** to confirm each secret exists on the **target runner location(s)**
- Secrets are injected as **file paths**, not literal values — script must `open()` them

Example for render:

```text
secret_vars={"kubeconfig": "kubeconfig", "USER_TOKEN": "BETA-USER_TOKEN"}
```

These become `secretsProvided` in the TaskSet template and names in `SECRET_ENV_MAP`.

### 2. Static env vars (`env_vars`)

- Non-secret config baked into the template (namespace, cluster name, feature flags)
- Must match what you passed to `run_script_and_wait` during testing
- **Ask the user** for production vs dev values if they differ from the test workspace

### 3. Runtime variables (`runtime_vars`)

- Per-run user inputs (namespace picker, lookback window, resource name)
- Only include if the task uses them — same schema as `commit_slx`
- **Ask the user** for defaults, descriptions, and validation (regex or enum)

### 4. Runtime repo URL (`generic_runtime_repo_url`)

The MCP resolves this automatically. Resolution order:

1. **Explicit call arg** — `generic_runtime_repo_url` you pass in
2. **Env override** — `MCP_GENERIC_CODECOLLECTION_REPO_URL` on the MCP server
3. **Workspace lookup** — the MCP queries `GET /api/v3/codecollections` (the
   same list the platform UI uses) and picks the entry named
   `rw-generic-codecollection`. In airgap workspaces this is already set to
   the internal catalog URL (e.g.
   `http://rw-airgap-cc-catalog-svc.<namespace>:8080/git/rw-generic-codecollection.git`).
4. **`github.com` default**

Practical rules:

- **Public / SaaS workspaces:** leave `generic_runtime_repo_url` unset — the
  workspace lookup or the github default is correct.
- **Airgap / private catalogs:** leave `generic_runtime_repo_url` unset — the
  workspace lookup finds the registered mirror. Only pass an explicit override
  if you're deliberately targeting a fork.
- Check `generic_repo_resolved_from` in the render response to confirm which
  source won (`explicit` / `env` / `workspace` / `default`).

### 5. Discovery platform fields (confirm with user — required for non-runwhen)

| Field | Template effect | Ask when |
|-------|-----------------|----------|
| `platform` | Generation rule indexer + SLX include pattern | **Always** — ask user workspace vs discovery |
| `resource_types` | Which indexed resources to match | Any discovery platform; grep catalog via `list_indexed_resource_types` |
| `match_rules` | Predicates narrowing matches | Namespace patterns, tags, replica counts, etc. |
| `slx_qualifiers` | SLX naming scope (`namespace`, `cluster`, …) | Discovery platforms — confirm with user |
| `base_name` | Short SLX suffix (<15 chars) | Discovery bundles with long bundle names |

**Never guess `resource_types`.** Call `list_indexed_resource_types(platform=...)` and
pick from the returned list. Invalid types are rejected at render time.

Discovery platforms (`kubernetes`, `azure`, `aws`, `gcp`) emit platform tag/hierarchy
includes in the SLX template (e.g. `kubernetes-tags.yaml`). Do **not** pass `hierarchy`
or `resource_path` for those platforms — they are rejected.

### 6. Other render fields (confirm with user when non-obvious)

| Field | Template effect | Ask when |
|-------|-----------------|----------|
| `task_title` | Static Robot task title | Always — must be a literal string |
| `timeout_seconds` | `TIMEOUT_SECONDS` in TaskSet | Long-running checks |
| `access` / `data` | SLX tags | Modifies infra vs read-only |
| `include_sli` + `sli_script` | Extra SLI template | User wants a monitor too |
| `generic_runtime_ref` | Git ref pinned in templates | Not `main` |

### 7. Cross-check against the test run

Before calling `render_codecollection_skill`, verify:

- [ ] Same `script` string that passed `validate_script` and `run_script_and_wait`
- [ ] Same `interpreter`, `env_vars`, `secret_vars`, `runtime_vars` as the successful test
- [ ] User confirmed secrets are provisioned on runners that will execute this bundle
- [ ] `generic_runtime_repo_url` left unset (MCP auto-resolves) unless deliberately targeting a fork

## Example — workspace-scoped (platform: runwhen)

```text
render_codecollection_skill(
  bundle_name="my-health-check",
  alias="My Health Check",
  statement="The service should respond healthy on every probe interval.",
  workspace_name="dev-workspace",
  task_title="Run my health check",
  script=<tested script>,
  interpreter="python",
  env_vars={"NAMESPACE": "prod"},
  secret_vars={"kubeconfig": "kubeconfig"},
  access="read-only",
  data="logs-bulk",
  output_dir="/path/to/my-private-codecollection",
)
```

## Example — Kubernetes namespace discovery (platform: kubernetes)

After the user confirms namespace-scoped discovery:

```text
list_indexed_resource_types(platform="kubernetes", search="namespace")

render_codecollection_skill(
  bundle_name="tybr-kfk-dly",
  alias="Check Tyburn Recorder Apiary Kafka consumption delay",
  statement="Tyburn Recorder Apiary Kafka consumption delay (p95) should stay below 60s.",
  workspace_name="dev-workspace",
  task_title="Check Tyburn Recorder Apiary Kafka consumption delay",
  script=<tested script>,
  interpreter="python",
  platform="kubernetes",
  resource_types=["namespace"],
  match_rules=[{
    "type": "pattern",
    "pattern": "^tyburnrecorder-apiary$",
    "properties": ["name"],
    "mode": "substring",
  }],
  slx_qualifiers=["namespace", "cluster"],
  base_name="tybr-kfk-dly",
  access="read-only",
  data="logs-bulk",
  env_vars={"QUANTILE": "0.95", "DELAY_THRESHOLD_SECONDS": "60.0"},
)
```

The SLX template will include `kubernetes-tags.yaml` and `kubernetes-hierarchy.yaml`.

## Output layout

```
codebundles/<bundle_name>/
├── README.md
└── .runwhen/
    ├── SKILL_TEMPLATE.md         ← description + match rules (points to raw_script)
    ├── raw_script.py             ← decoded script for reviewers/systems to inspect
    ├── generation-rules/
    │   └── <bundle_name>.yaml    ← platform + resourceTypes from user-confirmed catalog
    └── templates/
        ├── <bundle_name>-slx.yaml   ← includes *-tags.yaml / *-hierarchy.yaml for discovery
        └── <bundle_name>-taskset.yaml   ← tool-builder GEN_CMD (base64 in YAML)
```

Templates delegate runtime to `rw-generic-codecollection/codebundles/tool-builder`.
The `raw_script.{py,sh}` file shows the **decoded** script — reviewers and automated
systems should inspect this file and should **not** parse base64 from templates.

## Key rules

- Same script contract as `commit_slx` (Python `main()` returns issues list; Bash writes JSON to FD 3)
- Same tag requirements: `access`, `data`
- `task_title` must be a static literal (no `${VAR}` placeholders)
- Default generation rule for **workspace** output: `platform: runwhen`, one SLX per workspace
- For **discovery** output: set `platform` to `kubernetes|azure|aws|gcp`; SLX templates use
  workspace-builder tag/hierarchy includes — see `list_discovery_platforms()`
- For hand-authored generation rules beyond tool-builder render, use
  **`author-generation-rules`** skill and runwhen-local `docs/authoring/`

## Critical — GEN_CMD integrity (do not skip)

The TaskSet template stores the script as **base64 `GEN_CMD`**. The review files
(`.runwhen/SKILL_TEMPLATE.md` and `.runwhen/raw_script.{py,sh}`) show the **decoded**
script. These **must match**.

`tool-builder/runbook.robot` reads issue dict keys **exactly**:

- `issue title`
- `issue description`  ← spelling matters; `issue desription` causes `KeyError` at runtime
- `issue severity`
- `issue next steps`

### ALWAYS

1. **Render with `render_codecollection_skill`** — never hand-author or re-paste `GEN_CMD` base64.
2. **Use the tool output as-is** for templates — if YAML indentation looks wrong, fix
   `codecollection_render.py` and re-render; do not manually splice base64 strings.
3. **Verify round-trip before git commit** (stdio / local checkout):

```bash
python3 - <<'PY'
import base64, re, pathlib
taskset = pathlib.Path("codebundles/<bundle>/.runwhen/templates/<bundle>-taskset.yaml").read_text()
raw = pathlib.Path("codebundles/<bundle>/.runwhen/raw_script.py").read_text()
b64 = re.search(r"name: GEN_CMD\n\s+value: ['\"]([A-Za-z0-9+/=]+)", taskset).group(1)
decoded = base64.b64decode(b64).decode()
assert decoded == raw, "raw_script does not match decoded GEN_CMD"
assert "issue description" in decoded, "missing exact key issue description"
assert "desription" not in decoded, "typo in issue description key"
assert "def main():" in decoded or "main()" in decoded
print("GEN_CMD round-trip OK")
PY
```

4. **Re-run `validate_script`** on the decoded script if you touched templates at all.

### NEVER

- ❌ Copy base64 from chat logs, truncated diffs, or an old render into a template
- ❌ Hand-fix TaskSet YAML and guess the base64 blob
- ❌ Trust `.runwhen/SKILL_TEMPLATE.md` alone — the runner uses **TaskSet `GEN_CMD`**, not the review file

If `render_codecollection_skill` is unavailable (older MCP deploy), run
`render_codecollection_files()` from `runwhen_platform_mcp.codecollection_render` locally
with the **same script string** passed to `run_script_and_wait` — still do not hand-edit base64.

## Related skills

- `build-runwhen-task` — author and test before rendering
- `discover-secrets` — map `secret_vars` before render
- `discover-locations` — confirm runner locations have required secrets
- `author-generation-rules` — schema, catalogs, matchRules, and examples
- `list_discovery_platforms` / `list_indexed_resource_types` — MCP tools for platform + catalog lookup
- `find-and-deploy-codebundle` — check registry before building custom automation
