"""Tests for scripts/generate_workflow_blocks.py

Tests cover:
- Schemas block generation (declared schemas, meta-keywords stripping, deterministic output)
- Write mode preservation (idempotency, marker boundaries, untouched files)
- Check mode (fresh file passing, drift detection, listing stale files)
- Error handling (exit 2 with one clear `error:` line and no traceback; the file stays untouched):
  unknown schemas, invalid or unreadable schema files, unbalanced, misordered or nested markers
  (reported with a line number), missing or non-UTF-8 target files
- Continue-on-error across several paths (write and --check): good files are still written or
  listed as stale while the failing file is reported
- Path selection (explicit paths, or every *.js in --workflows-dir when none are given)
"""

import json
import os
import re
import subprocess
import sys
from collections import namedtuple
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "generate_workflow_blocks.py"
SCHEMAS_DIR = REPO_ROOT / "contracts" / "schemas"
GENERATOR_TIMEOUT_SECONDS = 60
EXIT_OK = 0
EXIT_STALE = 1
EXIT_ERROR = 2
# A timestamp far in the past: any rewrite of the file would bump mtime to "now".
PAST_MTIME_NS = 1_000_000_000_000_000_000

SCHEMAS_OPEN_MARKER = (
    "// <generated:schemas {names}> — do not edit; regenerate from contracts/schemas"
)
SCHEMAS_CLOSE_MARKER = "// </generated:schemas>"


def run_generator(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *map(str, args)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=GENERATOR_TIMEOUT_SECONDS,
    )


def load_contract_schema(name):
    schema = json.loads((SCHEMAS_DIR / f"{name}.schema.json").read_text())
    schema.pop("$schema", None)
    schema.pop("$id", None)
    return schema


def schemas_block_source(*names, before="// header\n", after="// footer\n"):
    open_marker = SCHEMAS_OPEN_MARKER.format(names=" ".join(names))
    return f"{before}{open_marker}\n{SCHEMAS_CLOSE_MARKER}\n{after}"


def generate_workflow(path, *schema_names):
    path.write_text(schemas_block_source(*schema_names))
    result = run_generator(path)
    assert result.returncode == 0, f"write mode failed: {result.stderr}"
    return path.read_bytes()


def make_stale_workflow(path):
    generate_workflow(path, "task-classification")
    generated = extract_schemas_block(path.read_text())
    path.write_text(path.read_text().replace(generated, generated + "// hand edit\n"))
    assert extract_schemas_block(path.read_text()) != generated, "hand edit should change block"
    return path


def freeze_mtime(path):
    os.utime(path, ns=(PAST_MTIME_NS, PAST_MTIME_NS))


def assert_not_rewritten(path, expected_bytes):
    assert path.read_bytes() == expected_bytes, "file content should be unchanged"
    assert path.stat().st_mtime_ns == PAST_MTIME_NS, (
        "file should not be rewritten (mtime_ns changed)"
    )


def error_lines(result):
    return [line for line in result.stderr.splitlines() if line.startswith("error:")]


def assert_no_traceback(result):
    assert "Traceback" not in result.stdout + result.stderr, (
        "should be a clear error, not a Python traceback"
    )


MixedRun = namedtuple("MixedRun", "result good_paths expected_good_bytes expected_stale_lines")


def run_with_bad_target(tmp_path, bad, check_mode):
    """Run the generator over two stale good files around `bad` (already created)."""
    first_good = make_stale_workflow(tmp_path / "good_one.js")
    second_good = make_stale_workflow(tmp_path / "good_two.js")
    stale_bytes = first_good.read_bytes()
    fresh_bytes = generate_workflow(tmp_path / "reference.js", "task-classification")
    good_paths = [first_good, second_good]
    for path in good_paths:
        freeze_mtime(path)
    args = ["--check"] if check_mode else []

    result = run_generator(*args, first_good, bad, second_good)

    return MixedRun(
        result=result,
        good_paths=good_paths,
        expected_good_bytes=stale_bytes if check_mode else fresh_bytes,
        expected_stale_lines=[f"stale: {path}" for path in good_paths] if check_mode else [],
    )


