# Script Variables Support — MCP Server Design

**Date:** 2026-04-28
**Status:** Approved

## Overview

Add `script_vars` awareness to the MCP server so agents can define and test runtime-overridable
parameters (query strings, log filters, time windows) as a first-class concept distinct from
config vars (`env_vars`) and secrets (`secret_vars`).

Script vars are **task-only** — they map to `scriptVarsProvided` in `runbook.yaml`. They are
never written to `sli.yaml` or `cron_sli.yaml`.

---

## Section 1: Data Model & Validation

Each entry in `script_vars` (passed to `commit_slx`) is a dict with these **required** fields:

| Field | Type | Rules |
|---|---|---|
| `name` | `str` | Non-empty |
| `description` | `str` | Non-empty, one-sentence UI label |
| `default` | `str` | Non-empty fallback value |
| `validation` | `dict` | Must have `type` = `"regex"` or `"enum"` |

Validation sub-rules:
- If `type == "regex"` → `pattern` (str) is required
- If `type == "enum"` → `values` (non-empty `list[str]`) is required

A private `_validate_script_vars(script_vars)` helper runs these checks and returns a list of
error strings. If any errors exist, `commit_slx` returns them immediately before touching PAPI.

For `run_script_and_wait`, `script_var_overrides` is a plain `dict[str, str]` (name → override
value). No structural validation — it is simply merged into `envVars` at call time.

---

## Section 2: `commit_slx` Changes

### New parameter
```python
script_vars: list[dict] | None = None
```

### Flow
1. Call `_validate_script_vars(script_vars)` after existing validation — return errors if any.
2. In `_build_runbook_yaml`, if `script_vars` is non-empty, add `scriptVarsProvided` to the spec.
3. Do **not** add `scriptVarsProvided` to `_build_sli_yaml` or `_build_cron_sli_yaml`.

### YAML output
```yaml
spec:
  scriptVarsProvided:
    - name: LOG_QUERY
      default: "error"
      description: "Log search string to filter entries"
      validation:
        type: regex
        pattern: "^.+$"
    - name: SEVERITY
      default: "warning"
      description: "Severity level to filter"
      validation:
        type: enum
        values: ["debug", "warning", "error", "critical"]
```

Script vars are **not** added to `configProvided` — they live exclusively in `scriptVarsProvided`.

---

## Section 3: `run_script_and_wait` Changes

### New parameter
```python
script_var_overrides: dict[str, str] | None = None
```

### Flow
Override values are merged into `envVars` before the `author/run` API body is built:
```python
merged_env_vars = {**(env_vars or {}), **(script_var_overrides or {})}
```

Sent as `envVars` in the request body — no new API field. Script var overrides take precedence
over `env_vars` on name collision (right-side merge wins).

---

## Section 4: Skill File Changes

`skills/build-runwhen-task/SKILL.md` gets a **Script Variables** section covering:

- **What they are**: runtime-overridable params (query strings, log filters, time windows) —
  distinct from `env_vars` (infra targets) and `secret_vars` (credentials)
- **When to use**: Tasks only — never SLIs
- **Classification rules** (in order):
  1. IF variable identifies a cluster/namespace/resource (KUBECTL_CONTEXT, NAMESPACE, *_NAME,
     *_CLUSTER) → use `env_vars`
  2. IF variable is a search query, filter, pattern, time window, or per-run target →
     use `script_vars`
  3. IF variable name ends in *_QUERY, *_PATTERN, *_FILTER, *_WINDOW, *_TARGET →
     use `script_vars`
  4. IF unsure → use `env_vars` (safer default; script vars are opt-in)
- **`commit_slx` usage**: pass `script_vars` as list of dicts with required fields
- **`run_script_and_wait` usage**: pass `script_var_overrides` as `{name: value}` for test runs

No other skill files require changes.
