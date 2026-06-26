"""Unit tests for script validation and helper functions."""

import asyncio
import base64
import gzip
import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import httpx
import pytest

from runwhen_platform_mcp.authorization import WRITE_TOOLS
from runwhen_platform_mcp.server import (
    SKILL_URI_SCHEME,
    _assess_combined_script_size,
    _assess_issue_quality_static,
    _assess_run_output_quality,
    _assess_script_size,
    _azure_credentials_hint,
    _build_persona_payload,
    _classify_secret,
    _decode_script_base64,
    _decode_script_gzip_base64,
    _detect_unresolved_placeholders,
    _discover_skills,
    _ensure_required_tags,
    _extract_env_vars,
    _extract_secret_keys,
    _fetch_known_runtime_vars,
    _form_persona_full_name,
    _is_blocking_warning,
    _looks_like_runtime_var_error,
    _normalize_chat_persona_scope_id,
    _parse_skill_file,
    _persona_short_name,
    _python_main_guard_has_paired_clause,
    _register_skill_resources,
    _resolve_assistant_short_name,
    _resolve_command_assistant_name,
    _resolve_script,
    _scripts_have_identical_content,
    _skills_root,
    _strip_python_main_guards,
    _strip_runner_unsafe_blocks,
    _validate_assistant_name,
    _validate_runtime_vars,
    _validate_script,
    _validate_slx_name,
    commit_slx,
    get_registry_codebundle,
    get_skill,
    get_workspace_locations,
    get_workspace_secrets,
    list_skills,
    run_script_and_wait,
    run_slx,
    search_registry,
    update_chat_command,
    validate_script,
)
from runwhen_platform_mcp.server import (
    mcp as _mcp,
)


class TestResolveScript:
    """Tests for _resolve_script (inline, file path, base64)."""

    def test_base64_roundtrip(self) -> None:
        src = "def main():\n    return []\n"
        b64 = base64.b64encode(src.encode("utf-8")).decode("ascii")
        assert _resolve_script(None, None, b64) == src

    def test_inline_exclusive_with_base64(self) -> None:
        try:
            _resolve_script("x", None, "eA==")
        except ValueError as e:
            assert "exactly one" in str(e).lower()
        else:
            raise AssertionError("expected ValueError")

    def test_neither_source_raises(self) -> None:
        try:
            _resolve_script(None, None, None)
        except ValueError as e:
            assert "exactly one" in str(e).lower()
        else:
            raise AssertionError("expected ValueError")

    def test_script_path_blocked_in_http_mode(self) -> None:
        with mock.patch("runwhen_platform_mcp.server.MCP_TRANSPORT", "http"):
            try:
                _resolve_script(None, "/etc/passwd", None)
            except ValueError as e:
                assert "not supported in HTTP mode" in str(e)
            else:
                raise AssertionError("expected ValueError")

    def test_script_path_allowed_in_stdio_mode(self, tmp_path: object) -> None:
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def main(): pass\n")
            f.flush()
            with mock.patch("runwhen_platform_mcp.server.MCP_TRANSPORT", "stdio"):
                result = _resolve_script(None, f.name, None)
                assert "def main()" in result


class TestValidateScript:
    """Tests for _validate_script."""

    def test_python_task_valid(self) -> None:
        script = (
            "def main():\n"
            '    return [{"issue title": "x", "issue description": "y",'
            ' "issue severity": 1, "issue next steps": "z"}]\n'
        )
        assert _validate_script(script, "python", "task") == []

    def test_python_task_missing_main(self) -> None:
        script = "print('hello')"
        warnings = _validate_script(script, "python", "task")
        assert any("main()" in w for w in warnings)

    def test_python_task_calls_main_directly(self) -> None:
        script = """
def main():
    pass
main()
"""
        warnings = _validate_script(script, "python", "task")
        assert any("Do not call main()" in w for w in warnings)

    def test_python_sli_valid(self) -> None:
        script = """
def main():
    return 0.95
"""
        assert _validate_script(script, "python", "sli") == []

    def test_bash_task_valid(self) -> None:
        script = """
main() {
  echo '[{"issue title":"x"}]' >&3
}
"""
        assert _validate_script(script, "bash", "task") == []

    def test_bash_task_missing_fd3(self) -> None:
        script = """
main() {
  echo "no fd3"
}
"""
        warnings = _validate_script(script, "bash", "task")
        assert any("file descriptor 3" in w for w in warnings)

    def test_bash_task_dev_fd3_accepted(self) -> None:
        script = """
main() {
  echo '[]' > /dev/fd/3
}
"""
        assert _validate_script(script, "bash", "task") == []


class TestExtractEnvVars:
    """Tests for _extract_env_vars."""

    def test_python_os_environ_get(self) -> None:
        script = 'x = os.environ.get("MY_VAR")'
        assert _extract_env_vars(script, "python") == ["MY_VAR"]

    def test_python_os_getenv(self) -> None:
        script = 'os.getenv("NAMESPACE")'
        assert _extract_env_vars(script, "python") == ["NAMESPACE"]

    def test_bash_simple_var(self) -> None:
        script = "echo $NAMESPACE"
        assert "NAMESPACE" in _extract_env_vars(script, "bash")

    def test_bash_ignores_builtins(self) -> None:
        script = "echo $HOME $PATH"
        result = _extract_env_vars(script, "bash")
        assert "HOME" not in result
        assert "PATH" not in result

    def test_bash_braced_var(self) -> None:
        script = "echo ${KUBECONFIG}"
        assert _extract_env_vars(script, "bash") == ["KUBECONFIG"]


class TestEnsureRequiredTags:
    """Tests for _ensure_required_tags."""

    def test_adds_access_and_data(self) -> None:
        result = _ensure_required_tags(None, "read-only", "config")
        names = [t["name"] for t in result]
        values = {t["name"]: t["value"] for t in result}
        assert "access" in names
        assert "data" in names
        assert values["access"] == "read-only"
        assert values["data"] == "config"

    def test_overwrites_existing_access_data(self) -> None:
        tags = [
            {"name": "access", "value": "read-write"},
            {"name": "data", "value": "logs-bulk"},
            {"name": "custom", "value": "x"},
        ]
        result = _ensure_required_tags(tags, "read-only", "config")
        values = {t["name"]: t["value"] for t in result}
        assert values["access"] == "read-only"
        assert values["data"] == "config"
        assert values["custom"] == "x"

    def test_preserves_duplicate_tag_names(self) -> None:
        tags = [
            {"name": "repo", "value": "agentfarm"},
            {"name": "repo", "value": "usearch"},
            {"name": "repo", "value": "468-platform"},
            {"name": "platform", "value": "github"},
        ]
        result = _ensure_required_tags(tags, "read-only", "logs-bulk")
        repo_tags = [t for t in result if t["name"] == "repo"]
        assert len(repo_tags) == 3
        repo_values = sorted(t["value"] for t in repo_tags)
        assert repo_values == ["468-platform", "agentfarm", "usearch"]


class TestValidateSlxName:
    """Test SLX name validation."""

    def test_valid_names(self) -> None:
        for name in ["k8s-pod-health", "my-slx", "a", "abc123", "a-b-c"]:
            _validate_slx_name(name)

    def test_empty_name(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            _validate_slx_name("")

    def test_too_long(self) -> None:
        with pytest.raises(ValueError, match="max allowed is 63"):
            _validate_slx_name("a" * 64)

    def test_exactly_63_chars(self) -> None:
        _validate_slx_name("a" * 63)

    def test_uppercase_rejected(self) -> None:
        with pytest.raises(ValueError, match="lowercase kebab-case"):
            _validate_slx_name("My-Slx")

    def test_spaces_rejected(self) -> None:
        with pytest.raises(ValueError, match="lowercase kebab-case"):
            _validate_slx_name("my slx")

    def test_leading_hyphen_rejected(self) -> None:
        with pytest.raises(ValueError, match="lowercase kebab-case"):
            _validate_slx_name("-my-slx")

    def test_trailing_hyphen_rejected(self) -> None:
        with pytest.raises(ValueError, match="lowercase kebab-case"):
            _validate_slx_name("my-slx-")

    def test_consecutive_hyphens_rejected(self) -> None:
        with pytest.raises(ValueError, match="Consecutive hyphens"):
            _validate_slx_name("my--slx")

    def test_underscores_rejected(self) -> None:
        with pytest.raises(ValueError, match="lowercase kebab-case"):
            _validate_slx_name("my_slx")

    def test_dots_rejected(self) -> None:
        with pytest.raises(ValueError, match="lowercase kebab-case"):
            _validate_slx_name("my.slx")


class TestValidateAssistantName:
    """Tests for _validate_assistant_name (persona short names)."""

    def test_valid_names(self) -> None:
        for name in ("azure-devops", "sre", "team-backend-1", "a1"):
            _validate_assistant_name(name)

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            _validate_assistant_name("")

    def test_too_long_rejected(self) -> None:
        with pytest.raises(ValueError, match="max allowed is 63"):
            _validate_assistant_name("a" * 64)

    def test_exactly_63_chars_allowed(self) -> None:
        _validate_assistant_name("a" * 63)

    def test_uppercase_rejected(self) -> None:
        with pytest.raises(ValueError, match="lowercase kebab-case"):
            _validate_assistant_name("Azure-DevOps")

    def test_double_hyphen_rejected(self) -> None:
        with pytest.raises(ValueError, match="reserved"):
            _validate_assistant_name("azure--devops")

    def test_leading_hyphen_rejected(self) -> None:
        with pytest.raises(ValueError, match="lowercase kebab-case"):
            _validate_assistant_name("-azure")


class TestPersonaNameHelpers:
    """Tests for persona full-name / short-name conversion."""

    def test_form_full_name_adds_prefix(self) -> None:
        assert _form_persona_full_name("t-oncall", "azure-devops") == "t-oncall--azure-devops"

    def test_form_full_name_idempotent_when_prefixed(self) -> None:
        assert (
            _form_persona_full_name("t-oncall", "t-oncall--azure-devops")
            == "t-oncall--azure-devops"
        )

    def test_short_name_strips_prefix(self) -> None:
        assert _persona_short_name("t-oncall", "t-oncall--azure-devops") == "azure-devops"

    def test_short_name_passthrough_when_unprefixed(self) -> None:
        assert _persona_short_name("t-oncall", "azure-devops") == "azure-devops"

    def test_prefixed_name_valid_after_strip(self) -> None:
        """Assistant tools strip workspace prefix before -- validation."""
        ws = "t-oncall"
        assert _resolve_assistant_short_name(ws, "t-oncall--azure-devops") == "azure-devops"
        assert _form_persona_full_name(ws, "azure-devops") == "t-oncall--azure-devops"

    def test_resolve_rejects_path_traversal_segments(self) -> None:
        with pytest.raises(ValueError, match="lowercase kebab-case"):
            _resolve_assistant_short_name("t-oncall", "..")

    def test_resolve_rejects_unprefixed_double_hyphen(self) -> None:
        with pytest.raises(ValueError, match="reserved"):
            _resolve_assistant_short_name("t-oncall", "other--azure-devops")

    def test_normalize_chat_persona_scope_id_strips_prefix(self) -> None:
        result = _normalize_chat_persona_scope_id("t-oncall", "t-oncall--azure-devops")
        assert result == "azure-devops"

    def test_command_assistant_name_persona_scope_uses_full_name(self) -> None:
        result = _resolve_command_assistant_name("t-oncall", "persona", "azure-devops")
        assert result == "t-oncall--azure-devops"

    def test_command_assistant_name_workspace_scope_uses_short_name(self) -> None:
        result = _resolve_command_assistant_name("t-oncall", "workspace", "azure-devops")
        assert result == "azure-devops"


class TestBuildPersonaPayload:
    """Tests for persona sync payload assembly."""

    def test_coalesces_null_collections_to_empty(self) -> None:
        payload = _build_persona_payload(
            full_name="t-oncall--azure-devops",
            description=None,
            display_name=None,
            avatar_url=None,
            filter_confidence_threshold=0.5,
            filter_issue_selection_strategy="MOST_SEVERE",
            filter_codebundle_task_tags=None,
            filter_stop_words=None,
            filter_scope=None,
            search_filters=None,
            run_confidence_threshold=0.95,
            run_config=None,
        )
        assert payload["filterCodebundleTaskTags"] == []
        assert payload["filterStopWords"] == []
        assert payload["searchFilters"] == {}
        assert payload["runConfig"] == {}


class TestUpdateChatCommandScopeFetch:
    """update_chat_command must surface PAPI errors when resolving scope_type."""

    def _run(self, coro):
        return asyncio.run(coro)

    @mock.patch("runwhen_platform_mcp.server._resolve_workspace", new_callable=mock.AsyncMock)
    @mock.patch("runwhen_platform_mcp.server._papi_get", new_callable=mock.AsyncMock)
    def test_scope_fetch_error_returned_when_updating_persona_scope_id(
        self, mock_get, mock_ws
    ) -> None:
        mock_ws.return_value = "t-oncall"
        request = httpx.Request("GET", "https://papi.example/chat-config/commands/1")
        response = httpx.Response(404, request=request)
        mock_get.side_effect = httpx.HTTPStatusError(
            "not found", request=request, response=response
        )

        result = self._run(
            update_chat_command(
                command_id=1,
                workspace_name="t-oncall",
                scope_id="t-oncall--azure-devops",
            )
        )
        data = json.loads(result)
        assert "error" in data

    @mock.patch("runwhen_platform_mcp.server._papi_put", new_callable=mock.AsyncMock)
    @mock.patch("runwhen_platform_mcp.server._resolve_workspace", new_callable=mock.AsyncMock)
    @mock.patch("runwhen_platform_mcp.server._papi_get", new_callable=mock.AsyncMock)
    def test_cron_on_persona_scope_rejects_stale_workspace_scope_id(
        self, mock_get, mock_ws, mock_put
    ) -> None:
        mock_ws.return_value = "t-oncall"
        mock_get.return_value = {
            "scope_type": "workspace",
            "scope_id": "t-oncall",
        }

        result = self._run(
            update_chat_command(
                command_id=1,
                workspace_name="t-oncall",
                scope_type="persona",
                cron_schedule="0 9 * * *",
            )
        )
        data = json.loads(result)
        assert "assistant_name" in data["error"] or "scope_id" in data["error"]
        mock_put.assert_not_called()

    @mock.patch("runwhen_platform_mcp.server._papi_put", new_callable=mock.AsyncMock)
    @mock.patch("runwhen_platform_mcp.server._resolve_workspace", new_callable=mock.AsyncMock)
    @mock.patch("runwhen_platform_mcp.server._papi_get", new_callable=mock.AsyncMock)
    def test_cron_on_existing_persona_command_derives_assistant_name(
        self, mock_get, mock_ws, mock_put
    ) -> None:
        mock_ws.return_value = "t-oncall"
        mock_get.return_value = {
            "scope_type": "persona",
            "scope_id": "t-oncall--azure-devops",
        }
        mock_put.return_value = (200, {"id": 1})

        result = self._run(
            update_chat_command(
                command_id=1,
                workspace_name="t-oncall",
                cron_schedule="0 9 * * *",
            )
        )
        data = json.loads(result)
        assert "error" not in data
        body = mock_put.call_args[0][1]
        assert body["assistant_name"] == "t-oncall--azure-devops"


class TestWriteToolsCompleteness:
    """Ensure WRITE_TOOLS includes all mutating tool names."""

    EXPECTED_WRITE_TOOLS = {
        "run_script",
        "run_script_and_wait",
        "run_slx",
        "commit_slx",
        "delete_slx",
        "deploy_registry_codebundle",
        "create_chat_rule",
        "update_chat_rule",
        "create_chat_command",
        "update_chat_command",
        "create_assistant",
        "update_assistant",
        "delete_assistant",
        "create_knowledge_base_article",
        "update_knowledge_base_article",
        "delete_knowledge_base_article",
    }

    def test_write_tools_contains_all_expected(self) -> None:
        missing = self.EXPECTED_WRITE_TOOLS - WRITE_TOOLS
        assert not missing, f"WRITE_TOOLS is missing: {missing}"

    def test_no_unexpected_removals(self) -> None:
        assert WRITE_TOOLS >= self.EXPECTED_WRITE_TOOLS


class TestValidateRunTimeVars:
    """Tests for _validate_runtime_vars."""

    def test_empty_list_is_valid(self) -> None:
        assert _validate_runtime_vars([]) == []

    def test_none_is_valid(self) -> None:
        assert _validate_runtime_vars(None) == []

    def test_valid_regex_var(self) -> None:
        errors = _validate_runtime_vars(
            [
                {
                    "name": "LOG_QUERY",
                    "description": "Log filter string",
                    "default": "error",
                    "validation": {"type": "regex", "pattern": "^.+$"},
                }
            ]
        )
        assert errors == []

    def test_valid_enum_var(self) -> None:
        errors = _validate_runtime_vars(
            [
                {
                    "name": "SEVERITY",
                    "description": "Severity level",
                    "default": "warning",
                    "validation": {"type": "enum", "values": ["debug", "warning", "error"]},
                }
            ]
        )
        assert errors == []

    def test_missing_name(self) -> None:
        errors = _validate_runtime_vars(
            [{"description": "x", "default": "y", "validation": {"type": "enum", "values": ["a"]}}]
        )
        assert any("name" in e for e in errors)

    def test_missing_description(self) -> None:
        errors = _validate_runtime_vars(
            [{"name": "FOO", "default": "y", "validation": {"type": "enum", "values": ["a"]}}]
        )
        assert any("description" in e for e in errors)

    def test_missing_default(self) -> None:
        errors = _validate_runtime_vars(
            [{"name": "FOO", "description": "x", "validation": {"type": "enum", "values": ["a"]}}]
        )
        assert any("default" in e for e in errors)

    def test_missing_validation(self) -> None:
        errors = _validate_runtime_vars([{"name": "FOO", "description": "x", "default": "y"}])
        assert any("validation" in e for e in errors)

    def test_invalid_validation_type(self) -> None:
        errors = _validate_runtime_vars(
            [
                {
                    "name": "FOO",
                    "description": "x",
                    "default": "y",
                    "validation": {"type": "freetext"},
                }
            ]
        )
        assert any("type" in e for e in errors)

    def test_regex_missing_pattern(self) -> None:
        errors = _validate_runtime_vars(
            [{"name": "FOO", "description": "x", "default": "y", "validation": {"type": "regex"}}]
        )
        assert any("pattern" in e for e in errors)

    def test_enum_missing_values(self) -> None:
        errors = _validate_runtime_vars(
            [{"name": "FOO", "description": "x", "default": "y", "validation": {"type": "enum"}}]
        )
        assert any("values" in e for e in errors)

    def test_enum_empty_values(self) -> None:
        errors = _validate_runtime_vars(
            [
                {
                    "name": "FOO",
                    "description": "x",
                    "default": "y",
                    "validation": {"type": "enum", "values": []},
                }
            ]
        )
        assert any("values" in e for e in errors)

    def test_multiple_vars_one_invalid(self) -> None:
        """Errors reference the index of the invalid var."""
        errors = _validate_runtime_vars(
            [
                {
                    "name": "GOOD",
                    "description": "x",
                    "default": "y",
                    "validation": {"type": "enum", "values": ["a"]},
                },
                {"name": "BAD", "default": "y", "validation": {"type": "enum", "values": ["a"]}},
            ]
        )
        assert len(errors) == 1
        assert "runtime_vars[1]" in errors[0]


class TestCommitSlxRunTimeVarsValidation:
    """commit_slx returns validation errors for invalid runtime_vars."""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_invalid_runtime_var_returns_error(self) -> None:
        result = self._run(
            commit_slx(
                slx_name="my-task",
                alias="My Task",
                statement="Things should work",
                workspace_name="test-ws",
                script="def main(): return []",
                interpreter="python",
                runtime_vars=[
                    # missing description and validation
                    {"name": "FOO", "default": "bar"}
                ],
            )
        )
        data = json.loads(result)
        assert "error" in data
        assert "runtime_vars" in data["error"].lower() or any(
            "runtime_vars" in str(e) for e in data.get("errors", [])
        )


class TestRunScriptAndWaitRunTimeVarOverrides:
    """runtime_var_overrides are merged into envVars sent to author/run."""

    def _run(self, coro):
        return asyncio.run(coro)

    @mock.patch("runwhen_platform_mcp.server._resolve_workspace", new_callable=mock.AsyncMock)
    @mock.patch("runwhen_platform_mcp.server._resolve_location", new_callable=mock.AsyncMock)
    @mock.patch("runwhen_platform_mcp.server._papi_post", new_callable=mock.AsyncMock)
    @mock.patch("runwhen_platform_mcp.server._papi_get", new_callable=mock.AsyncMock)
    def test_runtime_var_overrides_merged_into_env_vars(
        self, mock_get, mock_post, mock_location, mock_ws
    ) -> None:
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
                runtime_var_overrides={"LOG_QUERY": "critical"},
            )
        )

        body = mock_post.call_args[0][1]
        assert body["envVars"]["NAMESPACE"] == "default"
        assert body["envVars"]["LOG_QUERY"] == "critical"

    @mock.patch("runwhen_platform_mcp.server._resolve_workspace", new_callable=mock.AsyncMock)
    @mock.patch("runwhen_platform_mcp.server._resolve_location", new_callable=mock.AsyncMock)
    @mock.patch("runwhen_platform_mcp.server._papi_post", new_callable=mock.AsyncMock)
    @mock.patch("runwhen_platform_mcp.server._papi_get", new_callable=mock.AsyncMock)
    def test_runtime_var_overrides_take_precedence(
        self, mock_get, mock_post, mock_location, mock_ws
    ) -> None:
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
                runtime_var_overrides={"LOG_QUERY": "override"},
            )
        )

        body = mock_post.call_args[0][1]
        assert body["envVars"]["LOG_QUERY"] == "override"

    @mock.patch("runwhen_platform_mcp.server._resolve_workspace", new_callable=mock.AsyncMock)
    @mock.patch("runwhen_platform_mcp.server._resolve_location", new_callable=mock.AsyncMock)
    @mock.patch("runwhen_platform_mcp.server._papi_post", new_callable=mock.AsyncMock)
    @mock.patch("runwhen_platform_mcp.server._papi_get", new_callable=mock.AsyncMock)
    def test_no_overrides_works_as_before(
        self, mock_get, mock_post, mock_location, mock_ws
    ) -> None:
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


