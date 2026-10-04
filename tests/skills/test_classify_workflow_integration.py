"""Structural tests for the classify workflow integration in the coding skill.

The coding skill's Workflow pseudocode must classify tasks through the
`pairingbuddy:classify` workflow instead of the classify_task agent. Checks are
anchored on the AST of the pseudocode block and on exact contract strings
(compared after whitespace normalization), so they reject real defects rather
than formatting changes. Prose meaning is deliberately not tested.
"""

import ast
import re
from pathlib import Path

import frontmatter
import pytest

from tests.agents.test_workflow_logic import (
    ORCHESTRATOR_SKILL,
    PROJECT_ROOT,
    extract_workflow_code,
    find_unresolved_calls,
    get_registered_agent_names,
)

COMMAND_CODE = PROJECT_ROOT / "commands" / "code.md"
CLASSIFY_WORKFLOW_NAME = "pairingbuddy:classify"
TASK_TYPES = {"new_feature", "bug_fix", "refactoring", "config_change", "spike"}
# bug_fix left the bridge when its flow migrated (plan Task 11)
UNMIGRATED_TASK_TYPES = TASK_TYPES - {"bug_fix"}
BRIDGE_MARKER = "TEMPORARY BRIDGE — removed in TB3.3"
AUTHORIZATION_SENTENCE = (
    "Invoking /pairingbuddy:code authorizes the Workflow tool "
    "for the workflows named in the coding skill."
)
HUMAN_GUIDANCE_GUARD = ast.dump(
    ast.parse('_file_exists(".pairingbuddy/human-guidance.json")', mode="eval").body
)
REQUIRED_ARGS = {"project_root", "task", "human_guidance"}


# ---------------------------------------------------------------- helpers ---


def parse_workflow() -> tuple[str, ast.Module]:
    code = extract_workflow_code()
    assert code is not None, "Could not extract workflow code from orchestrator skill"
    return code, ast.parse(code)


def parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}


def is_call_to(node: ast.AST, name: str) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name


def workflow_calls(tree: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(tree) if is_call_to(n, "Workflow")]


def first_arg_literal(call: ast.Call) -> str | None:
    if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
        return call.args[0].value
    return None


def workflow_calls_named(tree: ast.AST, name: str) -> list[ast.Call]:
    return [c for c in workflow_calls(tree) if first_arg_literal(c) == name]


def classify_calls(tree: ast.AST) -> list[ast.Call]:
    return workflow_calls_named(tree, CLASSIFY_WORKFLOW_NAME)


def enclosing_statement(node: ast.AST, parents: dict) -> ast.stmt:
    while not isinstance(node, ast.stmt):
        node = parents[node]
    return node


def sibling_list(stmt: ast.stmt, parents: dict) -> list[ast.stmt]:
    parent = parents[stmt]
    for field in ("body", "orelse", "finalbody"):
        stmts = getattr(parent, field, None)
        if isinstance(stmts, list) and stmt in stmts:
            return stmts
    raise AssertionError("statement has no enclosing statement list")


def has_ancestor(node: ast.AST, parents: dict, kind: type) -> bool:
    while node in parents:
        node = parents[node]
        if isinstance(node, kind):
            return True
    return False


def normalize(text: str) -> str:
    return " ".join(text.split())


def skill_text() -> str:
    return normalize(ORCHESTRATOR_SKILL.read_text())


def args_expr(call: ast.Call) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == "args":
            return kw.value
    return call.args[1] if len(call.args) > 1 else None


def subscript_assigned_key(node: ast.AST, name: str) -> str | None:
    """The key of a `name["key"] = ...` assignment, else None."""
    if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
        return None
    target = node.targets[0]
    if (
        isinstance(target, ast.Subscript)
        and isinstance(target.value, ast.Name)
        and target.value.id == name
        and isinstance(target.slice, ast.Constant)
        and isinstance(target.slice.value, str)
    ):
        return target.slice.value
    return None


def dict_entries(node: ast.Dict) -> dict[str, tuple[ast.expr, bool]]:
    return {
        key.value: (value, False)
        for key, value in zip(node.keys, node.values, strict=True)
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }


def resolve_workflow_args(
    call: ast.Call, parents: dict, label: str
) -> tuple[dict[str, tuple[ast.expr, bool]], list[str]]:
    """Resolve the args of one Workflow call from the statements preceding it.

    Only the call's own sibling statement list is read. Returns (entries, problems);
    entries maps each key to (value, guarded) where guarded means the key is only
    added under `if _file_exists(".pairingbuddy/human-guidance.json")`.
    """
    expr = args_expr(call)
    if isinstance(expr, ast.Dict):
        return dict_entries(expr), []
    if not isinstance(expr, ast.Name):
        return {}, [f"{label} call on line {call.lineno} has no resolvable args"]
    stmt = enclosing_statement(call, parents)
    siblings = sibling_list(stmt, parents)
    entries: dict[str, tuple[ast.expr, bool]] = {}
    problems = []
    for prior in siblings[: siblings.index(stmt)]:
        if isinstance(prior, ast.Assign):
            if (
                len(prior.targets) == 1
                and isinstance(prior.targets[0], ast.Name)
                and prior.targets[0].id == expr.id
                and isinstance(prior.value, ast.Dict)
            ):
                entries = dict_entries(prior.value)
            elif (key := subscript_assigned_key(prior, expr.id)) is not None:
                entries[key] = (prior.value, False)
            continue
        if not (isinstance(prior, ast.If) and ast.dump(prior.test) == HUMAN_GUIDANCE_GUARD):
            continue  # other compound statements are not part of this call's args
        for node in prior.body:
            key = subscript_assigned_key(node, expr.id)
            if key == "human_guidance":
                entries[key] = (node.value, True)
            elif key is not None:
                problems.append(
                    f"{label} call on line {call.lineno}: args key {key!r} is added "
                    "only under the human-guidance condition"
                )
    return entries, problems


def classify_args_by_call() -> list[tuple[ast.Call, dict, list[str]]]:
    _, tree = parse_workflow()
    parents = parent_map(tree)
    return [(c, *resolve_workflow_args(c, parents, "classify")) for c in classify_calls(tree)]


def run_checks(check, checks: dict, *fixtures) -> None:
    problems = checks[check](*fixtures)
    assert not problems, f"{check}: " + "; ".join(problems)


# ------------------------------------------------- test 1: classify calls ---


def check_two_classify_calls() -> list[str]:
    _, tree = parse_workflow()
    parents = parent_map(tree)
    calls = classify_calls(tree)
    if len(calls) != 2:
        return [
            f"expected exactly two Workflow('{CLASSIFY_WORKFLOW_NAME}') calls, found {len(calls)}"
        ]
    problems = []
    in_loop = [c for c in calls if has_ancestor(c, parents, ast.For)]
    at_top = [c for c in calls if isinstance(parents[enclosing_statement(c, parents)], ast.Module)]
    if len(in_loop) != 1:
        problems.append(f"expected one classify call inside the plan loop, found {len(in_loop)}")
    elif not has_ancestor(in_loop[0], parents, ast.If):
        problems.append("plan-loop classify call is not inside the plan-execution branch")
    if len(at_top) != 1:
        problems.append(f"expected one module-level classify call, found {len(at_top)}")
    return problems


def check_no_classify_task() -> list[str]:
    code, _ = parse_workflow()
    if "classify_task" in code or "classify-task" in code:
        return ["classify_task still appears in the Workflow section pseudocode"]
    return []


def routed_task_types(tree: ast.Module) -> set:
    routed = set()
    node = next(
        (
            s
            for s in tree.body
            if isinstance(s, ast.If)
            and isinstance(s.test, ast.Compare)
            and isinstance(s.test.ops[0], ast.Eq)
            and "task_type" in ast.unparse(s.test)
        ),
        None,
    )
    while isinstance(node, ast.If):
        test = node.test
        if (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == "task_type"
            and isinstance(test.ops[0], ast.Eq)
            and isinstance(test.comparators[0], ast.Constant)
        ):
            routed.add(test.comparators[0].value)
        node = node.orelse[0] if len(node.orelse) == 1 else None
    return routed


def reads_task_type_from(stmt: ast.stmt, result_name: str) -> bool:
    """True for `task_type = <result_name>.attr` or `task_type = <result_name>[...]`."""
    return (
        isinstance(stmt, ast.Assign)
        and len(stmt.targets) == 1
        and isinstance(stmt.targets[0], ast.Name)
        and stmt.targets[0].id == "task_type"
        and isinstance(stmt.value, (ast.Attribute, ast.Subscript))
        and isinstance(stmt.value.value, ast.Name)
        and stmt.value.value.id == result_name
    )


