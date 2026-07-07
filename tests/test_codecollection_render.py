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


class TestRenderCodecollectionSkillTool:
    """Server-level tests for the render_codecollection_skill MCP tool."""

    def _call(self, **kwargs) -> dict:
        import asyncio
        import json as _json

        from runwhen_platform_mcp.server import render_codecollection_skill

        defaults = dict(
            bundle_name="my-health-check",
            alias="My Health Check",
            statement="The service should stay healthy.",
            workspace_name="dev-ws",
            script=SAMPLE_SCRIPT,
            task_title="Run my health check",
            interpreter="python",
        )
        defaults.update(kwargs)
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