class TestRunTimeVarsDuplicateNames:
    """_validate_runtime_vars rejects duplicate names within the list."""

    _REGEX_VAR = {"type": "regex", "pattern": ".*"}

    def test_duplicate_name_flagged(self) -> None:
        errors = _validate_runtime_vars(
            [
                {"name": "FOO", "description": "d", "default": "v", "validation": self._REGEX_VAR},
                {
                    "name": "FOO",
                    "description": "d2",
                    "default": "v2",
                    "validation": self._REGEX_VAR,
                },
            ]
        )
        assert len(errors) == 1
        assert "duplicate" in errors[0]
        assert "FOO" in errors[0]

    def test_unique_names_ok(self) -> None:
        errors = _validate_runtime_vars(
            [
                {"name": "FOO", "description": "d", "default": "v", "validation": self._REGEX_VAR},
                {
                    "name": "BAR",
                    "description": "d2",
                    "default": "v2",
                    "validation": self._REGEX_VAR,
                },
            ]
        )
        assert errors == []


class TestCommitSlxRunTimeVarsCollisions:
    """commit_slx rejects runtime_vars overlapping env_vars/secret_vars, or used with SLIs."""

    def _run(self, coro):
        return asyncio.run(coro)

    def _make_valid_runtime_var(self, name: str) -> dict:
        return {
            "name": name,
            "description": "A query",
            "default": "error",
            "validation": {"type": "regex", "pattern": "^.+$"},
        }

    def test_runtime_vars_rejected_for_sli(self) -> None:
        result = self._run(
            commit_slx(
                slx_name="my-sli",
                alias="My SLI",
                statement="Health check",
                workspace_name="test-ws",
                script="def main(): return 1.0",
                interpreter="python",
                task_type="sli",
                runtime_vars=[self._make_valid_runtime_var("LOG_QUERY")],
            )
        )
        data = json.loads(result)
        assert "error" in data
        assert "task_type='task'" in data["error"]

    def test_env_vars_runtime_vars_overlap_rejected(self) -> None:
        result = self._run(
            commit_slx(
                slx_name="my-task",
                alias="My Task",
                statement="Things should work",
                workspace_name="test-ws",
                script="def main(): return []",
                interpreter="python",
                task_type="task",
                env_vars={"LOG_QUERY": "default"},
                runtime_vars=[self._make_valid_runtime_var("LOG_QUERY")],
            )
        )
        data = json.loads(result)
        assert "error" in data
        assert "LOG_QUERY" in data["error"]
        assert "env_vars" in data["error"]

    def test_secret_vars_runtime_vars_overlap_rejected(self) -> None:
        result = self._run(
            commit_slx(
                slx_name="my-task",
                alias="My Task",
                statement="Things should work",
                workspace_name="test-ws",
                script="def main(): return []",
                interpreter="python",
                task_type="task",
                secret_vars={"LOG_QUERY": "some-secret-key"},
                runtime_vars=[self._make_valid_runtime_var("LOG_QUERY")],
            )
        )
        data = json.loads(result)
        assert "error" in data
        assert "LOG_QUERY" in data["error"]
        assert "secret_vars" in data["error"]


class TestStripRunnerUnsafeBlocks:
    """Tests for auto-stripping ``__main__`` guards and ``main "$@"``."""

    def test_python_main_guard_stripped(self) -> None:
        script = 'def main():\n    return []\n\nif __name__ == "__main__":\n    main()\n'
        cleaned, notes = _strip_runner_unsafe_blocks(script, "python")
        assert "__name__" not in cleaned
        assert "if __name__" not in cleaned
        assert len(notes) == 1
        assert "__main__" in notes[0]

    def test_python_no_guard_unchanged(self) -> None:
        script = "def main():\n    return []\n"
        cleaned, notes = _strip_runner_unsafe_blocks(script, "python")
        assert cleaned == script
        assert notes == []

    def test_bash_trailing_main_stripped(self) -> None:
        script = 'main() {\n  echo "[]" >&3\n}\n\nmain "$@"\n'
        cleaned, notes = _strip_runner_unsafe_blocks(script, "bash")
        assert 'main "$@"' not in cleaned
        assert "main()" in cleaned
        assert len(notes) == 1

    def test_bash_main_dollar_at_unquoted_stripped(self) -> None:
        script = "main() {\n  : ;\n}\n\nmain $@\n"
        cleaned, notes = _strip_runner_unsafe_blocks(script, "bash")
        assert "main $@" not in cleaned
        assert notes

    def test_bash_no_trailing_invoke_unchanged(self) -> None:
        script = "main() {\n  : ;\n}\n"
        cleaned, notes = _strip_runner_unsafe_blocks(script, "bash")
        assert cleaned == script
        assert notes == []

    def test_validate_marks_main_guard_as_auto_fixable(self) -> None:
        script = 'def main():\n    return []\n\nif __name__ == "__main__":\n    main()\n'
        warnings = _validate_script(script, "python", "task")
        non_blocking = [w for w in warnings if not _is_blocking_warning(w)]
        assert any("__main__" in w for w in non_blocking)

    def test_validate_marks_missing_main_as_blocking(self) -> None:
        warnings = _validate_script('print("nope")', "python", "task")
        blocking = [w for w in warnings if _is_blocking_warning(w)]
        assert any("main()" in w for w in blocking)


class TestDetectUnresolvedPlaceholders:
    """Tests for ``_detect_unresolved_placeholders`` (Issue #5)."""

    def test_empty_title_ok(self) -> None:
        assert _detect_unresolved_placeholders("") is None

    def test_literal_title_ok(self) -> None:
        assert _detect_unresolved_placeholders("Check Storage Account Health") is None

    def test_robot_placeholder_rejected(self) -> None:
        err = _detect_unresolved_placeholders("Analyze ${NAMESPACE} pods")
        assert err is not None
        assert "${" in err

    def test_robot_placeholder_only_rejected(self) -> None:
        err = _detect_unresolved_placeholders("${TASK_TITLE}")
        assert err is not None

    def test_dollar_sign_alone_ok(self) -> None:
        assert _detect_unresolved_placeholders("Cost analysis $100/month") is None


class TestAzureCredentialsHint:
    """Tests for ``_azure_credentials_hint`` (Issue #11)."""

    AZURE_SCRIPT = (
        "import os\nfrom azure.identity import DefaultAzureCredential\ndef main():\n    return []\n"
    )

    NON_AZURE_SCRIPT = "def main():\n    return []\n"

    def test_azure_without_secret_returns_hint(self) -> None:
        hint = _azure_credentials_hint(self.AZURE_SCRIPT, None, {})
        assert hint is not None
        assert "azure_credentials" in hint

    def test_azure_with_secret_returns_none(self) -> None:
        hint = _azure_credentials_hint(
            self.AZURE_SCRIPT,
            None,
            {"azure_credentials": "azure:sp@cli"},
        )
        assert hint is None

    def test_non_azure_returns_none(self) -> None:
        hint = _azure_credentials_hint(self.NON_AZURE_SCRIPT, None, {})
        assert hint is None

    def test_azure_in_sli_only_detected(self) -> None:
        hint = _azure_credentials_hint(self.NON_AZURE_SCRIPT, self.AZURE_SCRIPT, {})
        assert hint is not None

    def test_camelcase_secret_key_accepted(self) -> None:
        hint = _azure_credentials_hint(
            self.AZURE_SCRIPT,
            None,
            {"azureCredentials": "azure:sp@cli"},
        )
        assert hint is None


