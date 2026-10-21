"""The userId workspace_chat sends must be the identity AgentFarm authenticates.

AgentFarm (runwhen/agentfarm#402) refuses a chat turn whose body ``userId`` is
not the bearer-token holder as PAPI reports it (``email or username``). These
tests pin that the MCP server resolves that exact value, never invents one for
AgentFarm calls, and does not cache a failed lookup.

All data is synthetic; no network.
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any
from unittest import mock

import httpx
import pytest

from runwhen_platform_mcp import server as _server


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _jwt(claims: dict[str, Any]) -> str:
    def b64(obj: dict[str, Any]) -> str:
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{b64({'alg': 'none'})}.{b64(claims)}.sig"


@pytest.fixture(autouse=True)
def _fresh_cache() -> Any:
    _server._user_email_cache._store.clear()
    yield
    _server._user_email_cache._store.clear()


def _papi(responses: dict[str, Any]) -> mock.AsyncMock:
    """A fake ``_papi_get``: dict value is returned, Exception value is raised."""

    async def fake(path: str, params: Any = None) -> Any:
        value = responses.get(path, ValueError(f"PAPI returned 404 for {path}"))
        if isinstance(value, Exception):
            raise value
        return value

    return mock.AsyncMock(side_effect=fake)


class TestResolveCallerIdentity:
    def test_whoami_primary_email_is_used(self) -> None:
        papi = _papi({"/api/v3/users/whoami": {"primaryEmail": "ana@example.com"}})
        with mock.patch.object(_server, "_papi_get", papi):
            assert _run(_server._resolve_caller_identity("pat-1")) == "ana@example.com"

    def test_username_when_user_has_no_email(self) -> None:
        # PAPI's agentfarm-params reports ``email or username``; mirror it, even
        # when the username has no "@" (the old code fell back to a numeric id).
        papi = _papi({"/api/v3/users/whoami": {"primaryEmail": "", "username": "ana-local"}})
        with mock.patch.object(_server, "_papi_get", papi):
            assert _run(_server._resolve_caller_identity("pat-1")) == "ana-local"

    def test_whoami_wins_over_a_stale_jwt_email_claim(self) -> None:
        token = _jwt({"email": "old@example.com", "user_id": 7})
        papi = _papi({"/api/v3/users/whoami": {"primaryEmail": "new@example.com"}})
        with mock.patch.object(_server, "_papi_get", papi):
            assert _run(_server._resolve_caller_identity(token)) == "new@example.com"

    def test_user_by_id_when_whoami_fails(self) -> None:
        token = _jwt({"user_id": 7})
        papi = _papi({"/api/v3/users/7": {"primaryEmail": "ana@example.com"}})
        with mock.patch.object(_server, "_papi_get", papi):
            assert _run(_server._resolve_caller_identity(token)) == "ana@example.com"

    def test_jwt_email_claim_only_when_papi_cannot_answer(self) -> None:
        token = _jwt({"email": "ana@example.com", "user_id": 7})
        with mock.patch.object(_server, "_papi_get", _papi({})):
            assert _run(_server._resolve_caller_identity(token)) == "ana@example.com"

    def test_unresolvable_is_none_and_not_cached(self) -> None:
        with mock.patch.object(_server, "_papi_get", _papi({})):
            assert _run(_server._resolve_caller_identity("pat-1")) is None
        # PAPI recovers: the next call must not be stuck on a cached failure.
        papi = _papi({"/api/v3/users/whoami": {"primaryEmail": "ana@example.com"}})
        with mock.patch.object(_server, "_papi_get", papi):
            assert _run(_server._resolve_caller_identity("pat-1")) == "ana@example.com"

    def test_success_is_cached_per_token(self) -> None:
        papi = _papi({"/api/v3/users/whoami": {"primaryEmail": "ana@example.com"}})
        with mock.patch.object(_server, "_papi_get", papi):
            _run(_server._resolve_caller_identity("pat-1"))
            _run(_server._resolve_caller_identity("pat-1"))
        assert papi.await_count == 1


class TestGetUserEmailLegacyFallback:
    """Owner metadata (commit_slx) keeps its old fallback; it is just not cached."""

    def test_numeric_id_fallback_is_not_cached(self) -> None:
        token = _jwt({"user_id": 7})
        with mock.patch.object(_server, "_papi_get", _papi({})):
            assert _run(_server._get_user_email(token)) == "7"
        assert _server._user_email_cache.get(token) is None

    def test_placeholder_fallback_without_any_id(self) -> None:
        with mock.patch.object(_server, "_papi_get", _papi({})):
            assert _run(_server._get_user_email("opaque-pat")) == "cursor@runwhen.com"


class TestWorkspaceChatIdentity:
    def _call(self) -> dict[str, Any]:
        return json.loads(
            _run(
                _server.workspace_chat(
                    message="hi",
                    workspace_name="ws-a",
                    persona_name="default",
                    session_id=None,
                )
            )
        )

    def test_sends_resolved_identity_and_the_path_workspace(self) -> None:
        consume = mock.AsyncMock(return_value={"message": "ok"})
        with (
            mock.patch.object(_server, "_resolve_workspace", mock.AsyncMock(return_value="ws-a")),
            mock.patch.object(
                _server, "_resolve_caller_identity", mock.AsyncMock(return_value="ana-local")
            ),
            mock.patch.object(_server, "_consume_agentfarm_sse", consume),
        ):
            assert self._call() == {"message": "ok"}
        url, body = consume.await_args.args
        assert url.endswith("/api/v1/workspaces/ws-a/chat-pro-sse")
        assert body["userId"] == "ana-local"
        assert body["workspaceName"] == "ws-a"

    def test_refuses_to_send_an_invented_identity(self) -> None:
        consume = mock.AsyncMock()
        with (
            mock.patch.object(_server, "_resolve_workspace", mock.AsyncMock(return_value="ws-a")),
            mock.patch.object(
                _server, "_resolve_caller_identity", mock.AsyncMock(return_value=None)
            ),
            mock.patch.object(_server, "_consume_agentfarm_sse", consume),
        ):
            result = self._call()
        assert "Could not resolve your RunWhen identity" in result["error"]
        consume.assert_not_awaited()


class TestAgentfarmRefusalMessage:
    def _resp(self, status: int, detail: str) -> httpx.Response:
        return httpx.Response(status, json={"detail": detail})

    def test_user_id_mismatch_names_the_cause(self) -> None:
        msg = _server._agentfarm_refusal_message(
            self._resp(403, "userId does not match the authenticated user")
        )
        assert "userId does not match" in msg
        assert "whoami" in msg

    def test_other_403_keeps_the_workspace_hint(self) -> None:
        msg = _server._agentfarm_refusal_message(self._resp(403, "Forbidden"))
        assert "access to this workspace" in msg

    def test_workspace_mismatch_400_surfaces_detail(self) -> None:
        msg = _server._agentfarm_refusal_message(
            self._resp(400, "The workspace named in the request does not match")
        )
        assert msg.startswith("AgentFarm returned 400")
        assert "does not match" in msg
