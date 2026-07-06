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

    def test_slx_template_uses_jinja_placeholders(self) -> None:
        files = self._render()
        slx = files["codebundles/my-health-check/.runwhen/templates/my-health-check-slx.yaml"]
        assert "{{slx_name}}" in slx
        assert "{{workspace.owner_email}}" in slx
        assert "common-labels.yaml" in slx

    def test_optional_sli_template(self) -> None:
        files = self._render(include_sli=True)
        sli_path = "codebundles/my-health-check/.runwhen/templates/my-health-check-sli.yaml"
        assert sli_path in files
        sli_text = files[sli_path]
        assert "codebundles/tool-builder/sli.robot" in sli_text
        assert "intervalSeconds: 300" in sli_text