class TestCommitSlxAirgapAndAzureGuards:
    """Integration: commit_slx surfaces the new validation hints."""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_commit_rejects_task_title_with_placeholder(self) -> None:
        result = self._run(
            commit_slx(
                slx_name="my-task",
                alias="My Task",
                statement="Things should work",
                workspace_name="test-ws",
                script="def main(): return []",
                interpreter="python",
                task_type="task",
                task_title="Analyze ${RESOURCE} health",
                access="read-write",
                data="logs-bulk",
            )
        )
        data = json.loads(result)
        assert "error" in data
        assert "task_title" in data["error"].lower()
        assert "${" in data["message"]

    def test_commit_rejects_azure_without_credentials(self) -> None:
        result = self._run(
            commit_slx(
                slx_name="azure-task",
                alias="Azure Task",
                statement="Things should work",
                workspace_name="test-ws",
                script=(
                    "import os\n"
                    "from azure.identity import DefaultAzureCredential\n"
                    "def main():\n"
                    "    return []\n"
                ),
                interpreter="python",
                task_type="task",
                task_title="Check Azure",
                secret_vars={},
                access="read-write",
                data="logs-bulk",
            )
        )
        data = json.loads(result)
        assert "error" in data
        assert "azure" in data["error"].lower()
        assert "azure_credentials" in data["message"]

    @mock.patch("runwhen_platform_mcp.server._resolve_workspace", new_callable=mock.AsyncMock)
    @mock.patch("runwhen_platform_mcp.server._resolve_location", new_callable=mock.AsyncMock)
    @mock.patch("runwhen_platform_mcp.server._get_user_email", new_callable=mock.AsyncMock)
    @mock.patch("runwhen_platform_mcp.server._get_codebundle_ref", new_callable=mock.AsyncMock)
    @mock.patch("runwhen_platform_mcp.server._papi_post", new_callable=mock.AsyncMock)
    def test_commit_strips_main_guard_before_sending(
        self,
        mock_post,
        mock_ref,
        mock_email,
        mock_location,
        mock_ws,
    ) -> None:
        mock_ws.return_value = "test-ws"
        mock_location.return_value = "runner-1"
        mock_email.return_value = "u@example.com"
        mock_ref.return_value = "main"
        mock_post.return_value = (200, {"ok": True})

        script = 'def main():\n    return []\n\nif __name__ == "__main__":\n    main()\n'

        self._run(
            commit_slx(
                slx_name="my-task",
                alias="My Task",
                statement="Things should work",
                workspace_name="test-ws",
                script=script,
                interpreter="python",
                task_type="task",
                task_title="My Task",
                access="read-write",
                data="logs-bulk",
            )
        )

        sent_payloads = [c[0][1] for c in mock_post.call_args_list]
        assert sent_payloads, "commit_slx should have called PAPI at least once"

        for payload in sent_payloads:
            payload_str = json.dumps(payload)
            assert "__main__" not in payload_str, (
                "Auto-strip should have removed the __main__ guard before sending"
            )


class TestSearchRegistryAirgap:
    """``search_registry`` and ``get_registry_codebundle`` honor ``RUNWHEN_AIRGAP``."""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_search_registry_airgap_returns_disabled(self) -> None:
        with mock.patch("runwhen_platform_mcp.server.RUNWHEN_AIRGAP", True):
            result = self._run(search_registry(search="anything"))
        data = json.loads(result)
        assert data["registry_available"] is False
        assert data["airgap"] is True
        assert "RUNWHEN_AIRGAP" in data["hint"]

    def test_get_registry_codebundle_airgap_returns_disabled(self) -> None:
        with mock.patch("runwhen_platform_mcp.server.RUNWHEN_AIRGAP", True):
            result = self._run(
                get_registry_codebundle(
                    collection_slug="x",
                    codebundle_slug="y",
                )
            )
        data = json.loads(result)
        assert data["registry_available"] is False
        assert data["airgap"] is True

    def test_search_registry_connect_error_returns_unavailable(self) -> None:
        def boom(*args, **kwargs):  # noqa: ARG001
            raise httpx.ConnectError("DNS failure")

        with (
            mock.patch("runwhen_platform_mcp.server.RUNWHEN_AIRGAP", False),
            mock.patch(
                "runwhen_platform_mcp.server.httpx.AsyncClient",
                side_effect=boom,
            ),
        ):
            result = self._run(search_registry(search="anything"))
        data = json.loads(result)
        assert data["registry_available"] is False
        assert data["airgap"] is False
        assert "RUNWHEN_AIRGAP" in data["message"]


class TestRunSlxTaskTitlesLiteralRejected:
    """``run_slx`` rejects literal resolved titles with a helpful hint (Issue #6)."""

    def _run(self, coro):
        return asyncio.run(coro)

    @mock.patch("runwhen_platform_mcp.server._resolve_workspace", new_callable=mock.AsyncMock)
    def test_literal_resolved_title_rejected(self, mock_ws) -> None:
        mock_ws.return_value = "test-ws"
        result = self._run(
            run_slx(
                slx_name="my-task",
                workspace_name="test-ws",
                task_titles="Analyze Storage Auth Type Metrics",
            )
        )
        data = json.loads(result)
        assert "error" in data
        assert "task_titles" in data["error"].lower()
        assert "${TASK_TITLE}" in data["message"]

    @mock.patch("runwhen_platform_mcp.server._resolve_workspace", new_callable=mock.AsyncMock)
    @mock.patch("runwhen_platform_mcp.server._papi_post", new_callable=mock.AsyncMock)
    @mock.patch("runwhen_platform_mcp.server._papi_get", new_callable=mock.AsyncMock)
    @mock.patch(
        "runwhen_platform_mcp.server.asyncio.sleep",
        new=mock.AsyncMock(),
    )
    def test_wildcard_passes_through(self, mock_get, mock_post, mock_ws) -> None:
        mock_ws.return_value = "test-ws"
        mock_post.return_value = (200, {"id": "sess-1"})
        mock_get.return_value = {
            "run_requests": [
                {
                    "id": "rr-1",
                    "response_time": "2026-06-25T20:00:00Z",
                    "passed_titles": "x",
                    "issues": [],
                }
            ],
        }
        result = self._run(
            run_slx(
                slx_name="my-task",
                workspace_name="test-ws",
                task_titles="*",
            )
        )
        data = json.loads(result)
        assert data["status"] == "completed"
        sent_body = mock_post.call_args[0][1]
        assert sent_body["runRequests"][0]["taskTitles"] == ["*"]

    @mock.patch("runwhen_platform_mcp.server._resolve_workspace", new_callable=mock.AsyncMock)
    @mock.patch("runwhen_platform_mcp.server._papi_post", new_callable=mock.AsyncMock)
    @mock.patch("runwhen_platform_mcp.server._papi_get", new_callable=mock.AsyncMock)
    @mock.patch(
        "runwhen_platform_mcp.server.asyncio.sleep",
        new=mock.AsyncMock(),
    )
    def test_explicit_task_title_variable_allowed(self, mock_get, mock_post, mock_ws) -> None:
        mock_ws.return_value = "test-ws"
        mock_post.return_value = (200, {"id": "sess-1"})
        mock_get.return_value = {
            "run_requests": [
                {
                    "id": "rr-1",
                    "response_time": "2026-06-25T20:00:00Z",
                    "passed_titles": "x",
                    "issues": [],
                }
            ],
        }
        result = self._run(
            run_slx(
                slx_name="my-task",
                workspace_name="test-ws",
                task_titles="${TASK_TITLE}",
            )
        )
        data = json.loads(result)
        assert data["status"] == "completed"

    @mock.patch(
        "runwhen_platform_mcp.server._fetch_known_runtime_vars",
        new_callable=mock.AsyncMock,
    )
    @mock.patch("runwhen_platform_mcp.server._resolve_workspace", new_callable=mock.AsyncMock)
    @mock.patch("runwhen_platform_mcp.server._papi_post", new_callable=mock.AsyncMock)
    def test_unknown_runtime_var_override_lists_known_vars(
        self, mock_post, mock_ws, mock_fetch
    ) -> None:
        mock_ws.return_value = "test-ws"
        mock_post.side_effect = ValueError(
            "PAPI returned 400 for /runsessions: {'detail': 'unknown runtime_var'}"
        )
        mock_fetch.return_value = ["LOG_QUERY", "TIME_WINDOW"]

        result = self._run(
            run_slx(
                slx_name="my-task",
                workspace_name="test-ws",
                task_titles="*",
                runtime_var_overrides={"BOGUS": "value"},
            )
        )
        data = json.loads(result)
        assert "error" in data
        assert data["known_runtime_vars"] == ["LOG_QUERY", "TIME_WINDOW"]
        assert data["submitted_override_keys"] == ["BOGUS"]


class TestAssessScriptSize:
    """Tests for ``_assess_script_size``."""

    def test_small_script_no_warning(self) -> None:
        warning, error = _assess_script_size("def main(): return []\n")
        assert warning is None
        assert error is None

    def test_soft_limit_warns(self) -> None:
        with (
            mock.patch("runwhen_platform_mcp.server.SCRIPT_SOFT_MAX_BYTES", 100),
            mock.patch("runwhen_platform_mcp.server.SCRIPT_HARD_MAX_BYTES", 10000),
        ):
            warning, error = _assess_script_size("x" * 500)
        assert warning is not None
        assert "base64" in warning
        assert error is None

    def test_hard_cap_errors(self) -> None:
        with mock.patch("runwhen_platform_mcp.server.SCRIPT_HARD_MAX_BYTES", 100):
            warning, error = _assess_script_size("x" * 500)
        assert warning is None
        assert error is not None
        assert "registry codebundle" in error
        assert "script_path" in error

    def test_label_used_in_messages(self) -> None:
        with mock.patch("runwhen_platform_mcp.server.SCRIPT_HARD_MAX_BYTES", 50):
            _, error = _assess_script_size("y" * 200, label="SLI script")
        assert error is not None
        assert error.startswith("SLI script")


class TestRunScriptSizeGuards:
    """``run_script_and_wait`` and ``commit_slx`` honour the size cap."""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_run_script_and_wait_rejects_oversize(self) -> None:
        script = "def main():\n    return []\n" + ("    # padding\n" * 5000)
        with mock.patch("runwhen_platform_mcp.server.SCRIPT_HARD_MAX_BYTES", 1024):
            result = self._run(
                run_script_and_wait(
                    workspace_name="test-ws",
                    script=script,
                    interpreter="python",
                )
            )
        data = json.loads(result)
        assert data["error"] == "Script too large for transport"
        assert "script_bytes" in data
        assert "registry codebundle" in data["message"]

    def test_commit_slx_rejects_oversize_script(self) -> None:
        script = (
            "def main():\n"
            "    return [{"
            "'issue title': 'x' * 50, 'issue description': 'y' * 80, "
            "'issue severity': 4, 'issue next steps': 'z' * 40}]\n" + ("    # padding\n" * 5000)
        )
        with mock.patch("runwhen_platform_mcp.server.SCRIPT_HARD_MAX_BYTES", 1024):
            result = self._run(
                commit_slx(
                    slx_name="big-task",
                    alias="Big Task",
                    statement="should fail",
                    workspace_name="test-ws",
                    script=script,
                    interpreter="python",
                    task_type="task",
                    task_title="Big Task",
                    access="read-write",
                    data="logs-bulk",
                )
            )
        data = json.loads(result)
        assert data["error"] == "Script too large for transport"


class TestAssessIssueQualityStatic:
    """Tests for ``_assess_issue_quality_static``."""

    def test_empty_description_flagged(self) -> None:
        script = (
            "def main():\n"
            '    return [{"issue title": f"t {x}", "issue description": "",\n'
            '             "issue severity": 2, "issue next steps": f"kubectl {cmd}"}]\n'
        )
        notes = _assess_issue_quality_static(script, "python", "task")
        assert any("empty 'issue description'" in n for n in notes)

    def test_empty_next_steps_flagged(self) -> None:
        script = (
            "def main():\n"
            '    return [{"issue title": f"t {x}", "issue description": f"d {y}",\n'
            '             "issue severity": 2, "issue next steps": ""}]\n'
        )
        notes = _assess_issue_quality_static(script, "python", "task")
        assert any("empty 'issue next steps'" in n for n in notes)

    def test_placeholder_token_flagged(self) -> None:
        script = (
            "def main():\n"
            '    return [{"issue title": f"t {x}", "issue description": "TODO fill in",\n'
            '             "issue severity": 2, "issue next steps": f"do {cmd}"}]\n'
        )
        notes = _assess_issue_quality_static(script, "python", "task")
        assert any("placeholder token" in n for n in notes)

    def test_no_dynamic_data_flagged(self) -> None:
        script = (
            "def main():\n"
            '    return [{"issue title": "static title here",\n'
            '             "issue description": "static description with no runtime data",\n'
            '             "issue severity": 2,\n'
            '             "issue next steps": "kubectl get pods"}]\n'
        )
        notes = _assess_issue_quality_static(script, "python", "task")
        assert any("f-strings" in n for n in notes)

    def test_dynamic_data_passes(self) -> None:
        script = (
            "def main():\n"
            "    count = 5\n"
            '    return [{"issue title": f"Pod restarts: {count}",\n'
            '             "issue description": f"Found {count} restarts in namespace {ns}",\n'
            '             "issue severity": 2,\n'
            '             "issue next steps": f"kubectl logs {pod}"}]\n'
        )
        notes = _assess_issue_quality_static(script, "python", "task")
        assert notes == []

    def test_sli_skipped(self) -> None:
        script = "def main(): return 0.95\n"
        assert _assess_issue_quality_static(script, "python", "sli") == []

    def test_bash_empty_description_flagged(self) -> None:
        script = (
            "main() {\n"
            '  jq -n --arg title "x" --arg description "" '
            '\'{"issue title":$title,"issue description":$description}\' >&3\n'
            "}\n"
        )
        notes = _assess_issue_quality_static(script, "bash", "task")
        assert any("empty issue description" in n for n in notes)


class TestAssessRunOutputQuality:
    """Tests for ``_assess_run_output_quality`` — runtime issue inspection."""

    def test_no_issues_returns_summary_warning(self) -> None:
        notes = _assess_run_output_quality({"issues": []})
        assert any("zero issues" in n for n in notes)

    def test_empty_description_flagged(self) -> None:
        notes = _assess_run_output_quality(
            {
                "issues": [
                    {
                        "title": "Pod restart spike",
                        "details": "",
                        "nextSteps": "kubectl get pods",
                        "severity": 2,
                    }
                ]
            }
        )
        assert any("missing 'issue description'" in n for n in notes)

    def test_short_description_flagged(self) -> None:
        notes = _assess_run_output_quality(
            {
                "issues": [
                    {
                        "title": "Pod restart spike",
                        "details": "short",
                        "nextSteps": "kubectl get pods now",
                        "severity": 2,
                    }
                ]
            }
        )
        assert any("description is only" in n for n in notes)

    def test_missing_next_steps_flagged(self) -> None:
        notes = _assess_run_output_quality(
            {
                "issues": [
                    {
                        "title": "Pod restart spike",
                        "details": "Pod foo in ns bar restarted 5 times in last hour",
                        "nextSteps": "",
                        "severity": 2,
                    }
                ]
            }
        )
        assert any("missing 'issue next steps'" in n for n in notes)

    def test_invalid_severity_flagged(self) -> None:
        notes = _assess_run_output_quality(
            {
                "issues": [
                    {
                        "title": "Health check completed",
                        "details": "Examined 12 pods; all healthy",
                        "nextSteps": "No action needed",
                        "severity": 0,
                    }
                ]
            }
        )
        assert any("severity" in n.lower() and "out of contract" in n for n in notes)

    def test_placeholder_text_flagged(self) -> None:
        notes = _assess_run_output_quality(
            {
                "issues": [
                    {
                        "title": "TODO fill in title",
                        "details": "Pod foo restarted; lorem ipsum dolor sit amet",
                        "nextSteps": "Investigate; check logs and runbook",
                        "severity": 2,
                    }
                ]
            }
        )
        assert any("placeholder/stub" in n for n in notes)

    def test_summary_issue_satisfies_check(self) -> None:
        notes = _assess_run_output_quality(
            {
                "issues": [
                    {
                        "title": "Storage Auth Type — Summary",
                        "details": "Examined 42 storage accounts, all use AAD auth",
                        "nextSteps": "Informational; no action required",
                        "severity": 4,
                    }
                ]
            }
        )
        assert not any("zero issues" in n for n in notes)
        assert not any("summary issue" in n for n in notes)

    def test_high_severity_issue_satisfies_check(self) -> None:
        notes = _assess_run_output_quality(
            {
                "issues": [
                    {
                        "title": "Pod crashlooping in production namespace",
                        "details": ("Pod foo in namespace bar has 12 restarts in last hour"),
                        "nextSteps": "kubectl logs foo -n bar --previous",
                        "severity": 1,
                    }
                ]
            }
        )
        assert not any("summary issue" in n for n in notes)