def assert_good_files_outcome(mixed, check_mode, context):
    stale_lines = [line for line in mixed.result.stderr.splitlines() if line.startswith("stale:")]
    assert stale_lines == mixed.expected_stale_lines, (
        "stale lines should list every readable stale file in --check and none in write mode"
    )
    for path in mixed.good_paths:
        assert path.read_bytes() == mixed.expected_good_bytes, (
            f"{path.name} should be {'left as is' if check_mode else 'written'} despite {context}"
        )


def extract_schemas_block(content):
    block = re.search(
        rf"// <generated:schemas[^\n]*\n(.*?){re.escape(SCHEMAS_CLOSE_MARKER)}",
        content,
        re.DOTALL,
    )
    assert block is not None, "markers missing after generation"
    return block.group(1)


def parse_constants(body):
    constants = {}
    for name, value in re.findall(r"const ([A-Z_]+) = (.*?);?\s*(?=const |\Z)", body, re.DOTALL):
        constants[name] = json.loads(value)
    return constants


def test_declared_schema_produces_constant(tmp_path):
    """A declared schema (task-classification) produces a TASK_CLASSIFICATION const in the block"""
    workflow = tmp_path / "classify.js"
    workflow.write_text(schemas_block_source("task-classification"))
    expected_schema = load_contract_schema("task-classification")

    result = run_generator(workflow)

    assert result.returncode == 0, f"generator failed: {result.stderr}"
    constants = parse_constants(extract_schemas_block(workflow.read_text()))
    assert constants == {"TASK_CLASSIFICATION": expected_schema}


def test_multiple_schemas_emitted_in_declared_order(tmp_path):
    """Two declared names each get an UPPER_SNAKE constant, in the declared order"""
    workflow = tmp_path / "multi.js"
    workflow.write_text(schemas_block_source("task-classification", "commit-result"))
    expected = {
        "TASK_CLASSIFICATION": load_contract_schema("task-classification"),
        "COMMIT_RESULT": load_contract_schema("commit-result"),
    }

    result = run_generator(workflow)

    assert result.returncode == 0, f"generator failed: {result.stderr}"
    block = extract_schemas_block(workflow.read_text())
    constants = parse_constants(block)
    assert constants == expected, "each declared schema should be emitted as its constant"
    assert re.findall(r"const ([A-Z_]+) =", block) == list(expected), (
        "constants should follow the declared order, not alphabetical order"
    )


def test_meta_keywords_stripped(tmp_path):
    """$schema and $id are removed from the output while title and description are kept"""
    schemas_dir = tmp_path / "schemas"
    schemas_dir.mkdir()
    source_schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "$id": "https://example.test/meta-probe.schema.json",
        "title": "Meta probe",
        "description": "Schema used to probe meta keyword stripping",
        "type": "object",
    }
    (schemas_dir / "meta-probe.schema.json").write_text(json.dumps(source_schema))
    workflow = tmp_path / "meta.js"
    workflow.write_text(schemas_block_source("meta-probe"))

    result = run_generator("--schemas-dir", schemas_dir, workflow)

    assert result.returncode == 0, f"generator failed: {result.stderr}"
    generated = parse_constants(extract_schemas_block(workflow.read_text()))["META_PROBE"]
    assert "$schema" not in generated, "$schema should be stripped"
    assert "$id" not in generated, "$id should be stripped"
    assert generated["title"] == source_schema["title"], "title should be kept"
    assert generated["description"] == source_schema["description"], "description should be kept"


