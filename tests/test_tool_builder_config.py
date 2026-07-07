"""Tests for Tool Builder env-driven configuration."""

import asyncio
import os
from unittest.mock import AsyncMock, patch

from runwhen_platform_mcp import server as server_mod
from runwhen_platform_mcp.server import (
    _DEFAULT_GENERIC_CODECOLLECTION_REPO,
    _DEFAULT_WORKSPACE_UTILS_REPO,
    _code_bundle_from_env,
    _env_int,
    _env_str,
    _env_str_optional,
    _lookup_codecollection_url,
    _next_poll_sleep_s,
    _resolve_generic_codecollection_url,
    _resolve_workspace_utils_url,
)


def _clear_cc_cache() -> None:
    """Purge the shared TTL cache so each test observes a fresh PAPI lookup."""
    server_mod._codecollections_lookup_cache._store.clear()


def _run(coro):
    return asyncio.run(coro)


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

    def test_zero_below_minimum_falls_back(self) -> None:
        with patch.dict(os.environ, {"MCP_POLL_INTERVAL_S": "0"}, clear=True):
            assert _env_int("MCP_POLL_INTERVAL_S", 5, minimum=1) == 5

    def test_negative_below_minimum_falls_back(self) -> None:
        with patch.dict(os.environ, {"MCP_POLL_INTERVAL_S": "-1"}, clear=True):
            assert _env_int("MCP_POLL_INTERVAL_S", 5, minimum=1) == 5

    def test_minimum_allows_zero_when_configured(self) -> None:
        with patch.dict(os.environ, {"MCP_ARTIFACT_SETTLE_DELAY_S": "0"}, clear=True):
            assert _env_int("MCP_ARTIFACT_SETTLE_DELAY_S", 2, minimum=0) == 0


class TestNextPollSleep:
    def test_uses_full_interval_when_time_remains(self) -> None:
        assert _next_poll_sleep_s(elapsed=0, poll_interval_s=5, max_duration_s=300) == 5

    def test_caps_sleep_to_remaining_max(self) -> None:
        assert _next_poll_sleep_s(elapsed=8, poll_interval_s=10, max_duration_s=10) == 2

    def test_returns_zero_when_max_reached(self) -> None:
        assert _next_poll_sleep_s(elapsed=10, poll_interval_s=5, max_duration_s=10) == 0


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
            assert (
                _env_str(
                    "MCP_GENERIC_CODECOLLECTION_REPO_URL",
                    "https://github.com/runwhen-contrib/rw-generic-codecollection.git",
                )
                == "https://github.com/runwhen-contrib/rw-generic-codecollection.git"
            )

    def test_optional_blank_returns_none(self) -> None:
        with patch.dict(os.environ, {"MCP_TOOL_BUILDER_RUNBOOK_REF": ""}, clear=True):
            assert _env_str_optional("MCP_TOOL_BUILDER_RUNBOOK_REF") is None


# ---------------------------------------------------------------------------
# Workspace-aware code collection URL resolver
# ---------------------------------------------------------------------------