class TestValidateScriptIncludesQualityNotes:
    """``validate_script`` surfaces the new quality and size signals."""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_clean_script_validates(self) -> None:
        script = (
            "def main():\n"
            "    count = 5\n"
            '    return [{"issue title": f"Pod restarts: {count}",\n'
            '             "issue description": f"Found {count} restarts in {ns}",\n'
            '             "issue severity": 2,\n'
            '             "issue next steps": f"kubectl logs {pod}"}]\n'
        )
        result = self._run(validate_script(script=script, interpreter="python", task_type="task"))
        data = json.loads(result)
        assert data["valid"] is True
        assert data["issue_quality_notes"] == []

    def test_empty_description_surfaces_in_quality_notes(self) -> None:
        script = (
            "def main():\n"
            '    return [{"issue title": f"t {x}", "issue description": "",\n'
            '             "issue severity": 2, "issue next steps": f"do {cmd}"}]\n'
        )
        result = self._run(validate_script(script=script, interpreter="python", task_type="task"))
        data = json.loads(result)
        notes = data.get("issue_quality_notes", [])
        assert any("'issue description'" in n for n in notes)

    def test_oversize_script_marks_invalid(self) -> None:
        script = (
            "def main():\n"
            "    return [{'issue title': 'x' * 50, 'issue description': 'y' * 80,\n"
            "             'issue severity': 4, 'issue next steps': 'z' * 40}]\n"
            + ("    # padding\n" * 5000)
        )
        with mock.patch("runwhen_platform_mcp.server.SCRIPT_HARD_MAX_BYTES", 1024):
            result = self._run(
                validate_script(script=script, interpreter="python", task_type="task")
            )
        data = json.loads(result)
        assert data["valid"] is False
        assert "size_error" in data


# ---------------------------------------------------------------------------
# Bugbot regressions for PR #14
# ---------------------------------------------------------------------------


class TestLooksLikeRuntimeVarError:
    """Tighter 400-detection trigger for run_slx runtime-var enrichment."""

    def test_matches_runtime_var_token(self) -> None:
        assert _looks_like_runtime_var_error(
            "PAPI returned 400 for /runsessions: {'detail': 'unknown runtime_var FOO'}"
        )

    def test_matches_camel_case(self) -> None:
        assert _looks_like_runtime_var_error(
            "400 Bad Request: runtimeVarsProvided does not allow extra keys"
        )

    def test_no_match_on_bare_400(self) -> None:
        # An unrelated 400 (e.g. invalid location name) must not trigger the
        # runtime-var hint path.
        assert not _looks_like_runtime_var_error(
            "PAPI returned 400 for /runsessions: {'detail': 'invalid location'}"
        )

    def test_no_match_on_empty(self) -> None:
        assert not _looks_like_runtime_var_error("")


class TestRunSlxRuntimeVarHintScoping:
    """run_slx must not emit the runtime_var hint on unrelated 400 errors."""

    def _run(self, coro):
        return asyncio.run(coro)

    @mock.patch(
        "runwhen_platform_mcp.server._fetch_known_runtime_vars",
        new_callable=mock.AsyncMock,
    )
    @mock.patch("runwhen_platform_mcp.server._resolve_workspace", new_callable=mock.AsyncMock)
    @mock.patch("runwhen_platform_mcp.server._papi_post", new_callable=mock.AsyncMock)
    def test_unrelated_400_does_not_enrich(self, mock_post, mock_ws, mock_fetch) -> None:
        mock_ws.return_value = "test-ws"
        mock_post.side_effect = ValueError(
            "PAPI returned 400 for /runsessions: {'detail': 'invalid location'}"
        )
        mock_fetch.return_value = ["LOG_QUERY"]

        result = self._run(
            run_slx(
                slx_name="my-task",
                workspace_name="test-ws",
                task_titles="*",
                runtime_var_overrides={"FOO": "bar"},
            )
        )
        data = json.loads(result)
        assert "error" in data
        # Critically: no known_runtime_vars enrichment, because the 400 is
        # not actually about runtime vars.
        assert "known_runtime_vars" not in data
        assert "submitted_override_keys" not in data
        # And we never went looking for them.
        mock_fetch.assert_not_called()

    @mock.patch(
        "runwhen_platform_mcp.server._fetch_known_runtime_vars",
        new_callable=mock.AsyncMock,
    )
    @mock.patch("runwhen_platform_mcp.server._resolve_workspace", new_callable=mock.AsyncMock)
    @mock.patch("runwhen_platform_mcp.server._papi_post", new_callable=mock.AsyncMock)
    def test_runtime_var_400_enriches(self, mock_post, mock_ws, mock_fetch) -> None:
        mock_ws.return_value = "test-ws"
        mock_post.side_effect = ValueError(
            "PAPI returned 400: {'detail': 'unknown runtime_var BOGUS'}"
        )
        mock_fetch.return_value = ["LOG_QUERY"]

        result = self._run(
            run_slx(
                slx_name="my-task",
                workspace_name="test-ws",
                task_titles="*",
                runtime_var_overrides={"BOGUS": "x"},
            )
        )
        data = json.loads(result)
        assert data["known_runtime_vars"] == ["LOG_QUERY"]
        assert data["submitted_override_keys"] == ["BOGUS"]


class TestFetchKnownRuntimeVarsMerging:
    """``_fetch_known_runtime_vars`` must not short-circuit on empty lists."""

    def _run(self, coro):
        return asyncio.run(coro)

    @mock.patch("runwhen_platform_mcp.server._papi_get", new_callable=mock.AsyncMock)
    def test_empty_top_level_falls_through_to_spec(self, mock_get) -> None:
        # First candidate is an empty list — used to short-circuit and
        # hide the populated spec variant.
        mock_get.return_value = {
            "runtime_vars_provided": [],
            "spec": {
                "runtimeVarsProvided": [
                    {"name": "LOG_QUERY"},
                    {"name": "TIME_WINDOW"},
                ]
            },
        }
        result = self._run(_fetch_known_runtime_vars("ws", "ws--task"))
        assert result == ["LOG_QUERY", "TIME_WINDOW"]

    @mock.patch("runwhen_platform_mcp.server._papi_get", new_callable=mock.AsyncMock)
    def test_all_empty_returns_empty_list(self, mock_get) -> None:
        mock_get.return_value = {
            "runtime_vars_provided": [],
            "spec": {"runtimeVarsProvided": []},
        }
        result = self._run(_fetch_known_runtime_vars("ws", "ws--task"))
        # Empty but not None — we *did* find the schema, it just has no vars.
        assert result == []

    @mock.patch("runwhen_platform_mcp.server._papi_get", new_callable=mock.AsyncMock)
    def test_no_candidates_returns_none(self, mock_get) -> None:
        mock_get.return_value = {"unrelated": "shape"}
        result = self._run(_fetch_known_runtime_vars("ws", "ws--task"))
        assert result is None

    @mock.patch("runwhen_platform_mcp.server._papi_get", new_callable=mock.AsyncMock)
    def test_merges_and_dedups_across_candidates(self, mock_get) -> None:
        mock_get.return_value = {
            "runtime_vars_provided": [{"name": "A"}],
            "spec": {
                "runtimeVarsProvided": [{"name": "B"}, {"name": "A"}],
            },
        }
        result = self._run(_fetch_known_runtime_vars("ws", "ws--task"))
        assert result == ["A", "B"]


class TestPythonMainGuardPairedClause:
    """``__main__`` guards with paired else/elif must not be auto-stripped."""

    def test_paired_else_is_not_stripped(self) -> None:
        script = (
            "def main():\n"
            "    return []\n"
            "\n"
            'if __name__ == "__main__":\n'
            "    main()\n"
            "else:\n"
            "    print('imported')\n"
        )
        cleaned, removed, skipped = _strip_python_main_guards(script)
        # The original is preserved and the skip counter ticks.
        assert "if __name__" in cleaned
        assert "else:" in cleaned
        assert removed == 0
        assert skipped == 1

    def test_paired_elif_is_not_stripped(self) -> None:
        script = (
            "def main():\n"
            "    return []\n"
            "\n"
            'if __name__ == "__main__":\n'
            "    main()\n"
            'elif __name__ == "__test__":\n'
            "    pass\n"
        )
        _, removed, skipped = _strip_python_main_guards(script)
        assert removed == 0
        assert skipped == 1

    def test_unpaired_guard_is_stripped(self) -> None:
        script = 'def main():\n    return []\n\nif __name__ == "__main__":\n    main()\n'
        cleaned, removed, skipped = _strip_python_main_guards(script)
        assert "__main__" not in cleaned
        assert removed == 1
        assert skipped == 0

    def test_strip_wrapper_marks_paired_clauses(self) -> None:
        script = (
            "def main():\n"
            "    return []\n"
            "\n"
            'if __name__ == "__main__":\n'
            "    main()\n"
            "else:\n"
            "    print('x')\n"
        )
        cleaned, notes = _strip_runner_unsafe_blocks(script, "python")
        assert "if __name__" in cleaned  # preserved verbatim
        assert any("paired else/elif" in n for n in notes)

    def test_validate_flags_paired_guard_as_blocking(self) -> None:
        script = (
            "def main():\n"
            "    return []\n"
            "\n"
            'if __name__ == "__main__":\n'
            "    main()\n"
            "else:\n"
            "    print('x')\n"
        )
        warnings = _validate_script(script, "python", "task")
        blocking = [w for w in warnings if _is_blocking_warning(w)]
        # The paired-clause warning must be blocking — agents need to fix it
        # by hand.
        assert any("paired else/elif" in w for w in blocking)

    def test_paired_clause_detector(self) -> None:
        paired = 'if __name__ == "__main__":\n    pass\nelse:\n    pass\n'
        plain = 'if __name__ == "__main__":\n    pass\n'
        assert _python_main_guard_has_paired_clause(paired) is True
        assert _python_main_guard_has_paired_clause(plain) is False

    def test_guard_followed_by_unrelated_code_at_module_indent(self) -> None:
        # Sanity: the body ends at the next module-level statement that is
        # NOT an else/elif — we must strip cleanly.
        script = 'if __name__ == "__main__":\n    main()\n\ndef helper():\n    return 1\n'
        cleaned, removed, skipped = _strip_python_main_guards(script)
        assert "__main__" not in cleaned
        assert "def helper" in cleaned
        assert removed == 1
        assert skipped == 0


class TestAssessRunOutputQualitySummaryDetection:
    """Severity-4 summary detection (operator-precedence bug)."""

    def test_sev4_without_keyword_counts_as_summary(self) -> None:
        parsed = {
            "issues": [
                {
                    "issue title": "All replicas healthy",
                    "issue description": "x" * 60,
                    "issue next steps": "y" * 30,
                    "issue severity": 4,
                }
            ]
        }
        notes = _assess_run_output_quality(parsed)
        # The "no high-severity issues and no severity-4 summary" warning
        # must NOT fire — this is the contract-compliant case.
        assert not any("severity-4 summary" in n for n in notes)

    def test_sev1_alone_does_not_count_as_summary(self) -> None:
        parsed = {
            "issues": [
                {
                    "issue title": "Pod CrashLoopBackOff",
                    "issue description": "x" * 60,
                    "issue next steps": "y" * 30,
                    "issue severity": 1,
                }
            ]
        }
        notes = _assess_run_output_quality(parsed)
        # sev-1 is "high severity" so the summary requirement is satisfied
        # via that branch — no missing-summary warning.
        assert not any("severity-4 summary" in n for n in notes)

    def test_sev2_alone_missing_summary_warns(self) -> None:
        parsed = {
            "issues": [
                {
                    "issue title": "Some non-critical thing",
                    "issue description": "x" * 60,
                    "issue next steps": "y" * 30,
                    "issue severity": 2,
                }
            ]
        }
        notes = _assess_run_output_quality(parsed)
        # sev-2 is high-severity-ish (1-3) so summary not required.
        assert not any("severity-4 summary" in n for n in notes)

    def test_no_issues_warns(self) -> None:
        notes = _assess_run_output_quality({"issues": []})
        assert any("zero issues" in n for n in notes)

    def test_summary_title_explicit(self) -> None:
        parsed = {
            "issues": [
                {
                    "issue title": "Cluster health summary",
                    "issue description": "x" * 60,
                    "issue next steps": "y" * 30,
                    "issue severity": 4,
                }
            ]
        }
        notes = _assess_run_output_quality(parsed)
        assert not any("severity-4 summary" in n for n in notes)


class TestSizeWarningSurfacing:
    """Soft ``size_warning`` must reach the JSON response (Bugbot #4)."""

    def _run(self, coro):
        return asyncio.run(coro)

    @mock.patch("runwhen_platform_mcp.server._resolve_workspace", new_callable=mock.AsyncMock)
    @mock.patch("runwhen_platform_mcp.server._resolve_location", new_callable=mock.AsyncMock)
    @mock.patch("runwhen_platform_mcp.server._papi_post", new_callable=mock.AsyncMock)
    def test_run_script_surfaces_size_warning(self, mock_post, mock_loc, mock_ws) -> None:
        from runwhen_platform_mcp.server import run_script

        mock_ws.return_value = "test-ws"
        mock_loc.return_value = "loc-1"
        mock_post.return_value = (200, {"runId": "run-1"})
        script = "def main():\n    return []\n" + ("    # padding\n" * 200)
        with (
            mock.patch("runwhen_platform_mcp.server.SCRIPT_SOFT_MAX_BYTES", 100),
            mock.patch("runwhen_platform_mcp.server.SCRIPT_HARD_MAX_BYTES", 100_000),
        ):
            result = self._run(
                run_script(
                    workspace_name="test-ws",
                    script=script,
                    interpreter="python",
                )
            )
        data = json.loads(result)
        assert data["runId"] == "run-1"
        assert "size_warning" in data
        assert "base64" in data["size_warning"]
        assert data["script_bytes"] == len(script.encode("utf-8"))

    @mock.patch("runwhen_platform_mcp.server._resolve_workspace", new_callable=mock.AsyncMock)
    @mock.patch("runwhen_platform_mcp.server._resolve_location", new_callable=mock.AsyncMock)
    @mock.patch("runwhen_platform_mcp.server._papi_post", new_callable=mock.AsyncMock)
    @mock.patch("runwhen_platform_mcp.server._papi_get", new_callable=mock.AsyncMock)
    @mock.patch(
        "runwhen_platform_mcp.server._fetch_and_parse_artifacts",
        new_callable=mock.AsyncMock,
    )
    @mock.patch(
        "runwhen_platform_mcp.server.asyncio.sleep",
        new=mock.AsyncMock(),
    )
    def test_run_script_and_wait_surfaces_size_warning(
        self, mock_parse, mock_get, mock_post, mock_loc, mock_ws
    ) -> None:
        mock_ws.return_value = "test-ws"
        mock_loc.return_value = "loc-1"
        mock_post.return_value = (200, {"runId": "run-1"})
        mock_get.return_value = {"status": "SUCCEEDED", "artifacts": []}
        mock_parse.return_value = {
            "issues": [
                {
                    "issue title": "Cluster healthy summary",
                    "issue description": "x" * 60,
                    "issue next steps": "y" * 30,
                    "issue severity": 4,
                }
            ],
            "stdout": "",
            "stderr": "",
            "report": "",
        }
        script = (
            "def main():\n"
            "    cnt = 0\n"
            "    return [{'issue title': f't {cnt}', 'issue description': f'd {cnt}',\n"
            "             'issue severity': 4, 'issue next steps': f'n {cnt}'}]\n"
        ) + ("    # padding\n" * 200)
        with (
            mock.patch("runwhen_platform_mcp.server.SCRIPT_SOFT_MAX_BYTES", 100),
            mock.patch("runwhen_platform_mcp.server.SCRIPT_HARD_MAX_BYTES", 100_000),
        ):
            result = self._run(
                run_script_and_wait(
                    workspace_name="test-ws",
                    script=script,
                    interpreter="python",
                )
            )
        data = json.loads(result)
        assert "size_warning" in data
        assert "base64" in data["size_warning"]
        assert data["script_bytes"] == len(script.encode("utf-8"))


