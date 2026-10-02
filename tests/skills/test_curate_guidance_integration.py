"""Structural tests for the curate-guidance workflow integration in the coding skill.

The coding skill's Workflow pseudocode must curate guidance through the
`pairingbuddy:curate-guidance` workflow instead of the curate_guidance agent, and
the human review of the proposal must be defined once, in the `## Review loop`
section, and referenced by name from each curate site. Checks are anchored on the
AST of the pseudocode block and on exact contract strings, so they reject real
defects rather than formatting changes. Prose meaning is deliberately not tested.
"""

import ast
import re

import pytest
import yaml

from tests.agents.test_workflow_logic import ORCHESTRATOR_SKILL, PROJECT_ROOT
from tests.skills.test_classify_workflow_integration import (
    enclosing_statement,
    has_ancestor,
    is_call_to,
    normalize,
    parent_map,
    parse_workflow,
    resolve_workflow_args,
    run_checks,
    sibling_list,
    skill_text,
    workflow_calls_named,
)

CURATE_WORKFLOW_NAME = "pairingbuddy:curate-guidance"
GUIDANCE_PATH = ".pairingbuddy/human-guidance.json"
REVIEW_LOOP = "Review loop"
REVIEW_LOOP_HEADING = f"## {REVIEW_LOOP}"
SKILL_CONFIG = PROJECT_ROOT / "contracts" / "skill-config.yaml"
REVIEW_LOOP_SPANS = [
    "AskUserQuestion",
    GUIDANCE_PATH,
    "human_feedback",
    "previous_proposal",
]
REVIEW_LOOP_ENTRY_FIELDS = {"agent", "timestamp", "context", "feedback"}
REVIEW_LOOP_ONLY_STRINGS = ["AskUserQuestion", "human_feedback", "previous_proposal"]
REVIEW_LOOP_SECTION = re.compile(
    rf"^{re.escape(REVIEW_LOOP_HEADING)}[ \t]*$\n(.*?)(?=\n## |\Z)", re.M | re.S
)
LOOP_REFERENCE = re.compile(rf"^\s*#\s*{REVIEW_LOOP}\b")


# ---------------------------------------------------------------- helpers ---


def curate_calls(tree: ast.AST) -> list[ast.Call]:
    return workflow_calls_named(tree, CURATE_WORKFLOW_NAME)


def result_name(call: ast.Call, parents: dict) -> str | None:
    stmt = enclosing_statement(call, parents)
    targets = getattr(stmt, "targets", [])
    return targets[0].id if len(targets) == 1 and isinstance(targets[0], ast.Name) else None


def is_cleanup(stmt: ast.stmt) -> bool:
    return isinstance(stmt, ast.Expr) and is_call_to(stmt.value, "_cleanup_state_files")


def first_arg_is_guidance_path(call: ast.Call) -> bool:
    return (
        bool(call.args)
        and isinstance(call.args[0], ast.Constant)
        and call.args[0].value == GUIDANCE_PATH
    )


def is_guidance_write(node: ast.AST) -> bool:
    return is_call_to(node, "_write_json") and first_arg_is_guidance_path(node)


def guidance_writes(tree: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(tree) if is_guidance_write(n)]


def loop_reference_line(code: str, call: ast.Call, parents: dict) -> int | None:
    """Line number of the `# Review loop` comment right after the curate statement."""
    stmt = enclosing_statement(call, parents)
    lines = code.splitlines()
    following = stmt.end_lineno  # 0-based index of the line after the statement
    if following < len(lines) and LOOP_REFERENCE.match(lines[following]):
        return following + 1
    return None


def review_loop_section() -> str | None:
    match = REVIEW_LOOP_SECTION.search(ORCHESTRATOR_SKILL.read_text())
    return match.group(1) if match else None


def outside_review_loop() -> str:
    return REVIEW_LOOP_SECTION.sub("", ORCHESTRATOR_SKILL.read_text())


def state_file_management_step_2() -> str | None:
    text = ORCHESTRATOR_SKILL.read_text()
    section = re.search(r"^### State File Management[ \t]*$\n(.*?)(?=\n##+ |\Z)", text, re.M | re.S)
    if section is None:
        return None
    step = re.search(r"^2\. .*$", section.group(1), re.M)
    return step.group(0) if step else None


# ------------------------------------------------ test 1: curate calls ---