def test_output_is_deterministic(tmp_path):
    """Output uses 2-space indentation and preserves the source file's key order
    at every level, so repeated runs give identical text"""
    schemas_dir = tmp_path / "schemas"
    schemas_dir.mkdir()
    source_schema = {
        "type": "object",
        "title": "Probe",
        "required": ["zeta", "alpha"],
        "properties": {
            "zeta": {"type": "string"},
            "alpha": {"type": "integer", "description": "a"},
        },
    }
    (schemas_dir / "determinism-probe.schema.json").write_text(json.dumps(source_schema))
    first = tmp_path / "first.js"
    second = tmp_path / "second.js"
    first.write_text(schemas_block_source("determinism-probe"))
    second.write_text(schemas_block_source("determinism-probe"))
    expected_body = (
        "const DETERMINISM_PROBE = {\n"
        '  "type": "object",\n'
        '  "title": "Probe",\n'
        '  "required": [\n'
        '    "zeta",\n'
        '    "alpha"\n'
        "  ],\n"
        '  "properties": {\n'
        '    "zeta": {\n'
        '      "type": "string"\n'
        "    },\n"
        '    "alpha": {\n'
        '      "type": "integer",\n'
        '      "description": "a"\n'
        "    }\n"
        "  }\n"
        "};\n"
    )

    first_result = run_generator("--schemas-dir", schemas_dir, first)
    second_result = run_generator("--schemas-dir", schemas_dir, second)

    assert first_result.returncode == 0, f"generator failed: {first_result.stderr}"
    assert second_result.returncode == 0, f"generator failed: {second_result.stderr}"
    assert extract_schemas_block(first.read_text()) == expected_body, (
        "block should use 2-space indentation with source key order preserved at every level"
    )
    assert first.read_text() == second.read_text(), "repeated runs should give identical text"


def test_rewrite_is_idempotent(tmp_path):
    """Re-running write mode produces no diff"""
    workflow = tmp_path / "idempotent.js"
    workflow.write_text(schemas_block_source("task-classification", "commit-result"))
    original_bytes = workflow.read_bytes()
    first_result = run_generator(workflow)
    content_after_first_run = workflow.read_bytes()
    assert first_result.returncode == 0, f"first run failed: {first_result.stderr}"
    assert content_after_first_run != original_bytes, (
        "first run should populate the empty schemas block"
    )

    second_result = run_generator(workflow)

    assert second_result.returncode == 0, f"second run failed: {second_result.stderr}"
    assert workflow.read_bytes() == content_after_first_run, (
        "second write run should leave the file byte-identical"
    )


def test_outside_markers_preserved_byte_for_byte(tmp_path):
    """Text before and after the block, including the marker lines, is unchanged byte for byte"""
    workflow = tmp_path / "preserve.js"
    open_marker_line = SCHEMAS_OPEN_MARKER.format(names="task-classification") + "\r\n"
    close_marker_line = SCHEMAS_CLOSE_MARKER + "\r\n"
    before = "// header — café naïve  \r\nconst x = 1;\t \r\n"
    after = "// footer — señor   \r\nexport {};  \r\n// last line, no newline"
    stale_body = "const STALE = {};\r\n"
    original = (before + open_marker_line + stale_body + close_marker_line + after).encode("utf-8")
    prefix = (before + open_marker_line).encode("utf-8")
    suffix = (close_marker_line + after).encode("utf-8")
    workflow.write_bytes(original)

    result = run_generator(workflow)

    assert result.returncode == 0, f"generator failed: {result.stderr}"
    content = workflow.read_bytes()
    assert content != original, "the block between the markers should have been regenerated"
    assert content.startswith(prefix), (
        "bytes before the block and the opening marker line should be unchanged"
    )
    assert content.endswith(suffix), (
        "the closing marker line and the bytes after it should be unchanged"
    )


def test_script_without_schemas_block_untouched(tmp_path):
    """A file with no schemas block is not modified"""
    workflow = tmp_path / "plain.js"
    original = "// plain script — no generated blocks\r\nconst x = 1;  \r\n".encode()
    workflow.write_bytes(original)
    freeze_mtime(workflow)

    result = run_generator(workflow)

    assert result.returncode == 0, f"generator failed: {result.stderr}"
    assert_not_rewritten(workflow, original)


def test_check_passes_on_fresh_file(tmp_path):
    """--check exits 0 on a file freshly generated by write mode"""
    workflow = tmp_path / "fresh.js"
    workflow.write_text(schemas_block_source("task-classification", "commit-result"))
    write_result = run_generator(workflow)
    assert write_result.returncode == 0, f"write mode failed: {write_result.stderr}"
    generated_bytes = workflow.read_bytes()
    freeze_mtime(workflow)

    result = run_generator("--check", workflow)

    assert result.returncode == 0, f"--check should pass on a fresh file: {result.stderr}"
    assert_not_rewritten(workflow, generated_bytes)


