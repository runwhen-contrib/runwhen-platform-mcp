"""Tests for PAPI list auto-pagination (RW-1436).

``_papi_get_all_pages`` must follow the ``next`` link across every page so
list endpoints like ``get_workspace_slxs`` return the complete set, not just
the first 100-item page.
"""

import asyncio
from unittest import mock

from runwhen_platform_mcp.server import _papi_get_all_pages


def _run(coro):
    return asyncio.run(coro)


def _page(results, next_offset, total):
    """Build a PAPI-shaped page. ``next_offset`` None means last page."""
    next_url = (
        f"https://papi.example/api/v3/workspaces/ws/slxs?limit=100&offset={next_offset}"
        if next_offset is not None
        else None
    )
    return {"count": total, "next": next_url, "previous": None, "results": results}


class TestPapiGetAllPages:
    @mock.patch("runwhen_platform_mcp.server._papi_get", new_callable=mock.AsyncMock)
    def test_follows_next_across_all_pages(self, mock_get) -> None:
        # 250 items across three pages of 100/100/50.
        mock_get.side_effect = [
            _page([{"id": i} for i in range(0, 100)], 100, 250),
            _page([{"id": i} for i in range(100, 200)], 200, 250),
            _page([{"id": i} for i in range(200, 250)], None, 250),
        ]

        result = _run(_papi_get_all_pages("/api/v3/workspaces/ws/slxs", page_size=100))

        assert result["count"] == 250
        assert result["next"] is None
        assert result["previous"] is None
        assert len(result["results"]) == 250
        assert [r["id"] for r in result["results"]] == list(range(250))
        # Offsets requested should be 0, 100, 200.
        offsets = [c.kwargs["params"]["offset"] for c in mock_get.call_args_list]
        assert offsets == [0, 100, 200]

    @mock.patch("runwhen_platform_mcp.server._papi_get", new_callable=mock.AsyncMock)
    def test_single_page_no_extra_requests(self, mock_get) -> None:
        mock_get.side_effect = [_page([{"id": 1}], None, 1)]

        result = _run(_papi_get_all_pages("/api/v3/workspaces/ws/slxs"))

        assert len(result["results"]) == 1
        assert mock_get.call_count == 1

    @mock.patch("runwhen_platform_mcp.server._papi_get", new_callable=mock.AsyncMock)
    def test_non_paginated_dict_returned_unchanged(self, mock_get) -> None:
        payload = {"foo": "bar"}
        mock_get.side_effect = [payload]

        result = _run(_papi_get_all_pages("/api/v3/workspaces/ws/thing"))

        assert result is payload
        assert mock_get.call_count == 1

    @mock.patch("runwhen_platform_mcp.server._papi_get", new_callable=mock.AsyncMock)
    def test_bare_list_returned_unchanged(self, mock_get) -> None:
        payload = [{"id": 1}, {"id": 2}]
        mock_get.side_effect = [payload]

        result = _run(_papi_get_all_pages("/api/v3/workspaces/ws/thing"))

        assert result is payload
        assert mock_get.call_count == 1

    @mock.patch("runwhen_platform_mcp.server._papi_get", new_callable=mock.AsyncMock)
    def test_empty_page_stops_even_if_next_present(self, mock_get) -> None:
        # A page that claims ``next`` but returns no rows must not loop forever.
        mock_get.side_effect = [
            _page([{"id": 1}], 100, 999),
            _page([], 200, 999),
        ]

        result = _run(_papi_get_all_pages("/api/v3/workspaces/ws/slxs"))

        assert len(result["results"]) == 1
        assert result["next"] is None
        assert mock_get.call_count == 2
