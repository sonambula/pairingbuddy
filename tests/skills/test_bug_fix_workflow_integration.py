"""Structural tests for the bug_fix branch integration in the coding skill.

The bug_fix workflow pseudocode calls enumerate and placeholders as workflows,
each followed by a review loop, and stops explicitly at the migration frontier.
Checks are anchored on the AST of the bug_fix branch and on exact contract
strings, so they reject real defects rather than formatting changes. Prose
meaning is deliberately not tested.
"""

import ast
import re
from typing import NamedTuple

from tests.agents.test_workflow_logic import (
    ORCHESTRATOR_SKILL,
    PROJECT_ROOT,
    find_unresolved_calls,
    get_registered_agent_names,
)
from tests.skills import test_classify_workflow_integration as classify_tests
from tests.skills.test_classify_workflow_integration import (
    UNMIGRATED_TASK_TYPES,
    bridge_assign,
    check_task_json_write_guarded,
    enclosing_statement,
    is_call_to,
    parent_map,
    parse_workflow,
    resolve_workflow_args,
    run_checks,
    task_json_writes,
    workflow_calls_named,
)
from tests.skills.test_curate_guidance_integration import loop_reference_line

ENUMERATE_NAME = "pairingbuddy:bug-fix-enumerate"
PLACEHOLDERS_NAME = "pairingbuddy:bug-fix-placeholders"
WORKFLOWS_DIR = PROJECT_ROOT / "workflows"
FRONTIER_MESSAGE = "bug_fix migration frontier: RED-GREEN not yet migrated"
FRONTIER_MARKER = "TEMPORARY — moved in Task 17, removed in Task 21"
BUG_FIX_TEST = ast.dump(ast.parse('task_type == "bug_fix"', mode="eval").body)
TEST_CONFIG_READ = ast.dump(
    ast.parse('_read_json(".pairingbuddy/test-config.json")', mode="eval").body
)
HUMAN_GUIDANCE_READ = ast.dump(
    ast.parse('_read_json(".pairingbuddy/human-guidance.json")', mode="eval").body
)
REVISION_KEYS = {"previous_proposal", "human_feedback"}
OLD_AGENT_CALLS = {
    "enumerate_scenarios_and_test_cases",
    "create_test_placeholders",
    "implement_tests",
    "implement_code",
}
INTERMEDIATE_FILES = ("scenarios.json", "tests.json")
INPUT_CONTRACT = re.compile(r"Requires args \{([^}]*)\}")
NO_BUG_FIX_BRANCH = 'no single `task_type == "bug_fix"` branch in the pseudocode'


class BugFixCall(NamedTuple):
    call: ast.Call
    entries: dict
    parents: dict
    body: list[ast.stmt]


# ---------------------------------------------------------------- helpers ---


def bug_fix_body(tree: ast.Module) -> list[ast.stmt] | None:
    """The statements of the `task_type == "bug_fix"` branch, else None."""
    branches = [
        n for n in ast.walk(tree) if isinstance(n, ast.If) and ast.dump(n.test) == BUG_FIX_TEST
    ]
    return branches[0].body if len(branches) == 1 else None


def parse_bug_fix() -> tuple[str, ast.Module, list[ast.stmt] | None, list[str]]:
    """(code, tree, bug_fix body, problems); problems is non-empty iff body is None."""
    code, tree = parse_workflow()
    body = bug_fix_body(tree)
    return code, tree, body, [] if body is not None else [NO_BUG_FIX_BRANCH]


def branch_nodes(body: list[ast.stmt]) -> list[ast.AST]:
    return [node for stmt in body for node in ast.walk(stmt)]


def branch_calls_to(body: list[ast.stmt], name: str) -> list[ast.Call]:
    return [n for n in branch_nodes(body) if is_call_to(n, name)]


def workflow_calls_in(body: list[ast.stmt]) -> list[ast.Call]:
    return branch_calls_to(body, "Workflow")


def string_constants(node: ast.AST) -> list[str]:
    return [
        n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]


def result_name(call: ast.Call, parents: dict) -> str | None:
    stmt = enclosing_statement(call, parents)
    targets = getattr(stmt, "targets", [])
    if len(targets) == 1 and isinstance(targets[0], ast.Name):
        return targets[0].id
    return None


