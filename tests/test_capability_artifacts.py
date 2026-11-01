"""Tests for capability artifacts over MCP: the pure helpers and the ``artifact_read`` tool.

The helpers in ``capability_artifacts`` are called directly. The tool is called
as in ``test_capability_tasks.py``: the platform API is mocked at the module's
HTTP helper and every argument is passed explicitly (FastMCP ``Field`` defaults
do not resolve on a direct call).
"""

from __future__ import annotations

import asyncio
import copy
import json
from unittest import mock

import pytest

from runwhen_platform_mcp import server
from runwhen_platform_mcp.capability_artifacts import (
    MAX_OUTPUT_CHARS,
    fit_output,
    has_artifact_input,
    is_artifact,
    read_request,
    read_result,
)

_REF = "art_" + "0123456789abcdef" * 2
_OTHER_REF = "art_" + "fedcba9876543210" * 2
_RUN = "3f2b8c1e-9d4a-4e57-8a61-0c5d7e9b1a24"
_RUN_REF = f"run:{_RUN}/outputs/report"
_BASE = "/api/v4/workspaces/ws"


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _workspace():
    with mock.patch.object(server, "_resolve_workspace", mock.AsyncMock(return_value="ws")):
        yield


def _request(op, ref="", *, lines=200, offset=0, nbytes=0, task="", run_id=""):
    return read_request(
        "ws", op, ref, lines=lines, offset=offset, nbytes=nbytes, task=task, run_id=run_id
    )


def _artifact_read(**kwargs):
    args = {
        "workspace_name": "ws",
        "op": "head",
        "ref": "",
        "lines": 200,
        "offset": 0,
        "bytes": 0,
        "task": "",
        "run_id": "",
    }
    args.update(kwargs)
    return _run(server.artifact_read(**args))


class TestReadRequestList:
    def test_list_has_no_params_by_default(self) -> None:
        assert _request("list") == (f"{_BASE}/artifacts", {})

    def test_a_task_filter_is_sent_as_the_bare_task_name(self) -> None:
        path, params = _request("list", task="k8s-discovery/logs")
        assert path == f"{_BASE}/artifacts"
        assert params == {"task": "logs"}

    def test_a_bare_task_name_is_sent_as_given(self) -> None:
        assert _request("list", task="logs")[1] == {"task": "logs"}

    def test_a_run_filter_is_sent_as_run_uuid(self) -> None:
        assert _request("list", run_id=_RUN)[1] == {"runUuid": _RUN}

    def test_a_run_id_that_is_not_a_uuid_is_refused(self) -> None:
        with pytest.raises(ValueError, match="run_id"):
            _request("list", run_id="not-a-uuid")

    def test_list_ignores_a_ref(self) -> None:
        assert _request("list", ref="not even a ref") == (f"{_BASE}/artifacts", {})


class TestReadRequestStat:
    def test_stat_reads_one_artifact(self) -> None:
        assert _request("stat", _REF) == (f"{_BASE}/artifacts/{_REF}", {})

    def test_stat_refuses_a_run_output(self) -> None:
        with pytest.raises(ValueError, match="stat takes an art_ ref"):
            _request("stat", _RUN_REF)