def check_task_type_from_result() -> list[str]:
    _, tree = parse_workflow()
    parents = parent_map(tree)
    calls = classify_calls(tree)
    if not calls:
        return ["no classify Workflow calls"]
    problems = []
    for call in calls:
        stmt = enclosing_statement(call, parents)
        siblings = sibling_list(stmt, parents)
        following = siblings[siblings.index(stmt) + 1 :][:1]
        targets = getattr(stmt, "targets", [])
        result = targets[0].id if len(targets) == 1 and isinstance(targets[0], ast.Name) else None
        if result is None or not following or not reads_task_type_from(following[0], result):
            problems.append(
                f"task_type is not read from the classify result right after the call "
                f"on line {call.lineno}"
            )
    if routed_task_types(tree) != TASK_TYPES:
        problems.append(
            f"task_type routing changed: {sorted(routed_task_types(tree))} != {sorted(TASK_TYPES)}"
        )
    return problems


def check_classify_after_cleanup() -> list[str]:
    _, tree = parse_workflow()
    parents = parent_map(tree)
    calls = classify_calls(tree)
    if not calls:
        return ["no classify Workflow calls"]
    problems = []
    for call in calls:
        stmt = enclosing_statement(call, parents)
        stmts = sibling_list(stmt, parents)
        before = stmts[: stmts.index(stmt)]
        if not any(
            isinstance(s, ast.Expr) and is_call_to(s.value, "_cleanup_state_files") for s in before
        ):
            problems.append(
                f"classify call on line {call.lineno} is not preceded by _cleanup_state_files()"
            )
    return problems


CLASSIFY_CALL_CHECKS = {
    "two-classify-workflow-calls-in-expected-positions": check_two_classify_calls,
    "no-classify-task-call-remains": check_no_classify_task,
    "task-type-read-from-workflow-result": check_task_type_from_result,
    "classify-runs-after-cleanup": check_classify_after_cleanup,
}


@pytest.mark.parametrize("check", list(CLASSIFY_CALL_CHECKS))
def test_classify_via_workflow_in_pseudocode(check):
    """classify-via-workflow-in-pseudocode."""
    run_checks(check, CLASSIFY_CALL_CHECKS)


# ------------------------------------------------------ test 2: arguments ---


def check_args_have_required_keys() -> list[str]:
    results = classify_args_by_call()
    if not results:
        return ["no classify Workflow calls"]
    problems = []
    for call, entries, resolve_problems in results:
        problems.extend(resolve_problems)
        missing = REQUIRED_ARGS - set(entries)
        if missing:
            problems.append(
                f"classify call on line {call.lineno} lacks args keys {sorted(missing)}"
            )
    return problems


def check_project_root_absolute() -> list[str]:
    results = classify_args_by_call()
    if not results:
        return ["no classify Workflow calls"]
    problems = []
    for call, entries, _ in results:
        value = entries.get("project_root", (None, False))[0]
        if not is_call_to(value, "_absolute_project_root"):
            problems.append(
                f"classify call on line {call.lineno}: project_root is not _absolute_project_root()"
            )
    return problems


def check_human_guidance_only_when_present() -> list[str]:
    results = classify_args_by_call()
    if not results:
        return ["no classify Workflow calls"]
    problems = []
    for call, entries, resolve_problems in results:
        problems.extend(resolve_problems)
        if "human_guidance" not in entries:
            problems.append(f"classify call on line {call.lineno} never passes human_guidance")
        elif not entries["human_guidance"][1]:
            problems.append(
                f"classify call on line {call.lineno} passes human_guidance unconditionally"
            )
    return problems


ARGS_CHECKS = {
    "args-dict-has-required-keys": check_args_have_required_keys,
    "project-root-is-absolute": check_project_root_absolute,
    "human-guidance-passed-only-when-present": check_human_guidance_only_when_present,
}


@pytest.mark.parametrize("check", list(ARGS_CHECKS))
def test_classify_workflow_args(check):
    """classify-workflow-args."""
    run_checks(check, ARGS_CHECKS)


# ---------------------------------- test 3: existing pseudocode tests ---


def check_workflow_exempt_only_for_existing_script(tmp_path: Path) -> list[str]:
    agents = get_registered_agent_names()
    with_script = tmp_path / "with_script"
    with_script.mkdir()
    (with_script / "classify.js").write_text("")
    without_script = tmp_path / "without_script"
    without_script.mkdir()
    call = "Workflow('pairingbuddy:classify', args={})"
    problems = []
    if find_unresolved_calls(call, agents, with_script):
        problems.append("Workflow('pairingbuddy:classify') not exempt although classify.js exists")
    if not find_unresolved_calls(call, agents, without_script):
        problems.append("Workflow('pairingbuddy:classify') exempt although classify.js is missing")
    if not find_unresolved_calls("Workflow(workflow_name, args={})", agents, with_script):
        problems.append("Workflow call with a non-literal first argument is exempt")
    return problems


