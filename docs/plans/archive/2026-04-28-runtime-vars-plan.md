# Script Variables Support Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add `script_vars` support to `commit_slx` (writes `scriptVarsProvided` to runbook.yaml) and `run_script_and_wait` (injects overrides as envVars at test time), with input validation and skill file documentation.

**Architecture:** A private `_validate_script_vars` helper enforces required fields on each script var dict before any PAPI call. `_build_runbook_yaml` accepts a new `script_vars` param and appends `scriptVarsProvided` to the YAML spec. `run_script_and_wait` merges `script_var_overrides` into `envVars`. Script vars are task-only — SLI builders are untouched.

**Tech Stack:** Python 3.10+, PyYAML, pytest, MCP FastMCP server

---

### Task 1: `_validate_script_vars` helper + tests

**Files:**
- Modify: `runwhen_platform_mcp/server.py` (after line 1104, near `_validate_slx_name`)
- Modify: `tests/test_validation.py` (add `TestValidateScriptVars` class)

**Step 1: Write the failing tests**

Add this class to `tests/test_validation.py`:

```python
from runwhen_platform_mcp.server import _validate_script_vars


class TestValidateScriptVars:
    """Tests for _validate_script_vars."""

    def test_empty_list_is_valid(self) -> None:
        assert _validate_script_vars([]) == []

    def test_none_is_valid(self) -> None:
        assert _validate_script_vars(None) == []

    def test_valid_regex_var(self) -> None:
        errors = _validate_script_vars([
            {
                "name": "LOG_QUERY",
                "description": "Log filter string",
                "default": "error",
                "validation": {"type": "regex", "pattern": "^.+$"},
            }
        ])
        assert errors == []

    def test_valid_enum_var(self) -> None:
        errors = _validate_script_vars([
            {
                "name": "SEVERITY",
                "description": "Severity level",
                "default": "warning",
                "validation": {"type": "enum", "values": ["debug", "warning", "error"]},
            }
        ])
        assert errors == []

    def test_missing_name(self) -> None:
        errors = _validate_script_vars([
            {"description": "x", "default": "y", "validation": {"type": "enum", "values": ["a"]}}
        ])
        assert any("name" in e for e in errors)

    def test_missing_description(self) -> None:
        errors = _validate_script_vars([
            {"name": "FOO", "default": "y", "validation": {"type": "enum", "values": ["a"]}}
        ])
        assert any("description" in e for e in errors)

    def test_missing_default(self) -> None:
        errors = _validate_script_vars([
            {"name": "FOO", "description": "x", "validation": {"type": "enum", "values": ["a"]}}
        ])
        assert any("default" in e for e in errors)

    def test_missing_validation(self) -> None:
        errors = _validate_script_vars([
            {"name": "FOO", "description": "x", "default": "y"}
        ])
        assert any("validation" in e for e in errors)

    def test_invalid_validation_type(self) -> None:
        errors = _validate_script_vars([
            {"name": "FOO", "description": "x", "default": "y", "validation": {"type": "freetext"}}
        ])
        assert any("type" in e for e in errors)

    def test_regex_missing_pattern(self) -> None:
        errors = _validate_script_vars([
            {"name": "FOO", "description": "x", "default": "y", "validation": {"type": "regex"}}
        ])
        assert any("pattern" in e for e in errors)

    def test_enum_missing_values(self) -> None:
        errors = _validate_script_vars([
            {"name": "FOO", "description": "x", "default": "y", "validation": {"type": "enum"}}
        ])
        assert any("values" in e for e in errors)

    def test_enum_empty_values(self) -> None:
        errors = _validate_script_vars([
            {
                "name": "FOO",
                "description": "x",
                "default": "y",
                "validation": {"type": "enum", "values": []},
            }
        ])
        assert any("values" in e for e in errors)

    def test_multiple_vars_one_invalid(self) -> None:
        """Errors reference the index of the invalid var."""
        errors = _validate_script_vars([
            {
                "name": "GOOD",
                "description": "x",
                "default": "y",
                "validation": {"type": "enum", "values": ["a"]},
            },
            {"name": "BAD", "default": "y", "validation": {"type": "enum", "values": ["a"]}},
        ])
        assert len(errors) == 1
        assert "script_vars[1]" in errors[0]
```

**Step 2: Run tests to verify they fail**

```bash
cd /Users/prats/Documents/work/runwhen-platform-mcp
pytest tests/test_validation.py::TestValidateScriptVars -v
```
Expected: `ImportError` or `AttributeError` — `_validate_script_vars` doesn't exist yet.

**Step 3: Implement `_validate_script_vars` in server.py**