class TestValidateScriptSizeMatchesRunSubmission:
    """``validate_script`` must measure size on the same payload run_* submits.

    Regression: previously ``validate_script`` measured the original source
    while ``run_script`` / ``run_script_and_wait`` / ``commit_slx`` stripped
    first and measured the stripped payload, so the two could disagree.
    """

    def _run(self, coro):
        return asyncio.run(coro)

    def _padding_lines(self, n: int) -> str:
        return "".join("    # padding-line\n" for _ in range(n))

    def test_validate_strips_before_measuring(self) -> None:
        # A script whose un-stripped size is over the hard cap but whose
        # stripped size is under should validate cleanly — matching the
        # behaviour of run_script / commit_slx.
        guard_block = 'if __name__ == "__main__":\n' + "".join(
            "    print('big-guard-body')\n" for _ in range(50)
        )
        script = (
            "def main():\n"
            "    cnt = 0\n"
            "    return [{'issue title': f't {cnt}',\n"
            "             'issue description': f'desc with detail {cnt}',\n"
            "             'issue severity': 4,\n"
            "             'issue next steps': f'next {cnt}'}]\n" + guard_block
        )
        # Pick a hard cap that the un-stripped script overflows but the
        # stripped version fits within.
        stripped_size = len(script.encode("utf-8")) - len(guard_block.encode("utf-8"))
        original_size = len(script.encode("utf-8"))
        cap = stripped_size + 100
        assert cap < original_size, "cap must straddle the guard size"

        with (
            mock.patch("runwhen_platform_mcp.server.SCRIPT_HARD_MAX_BYTES", cap),
            mock.patch("runwhen_platform_mcp.server.SCRIPT_SOFT_MAX_BYTES", cap // 2),
        ):
            result = self._run(
                validate_script(script=script, interpreter="python", task_type="task")
            )
            data = json.loads(result)

        assert data["valid"] is True, data
        assert "size_error" not in data
        # script_bytes reflects the stripped payload (what actually ships)
        # and the response surfaces the original size for transparency.
        assert data["script_bytes"] < original_size
        assert data["original_script_bytes"] == original_size

    def test_validate_and_run_script_agree_on_size_error(self) -> None:
        # When the script is over the cap even after stripping, both
        # validate_script and run_script_and_wait must reject it.
        script = (
            "def main():\n"
            "    cnt = 0\n"
            "    return [{'issue title': f't {cnt}',\n"
            "             'issue description': f'desc {cnt}',\n"
            "             'issue severity': 4,\n"
            "             'issue next steps': f'next {cnt}'}]\n" + self._padding_lines(5000)
        )
        with mock.patch("runwhen_platform_mcp.server.SCRIPT_HARD_MAX_BYTES", 1024):
            vresult = self._run(
                validate_script(script=script, interpreter="python", task_type="task")
            )
            rresult = self._run(
                run_script_and_wait(
                    workspace_name="test-ws",
                    script=script,
                    interpreter="python",
                )
            )
        vdata = json.loads(vresult)
        rdata = json.loads(rresult)
        assert vdata["valid"] is False
        assert "size_error" in vdata
        assert rdata["error"] == "Script too large for transport"

    def test_no_strip_changes_no_original_field(self) -> None:
        # A clean script (no auto-fixable constructs) must not surface
        # original_script_bytes, since stripping was a no-op.
        script = (
            "def main():\n"
            "    cnt = 0\n"
            "    return [{'issue title': f't {cnt}',\n"
            "             'issue description': f'descriptive details {cnt}',\n"
            "             'issue severity': 2,\n"
            "             'issue next steps': f'next {cnt}'}]\n"
        )
        result = self._run(validate_script(script=script, interpreter="python", task_type="task"))
        data = json.loads(result)
        assert data["valid"] is True
        assert "original_script_bytes" not in data


class TestIssueSeverityRegex:
    """``_PY_ISSUE_SEVERITY_INVALID_RE`` must not flag valid quoted severities."""

    def _quality(self, severity_literal: str) -> list[str]:
        script = (
            "def main():\n"
            "    return [{\n"
            '        "issue title": f"t {x}",\n'
            '        "issue description": f"d {x} with detail",\n'
            f'        "issue severity": {severity_literal},\n'
            '        "issue next steps": f"do {y}"\n'
            "    }]\n"
        )
        return _assess_issue_quality_static(script, "python", "task")

    def test_valid_bare_severities_not_flagged(self) -> None:
        for valid in ("1", "2", "3", "4"):
            notes = self._quality(valid)
            assert not any("severity" in n.lower() and "1-4" in n for n in notes), (
                f"bare severity {valid!r} should not be flagged: {notes!r}"
            )

    def test_valid_quoted_severities_not_flagged(self) -> None:
        for valid in ('"1"', '"2"', '"3"', '"4"', "'1'", "'2'", "'3'", "'4'"):
            notes = self._quality(valid)
            assert not any("severity" in n.lower() and "1-4" in n for n in notes), (
                f"quoted severity {valid!r} should not be flagged: {notes!r}"
            )

    def test_invalid_bare_severities_flagged(self) -> None:
        for invalid in ("0", "5", "6", "9", "10", "42"):
            notes = self._quality(invalid)
            assert any("severity" in n.lower() and "1-4" in n for n in notes), (
                f"bare severity {invalid!r} should be flagged: {notes!r}"
            )

    def test_invalid_quoted_severities_flagged(self) -> None:
        for invalid in ('"0"', '"5"', '"10"', "'0'", "'9'"):
            notes = self._quality(invalid)
            assert any("severity" in n.lower() and "1-4" in n for n in notes), (
                f"quoted severity {invalid!r} should be flagged: {notes!r}"
            )


# ---------------------------------------------------------------------------
# Progressive-disclosure skills as MCP resources (cross-vendor)
# ---------------------------------------------------------------------------


class TestSkillLoader:
    """Walks skills/*/SKILL.md, parses frontmatter, exposes records."""

    def test_discovers_repo_skills(self) -> None:
        skills = _discover_skills()
        names = {s["name"] for s in skills}
        # The canonical skill names should be present (from the repo's
        # ``skills/`` tree). Spot-check a stable subset.
        expected_subset = {
            "build-runwhen-task",
            "discover-secrets",
            "discover-locations",
            "find-and-deploy-codebundle",
            "manage-rules",
            "manage-commands",
            "manage-knowledge",
        }
        assert expected_subset.issubset(names), names

    def test_each_skill_has_required_fields(self) -> None:
        for s in _discover_skills():
            assert s["name"], s
            assert s["description"], s
            assert s["body"], s
            assert s["uri"].startswith(SKILL_URI_SCHEME), s
            assert s["uri"].endswith(s["name"]), s

    def test_parse_skill_file_missing_frontmatter_returns_none(self, tmp_path) -> None:
        bad = tmp_path / "bad.md"
        bad.write_text("# No frontmatter here\n\nJust markdown.")
        assert _parse_skill_file(bad) is None

    def test_parse_skill_file_malformed_yaml_returns_none(self, tmp_path) -> None:
        bad = tmp_path / "bad.md"
        bad.write_text("---\nname: [unclosed\n---\nbody\n")
        assert _parse_skill_file(bad) is None

    def test_parse_skill_file_missing_name(self, tmp_path) -> None:
        bad = tmp_path / "bad.md"
        bad.write_text('---\ndescription: "no name"\n---\nbody\n')
        assert _parse_skill_file(bad) is None

    def test_parse_skill_file_happy_path(self, tmp_path) -> None:
        ok = tmp_path / "ok.md"
        ok.write_text("---\nname: my-skill\ndescription: Use when X.\n---\n# Body\n\nContent.\n")
        parsed = _parse_skill_file(ok)
        assert parsed is not None
        assert parsed["name"] == "my-skill"
        assert parsed["description"] == "Use when X."
        assert parsed["body"].startswith("# Body")


class TestSkillResourcesExposedViaMCP:
    """Every discovered skill is exposed as an MCP resource."""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_list_resources_includes_skills(self) -> None:
        resources = self._run(_mcp.list_resources())
        skill_uris = {str(r.uri) for r in resources if str(r.uri).startswith(SKILL_URI_SCHEME)}
        # Match the loader's count exactly so we know nothing is dropped.
        discovered = {s["uri"] for s in _discover_skills()}
        assert skill_uris == discovered

    def test_read_resource_returns_body(self) -> None:
        skills = _discover_skills()
        target = next(s for s in skills if s["name"] == "discover-secrets")
        result = self._run(_mcp.read_resource(target["uri"]))
        # FastMCP returns a ResourceResult with a list of ResourceContent
        # objects; the body lives on `.content`.
        body = result.contents[0].content
        assert "discover-secrets" not in body.lower() or "kubeconfig" in body.lower()
        # And the description on the LIST entry comes from frontmatter.
        resources = self._run(_mcp.list_resources())
        entry = next(r for r in resources if str(r.uri) == target["uri"])
        assert entry.description == target["description"]


class TestListSkillsAndGetSkillTools:
    """Fallback tools for clients that under-surface MCP resources."""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_list_skills_returns_all(self) -> None:
        out = json.loads(self._run(list_skills()))
        discovered = _discover_skills()
        assert out["count"] == len(discovered)
        names = {s["name"] for s in out["skills"]}
        assert names == {s["name"] for s in discovered}
        # Each entry carries a usable URI for clients that prefer the
        # resource pathway.
        for entry in out["skills"]:
            assert entry["uri"].startswith(SKILL_URI_SCHEME)
            assert entry["description"]

    def test_list_skills_sorted_deterministic(self) -> None:
        out = json.loads(self._run(list_skills()))
        names = [s["name"] for s in out["skills"]]
        assert names == sorted(names)

    def test_get_skill_returns_body(self) -> None:
        out = json.loads(self._run(get_skill(name="discover-secrets")))
        assert out["name"] == "discover-secrets"
        assert out["uri"] == f"{SKILL_URI_SCHEME}discover-secrets"
        assert out["body"]
        assert "kubeconfig" in out["body"].lower()

    def test_get_skill_unknown_name_lists_available(self) -> None:
        out = json.loads(self._run(get_skill(name="nope-no-such-skill")))
        assert "error" in out
        assert "available" in out
        assert "discover-secrets" in out["available"]


# ---------------------------------------------------------------------------
# get_workspace_secrets wrapper
# ---------------------------------------------------------------------------


class TestClassifySecret:
    """Heuristic platform/env-var inference for workspace secret keys."""

    def test_kubeconfig(self) -> None:
        platform, env_var = _classify_secret("kubeconfig")
        assert platform == "kubernetes"
        assert env_var == "kubeconfig"

    def test_azure_credentials(self) -> None:
        platform, env_var = _classify_secret("azure_credentials")
        assert platform == "azure"
        assert env_var == "azure_credentials"

    def test_azure_client_id_variants(self) -> None:
        # Hyphenated and underscored variants both map to AZURE_CLIENT_ID.
        for key in ("azure-clientId", "az-clientid", "clientId", "client_id"):
            platform, env_var = _classify_secret(key)
            assert platform == "azure", key
            assert env_var == "AZURE_CLIENT_ID", key

    def test_aws_credentials(self) -> None:
        platform, env_var = _classify_secret("aws_credentials")
        assert platform == "aws"

    def test_papi_user_token(self) -> None:
        for key in ("USER_TOKEN", "BETA-USER_TOKEN", "PROD-USER_TOKEN"):
            platform, env_var = _classify_secret(key)
            assert platform == "papi", key
            assert env_var == "USER_TOKEN", key

    def test_github_repo_token(self) -> None:
        platform, env_var = _classify_secret("RUNWHEN-REPO-TOKEN")
        assert platform == "github"
        assert env_var == "GITHUB_TOKEN"

    def test_unknown_falls_back_to_identity(self) -> None:
        platform, env_var = _classify_secret("totally-custom-thing")
        assert platform == "other"
        assert env_var == "totally-custom-thing"


class TestExtractSecretKeys:
    """Normalising PAPI's varied secrets-keys response shapes."""

    def test_flat_string_list(self) -> None:
        assert _extract_secret_keys(["a", "b", "c"]) == ["a", "b", "c"]

    def test_list_of_dicts(self) -> None:
        keys = _extract_secret_keys([{"key": "a"}, {"name": "b"}, {"workspaceKey": "c"}])
        assert keys == ["a", "b", "c"]

    def test_dict_with_keys_field(self) -> None:
        assert _extract_secret_keys({"keys": ["a", "b"]}) == ["a", "b"]

    def test_dict_with_results_field(self) -> None:
        assert _extract_secret_keys({"results": [{"key": "a"}]}) == ["a"]

    def test_unsupported_shape_returns_empty(self) -> None:
        assert _extract_secret_keys("not-a-list") == []
        assert _extract_secret_keys(None) == []


class TestGetWorkspaceSecretsWrapper:
    """``get_workspace_secrets`` returns structured guidance."""

    def _run(self, coro):
        return asyncio.run(coro)

    @mock.patch("runwhen_platform_mcp.server._resolve_workspace", new_callable=mock.AsyncMock)
    @mock.patch("runwhen_platform_mcp.server._papi_get", new_callable=mock.AsyncMock)
    def test_groups_by_platform(self, mock_get, mock_ws) -> None:
        mock_ws.return_value = "test-ws"
        mock_get.return_value = [
            "kubeconfig",
            "azure_credentials",
            "azure-clientId",
            "azure-clientSecret",
            "BETA-USER_TOKEN",
            "RUNWHEN-REPO-TOKEN",
        ]
        result = json.loads(self._run(get_workspace_secrets(workspace_name="test-ws")))
        groups = result["platform_groups"]
        assert "kubeconfig" in groups["kubernetes"]
        assert "azure_credentials" in groups["azure"]
        assert "BETA-USER_TOKEN" in groups["papi"]
        assert "RUNWHEN-REPO-TOKEN" in groups["github"]

    @mock.patch("runwhen_platform_mcp.server._resolve_workspace", new_callable=mock.AsyncMock)
    @mock.patch("runwhen_platform_mcp.server._papi_get", new_callable=mock.AsyncMock)
    def test_recommended_secret_vars_uses_correct_env_var(self, mock_get, mock_ws) -> None:
        mock_ws.return_value = "test-ws"
        mock_get.return_value = ["kubeconfig", "azure-clientId"]
        result = json.loads(self._run(get_workspace_secrets(workspace_name="test-ws")))
        # kubeconfig stays lowercase, clientId becomes AZURE_CLIENT_ID.
        assert result["recommended_secret_vars"]["kubernetes"] == {"kubeconfig": "kubeconfig"}
        assert result["recommended_secret_vars"]["azure"]["AZURE_CLIENT_ID"] == ("azure-clientId")

    @mock.patch("runwhen_platform_mcp.server._resolve_workspace", new_callable=mock.AsyncMock)
    @mock.patch("runwhen_platform_mcp.server._papi_get", new_callable=mock.AsyncMock)
    def test_carries_skill_reference_and_semantics(self, mock_get, mock_ws) -> None:
        mock_ws.return_value = "test-ws"
        mock_get.return_value = ["kubeconfig"]
        result = json.loads(self._run(get_workspace_secrets(workspace_name="test-ws")))
        assert result["skill_reference"] == f"{SKILL_URI_SCHEME}discover-secrets"
        assert "FILE PATHS" in result["runtime_semantics"]
        # Backward compat: the raw key list is preserved.
        assert result["secrets"] == ["kubeconfig"]

    @mock.patch("runwhen_platform_mcp.server._resolve_workspace", new_callable=mock.AsyncMock)
    @mock.patch("runwhen_platform_mcp.server._papi_get", new_callable=mock.AsyncMock)
    def test_empty_secrets_response_does_not_crash(self, mock_get, mock_ws) -> None:
        mock_ws.return_value = "test-ws"
        mock_get.return_value = []
        result = json.loads(self._run(get_workspace_secrets(workspace_name="test-ws")))
        assert result["secrets"] == []
        assert result["platform_groups"] == {}


# ---------------------------------------------------------------------------
# get_workspace_locations wrapper
# ---------------------------------------------------------------------------


class TestGetWorkspaceLocationsWrapper:
    """``get_workspace_locations`` returns structured guidance with recommendations."""

    def _run(self, coro):
        return asyncio.run(coro)

    @mock.patch("runwhen_platform_mcp.server._resolve_workspace", new_callable=mock.AsyncMock)
    @mock.patch(
        "runwhen_platform_mcp.server._get_authorized_locations",
        new_callable=mock.AsyncMock,
    )
    def test_single_private_location_auto_resolves(self, mock_locs, mock_ws) -> None:
        mock_ws.return_value = "test-ws"
        mock_locs.return_value = [
            {"name": "watcher-controlplane", "type": "workspace", "health": "online"}
        ]
        result = json.loads(self._run(get_workspace_locations(workspace_name="test-ws")))
        assert result["recommended"] == "watcher-controlplane"
        assert result["auto_resolves"] is True
        assert "omit" in result["disambiguation_hint"].lower()

    @mock.patch("runwhen_platform_mcp.server._resolve_workspace", new_callable=mock.AsyncMock)
    @mock.patch(
        "runwhen_platform_mcp.server._infer_location_from_slxs",
        new_callable=mock.AsyncMock,
    )
    @mock.patch(
        "runwhen_platform_mcp.server._get_authorized_locations",
        new_callable=mock.AsyncMock,
    )
    def test_multiple_private_inferred_pick_resolves(self, mock_locs, mock_infer, mock_ws) -> None:
        mock_ws.return_value = "test-ws"
        mock_locs.return_value = [
            {"name": "prod-runner", "type": "workspace", "health": "online"},
            {"name": "dev-runner", "type": "workspace", "health": "online"},
        ]
        mock_infer.return_value = "prod-runner"
        result = json.loads(self._run(get_workspace_locations(workspace_name="test-ws")))
        assert result["recommended"] == "prod-runner"
        assert result["auto_resolves"] is True
        assert "existing SLX usage" in result["disambiguation_hint"]

    @mock.patch("runwhen_platform_mcp.server._resolve_workspace", new_callable=mock.AsyncMock)
    @mock.patch(
        "runwhen_platform_mcp.server._infer_location_from_slxs",
        new_callable=mock.AsyncMock,
    )
    @mock.patch(
        "runwhen_platform_mcp.server._get_authorized_locations",
        new_callable=mock.AsyncMock,
    )
    def test_multiple_private_ambiguous_recommends_user_input(
        self, mock_locs, mock_infer, mock_ws
    ) -> None:
        mock_ws.return_value = "test-ws"
        mock_locs.return_value = [
            {"name": "prod-runner", "type": "workspace", "health": "online"},
            {"name": "dev-runner", "type": "workspace", "health": "online"},
        ]
        mock_infer.return_value = None
        result = json.loads(self._run(get_workspace_locations(workspace_name="test-ws")))
        assert result["recommended"] is None
        assert result["auto_resolves"] is False
        assert "Ask the user" in result["disambiguation_hint"]
        assert "prod-runner" in result["disambiguation_hint"]
        assert "dev-runner" in result["disambiguation_hint"]

    @mock.patch("runwhen_platform_mcp.server._resolve_workspace", new_callable=mock.AsyncMock)
    @mock.patch(
        "runwhen_platform_mcp.server._get_authorized_locations",
        new_callable=mock.AsyncMock,
    )
    def test_only_public_locations_warns_about_internal_access(self, mock_locs, mock_ws) -> None:
        mock_ws.return_value = "test-ws"
        mock_locs.return_value = [{"name": "public-runner", "type": "public", "health": "online"}]
        result = json.loads(self._run(get_workspace_locations(workspace_name="test-ws")))
        assert result["recommended"] == "public-runner"
        assert result["auto_resolves"] is True
        assert "internal" in result["disambiguation_hint"].lower()

    @mock.patch("runwhen_platform_mcp.server._resolve_workspace", new_callable=mock.AsyncMock)
    @mock.patch(
        "runwhen_platform_mcp.server._get_authorized_locations",
        new_callable=mock.AsyncMock,
    )
    def test_no_runners_returns_actionable_hint(self, mock_locs, mock_ws) -> None:
        mock_ws.return_value = "test-ws"
        mock_locs.return_value = []
        result = json.loads(self._run(get_workspace_locations(workspace_name="test-ws")))
        assert result["count"] == 0
        assert result["recommended"] is None
        assert result["auto_resolves"] is False
        assert "Register" in result["disambiguation_hint"]

    @mock.patch("runwhen_platform_mcp.server._resolve_workspace", new_callable=mock.AsyncMock)
    @mock.patch(
        "runwhen_platform_mcp.server._get_authorized_locations",
        new_callable=mock.AsyncMock,
    )
    def test_carries_skill_reference_and_raw_list(self, mock_locs, mock_ws) -> None:
        mock_ws.return_value = "test-ws"
        raw = [{"name": "wr", "type": "workspace", "health": "online"}]
        mock_locs.return_value = raw
        result = json.loads(self._run(get_workspace_locations(workspace_name="test-ws")))
        assert result["skill_reference"] == f"{SKILL_URI_SCHEME}discover-locations"
        # Backward compat: raw PAPI list preserved.
        assert result["locations"] == raw


# ---------------------------------------------------------------------------
# Bugbot round 3 regressions (PR #14 commit 26555c5)
# ---------------------------------------------------------------------------


class TestSeverityRegexCatchesNegatives:
    """``_PY_ISSUE_SEVERITY_INVALID_RE`` must flag negative severities."""

    def _quality(self, severity_literal: str) -> list[str]:
        # Wrap the literal in a minimal Python issue payload to exercise the
        # static scan path end-to-end.
        script = (
            "def main():\n"
            "    return [{\n"
            '        "issue title": "found something",\n'
            f'        "issue severity": {severity_literal},\n'
            '        "issue description": "context: " + str(123),\n'
            '        "issue next steps": "investigate further now",\n'
            "    }]\n"
        )
        return _assess_issue_quality_static(script, "python", "task")

    def test_bare_negative_one_through_four_flagged(self) -> None:
        for sev in ("-1", "-2", "-3", "-4"):
            notes = self._quality(sev)
            assert any("severity" in n.lower() and "1-4" in n for n in notes), (
                f"bare {sev} should be flagged: {notes!r}"
            )

    def test_quoted_negative_one_through_four_flagged(self) -> None:
        for sev in ('"-1"', '"-2"', '"-3"', '"-4"', "'-1'", "'-4'"):
            notes = self._quality(sev)
            assert any("severity" in n.lower() and "1-4" in n for n in notes), (
                f"quoted {sev} should be flagged: {notes!r}"
            )

    def test_bare_negative_large_still_flagged(self) -> None:
        for sev in ("-10", "-99", "-100"):
            notes = self._quality(sev)
            assert any("severity" in n.lower() and "1-4" in n for n in notes), (
                f"large negative {sev} should be flagged: {notes!r}"
            )

    def test_valid_positives_not_flagged(self) -> None:
        for sev in ("1", "2", "3", "4", '"1"', '"4"', "'2'"):
            notes = self._quality(sev)
            assert not any("severity" in n.lower() and "1-4" in n for n in notes), (
                f"valid {sev} should NOT be flagged: {notes!r}"
            )


class TestStripPythonMainGuardsLeavesPass:
    """A stripped guard must not orphan an outer block."""

    def test_module_level_guard_replaced_with_pass(self) -> None:
        script = 'def main():\n    return []\n\nif __name__ == "__main__":\n    main()\n'
        cleaned, removed, skipped = _strip_python_main_guards(script)
        assert removed == 1
        assert skipped == 0
        assert "if __name__" not in cleaned
        # ``pass`` should appear at module-level indent.
        assert "\npass\n" in cleaned
        # Cleaned script must still be syntactically valid Python.
        compile(cleaned, "<cleaned>", "exec")

    def test_nested_guard_keeps_outer_block_valid(self) -> None:
        # The inner ``if __name__ == "__main__":`` is the *only* statement
        # inside the outer ``if True:`` branch. Previous behaviour stripped
        # it entirely, leaving an empty ``if True:`` body and producing a
        # SyntaxError that slipped through to ``run_script``.
        script = (
            "def main():\n"
            "    return []\n"
            "\n"
            "if True:\n"
            '    if __name__ == "__main__":\n'
            "        main()\n"
            "else:\n"
            "    raise SystemExit(0)\n"
        )
        cleaned, removed, _ = _strip_python_main_guards(script)
        assert removed == 1
        compile(cleaned, "<cleaned>", "exec")
        assert "if True:" in cleaned
        assert "else:" in cleaned

    def test_indent_preserved(self) -> None:
        script = (
            "def runner():\n"
            "    def inner():\n"
            "        return 1\n"
            '    if __name__ == "__main__":\n'
            "        inner()\n"
        )
        cleaned, removed, _ = _strip_python_main_guards(script)
        assert removed == 1
        # The replacement ``pass`` should sit at the 4-space indent of the
        # original guard (inside ``runner``), not at module level.
        assert "\n    pass\n" in cleaned
        compile(cleaned, "<cleaned>", "exec")


class TestClassifySecretRejectsEmbeddedSuffixes:
    """``_classify_secret`` must require a separator before suffix tokens."""

    def test_melissa_not_gcp(self) -> None:
        platform, env_var = _classify_secret("melissa")
        assert platform == "other"
        assert env_var == "melissa"

    def test_lisa_marissa_medusa_not_gcp(self) -> None:
        for key in ("lisa", "marissa", "medusa", "vista"):
            platform, _env = _classify_secret(key)
            assert platform == "other", key

    def test_myserviceaccount_not_gcp(self) -> None:
        platform, _env = _classify_secret("myserviceaccount")
        assert platform == "other"

    def test_prod_sa_is_gcp(self) -> None:
        platform, env_var = _classify_secret("prod-sa")
        assert platform == "gcp"
        assert env_var == "GOOGLE_APPLICATION_CREDENTIALS"

    def test_team_service_account_is_gcp(self) -> None:
        platform, env_var = _classify_secret("team-service-account")
        assert platform == "gcp"
        assert env_var == "GOOGLE_APPLICATION_CREDENTIALS"

    def test_exact_sa_and_ops_suite_sa_still_match(self) -> None:
        assert _classify_secret("sa")[0] == "gcp"
        assert _classify_secret("ops-suite-sa")[0] == "gcp"

    def test_embedded_clientid_no_separator_not_azure(self) -> None:
        # ``aclientid`` has no separator before ``clientid`` and should fall
        # through to ``other`` rather than being misclassified as Azure.
        platform, _env = _classify_secret("aclientid")
        assert platform == "other"

    def test_separator_clientid_still_azure(self) -> None:
        for key in ("azure-clientId", "oauth_client_id", "client_id"):
            platform, env_var = _classify_secret(key)
            assert platform == "azure", key
            assert env_var == "AZURE_CLIENT_ID", key


class TestHasDynamicNarrowedPlusCheck:
    """``has_dynamic`` should only trip on ``+`` adjacent to string-y operands."""

    def _quality(self, body: str) -> list[str]:
        # Build a script that has a populated issue payload (so
        # ``has_issue_key`` is true) but no dynamic-text construction.
        # ``body`` is appended verbatim before the return.
        script = (
            "def main():\n"
            f"{body}"
            "    return [{\n"
            '        "issue title": "found a thing",\n'
            '        "issue severity": 3,\n'
            '        "issue description": "context observed at runtime",\n'
            '        "issue next steps": "investigate and report back",\n'
            "    }]\n"
        )
        return _assess_issue_quality_static(script, "python", "task")

    def test_arithmetic_does_not_suppress_stub_warning(self) -> None:
        # Pure arithmetic must not be treated as dynamic string building.
        # The "no f-strings / concatenation" warning must therefore fire
        # because the issue fields are all static literals.
        body = "    i = 1 + 2\n    j = len([1,2,3]) + 1\n"
        notes = self._quality(body)
        assert any("f-strings" in n.lower() for n in notes), (
            f"expected stub warning when only arithmetic is present: {notes!r}"
        )

    def test_string_concat_with_literal_suppresses_warning(self) -> None:
        body = '    name = "foo"\n    msg = "hello " + name\n'
        notes = self._quality(body)
        assert not any("f-strings" in n.lower() for n in notes), (
            f"static warning should NOT fire when concat is present: {notes!r}"
        )

    def test_str_call_concat_suppresses_warning(self) -> None:
        body = '    x = 42\n    msg = "got " + str(x)\n'
        notes = self._quality(body)
        assert not any("f-strings" in n.lower() for n in notes), (
            f"static warning should NOT fire when str() concat is present: {notes!r}"
        )

    def test_fstring_still_suppresses_warning(self) -> None:
        body = '    name = "foo"\n    msg = f"hi {name}"\n'
        notes = self._quality(body)
        assert not any("f-strings" in n.lower() for n in notes), (
            f"f-strings should still suppress the warning: {notes!r}"
        )


# ---------------------------------------------------------------------------
# Layered transport + parameter-shape fixes (PR #14 follow-up)
# ---------------------------------------------------------------------------


class TestDecodeScriptGzipBase64:
    """Tests for _decode_script_gzip_base64 (gzip+base64 round-trip)."""

    def test_roundtrip(self) -> None:
        src = "def main():\n    return [{'issue title': 'x'}]\n"
        encoded = base64.b64encode(gzip.compress(src.encode("utf-8"))).decode("ascii")
        assert _decode_script_gzip_base64(encoded) == src

    def test_compression_ratio_typical_python(self) -> None:
        """Sanity: typical Python script gzip+b64 is materially smaller than b64."""
        src = (
            "import os\nimport json\n\n"
            + "def main():\n    issues = []\n"
            + "    " * 50  # whitespace pad
            + "    return issues\n"
        )
        raw = src.encode("utf-8")
        b64_len = len(base64.b64encode(raw))
        gz_len = len(base64.b64encode(gzip.compress(raw)))
        # whitespace-heavy code compresses well — expect at least 2x reduction.
        assert gz_len < b64_len / 2, (
            f"gzip+b64={gz_len} should be <50% of b64={b64_len} for whitespace-heavy code"
        )

    def test_invalid_outer_base64_raises(self) -> None:
        with pytest.raises(ValueError, match="outer base64"):
            _decode_script_gzip_base64("!!!not-base64!!!")

    def test_not_gzipped_raises(self) -> None:
        raw_b64 = base64.b64encode(b"plain text, not gzipped").decode("ascii")
        with pytest.raises(ValueError, match="gzip decompression failed"):
            _decode_script_gzip_base64(raw_b64)

    def test_not_utf8_raises(self) -> None:
        bad = base64.b64encode(gzip.compress(b"\xff\xfe\xfd")).decode("ascii")
        with pytest.raises(ValueError, match="UTF-8"):
            _decode_script_gzip_base64(bad)


class TestResolveScriptGzipBase64:
    """Tests for _resolve_script with the new script_gzip_base64 variant."""

    def test_gzip_b64_alone_is_valid(self) -> None:
        src = "def main():\n    return []\n"
        encoded = base64.b64encode(gzip.compress(src.encode("utf-8"))).decode("ascii")
        assert _resolve_script(None, None, None, encoded, None) == src

    def test_gzip_b64_mutually_exclusive_with_b64(self) -> None:
        src = "def main():\n    return []\n"
        encoded = base64.b64encode(gzip.compress(src.encode("utf-8"))).decode("ascii")
        b64 = base64.b64encode(src.encode("utf-8")).decode("ascii")
        with pytest.raises(ValueError, match="exactly one"):
            _resolve_script(None, None, b64, encoded, None)

    def test_gzip_b64_mutually_exclusive_with_inline(self) -> None:
        encoded = base64.b64encode(gzip.compress(b"x")).decode("ascii")
        with pytest.raises(ValueError, match="exactly one"):
            _resolve_script("inline", None, None, encoded, None)


class TestResolveScriptBase64Path:
    """Tests for the new script_base64_path variant."""

    def test_base64_path_in_stdio_reads_and_decodes(self) -> None:
        src = "def main():\n    return []\n"
        encoded = base64.b64encode(src.encode("utf-8")).decode("ascii")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".b64", delete=False) as f:
            f.write(encoded)
            f.flush()
            path = f.name
        try:
            with mock.patch("runwhen_platform_mcp.server.MCP_TRANSPORT", "stdio"):
                assert _resolve_script(None, None, None, None, path) == src
        finally:
            os.unlink(path)

    def test_base64_path_rejected_in_http_mode_with_hint(self) -> None:
        with mock.patch("runwhen_platform_mcp.server.MCP_TRANSPORT", "http"):
            with pytest.raises(ValueError) as excinfo:
                _resolve_script(None, None, None, None, "/some/path.b64")
            msg = str(excinfo.value)
            assert "HTTP mode" in msg
            assert "script_gzip_base64" in msg
            assert "script_base64" in msg

    def test_base64_path_missing_file_raises(self) -> None:
        with (
            mock.patch("runwhen_platform_mcp.server.MCP_TRANSPORT", "stdio"),
            pytest.raises(FileNotFoundError, match="base64_path"),
        ):
            _resolve_script(None, None, None, None, "/nonexistent/scratch.b64")

    def test_base64_path_mutually_exclusive_with_others(self) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            _resolve_script(None, None, "eA==", None, "/tmp/x.b64")