def check_unknown_workflow_rejected(tmp_path: Path) -> list[str]:
    agents = get_registered_agent_names()
    (tmp_path / "classify.js").write_text("")
    rejected = {
        "Workflow('pairingbuddy:nope', args={})": "Workflow('pairingbuddy:nope')",
        "Workflow('other:classify', args={})": "Workflow('other:classify') despite classify.js",
        "Workflow('pairingbuddx:classify', args={})": (
            "Workflow('pairingbuddx:classify') (same-length wrong prefix)"
        ),
        "not_an_agent(task)": "a non-agent function call",
        "obj.not_an_agent()": "a non-agent attribute call",
    }
    problems = [
        f"{label} is not reported as unresolved"
        for code, label in rejected.items()
        if not find_unresolved_calls(code, agents, tmp_path)
    ]
    if find_unresolved_calls(
        "enumerate_scenarios_and_test_cases(task, test_config)\n_helper()", agents, tmp_path
    ):
        problems.append("registered agent calls or _-prefixed helpers are wrongly reported")
    return problems


def check_pseudocode_has_classify_workflow_calls() -> list[str]:
    _, tree = parse_workflow()
    if not classify_calls(tree):
        return ["pseudocode has no Workflow calls in place"]
    return []


KEEP_INTENT_CHECKS = {
    "pseudocode-has-classify-workflow-calls": check_pseudocode_has_classify_workflow_calls,
}

WORKFLOW_RESOLUTION_CHECKS = {
    "workflow-call-exempt-only-for-existing-workflow-script": (
        check_workflow_exempt_only_for_existing_script
    ),
    "unknown-workflow-name-still-rejected": check_unknown_workflow_rejected,
}


@pytest.mark.parametrize("check", list(KEEP_INTENT_CHECKS))
def test_existing_pseudocode_tests_keep_intent(check):
    """existing-pseudocode-tests-keep-intent."""
    run_checks(check, KEEP_INTENT_CHECKS)


@pytest.mark.parametrize("check", list(WORKFLOW_RESOLUTION_CHECKS))
def test_workflow_resolution_keeps_intent(check, tmp_path):
    """existing-pseudocode-tests-keep-intent: workflow name resolution."""
    run_checks(check, WORKFLOW_RESOLUTION_CHECKS, tmp_path)


# ------------------------------- test 4: result held in context only ---


def check_state_table_row_removed() -> list[str]:
    text = ORCHESTRATOR_SKILL.read_text()
    if re.search(r"^\|\s*task_classification\s*\|", text, re.MULTILINE):
        return ["task_classification row still in the state files table"]
    return []


def check_no_pseudocode_write() -> list[str]:
    code, tree = parse_workflow()
    problems = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and "task-classification" in node.value
        ):
            problems.append(f"pseudocode references {node.value!r}")
        if isinstance(node, ast.Name) and node.id == "task_classification":
            problems.append("pseudocode still uses the task_classification variable")
    if "task-classification" in code and not problems:
        problems.append("pseudocode mentions task-classification outside string literals")
    if not classify_calls(tree):
        problems.append("no classify Workflow calls (result must be held in context)")
    return problems


CONTEXT_CHECKS = {
    "task-classification-row-removed-from-state-table": check_state_table_row_removed,
    "no-pseudocode-write-of-task-classification": check_no_pseudocode_write,
}


@pytest.mark.parametrize("check", list(CONTEXT_CHECKS))
def test_result_held_in_context_no_state_file(check):
    """result-held-in-context-no-state-file."""
    run_checks(check, CONTEXT_CHECKS)


# ------------------------------------------- test 5: task.json bridge ---


def bridge_assign(code: str) -> ast.Assign | None:
    """The module-level assignment on the first code line after the bridge marker."""
    lines = code.splitlines()
    marker_indexes = [i for i, line in enumerate(lines) if BRIDGE_MARKER in line]
    if len(marker_indexes) != 1:
        return None
    for index in range(marker_indexes[0] + 1, len(lines)):
        stripped = lines[index].strip()
        if not stripped or stripped.startswith("#"):
            continue
        return next(
            (
                node
                for node in ast.parse(code).body
                if isinstance(node, ast.Assign)
                and node.lineno == index + 1
                and any(
                    isinstance(t, ast.Name) and t.id == "UNMIGRATED_FLOWS" for t in node.targets
                )
            ),
            None,
        )
    return None


