import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.js_syntax import check_node_requirement


def pytest_configure(config):
    """Stop the session early if Node.js >= 18 is missing (hard requirement, no skips)."""
    problem = check_node_requirement()
    if problem:
        pytest.exit(problem, returncode=pytest.ExitCode.USAGE_ERROR)


HOOK_PATH = Path(__file__).parent.parent / "hooks" / "solo-progress.mjs"


def run_hook(
    env_vars: dict,
    stdin_payload: dict | None = None,
    cwd: str | None = None,
) -> subprocess.CompletedProcess:
    """Run the solo-progress.mjs hook as a subprocess with the given environment."""
    if stdin_payload is None:
        stdin_payload = {"tool_name": "Write", "tool_input": {"path": "some/file.py"}}

    env = os.environ.copy()
    env.pop("PAIRINGBUDDY_SOLO", None)
    env.update(env_vars)

    return subprocess.run(
        ["node", str(HOOK_PATH)],
        input=json.dumps(stdin_payload),
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
    )
