"""Tests for workflow logic in the orchestrator skill.

Tests that workflow pseudocode calls resolve to registered agents or existing workflows.
"""

import ast
import re
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).parent.parent.parent
ORCHESTRATOR_SKILL = PROJECT_ROOT / "skills" / "coding" / "SKILL.md"
AGENT_CONFIG = PROJECT_ROOT / "contracts" / "agent-config.yaml"


def load_agent_config():
    """Load agent configuration from YAML."""
    with open(AGENT_CONFIG) as f:
        return yaml.safe_load(f)


def get_registered_agent_names() -> set[str]:
    """Get set of agent names from config."""
    config = load_agent_config()
    return set(config["agents"].keys())


def extract_workflow_code() -> str | None:
    """Extract Python code block from the Workflow section."""
    content = ORCHESTRATOR_SKILL.read_text()

    # Find the Workflow section
    pattern = r"^## Workflow[ \t]*$\s*(.*?)(?=\n## |\Z)"
    match = re.search(pattern, content, re.DOTALL | re.MULTILINE)
    if not match:
        return None

    section_content = match.group(1)

    # Extract python code block
    code_pattern = r"```python\s*(.*?)```"
    code_match = re.search(code_pattern, section_content, re.DOTALL)
    if not code_match:
        return None

    return code_match.group(1).strip()


def python_name_to_agent_name(func_name: str) -> str:
    """Convert Python function name to agent name (underscores to hyphens)."""
    return func_name.replace("_", "-")


def find_unresolved_calls(code: str, registered_agents: set[str], workflows_dir: Path) -> list[str]:
    """Return calls in workflow pseudocode that resolve to neither an agent nor a workflow.

    Workflow('pairingbuddy:<name>', ...) resolves when <workflows_dir>/<name>.js exists;
    any other Workflow call (such as a non-literal first argument) is unresolved.
    """
    tree = ast.parse(code)

    unresolved = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == "Workflow":
            first = node.args[0] if node.args else None
            literal = first.value if isinstance(first, ast.Constant) else None
            prefix = "pairingbuddy:"
            if (
                isinstance(literal, str)
                and literal.startswith(prefix)
                and (workflows_dir / f"{literal[len(prefix) :]}.js").is_file()
            ):
                continue
            unresolved.append(f"Workflow({ast.unparse(first) if first else ''})")
            continue
        func_name = node.func.id if isinstance(node.func, ast.Name) else None
        if func_name is None and isinstance(node.func, ast.Attribute):
            func_name = node.func.attr
        if func_name is None or func_name.startswith("_"):
            continue  # orchestrator-only functions use _ prefix
        agent_name = python_name_to_agent_name(func_name)
        if agent_name not in registered_agents:
            unresolved.append(f"{func_name} -> {agent_name}")
    return unresolved


def test_workflow_references_resolve_to_agents():
    """Function calls in workflow must resolve to registered agents or existing workflows."""
    code = extract_workflow_code()
    assert code is not None, "Could not extract workflow code from orchestrator skill"

    unresolved = find_unresolved_calls(
        code, get_registered_agent_names(), PROJECT_ROOT / "workflows"
    )

    assert not unresolved, (
        f"Workflow calls must resolve to registered agents or existing workflows: {unresolved}"
    )
