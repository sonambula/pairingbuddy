import subprocess

import pytest

from tests import conftest, js_syntax


def fake_which(found):
    return lambda name: "/usr/bin/node" if found and name == "node" else None


def fake_run(version):
    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=version + "\n", stderr="")

    return run


@pytest.mark.parametrize("version", ["v18.0.0", "v24.1.0"])
def test_node_requirement_enforced_accepts_supported_node(version):
    sut = js_syntax.check_node_requirement

    result = sut(which=fake_which(True), run=fake_run(version))

    assert result is None, f"node {version} should satisfy the requirement"


@pytest.mark.parametrize(
    "which_found, version, expected_fragment",
    [
        (False, "", "not found on PATH"),
        (True, "v16.20.2", "found v16.20.2"),
    ],
    ids=["node-missing", "node-too-old"],
)
def test_node_requirement_enforced_rejects_missing_or_old_node(
    which_found, version, expected_fragment
):
    sut = js_syntax.check_node_requirement

    result = sut(which=fake_which(which_found), run=fake_run(version))

    assert result is not None, "missing or old node must be reported"
    assert "Node.js" in result, f"message should name Node.js: {result!r}"
    assert ">= 18" in result, f"message should state the minimum: {result!r}"
    assert expected_fragment in result, f"message should contain {expected_fragment!r}: {result!r}"


def test_conftest_exits_session_when_node_requirement_unmet(monkeypatch):
    sut = conftest.pytest_configure
    monkeypatch.setattr(conftest, "check_node_requirement", lambda: "boom")

    with pytest.raises(pytest.exit.Exception) as exc_info:
        sut(None)

    assert exc_info.value.returncode == pytest.ExitCode.USAGE_ERROR, "must exit with usage error"
    assert "boom" in str(exc_info.value), "exit message must carry the problem text"


def test_conftest_continues_session_when_node_requirement_met(monkeypatch):
    sut = conftest.pytest_configure
    monkeypatch.setattr(conftest, "check_node_requirement", lambda: None)

    sut(None)
