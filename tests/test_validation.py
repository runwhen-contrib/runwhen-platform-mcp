"""Unit tests for script validation and helper functions."""

import base64
from unittest import mock

import pytest

from runwhen_platform_mcp.authorization import WRITE_TOOLS
from runwhen_platform_mcp.server import (
    _ensure_required_tags,
    _extract_env_vars,
    _resolve_script,
    _validate_script,
    _validate_script_vars,
    _validate_slx_name,
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
        "create_knowledge_base_article",
        "update_knowledge_base_article",
        "delete_knowledge_base_article",
    }

    def test_write_tools_contains_all_expected(self) -> None:
        missing = self.EXPECTED_WRITE_TOOLS - WRITE_TOOLS
        assert not missing, f"WRITE_TOOLS is missing: {missing}"

    def test_no_unexpected_removals(self) -> None:
        assert WRITE_TOOLS >= self.EXPECTED_WRITE_TOOLS


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


class TestCommitSlxScriptVarsValidation:
    """commit_slx returns validation errors for invalid script_vars."""

    def _run(self, coro):
        import asyncio
        return asyncio.run(coro)

    @mock.patch("runwhen_platform_mcp.server._resolve_workspace", new_callable=mock.AsyncMock)
    def test_invalid_script_var_returns_error(self, mock_resolve) -> None:
        import json
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
