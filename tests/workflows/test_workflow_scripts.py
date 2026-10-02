"""Structural tests for workflow scripts (agent() is runtime-only, so verified statically)."""

import importlib.util
import re
from pathlib import Path

import pytest
import yaml

from tests.js_syntax import node_check_problems

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / "workflows"
GENERATOR_PATH = REPO_ROOT / "scripts" / "generate_workflow_blocks.py"
AGENT_CONFIG_PATH = REPO_ROOT / "contracts" / "agent-config.yaml"

_generator_spec = importlib.util.spec_from_file_location("generate_workflow_blocks", GENERATOR_PATH)
generator = importlib.util.module_from_spec(_generator_spec)
_generator_spec.loader.exec_module(generator)


def read_script(name):
    path = WORKFLOWS_DIR / f"{name}.js"
    assert path.exists(), f"workflow script missing: {path}"
    return path.read_text(encoding="utf-8")


def strip_generated_blocks(text):
    return generator.BLOCK.sub("", text)


def normalize_js(text):
    """Remove formatting differences: quote style, trailing commas, whitespace."""
    text = re.sub(r"'", '"', text)
    text = re.sub(r"`((?:(?!\$\{)[^`])*)`", r'"\1"', text)
    text = re.sub(r",\s*([)\]}])", r"\1", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"([(\[]) ", r"\1", text)
    return re.sub(r" ([)\]])", r"\1", text)


def normalized_script(name):
    return normalize_js(strip_generated_blocks(read_script(name)))


def extract_meta(text):
    """Return the body of `export const meta = {...}` (brace-balanced)."""
    start = re.search(r"export const meta = \{", text)
    assert start, "no `export const meta = {` found"
    depth = 1
    for index in range(start.end(), len(text)):
        depth += {"{": 1, "}": -1}.get(text[index], 0)
        if depth == 0:
            return text[start.end() : index]
    raise AssertionError("unbalanced braces in meta")


def captured_run(text):
    """Match the classify-task run call and return (result variable, block names)."""
    match = re.search(
        r'const (\w+) = await run\("classify-task", "Classify", \[(.*?)\], '
        r"TASK_CLASSIFICATION\)",
        text,
    )
    assert match, (
        'no `const X = await run("classify-task", "Classify", [...], TASK_CLASSIFICATION)`'
    )
    return match.group(1), set(re.findall(r'block\("(\w+)"', match.group(2)))


def check_meta_name(text):
    assert re.search(r'\bname: "classify"', extract_meta(text)), "meta.name is not classify"


def check_when_to_use_documents_args(text):
    match = re.search(r'\bwhenToUse: "([^"]*)"', extract_meta(text))
    assert match, "meta has no whenToUse string"
    missing = [
        arg
        for arg in ("project_root", "task", "description", "context", "human_guidance")
        if arg not in match.group(1)
    ]
    assert not missing, f"whenToUse does not document: {missing}"


def check_require_args_before_run(text):
    require = re.search(r"\brequireArgs\(", text)
    run = re.search(r"\brun\(", text)
    assert require and run, "requireArgs( and run( must both be called"
    assert require.start() < run.start(), "run( is called before requireArgs("


def check_phase_classify(text):
    assert re.search(r'\bphase\("Classify"\)', text), 'no phase("Classify") call'


def check_run_classify_task(text):
    _, block_names = captured_run(text)
    assert block_names == {"task", "human_guidance"}, f"run blocks are {block_names}"


def check_throws_on_empty_result(text):
    variable, _ = captured_run(text)
    assert re.search(rf"\bif \(!{variable}\) (?:\{{ )?throw\b", text), (
        f"no `if (!{variable}) throw` guard"
    )


def check_logs_task_type(text):
    assert re.search(r'\blog\(["`][^"`]*\btask_type', text), "log( does not mention task_type"


def check_returns_classification(text):
    variable, _ = captured_run(text)
    assert re.search(rf"\breturn {variable}(?![\w.])", text), f"does not return {variable}"


CLASSIFY_STRUCTURAL_CHECKS = {
    "meta-name": check_meta_name,
    "when-to-use-documents-args": check_when_to_use_documents_args,
    "require-args-before-run": check_require_args_before_run,
    "phase-classify": check_phase_classify,
    "run-classify-task": check_run_classify_task,
    "throws-on-empty-result": check_throws_on_empty_result,
    "logs-task-type": check_logs_task_type,
    "returns-classification": check_returns_classification,
}

FORBIDDEN = {
    "no-hand-written-agent-type": r"\bagentType\b",
    "no-inline-schema": r'\bproperties"?\s*:|\$schema|\btype"?\s*:\s*"object"',
}