def bug_fix_workflow_call(name: str) -> tuple[BugFixCall | None, list[str]]:
    """(BugFixCall, problems) for the single call named `name`; (None, problems) if not found."""
    _, tree, body, problems = parse_bug_fix()
    if body is None:
        return None, problems
    calls = [c for c in workflow_calls_named(tree, name) if c in branch_nodes(body)]
    if len(calls) != 1:
        return None, [
            f"expected exactly one Workflow('{name}') call in bug_fix, found {len(calls)}"
        ]
    parents = parent_map(tree)
    entries, problems = resolve_workflow_args(calls[0], parents, name)
    return BugFixCall(calls[0], entries, parents, body), problems


def workflow_input_contract(name: str) -> tuple[set[str], set[str]] | None:
    """(required, optional) args keys parsed from meta.whenToUse, None without a contract."""
    script = (WORKFLOWS_DIR / f"{name.removeprefix('pairingbuddy:')}.js").read_text()
    match = INPUT_CONTRACT.search(script)
    if match is None:
        return None
    keys = [k.strip() for k in match.group(1).split(",") if k.strip()]
    required = {k for k in keys if not k.endswith("?")}
    optional = {k.removesuffix("?") for k in keys if k.endswith("?")}
    return required, optional


def unguarded_only(entries: dict, key: str, expected_dump: str, label: str) -> list[str]:
    if key not in entries:
        return [f"{label} args lack {key!r}"]
    value, guarded = entries[key]
    problems = []
    if ast.dump(value) != expected_dump:
        problems.append(f"{label} {key!r} is not {expected_dump!r}")
    if guarded:
        problems.append(f"{label} {key!r} is only passed under the human-guidance condition")
    return problems


def guarded_human_guidance(entries: dict, label: str) -> list[str]:
    if "human_guidance" not in entries:
        return [f"{label} args never pass human_guidance"]
    value, guarded = entries["human_guidance"]
    problems = []
    if not guarded:
        problems.append(f"{label} passes human_guidance unconditionally")
    if ast.dump(value) != HUMAN_GUIDANCE_READ:
        problems.append(f"{label} human_guidance is not _read_json(human-guidance.json)")
    return problems


# ------------------------------------------------- scenario: workflow calls ---


def check_enumerate_then_placeholders() -> list[str]:
    _, tree, body, problems = parse_bug_fix()
    if body is None:
        return problems
    nodes = branch_nodes(body)
    enumerate_calls = [c for c in workflow_calls_named(tree, ENUMERATE_NAME) if c in nodes]
    placeholder_calls = [c for c in workflow_calls_named(tree, PLACEHOLDERS_NAME) if c in nodes]
    problems = []
    if len(enumerate_calls) != 1:
        problems.append(f"expected one Workflow('{ENUMERATE_NAME}'), found {len(enumerate_calls)}")
    if len(placeholder_calls) != 1:
        problems.append(
            f"expected one Workflow('{PLACEHOLDERS_NAME}'), found {len(placeholder_calls)}"
        )
    other = [
        c
        for c in nodes
        if is_call_to(c, "Workflow") and c not in enumerate_calls and c not in placeholder_calls
    ]
    if other:
        problems.append(f"{len(other)} other Workflow call(s) in the bug_fix branch")
    if not problems and enumerate_calls[0].lineno >= placeholder_calls[0].lineno:
        problems.append("the enumerate call does not come before the placeholders call")
    return problems


def check_review_loop_after_each_call() -> list[str]:
    code, tree, body, problems = parse_bug_fix()
    if body is None:
        return problems
    parents = parent_map(tree)
    nodes = branch_nodes(body)
    calls = [
        c
        for name in (ENUMERATE_NAME, PLACEHOLDERS_NAME)
        for c in workflow_calls_named(tree, name)
        if c in nodes
    ]
    if len(calls) != 2:
        return [f"expected two bug_fix workflow calls, found {len(calls)}"]
    return [
        f"the call on line {c.lineno} is not followed by a '# Review loop' comment"
        for c in calls
        if loop_reference_line(code, c, parents) is None
    ]