def check_two_curate_calls_in_expected_positions() -> list[str]:
    _, tree = parse_workflow()
    parents = parent_map(tree)
    calls = curate_calls(tree)
    if len(calls) != 2:
        return [
            f"expected exactly two Workflow('{CURATE_WORKFLOW_NAME}') calls, found {len(calls)}"
        ]
    problems = []
    in_loop = [c for c in calls if has_ancestor(c, parents, ast.For)]
    at_top = [c for c in calls if isinstance(parents[enclosing_statement(c, parents)], ast.Module)]
    if len(in_loop) != 1:
        problems.append(f"expected one curate call inside the plan loop, found {len(in_loop)}")
    elif not has_ancestor(in_loop[0], parents, ast.If):
        problems.append("plan-loop curate call is not inside the plan-execution branch")
    if len(at_top) != 1:
        problems.append(f"expected one module-level curate call, found {len(at_top)}")
    return problems


def check_curate_precedes_cleanup() -> list[str]:
    _, tree = parse_workflow()
    parents = parent_map(tree)
    calls = curate_calls(tree)
    if not calls:
        return ["no curate Workflow calls"]
    problems = []
    for call in calls:
        stmt = enclosing_statement(call, parents)
        stmts = sibling_list(stmt, parents)
        after = stmts[stmts.index(stmt) + 1 :]
        if not any(is_cleanup(s) for s in after):
            problems.append(
                f"curate call on line {call.lineno} is not followed by _cleanup_state_files()"
            )
    return problems


CURATE_CALL_CHECKS = {
    "two-curate-workflow-calls-in-expected-positions": check_two_curate_calls_in_expected_positions,
    "curate-runs-before-cleanup": check_curate_precedes_cleanup,
}


@pytest.mark.parametrize("check", list(CURATE_CALL_CHECKS))
def test_curate_workflow_calls_in_expected_positions(check):
    """curate-workflow-calls-in-expected-positions."""
    run_checks(check, CURATE_CALL_CHECKS)


# ------------------------------- test 2: no curate_guidance agent call ---


def check_no_curate_guidance_call_in_ast() -> list[str]:
    _, tree = parse_workflow()
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and (
            (isinstance(n.func, ast.Name) and n.func.id == "curate_guidance")
            or (isinstance(n.func, ast.Attribute) and n.func.attr == "curate_guidance")
        )
    ]
    problems = [f"curate_guidance(...) call remains on line {c.lineno}" for c in calls]
    if not curate_calls(tree):
        problems.append("no curate Workflow calls replace the curate_guidance agent call")
    return problems


def check_no_invoke_agent_text() -> list[str]:
    text = skill_text()
    problems = []
    if "Invoke `curate_guidance` agent" in text:
        problems.append("skill text still says 'Invoke `curate_guidance` agent'")
    return problems


NO_AGENT_CHECKS = {
    "no-curate-guidance-call-in-pseudocode-ast": check_no_curate_guidance_call_in_ast,
    "no-invoke-curate-guidance-agent-text": check_no_invoke_agent_text,
}


@pytest.mark.parametrize("check", list(NO_AGENT_CHECKS))
def test_no_curate_guidance_agent_call_remains(check):
    """no-curate-guidance-agent-call-remains."""
    run_checks(check, NO_AGENT_CHECKS)


# ------------------------------------------------------ test 3: arguments ---


def curate_args_by_call() -> list[tuple[ast.Call, dict, list[str]]]:
    _, tree = parse_workflow()
    parents = parent_map(tree)
    return [(c, *resolve_workflow_args(c, parents, "curate")) for c in curate_calls(tree)]


def check_curate_args_project_root_and_task() -> list[str]:
    results = curate_args_by_call()
    if not results:
        return ["no curate Workflow calls"]
    problems = []
    for call, entries, resolve_problems in results:
        problems.extend(resolve_problems)
        root = entries.get("project_root", (None, False))[0]
        if not is_call_to(root, "_absolute_project_root"):
            problems.append(
                f"curate call on line {call.lineno}: project_root is not _absolute_project_root()"
            )
        task = entries.get("task", (None, False))
        if not (isinstance(task[0], ast.Name) and task[0].id == "task" and not task[1]):
            problems.append(f"curate call on line {call.lineno}: task is not passed as `task`")
    return problems


