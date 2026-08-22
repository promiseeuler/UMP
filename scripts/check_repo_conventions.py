from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).parents[1]
COMMIT_PATTERN = re.compile(
    r"^(feat|fix|docs|test|refactor|perf|build|ci|chore|revert)"
    r"(?:\([a-z0-9][a-z0-9-]*\))?!?: [a-z0-9].+"
)
SCHEMA_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*-v[0-9]+\.schema\.json$")
DOC_PATTERN = re.compile(r"^[A-Z0-9]+(?:_[A-Z0-9]+)*\.md$")
PYTHON_PATTERN = re.compile(r"^[a-z][a-z0-9_]*\.py$")


def tracked_files() -> list[Path]:
    output = subprocess.check_output(
        ["git", "ls-files"], cwd=ROOT, text=True
    )
    return [Path(line) for line in output.splitlines() if line]


def filename_errors(paths: list[Path]) -> list[str]:
    errors: list[str] = []
    for path in paths:
        name = path.name
        if " " in name:
            errors.append(f"filename contains spaces: {path}")
        if path.parts[:1] == ("schemas",) and not SCHEMA_PATTERN.fullmatch(name):
            errors.append(f"schema filename is not versioned kebab-case: {path}")
        if path.parts[:1] == ("docs",) and not DOC_PATTERN.fullmatch(name):
            errors.append(f"documentation filename is not uppercase snake-case: {path}")
        if (
            path.suffix == ".py"
            and name != "__init__.py"
            and not PYTHON_PATTERN.fullmatch(name)
        ):
            errors.append(f"Python filename is not snake_case: {path}")
        if path.parts[:1] == ("tests",) and path.suffix == ".py" and not name.startswith(
            "test_"
        ):
            errors.append(f"test filename must start with test_: {path}")
    return errors


def commit_error(subject: str) -> str | None:
    if COMMIT_PATTERN.fullmatch(subject):
        return None
    return f"commit subject is not Conventional Commits compliant: {subject!r}"


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    errors = filename_errors(tracked_files())
    if arguments:
        error = commit_error(arguments[0])
        if error:
            errors.append(error)
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
