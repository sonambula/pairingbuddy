"""Fill the generated blocks of workflow scripts from contracts/.

Usage: uv run python scripts/generate_workflow_blocks.py [--schemas-dir DIR] [--workflows-dir DIR]
       [--check] [paths...]

When no paths are given, every *.js file (sorted) in <workflows-dir> (default: workflows/) is
processed; explicit paths are used as given and --workflows-dir is ignored. When no paths are
given and <workflows-dir> is missing, `error: workflows directory not found: <dir>` is printed
to stderr; if it is not a directory, `error: --workflows-dir is not a directory: <dir>` is
printed instead. The exit code is 2 either way.

Schemas block: the opening marker line declares schema names
(`// <generated:schemas a-b> ...`); each name is loaded from
<schemas-dir>/<name>.schema.json (default: contracts/schemas) and emitted as `const A_B = <json>;`.
The JSON-Schema meta keywords `$schema` and `$id` are stripped.

With --check nothing is written: each out-of-date file is reported as `stale: <path>` on
stderr and the exit code is 1 (0 when every block is up to date).

A schema error (an unknown schema name, an unreadable or invalid schema file, or misordered,
nested or unclosed schemas markers), an unreadable target (`error: cannot read <path>: <reason>`)
or an unwritable target (`error: cannot write <path>: <reason>`)
prints `error: ...` to stderr for that file only: the remaining paths are still processed
(good files are written, stale files still reported).
The exit code is 2 if any file had an error (schema error, unreadable or unwritable target),
else 1 if any is stale (--check), else 0.
"""

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMAS_DIR = REPO_ROOT / "contracts" / "schemas"
DEFAULT_WORKFLOWS_DIR = REPO_ROOT / "workflows"
EXIT_OK = 0
EXIT_STALE = 1
EXIT_ERROR = 2
META_KEYWORDS = ("$schema", "$id")
OPEN_MARKER = r"// <generated:schemas (?P<names>[^>]*)>"
CLOSE_MARKER = r"// </generated:schemas>"
BLOCK = re.compile(
    rf"(?P<open>{OPEN_MARKER}[^\n]*\n)(?:.*?)(?P<close>{CLOSE_MARKER})",
    re.DOTALL,
)
MARKER = re.compile(rf"(?P<open>{OPEN_MARKER}[^\n]*(?:\n|\Z))|(?P<close>{CLOSE_MARKER})")


class GenerationError(Exception):
    """A problem with one target file; it is reported and the remaining paths still run."""

    location = "in"

    def describe(self, path: Path) -> str:
        return f"{self} ({self.location} {path})"


class TargetError(GenerationError):
    """The target file itself could not be accessed; its message already names the path."""

    verb: str

    def __init__(self, path: Path, reason: str):
        super().__init__(f"cannot {self.verb} {path}: {reason}")

    def describe(self, path: Path) -> str:
        return str(self)


class UnreadableTargetError(TargetError):
    verb = "read"


class UnwritableTargetError(TargetError):
    verb = "write"


class SchemaError(GenerationError):
    """A problem with a file's schemas block."""


class MarkerError(SchemaError):
    def __init__(self, problem: str, line: int):
        super().__init__(f"invalid schemas markers ({problem} at line {line})")


class UnknownSchemaError(SchemaError):
    location = "declared in"

    def __init__(self, name: str, schema_path: Path):
        super().__init__(f"unknown schema '{name}' (no {schema_path.name} in {schema_path.parent})")


class InvalidSchemaError(SchemaError):
    location = "declared in"

    def __init__(self, name: str, schema_path: Path, reason: str):
        super().__init__(f"invalid schema '{name}' ({schema_path}): {reason}")


class UnreadableSchemaError(SchemaError):
    location = "declared in"

    def __init__(self, name: str, schema_path: Path, reason: str):
        super().__init__(f"unreadable schema '{name}' ({schema_path}): {reason}")


def os_reason(error: OSError) -> str:
    return error.strerror or str(error)


def load_schema(name: str, schema_path: Path) -> dict:
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise UnreadableSchemaError(name, schema_path, os_reason(error)) from error
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise InvalidSchemaError(name, schema_path, str(error)) from error
    if not isinstance(schema, dict):
        raise InvalidSchemaError(
            name, schema_path, f"expected a JSON object, got {type(schema).__name__}"
        )
    return schema