@pytest.mark.parametrize(
    "check",
    CLASSIFY_STRUCTURAL_CHECKS.values(),
    ids=CLASSIFY_STRUCTURAL_CHECKS.keys(),
)
def test_classify_structure(check):
    """classify-structure: Parametrized structural checks on classify.js."""
    text = normalized_script("classify")

    check(text)


@pytest.mark.parametrize("pattern", FORBIDDEN.values(), ids=FORBIDDEN.keys())
def test_classify_forbidden_constructs_absent(pattern):
    """classify-structure: classify.js outside generated blocks avoids forbidden constructs."""
    text = normalized_script("classify")

    found = re.search(pattern, text)

    assert found is None, f"forbidden construct in classify.js: {found.group(0)!r}"


# ---- per-script checkers (each returns a list of problem messages) -----------

SCRIPT_PATHS = sorted(WORKFLOWS_DIR.glob("*.js"))
SCRIPT_IDS = [path.stem for path in SCRIPT_PATHS]
# Spike scripts that predate the generated blocks; remove entries when TB5.1 migrates them.
KNOWN_SPIKE_FILES = frozenset({"bug-fix-pilot", "spike-probe"})
# spike-probe.js predates the meta contract and has no whenToUse (bug-fix-pilot.js has one).
SPIKE_META_EXEMPT_FIELDS = {"spike-probe": frozenset({"whenToUse"})}
META_REQUIRED_FIELDS = ("name", "description", "whenToUse")

AGENT_TYPE_REFERENCE = re.compile(r"\bagentType\s*:\s*[\"'`]pairingbuddy:([\w-]+)[\"'`]")
RUN_LITERAL_REFERENCE = re.compile(r"\brun\(\s*[\"']([\w-]+)[\"']")


def known_agents():
    config = yaml.safe_load(AGENT_CONFIG_PATH.read_text(encoding="utf-8"))
    return set(config["agents"])


def agent_references(text):
    """Return [(construct, agent name)] for every agentType literal and run('<x>') call."""
    return [
        (f"agentType pairingbuddy:{name!r}", name) for name in AGENT_TYPE_REFERENCE.findall(text)
    ] + [(f"run({name!r})", name) for name in RUN_LITERAL_REFERENCE.findall(text)]


def agent_name_problems(text, agents=None):
    agents = known_agents() if agents is None else agents
    return [
        f"{construct} is not an agent in contracts/agent-config.yaml"
        for construct, name in agent_references(text)
        if name not in agents
    ]


def top_level_fields(meta):
    """Map each field name at depth 0 of the meta body to its raw value text."""
    entries, depth, quote, start = [], 0, False, 0
    for index, char in enumerate(meta):
        if char == '"':
            quote = not quote
        elif quote:
            continue
        elif char in "{[(":
            depth += 1
        elif char in "}])":
            depth -= 1
        elif char == "," and depth == 0:
            entries.append(meta[start:index])
            start = index + 1
    entries.append(meta[start:])
    fields = {}
    for entry in entries:
        match = re.match(r"\s*(\w+)\s*:\s*(.*)", entry, re.DOTALL)
        if match:
            fields[match.group(1)] = match.group(2).strip()
    return fields


def meta_string_field(fields, name):
    match = re.fullmatch(r'"([^"]*)"', fields.get(name, ""))
    return match.group(1) if match else ""


def meta_fields(text):
    """Return (top-level fields, problems) for the script's meta object."""
    try:
        return top_level_fields(extract_meta(normalize_js(text))), []
    except AssertionError as error:
        return {}, [f"meta: {error}"]


def required_meta_problems(text, exempt_fields=()):
    fields, problems = meta_fields(text)
    if problems:
        return problems
    for field in META_REQUIRED_FIELDS:
        if field not in exempt_fields and not meta_string_field(fields, field).strip():
            problems.append(f"meta.{field} is missing or empty")
    if not re.search(r'\[[^\]]*\btitle\s*:\s*"[^"]+"', fields.get("phases", "")):
        problems.append("meta.phases is missing or empty")
    return problems


def name_stem_problems(text, stem):
    """Report a meta.name that differs from the file stem (a missing name is reported elsewhere)."""
    fields, _ = meta_fields(text)
    name = meta_string_field(fields, "name")
    if name and name != stem:
        return [f"meta.name {name!r} does not match file stem {stem!r}"]
    return []


def has_generated_markers(text):
    return generator.MARKER.search(text) is not None