Insert after the `_validate_slx_name` function (around line 1104):

```python
def _validate_script_vars(script_vars: list[dict] | None) -> list[str]:
    """Validate script_vars list. Returns list of error strings (empty = valid)."""
    if not script_vars:
        return []
    errors: list[str] = []
    for i, var in enumerate(script_vars):
        prefix = f"script_vars[{i}]"
        for field in ("name", "description", "default"):
            if not var.get(field):
                errors.append(f"{prefix}: '{field}' is required and must be non-empty")
        validation = var.get("validation")
        if not validation:
            errors.append(f"{prefix}: 'validation' is required")
        else:
            vtype = validation.get("type")
            if vtype not in ("regex", "enum"):
                errors.append(f"{prefix}: validation.type must be 'regex' or 'enum', got {vtype!r}")
            elif vtype == "regex" and not validation.get("pattern"):
                errors.append(f"{prefix}: validation.pattern is required when type is 'regex'")
            elif vtype == "enum" and not validation.get("values"):
                errors.append(
                    f"{prefix}: validation.values must be a non-empty list when type is 'enum'"
                )
    return errors
```

Also add `_validate_script_vars` to the imports in `tests/test_validation.py`.

**Step 4: Run tests to verify they pass**

```bash
pytest tests/test_validation.py::TestValidateScriptVars -v
```
Expected: All tests PASS.

**Step 5: Run full test suite to check for regressions**

```bash
pytest tests/ -v --ignore=tests/test_papi_live_smoke.py --ignore=tests/test_mcp_http_remote_smoke.py
```
Expected: All existing tests PASS.

**Step 6: Commit**

```bash
git add runwhen_platform_mcp/server.py tests/test_validation.py
git commit -m "feat(script-vars): add _validate_script_vars helper with required field checks"
```

---

### Task 2: `_build_runbook_yaml` changes + tests

**Files:**
- Modify: `runwhen_platform_mcp/server.py` lines 1113–1172 (`_build_runbook_yaml`)
- Modify: `tests/test_yaml_generation.py` (add tests to `TestBuildRunbookYaml`)

**Step 1: Write the failing tests**

Add these tests inside `TestBuildRunbookYaml` in `tests/test_yaml_generation.py`:

```python
def test_no_script_vars_by_default(self) -> None:
    """scriptVarsProvided should not appear when script_vars is omitted."""
    doc = self._parse()
    assert "scriptVarsProvided" not in doc["spec"]

def test_script_vars_added_to_spec(self) -> None:
    """scriptVarsProvided is written to the spec when script_vars are provided."""
    doc = self._parse(
        script_vars=[
            {
                "name": "LOG_QUERY",
                "description": "Log filter",
                "default": "error",
                "validation": {"type": "regex", "pattern": "^.+$"},
            }
        ]
    )
    svp = doc["spec"].get("scriptVarsProvided")
    assert svp is not None
    assert len(svp) == 1
    assert svp[0]["name"] == "LOG_QUERY"
    assert svp[0]["default"] == "error"
    assert svp[0]["description"] == "Log filter"
    assert svp[0]["validation"]["type"] == "regex"
    assert svp[0]["validation"]["pattern"] == "^.+$"

def test_script_vars_enum_written_correctly(self) -> None:
    doc = self._parse(
        script_vars=[
            {
                "name": "SEVERITY",
                "description": "Severity level",
                "default": "warning",
                "validation": {"type": "enum", "values": ["debug", "warning", "error"]},
            }
        ]
    )
    svp = doc["spec"]["scriptVarsProvided"]
    assert svp[0]["validation"]["values"] == ["debug", "warning", "error"]

def test_script_vars_not_in_config_provided(self) -> None:
    """Script vars must NOT appear in configProvided — only in scriptVarsProvided."""
    doc = self._parse(
        script_vars=[
            {
                "name": "LOG_QUERY",
                "description": "x",
                "default": "error",
                "validation": {"type": "regex", "pattern": "^.+$"},
            }
        ]
    )
    config_names = [c["name"] for c in doc["spec"]["configProvided"]]
    assert "LOG_QUERY" not in config_names

def test_empty_script_vars_omits_field(self) -> None:
    doc = self._parse(script_vars=[])
    assert "scriptVarsProvided" not in doc["spec"]
```

You'll also need to update the `_parse` helper in `TestBuildRunbookYaml` to pass `script_vars` through. Find it (around line 114–136) and add `script_vars=kwargs.get("script_vars")` to the `_build_runbook_yaml` call.

**Step 2: Run tests to verify they fail**

