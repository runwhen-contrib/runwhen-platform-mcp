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

    def test_sync_is_idempotent_when_content_unchanged(self, tmp_path: Path) -> None:
        # Regression for Bugbot LOW "Sync rewrites manifest every run"
        # on PR #17: a second sync against the same source tree must not
        # rewrite ``BUNDLE_MANIFEST.json`` — otherwise the scheduled
        # workflow pushes a no-op ``chore(bundle): sync ...`` commit
        # each Monday because the ``synced_at`` timestamp differs. Point
        # ``_BUNDLE_ROOT`` at a temp dir, mirror the file list from the
        # in-repo bundle, then run ``sync`` twice back-to-back and
        # confirm the manifest bytes are identical.
        import importlib
        import shutil as _shutil
        import sys

        sys.path.insert(0, str(_REPO_ROOT / "scripts"))
        try:
            sba = importlib.import_module("sync_bundled_authoring")
        finally:
            sys.path.pop(0)

        # Build a fake runwhen-local tree from the bundled files. Every
        # entry in ``_COPY_FILES``/`_COPY_DIRS` maps a source path to a
        # dest — reuse the current bundle content as the "source" so the
        # sync reports OK for every file.
        fake_rwl = tmp_path / "runwhen-local"
        fake_rwl.mkdir()
        for src_rel, dest_rel in sba._COPY_FILES:
            src = fake_rwl / src_rel
            src.parent.mkdir(parents=True, exist_ok=True)
            src.write_bytes((_BUNDLE / dest_rel).read_bytes())
        for src_dir_rel, dest_dir_rel in sba._COPY_DIRS:
            src_dir = fake_rwl / src_dir_rel
            src_dir.mkdir(parents=True, exist_ok=True)
            for md in sorted((_BUNDLE / dest_dir_rel).glob("*.md")):
                (src_dir / md.name).write_bytes(md.read_bytes())

        # Redirect the module's bundle root into a temp dir seeded from
        # the real bundle (so a first sync is a no-op).
        fake_bundle = tmp_path / "bundle"
        _shutil.copytree(_BUNDLE, fake_bundle)
        original_root = sba._BUNDLE_ROOT
        sba._BUNDLE_ROOT = fake_bundle
        try:
            manifest_path = fake_bundle / "BUNDLE_MANIFEST.json"
            # Force the initial manifest to reference the fake source
            # path so both sync runs use the same ``runwhen_local`` and
            # the unchanged short-circuit kicks in.
            initial = json.loads(manifest_path.read_text(encoding="utf-8"))
            initial["runwhen_local"] = str(fake_rwl.resolve())
            initial["synced_at"] = "1970-01-01T00:00:00Z"
            manifest_path.write_text(json.dumps(initial, indent=2) + "\n", encoding="utf-8")

            first_rc = sba.sync(fake_rwl, check=False)
            first_bytes = manifest_path.read_bytes()
            assert first_rc == 0
            # ``synced_at`` must be preserved — no rewrite happened.
            assert b'"synced_at": "1970-01-01T00:00:00Z"' in first_bytes

            second_rc = sba.sync(fake_rwl, check=False)
            second_bytes = manifest_path.read_bytes()
            assert second_rc == 0
            assert first_bytes == second_bytes
        finally:
            sba._BUNDLE_ROOT = original_root

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