class TestReadRequestPages:
    def test_head_sends_lines_and_op_only(self) -> None:
        assert _request("head", _REF, lines=50, offset=7, nbytes=9) == (
            f"{_BASE}/artifacts/{_REF}/content",
            {"lines": 50, "op": "head"},
        )

    def test_tail_sends_lines_and_op_only(self) -> None:
        assert _request("tail", _REF, lines=50, offset=7, nbytes=9)[1] == {
            "lines": 50,
            "op": "tail",
        }

    def test_lines_are_clamped_to_the_platform_limits(self) -> None:
        assert _request("head", _REF, lines=5000)[1] == {"lines": 2000, "op": "head"}
        assert _request("tail", _REF, lines=0)[1] == {"lines": 1, "op": "tail"}
        assert _request("head", _REF, lines=-3)[1] == {"lines": 1, "op": "head"}

    def test_range_without_a_byte_count_sends_no_bytes_and_no_lines(self) -> None:
        path, params = _request("range", _REF, lines=50, offset=100, nbytes=0)
        assert path == f"{_BASE}/artifacts/{_REF}/content"
        assert params == {"offset": 100, "op": "range"}

    def test_range_with_a_byte_count_sends_it_and_no_lines(self) -> None:
        assert _request("range", _REF, lines=50, offset=100, nbytes=512)[1] == {
            "offset": 100,
            "bytes": 512,
            "op": "range",
        }

    def test_range_bytes_are_clamped_to_the_platform_limit(self) -> None:
        assert _request("range", _REF, nbytes=999999)[1]["bytes"] == 65536

    def test_a_negative_offset_becomes_zero(self) -> None:
        assert _request("range", _REF, offset=-5)[1] == {"offset": 0, "op": "range"}

    def test_a_run_output_is_read_through_the_run(self) -> None:
        path, params = _request("tail", _RUN_REF, lines=10)
        assert path == f"{_BASE}/capability-runs/{_RUN}/outputs/report/content"
        assert params == {"lines": 10, "op": "tail"}

    def test_a_run_output_range_pages_by_bytes(self) -> None:
        path, params = _request("range", _RUN_REF, offset=4096, nbytes=1024)
        assert path == f"{_BASE}/capability-runs/{_RUN}/outputs/report/content"
        assert params == {"offset": 4096, "bytes": 1024, "op": "range"}

    def test_a_slash_in_an_output_name_is_percent_encoded(self) -> None:
        path, _ = _request("tail", f"run:{_RUN}/outputs/a/b")
        assert path == f"{_BASE}/capability-runs/{_RUN}/outputs/a%2Fb/content"

    def test_a_ref_is_stripped_before_it_is_used(self) -> None:
        assert _request("stat", f"  {_REF}\n")[0] == f"{_BASE}/artifacts/{_REF}"


class TestReadRequestRefused:
    @pytest.mark.parametrize(
        "ref",
        [
            "",
            "   ",
            "art_XYZ",
            "art_" + "0123456789ABCDEF" * 2,
            "art_" + "0123456789abcdef" * 2 + "0",
            f"{_REF}/content",
            f"run:{_RUN}/outputs/..",
            f"run:{_RUN}/outputs/.",
            f"run:{_RUN}/outputs/",
            "run:not-a-uuid/outputs/report",
        ],
    )
    @pytest.mark.parametrize("op", ["stat", "head", "tail", "range"])
    def test_a_bad_ref_is_refused(self, op: str, ref: str) -> None:
        with pytest.raises(ValueError, match="ref"):
            _request(op, ref)

    def test_an_unknown_op_is_refused(self) -> None:
        with pytest.raises(ValueError, match="op must be one of"):
            _request("cat", _REF)


