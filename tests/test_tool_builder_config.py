"""Tests for Tool Builder env-driven configuration."""

import os
from unittest.mock import patch

from runwhen_platform_mcp.server import (
    _code_bundle_from_env,
    _env_int,
    _env_str,
    _env_str_optional,
)


class TestEnvInt:
    def test_default_when_unset(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            assert _env_int("MCP_POLL_INTERVAL_S", 5) == 5

    def test_parses_integer(self) -> None:
        with patch.dict(os.environ, {"MCP_POLL_INTERVAL_S": "10"}, clear=True):
            assert _env_int("MCP_POLL_INTERVAL_S", 5) == 10

    def test_invalid_value_falls_back(self) -> None:
        with patch.dict(os.environ, {"MCP_POLL_INTERVAL_S": "not-a-number"}, clear=True):
            assert _env_int("MCP_POLL_INTERVAL_S", 5) == 5


class TestCodeBundleFromEnv:
    def test_uses_defaults(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            bundle = _code_bundle_from_env(
                repo_url_var="MCP_TOOL_BUILDER_RUNBOOK_REPO_URL",
                ref_var="MCP_TOOL_BUILDER_RUNBOOK_REF",
                path_var="MCP_TOOL_BUILDER_RUNBOOK_PATH",
                default_repo_url="https://github.com/example/rw-generic-codecollection.git",
                default_ref="main",
                default_path="codebundles/tool-builder/runbook.robot",
            )
        assert bundle == {
            "repoUrl": "https://github.com/example/rw-generic-codecollection.git",
            "ref": "main",
            "pathToRobot": "codebundles/tool-builder/runbook.robot",
        }

    def test_per_bundle_override(self) -> None:
        env = {
            "MCP_TOOL_BUILDER_RUNBOOK_REPO_URL": "https://git.internal/rw-generic-codecollection.git",
            "MCP_TOOL_BUILDER_RUNBOOK_REF": "release-1.2",
            "MCP_TOOL_BUILDER_RUNBOOK_PATH": "custom/path/runbook.robot",
        }
        with patch.dict(os.environ, env, clear=True):
            bundle = _code_bundle_from_env(
                repo_url_var="MCP_TOOL_BUILDER_RUNBOOK_REPO_URL",
                ref_var="MCP_TOOL_BUILDER_RUNBOOK_REF",
                path_var="MCP_TOOL_BUILDER_RUNBOOK_PATH",
                default_repo_url="https://github.com/example/rw-generic-codecollection.git",
                default_ref="main",
                default_path="codebundles/tool-builder/runbook.robot",
            )
        assert bundle == {
            "repoUrl": "https://git.internal/rw-generic-codecollection.git",
            "ref": "release-1.2",
            "pathToRobot": "custom/path/runbook.robot",
        }

    def test_shared_fallback_when_per_bundle_unset(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            bundle = _code_bundle_from_env(
                repo_url_var="MCP_TOOL_BUILDER_SLI_REPO_URL",
                ref_var="MCP_TOOL_BUILDER_SLI_REF",
                path_var="MCP_TOOL_BUILDER_SLI_PATH",
                default_repo_url="https://github.com/example/rw-generic-codecollection.git",
                default_ref="main",
                default_path="codebundles/tool-builder/sli.robot",
                repo_url_fallback="https://git.internal/rw-generic-codecollection.git",
                ref_fallback="mirror-main",
            )
        assert bundle["repoUrl"] == "https://git.internal/rw-generic-codecollection.git"
        assert bundle["ref"] == "mirror-main"
        assert bundle["pathToRobot"] == "codebundles/tool-builder/sli.robot"

    def test_per_bundle_override_beats_shared_fallback(self) -> None:
        env = {
            "MCP_TOOL_BUILDER_SLI_REPO_URL": "https://git.internal/override.git",
            "MCP_TOOL_BUILDER_SLI_REF": "override-ref",
        }
        with patch.dict(os.environ, env, clear=True):
            bundle = _code_bundle_from_env(
                repo_url_var="MCP_TOOL_BUILDER_SLI_REPO_URL",
                ref_var="MCP_TOOL_BUILDER_SLI_REF",
                path_var="MCP_TOOL_BUILDER_SLI_PATH",
                default_repo_url="https://github.com/example/rw-generic-codecollection.git",
                default_ref="main",
                default_path="codebundles/tool-builder/sli.robot",
                repo_url_fallback="https://git.internal/rw-generic-codecollection.git",
                ref_fallback="mirror-main",
            )
        assert bundle["repoUrl"] == "https://git.internal/override.git"
        assert bundle["ref"] == "override-ref"

    def test_empty_env_falls_back_to_shared_and_defaults(self) -> None:
        env = {
            "MCP_TOOL_BUILDER_SLI_REPO_URL": "",
            "MCP_TOOL_BUILDER_SLI_REF": "   ",
            "MCP_TOOL_BUILDER_SLI_PATH": "",
        }
        with patch.dict(os.environ, env, clear=True):
            bundle = _code_bundle_from_env(
                repo_url_var="MCP_TOOL_BUILDER_SLI_REPO_URL",
                ref_var="MCP_TOOL_BUILDER_SLI_REF",
                path_var="MCP_TOOL_BUILDER_SLI_PATH",
                default_repo_url="https://github.com/example/rw-generic-codecollection.git",
                default_ref="main",
                default_path="codebundles/tool-builder/sli.robot",
                repo_url_fallback="https://git.internal/rw-generic-codecollection.git",
                ref_fallback="mirror-main",
            )
        assert bundle == {
            "repoUrl": "https://git.internal/rw-generic-codecollection.git",
            "ref": "mirror-main",
            "pathToRobot": "codebundles/tool-builder/sli.robot",
        }


class TestEnvStr:
    def test_blank_env_uses_default(self) -> None:
        with patch.dict(os.environ, {"MCP_GENERIC_SLX_ICON": ""}, clear=True):
            assert _env_str("MCP_GENERIC_SLX_ICON", "https://example/icon.svg") == (
                "https://example/icon.svg"
            )

    def test_whitespace_env_uses_default(self) -> None:
        with patch.dict(os.environ, {"MCP_GENERIC_CODECOLLECTION_REPO_URL": "  "}, clear=True):
            assert _env_str(
                "MCP_GENERIC_CODECOLLECTION_REPO_URL",
                "https://github.com/runwhen-contrib/rw-generic-codecollection.git",
            ) == "https://github.com/runwhen-contrib/rw-generic-codecollection.git"

    def test_optional_blank_returns_none(self) -> None:
        with patch.dict(os.environ, {"MCP_TOOL_BUILDER_RUNBOOK_REF": ""}, clear=True):
            assert _env_str_optional("MCP_TOOL_BUILDER_RUNBOOK_REF") is None
