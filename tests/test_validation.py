"""Unit tests for script validation and helper functions."""

import asyncio
import base64
import json
from unittest import mock

import httpx
import pytest

from runwhen_platform_mcp.authorization import WRITE_TOOLS
from runwhen_platform_mcp.server import (
    _assess_issue_quality_static,
    _assess_run_output_quality,
    _assess_script_size,
    _azure_credentials_hint,
    _build_persona_payload,
    _detect_unresolved_placeholders,
    _ensure_required_tags,
    _extract_env_vars,
    _form_persona_full_name,
    _is_blocking_warning,
    _normalize_chat_persona_scope_id,
    _persona_short_name,
    _resolve_assistant_short_name,
    _resolve_command_assistant_name,
    _resolve_script,
    _strip_runner_unsafe_blocks,
    _validate_assistant_name,
    _validate_runtime_vars,
    _validate_script,
    _validate_slx_name,
    commit_slx,
    get_registry_codebundle,
    run_script_and_wait,
    run_slx,
    search_registry,
    update_chat_command,
    validate_script,
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
