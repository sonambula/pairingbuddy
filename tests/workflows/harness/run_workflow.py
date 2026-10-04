"""SPIKE U3 prototype: pytest-side driver for run_workflow.mjs."""

import json
import subprocess
from pathlib import Path

HARNESS = Path(__file__).with_name("run_workflow.mjs")
HARNESS_TIMEOUT_SECONDS = 30


def run_workflow(script_path, args, responses, max_calls=100):
    """Run a workflow script with scripted agent responses and return the captured run.

    Raises RuntimeError if the harness itself crashed (non-JSON output); script errors are
    returned in the "error" field instead.
    """
    payload = {
        "script_path": str(script_path),
        "args": args,
        "responses": responses,
        "max_calls": max_calls,
    }
    completed = subprocess.run(
        ["node", str(HARNESS)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=HARNESS_TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"harness crashed: {completed.stderr.strip()}")
    return json.loads(completed.stdout)


def calls_to(run, agent_name):
    """Agent calls made to pairingbuddy:<agent_name>, in order."""
    return [c for c in run["calls"] if c["agentType"] == f"pairingbuddy:{agent_name}"]