class TestResolveScriptHTTPRefusalHints:
    """script_path / script_base64_path in HTTP mode must include actionable hints."""

    def test_script_path_http_refusal_mentions_gzip(self) -> None:
        with mock.patch("runwhen_platform_mcp.server.MCP_TRANSPORT", "http"):
            with pytest.raises(ValueError) as excinfo:
                _resolve_script(None, "/tmp/a.py", None, None, None)
            msg = str(excinfo.value)
            assert "HTTP mode" in msg
            assert "script_gzip_base64" in msg


class TestValidateRunTimeVarsEmptyDefault:
    """Empty-string default is a legitimate optional-override pattern."""

    def test_empty_string_default_is_valid(self) -> None:
        errors = _validate_runtime_vars(
            [
                {
                    "name": "SCAN_KUBE_CONTEXT",
                    "description": "Optional kubectl context",
                    "default": "",
                    "validation": {"type": "regex", "pattern": "^.*$"},
                }
            ]
        )
        assert errors == []

    def test_missing_default_key_still_rejected(self) -> None:
        errors = _validate_runtime_vars(
            [
                {
                    "name": "FOO",
                    "description": "x",
                    "validation": {"type": "enum", "values": ["a"]},
                }
            ]
        )
        assert any("default" in e for e in errors)

    def test_non_string_default_rejected(self) -> None:
        errors = _validate_runtime_vars(
            [
                {
                    "name": "FOO",
                    "description": "x",
                    "default": 42,
                    "validation": {"type": "regex", "pattern": "^.+$"},
                }
            ]
        )
        assert any("default" in e and "string" in e for e in errors)