def check_workflow_names_registered() -> list[str]:
    _, tree, body, problems = parse_bug_fix()
    if body is None:
        return problems
    problems = []
    for name in (ENUMERATE_NAME, PLACEHOLDERS_NAME):
        script = WORKFLOWS_DIR / f"{name.removeprefix('pairingbuddy:')}.js"
        if not script.is_file():
            problems.append(f"{script.relative_to(PROJECT_ROOT)} does not exist")
    branch_code = "\n".join(ast.unparse(stmt) for stmt in body)
    problems.extend(
        f"unresolved call {call} in the bug_fix branch"
        for call in find_unresolved_calls(branch_code, get_registered_agent_names(), WORKFLOWS_DIR)
    )
    if not workflow_calls_in(body):
        problems.append("the bug_fix branch has no Workflow calls to resolve")
    return problems


CALL_CHECKS = {
    "enumerate-then-placeholders-in-order": check_enumerate_then_placeholders,
    "review-loop-comment-after-each-call": check_review_loop_after_each_call,
    "workflow-names-registered": check_workflow_names_registered,
}


def test_bug_fix_branch_calls_enumerate_then_placeholders_in_order():
    """bug-fix-branch-calls-enumerate-then-placeholders-in-order."""
    run_checks("enumerate-then-placeholders-in-order", CALL_CHECKS)


def test_each_bug_fix_call_followed_by_review_loop_comment():
    """each-bug-fix-call-followed-by-review-loop-comment."""
    run_checks("review-loop-comment-after-each-call", CALL_CHECKS)


def test_bug_fix_workflow_names_are_registered_workflows():
    """bug-fix-workflow-names-are-registered-workflows."""
    run_checks("workflow-names-registered", CALL_CHECKS)


# ------------------------------------------------------- scenario: args ---


def check_enumerate_args() -> list[str]:
    found, problems = bug_fix_workflow_call(ENUMERATE_NAME)
    if found is None:
        return problems
    entries = found.entries
    problems = list(problems)
    root = entries.get("project_root", (None, False))[0]
    if not (is_call_to(root, "_absolute_project_root") and not root.args and not root.keywords):
        problems.append("enumerate project_root is not _absolute_project_root()")
    task = entries.get("task", (None, False))
    if not (isinstance(task[0], ast.Name) and task[0].id == "task" and not task[1]):
        problems.append("enumerate task is not the unconditional variable `task`")
    problems += unguarded_only(entries, "test_config", TEST_CONFIG_READ, "enumerate")
    problems += guarded_human_guidance(entries, "enumerate")
    revision = REVISION_KEYS & set(entries)
    if revision:
        problems.append(f"enumerate initial call passes revision keys {sorted(revision)}")
    return problems


def check_placeholders_args() -> list[str]:
    enumerate_found, enumerate_problems = bug_fix_workflow_call(ENUMERATE_NAME)
    found, problems = bug_fix_workflow_call(PLACEHOLDERS_NAME)
    if found is None or enumerate_found is None:
        return (enumerate_problems if enumerate_found is None else []) + problems
    entries = found.entries
    enumerate_call, enumerate_parents = enumerate_found.call, enumerate_found.parents
    problems = list(problems)
    root = entries.get("project_root", (None, False))[0]
    if not (is_call_to(root, "_absolute_project_root") and not root.args and not root.keywords):
        problems.append("placeholders project_root is not _absolute_project_root()")
    enumeration = result_name(enumerate_call, enumerate_parents)
    scenarios = entries.get("scenarios", (None, False))
    value = scenarios[0]
    if not (
        isinstance(value, ast.Attribute)
        and value.attr == "proposal"
        and isinstance(value.value, ast.Name)
        and enumeration is not None
        and value.value.id == enumeration
    ):
        problems.append(f"placeholders scenarios is not `{enumeration}.proposal`")
    elif value.lineno <= enumerate_call.end_lineno:
        problems.append("scenarios is read before the enumerate call returns")
    if scenarios[1]:
        problems.append("placeholders scenarios is only passed under the human-guidance condition")
    problems += unguarded_only(entries, "test_config", TEST_CONFIG_READ, "placeholders")
    problems += guarded_human_guidance(entries, "placeholders")
    revision = REVISION_KEYS & set(entries)
    if revision:
        problems.append(f"placeholders initial call passes revision keys {sorted(revision)}")
    return problems


def check_args_within_input_contract() -> list[str]:
    problems = []
    for name in (ENUMERATE_NAME, PLACEHOLDERS_NAME):
        found, resolve_problems = bug_fix_workflow_call(name)
        if found is None:
            problems += resolve_problems
            continue
        problems += resolve_problems
        contract = workflow_input_contract(name)
        if contract is None:
            problems.append(f"{name}: no 'Requires args {{...}}' contract in whenToUse")
            continue
        required, optional = contract
        keys = set(found.entries)
        if missing := required - keys:
            problems.append(f"{name} lacks required args keys {sorted(missing)}")
        if unknown := keys - required - optional:
            problems.append(f"{name} passes unknown args keys {sorted(unknown)}")
    return problems