class TestReadResult:
    def test_list_drops_each_items_previews_and_keeps_the_rest(self) -> None:
        view = {
            "items": [
                {"ref": _REF, "task": "logs", "sizeBytes": 10, "head": "h" * 50, "tail": "t" * 50},
                {"ref": _OTHER_REF, "task": "pods", "sizeBytes": 20, "head": "h", "tail": "t"},
            ],
            "total": 2,
        }
        result = read_result("list", "", view)
        assert result["items"] == [
            {"ref": _REF, "task": "logs", "sizeBytes": 10},
            {"ref": _OTHER_REF, "task": "pods", "sizeBytes": 20},
        ]
        assert result["total"] == 2
        assert "op='stat'" in result["next"]

    def test_list_passes_through_an_item_that_is_not_a_dict(self) -> None:
        result = read_result("list", "", {"items": ["odd", {"ref": _REF, "head": "h"}]})
        assert result["items"] == ["odd", {"ref": _REF}]

    def test_stat_keeps_the_head_and_tail(self) -> None:
        view = {"ref": _REF, "sizeBytes": 10, "head": "first", "tail": "last"}
        result = read_result("stat", _REF, view)
        assert result["head"] == "first"
        assert result["tail"] == "last"

    def test_a_next_page_is_offered_for_an_artifact(self) -> None:
        view = {"op": "head", "text": "abc", "nextOffset": 4096, "prevOffset": None}
        hint = read_result("head", _REF, view)["next"]
        assert hint.startswith(f"next page: artifact_read(op='range', ref='{_REF}', offset=4096)")
        assert "previous page" not in hint
        assert "artifact-tools/grep" in hint

    def test_a_previous_offset_of_zero_still_counts(self) -> None:
        view = {"op": "range", "text": "abc", "nextOffset": None, "prevOffset": 0}
        hint = read_result("range", _REF, view)["next"]
        assert f"previous page: artifact_read(op='range', ref='{_REF}', offset=0)" in hint
        assert "next page" not in hint

    def test_both_neighbouring_pages_are_offered(self) -> None:
        view = {"op": "range", "text": "abc", "nextOffset": 300, "prevOffset": 100}
        hint = read_result("range", _REF, view)["next"]
        assert f"next page: artifact_read(op='range', ref='{_REF}', offset=300)" in hint
        assert f"previous page: artifact_read(op='range', ref='{_REF}', offset=100)" in hint

    def test_a_run_output_hint_only_pages(self) -> None:
        view = {"op": "head", "text": "abc", "nextOffset": 4096, "prevOffset": None}
        hint = read_result("head", _RUN_REF, view)["next"]
        assert hint.startswith(f"next page: artifact_read(op='range', ref='{_RUN_REF}', offset=")
        assert f"artifact_read(op='head'|'tail'|'range', ref='{_RUN_REF}') to page" in hint
        assert "artifact-tools" not in hint

    def test_a_stat_has_no_page_to_offer(self) -> None:
        view = {"ref": _REF, "sizeBytes": 10, "state": "ready"}
        hint = read_result("stat", _REF, view)["next"]
        assert "next page" not in hint
        assert "previous page" not in hint
        assert f"ref='{_REF}'" in hint

    def test_the_input_is_not_mutated(self) -> None:
        listing = {"items": [{"ref": _REF, "head": "h", "tail": "t"}], "total": 1}
        page = {"op": "head", "text": "abc", "nextOffset": 3, "prevOffset": None}
        for op, ref, view in (("list", "", listing), ("head", _REF, page)):
            before = copy.deepcopy(view)
            read_result(op, ref, view)
            assert view == before

    def test_a_view_that_is_not_a_dict_is_wrapped(self) -> None:
        result = read_result("stat", _REF, "oops")
        assert result["value"] == "oops"
        assert "next" in result


class TestFitOutput:
    def test_a_small_value_is_returned_unchanged(self) -> None:
        value = {"items": ["a", "b"], "n": 2}
        assert fit_output(value) == (value, False)

    def test_a_huge_string_is_cut_to_fit(self) -> None:
        value = {"log": "x" * 100_000}
        result, shortened = fit_output(value)
        assert shortened is True
        assert len(json.dumps(result)) <= MAX_OUTPUT_CHARS
        assert "chars omitted" in result["log"]
        assert result["log"].startswith("x")
        assert result["log"].endswith("x")

    def test_a_long_list_is_cut_and_says_how_many_items_were_left_out(self) -> None:
        value = [{"i": i} for i in range(10_000)]
        result, shortened = fit_output(value)
        assert shortened is True
        assert len(json.dumps(result)) <= MAX_OUTPUT_CHARS
        assert result[0] == {"i": 0}
        omitted = result[-1]["omittedItems"]
        assert omitted > 0
        assert len(result) - 1 + omitted == 10_000

    def test_a_value_that_cannot_be_cut_to_fit_is_dropped(self) -> None:
        # Thousands of keys survive every pass: only strings and lists shrink.
        value = {f"key{i}": i for i in range(10_000)}
        assert fit_output(value) == (None, True)