def constant_name(name: str) -> str:
    return name.upper().replace("-", "_")


def render_constant(name: str, schemas_dir: Path) -> str:
    schema_path = schemas_dir / f"{name}.schema.json"
    if not schema_path.is_file():
        raise UnknownSchemaError(name, schema_path)
    schema = load_schema(name, schema_path)
    for keyword in META_KEYWORDS:
        schema.pop(keyword, None)
    return f"const {constant_name(name)} = {json.dumps(schema, indent=2)};\n"


def check_markers_ordered(text: str) -> None:
    pending_line = None
    for marker in MARKER.finditer(text):
        line = text.count("\n", 0, marker.start()) + 1
        if marker["close"]:
            if pending_line is None:
                raise MarkerError("closing marker without an opening one", line)
            pending_line = None
        elif pending_line is not None:
            raise MarkerError(
                f"opening marker while the one at line {pending_line} is still open", line
            )
        else:
            pending_line = line
    if pending_line is not None:
        raise MarkerError("opening marker never closed", pending_line)


def render_block(match: re.Match, schemas_dir: Path) -> str:
    names = match["names"].split()
    body = "".join(render_constant(name, schemas_dir) for name in names)
    return match["open"] + body + match["close"]


def process_path(path: Path, schemas_dir: Path, check: bool) -> bool:
    """Regenerate one file's blocks; return True when it is stale (--check mode only).

    Raises GenerationError for any problem with that file; main reports it and moves on.
    """
    try:
        with path.open(encoding="utf-8", newline="") as src:
            text = src.read()
    except OSError as error:
        raise UnreadableTargetError(path, os_reason(error)) from error
    except UnicodeDecodeError as error:
        raise UnreadableTargetError(path, f"not valid UTF-8 ({error.reason})") from error
    check_markers_ordered(text)
    rendered = BLOCK.sub(lambda m: render_block(m, schemas_dir), text)
    if rendered == text:
        return False
    if check:
        return True
    try:
        with path.open("w", encoding="utf-8", newline="") as out:
            out.write(rendered)
    except OSError as error:
        raise UnwritableTargetError(path, os_reason(error)) from error
    return False


def workflows_dir_problem(workflows_dir: Path) -> str | None:
    if not workflows_dir.exists():
        return f"workflows directory not found: {workflows_dir}"
    if not workflows_dir.is_dir():
        return f"--workflows-dir is not a directory: {workflows_dir}"
    return None


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog=(
            "An error (schema problem, unreadable or unwritable target, missing workflows "
            "directory) prints `error: ...` "
            "on stderr; "
            "other paths are still processed. "
            "Exit 2 on any error, else 1 if any file is stale (--check), else 0."
        ),
    )
    parser.add_argument(
        "--schemas-dir",
        type=Path,
        default=DEFAULT_SCHEMAS_DIR,
        metavar="DIR",
        help="directory containing <name>.schema.json files (default: contracts/schemas)",
    )
    parser.add_argument(
        "--workflows-dir",
        type=Path,
        default=DEFAULT_WORKFLOWS_DIR,
        metavar="DIR",
        help=(
            "directory whose *.js files are processed when no paths are given (default: workflows/)"
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "write nothing; print `stale: <path>` to stderr for each out-of-date file "
            "and exit 1 if any"
        ),
    )
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args(argv)
    if not args.paths:
        problem = workflows_dir_problem(args.workflows_dir)
        if problem:
            print(f"error: {problem}", file=sys.stderr)
            return EXIT_ERROR
    paths = args.paths or sorted(args.workflows_dir.glob("*.js"))
    stale = []
    errors = 0
    for path in paths:
        try:
            if process_path(path, args.schemas_dir, args.check):
                stale.append(path)
        except GenerationError as error:
            print(f"error: {error.describe(path)}", file=sys.stderr)
            errors += 1
    for path in stale:
        print(f"stale: {path}", file=sys.stderr)
    if errors:
        return EXIT_ERROR
    return EXIT_STALE if stale else EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