class TestLookupCodecollectionUrl:
    def test_returns_repo_web_url_when_name_matches(self) -> None:
        _clear_cc_cache()
        papi_response = {
            "count": 2,
            "results": [
                {"name": "rw-cli-codecollection", "repo_web_url": "https://x/cli.git"},
                {
                    "name": "rw-generic-codecollection",
                    "repo_web_url": "http://cc.internal/rw-generic-codecollection.git",
                },
            ],
        }
        with patch.object(
            server_mod,
            "_papi_get",
            new=AsyncMock(return_value=papi_response),
        ) as mock_get:
            url = _run(_lookup_codecollection_url("rw-generic-codecollection"))

        assert url == "http://cc.internal/rw-generic-codecollection.git"
        mock_get.assert_awaited_once_with("/api/v3/codecollections")

    def test_falls_back_to_spec_repourl(self) -> None:
        _clear_cc_cache()
        papi_response = {
            "results": [
                {
                    "name": "rw-workspace-utils",
                    "repo_web_url": None,
                    "spec": {"repoURL": "http://cc.internal/rw-workspace-utils.git"},
                },
            ],
        }
        with patch.object(
            server_mod,
            "_papi_get",
            new=AsyncMock(return_value=papi_response),
        ):
            url = _run(_lookup_codecollection_url("rw-workspace-utils"))
        assert url == "http://cc.internal/rw-workspace-utils.git"

    def test_missing_name_returns_none(self) -> None:
        _clear_cc_cache()
        papi_response = {"results": [{"name": "other-cc", "repo_web_url": "x"}]}
        with patch.object(
            server_mod,
            "_papi_get",
            new=AsyncMock(return_value=papi_response),
        ):
            url = _run(_lookup_codecollection_url("rw-generic-codecollection"))
        assert url is None

    def test_papi_error_returns_none(self) -> None:
        _clear_cc_cache()
        with patch.object(
            server_mod,
            "_papi_get",
            new=AsyncMock(side_effect=RuntimeError("connection refused")),
        ):
            url = _run(_lookup_codecollection_url("rw-generic-codecollection"))
        assert url is None

    def test_result_is_cached(self) -> None:
        _clear_cc_cache()
        papi_response = {
            "results": [
                {"name": "rw-generic-codecollection", "repo_web_url": "http://x.git"},
            ],
        }
        mock_get = AsyncMock(return_value=papi_response)
        with patch.object(server_mod, "_papi_get", new=mock_get):
            first = _run(_lookup_codecollection_url("rw-generic-codecollection"))
            second = _run(_lookup_codecollection_url("rw-generic-codecollection"))

        assert first == second == "http://x.git"
        mock_get.assert_awaited_once()

    def test_negative_result_is_cached(self) -> None:
        _clear_cc_cache()
        mock_get = AsyncMock(return_value={"results": []})
        with patch.object(server_mod, "_papi_get", new=mock_get):
            first = _run(_lookup_codecollection_url("rw-generic-codecollection"))
            second = _run(_lookup_codecollection_url("rw-generic-codecollection"))

        assert first is None
        assert second is None
        # Only one PAPI call — the negative result is cached too, so we don't
        # hammer PAPI on every commit_slx / render call in a workspace that
        # simply hasn't registered the mirror.
        mock_get.assert_awaited_once()


