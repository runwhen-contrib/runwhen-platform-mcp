"""Platform output profiles for Custom Discovery CodeCollection rendering.

Each profile defines how SLX templates are shaped for a discovery indexer
(``kubernetes``, ``azure``, …) vs workspace-scoped tool-builder (``runwhen``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

_ICON_BASE = "https://storage.googleapis.com/runwhen-nonprod-shared-images/icons"

SUPPORTED_PLATFORMS: Final[frozenset[str]] = frozenset(
    {"runwhen", "kubernetes", "azure", "aws", "gcp"}
)


@dataclass(frozen=True)
class PlatformOutputProfile:
    """Rendering contract for a generation-rule ``spec.platform`` value."""

    platform: str
    default_resource_types: tuple[str, ...]
    default_qualifiers: tuple[str, ...]
    default_image_url: str
    tag_include: str | None
    hierarchy_include: str | None
    inject_platform_tag: bool
    allow_manual_hierarchy: bool
    allow_manual_resource_path: bool
    valid_qualifiers: frozenset[str]
    catalog_doc_path: str


_PROFILES: dict[str, PlatformOutputProfile] = {
    "runwhen": PlatformOutputProfile(
        platform="runwhen",
        default_resource_types=("workspace",),
        default_qualifiers=("workspace",),
        default_image_url=f"{_ICON_BASE}/runwhen.svg",
        tag_include=None,
        hierarchy_include=None,
        inject_platform_tag=True,
        allow_manual_hierarchy=True,
        allow_manual_resource_path=True,
        valid_qualifiers=frozenset({"workspace"}),
        catalog_doc_path="catalogs/runwhen-platform-resource-catalog.md",
    ),
    "kubernetes": PlatformOutputProfile(
        platform="kubernetes",
        default_resource_types=("namespace",),
        default_qualifiers=("namespace", "cluster"),
        default_image_url=f"{_ICON_BASE}/kubernetes.svg",
        tag_include="kubernetes-tags.yaml",
        hierarchy_include="kubernetes-hierarchy.yaml",
        inject_platform_tag=False,
        allow_manual_hierarchy=False,
        allow_manual_resource_path=False,
        valid_qualifiers=frozenset({"cluster", "namespace", "resource", "name"}),
        catalog_doc_path="catalogs/kubernetes-resource-catalog.md",
    ),
    "azure": PlatformOutputProfile(
        platform="azure",
        default_resource_types=("azure_appservice_web_apps",),
        default_qualifiers=("resource", "resource_group"),
        default_image_url=f"{_ICON_BASE}/azure/app%20services/10035-icon-service-App-Services.svg",
        tag_include="azure-tags.yaml",
        hierarchy_include="azure-hierarchy.yaml",
        inject_platform_tag=False,
        allow_manual_hierarchy=False,
        allow_manual_resource_path=False,
        valid_qualifiers=frozenset(
            {
                "resource",
                "resource_group",
                "subscription_name",
                "subscription_id",
                "project",
                "organization",
            }
        ),
        catalog_doc_path="catalogs/azure-resource-catalog.md",
    ),
    "aws": PlatformOutputProfile(
        platform="aws",
        default_resource_types=("aws_ec2_instances",),
        default_qualifiers=("resource", "region", "account_name"),
        default_image_url=f"{_ICON_BASE}/aws/eks.png",
        tag_include="aws-tags.yaml",
        hierarchy_include="aws-hierarchy.yaml",
        inject_platform_tag=False,
        allow_manual_hierarchy=False,
        allow_manual_resource_path=False,
        valid_qualifiers=frozenset({"resource", "region", "account_name", "account_id", "name"}),
        catalog_doc_path="catalogs/aws-resource-catalog.md",
    ),
    "gcp": PlatformOutputProfile(
        platform="gcp",
        default_resource_types=("gcp_compute_instances",),
        default_qualifiers=("resource", "project"),
        default_image_url=(
            f"{_ICON_BASE}/gcp/google_kubernetes_engine/google_kubernetes_engine.svg"
        ),
        tag_include="gcp-tags.yaml",
        hierarchy_include="gcp-hierarchy.yaml",
        inject_platform_tag=False,
        allow_manual_hierarchy=False,
        allow_manual_resource_path=False,
        valid_qualifiers=frozenset({"resource", "project", "name"}),
        catalog_doc_path="catalogs/gcp-resource-catalog.md",
    ),
}


def get_platform_profile(platform: str) -> PlatformOutputProfile:
    """Return the output profile for *platform* (case-insensitive)."""
    key = platform.strip().lower()
    if key not in _PROFILES:
        supported = ", ".join(sorted(SUPPORTED_PLATFORMS))
        raise ValueError(f"Unsupported platform {platform!r}. Supported values: {supported}.")
    return _PROFILES[key]


def list_platform_profiles() -> list[dict[str, object]]:
    """Summaries for MCP tools and agent skills."""
    from runwhen_platform_mcp.indexed_resource_catalog import catalog_reference_for_agents

    rows: list[dict[str, object]] = []
    for name in sorted(_PROFILES):
        profile = _PROFILES[name]
        ref = catalog_reference_for_agents(name)
        rows.append(
            {
                "platform": profile.platform,
                "default_resource_types": list(profile.default_resource_types),
                "default_qualifiers": list(profile.default_qualifiers),
                "uses_tag_include": profile.tag_include,
                "uses_hierarchy_include": profile.hierarchy_include,
                "catalog_doc_path": profile.catalog_doc_path,
                "bundled_catalog_path": ref["bundled_catalog_path"],
                "indexed_resources_url": ref.get("indexed_resources_url"),
            }
        )
    return rows