def check_curate_human_guidance_only_when_present() -> list[str]:
    results = curate_args_by_call()
    if not results:
        return ["no curate Workflow calls"]
    problems = []
    for call, entries, resolve_problems in results:
        problems.extend(resolve_problems)
        if "human_guidance" not in entries:
            problems.append(f"curate call on line {call.lineno} never passes human_guidance")
            continue
        value, guarded = entries["human_guidance"]
        if not guarded:
            problems.append(
                f"curate call on line {call.lineno} passes human_guidance unconditionally"
            )
        if not (is_call_to(value, "_read_json") and first_arg_is_guidance_path(value)):
            problems.append(
                f"curate call on line {call.lineno}: human_guidance is not "
                f"_read_json('{GUIDANCE_PATH}')"
            )
    return problems


def check_initial_curate_call_has_no_revision_keys() -> list[str]:
    results = curate_args_by_call()
    if not results:
        return ["no curate Workflow calls"]
    return [
        f"initial curate call on line {call.lineno} passes {key!r}"
        for call, entries, _ in results
        for key in ("previous_proposal", "human_feedback")
        if key in entries
    ]


CURATE_ARGS_CHECKS = {
    "args-have-absolute-project-root-and-task": check_curate_args_project_root_and_task,
    "human-guidance-passed-only-when-present": check_curate_human_guidance_only_when_present,
    "initial-call-has-no-previous-proposal-or-human-feedback": (
        check_initial_curate_call_has_no_revision_keys
    ),
}


@pytest.mark.parametrize("check", list(CURATE_ARGS_CHECKS))
def test_curate_args_keys_and_guards(check):
    """curate-args-keys-and-guards."""
    run_checks(check, CURATE_ARGS_CHECKS)


# ------------------------------------------- test 4: Review loop section ---


def check_review_loop_heading_once() -> list[str]:
    count = len(
        re.findall(
            rf"^{re.escape(REVIEW_LOOP_HEADING)}[ \t]*$", ORCHESTRATOR_SKILL.read_text(), re.M
        )
    )
    return (
        []
        if count == 1
        else [f"'{REVIEW_LOOP_HEADING}' heading appears {count} times, expected once"]
    )


def check_review_loop_contract_strings_in_order() -> list[str]:
    section = review_loop_section()
    if section is None:
        return [f"no '{REVIEW_LOOP_HEADING}' section"]
    spans = re.findall(r"`([^`\n]+)`", section)
    position = 0
    problems = []
    for expected in REVIEW_LOOP_SPANS:
        if expected in spans[position:]:
            position += spans[position:].index(expected) + 1
        else:
            problems.append(f"`{expected}` is missing or out of order in the Review loop section")
    problems.extend(
        f"guidance entry field `{field}` is missing from the Review loop section"
        for field in sorted(REVIEW_LOOP_ENTRY_FIELDS - set(spans))
    )
    return problems


def check_review_loop_solo_note() -> list[str]:
    section = review_loop_section()
    if section is None:
        return [f"no '{REVIEW_LOOP_HEADING}' section"]
    notes = [
        line for line in map(normalize, section.splitlines()) if line.startswith("**Solo note:**")
    ]
    steps = re.findall(r"^(\d+)\. ", section, re.M)
    problems = []
    if len(notes) != 1 or "steps 2-3" not in notes[0]:
        problems.append("Review loop lacks one '**Solo note:**' line naming 'steps 2-3'")
    if not {"1", "2", "3"} <= set(steps):
        problems.append("Review loop does not number steps 1, 2 and 3")
    return problems


def check_skill_config_lists_review_loop_once() -> list[str]:
    with open(SKILL_CONFIG) as f:
        sections = yaml.safe_load(f)["skills"]["coding"]["sections"]
    headings = [s["heading"] for s in sections]
    if headings.count(REVIEW_LOOP_HEADING) != 1:
        return [f"skill-config coding sections must list '{REVIEW_LOOP_HEADING}' exactly once"]
    return []


REVIEW_LOOP_CHECKS = {
    "review-loop-heading-exactly-once": check_review_loop_heading_once,
    "review-loop-contract-strings-in-step-order": check_review_loop_contract_strings_in_order,
    "review-loop-solo-note-names-steps-2-3": check_review_loop_solo_note,
    "skill-config-lists-review-loop-once": (check_skill_config_lists_review_loop_once),
}


@pytest.mark.parametrize("check", list(REVIEW_LOOP_CHECKS))
def test_review_loop_section_defined_once(check):
    """review-loop-section-defined-once."""
    run_checks(check, REVIEW_LOOP_CHECKS)


