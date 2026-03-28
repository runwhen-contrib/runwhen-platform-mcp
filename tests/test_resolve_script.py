"""Tests for _resolve_script and script_path support (RW-441)."""

import pytest

from runwhen_platform_mcp.server import _resolve_script


class TestResolveScript:
    """Tests for the _resolve_script helper."""

    def test_inline_script_returned(self) -> None:
        assert _resolve_script("echo hello", None) == "echo hello"

    def test_file_script_returned(self, tmp_path) -> None:
        p = tmp_path / "script.sh"
        p.write_text("#!/bin/bash\nmain() { echo ok >&3; }")
        result = _resolve_script(None, str(p))
        assert "main()" in result

    def test_both_raises(self) -> None:
        with pytest.raises(ValueError, match="not both"):
            _resolve_script("inline", "/some/path")

    def test_neither_raises(self) -> None:
        with pytest.raises(ValueError, match="must be provided"):
            _resolve_script(None, None)

    def test_empty_string_script_raises(self) -> None:
        with pytest.raises(ValueError, match="must be provided"):
            _resolve_script("", None)

    def test_missing_file_raises(self) -> None:
        with pytest.raises(FileNotFoundError, match="not found"):
            _resolve_script(None, "/nonexistent/path/to/script.sh")

    def test_tilde_expansion(self, tmp_path, monkeypatch) -> None:
        p = tmp_path / "task.py"
        p.write_text("def main(): return []")
        monkeypatch.setenv("HOME", str(tmp_path))
        result = _resolve_script(None, "~/task.py")
        assert "def main()" in result

    def test_large_script_from_file(self, tmp_path) -> None:
        p = tmp_path / "big.sh"
        content = "main() {\n" + "echo line\n" * 5000 + "echo '[]' >&3\n}"
        p.write_text(content)
        result = _resolve_script(None, str(p))
        assert len(result) > 50000
        assert "main()" in result