```bash
pytest tests/test_yaml_generation.py::TestBuildRunbookYaml -v
```
Expected: `TypeError` — `_build_runbook_yaml` doesn't accept `script_vars` yet.

**Step 3: Update `_build_runbook_yaml` in server.py**

Change the function signature (line 1113) to add the new param:

```python
def _build_runbook_yaml(
    workspace: str,
    slx_name: str,
    script_b64: str,
    interpreter: str,
    task_title: str,
    location: str,
    env_vars: dict[str, str] | None = None,
    secret_vars: dict[str, str] | None = None,
    codebundle_ref: str | None = None,
    script_vars: list[dict] | None = None,   # <-- add this
) -> str:
```

Then, after the `if secrets_provided:` block that sets `spec["secretsProvided"]` (around line 1155), add:

```python
    if script_vars:
        spec["scriptVarsProvided"] = [
            {
                "name": sv["name"],
                "default": sv["default"],
                "description": sv["description"],
                "validation": sv["validation"],
            }
            for sv in script_vars
        ]
```

**Step 4: Run tests to verify they pass**

```bash
pytest tests/test_yaml_generation.py::TestBuildRunbookYaml -v
```
Expected: All tests PASS.

**Step 5: Run full suite**

```bash
pytest tests/ -v --ignore=tests/test_papi_live_smoke.py --ignore=tests/test_mcp_http_remote_smoke.py
```
Expected: All tests PASS.

**Step 6: Commit**

```bash
git add runwhen_platform_mcp/server.py tests/test_yaml_generation.py
git commit -m "feat(script-vars): add scriptVarsProvided support to _build_runbook_yaml"
```

---

### Task 3: `commit_slx` tool changes + tests

**Files:**
- Modify: `runwhen_platform_mcp/server.py` lines 3062–3160 (`commit_slx` signature)
- Modify: `tests/test_yaml_generation.py` (integration-level check via `_build_runbook_yaml` already covered; add a tool-level validation test)
- Modify: `tests/test_validation.py` (add `TestCommitSlxScriptVars` for validation error path)

**Step 1: Write the failing tests**

Add to `tests/test_validation.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch
import json


class TestCommitSlxScriptVarsValidation:
    """commit_slx returns validation errors for invalid script_vars."""

    def _run(self, coro):
        import asyncio
        return asyncio.get_event_loop().run_until_complete(coro)

    @patch("runwhen_platform_mcp.server._resolve_workspace", new_callable=AsyncMock)
    def test_invalid_script_var_returns_error(self, mock_resolve) -> None:
        from runwhen_platform_mcp.server import commit_slx

        mock_resolve.return_value = "test-ws"
        result = self._run(
            commit_slx(
                slx_name="my-task",
                alias="My Task",
                statement="Things should work",
                workspace_name="test-ws",
                script="def main(): return []",
                interpreter="python",
                script_vars=[
                    # missing description and validation
                    {"name": "FOO", "default": "bar"}
                ],
            )
        )
        data = json.loads(result)
        assert "error" in data
        assert "script_vars" in data["error"].lower() or any(
            "script_vars" in str(e) for e in data.get("errors", [])
        )
```

**Step 2: Run test to verify it fails**

```bash
pytest tests/test_validation.py::TestCommitSlxScriptVarsValidation -v
```
Expected: FAIL — `commit_slx` doesn't accept `script_vars` yet.

**Step 3: Add `script_vars` parameter to `commit_slx`**

In the `commit_slx` signature (around line 3062), add after `secret_vars`:

```python
    script_vars: Annotated[
        list[dict] | None,
        Field(
            default=None,
            description=(
                "Runtime-overridable script parameters (task type only, never SLI). "
                "Each entry requires: name (str), description (str), default (str), "
                "validation (dict with type='regex'+'pattern' or type='enum'+'values')."
            ),
        ),
    ] = None,
```

In the body of `commit_slx`, after existing name validation (look for the `_validate_slx_name` call), add:

```python
    sv_errors = _validate_script_vars(script_vars)
    if sv_errors:
        return _json_response({"error": "Invalid script_vars", "errors": sv_errors})
```

Then find where `_build_runbook_yaml` is called inside `commit_slx` and add `script_vars=script_vars or []`:

```python
    runbook_yaml = _build_runbook_yaml(
        workspace=ws,
        slx_name=slx_name,
        script_b64=script_b64,
        interpreter=interpreter,
        task_title=task_title,
        location=location,
        env_vars=env_vars,
        secret_vars=secret_vars,
        codebundle_ref=codebundle_ref,
        script_vars=script_vars or [],   # <-- add this
    )
```

**Step 4: Run tests to verify they pass**