class TestIsArtifact:
    def test_a_handle_with_a_real_ref_is_an_artifact(self) -> None:
        assert is_artifact({"$artifact": _REF})
        assert is_artifact({"$artifact": _REF, "sizeBytes": 10_000_000, "head": "x", "tail": "y"})

    @pytest.mark.parametrize(
        "value",
        [
            {"$artifact": "not-a-ref"},
            {"$artifact": "art_XYZ"},
            {"$artifact": _REF + "0"},
            {"$artifact": f" {_REF}"},
            {"$artifact": None},
            {"$artifact": {"ref": _REF}},
            {"artifact": _REF},
            {},
            _REF,
            None,
            [{"$artifact": _REF}],
        ],
    )
    def test_anything_else_is_not(self, value) -> None:
        assert not is_artifact(value)


class TestHasArtifactInput:
    def test_a_bare_art_ref_input_counts(self) -> None:
        assert has_artifact_input({"source": _REF})
        assert has_artifact_input({"pattern": "error", "source": _REF})

    @pytest.mark.parametrize(
        "inputs",
        [
            {},
            {"source": "not-an-art-ref"},
            {"source": f"see {_REF} for details"},
            {"source": f"{_REF}/extra"},
            {"source": f" {_REF}"},
            {"source": 12},
            {"source": {"$artifact": _REF}},
            {"source": [_REF]},
        ],
    )
    def test_anything_else_does_not(self, inputs) -> None:
        assert not has_artifact_input(inputs)


class TestArtifactReadTool:
    def test_reads_a_page_and_says_what_to_read_next(self) -> None:
        view = {"op": "range", "text": "abc", "nextOffset": 3, "prevOffset": None, "totalBytes": 10}
        get = mock.AsyncMock(return_value=view)
        with mock.patch.object(server, "_papi_get", get):
            result = _artifact_read(op="range", ref=_REF)
        get.assert_awaited_once_with(
            f"{_BASE}/artifacts/{_REF}/content", params={"offset": 0, "op": "range"}
        )
        assert isinstance(result, str)
        data = json.loads(result)
        assert data["text"] == "abc"
        assert data["totalBytes"] == 10
        assert f"next page: artifact_read(op='range', ref='{_REF}', offset=3)" in data["next"]

    def test_lists_artifacts_without_their_previews(self) -> None:
        listing = {"items": [{"ref": _REF, "head": "h", "tail": "t"}], "total": 1}
        get = mock.AsyncMock(return_value=listing)
        with mock.patch.object(server, "_papi_get", get):
            result = _artifact_read(op="list", task="k8s-discovery/logs", run_id=_RUN)
        get.assert_awaited_once_with(f"{_BASE}/artifacts", params={"task": "logs", "runUuid": _RUN})
        data = json.loads(result)
        assert data["items"] == [{"ref": _REF}]
        assert data["total"] == 1

    def test_pages_a_shortened_cap_run_output(self) -> None:
        view = {"op": "tail", "text": "end", "nextOffset": None, "prevOffset": 900}
        get = mock.AsyncMock(return_value=view)
        with mock.patch.object(server, "_papi_get", get):
            result = _artifact_read(op="tail", ref=_RUN_REF, lines=20)
        get.assert_awaited_once_with(
            f"{_BASE}/capability-runs/{_RUN}/outputs/report/content",
            params={"lines": 20, "op": "tail"},
        )
        data = json.loads(result)
        assert data["text"] == "end"
        assert "offset=900" in data["next"]

    def test_a_bad_ref_is_refused_before_anything_is_sent(self) -> None:
        get = mock.AsyncMock()
        with (
            mock.patch.object(server, "_papi_get", get),
            pytest.raises(ValueError, match="ref"),
        ):
            _artifact_read(op="head", ref="art_XYZ")
        get.assert_not_called()

    def test_a_missing_ref_is_refused_before_anything_is_sent(self) -> None:
        get = mock.AsyncMock()
        with (
            mock.patch.object(server, "_papi_get", get),
            pytest.raises(ValueError, match="ref is required"),
        ):
            _artifact_read(op="stat", ref="")
        get.assert_not_called()