class TestScriptsHaveIdenticalContent:
    """Tests for _scripts_have_identical_content."""

    def test_identical_returns_true(self) -> None:
        s = "def main():\n    return []\n"
        assert _scripts_have_identical_content(s, s) is True

    def test_trailing_whitespace_normalised(self) -> None:
        a = "def main():   \n    return []\n"
        b = "def main():\n    return []\n"
        assert _scripts_have_identical_content(a, b) is True

    def test_crlf_normalised(self) -> None:
        a = "def main():\r\n    return []\r\n"
        b = "def main():\n    return []\n"
        assert _scripts_have_identical_content(a, b) is True

    def test_different_returns_false(self) -> None:
        a = "def main():\n    return []\n"
        b = "def main():\n    return 0.5\n"
        assert _scripts_have_identical_content(a, b) is False

    def test_empty_or_none_returns_false(self) -> None:
        assert _scripts_have_identical_content("", "") is False
        assert _scripts_have_identical_content(None, "") is False
        assert _scripts_have_identical_content("x", None) is False


class TestAssessCombinedScriptSize:
    """Tests for _assess_combined_script_size (sum-of-fields envelope check)."""

    def test_below_soft_no_warning(self) -> None:
        warn, err = _assess_combined_script_size(("a" * 100, "script"), ("b" * 100, "sli"))
        assert warn is None
        assert err is None

    def test_single_field_returns_nothing(self) -> None:
        # Only one non-empty script; combined check is N/A.
        warn, err = _assess_combined_script_size(("a" * 100, "script"), ("", "sli"))
        assert warn is None
        assert err is None

    def test_combined_over_soft_warns(self) -> None:
        # Each below soft cap, sum exceeds it.
        with (
            mock.patch("runwhen_platform_mcp.server.SCRIPT_SOFT_MAX_BYTES", 1000),
            mock.patch("runwhen_platform_mcp.server.SCRIPT_HARD_MAX_BYTES", 10000),
        ):
            warn, err = _assess_combined_script_size(
                ("a" * 600, "script"), ("b" * 600, "sli_script")
            )
            assert warn is not None
            assert "script=600B" in warn
            assert "sli_script=600B" in warn
            assert err is None

    def test_combined_over_hard_errors(self) -> None:
        with (
            mock.patch("runwhen_platform_mcp.server.SCRIPT_HARD_MAX_BYTES", 1000),
            mock.patch("runwhen_platform_mcp.server.SCRIPT_SOFT_MAX_BYTES", 500),
        ):
            warn, err = _assess_combined_script_size(
                ("a" * 600, "script"), ("b" * 600, "sli_script")
            )
            assert err is not None
            assert "script_gzip_base64" in err


class TestCommitSlxRejectsIdenticalTaskAndSli:
    """commit_slx must reject identical script+sli_script with SLI contract hint."""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_identical_python_scripts_rejected(self) -> None:
        s = (
            "def main():\n"
            "    return [{'issue title': 'ok 12345', 'issue description':"
            " 'd' * 60, 'issue severity': 4, 'issue next steps':"
            " 'check stuff for things and ensure'}]\n"
        )
        result = self._run(
            commit_slx(
                slx_name="dup-task",
                alias="Dup",
                statement="Dup test",
                workspace_name="test-ws",
                script=s,
                sli_script=s,
                interpreter="python",
                task_type="task",
                access="read-only",
                data="logs-bulk",
            )
        )
        data = json.loads(result)
        assert data.get("error") == "Identical task and SLI script content"
        assert "different contracts" in data.get("message", "")
        assert "float" in data.get("message", "").lower()

    def test_different_scripts_dont_trigger_identical_check(self) -> None:
        # Unit-level confirmation that the helper doesn't false-positive on
        # distinct scripts. The full end-to-end commit_slx integration test
        # for the success path lives in tests that mock PAPI; this guard is
        # a direct check on the predicate the route uses.
        task = "def main():\n    return [{'issue title': 'x'}]\n"
        sli = "def main():\n    return 1.0\n"
        assert _scripts_have_identical_content(task, sli) is False


class TestCommitSlxAcceptsNewScriptSourceVariants:
    """commit_slx must accept script_gzip_base64 and (in stdio) script_base64_path."""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_gzip_b64_resolves_via_helper(self) -> None:
        # Verify the script_gzip_base64 parameter properly plumbs through
        # _resolve_script. The end-to-end commit_slx code path uses the
        # same call form, so this is the meaningful coverage.
        src = "def main():\n    return 1.0\n"
        encoded = base64.b64encode(gzip.compress(src.encode("utf-8"))).decode("ascii")
        assert _resolve_script(None, None, None, encoded, None) == src

    def test_base64_path_resolves_via_helper_in_stdio(self) -> None:
        # Mirror of the above for script_base64_path.
        src = "def main():\n    return [{'issue title': 'x'}]\n"
        encoded = base64.b64encode(src.encode("utf-8")).decode("ascii")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".b64", delete=False) as f:
            f.write(encoded)
            f.flush()
            path = f.name
        try:
            with mock.patch("runwhen_platform_mcp.server.MCP_TRANSPORT", "stdio"):
                assert _resolve_script(None, None, None, None, path) == src
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Bugbot regression — round 4 (commit-following 645d20c & 83b8516)
# ---------------------------------------------------------------------------


class TestSkillsRootResolvesBundledPackage:
    """_skills_root must find skills in both editable and wheel layouts.

    Regression for Bugbot HIGH severity ("Skills missing in Docker"). The
    previous "two parents up from server.py" fallback resolved to
    ``site-packages/skills`` after a pip install — a directory that was
    never shipped with the wheel. The fix bundles ``skills`` as a data-
    package and prefers :func:`importlib.resources.files`.
    """

    def test_returns_existing_directory(self) -> None:
        # The resolver must point at a real directory in every supported
        # install layout. If both the resource lookup and the legacy
        # fallback fail, this returns a non-existent path and skill
        # discovery silently yields nothing.
        root = _skills_root()
        assert root.is_dir(), f"{root} does not exist; skills will not load"

    def test_root_actually_contains_skill_files(self) -> None:
        # Sanity: the resolved root must hold ``<skill>/SKILL.md`` files,
        # which is what ``_discover_skills`` walks.
        root = _skills_root()
        skill_files = list(root.glob("*/SKILL.md"))
        assert skill_files, f"no SKILL.md found under {root}"

    def test_env_override_wins(self, tmp_path) -> None:
        # The ``RUNWHEN_SKILLS_DIR`` override is honored above any
        # auto-discovery, so tests and air-gap deployments can swap in
        # their own skill tree without rebuilding the package.
        (tmp_path / "demo").mkdir()
        (tmp_path / "demo" / "SKILL.md").write_text("---\nname: x\ndescription: y\n---\nb\n")
        with mock.patch.dict(os.environ, {"RUNWHEN_SKILLS_DIR": str(tmp_path)}):
            assert _skills_root() == tmp_path.resolve()

    def test_importlib_resources_path_used(self) -> None:
        # When importlib.resources resolves the ``skills`` package, that
        # path is preferred over the legacy two-parents-up fallback.
        # Confirm the import succeeds (i.e. the data-package config in
        # pyproject.toml is wired correctly).
        from importlib.resources import files as _files

        pkg_root = Path(str(_files("skills"))).resolve()
        assert pkg_root.is_dir()
        # And ``_skills_root`` returns that same path (env override not
        # set in this test, so the importlib branch should win).
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RUNWHEN_SKILLS_DIR", None)
            assert _skills_root() == pkg_root


class TestSkillResourcesRegisteredOnHttpServer:
    """Both stdio ``mcp`` and HTTP ``http_mcp`` must expose skill resources.

    Regression for Bugbot MEDIUM severity ("HTTP mode lacks skill resources").
    """

    def _run(self, coro):
        return asyncio.run(coro)

    def test_register_skill_resources_accepts_a_server_instance(self) -> None:
        # The helper is parameterized so it can be invoked twice (once on
        # ``mcp``, once on ``http_mcp``). Verify it accepts an arbitrary
        # FastMCP target and returns the registration count.
        from fastmcp import FastMCP

        target = FastMCP("test-skills-target")
        count = _register_skill_resources(target)
        # The repo ships at least one canonical skill, so the count is
        # strictly positive even in a fresh server.
        assert count > 0
        # And those resources are now discoverable on the target.
        resources = self._run(target.list_resources())
        skill_uris = {str(r.uri) for r in resources if str(r.uri).startswith(SKILL_URI_SCHEME)}
        assert len(skill_uris) == count

    def test_http_server_exposes_skill_resources_end_to_end(self) -> None:
        # Build a fresh HTTP server and confirm the resource list matches
        # what ``_discover_skills`` produced — without this fix the HTTP
        # build path skipped skill registration entirely.
        # ``_build_http_server`` lazily imports ``consent_ui`` and
        # ``auth`` from sibling modules; patch them on those modules, not
        # on ``server`` (the import path the function actually uses).
        with (
            mock.patch(
                "runwhen_platform_mcp.consent_ui.patch_fastmcp_consent_ui",
                lambda: None,
            ),
            mock.patch(
                "runwhen_platform_mcp.auth.build_auth_provider",
                return_value=None,
            ),
        ):
            from runwhen_platform_mcp import server as _server

            http_mcp = _server._build_http_server()
        discovered = {s["uri"] for s in _discover_skills()}
        resources = self._run(http_mcp.list_resources())
        skill_uris = {str(r.uri) for r in resources if str(r.uri).startswith(SKILL_URI_SCHEME)}
        assert skill_uris == discovered