ARGS_CHECKS = {
    "enumerate-args": check_enumerate_args,
    "placeholders-args": check_placeholders_args,
    "args-within-contract": check_args_within_input_contract,
}


def test_enumerate_args_keys_and_guards():
    """enumerate-args-keys-and-guards."""
    run_checks("enumerate-args", ARGS_CHECKS)


def test_placeholders_args_take_approved_scenarios_proposal():
    """placeholders-args-take-approved-scenarios-proposal."""
    run_checks("placeholders-args", ARGS_CHECKS)


def test_args_keys_within_workflow_input_contract():
    """args-keys-within-workflow-input-contract."""
    run_checks("args-within-contract", ARGS_CHECKS)


# ------------------------------------------------ scenario: frontier stop ---


def frontier_stops(body: list[ast.stmt]) -> list[ast.Call]:
    return branch_calls_to(body, "_stop")


def check_frontier_stop_message() -> list[str]:
    _, tree, body, problems = parse_bug_fix()
    if body is None:
        return problems
    stops = frontier_stops(body)
    messages = [
        s.args[0].value
        if len(s.args) == 1
        and not s.keywords
        and isinstance(s.args[0], ast.Constant)
        and isinstance(s.args[0].value, str)
        else None
        for s in stops
    ]
    if messages != [FRONTIER_MESSAGE]:
        return [f"expected exactly one _stop({FRONTIER_MESSAGE!r}) in bug_fix, found {messages}"]
    return []


def check_frontier_marker() -> list[str]:
    raw_count = ORCHESTRATOR_SKILL.read_text().count(FRONTIER_MARKER)
    if raw_count != 1:
        return [f"marker {FRONTIER_MARKER!r} appears {raw_count} times, expected once"]
    code, tree, body, problems = parse_bug_fix()
    if body is None:
        return problems
    stops = [s for s in frontier_stops(body) if string_constants(s) == [FRONTIER_MESSAGE]]
    if len(stops) != 1:
        return ["no frontier stop in the bug_fix branch to attach the marker to"]
    lines = code.splitlines()
    stop_line = enclosing_statement(stops[0], parent_map(tree)).lineno
    own_line = lines[stop_line - 1]
    if own_line.rstrip().endswith(f"# {FRONTIER_MARKER}"):
        return []
    index = stop_line - 2
    while index >= 0 and lines[index].strip().startswith("#"):
        if lines[index].strip() == f"# {FRONTIER_MARKER}":
            return []
        index -= 1
    return ["the marker is not a comment directly attached to the frontier stop"]


def check_nothing_after_frontier_stop() -> list[str]:
    _, tree, body, problems = parse_bug_fix()
    if body is None:
        return problems
    last = body[-1]
    if not (
        isinstance(last, ast.Expr)
        and is_call_to(last.value, "_stop")
        and string_constants(last.value) == [FRONTIER_MESSAGE]
    ):
        return [f"the last statement of the bug_fix branch is not _stop({FRONTIER_MESSAGE!r})"]
    return []


FRONTIER_CHECKS = {
    "stop-message-exact": check_frontier_stop_message,
    "marker-exact": check_frontier_marker,
    "nothing-after-stop": check_nothing_after_frontier_stop,
}


def test_frontier_stop_message_exact_string():
    """frontier-stop-message-exact-string."""
    run_checks("stop-message-exact", FRONTIER_CHECKS)


def test_frontier_marker_exact_string():
    """frontier-marker-exact-string."""
    run_checks("marker-exact", FRONTIER_CHECKS)


def test_nothing_runs_after_frontier_stop():
    """nothing-runs-after-frontier-stop."""
    run_checks("nothing-after-stop", FRONTIER_CHECKS)


# ------------------------------- scenario: no state files or old agents ---


def check_no_intermediate_json_writes() -> list[str]:
    _, _, body, problems = parse_bug_fix()
    if body is None:
        return problems
    if not workflow_calls_in(body):
        return ["the bug_fix branch has no workflow calls, so it is not migrated"]
    return [
        f"_write_json on line {call.lineno} writes {path!r} in the bug_fix branch"
        for call in branch_calls_to(body, "_write_json")
        for path in string_constants(call)
        if any(name in path for name in INTERMEDIATE_FILES)
    ]