# ------------------------------------------ test 5: references by name ---


def check_pseudocode_sites_reference_review_loop() -> list[str]:
    code, tree = parse_workflow()
    parents = parent_map(tree)
    calls = curate_calls(tree)
    if not calls:
        return ["no curate Workflow calls"]
    return [
        f"curate call on line {call.lineno} has no '# {REVIEW_LOOP}' comment on the next line"
        for call in calls
        if loop_reference_line(code, call, parents) is None
    ]


def check_state_file_management_references_review_loop() -> list[str]:
    step = state_file_management_step_2()
    if step is None:
        return ["State File Management step 2 not found"]
    if REVIEW_LOOP not in step:
        return [f"State File Management step 2 does not reference '{REVIEW_LOOP}'"]
    return []


def check_loop_steps_not_duplicated() -> list[str]:
    if review_loop_section() is None:
        return [f"no '{REVIEW_LOOP_HEADING}' section to be referenced"]
    outside = outside_review_loop()
    return [
        f"`{string}` appears outside the Review loop section"
        for string in REVIEW_LOOP_ONLY_STRINGS
        if string in outside
    ]


REFERENCE_CHECKS = {
    "pseudocode-curate-sites-reference-review-loop": check_pseudocode_sites_reference_review_loop,
    "state-file-management-step-2-references-review-loop": (
        check_state_file_management_references_review_loop
    ),
    "review-loop-strings-only-in-review-loop-section": check_loop_steps_not_duplicated,
}


@pytest.mark.parametrize("check", list(REFERENCE_CHECKS))
def test_curate_step_references_review_loop_by_name(check):
    """curate-step-references-review-loop-by-name."""
    run_checks(check, REFERENCE_CHECKS)


# ------------------------------------------------ test 6: skill writes ---


def check_one_write_per_curate_site() -> list[str]:
    code, tree = parse_workflow()
    parents = parent_map(tree)
    calls = curate_calls(tree)
    if not calls:
        return ["no curate Workflow calls"]
    problems = []
    total = len(guidance_writes(tree))
    if total != len(calls):
        problems.append(f"expected {len(calls)} writes to {GUIDANCE_PATH}, found {total}")
    for call in calls:
        stmt = enclosing_statement(call, parents)
        stmts = sibling_list(stmt, parents)
        after = stmts[stmts.index(stmt) + 1 :]
        writes = [s for s in after if isinstance(s, ast.Expr) and is_guidance_write(s.value)]
        cleanup = next((s for s in after if is_cleanup(s)), None)
        reference = loop_reference_line(code, call, parents)
        if len(writes) != 1:
            problems.append(
                f"curate call on line {call.lineno} is followed by {len(writes)} writes "
                f"to {GUIDANCE_PATH}, expected one"
            )
            continue
        if reference is None or writes[0].lineno <= reference:
            problems.append(
                f"write on line {writes[0].lineno} is not after the '# {REVIEW_LOOP}' reference"
            )
        if cleanup is None or writes[0].lineno >= cleanup.lineno:
            problems.append(
                f"write on line {writes[0].lineno} is not before _cleanup_state_files()"
            )
    return problems


def check_write_is_verbatim_proposal() -> list[str]:
    _, tree = parse_workflow()
    parents = parent_map(tree)
    calls = curate_calls(tree)
    if not calls:
        return ["no curate Workflow calls"]
    results = {result_name(c, parents) for c in calls}
    writes = guidance_writes(tree)
    if not writes:
        return [f"no write to {GUIDANCE_PATH}"]
    problems = []
    for write in writes:
        data = write.args[1] if len(write.args) == 2 and not write.keywords else None
        if not (
            isinstance(data, ast.Attribute)
            and data.attr == "proposal"
            and isinstance(data.value, ast.Name)
            and data.value.id in results - {None}
        ):
            problems.append(
                f"write on line {write.lineno} is not _write_json('{GUIDANCE_PATH}', "
                "<curate result>.proposal)"
            )
    return problems


WRITE_CHECKS = {
    "one-write-per-curate-site-between-loop-reference-and-cleanup": check_one_write_per_curate_site,
    "write-is-verbatim-result-proposal": check_write_is_verbatim_proposal,
}


@pytest.mark.parametrize("check", list(WRITE_CHECKS))
def test_approved_proposal_written_verbatim_by_skill(check):
    """approved-proposal-written-verbatim-by-skill."""
    run_checks(check, WRITE_CHECKS)