class TestResolveGenericCodecollectionUrl:
    def test_explicit_argument_wins(self) -> None:
        _clear_cc_cache()
        with patch.object(
            server_mod, "_papi_get", new=AsyncMock(return_value={"results": []})
        ) as mock_get:
            url, source = _run(
                _resolve_generic_codecollection_url(explicit="http://caller/override.git")
            )

        assert url == "http://caller/override.git"
        assert source == "explicit"
        mock_get.assert_not_awaited()

    def test_env_override_beats_workspace_lookup(self) -> None:
        _clear_cc_cache()
        papi_response = {
            "results": [
                {
                    "name": "rw-generic-codecollection",
                    "repo_web_url": "http://cc.internal/rw-generic-codecollection.git",
                },
            ],
        }
        with (
            patch.object(
                server_mod,
                "_GENERIC_CODECOLLECTION_REPO_URL",
                "http://env-override.example/rw-generic-codecollection.git",
            ),
            patch.object(
                server_mod,
                "_papi_get",
                new=AsyncMock(return_value=papi_response),
            ) as mock_get,
        ):
            url, source = _run(_resolve_generic_codecollection_url())

        assert url == "http://env-override.example/rw-generic-codecollection.git"
        assert source == "env"
        mock_get.assert_not_awaited()

    def test_workspace_lookup_when_no_env_or_explicit(self) -> None:
        _clear_cc_cache()
        papi_response = {
            "results": [
                {
                    "name": "rw-generic-codecollection",
                    "repo_web_url": "http://cc.internal/rw-generic-codecollection.git",
                },
            ],
        }
        with (
            patch.object(
                server_mod,
                "_GENERIC_CODECOLLECTION_REPO_URL",
                _DEFAULT_GENERIC_CODECOLLECTION_REPO,
            ),
            patch.object(
                server_mod,
                "_papi_get",
                new=AsyncMock(return_value=papi_response),
            ),
        ):
            url, source = _run(_resolve_generic_codecollection_url())

        assert url == "http://cc.internal/rw-generic-codecollection.git"
        assert source == "workspace"

    def test_falls_back_to_hardcoded_default(self) -> None:
        _clear_cc_cache()
        with (
            patch.object(
                server_mod,
                "_GENERIC_CODECOLLECTION_REPO_URL",
                _DEFAULT_GENERIC_CODECOLLECTION_REPO,
            ),
            patch.object(
                server_mod,
                "_papi_get",
                new=AsyncMock(return_value={"results": []}),
            ),
        ):
            url, source = _run(_resolve_generic_codecollection_url())

        assert url == _DEFAULT_GENERIC_CODECOLLECTION_REPO
        assert source == "default"

    def test_per_bundle_env_override_wins_over_workspace(self) -> None:
        # Regression: bugbot flagged that per-bundle env vars
        # (MCP_TOOL_BUILDER_RUNBOOK_REPO_URL / MCP_TOOL_BUILDER_SLI_REPO_URL,
        # applied to RB_CODE_BUNDLE / SLI_CODE_BUNDLE by _code_bundle_from_env)
        # were silently ignored once the resolver switched to workspace lookup.
        # Passing the bundle in should honor its non-default repoUrl before
        # falling through to a workspace lookup.
        _clear_cc_cache()
        rb_bundle = {
            "repoUrl": "http://env-override.example/rb-mirror.git",
            "ref": "main",
            "pathToRobot": "codebundles/tool-builder/runbook.robot",
        }
        papi_response = {
            "results": [
                {
                    "name": "rw-generic-codecollection",
                    "repo_web_url": "http://cc.internal/rw-generic-codecollection.git",
                },
            ],
        }
        with (
            patch.object(
                server_mod,
                "_GENERIC_CODECOLLECTION_REPO_URL",
                _DEFAULT_GENERIC_CODECOLLECTION_REPO,
            ),
            patch.object(
                server_mod,
                "_papi_get",
                new=AsyncMock(return_value=papi_response),
            ) as mock_get,
        ):
            url, source = _run(_resolve_generic_codecollection_url(bundle=rb_bundle))

        assert url == "http://env-override.example/rb-mirror.git"
        assert source == "env"
        # No workspace round-trip when the per-bundle override is already set.
        mock_get.assert_not_awaited()

    def test_per_bundle_default_falls_through_to_workspace(self) -> None:
        # When the caller passes a bundle whose repoUrl is still the hardcoded
        # default (no per-bundle env override), the resolver must NOT treat
        # that as an env override and must fall through to workspace lookup.
        _clear_cc_cache()
        rb_bundle = {
            "repoUrl": _DEFAULT_GENERIC_CODECOLLECTION_REPO,
            "ref": "main",
            "pathToRobot": "codebundles/tool-builder/runbook.robot",
        }
        papi_response = {
            "results": [
                {
                    "name": "rw-generic-codecollection",
                    "repo_web_url": "http://cc.internal/rw-generic-codecollection.git",
                },
            ],
        }
        with (
            patch.object(
                server_mod,
                "_GENERIC_CODECOLLECTION_REPO_URL",
                _DEFAULT_GENERIC_CODECOLLECTION_REPO,
            ),
            patch.object(
                server_mod,
                "_papi_get",
                new=AsyncMock(return_value=papi_response),
            ),
        ):
            url, source = _run(_resolve_generic_codecollection_url(bundle=rb_bundle))

        assert url == "http://cc.internal/rw-generic-codecollection.git"
        assert source == "workspace"

    def test_per_bundle_env_wins_over_global_env(self) -> None:
        # If BOTH the per-bundle override and the global MCP_GENERIC_...
        # override are set, the per-bundle value should win — that's the
        # whole point of the per-bundle knob (e.g. RB and SLI mirrors served
        # by different repos).
        _clear_cc_cache()
        rb_bundle = {
            "repoUrl": "http://env-override.example/rb-mirror.git",
            "ref": "main",
            "pathToRobot": "codebundles/tool-builder/runbook.robot",
        }
        with patch.object(
            server_mod,
            "_GENERIC_CODECOLLECTION_REPO_URL",
            "http://env-override.example/global-generic.git",
        ):
            url, source = _run(_resolve_generic_codecollection_url(bundle=rb_bundle))

        assert url == "http://env-override.example/rb-mirror.git"
        assert source == "env"


