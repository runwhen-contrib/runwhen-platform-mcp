"""Direct unit tests for MCP tool handler functions.

These test the actual function bodies behind the MCP tools — no HTTP,
no deployed server, no secrets needed. The handlers read bundled catalog
data and return deterministically.

Note: FastMCP defaults (FieldInfo) don't resolve when calling handlers
directly — every kwarg must be passed explicitly.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from runwhen_platform_mcp.server import (
    list_discovery_platforms,
    list_indexed_resource_types,
)


def _run(coro):
    return asyncio.run(coro)


class TestListDiscoveryPlatforms:
    def test_returns_platforms_list(self) -> None:
        payload = json.loads(_run(list_discovery_platforms()))
        assert isinstance(payload, dict)
        assert "platforms" in payload
        platforms = payload["platforms"]
        assert isinstance(platforms, list)
        assert len(platforms) >= 2

    def test_kubernetes_in_platforms(self) -> None:
        payload = json.loads(_run(list_discovery_platforms()))
        names = {p["platform"] for p in payload["platforms"]}
        assert "kubernetes" in names

    def test_decision_guide_covers_all_platforms(self) -> None:
        payload = json.loads(_run(list_discovery_platforms()))
        guide = payload["decision_guide"]
        names = {p["platform"] for p in payload["platforms"]}
        assert set(guide.keys()) == names


class TestListIndexedResourceTypes:
    def test_kubernetes_returns_namespace(self) -> None:
        payload = json.loads(
            _run(list_indexed_resource_types(platform="kubernetes", search="", limit=50))
        )
        assert "namespace" in payload.get("resource_types", [])

    def test_unknown_platform_returns_error(self) -> None:
        payload = json.loads(
            _run(list_indexed_resource_types(platform="not-a-platform", search="", limit=50))
        )
        assert "error" in payload

    @pytest.mark.parametrize(
        "platform",
        ["kubernetes", "azure", "aws", "gcp", "runwhen"],
    )
    def test_all_known_platforms_return_without_error(self, platform: str) -> None:
        payload = json.loads(
            _run(list_indexed_resource_types(platform=platform, search="", limit=50))
        )
        assert "error" not in payload