def test_check_fails_naming_file_after_hand_edit(tmp_path):
    """After a hand-edit, --check exits non-zero, names the file, and leaves it unchanged"""
    workflow = tmp_path / "drifted.js"
    make_stale_workflow(workflow)
    edited_bytes = workflow.read_bytes()
    freeze_mtime(workflow)

    result = run_generator("--check", workflow)

    assert result.returncode != 0, "--check should exit non-zero when a block was hand-edited"
    assert str(workflow) in result.stdout + result.stderr, (
        "--check output should name the stale file"
    )
    assert_not_rewritten(workflow, edited_bytes)


def test_check_lists_every_stale_file(tmp_path):
    """With two stale files, --check names both"""
    stale_paths = [
        make_stale_workflow(tmp_path / "stale_one.js"),
        make_stale_workflow(tmp_path / "stale_two.js"),
    ]
    fresh = tmp_path / "fresh_one.js"
    generate_workflow(fresh, "task-classification")
    all_paths = [stale_paths[0], fresh, stale_paths[1]]
    original_bytes = {path: path.read_bytes() for path in all_paths}
    for path in all_paths:
        freeze_mtime(path)

    result = run_generator("--check", *all_paths)

    output = result.stdout + result.stderr
    assert result.returncode != 0, "--check should exit non-zero when files are stale"
    for stale in stale_paths:
        assert str(stale) in output, f"--check output should name stale file {stale.name}"
    assert str(fresh) not in output, "--check output should not name the fresh file"
    for path in all_paths:
        assert_not_rewritten(path, original_bytes[path])


def test_unknown_schema_name_fails_naming_it(tmp_path):
    """An unknown schema name fails with a message that names it"""
    schemas_dir = tmp_path / "schemas"
    schemas_dir.mkdir()
    workflow = tmp_path / "unknown.js"
    workflow.write_text(schemas_block_source("no-such-schema"))
    original_bytes = workflow.read_bytes()
    freeze_mtime(workflow)

    result = run_generator("--schemas-dir", schemas_dir, workflow)

    output = result.stdout + result.stderr
    assert result.returncode != 0, "an unknown schema name should exit non-zero"
    assert "no-such-schema" in output, "error message should name the unknown schema"
    assert_no_traceback(result)
    assert_not_rewritten(workflow, original_bytes)


UNBALANCED_MARKER_SOURCES = {
    "open-without-close": (
        "// header\n"
        + SCHEMAS_OPEN_MARKER.format(names="task-classification")
        + "\nconst STALE = {};\n// footer\n"
    ),
    "close-without-open": "// header\nconst STALE = {};\n" + SCHEMAS_CLOSE_MARKER + "\n// footer\n",
}


@pytest.mark.parametrize(
    "source", UNBALANCED_MARKER_SOURCES.values(), ids=UNBALANCED_MARKER_SOURCES
)
def test_missing_or_unbalanced_marker_fails_clearly(tmp_path, source):
    """An unbalanced marker fails clearly with the schema-error exit code"""
    workflow = tmp_path / "unbalanced.js"
    workflow.write_text(source)
    original_bytes = workflow.read_bytes()
    freeze_mtime(workflow)

    result = run_generator(workflow)

    output = result.stdout + result.stderr
    assert result.returncode == EXIT_ERROR, (
        f"an unbalanced marker should exit with {EXIT_ERROR}, stderr: {result.stderr}"
    )
    assert "marker" in output.lower(), "error message should mention the schemas markers"
    assert str(workflow) in output, "error message should identify the file"
    assert_no_traceback(result)
    assert_not_rewritten(workflow, original_bytes)


def test_explicit_paths_processed_only(tmp_path):
    """Only the paths passed on the command line are processed; other files are untouched"""
    selected = tmp_path / "selected.js"
    other = tmp_path / "other.js"
    selected.write_text(schemas_block_source("task-classification"))
    other.write_text(schemas_block_source("task-classification"))
    other_original = other.read_bytes()
    freeze_mtime(other)

    result = run_generator(selected)

    assert result.returncode == 0, f"generator failed: {result.stderr}"
    constants = parse_constants(extract_schemas_block(selected.read_text()))
    assert "TASK_CLASSIFICATION" in constants, "the selected file should be generated"
    assert_not_rewritten(other, other_original)