class TestCodecollectionCacheAuthScoping:
    """Cache must not leak one principal's resolved URL to another."""

    def _install_token(self, token: str):
        """Force ``_get_token`` to return ``token`` for the duration of the test."""
        return patch.object(server_mod, "_get_token", return_value=token)

    def test_cache_key_differs_per_token(self) -> None:
        # Regression: bugbot flagged that the codecollections cache was keyed
        # only by codecollection name. On a shared HTTP MCP instance each
        # request carries its own Bearer token (contextvar), so User A's
        # resolved mirror could be served to User B within the 5-minute TTL.
        # The auth-scoped cache key must produce different keys for different
        # tokens targeting the same codecollection name.
        with self._install_token("token-user-a"):
            key_a = server_mod._codecollection_cache_key("rw-generic-codecollection")
        with self._install_token("token-user-b"):
            key_b = server_mod._codecollection_cache_key("rw-generic-codecollection")

        assert key_a != key_b, "cache key must vary by token to prevent cross-user leakage"
        assert key_a.endswith(":rw-generic-codecollection")
        assert key_b.endswith(":rw-generic-codecollection")
        # Token material must not appear verbatim in the cache key (the hash
        # buys us defence-in-depth if the cache dict is ever surfaced in logs).
        assert "token-user-a" not in key_a
        assert "token-user-b" not in key_b

    def test_cache_key_stable_for_same_token(self) -> None:
        # Same token → same key (otherwise we'd never hit the cache).
        with self._install_token("token-same"):
            key1 = server_mod._codecollection_cache_key("rw-generic-codecollection")
            key2 = server_mod._codecollection_cache_key("rw-generic-codecollection")
        assert key1 == key2

    def test_lookup_does_not_leak_across_tokens(self) -> None:
        # Full end-to-end: User A's lookup populates the cache; User B's
        # lookup with a different token must issue its own PAPI call
        # (different result), not reuse User A's cached URL.
        _clear_cc_cache()
        papi_response_a = {
            "results": [
                {"name": "rw-generic-codecollection", "repo_web_url": "http://a.git"},
            ],
        }
        papi_response_b = {
            "results": [
                {"name": "rw-generic-codecollection", "repo_web_url": "http://b.git"},
            ],
        }
        mock_get = AsyncMock(side_effect=[papi_response_a, papi_response_b])
        with (
            patch.object(server_mod, "_papi_get", new=mock_get),
            self._install_token("token-user-a"),
        ):
            url_a = _run(_lookup_codecollection_url("rw-generic-codecollection"))
        with (
            patch.object(server_mod, "_papi_get", new=mock_get),
            self._install_token("token-user-b"),
        ):
            url_b = _run(_lookup_codecollection_url("rw-generic-codecollection"))

        assert url_a == "http://a.git"
        assert url_b == "http://b.git"
        # Each principal must have made its own PAPI call — the cache must
        # NOT have short-circuited User B with User A's cached URL.
        assert mock_get.await_count == 2


class TestResolveWorkspaceUtilsUrl:
    def test_env_override_wins(self) -> None:
        _clear_cc_cache()
        override_bundle = {
            "repoUrl": "http://env-override.example/rw-workspace-utils.git",
            "ref": "main",
            "pathToRobot": "codebundles/cron-scheduler-sli/sli.robot",
        }
        with (
            patch.object(server_mod, "CRON_SLI_CODE_BUNDLE", override_bundle),
            patch.object(
                server_mod, "_papi_get", new=AsyncMock(return_value={"results": []})
            ) as mock_get,
        ):
            url, source = _run(_resolve_workspace_utils_url())

        assert url == "http://env-override.example/rw-workspace-utils.git"
        assert source == "env"
        mock_get.assert_not_awaited()

    def test_workspace_lookup_used_by_default(self) -> None:
        _clear_cc_cache()
        default_bundle = {
            "repoUrl": _DEFAULT_WORKSPACE_UTILS_REPO,
            "ref": "main",
            "pathToRobot": "codebundles/cron-scheduler-sli/sli.robot",
        }
        papi_response = {
            "results": [
                {
                    "name": "rw-workspace-utils",
                    "repo_web_url": "http://cc.internal/rw-workspace-utils.git",
                },
            ],
        }
        with (
            patch.object(server_mod, "CRON_SLI_CODE_BUNDLE", default_bundle),
            patch.object(server_mod, "_papi_get", new=AsyncMock(return_value=papi_response)),
        ):
            url, source = _run(_resolve_workspace_utils_url())

        assert url == "http://cc.internal/rw-workspace-utils.git"
        assert source == "workspace"

    def test_falls_back_to_default(self) -> None:
        _clear_cc_cache()
        default_bundle = {
            "repoUrl": _DEFAULT_WORKSPACE_UTILS_REPO,
            "ref": "main",
            "pathToRobot": "codebundles/cron-scheduler-sli/sli.robot",
        }
        with (
            patch.object(server_mod, "CRON_SLI_CODE_BUNDLE", default_bundle),
            patch.object(server_mod, "_papi_get", new=AsyncMock(return_value={"results": []})),
        ):
            url, source = _run(_resolve_workspace_utils_url())

        assert url == _DEFAULT_WORKSPACE_UTILS_REPO
        assert source == "default"
