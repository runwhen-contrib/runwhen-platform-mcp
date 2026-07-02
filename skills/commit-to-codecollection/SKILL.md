---
name: commit-to-codecollection
description: "Render a tested tool-builder task as a private Custom Discovery CodeCollection for GitOps. Use when: (1) The user wants version-controlled storage instead of commit_slx to a workspace, (2) Publishing tool-builder output to a private git repo shaped like simple-private-codecollection, (3) After run_script_and_wait succeeds and the user chooses GitOps over inline workspace SLX, or (4) The user asks to commit to a codecollection, private codecollection, or generation-rule repo."
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
3. runwhen-local release with **`platform: runwhen`** support (RW-1355) on the target runner

## Workflow

1. **Build and test** — follow `build-runwhen-task` through validation and `run_script_and_wait`
2. **Render** — call `render_codecollection_skill` with the same script/metadata you would pass to `commit_slx`
3. **Review** — open `codebundles/<bundle>/.runwhen/README.md` (decoded script + plain-English match rules)
4. **Commit locally** — `git add`, `git commit`, `git push` to your private codecollection repo
5. **Wire runner** — add the repo to `workspaceInfo.yaml` `codeCollections` and reconcile runwhen-local

## Example

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

## Output layout

```
codebundles/<bundle_name>/
├── README.md
└── .runwhen/
    ├── README.md              ← human review (decoded script, match rules)
    ├── generation-rules/
    │   └── <bundle_name>.yaml ← platform: runwhen, resourceTypes: [workspace]
    └── templates/
        ├── <bundle_name>-slx.yaml
        └── <bundle_name>-taskset.yaml   ← tool-builder GEN_CMD (base64 in YAML)
```

Templates delegate runtime to `rw-generic-codecollection/codebundles/tool-builder`.
The review file shows the **decoded** script — reviewers never need to base64-decode templates.

## Key rules

- Same script contract as `commit_slx` (Python `main()` returns issues list; Bash writes JSON to FD 3)
- Same tag requirements: `access`, `data`
- `task_title` must be a static literal (no `${VAR}` placeholders)
- Default generation rule: `platform: runwhen`, one SLX per workspace match
- For cloud/K8s **per-resource** discovery (CRD, Azure VM, etc.), use the
  **`author-generation-rules`** skill and runwhen-local `docs/authoring/` —
  not legacy docs.runwhen.com author pages

## Related skills

- `build-runwhen-task` — author and test before rendering
- `author-generation-rules` — schema, catalogs, and examples from runwhen-local
- `find-and-deploy-codebundle` — check registry before building custom automation