```bash
pytest tests/test_validation.py::TestCommitSlxScriptVarsValidation -v
```
Expected: PASS.

**Step 5: Run full suite**

```bash
pytest tests/ -v --ignore=tests/test_papi_live_smoke.py --ignore=tests/test_mcp_http_remote_smoke.py
```
Expected: All tests PASS.

**Step 6: Commit**

```bash
git add runwhen_platform_mcp/server.py tests/test_validation.py
git commit -m "feat(script-vars): add script_vars param to commit_slx with validation gate"
```

---

### Task 4: `run_script_and_wait` changes + tests

**Files:**
- Modify: `runwhen_platform_mcp/server.py` lines 2831–2940 (`run_script_and_wait`)
- Modify: `tests/test_validation.py` (add `TestRunScriptAndWaitScriptVarOverrides`)

**Step 1: Write the failing tests**

Add to `tests/test_validation.py`:

```python
class TestRunScriptAndWaitScriptVarOverrides:
    """script_var_overrides are merged into envVars sent to author/run."""

    def _run(self, coro):
        import asyncio
        return asyncio.get_event_loop().run_until_complete(coro)

    @patch("runwhen_platform_mcp.server._resolve_workspace", new_callable=AsyncMock)
    @patch("runwhen_platform_mcp.server._resolve_location", new_callable=AsyncMock)
    @patch("runwhen_platform_mcp.server._papi_post", new_callable=AsyncMock)
    @patch("runwhen_platform_mcp.server._papi_get", new_callable=AsyncMock)
    def test_script_var_overrides_merged_into_env_vars(
        self, mock_get, mock_post, mock_location, mock_ws
    ) -> None:
        from runwhen_platform_mcp.server import run_script_and_wait

        mock_ws.return_value = "test-ws"
        mock_location.return_value = "my-runner"
        mock_post.return_value = (200, {"runId": "run-123"})
        mock_get.side_effect = [
            {"status": "SUCCEEDED"},
            {"artifacts": []},
        ]

        self._run(
            run_script_and_wait(
                workspace_name="test-ws",
                script="def main(): return []",
                interpreter="python",
                env_vars={"NAMESPACE": "default"},
                script_var_overrides={"LOG_QUERY": "critical"},
            )
        )

        _, call_kwargs = mock_post.call_args
        body = mock_post.call_args[0][1]  # second positional arg is the body
        assert body["envVars"]["NAMESPACE"] == "default"
        assert body["envVars"]["LOG_QUERY"] == "critical"

    @patch("runwhen_platform_mcp.server._resolve_workspace", new_callable=AsyncMock)
    @patch("runwhen_platform_mcp.server._resolve_location", new_callable=AsyncMock)
    @patch("runwhen_platform_mcp.server._papi_post", new_callable=AsyncMock)
    @patch("runwhen_platform_mcp.server._papi_get", new_callable=AsyncMock)
    def test_script_var_overrides_take_precedence(
        self, mock_get, mock_post, mock_location, mock_ws
    ) -> None:
        from runwhen_platform_mcp.server import run_script_and_wait

        mock_ws.return_value = "test-ws"
        mock_location.return_value = "my-runner"
        mock_post.return_value = (200, {"runId": "run-123"})
        mock_get.side_effect = [
            {"status": "SUCCEEDED"},
            {"artifacts": []},
        ]

        self._run(
            run_script_and_wait(
                workspace_name="test-ws",
                script="def main(): return []",
                interpreter="python",
                env_vars={"LOG_QUERY": "original"},
                script_var_overrides={"LOG_QUERY": "override"},
            )
        )

        body = mock_post.call_args[0][1]
        assert body["envVars"]["LOG_QUERY"] == "override"

    @patch("runwhen_platform_mcp.server._resolve_workspace", new_callable=AsyncMock)
    @patch("runwhen_platform_mcp.server._resolve_location", new_callable=AsyncMock)
    @patch("runwhen_platform_mcp.server._papi_post", new_callable=AsyncMock)
    @patch("runwhen_platform_mcp.server._papi_get", new_callable=AsyncMock)
    def test_no_overrides_works_as_before(
        self, mock_get, mock_post, mock_location, mock_ws
    ) -> None:
        from runwhen_platform_mcp.server import run_script_and_wait

        mock_ws.return_value = "test-ws"
        mock_location.return_value = "my-runner"
        mock_post.return_value = (200, {"runId": "run-123"})
        mock_get.side_effect = [
            {"status": "SUCCEEDED"},
            {"artifacts": []},
        ]

        self._run(
            run_script_and_wait(
                workspace_name="test-ws",
                script="def main(): return []",
                interpreter="python",
                env_vars={"NAMESPACE": "prod"},
            )
        )

        body = mock_post.call_args[0][1]
        assert body["envVars"] == {"NAMESPACE": "prod"}
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/test_validation.py::TestRunScriptAndWaitScriptVarOverrides -v
```
Expected: `TypeError` — `run_script_and_wait` doesn't accept `script_var_overrides` yet.