def sync_problems(path, schemas_dir=generator.DEFAULT_SCHEMAS_DIR):
    text = path.read_text(encoding="utf-8")
    if not has_generated_markers(text):
        if path.stem in KNOWN_SPIKE_FILES:
            return []
        return [f"{path.name} has no generated markers and is not a known spike file"]
    try:
        stale = generator.process_path(path, schemas_dir, check=True)
    except generator.GenerationError as error:
        return [f"generated blocks cannot be checked: {error}"]
    return (
        ["generated blocks are out of date; run scripts/generate_workflow_blocks.py"]
        if stale
        else []
    )


def script_required_meta_problems(path):
    exempt = SPIKE_META_EXEMPT_FIELDS.get(path.stem, ())
    return required_meta_problems(path.read_text(encoding="utf-8"), exempt)


def script_name_stem_problems(path):
    return name_stem_problems(path.read_text(encoding="utf-8"), path.stem)


SYNTHETIC_SCRIPT = """\
export const meta = {
  name: 'synthetic',
  description: 'A synthetic script',
  whenToUse: 'Only in tests',
  phases: [{ title: 'Only' }],
}

// <generated:helper>
// </generated:helper>

phase('Only')
const result = await run('classify-task', 'Only', [], {})
await agent('go', { agentType: 'pairingbuddy:implement-code' })
return result
"""


@pytest.fixture
def synthetic_script(tmp_path):
    """A valid synthetic script (helper block filled in by the generator) plus a writer."""
    path = tmp_path / "synthetic.js"

    def write(mutate=lambda text: text):
        path.write_text(SYNTHETIC_SCRIPT, encoding="utf-8")
        generator.process_path(path, generator.DEFAULT_SCHEMAS_DIR, check=False)
        path.write_text(mutate(path.read_text(encoding="utf-8")), encoding="utf-8")
        return path

    return write


def replace_once(old, new):
    def mutate(text):
        assert old in text, f"mutation target not found: {old!r}"
        return text.replace(old, new, 1)

    return mutate


@pytest.mark.parametrize("path", SCRIPT_PATHS, ids=SCRIPT_IDS)
def test_node_check_passes_for_each_script(path):
    """node-check-passes-for-each-script: node --check passes for each script."""
    problems = node_check_problems(path)

    assert problems == [], f"{path.name}: {problems}"


def test_node_check_fails_on_syntactically_broken_script(tmp_path):
    """node-check-fails-on-syntactically-broken-script: Syntax checker reports failure."""
    broken = tmp_path / "broken.js"
    broken.write_text("const x = (;\n", encoding="utf-8")

    problems = node_check_problems(broken)

    assert len(problems) == 1, f"expected one problem, got {problems}"
    assert "node --check failed" in problems[0] and "SyntaxError" in problems[0], problems


@pytest.mark.parametrize("path", SCRIPT_PATHS, ids=SCRIPT_IDS)
def test_agent_names_in_each_script_resolve_to_agent_config(path):
    """agent-names-in-each-script-resolve-to-agent-config: Agent names resolve."""
    text = path.read_text(encoding="utf-8")

    problems = agent_name_problems(text)

    assert agent_references(text), f"{path.name} references no agents; checker may be stale"
    assert problems == [], f"{path.name}: {problems}"


def test_known_agent_names_produce_no_problems(synthetic_script):
    """known-agent-names-produce-no-problems: Valid agent names yield no problems."""
    text = synthetic_script().read_text(encoding="utf-8")

    problems = agent_name_problems(text)
    references = agent_references(text)

    assert ("run('classify-task')", "classify-task") in references, (
        f"run literal not seen in {references}"
    )
    assert ("agentType pairingbuddy:'implement-code'", "implement-code") in references, (
        f"agentType literal not seen in {references}"
    )
    assert problems == [], f"known agent names were flagged: {problems}"


@pytest.mark.parametrize("path", SCRIPT_PATHS, ids=SCRIPT_IDS)
def test_meta_has_required_fields_and_nonempty_phases_for_each_script(path):
    """meta-has-required-fields-and-nonempty-phases-for-each-script: Meta is complete."""
    problems = script_required_meta_problems(path)

    assert problems == [], f"{path.name}: {problems}"


@pytest.mark.parametrize("path", SCRIPT_PATHS, ids=SCRIPT_IDS)
def test_meta_name_matches_file_stem_for_each_script(path):
    """meta-name-matches-file-stem-for-each-script: meta.name matches file stem."""
    problems = script_name_stem_problems(path)

    assert problems == [], f"{path.name}: {problems}"


@pytest.mark.parametrize("path", SCRIPT_PATHS, ids=SCRIPT_IDS)
def test_generated_blocks_in_sync_for_each_script(path):
    """generated-blocks-in-sync-for-each-script: Generated blocks match output."""
    problems = sync_problems(path)

    assert problems == [], f"{path.name}: {problems}"


