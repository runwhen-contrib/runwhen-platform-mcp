"""Tests for bundled indexer resource-type catalogs."""

import json
from pathlib import Path

from runwhen_platform_mcp.bundled_skills import authoring_references_root
from runwhen_platform_mcp.codecollection_platform_profiles import (
    SUPPORTED_PLATFORMS,
    get_platform_profile,
    list_platform_profiles,
)
from runwhen_platform_mcp.indexed_resource_catalog import (
    build_resource_type_index,
    catalog_reference_for_agents,
    get_known_resource_types,
    is_kubernetes_crd_resource_type,
    list_resource_types_response,
    search_resource_types,
    validate_qualifiers,
    validate_resource_types,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]


class TestPlatformProfiles:
    def test_all_supported_platforms_have_profiles(self) -> None:
        for platform in SUPPORTED_PLATFORMS:
            profile = get_platform_profile(platform)
            assert profile.platform == platform

    def test_list_platform_profiles_includes_kubernetes(self) -> None:
        names = {row["platform"] for row in list_platform_profiles()}
        assert "kubernetes" in names
        assert "runwhen" in names


class TestKubernetesCatalog:
    def test_builtin_namespace_is_known(self) -> None:
        known = get_known_resource_types("kubernetes")
        assert "namespace" in known
        assert "deployment" in known

    def test_crd_pattern_accepts_upbound_bucket(self) -> None:
        assert is_kubernetes_crd_resource_type("buckets.storage.gcp.upbound.io")

    def test_unknown_builtin_is_rejected(self) -> None:
        errors = validate_resource_types("kubernetes", ["not-a-real-kind"])
        assert len(errors) == 1
        assert "Unknown kubernetes resource type" in errors[0]

    def test_crd_not_in_catalog_is_accepted(self) -> None:
        assert validate_resource_types("kubernetes", ["mycrd.example.com/v1"]) == []


class TestRunwhenCatalog:
    def test_workspace_is_only_type(self) -> None:
        known = get_known_resource_types("runwhen")
        assert "workspace" in known

    def test_invalid_runwhen_type_rejected(self) -> None:
        errors = validate_resource_types("runwhen", ["deployment"])
        assert errors


class TestQualifierValidation:
    def test_kubernetes_namespace_cluster_ok(self) -> None:
        assert validate_qualifiers("kubernetes", ["namespace", "cluster"]) == []

    def test_runwhen_rejects_cluster_qualifier(self) -> None:
        errors = validate_qualifiers("runwhen", ["cluster"])
        assert errors


class TestSearchResourceTypes:
    def test_kubernetes_deployment_search(self) -> None:
        matches = search_resource_types("kubernetes", search="deploy")
        assert "deployment" in matches

    def test_azure_search_returns_limited_results(self) -> None:
        matches = search_resource_types("azure", search="azure_keyvault", limit=5)
        assert matches
        assert len(matches) <= 5


class TestBundledIndexJson:
    def test_index_json_exists_and_covers_platforms(self) -> None:
        index_path = authoring_references_root() / "catalogs/indexed-resource-types.json"
        assert index_path.is_file(), "run sync_bundled_authoring or write_resource_type_index"
        data = json.loads(index_path.read_text(encoding="utf-8"))
        for platform in SUPPORTED_PLATFORMS:
            assert platform in data
            assert len(data[platform]) > 0

    def test_json_matches_markdown_parse(self) -> None:
        built = build_resource_type_index()
        index_path = authoring_references_root() / "catalogs/indexed-resource-types.json"
        on_disk = json.loads(index_path.read_text(encoding="utf-8"))
        assert built == on_disk


class TestAirgapCatalogResponses:
    def test_airgap_omits_external_docs_url(self, monkeypatch) -> None:
        monkeypatch.setenv("RUNWHEN_AIRGAP", "true")
        ref = catalog_reference_for_agents("kubernetes")
        assert ref["indexed_resources_url"] is None
        assert ref["airgap_note"]
        assert ref["bundled_catalog_path"].endswith("kubernetes-resource-catalog.md")

    def test_list_response_requires_search_for_azure(self) -> None:
        resp = list_resource_types_response("azure", search="", limit=50)
        assert resp["search_required"] is True
        assert resp["returned_count"] == 0
        assert "offline" in resp["message"].lower()

    def test_list_response_kubernetes_without_search_ok(self) -> None:
        resp = list_resource_types_response("kubernetes", search="", limit=50)
        assert resp["search_required"] is False
        assert resp["returned_count"] > 0
        assert "namespace" in resp["resource_types"]


class TestSkillsDirOverride:
    def test_resolves_catalog_from_runwhen_skills_dir(self, tmp_path, monkeypatch) -> None:
        bundle = tmp_path / "bundle"
        refs = bundle / "author-generation-rules" / "references" / "catalogs"
        refs.mkdir(parents=True)
        payload = {"runwhen": ["workspace"], "kubernetes": ["namespace"]}
        (refs / "indexed-resource-types.json").write_text(json.dumps(payload), encoding="utf-8")
        monkeypatch.setenv("RUNWHEN_SKILLS_DIR", str(bundle))
        from runwhen_platform_mcp.indexed_resource_catalog import (
            _index_json_cache,
            get_known_resource_types,
        )

        get_known_resource_types.cache_clear()
        _index_json_cache.cache_clear()
        assert get_known_resource_types("kubernetes") == frozenset({"namespace"})
