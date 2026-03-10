"""Unit tests for script validation and helper functions."""

from runwhen_platform_mcp.server import (
    _ensure_required_tags,
    _extract_env_vars,
    _validate_script,
)


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
