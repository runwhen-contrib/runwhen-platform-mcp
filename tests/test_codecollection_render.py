"""Tests for Custom Discovery CodeCollection rendering."""

import base64
import re

import yaml

from runwhen_platform_mcp.codecollection_render import (
    CodecollectionRenderInput,
    render_codecollection_files,
)

SAMPLE_SCRIPT = """def main():
    return [{
        "issue title": "Example issue",
        "issue description": "Something failed during the health check probe.",
        "issue severity": 3,
        "issue next steps": "Check logs and retry.",
    }]
"""


SLI_SCRIPT = """import random

def main():
    return random.random()
"""


class TestRenderCodecollectionFiles:
    def _render(self, **kwargs):
        inp = CodecollectionRenderInput(
            bundle_name="my-health-check",
            alias="My Health Check",
            statement="The service should stay healthy.",
            task_title="Run my health check",
            script=SAMPLE_SCRIPT,
            interpreter="python",
            env_vars={"NAMESPACE": "prod"},
            secret_vars={"kubeconfig": "kubeconfig"},
            owners=["reviewer@example.com"],
            source_workspace="dev-ws",
            **kwargs,
        )
        return render_codecollection_files(inp)

    def test_emits_expected_paths(self) -> None:
        files = self._render()
        prefix = "codebundles/my-health-check"
        assert f"{prefix}/README.md" in files
        assert f"{prefix}/.runwhen/SKILL_TEMPLATE.md" in files
        assert f"{prefix}/.runwhen/raw_script.py" in files
        assert f"{prefix}/.runwhen/generation-rules/my-health-check.yaml" in files
        assert f"{prefix}/.runwhen/templates/my-health-check-slx.yaml" in files
        assert f"{prefix}/.runwhen/templates/my-health-check-taskset.yaml" in files

    def test_generation_rule_uses_runwhen_platform(self) -> None:
        files = self._render()
        rule = yaml.safe_load(
            files["codebundles/my-health-check/.runwhen/generation-rules/my-health-check.yaml"]
        )
        assert rule["spec"]["platform"] == "runwhen"
        resource_types = rule["spec"]["generationRules"][0]["resourceTypes"]
        assert resource_types == ["workspace"]

    def test_taskset_embeds_base64_gen_cmd(self) -> None:
        files = self._render()
        taskset_text = files[
            "codebundles/my-health-check/.runwhen/templates/my-health-check-taskset.yaml"
        ]
        assert "codebundles/tool-builder/runbook.robot" in taskset_text
        assert "name: INTERPRETER" in taskset_text or "- name: INTERPRETER" in taskset_text
        assert "Run my health check" in taskset_text
        gen_match = re.search(
            r"- name: GEN_CMD\n\s+value: ['\"]?([A-Za-z0-9+/=]+)['\"]?\n",
            taskset_text,
        )
        assert gen_match is not None
        decoded = base64.b64decode(gen_match.group(1)).decode("utf-8")
        assert "def main():" in decoded

    def test_skill_template_points_to_raw_script_not_to_base64(self) -> None:
        files = self._render()
        review = files["codebundles/my-health-check/.runwhen/SKILL_TEMPLATE.md"]
        assert "raw_script.py" in review
        assert "Review artifact" in review
        assert "do **not** parse" in review.lower() or "should inspect" in review
        assert "`runwhen`" in review
        gen_b64 = base64.b64encode(SAMPLE_SCRIPT.encode()).decode()
        assert gen_b64 not in review

    def test_raw_script_contains_exact_script(self) -> None:
        files = self._render()
        raw = files["codebundles/my-health-check/.runwhen/raw_script.py"]
        assert raw == SAMPLE_SCRIPT
        assert "def main():" in raw

    def test_skill_template_lists_secrets_by_name_only(self) -> None:
        files = self._render()
        review = files["codebundles/my-health-check/.runwhen/SKILL_TEMPLATE.md"]
        assert "`kubeconfig`" in review
        assert "workspaceKey" not in review

    def test_skill_template_starts_at_column_zero(self) -> None:
        # Regression: the previous version used a textwrap.dedent-with-f-string
        # that mixed 8-space-indented outer lines with column-0 injected blocks
        # (tables, provenance). Because textwrap.dedent's common prefix was
        # then 0, the outer lines kept their 8-space indent and the resulting
        # markdown was rendered as an indented code block instead of a
        # heading. Guard against a recurrence by asserting the top-level
        # markers start at column 0.
        files = self._render()
        review = files["codebundles/my-health-check/.runwhen/SKILL_TEMPLATE.md"]
        for marker in ("# My Health Check", "## When this SLX gets created", "## Provenance"):
            assert marker in review, f"missing marker: {marker!r}"
            for line in review.splitlines():
                if line.lstrip() == marker:
                    assert line == marker, f"marker not at col 0: {line!r}"

    def test_slx_template_uses_jinja_placeholders(self) -> None:
        files = self._render()
        slx = files["codebundles/my-health-check/.runwhen/templates/my-health-check-slx.yaml"]
        assert "{{slx_name}}" in slx
        assert "{{workspace.owner_email}}" in slx
        assert "common-labels.yaml" in slx

    def test_optional_sli_template(self) -> None:
        files = self._render(include_sli=True, sli_script=SLI_SCRIPT, sli_interpreter="python")
        sli_path = "codebundles/my-health-check/.runwhen/templates/my-health-check-sli.yaml"
        assert sli_path in files
        sli_text = files[sli_path]
        assert "codebundles/tool-builder/sli.robot" in sli_text
        assert "intervalSeconds: 300" in sli_text

    def test_sli_without_explicit_script_raises(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="requires an explicit sli_script"):
            self._render(include_sli=True)

    def test_optional_sli_template_uses_sli_interpreter(self) -> None:
        files = self._render(include_sli=True, sli_script=SLI_SCRIPT, sli_interpreter="bash")
        sli_text = files["codebundles/my-health-check/.runwhen/templates/my-health-check-sli.yaml"]
        assert "- name: INTERPRETER" in sli_text
        assert "value: bash" in sli_text
        assert "codebundles/tool-builder/sli.robot" in sli_text

    def test_sli_template_uses_dedicated_sli_repo_when_set(self) -> None:
        # Regression for Bugbot MED "Render ignores SLI repo override" on
        # PR #17. commit_slx resolves runbook + SLI repos independently
        # (RB_CODE_BUNDLE vs SLI_CODE_BUNDLE); render_codecollection_skill
        # must do the same so per-bundle env overrides
        # (MCP_TOOL_BUILDER_SLI_REPO_URL) flow into the GitOps SLI
        # template. When generic_sli_repo_url is set, the SLI template
        # must use it; the runbook (taskset) template must still use
        # generic_repo_url.
        files = self._render(
            include_sli=True,
            sli_script=SLI_SCRIPT,
            sli_interpreter="python",
            generic_repo_url="https://mirror.internal/runbook-mirror.git",
            generic_sli_repo_url="https://mirror.internal/sli-mirror.git",
        )
        sli_text = files["codebundles/my-health-check/.runwhen/templates/my-health-check-sli.yaml"]
        taskset_text = files[
            "codebundles/my-health-check/.runwhen/templates/my-health-check-taskset.yaml"
        ]
        assert "https://mirror.internal/sli-mirror.git" in sli_text
        assert "https://mirror.internal/runbook-mirror.git" not in sli_text
        assert "https://mirror.internal/runbook-mirror.git" in taskset_text
        assert "https://mirror.internal/sli-mirror.git" not in taskset_text

    def test_sli_template_falls_back_to_runbook_repo_when_unset(self) -> None:
        # Backwards-compat: if the caller doesn't set generic_sli_repo_url
        # (older callers, or the runbook/SLI use the same mirror) the SLI
        # template continues to inherit ``generic_repo_url`` — no regression
        # for the common single-mirror case.
        files = self._render(
            include_sli=True,
            sli_script=SLI_SCRIPT,
            sli_interpreter="python",
            generic_repo_url="https://mirror.internal/shared-mirror.git",
        )
        sli_text = files["codebundles/my-health-check/.runwhen/templates/my-health-check-sli.yaml"]
        assert "https://mirror.internal/shared-mirror.git" in sli_text

    def _parse_slx_yaml(self, files: dict[str, str]) -> dict:
        slx = files["codebundles/my-health-check/.runwhen/templates/my-health-check-slx.yaml"]
        # Strip Jinja before yaml.safe_load (the workspace-builder renders them).
        stripped = re.sub(r"\{%.*?%\}", "_placeholder: 1", slx)
        stripped = re.sub(r"\{\{[^}]+\}\}", "X", stripped)
        return yaml.safe_load(stripped)

    def _render_minimal(self, **overrides) -> dict[str, str]:
        defaults = dict(
            bundle_name="my-health-check",
            alias="My Health Check",
            statement="The service should stay healthy.",
            task_title="Run my health check",
            script=SAMPLE_SCRIPT,
            interpreter="python",
        )
        defaults.update(overrides)
        return render_codecollection_files(CodecollectionRenderInput(**defaults))

    def test_slx_template_neutralises_jinja_in_alias_and_statement(self) -> None:
        # Regression: bugbot flagged that user text with `{{ }}` or `{% %}`
        # was injected raw into the SLX Jinja template, which broke
        # workspace-builder rendering.
        files = self._render_minimal(
            alias="SRE {{ team }} probe",
            statement="Only failing when {% if broken %} broken {% endif %}",
        )
        parsed = self._parse_slx_yaml(files)
        assert parsed["spec"]["alias"] == "SRE { { team } } probe"
        assert (
            parsed["spec"]["statement"]
            == "Only failing when { % if broken % } broken { % endif % }"
        )

    def test_slx_template_yaml_quotes_resource_path_with_special_chars(self) -> None:
        # Regression: bugbot flagged that resource_path was written as a raw
        # double-quoted scalar without proper YAML escaping, so a path
        # containing quotes or colons could break the template.
        files = self._render_minimal(resource_path='custom/foo/"weird": path')
        parsed = self._parse_slx_yaml(files)
        assert parsed["spec"]["additionalContext"]["resourcePath"] == 'custom/foo/"weird": path'

    def test_slx_template_yaml_quotes_secret_workspace_keys(self) -> None:
        # Regression: bugbot flagged that resolved RunWhen keys (e.g.
        # `k8s:file@secret/...`) contain colons and were written unquoted.
        files = self._render_minimal(secret_vars={"KUBECONFIG": "k8s:file@secret/kube.config"})
        taskset = files[
            "codebundles/my-health-check/.runwhen/templates/my-health-check-taskset.yaml"
        ]
        stripped = re.sub(r"\{%.*?%\}", "_placeholder: 1", taskset)
        stripped = re.sub(r"\{\{[^}]+\}\}", "X", stripped)
        parsed = yaml.safe_load(stripped)
        secrets = parsed["spec"]["secretsProvided"]
        assert secrets[0]["name"] == "KUBECONFIG"
        assert secrets[0]["workspaceKey"] == "k8s:file@secret/kube.config"

    def test_taskset_template_neutralises_jinja_in_task_title(self) -> None:
        # Regression: bugbot flagged that TASK_TITLE was written into the
        # taskset Jinja template with YAML quoting only, while alias/statement
        # were `_escape_jinja`-neutralised. A title containing `{{` or `{%`
        # was still parsed as template syntax at workspace-builder render time.
        files = self._render_minimal(task_title="Run {{ probe }} for {% team %}")
        taskset = files[
            "codebundles/my-health-check/.runwhen/templates/my-health-check-taskset.yaml"
        ]
        stripped = re.sub(r"\{%.*?%\}", "_placeholder: 1", taskset)
        stripped = re.sub(r"\{\{[^}]+\}\}", "X", stripped)
        parsed = yaml.safe_load(stripped)
        env_pairs = {
            item["name"]: item["value"]
            for item in parsed["spec"]["configProvided"]
            if isinstance(item, dict)
        }
        assert env_pairs["TASK_TITLE"] == "Run { { probe } } for { % team % }"

    def test_sli_template_neutralises_jinja_in_alias(self) -> None:
        # Regression: bugbot flagged that when include_sli=True the SLI Jinja
        # template embedded `inp.alias` raw in spec.description, so an alias
        # with `{{` or `{%` broke SLI rendering (the SLX template already
        # neutralises it).
        files = self._render_minimal(
            alias="SRE {{ team }} probe",
            include_sli=True,
            sli_script=SLI_SCRIPT,
            sli_interpreter="python",
        )
        sli_text = files["codebundles/my-health-check/.runwhen/templates/my-health-check-sli.yaml"]
        # Raw Jinja must not survive into the SLI template.
        assert "{{ team }}" not in sli_text
        assert "SRE { { team } } probe" in sli_text
        stripped = re.sub(r"\{%.*?%\}", "_placeholder: 1", sli_text)
        stripped = re.sub(r"\{\{[^}]+\}\}", "X", stripped)
        parsed = yaml.safe_load(stripped)
        assert parsed["spec"]["description"] == "Tool Builder SLI for SRE { { team } } probe"

    def test_slx_template_neutralises_jinja_in_resource_path(self) -> None:
        # Regression: bugbot flagged that resource_path was YAML-quoted but
        # NOT `_escape_jinja`-neutralised, so a path containing `{{` or `{%`
        # was still parsed as workspace-builder template syntax at
        # discovery-render time (unlike alias / statement / task_title,
        # which are already neutralised).
        files = self._render_minimal(resource_path="custom/{{ tenant }}/{% shard %}")
        parsed = self._parse_slx_yaml(files)
        assert (
            parsed["spec"]["additionalContext"]["resourcePath"]
            == "custom/{ { tenant } }/{ % shard % }"
        )
        slx = files["codebundles/my-health-check/.runwhen/templates/my-health-check-slx.yaml"]
        # Raw Jinja delimiters must not survive into the SLX template body —
        # otherwise workspace-builder will try to interpret them.
        assert "{{ tenant }}" not in slx
        assert "{% shard %}" not in slx

    def test_slx_template_neutralises_jinja_in_image_url(self) -> None:
        # Regression: bugbot flagged that `image_url` was interpolated into
        # the SLX Jinja template without `_escape_jinja` or `_yaml_quote`,
        # so a user-supplied icon URL containing Jinja delimiters was
        # treated as template syntax at render time (and the missing YAML
        # quoting could also break the template on colons/special chars).
        files = self._render_minimal(image_url="https://cdn/{{ theme }}/icon.svg")
        parsed = self._parse_slx_yaml(files)
        assert parsed["spec"]["imageURL"] == "https://cdn/{ { theme } }/icon.svg"
        slx = files["codebundles/my-health-check/.runwhen/templates/my-health-check-slx.yaml"]
        assert "{{ theme }}" not in slx

    def test_slx_template_yaml_quotes_default_image_url(self) -> None:
        # The default image URL contains no colons but is still a plain URL —
        # once we YAML-quote it defensively, the parsed value should stay
        # identical to the raw URL for the common no-override case.
        files = self._render_minimal()
        parsed = self._parse_slx_yaml(files)
        assert parsed["spec"]["imageURL"].startswith("https://storage.googleapis.com/")


