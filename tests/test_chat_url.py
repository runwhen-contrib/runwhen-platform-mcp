"""Tests for workspace chat browser URL helpers."""

from runwhen_platform_mcp.server import (
    _derive_runwhen_app_url_from_papi,
    _format_workspace_chat_browser_url,
)


def test_derive_runwhen_app_url_from_papi() -> None:
    assert _derive_runwhen_app_url_from_papi("https://papi.test.runwhen.com") == (
        "https://app.test.runwhen.com"
    )
    assert _derive_runwhen_app_url_from_papi("https://papi.beta.runwhen.com/") == (
        "https://app.beta.runwhen.com"
    )
    assert _derive_runwhen_app_url_from_papi("") == ""
    # Internal URLs produce unusable app URLs; callers should set RUNWHEN_APP_URL instead
    assert (
        _derive_runwhen_app_url_from_papi("http://papi.backend-services.svc.cluster.local")
        == "http://app.backend-services.svc.cluster.local"
    )


def test_format_workspace_chat_browser_url() -> None:
    u = _format_workspace_chat_browser_url(
        "https://app.test.runwhen.com",
        "t-oncall",
        "2ca78762-6165-4adb-991c-ba11b98952e2",
    )
    assert u == (
        "https://app.test.runwhen.com/workspace/t-oncall/workspace-chat?"
        "session=2ca78762-6165-4adb-991c-ba11b98952e2"
    )


def test_format_workspace_chat_browser_url_encodes_workspace() -> None:
    u = _format_workspace_chat_browser_url(
        "https://app.test.runwhen.com",
        "space name",
        "sid",
    )
    assert "space%20name" in u
    assert u.endswith("?session=sid")