@pytest.mark.parametrize("check_mode", [False, True], ids=["write", "check"])
def test_schema_error_does_not_abort_other_files(tmp_path, check_mode):
    """With several paths where one declares an unknown schema, the run is not aborted (exit 2)

    Every error gets an 'error:' line, every stale file is still listed in --check,
    good files are still written in write mode, and the failing file is untouched.
    """
    bad = tmp_path / "bad.js"
    bad.write_text(schemas_block_source("no-such-schema"))
    bad_bytes = bad.read_bytes()
    freeze_mtime(bad)

    mixed = run_with_bad_target(tmp_path, bad, check_mode)

    errors = error_lines(mixed.result)
    assert mixed.result.returncode == EXIT_ERROR, (
        f"a schema error should win with exit {EXIT_ERROR}, stderr: {mixed.result.stderr}"
    )
    assert len(errors) == 1, f"expected exactly one error line, got: {errors}"
    assert "no-such-schema" in errors[0], "error line should name the unknown schema"
    assert_no_traceback(mixed.result)
    assert_not_rewritten(bad, bad_bytes)
    assert_good_files_outcome(mixed, check_mode, "the error in another file")


BAD_SCHEMA_CONTENTS = {
    "invalid-json": b"{ not valid json",
    "not-an-object": b'["a", "list", "not", "an", "object"]',
    "invalid-utf8": b"\xff\xfe\x00 not utf-8 \x80\x81",
}


@pytest.mark.parametrize("schema_bytes", BAD_SCHEMA_CONTENTS.values(), ids=BAD_SCHEMA_CONTENTS)
def test_invalid_schema_json_fails_clearly(tmp_path, schema_bytes):
    """A bad schema file (invalid JSON, non-object, non-UTF-8) fails like an unknown schema"""
    schemas_dir = tmp_path / "schemas"
    schemas_dir.mkdir()
    schema_file = schemas_dir / "bad-schema.schema.json"
    schema_file.write_bytes(schema_bytes)
    workflow = tmp_path / "bad_schema.js"
    workflow.write_text(schemas_block_source("bad-schema"))
    original_bytes = workflow.read_bytes()
    freeze_mtime(workflow)

    result = run_generator("--schemas-dir", schemas_dir, workflow)

    errors = error_lines(result)
    assert result.returncode == EXIT_ERROR, (
        f"a bad schema file should exit with {EXIT_ERROR}, stderr: {result.stderr}"
    )
    assert len(errors) == 1, f"expected exactly one error line, got: {errors}"
    assert "bad-schema" in errors[0], "error line should name the schema file or name"
    assert_no_traceback(result)
    assert_not_rewritten(workflow, original_bytes)


TASK_CLASSIFICATION_OPEN_MARKER = SCHEMAS_OPEN_MARKER.format(names="task-classification")
# id -> (source, line the generator should report)
MISORDERED_MARKER_SOURCES = {
    "close-then-open": (
        f"// header\n{SCHEMAS_CLOSE_MARKER}\n{TASK_CLASSIFICATION_OPEN_MARKER}\n"
        "const STALE = {};\n// footer\n",
        2,
    ),
    "nested-open-open-close-close": (
        f"// header\n{TASK_CLASSIFICATION_OPEN_MARKER}\n{TASK_CLASSIFICATION_OPEN_MARKER}\n"
        f"{SCHEMAS_CLOSE_MARKER}\n{SCHEMAS_CLOSE_MARKER}\n",
        3,
    ),
    "open-close-close-open": (
        f"// header\n{TASK_CLASSIFICATION_OPEN_MARKER}\n{SCHEMAS_CLOSE_MARKER}\n"
        f"{SCHEMAS_CLOSE_MARKER}\n{TASK_CLASSIFICATION_OPEN_MARKER}\n",
        4,
    ),
    "close-then-open-at-eof-without-newline": (
        f"// header\n{SCHEMAS_CLOSE_MARKER}\n{TASK_CLASSIFICATION_OPEN_MARKER}",
        2,
    ),
    "lone-open-at-eof-without-newline": (f"x\n{TASK_CLASSIFICATION_OPEN_MARKER}", 2),
}


