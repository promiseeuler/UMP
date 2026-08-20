from __future__ import annotations

from pathlib import Path
import re
import subprocess
from typing import Any


PROFILE = "ump.public-readiness/v1"
MAX_PUBLIC_BLOB_BYTES = 5 * 1024 * 1024
REQUIRED_PUBLIC_FILES = (
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "SECURITY.md",
)
SECRET_PATTERNS = {
    "private_key": re.compile(
        rb"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"
    ),
    "github_token": re.compile(rb"(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    "aws_access_key": re.compile(rb"AKIA[0-9A-Z]{16}"),
    "google_api_key": re.compile(rb"AIza[0-9A-Za-z_-]{30,}"),
    "slack_token": re.compile(rb"xox[baprs]-[A-Za-z0-9-]{10,}"),
}
SENSITIVE_PATH = re.compile(
    r"(^|/)(?:\.env(?:\..*)?|.*\.(?:pem|key|p12|pfx|jks|keystore)|"
    r"id_(?:rsa|ed25519)|\.npmrc|\.pypirc|netrc)$",
    re.IGNORECASE,
)


class PublicReadinessError(ValueError):
    pass


def _git(root: Path, *arguments: str) -> bytes:
    try:
        return subprocess.run(
            ["git", *arguments],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise PublicReadinessError(f"Git audit command failed: {' '.join(arguments)}") from error


def _git_input(root: Path, input_data: bytes, *arguments: str) -> bytes:
    try:
        return subprocess.run(
            ["git", *arguments],
            cwd=root,
            check=True,
            input=input_data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise PublicReadinessError(
            f"Git audit command failed: {' '.join(arguments)}"
        ) from error


def _objects(root: Path) -> tuple[tuple[str, str], ...]:
    rows = []
    for line in _git(root, "rev-list", "--objects", "--all").decode(
        "utf-8", errors="replace"
    ).splitlines():
        object_id, separator, path = line.partition(" ")
        if separator and path:
            rows.append((object_id, path))
    return tuple(rows)


def audit_public_release(
    project_root: str | Path,
    *,
    accept_historical_tree: bool = False,
) -> dict[str, Any]:
    """Audit high-confidence public-release risks across reachable Git history."""
    root = Path(project_root).resolve()
    if not (root / ".git").exists():
        raise PublicReadinessError("project root is not a Git repository")

    objects = _objects(root)
    historical_paths = {path for _, path in objects}
    current_paths = set(
        _git(root, "ls-files").decode("utf-8", errors="replace").splitlines()
    )
    sensitive_paths = sorted(path for path in historical_paths if SENSITIVE_PATH.search(path))

    patches = _git(root, "log", "--all", "-p", "--format=")
    secret_matches = {
        name: len(pattern.findall(patches))
        for name, pattern in SECRET_PATTERNS.items()
        if pattern.search(patches)
    }

    object_paths = {object_id: path for object_id, path in objects}
    object_input = "".join(f"{object_id}\n" for object_id in object_paths).encode()
    object_rows = _git_input(
        root,
        object_input,
        "cat-file",
        "--batch-check=%(objectname) %(objecttype) %(objectsize)",
    ).decode("ascii").splitlines()
    large_blobs = []
    for row in object_rows:
        object_id, object_type, size_text = row.split()
        if object_type != "blob":
            continue
        try:
            size = int(size_text)
        except ValueError as error:
            raise PublicReadinessError("Git object size is invalid") from error
        if size > MAX_PUBLIC_BLOB_BYTES:
            large_blobs.append(
                {"path": object_paths.get(object_id, ""), "size_bytes": size}
            )

    authors = sorted(
        set(
            _git(root, "log", "--all", "--format=%an <%ae>")
            .decode("utf-8", errors="replace")
            .splitlines()
        )
    )
    personal_author_emails = sorted(
        author
        for author in authors
        if "@" in author and "users.noreply.github.com" not in author.casefold()
    )
    current_roots = {path.split("/", 1)[0] for path in current_paths}
    historical_roots = {path.split("/", 1)[0] for path in historical_paths}
    historical_only_roots = sorted(historical_roots - current_roots)
    missing_files = [path for path in REQUIRED_PUBLIC_FILES if not (root / path).is_file()]
    dirty = bool(_git(root, "status", "--porcelain").strip())

    checks = {
        "no_high_confidence_secrets": not secret_matches,
        "no_sensitive_file_names": not sensitive_paths,
        "no_oversized_historical_blobs": not large_blobs,
        "author_emails_are_private": not personal_author_emails,
        "governance_files_present": not missing_files,
        "worktree_clean": not dirty,
        "historical_tree_reviewed": not historical_only_roots or accept_historical_tree,
    }
    return {
        "profile": PROFILE,
        "ready": all(checks.values()),
        "checks": checks,
        "findings": {
            "secret_match_counts": secret_matches,
            "sensitive_paths": sensitive_paths,
            "large_blobs": large_blobs,
            "personal_author_identities": personal_author_emails,
            "missing_governance_files": missing_files,
            "historical_only_roots": historical_only_roots,
        },
        "limits": {"maximum_blob_bytes": MAX_PUBLIC_BLOB_BYTES},
        "manual_requirements": [
            "Run an independent maintained secret scanner against full Git history.",
            "Review historical content, author identities, issues, and Actions artifacts.",
            "Enable GitHub secret scanning and branch protection after publication.",
        ],
    }