def check_no_old_agent_calls() -> list[str]:
    _, tree, body, problems = parse_bug_fix()
    if body is None:
        return problems
    problems = [
        f"the bug_fix branch still calls {name}() on line {call.lineno}"
        for name in sorted(OLD_AGENT_CALLS)
        for call in branch_calls_to(body, name)
    ]
    inside = set(branch_nodes(body))
    outside = [n for n in ast.walk(tree) if n not in inside]
    for name in ("enumerate_scenarios_and_test_cases", "create_test_placeholders"):
        if not any(is_call_to(n, name) for n in outside):
            problems.append(f"{name}() is not called anywhere outside the bug_fix branch")
    return problems


NO_OLD_CHECKS = {
    "no-intermediate-json-writes": check_no_intermediate_json_writes,
    "no-old-agent-calls": check_no_old_agent_calls,
}


def test_no_scenarios_or_tests_json_write_in_bug_fix_branch():
    """no-scenarios-or-tests-json-write-in-bug-fix-branch."""
    run_checks("no-intermediate-json-writes", NO_OLD_CHECKS)


def test_no_old_enumerate_or_placeholder_agent_calls_in_bug_fix_branch():
    """no-old-enumerate-or-placeholder-agent-calls-in-bug-fix-branch."""
    run_checks("no-old-agent-calls", NO_OLD_CHECKS)


# --------------------------------------------- scenario: task.json bridge ---


def check_task_json_writes_still_bridge_guarded() -> list[str]:
    code, tree, body, problems = parse_bug_fix()
    if body is None:
        return problems
    problems = check_task_json_write_guarded()
    writes = task_json_writes(tree)
    if len(writes) != 2:
        problems.append(
            f"expected two guarded task.json writes (plan and normal), found {len(writes)}"
        )
    node = bridge_assign(code)
    try:
        flows = ast.literal_eval(node.value) if node is not None else None
    except ValueError:
        flows = None
    if not isinstance(flows, (list, tuple, set, frozenset)) or "bug_fix" in flows:
        problems.append("bug_fix is still in UNMIGRATED_FLOWS, so it would write task.json")
    nodes = branch_nodes(body)
    problems.extend(
        f"task.json write on line {w.lineno} inside the bug_fix branch"
        for w in writes
        if w in nodes
    )
    return problems


def check_classify_check_uses_four_type_set(monkeypatch) -> list[str]:
    cases = {
        '["new_feature", "refactoring", "config_change", "spike"]': True,
        '["spike", "new_feature", "config_change", "refactoring"]': True,
        '["new_feature", "bug_fix", "refactoring", "config_change", "spike"]': False,
        '["new_feature", "refactoring", "config_change"]': False,
        '["new_feature", "refactoring", "config_change", "spike", "bug_fix"]': False,
    }
    problems = []
    for constant, should_accept in cases.items():
        code = f"# {classify_tests.BRIDGE_MARKER}\nUNMIGRATED_FLOWS = {constant}\n"
        monkeypatch.setattr(
            classify_tests, "parse_workflow", lambda code=code: (code, ast.parse(code))
        )
        rejected = bool(classify_tests.check_unmigrated_constant())
        if rejected == should_accept:
            verdict = "rejects" if rejected else "accepts"
            problems.append(f"check_unmigrated_constant {verdict} {constant}")
    if {"new_feature", "refactoring", "config_change", "spike"} != UNMIGRATED_TASK_TYPES:
        problems.append(f"UNMIGRATED_TASK_TYPES is {sorted(UNMIGRATED_TASK_TYPES)}")
    return problems


BRIDGE_CHECKS = {
    "task-json-writes-still-bridge-guarded": check_task_json_writes_still_bridge_guarded,
    "classify-check-uses-four-type-set": check_classify_check_uses_four_type_set,
}


def test_task_json_write_still_guarded_by_bridge():
    """task-json-write-still-guarded-by-bridge."""
    run_checks("task-json-writes-still-bridge-guarded", BRIDGE_CHECKS)


def test_classify_integration_test_adjusted(monkeypatch):
    """classify-integration-test-adjusted."""
    run_checks("classify-check-uses-four-type-set", BRIDGE_CHECKS, monkeypatch)