@pytest.mark.parametrize("check_mode", [False, True], ids=["write", "check"])
@pytest.mark.parametrize(
    "source, expected_line", MISORDERED_MARKER_SOURCES.values(), ids=MISORDERED_MARKER_SOURCES
)
def test_misordered_markers_fail_clearly(tmp_path, source, expected_line, check_mode):
    """Misordered markers with equal open/close counts fail with a clear error and exit 2"""
    workflow = tmp_path / "workflow.js"
    workflow.write_text(source)
    original_bytes = workflow.read_bytes()
    freeze_mtime(workflow)
    args = ["--check", workflow] if check_mode else [workflow]

    result = run_generator(*args)

    errors = error_lines(result)
    assert result.returncode == EXIT_ERROR, (
        f"misordered markers should exit with {EXIT_ERROR}, stderr: {result.stderr}"
    )
    assert len(errors) == 1, f"expected exactly one error line, got: {errors}"
    assert "marker" in errors[0].lower(), "error line should mention the markers"
    assert str(workflow) in errors[0], "error line should identify the file"
    assert re.search(rf"at line {expected_line}\b", errors[0]), (
        f"error line should name line {expected_line}, got: {errors[0]}"
    )
    assert_no_traceback(result)
    assert_not_rewritten(workflow, original_bytes)


def make_missing_target(path):
    """Leave `path` missing; return a verifier that it was not created."""

    def assert_untouched():
        assert not path.exists(), "a missing target should not be created"

    return assert_untouched


def make_invalid_utf8_target(path):
    """Write a non-UTF-8 `path`; return a verifier that it was not rewritten."""
    path.write_bytes(b"// header\n\xff\xfe not utf-8 \x80\n")
    freeze_mtime(path)
    original_bytes = path.read_bytes()

    def assert_untouched():
        assert_not_rewritten(path, original_bytes)

    return assert_untouched


UNREADABLE_TARGET_MAKERS = {
    "missing-file": make_missing_target,
    "invalid-utf8": make_invalid_utf8_target,
}


@pytest.mark.parametrize("check_mode", [False, True], ids=["write", "check"])
@pytest.mark.parametrize(
    "make_unreadable", UNREADABLE_TARGET_MAKERS.values(), ids=UNREADABLE_TARGET_MAKERS
)
def test_unreadable_target_fails_clearly(tmp_path, make_unreadable, check_mode):
    """A missing or non-UTF-8 target exits 2 with one clear error; other paths still run"""
    bad = tmp_path / "bad.js"
    assert_bad_untouched = make_unreadable(bad)

    mixed = run_with_bad_target(tmp_path, bad, check_mode)

    errors = error_lines(mixed.result)
    assert mixed.result.returncode == EXIT_ERROR, (
        f"an unreadable target should exit with {EXIT_ERROR}, stderr: {mixed.result.stderr}"
    )
    assert len(errors) == 1, f"expected exactly one error line, got: {errors}"
    prefix = f"error: cannot read {bad}: "
    assert errors[0].startswith(prefix), (
        f"error line should say 'cannot read <path>: <reason>', got: {errors[0]}"
    )
    assert errors[0].removeprefix(prefix).strip(), (
        f"error line should give a reason, got: {errors[0]}"
    )
    assert_no_traceback(mixed.result)
    assert_good_files_outcome(mixed, check_mode, "the unreadable target")
    assert_bad_untouched()