class TestKubernetesDiscoveryRender:
    """Regression for customer-project-g-research#71 — kubernetes namespace bundle."""

    ISSUE_MATCH_RULES = [
        {
            "type": "pattern",
            "pattern": "^example-app$",
            "properties": ["name"],
            "mode": "substring",
        }
    ]

    def _render_k8s_namespace(self, **kwargs) -> dict[str, str]:
        defaults = dict(
            bundle_name="ex-kfk-dly",
            alias="Check Kafka consumption delay",
            statement=("Kafka consumption delay (p95) should stay below 60s."),
            task_title="Check Kafka consumption delay",
            script=SAMPLE_SCRIPT,
            interpreter="python",
            platform="kubernetes",
            resource_types=["namespace"],
            match_rules=self.ISSUE_MATCH_RULES,
            slx_qualifiers=["namespace", "cluster"],
            base_name="ex-kfk-dly",
            access="read-only",
            data="logs-bulk",
        )
        defaults.update(kwargs)
        return render_codecollection_files(CodecollectionRenderInput(**defaults))

    def test_generation_rule_uses_kubernetes_platform(self) -> None:
        files = self._render_k8s_namespace()
        rule = yaml.safe_load(
            files["codebundles/ex-kfk-dly/.runwhen/generation-rules/ex-kfk-dly.yaml"]
        )
        assert rule["spec"]["platform"] == "kubernetes"
        gen = rule["spec"]["generationRules"][0]
        assert gen["resourceTypes"] == ["namespace"]
        assert gen["slxs"][0]["qualifiers"] == ["namespace", "cluster"]

    def test_slx_template_includes_kubernetes_includes(self) -> None:
        files = self._render_k8s_namespace()
        slx = files["codebundles/ex-kfk-dly/.runwhen/templates/ex-kfk-dly-slx.yaml"]
        assert '{% include "kubernetes-tags.yaml" ignore missing %}' in slx
        assert '{% include "kubernetes-hierarchy.yaml" ignore missing %}' in slx
        assert "value: runwhen" not in slx
        assert "- name: platform" not in slx or "value: runwhen" not in slx

    def test_slx_uses_kubernetes_icon_by_default(self) -> None:
        files = self._render_k8s_namespace()
        slx = files["codebundles/ex-kfk-dly/.runwhen/templates/ex-kfk-dly-slx.yaml"]
        assert "icons/kubernetes.svg" in slx