@pytest.mark.parametrize(
    ("block_name", "old", "new"),
    [
        ("schemas", "Why this classification was chosen", "Edited by hand"),
        ("helper", "const projectRoot = ARGS", "const projectRoot = 1 || ARGS"),
    ],
    ids=["schemas-block", "helper-block"],
)
def test_hand_edited_generated_block_is_detected(tmp_path, block_name, old, new):
    """hand-edited-generated-block-is-detected: Edited block fails sync check."""
    copy = tmp_path / "classify.js"
    original = (WORKFLOWS_DIR / "classify.js").read_text(encoding="utf-8")
    copy.write_text(original, encoding="utf-8")
    assert sync_problems(copy) == [], "unedited copy must be in sync (control)"
    block = re.search(
        rf"// <generated:{block_name}.*?// </generated:{block_name}>", original, re.DOTALL
    )
    assert block and old in block.group(0), f"{old!r} is not inside the {block_name} block"
    copy.write_text(original.replace(old, new, 1), encoding="utf-8")

    problems = sync_problems(copy)

    assert len(problems) == 1 and "out of date" in problems[0], problems


def test_spike_files_without_markers_tolerated():
    """spike-files-without-markers-tolerated: Spike files skip sync check."""
    spike_paths = [WORKFLOWS_DIR / f"{stem}.js" for stem in sorted(KNOWN_SPIKE_FILES)]
    missing = [path.name for path in spike_paths if not path.exists()]
    assert not missing, f"stale KNOWN_SPIKE_FILES entry: {missing}"

    spike_problems = {path.name: sync_problems(path) for path in spike_paths}
    marker_flags = {
        path.name: has_generated_markers(path.read_text("utf-8")) for path in spike_paths
    }

    assert not any(marker_flags.values()), f"spike file gained markers; drop it: {marker_flags}"
    assert spike_problems == {path.name: [] for path in spike_paths}, (
        f"spike files should be tolerated: {spike_problems}"
    )


def test_unknown_file_without_markers_is_flagged(tmp_path):
    """unknown-file-without-markers-is-flagged: Non-spike files need generated markers."""
    unknown = tmp_path / "not-a-spike.js"
    unknown.write_text("export const meta = {}\n", encoding="utf-8")

    problems = sync_problems(unknown)

    assert len(problems) == 1 and "no generated markers" in problems[0], problems


VIOLATIONS = {
    "unknown-run-literal": (
        replace_once("run('classify-task'", "run('no-such-agent'"),
        lambda path: agent_name_problems(path.read_text(encoding="utf-8")),
        "run('no-such-agent') is not an agent",
    ),
    "unknown-agent-type": (
        replace_once("pairingbuddy:implement-code", "pairingbuddy:no-such-agent"),
        lambda path: agent_name_problems(path.read_text(encoding="utf-8")),
        "agentType pairingbuddy:'no-such-agent' is not an agent",
    ),
    "missing-when-to-use": (
        replace_once("  whenToUse: 'Only in tests',\n", ""),
        script_required_meta_problems,
        "meta.whenToUse is missing or empty",
    ),
    "empty-phases": (
        replace_once("phases: [{ title: 'Only' }]", "phases: []"),
        script_required_meta_problems,
        "meta.phases is missing or empty",
    ),
    "name-only-nested-in-phase": (
        lambda text: replace_once(
            "phases: [{ title: 'Only' }]", "phases: [{ title: 'Only', name: 'synthetic' }]"
        )(replace_once("  name: 'synthetic',\n", "")(text)),
        script_required_meta_problems,
        "meta.name is missing or empty",
    ),
    "name-stem-mismatch": (
        replace_once("name: 'synthetic'", "name: 'other'"),
        script_name_stem_problems,
        "meta.name 'other' does not match file stem 'synthetic'",
    ),
    "hand-edited-generated-block": (
        replace_once("const projectRoot = ARGS", "const projectRoot = 1 || ARGS"),
        sync_problems,
        "out of date",
    ),
    "broken-js-syntax": (
        replace_once("phase('Only')", "phase('Only'"),
        node_check_problems,
        "node --check failed",
    ),
}


@pytest.mark.parametrize(
    ("mutate", "checker", "expected"), VIOLATIONS.values(), ids=VIOLATIONS.keys()
)
def test_checker_reports_each_violation(synthetic_script, mutate, checker, expected):
    """checker-reports-each-violation: Violations reported with specific messages."""
    control = synthetic_script()
    assert checker(control) == [], "control must be clean"
    path = synthetic_script(mutate)

    problems = checker(path)

    assert len(problems) == 1, f"expected exactly one problem, got {problems}"
    assert expected in problems[0], problems
