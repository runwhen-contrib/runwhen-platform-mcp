---
name: author-generation-rules
description: "Write Custom Discovery generation rules using bundled runwhen-local reference (airgap-safe, no network). Use when: (1) Authoring .runwhen/generation-rules/*.yaml, (2) Choosing resourceTypes or matchRules, (3) Looking up indexer resource types, (4) Fixing legacy generation-rule YAML, or (5) Validating against the bundled schema."
---

# Author Generation Rules

All reference material is **bundled inside this MCP package** under
`skills/author-generation-rules/references/`. No GitHub or internet access
required (airgap-safe).

Load **`get_skill("author-generation-rules")`** for this workflow; read bundled
files from the MCP install tree (same paths relative to the repo / wheel).

## Bundled reference index

| Need | Bundled path |
|------|----------------|
| Concepts | `references/concepts.md` |
| Schema (GenerationRules kind) | `references/generation-rules-schema.md` |
| Full syntax | `references/generation-rules-syntax.md` |
| JSON Schema | `references/generation-rule-schema.json` |
| Tag / hierarchy | `references/tag-hierarchy-contract.md` |
| Examples | `references/examples/*.md` |
| Platform indexer guides | `references/indexed-resources/*.md` |
| **Resource type catalogs** | `references/catalogs/*-resource-catalog.md` |

Sync stamp: `references/BUNDLE_MANIFEST.json` (updated by MCP CI from runwhen-local).

**Before writing a rule:** search the relevant catalog file for the exact
CloudQuery table name, K8s kind, or CRD syntax. Do not guess from memory.

## Real schema (never use legacy shapes)

Use **`kind: GenerationRules`** (plural), one **`platform`** per file:

```yaml
apiVersion: runwhen.com/v1
kind: GenerationRules
spec:
  platform: azure   # azure | aws | gcp | kubernetes | runwhen
  generationRules:
    - resourceTypes:
        - azure_appservice_web_apps
      matchRules:
        - type: pattern
          pattern: "prod"
          properties: [tags]
          mode: substring
      slxs:
        - baseName: az-appsvc-triage
          qualifiers: ["resource", "resource_group"]
          baseTemplateName: azure-appservice-triage
          levelOfDetail: detailed
          outputItems:
            - type: slx
            - type: sli
            - type: runbook
              templateName: azure-appservice-triage-taskset.yaml
```

### Rejected patterns (do not generate)

| Legacy / aspirational | Use instead |
|----------------------|-------------|
| `kind: GenerationRule` (singular) | `kind: GenerationRules` |
| `spec.match.resource_type` | `spec.platform` + `resourceTypes[]` |
| `spec.match.predicates` with jsonpath | `matchRules[]` (`pattern`, `exists`, `and`/`or`/`not`) |
| `spec.templates:` / `spec.context:` | `.runwhen/templates/*` + Jinja2 `match_resource` |
| `spec.relatedResources` | IDs on primary resource in templates, or separate rules |
| snake_case (`base_name`, `output_items`) | camelCase (`baseName`, `outputItems`) |

Copy patterns from `references/examples/` — all use the real parser.

## Platform quick reference

| platform | resourceTypes examples | Bundled guide |
|----------|----------------------|---------------|
| `kubernetes` | `deployment`, `pod`, CRD `buckets.storage.gcp.upbound.io` | `indexed-resources/kubernetes.md` |
| `azure` | `azure_compute_virtual_machines`, `azure_keyvault_vaults` | `indexed-resources/azure.md` |
| `aws` | `aws_ec2_instances`, `aws_s3_buckets` | `indexed-resources/aws.md` |
| `gcp` | `gcp_compute_instances`, `gcp_storage_buckets` | `indexed-resources/gcp.md` |
| `runwhen` | `workspace` (MCP tool-builder) | `indexed-resources/runwhen-platform.md` |

## Workflow

0. **If output target is unclear**, call `list_discovery_platforms()` and ask the user
   whether they need workspace-scoped (`runwhen`) or per-resource discovery
   (`kubernetes|azure|aws|gcp`). Never guess `resourceTypes`.
1. Read `references/generation-rules-schema.md` + platform guide under `references/indexed-resources/`
2. Call `list_indexed_resource_types(platform=..., search=...)` — **only** offer types from the response
3. Grep `references/catalogs/<platform>-resource-catalog.md` for field paths when writing `matchRules`
4. Author `.runwhen/generation-rules/*.yaml` and `.runwhen/templates/*` (or use `render_codecollection_skill`)
5. Test with runwhen-local workspace builder

For workspace-scoped tool-builder tasks (`platform: runwhen`), use
**`commit-to-codecollection`** / `render_codecollection_skill`.

For discovery platforms (`kubernetes`, etc.), **`render_codecollection_skill`** emits
the correct SLX includes when you pass confirmed `platform`, `resource_types`,
`match_rules`, and `slx_qualifiers`.

## Updating the bundle (maintainers, build-time only)

From runwhen-platform-mcp CI or locally with both repos checked out:

```bash
python scripts/sync_bundled_authoring.py --runwhen-local /path/to/runwhen-local
```

Source of truth remains **runwhen-local** (`docs/authoring/` + catalog dumpers).
The bundle is refreshed on MCP release CI — not at MCP request time.

## Related skills

- `commit-to-codecollection` — GitOps bundle for tool-builder output (workspace + discovery platforms)
- `list_discovery_platforms` / `list_indexed_resource_types` — MCP catalog lookup before render
- `find-and-deploy-codebundle` — registry Skill Templates before custom rules
- `configure-hierarchy` / `configure-resource-path` — SLX metadata after render