class TestAzureDiscoveryRender:
    def test_slx_includes_azure_includes(self) -> None:
        files = render_codecollection_files(
            CodecollectionRenderInput(
                bundle_name="az-check",
                alias="Azure check",
                statement="Should be healthy.",
                task_title="Run azure check",
                script=SAMPLE_SCRIPT,
                platform="azure",
                resource_types=["azure_appservice_web_apps"],
                slx_qualifiers=["resource", "resource_group"],
            )
        )
        slx = files["codebundles/az-check/.runwhen/templates/az-check-slx.yaml"]
        assert '{% include "azure-tags.yaml" ignore missing %}' in slx
        assert '{% include "azure-hierarchy.yaml" ignore missing %}' in slx


class TestRenderCodecollectionSkillTool:
    """Server-level tests for the render_codecollection_skill MCP tool."""

    def _call(self, **kwargs) -> dict:
        import asyncio
        import json as _json
        from unittest import mock

        from runwhen_platform_mcp import server as _server
        from runwhen_platform_mcp.server import render_codecollection_skill

        defaults = dict(
            bundle_name="my-health-check",
            alias="My Health Check",
            statement="The service should stay healthy.",
            workspace_name="dev-ws",
            script=SAMPLE_SCRIPT,
            task_title="Run my health check",
            interpreter="python",
            access="read-write",
            data="logs-bulk",
            platform="runwhen",
            generic_runtime_ref="main",
            timeout_seconds=300,
            include_sli=False,
            sli_interval_seconds=300,
        )
        defaults.update(kwargs)

        async def _fake_resolve_workspace(_name):
            return "dev-ws"

        async def _fake_get_user_email():
            return "reviewer@example.com"

        async def _fake_prepare_secrets(_ws, secrets):
            return secrets, []

        async def _fake_resolve_url(*, explicit=None, bundle=None):
            return "https://github.com/runwhen-contrib/rw-generic-codecollection.git", "default"

        with (
            mock.patch.object(_server, "_resolve_workspace", side_effect=_fake_resolve_workspace),
            mock.patch.object(_server, "_get_user_email", side_effect=_fake_get_user_email),
            mock.patch.object(
                _server,
                "_prepare_secret_vars_for_author",
                side_effect=_fake_prepare_secrets,
            ),
            mock.patch.object(
                _server,
                "_resolve_generic_codecollection_url",
                side_effect=_fake_resolve_url,
            ),
        ):
            result = asyncio.run(render_codecollection_skill(**defaults))
        return _json.loads(result)

    def test_bundle_name_with_underscore_is_rejected(self) -> None:
        # Regression: bugbot flagged that render_codecollection_skill silently
        # rewrote `foo_bar` → `foo-bar` for validation but still emitted
        # `codebundles/foo_bar/`. The name must be rejected outright so the
        # user renames to kebab-case before any filesystem side effects.
        response = self._call(bundle_name="foo_bar")
        assert "error" in response
        assert "Invalid bundle_name" in response["error"]
        assert "foo_bar" in response["error"]

    def test_bundle_name_uppercase_is_rejected(self) -> None:
        response = self._call(bundle_name="FooBar")
        assert "error" in response
        assert "Invalid bundle_name" in response["error"]

    def test_valid_kebab_bundle_name_is_accepted(self) -> None:
        response = self._call(bundle_name="foo-bar")
        # Accepted names produce a normal render result (files map), not an
        # "Invalid bundle_name" error. Any downstream error is fine — we only
        # care that the name-validation gate itself passes.
        assert response.get("error", "").startswith("Invalid bundle_name") is False

    def _call_with_papi_mocked(self, **kwargs) -> dict:
        # ``render_codecollection_skill`` calls ``_resolve_workspace`` /
        # ``_get_user_email`` (both hit PAPI, both need RUNWHEN_TOKEN) before
        # reaching the code-bundle URL resolver. Stub them so tests can
        # exercise the resolver-plumbing logic without a live token.
        #
        # Also fill in every ``Field(default=...)`` value explicitly. The
        # tool declares them via pydantic's ``Field(default=...)`` sentinel,
        # which FastMCP unwraps at wire-time — a direct in-process call
        # would otherwise pass ``FieldInfo`` objects through as if they
        # were the values, breaking the render pipeline downstream (yaml
        # dump on ``PydanticUndefined``).
        import asyncio
        import json as _json
        from unittest import mock

        from runwhen_platform_mcp import server as _server
        from runwhen_platform_mcp.server import render_codecollection_skill

        resolver = kwargs.pop("_url_resolver", None)
        assert resolver is not None, "must pass _url_resolver for these tests"

        defaults = dict(
            bundle_name="my-health-check",
            alias="My Health Check",
            statement="The service should stay healthy.",
            workspace_name="dev-ws",
            script=SAMPLE_SCRIPT,
            task_title="Run my health check",
            interpreter="python",
            access="read-write",
            data="logs-bulk",
            platform="runwhen",
            generic_runtime_ref="main",
            timeout_seconds=300,
            include_sli=False,
            sli_interval_seconds=300,
        )
        defaults.update(kwargs)

        async def _fake_resolve_workspace(_name):
            return "dev-ws"

        async def _fake_get_user_email():
            return "reviewer@example.com"

        async def _fake_prepare_secrets(_ws, secrets):
            return secrets, []

        with (
            mock.patch.object(_server, "_resolve_workspace", side_effect=_fake_resolve_workspace),
            mock.patch.object(_server, "_get_user_email", side_effect=_fake_get_user_email),
            mock.patch.object(
                _server,
                "_prepare_secret_vars_for_author",
                side_effect=_fake_prepare_secrets,
            ),
            mock.patch.object(
                _server,
                "_resolve_generic_codecollection_url",
                side_effect=resolver,
            ),
        ):
            result = asyncio.run(render_codecollection_skill(**defaults))
        return _json.loads(result)

    def test_render_resolves_runbook_and_sli_repos_independently(self) -> None:
        # Regression for Bugbot MED "Render ignores SLI repo override" on
        # PR #17. commit_slx resolves RB_CODE_BUNDLE / SLI_CODE_BUNDLE
        # separately so per-bundle env overrides
        # (MCP_TOOL_BUILDER_RUNBOOK_REPO_URL vs
        # MCP_TOOL_BUILDER_SLI_REPO_URL) flow through to the runtime.
        # render_codecollection_skill must do the same so the GitOps
        # output matches the runtime.
        from runwhen_platform_mcp import server as _server

        async def _resolver(*, explicit=None, bundle=None):
            if explicit:
                return explicit, "explicit"
            if bundle is _server.SLI_CODE_BUNDLE:
                return "https://mirror.internal/sli.git", "env"
            return "https://mirror.internal/runbook.git", "env"

        # Use a validator-friendly SLI body (returns a numeric literal so
        # ``_validate_script`` doesn't emit a blocking warning about the
        # SLI contract — the actual expression is irrelevant to this test).
        sli_body = "def main():\n    return 0.5\n"
        response = self._call_with_papi_mocked(
            _url_resolver=_resolver,
            include_sli=True,
            sli_script=sli_body,
            sli_interpreter="python",
        )
        # Both URLs must appear in the response payload so operators can
        # confirm each side picked up the intended mirror.
        assert response.get("generic_repo_runbook_url") == "https://mirror.internal/runbook.git"
        assert response.get("generic_repo_sli_url") == "https://mirror.internal/sli.git"
        # And the rendered SLI template must use the SLI mirror, not the
        # runbook mirror.
        sli_yaml = response["files"][
            "codebundles/my-health-check/.runwhen/templates/my-health-check-sli.yaml"
        ]
        taskset_yaml = response["files"][
            "codebundles/my-health-check/.runwhen/templates/my-health-check-taskset.yaml"
        ]
        assert "https://mirror.internal/sli.git" in sli_yaml
        assert "https://mirror.internal/runbook.git" not in sli_yaml
        assert "https://mirror.internal/runbook.git" in taskset_yaml
        assert "https://mirror.internal/sli.git" not in taskset_yaml

    def test_render_without_sli_does_not_resolve_sli_repo(self) -> None:
        # When include_sli=False we don't need an SLI URL at all — the SLI
        # template isn't emitted. Avoid the extra PAPI round-trip and
        # confirm the response's sli fields are null.
        from runwhen_platform_mcp import server as _server

        sli_bundle_seen = False

        async def _resolver(*, explicit=None, bundle=None):
            nonlocal sli_bundle_seen
            if bundle is _server.SLI_CODE_BUNDLE:
                sli_bundle_seen = True
            return "https://mirror.internal/runbook.git", "env"

        response = self._call_with_papi_mocked(
            _url_resolver=_resolver,
            include_sli=False,
        )
        assert sli_bundle_seen is False
        assert response.get("generic_repo_sli_url") is None
        assert response.get("generic_repo_sli_resolved_from") is None

    def test_explicit_repo_url_applies_to_both_runbook_and_sli(self) -> None:
        # Regression for Bugbot MED "Explicit repo URL skips SLI" on PR
        # #17. When a caller passes ``generic_runtime_repo_url``, it must
        # override BOTH the runbook AND the SLI resolver — otherwise a
        # GitOps bundle can ship a taskset pointing at the operator's
        # mirror while the SLI template still references the public
        # github.com default (or a diverged workspace URL). Runners /
        # PAPI then see an inconsistent runbook+SLI pair that airgap
        # clusters cannot actually pull the SLI half of.
        sli_bundle: list[str | None] = []
        rb_bundle: list[str | None] = []

        async def _resolver(*, explicit=None, bundle=None):
            # Capture which bundle got the explicit arg — both must see
            # it when include_sli=True. The resolver's contract is
            # ``explicit`` wins over ``bundle`` when both are set, so
            # returning the same explicit URL here matches production.
            from runwhen_platform_mcp import server as _server

            if bundle is _server.SLI_CODE_BUNDLE:
                sli_bundle.append(explicit)
                return explicit or "unset", "explicit"
            if bundle is _server.RB_CODE_BUNDLE:
                rb_bundle.append(explicit)
                return explicit or "unset", "explicit"
            return "unset", "default"

        response = self._call_with_papi_mocked(
            _url_resolver=_resolver,
            include_sli=True,
            sli_script="def main():\n    return 0.5\n",
            sli_interpreter="python",
            generic_runtime_repo_url="https://mirror.internal/override.git",
        )
        assert rb_bundle == ["https://mirror.internal/override.git"], rb_bundle
        assert sli_bundle == ["https://mirror.internal/override.git"], sli_bundle
        # And the rendered SLI template must reflect the explicit
        # override — not a fallback URL — so the GitOps bundle is
        # internally consistent.
        sli_yaml = response["files"][
            "codebundles/my-health-check/.runwhen/templates/my-health-check-sli.yaml"
        ]
        assert "https://mirror.internal/override.git" in sli_yaml

    def test_invalid_kubernetes_resource_type_is_rejected(self) -> None:
        response = self._call(
            platform="kubernetes",
            resource_types=["not-a-real-kind"],
            match_rules=[{"type": "pattern", "pattern": ".+", "properties": ["name"]}],
        )
        assert response.get("error") == "Invalid discovery platform configuration"
        assert any("Unknown kubernetes resource type" in d for d in response["details"])

    def test_hierarchy_rejected_for_kubernetes(self) -> None:
        response = self._call(
            platform="kubernetes",
            resource_types=["namespace"],
            hierarchy=["platform", "kubernetes"],
        )
        assert response.get("error") == "Invalid discovery platform configuration"
        assert any("hierarchy is not used" in d for d in response["details"])