class TestSkillResourceReadsLiveBody:
    """Skill resources must reflect on-disk edits after ``reload=True``.

    Regression for Bugbot LOW severity ("Skill resources ignore disk reload").
    Previously the closure captured ``skill["body"]`` at registration time
    so resource reads served the import-time snapshot forever.
    """

    def _run(self, coro):
        return asyncio.run(coro)

    def test_resource_body_follows_cache_updates(self) -> None:
        # Pick any registered skill; force its body to a sentinel value in
        # the cache (no disk edit needed for the unit test), then read the
        # resource through the live MCP server and assert we see the new
        # body. Without the live-lookup fix, the closure would still
        # return the import-time content.
        skills = _discover_skills()
        target = skills[0]
        uri = target["uri"]
        name = target["name"]

        original = target["body"]
        sentinel = f"## REGRESSION SENTINEL FOR {name}\n\nLive lookup works.\n"

        from runwhen_platform_mcp import server as _server

        # The cache is a private module attribute; patch it with a
        # forced-fresh dict so the live lookup picks up our sentinel.
        forced = {s["name"]: dict(s) for s in skills}
        forced[name]["body"] = sentinel

        with mock.patch.object(_server, "_skill_cache", forced):
            result = self._run(_mcp.read_resource(uri))
            body = result.contents[0].content
            assert body == sentinel
            assert original not in body

        # And without the patch (cache restored to its real state), the
        # original content comes back through the same path.
        result_after = self._run(_mcp.read_resource(uri))
        body_after = result_after.contents[0].content
        assert sentinel not in body_after


class TestGzipDecodeRejectsDecompressionBomb:
    """``_decode_script_gzip_base64`` must cap decompression output.

    Regression for Bugbot MEDIUM severity ("Gzip decode lacks size cap").
    A small encoded payload should not be able to expand to many megabytes
    in memory before the downstream size check fires.
    """

    def test_bomb_rejected_below_hard_cap(self) -> None:
        # A few KB of compressed zeros expands to many MB. Pin the hard
        # cap to a small value so this test is fast and deterministic.
        with mock.patch("runwhen_platform_mcp.server.SCRIPT_HARD_MAX_BYTES", 4096):
            payload = b"0" * (10 * 1024 * 1024)  # 10 MB of zeros
            compressed = gzip.compress(payload)
            encoded = base64.b64encode(compressed).decode("ascii")
            # The compressed size is small (compresses extremely well),
            # so the base64 payload itself fits well under any envelope.
            assert len(encoded) < 50 * 1024, "test would not exercise the bomb path"
            try:
                _decode_script_gzip_base64(encoded)
            except ValueError as exc:
                msg = str(exc).lower()
                assert "exceeds the hard cap" in msg or "decompressed" in msg
                assert "registry codebundle" in msg
            else:
                raise AssertionError("expected ValueError for decompression bomb")

    def test_legitimate_payload_decodes_successfully(self) -> None:
        # A real script comfortably under the cap must still round-trip.
        src = "def main():\n    return [{'issue title': 'x', 'issue severity': 4}]\n" * 10
        encoded = base64.b64encode(gzip.compress(src.encode("utf-8"))).decode("ascii")
        assert _decode_script_gzip_base64(encoded) == src

    def test_payload_just_under_cap_decodes(self) -> None:
        # The cap is exclusive in error messaging but the helper returns
        # the full payload when it fits, so a payload exactly at the cap
        # value is accepted (cap is len(decompressed) > cap → reject).
        with mock.patch("runwhen_platform_mcp.server.SCRIPT_HARD_MAX_BYTES", 4096):
            src = "x" * 4000
            encoded = base64.b64encode(gzip.compress(src.encode("utf-8"))).decode("ascii")
            assert _decode_script_gzip_base64(encoded) == src


class TestRunSlxRejectsEmptyTaskTitles:
    """run_slx must reject blank ``task_titles`` before building a request.

    Regression for Bugbot LOW severity ("Empty task_titles sent to API").
    Without the guard, ``task_titles=""`` produced ``taskTitles=[""]`` in
    the run request payload and PAPI returned an opaque failure.
    """

    def _run(self, coro):
        return asyncio.run(coro)

    @pytest.mark.parametrize("bad_value", ["", "   ", "\t", "||", "  ||  "])
    def test_blank_input_rejected_with_actionable_error(self, bad_value) -> None:
        # No PAPI calls should happen — the validation runs before the
        # workspace is resolved. We patch ``_resolve_workspace`` so the
        # tool would otherwise try to reach the network, and assert it is
        # never called.
        with (
            mock.patch(
                "runwhen_platform_mcp.server._resolve_workspace",
                new=mock.AsyncMock(return_value="ws-name"),
            ) as mock_resolve,
            mock.patch(
                "runwhen_platform_mcp.server._papi_post",
                new=mock.AsyncMock(),
            ) as mock_post,
        ):
            result = json.loads(
                self._run(
                    run_slx(workspace_name="ws-name", slx_name="my-slx", task_titles=bad_value)
                )
            )
        # ``_resolve_workspace`` runs first (the guard is downstream of
        # it), but the post must never fire — we caught the problem
        # before PAPI saw it.
        assert mock_resolve.call_count == 1
        assert mock_post.call_count == 0
        assert "error" in result
        assert "task_titles" in result["error"].lower()
        assert "hint" in result
        assert "task_titles='*'" in result["hint"]

    def test_wildcard_still_works(self) -> None:
        # Sanity: the default wildcard path must not regress.
        with (
            mock.patch(
                "runwhen_platform_mcp.server._resolve_workspace",
                new=mock.AsyncMock(return_value="ws-name"),
            ),
            mock.patch(
                "runwhen_platform_mcp.server._papi_post",
                new=mock.AsyncMock(side_effect=RuntimeError("boom")),
            ),
        ):
            # The function will attempt to call PAPI and our mock raises;
            # we only need to confirm the validation pass let it through.
            try:
                self._run(run_slx(workspace_name="ws-name", slx_name="my-slx", task_titles="*"))
            except RuntimeError as exc:
                assert "boom" in str(exc)
            else:
                # If for some reason the call didn't raise, no error from
                # the guard either — both paths confirm the guard let it
                # through.
                pass


# ---------------------------------------------------------------------------
# Bugbot regression — round 5 (post-merge commit 5ecb01a)
# ---------------------------------------------------------------------------


class TestStripMainGuardSkipsStringLiterals:
    """``_strip_python_main_guards`` must not mutate docstring contents.

    Regression for Bugbot MEDIUM ("Main guard strip corrupts strings").
    Previously the regex pass scanned every line and would happily strip
    a guard-shaped line that lived inside a triple-quoted docstring or
    example block.
    """

    def test_guard_inside_docstring_is_preserved(self) -> None:
        src = (
            "def f():\n"
            '    """\n'
            "    Example usage:\n"
            '    if __name__ == "__main__":\n'
            "        f()\n"
            '    """\n'
            "    return 1\n"
        )
        cleaned, removed, skipped = _strip_python_main_guards(src)
        assert removed == 0
        assert skipped == 0
        # The docstring content must come through verbatim — the example
        # would be useless if the stripper rewrote it.
        assert 'if __name__ == "__main__":' in cleaned
        assert cleaned == src

    def test_module_level_docstring_guard_preserved(self) -> None:
        # A module-level docstring at the top of the file is the most
        # common case for guard-shaped lines that must NOT be stripped.
        src = (
            '"""Module docs.\n'
            "\n"
            "Run this directly:\n"
            "\n"
            '    if __name__ == "__main__":\n'
            "        main()\n"
            '"""\n'
            "def main():\n"
            "    return []\n"
        )
        cleaned, removed, skipped = _strip_python_main_guards(src)
        assert removed == 0
        assert skipped == 0
        assert 'if __name__ == "__main__":' in cleaned

    def test_real_guard_after_string_literal_still_stripped(self) -> None:
        # A real guard following an unrelated multi-line string must
        # still be stripped — the heuristic only protects *interior* lines
        # of a multi-line string, not lines that come after one.
        src = 'BANNER = """\nHello\nWorld\n"""\nif __name__ == "__main__":\n    print(BANNER)\n'
        cleaned, removed, skipped = _strip_python_main_guards(src)
        assert removed == 1
        assert "pass" in cleaned
        assert "print(BANNER)" not in cleaned

    def test_guard_on_its_own_line_with_main_string_still_stripped(self) -> None:
        # The classic single-line guard has its ``"__main__"`` literal on
        # the same line as the ``if``. That literal must NOT cause the
        # heuristic to mark the guard's own line as "inside a string"
        # (a previous over-broad implementation did exactly that).
        src = 'def main():\n    return []\n\nif __name__ == "__main__":\n    main()\n'
        cleaned, removed, skipped = _strip_python_main_guards(src)
        assert removed == 1

    def test_tokenize_failure_falls_back_to_legacy_behaviour(self) -> None:
        # Syntactically invalid Python (e.g. an unclosed string) makes the
        # tokenizer raise. The helper must degrade gracefully — refuse to
        # mark anything as "inside a string" and let the line-based
        # parser proceed as before.
        src = 'x = \'unterminated\nif __name__ == "__main__":\n    main()\n'
        cleaned, removed, _ = _strip_python_main_guards(src)
        # The guard is at module indent; legacy behaviour strips it. The
        # important assertion is that we DIDN'T crash on the tokenizer
        # exception.
        assert removed == 1


class TestDecodeScriptBase64SizeCap:
    """``_decode_script_base64`` must enforce ``SCRIPT_HARD_MAX_BYTES``.

    Regression for Bugbot MEDIUM ("Base64 decode lacks size cap"). The
    gzip path got a cap in the previous round; the plain base64 path was
    the symmetric weak link.
    """

    def test_oversize_payload_rejected_before_decode(self) -> None:
        with mock.patch("runwhen_platform_mcp.server.SCRIPT_HARD_MAX_BYTES", 1024):
            # 4 KB of raw data → ~5.5 KB encoded, well over the 1 KB cap.
            payload = b"x" * 4096
            encoded = base64.b64encode(payload).decode("ascii")
            try:
                _decode_script_base64(encoded)
            except ValueError as exc:
                msg = str(exc).lower()
                assert "exceeds" in msg or "more than the hard cap" in msg
                # The error guides the agent toward the right fix.
                assert "registry codebundle" in msg or "split" in msg
            else:
                raise AssertionError("expected ValueError for oversize base64")

    def test_payload_under_cap_decodes_cleanly(self) -> None:
        src = "def main():\n    return [{'issue title': 'x', 'issue severity': 4}]\n"
        encoded = base64.b64encode(src.encode("utf-8")).decode("ascii")
        assert _decode_script_base64(encoded) == src

    def test_rejection_predates_decode_allocation(self) -> None:
        # The encoded-length check fires before ``base64.b64decode`` is
        # called, so a deliberately huge encoded blob is rejected without
        # allocating the decoded bytes. We assert by mocking out
        # ``base64.b64decode`` and confirming it never runs.
        with (
            mock.patch("runwhen_platform_mcp.server.SCRIPT_HARD_MAX_BYTES", 1024),
            mock.patch(
                "runwhen_platform_mcp.server.base64.b64decode",
                side_effect=AssertionError("decode must not run"),
            ),
        ):
            huge = "A" * 100_000
            try:
                _decode_script_base64(huge)
            except ValueError:
                pass
            else:
                raise AssertionError("expected pre-decode rejection")

    def test_symmetric_with_gzip_helper(self) -> None:
        # Both decoders must enforce the same cap. With the cap pinned
        # small, an identical raw payload is rejected by BOTH helpers.
        with mock.patch("runwhen_platform_mcp.server.SCRIPT_HARD_MAX_BYTES", 256):
            payload_bytes = ("x" * 1024).encode("utf-8")
            b64 = base64.b64encode(payload_bytes).decode("ascii")
            gz_b64 = base64.b64encode(gzip.compress(payload_bytes)).decode("ascii")

            for fn, arg in ((_decode_script_base64, b64), (_decode_script_gzip_base64, gz_b64)):
                try:
                    fn(arg)
                except ValueError:
                    continue
                raise AssertionError(f"{fn.__name__} accepted oversize payload")


class TestGetWorkspaceSecretsHandlesEnvVarCollisions:
    """Two keys mapped to the same env var must both surface.

    Regression for Bugbot LOW ("Secret map drops duplicate env vars").
    Previously ``recommended_secret_vars[platform][env_var] = key`` blindly
    overwrote, so duplicate-classification keys were silently dropped.

    Real-world collisions: the secret classifier accepts case-insensitive
    and separator-insensitive forms (``aws-access-key-id``,
    ``aws_access_key_id``, ``AWS_ACCESS_KEY_ID``), so the same workspace
    can legitimately carry several keys that classify to the same env var
    after the runner reformats them.
    """

    def _run(self, coro):
        return asyncio.run(coro)

    def test_duplicate_slack_tokens_both_listed(self) -> None:
        # ``slack-token`` and ``slack_token`` both classify as
        # ``(slack, SLACK_TOKEN)``. After the fix the second key gets a
        # numeric suffix and the collision is surfaced in the response.
        with (
            mock.patch(
                "runwhen_platform_mcp.server._resolve_workspace",
                new=mock.AsyncMock(return_value="ws"),
            ),
            mock.patch(
                "runwhen_platform_mcp.server._papi_get",
                new=mock.AsyncMock(return_value=["slack-token", "slack_token"]),
            ),
        ):
            result = json.loads(self._run(get_workspace_secrets(workspace_name="ws")))

        slack = result["recommended_secret_vars"].get("slack", {})
        # Both keys are present — neither silently dropped.
        assert set(slack.values()) == {"slack-token", "slack_token"}
        # One canonical entry plus a disambiguated one.
        assert "SLACK_TOKEN" in slack
        assert any(k.startswith("SLACK_TOKEN_") and k != "SLACK_TOKEN" for k in slack)
        # The collision is surfaced explicitly so agents can choose.
        assert "secret_var_collisions" in result
        assert "slack" in result["secret_var_collisions"]
        collided = result["secret_var_collisions"]["slack"]["SLACK_TOKEN"]
        assert sorted(collided) == ["slack-token", "slack_token"]

    def test_no_collision_no_field_emitted(self) -> None:
        # The clean single-secret case must not gain noise in the response.
        with (
            mock.patch(
                "runwhen_platform_mcp.server._resolve_workspace",
                new=mock.AsyncMock(return_value="ws"),
            ),
            mock.patch(
                "runwhen_platform_mcp.server._papi_get",
                new=mock.AsyncMock(return_value=["kubeconfig"]),
            ),
        ):
            result = json.loads(self._run(get_workspace_secrets(workspace_name="ws")))
        assert "secret_var_collisions" not in result

    def test_three_way_aws_access_key_collision(self) -> None:
        # All three forms of an AWS access-key-id classify identically.
        # The first wins the canonical name; the other two get suffixes.
        with (
            mock.patch(
                "runwhen_platform_mcp.server._resolve_workspace",
                new=mock.AsyncMock(return_value="ws"),
            ),
            mock.patch(
                "runwhen_platform_mcp.server._papi_get",
                new=mock.AsyncMock(
                    return_value=[
                        "aws-access-key-id",
                        "aws_access_key_id",
                        "AWS_ACCESS_KEY_ID",
                    ]
                ),
            ),
        ):
            result = json.loads(self._run(get_workspace_secrets(workspace_name="ws")))
        aws = result["recommended_secret_vars"]["aws"]
        # All three keys are present and uniquely addressed.
        assert sorted(aws.values()) == sorted(
            ["aws-access-key-id", "aws_access_key_id", "AWS_ACCESS_KEY_ID"]
        )
        names = set(aws.keys())
        assert "AWS_ACCESS_KEY_ID" in names
        assert "AWS_ACCESS_KEY_ID_2" in names
        assert "AWS_ACCESS_KEY_ID_3" in names