**Step 3: Update `run_script_and_wait` in server.py**

Add to the signature (after `secret_vars`, around line 2845):

```python
    script_var_overrides: Annotated[
        dict[str, str] | None,
        Field(
            default=None,
            description=(
                "Per-run override values for script variables (name → value). "
                "Merged into envVars at test time. Overrides win on name collision."
            ),
        ),
    ] = None,
```

In the body, find where `body` is constructed (around line 2904) and replace:

```python
    body: dict[str, Any] = {
        "command": script,
        "location": location,
        "run_type": run_type,
        "interpreter": interpreter,
        "envVars": {**(env_vars or {}), **(script_var_overrides or {})},
        "secretVars": secret_vars or {},
    }
```

**Step 4: Run tests to verify they pass**

```bash
pytest tests/test_validation.py::TestRunScriptAndWaitScriptVarOverrides -v
```
Expected: All PASS.

**Step 5: Run full suite**

```bash
pytest tests/ -v --ignore=tests/test_papi_live_smoke.py --ignore=tests/test_mcp_http_remote_smoke.py
```
Expected: All tests PASS.

**Step 6: Commit**

```bash
git add runwhen_platform_mcp/server.py tests/test_validation.py
git commit -m "feat(script-vars): add script_var_overrides to run_script_and_wait, merged into envVars"
```

---

### Task 5: Skill file update

**Files:**
- Modify: `skills/build-runwhen-task/SKILL.md`

**Step 1: Add Script Variables section**

After the `## Key rules` section, insert:

```markdown
## Script Variables (Tasks only — never SLIs)

Script variables are runtime-overridable parameters that users change per individual run:
query strings, log filters, time windows, transient target names. They are distinct from
`env_vars` (infra targets like namespace/cluster) and `secret_vars` (credentials).

### Classification rules — in order

1. IF the variable identifies WHICH cluster, namespace, or named resource to connect to
   (KUBECTL_CONTEXT, NAMESPACE, *_NAME, *_CLUSTER) → use `env_vars`
2. IF the variable is a search query, filter, pattern, time window, or per-run target →
   use `script_vars`
3. IF the variable name ends in *_QUERY, *_PATTERN, *_FILTER, *_WINDOW, *_TARGET →
   use `script_vars`
4. IF unsure → use `env_vars` (safer default; script vars are opt-in)

### Using script vars in `run_script_and_wait`

Pass override values via `script_var_overrides` (merged into `envVars` at test time):

```python
run_script_and_wait(
    workspace_name="my-workspace",
    script=my_script,
    env_vars={"NAMESPACE": "backend", "KUBECTL_CONTEXT": "gke-prod"},
    secret_vars={"kubeconfig": "kubeconfig"},
    script_var_overrides={"LOG_QUERY": "critical", "TIME_WINDOW": "30m"},
)
```

### Using script vars in `commit_slx`

Pass the full schema via `script_vars`. All four fields are **required**:

```python
commit_slx(
    workspace_name="my-workspace",
    slx_name="k8s-log-grep",
    alias="Kubernetes Log Grep",
    statement="Grep pod logs for a search term",
    script=my_script,
    interpreter="python",
    task_type="task",
    env_vars={"NAMESPACE": "backend", "KUBECTL_CONTEXT": "gke-prod"},
    secret_vars={"kubeconfig": "kubeconfig"},
    script_vars=[
        {
            "name": "LOG_QUERY",
            "description": "Log search string to filter entries",
            "default": "error",
            "validation": {"type": "regex", "pattern": "^.+$"},
        },
        {
            "name": "SEVERITY",
            "description": "Minimum severity level to report",
            "default": "warning",
            "validation": {"type": "enum", "values": ["debug", "warning", "error", "critical"]},
        },
    ],
)
```

**NEVER** pass `script_vars` when `task_type="sli"` — SLIs are automated health probes
with fixed thresholds; there is no per-run override concept for SLIs.
```

**Step 2: Verify skill file renders cleanly**

```bash
cat skills/build-runwhen-task/SKILL.md | head -120
```
Expected: No broken markdown fences.

**Step 3: Commit**

```bash
git add skills/build-runwhen-task/SKILL.md
git commit -m "docs(skill): document script_vars in build-runwhen-task skill"
```
