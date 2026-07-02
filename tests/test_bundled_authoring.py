"""Tests for airgap bundled authoring reference."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BUNDLE = _REPO_ROOT / "skills" / "author-generation-rules" / "references"


class TestBundledAuthoring:
    def test_manifest_exists(self) -> None:
        manifest_path = _BUNDLE / "BUNDLE_MANIFEST.json"
        assert manifest_path.is_file()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["files"]
        assert len(manifest["files"]) >= 10

    def test_schema_and_catalogs_present(self) -> None:
        required = [
            "generation-rules-schema.md",
            "generation-rules-syntax.md",
            "generation-rule-schema.json",
            "catalogs/azure-resource-catalog.md",
            "catalogs/aws-resource-catalog.md",
            "examples/azure-keyvault-slx.md",
        ]
        for name in required:
            path = _BUNDLE / name
            assert path.is_file(), f"missing bundled file: {name}"
            assert path.stat().st_size > 100

    def test_generation_rule_schema_json_valid(self) -> None:
        schema_path = _BUNDLE / "generation-rule-schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        assert schema.get("title") == "Generation Rules"
        assert schema["properties"]["kind"]["const"] == "GenerationRules"

    def test_bundle_matches_runwhen_local_when_adjacent(self) -> None:
        rwl = _REPO_ROOT.parent / "runwhen-local"
        if not rwl.is_dir():
            pytest.skip("adjacent runwhen-local checkout not present")
        import subprocess

        result = subprocess.run(
            [
                "python",
                str(_REPO_ROOT / "scripts" / "sync_bundled_authoring.py"),
                "--runwhen-local",
                str(rwl),
                "--check",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