def task_json_writes(tree: ast.AST) -> list[ast.Call]:
    return [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and any(
            isinstance(a, ast.Constant) and isinstance(a.value, str) and "task.json" in a.value
            for a in n.args
        )
    ]


def is_bridge_guard(test: ast.expr) -> bool:
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == "task_type"
        and isinstance(test.ops[0], ast.In)
        and isinstance(test.comparators[0], ast.Name)
        and test.comparators[0].id == "UNMIGRATED_FLOWS"
    )


def is_guarded(node: ast.AST, parents: dict) -> bool:
    while node in parents:
        node = parents[node]
        if isinstance(node, ast.If) and is_bridge_guard(node.test):
            return True
    return False


def check_marker_once() -> list[str]:
    count = skill_text().count(BRIDGE_MARKER)
    return [] if count == 1 else [f"marker {BRIDGE_MARKER!r} appears {count} times, expected once"]


def check_unmigrated_constant() -> list[str]:
    code, _ = parse_workflow()
    node = bridge_assign(code)
    if node is None:
        return ["no UNMIGRATED_FLOWS assignment directly under the bridge marker in the pseudocode"]
    problems = []
    try:
        value = ast.literal_eval(node.value)
    except ValueError:
        value = None
    if not isinstance(value, (list, tuple, set, frozenset)) or set(value) != UNMIGRATED_TASK_TYPES:
        problems.append(
            f"UNMIGRATED_FLOWS must contain exactly the unmigrated task types "
            f"{sorted(UNMIGRATED_TASK_TYPES)}"
        )
    if "_is_migrated" in code:
        problems.append("a _is_migrated helper was introduced")
    return problems


def check_task_json_write_guarded() -> list[str]:
    _, tree = parse_workflow()
    parents = parent_map(tree)
    writes = task_json_writes(tree)
    if not writes:
        return ["no task.json write in the pseudocode after classification"]
    return [
        f"task.json write on line {w.lineno} is not guarded by task_type in UNMIGRATED_FLOWS"
        for w in writes
        if not is_guarded(w, parents)
    ]


def check_plan_path_write_removed() -> list[str]:
    lines = ORCHESTRATOR_SKILL.read_text().splitlines()
    start = next((i for i, line in enumerate(lines) if "**Iteration:**" in line), None)
    if start is None:
        return ["'**Iteration:**' list item not found"]
    step = next((line for line in lines[start + 1 :] if re.match(r"\s*a\.\s", line)), None)
    if step is None:
        return ["plan-mode step 4a not found under '**Iteration:**'"]
    if "task_type in UNMIGRATED_FLOWS" not in step:
        return ["plan-mode step 4a does not condition the task.json write on the bridge guard"]
    return []


BRIDGE_CHECKS = {
    "bridge-marker-present-once": check_marker_once,
    "unmigrated-flows-constant-under-marker": check_unmigrated_constant,
    "task-json-write-guarded-by-unmigrated-flows": check_task_json_write_guarded,
    "plan-path-unconditional-write-removed": check_plan_path_write_removed,
}


@pytest.mark.parametrize("check", list(BRIDGE_CHECKS))
def test_temporary_task_json_bridge(check):
    """temporary-task-json-bridge."""
    run_checks(check, BRIDGE_CHECKS)


# ------------------------------- test 6: command authorizes Workflow ---


def check_authorization_sentence() -> list[str]:
    if AUTHORIZATION_SENTENCE in normalize(frontmatter.load(COMMAND_CODE).content):
        return []
    return [f"commands/code.md lacks the exact sentence: {AUTHORIZATION_SENTENCE!r}"]


def check_existing_command_content() -> list[str]:
    post = frontmatter.load(COMMAND_CODE)
    problems = []
    if post.metadata.get("name") != "code":
        problems.append("frontmatter name is not 'code'")
    if post.metadata.get("description") != (
        "Start any coding task - builds features, fixes bugs, refactors code with full TDD workflow"
    ):
        problems.append("frontmatter description changed")
    if "Use and follow the coding skill exactly as written" not in post.content:
        problems.append("'follow the coding skill exactly as written' line is gone")
    return problems


COMMAND_CHECKS = {
    "authorization-sentence-present": check_authorization_sentence,
    "existing-command-content-preserved": check_existing_command_content,
}


@pytest.mark.parametrize("check", list(COMMAND_CHECKS))
def test_command_authorizes_workflow_tool(check):
    """command-authorizes-workflow-tool."""
    run_checks(check, COMMAND_CHECKS)
