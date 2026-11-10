"""[INDEPENDENT] Production API, template isolation, public config and coverage."""
import ast
import json
import pytest

from pioneer.paths import REPO_ROOT
from pioneer.explorer.coverage import policy_coverage, save_world_model


def forbidden_test_import(node):
    modules = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
    return any(name.split(".")[0] in {"tests", "helpers"} or name.split(".")[0].startswith("legacy_")
               for name in modules)


def test_public_api_and_no_production_templates():
    import pioneer.explorer as package
    assert package.__all__ == ["ExplorerAgent"]
    assert not hasattr(package, "build_knowledge")
    for path in (REPO_ROOT / "src").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            assert not forbidden_test_import(node), path
            names = []
            if isinstance(node, ast.Name):
                names.append(node.id)
            elif isinstance(node, ast.ImportFrom):
                names.extend(alias.name for alias in node.names)
            assert not any(n == "rule_catalog" or n.startswith("_legacy_") for n in names), path


@pytest.mark.parametrize("code,blocked", [
    ("import tests", True), ("import tests.helpers.legacy_rules as rules", True),
    ("from tests.helpers.legacy_build import build_knowledge", True),
    ("from pioneer.explorer import ExplorerAgent", False),
])
def test_static_check_recognizes_both_import_forms(code, blocked):
    assert forbidden_test_import(ast.parse(code).body[0]) is blocked


def test_policy_coverage_leaves_unmapped_text_visible():
    result = policy_coverage("Title\n\nMust confirm.\n\nRequire balance.",
        [{"rule_id": "confirm", "policy_quote": "Must confirm."}], [], {"P2": "No paired probe yet"})
    assert result["covered"] == 1 and result["total"] == 3
    assert result["paragraphs"][2]["reason"] == "No paired probe yet"
    assert result["paragraphs"][0]["rule_ids"] == []


def test_world_trace_split(tmp_path):
    output = save_world_model(tmp_path / "world.json", dict(actions={}, agent_trace=["tool"], llm_requests=["usage"]))
    assert "agent_trace" not in output and "llm_requests" not in output
    trace = json.loads((tmp_path / "retail_explorer_trace.json").read_text())
    assert trace == {"agent_trace": ["tool"], "llm_requests": ["usage"]}


def test_gateway_is_environment_only():
    import yaml
    config = yaml.safe_load((REPO_ROOT / "configs/retail.yaml").read_text())
    assert "api_base" not in config["llm"]