@pytest.fixture
def read_only_stale_target(tmp_path):
    """A stale but valid workflow made read-only; permissions are restored on teardown."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root ignores file modes, so the target cannot be made unwritable")
    path = make_stale_workflow(tmp_path / "read_only.js")
    freeze_mtime(path)
    path.chmod(0o444)
    yield path
    path.chmod(0o644)


def test_unwritable_target_fails_clearly(tmp_path, read_only_stale_target):
    """Write mode on a read-only target prints 'error: cannot write <path>: <reason>',
    exits 2, still writes the good files, and leaves the target unchanged.
    """
    original_bytes = read_only_stale_target.read_bytes()

    mixed = run_with_bad_target(tmp_path, read_only_stale_target, check_mode=False)

    errors = error_lines(mixed.result)
    assert mixed.result.returncode == EXIT_ERROR, (
        f"an unwritable target should exit with {EXIT_ERROR}, stderr: {mixed.result.stderr}"
    )
    assert len(errors) == 1, f"expected exactly one error line, got: {errors}"
    prefix = f"error: cannot write {read_only_stale_target}: "
    assert errors[0].startswith(prefix), (
        f"error line should say 'cannot write <path>: <reason>', got: {errors[0]}"
    )
    assert errors[0].removeprefix(prefix).strip(), (
        f"error line should give a reason, got: {errors[0]}"
    )
    assert_no_traceback(mixed.result)
    assert_good_files_outcome(mixed, False, "the unwritable target")
    assert_not_rewritten(read_only_stale_target, original_bytes)


@pytest.mark.parametrize("check_mode", [False, True], ids=["write", "check"])
def test_no_paths_processes_workflows_dir(tmp_path, check_mode):
    """With no paths, processes every *.js file in --workflows-dir and ignores other files."""
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir()
    empty_js = workflows_dir / "empty.js"
    empty_js.write_text(schemas_block_source("task-classification"))
    stale_js = make_stale_workflow(workflows_dir / "stale.js")
    non_js = workflows_dir / "notes.txt"
    non_js.write_text(schemas_block_source("task-classification"))
    fresh_bytes = generate_workflow(tmp_path / "reference.js", "task-classification")
    js_paths = [empty_js, stale_js]
    original_bytes = {path: path.read_bytes() for path in [*js_paths, non_js]}
    for path in [*js_paths, non_js]:
        freeze_mtime(path)
    args = ["--check"] if check_mode else []
    expected_exit = EXIT_STALE if check_mode else EXIT_OK
    expected_stale_lines = [f"stale: {path}" for path in js_paths] if check_mode else []
    expected_js_bytes = {
        path: original_bytes[path] if check_mode else fresh_bytes for path in js_paths
    }

    result = run_generator(*args, "--workflows-dir", workflows_dir)

    stale_lines = [line for line in result.stderr.splitlines() if line.startswith("stale:")]
    assert result.returncode == expected_exit, (
        f"expected exit {expected_exit}, stderr: {result.stderr}"
    )
    assert stale_lines == expected_stale_lines, (
        "stale lines should list exactly the .js files in --check and none in write mode"
    )
    for path in js_paths:
        assert path.read_bytes() == expected_js_bytes[path], (
            f"{path.name} should be {'left as is' if check_mode else 'generated'}"
        )
    assert_not_rewritten(non_js, original_bytes[non_js])


def make_missing_workflows_dir(path):
    """Leave `path` missing; return a verifier that it was not created."""

    def assert_untouched():
        assert not path.exists(), "nothing should be created at the missing location"

    return assert_untouched


def make_file_as_workflows_dir(path):
    """Write a plain file at `path`; return a verifier that it was not rewritten."""
    path.write_text("just a file\n")
    freeze_mtime(path)
    original_bytes = path.read_bytes()

    def assert_untouched():
        assert_not_rewritten(path, original_bytes)

    return assert_untouched


BAD_WORKFLOWS_DIR_CASES = {
    "missing": (make_missing_workflows_dir, "error: workflows directory not found: {dir}"),
    "file": (make_file_as_workflows_dir, "error: --workflows-dir is not a directory: {dir}"),
}


@pytest.mark.parametrize("check_mode", [False, True], ids=["write", "check"])
@pytest.mark.parametrize(
    "make_bad_dir, expected_error",
    BAD_WORKFLOWS_DIR_CASES.values(),
    ids=BAD_WORKFLOWS_DIR_CASES,
)
def test_missing_workflows_dir_fails_clearly(tmp_path, check_mode, make_bad_dir, expected_error):
    """A --workflows-dir that does not exist or is a file fails with one clear error, exit 2."""
    bad_dir = tmp_path / "not_a_workflows_dir"
    assert_bad_untouched = make_bad_dir(bad_dir)
    args = ["--check"] if check_mode else []

    result = run_generator(*args, "--workflows-dir", bad_dir)

    assert result.returncode == EXIT_ERROR, (
        f"expected exit {EXIT_ERROR}, got {result.returncode}, stderr: {result.stderr}"
    )
    assert error_lines(result) == [expected_error.format(dir=bad_dir)], (
        "stderr should carry exactly the expected error line"
    )
    assert_no_traceback(result)
    assert_bad_untouched()
