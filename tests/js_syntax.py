"""Shared JavaScript syntax checking for tests that validate workflow scripts."""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

NODE_CHECK_TIMEOUT_SECONDS = 60
MINIMUM_NODE_MAJOR = 18
NODE_REQUIREMENT_MESSAGE = (
    f"Node.js >= {MINIMUM_NODE_MAJOR} is required to run the pairingbuddy test suite"
)


def check_node_requirement(which=shutil.which, run=subprocess.run):
    """Return None if Node.js meets the minimum major version, else a problem message."""
    if which("node") is None:
        return f"{NODE_REQUIREMENT_MESSAGE}, but `node` was not found on PATH."
    try:
        result = run(
            ["node", "--version"],
            capture_output=True,
            text=True,
            timeout=NODE_CHECK_TIMEOUT_SECONDS,
            check=False,
        )
        output = (result.stdout or "").strip()
        match = re.fullmatch(r"v(\d+)\.\d+\.\d+.*", output)
        if result.returncode != 0:
            error_output = (result.stderr or "").strip()
            return (
                f"{NODE_REQUIREMENT_MESSAGE}; `node --version` exited with "
                f"code {result.returncode}: {error_output!r}."
            )
        if match is None:
            return f"{NODE_REQUIREMENT_MESSAGE}; `node --version` gave unusable output {output!r}."
        major = int(match.group(1))
    except (OSError, subprocess.SubprocessError) as error:
        return f"{NODE_REQUIREMENT_MESSAGE}, but running `node --version` failed: {error}."
    if major < MINIMUM_NODE_MAJOR:
        return f"{NODE_REQUIREMENT_MESSAGE}, but found {output}."
    return None


def as_checkable_commonjs(text):
    """Wrap a workflow body the way the runtime runs it (async function body).

    `node --check x.js` silently accepts ANY content once it sees an `export`, so the script is
    checked as a CommonJS async function body instead; that also permits top-level await/return.
    """
    body = re.sub(r"^export (?=const meta\b)", "", text, flags=re.MULTILINE)
    return f"(async function () {{\n{body}\n}})\n"


def node_check_problems(path):
    """Return [] if the file passes `node --check` as an async function body, else one problem.

    Node.js >= 18 is a hard requirement, enforced by tests/conftest.py at session start.
    """
    with tempfile.TemporaryDirectory() as scratch:
        checkable = Path(scratch) / "script.cjs"
        checkable.write_text(as_checkable_commonjs(path.read_text(encoding="utf-8")), "utf-8")
        result = subprocess.run(
            ["node", "--check", str(checkable)],
            capture_output=True,
            text=True,
            timeout=NODE_CHECK_TIMEOUT_SECONDS,
            check=False,
        )
    if result.returncode == 0:
        return []
    return [f"node --check failed: {result.stderr.strip()}"]
