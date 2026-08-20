"""Validation for integrity-bound UMP release qualification evidence."""

from __future__ import annotations

from hashlib import sha256
from importlib.resources import files
import json
from pathlib import Path
import re
from typing import Any

from jsonschema import Draft202012Validator


class ReleaseEvidenceValidationError(ValueError):
    pass


def release_evidence_schema() -> dict[str, Any]:
    resource = files("ump").joinpath("release_evidence_data/v1/schema.json")
    return json.loads(resource.read_text(encoding="utf-8"))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ReleaseEvidenceValidationError(message)


def _read_json(path: Path, description: str) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReleaseEvidenceValidationError(
            f"{description} cannot be read: {error}"
        ) from error
    if not isinstance(document, dict):
        raise ReleaseEvidenceValidationError(f"{description} must be a JSON object")
    return document


def _digest(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact(base: Path, evidence: dict[str, Any], description: str) -> Path:
    path = (base / evidence["artifact"]).resolve()
    if not path.is_relative_to(base):
        raise ReleaseEvidenceValidationError(f"{description} escapes the evidence bundle")
    if not path.is_file():
        raise ReleaseEvidenceValidationError(f"{description} does not exist")
    _require(_digest(path) == evidence["sha256"], f"{description} digest does not match")
    _require(path.stat().st_size == evidence["size_bytes"], f"{description} size does not match")
    return path


def _checksums(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ReleaseEvidenceValidationError(f"checksum file cannot be read: {error}") from error
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})  ([^/\\]+)", line)
        if match is None or match.group(2) in entries:
            raise ReleaseEvidenceValidationError("checksum file has an invalid or duplicate entry")
        entries[match.group(2)] = match.group(1)
    _require(bool(entries), "checksum file is empty")
    return entries


def validate_release_evidence_bundle(manifest_path: str | Path) -> dict[str, Any]:
    """Validate retained artifact, install, checksum, and provenance bindings."""
    path = Path(manifest_path)
    document = _read_json(path, "release evidence manifest")
    errors = sorted(
        Draft202012Validator(release_evidence_schema()).iter_errors(document),
        key=lambda item: list(item.path),
    )
    if errors:
        error = errors[0]
        location = ".".join(str(item) for item in error.absolute_path) or "document"
        raise ReleaseEvidenceValidationError(f"{location}: {error.message}")

    version = document["package_version"]
    _require(document["tag"] == f"v{version}", "release tag does not match package version")
    base = path.resolve().parent
    evidence = document["artifacts"]
    names = [item["artifact"] for item in evidence.values()]
    _require(len(names) == len(set(names)), "release evidence artifacts must be distinct")
    resolved = {
        role: _artifact(base, item, role.replace("_", " "))
        for role, item in evidence.items()
    }

    distribution_prefix = f"universal_machine_protocol-{version}"
    wheel = resolved["wheel"]
    source = resolved["source_distribution"]
    _require(
        wheel.name == f"{distribution_prefix}-py3-none-any.whl",
        "wheel filename does not match the package version",
    )
    _require(
        source.name == f"{distribution_prefix}.tar.gz",
        "source distribution filename does not match the package version",
    )
    checksum_entries = _checksums(resolved["checksums"])
    for artifact in (wheel, source):
        _require(
            checksum_entries.get(artifact.name) == _digest(artifact),
            f"checksum file does not bind {artifact.name}",
        )

    install = _read_json(resolved["install_report"], "install report")
    _require(install.get("profile") == "ump.release-install-report/v1", "unsupported install report profile")
    for field in ("release_id", "package_version", "repository_revision"):
        _require(install.get(field) == document[field], f"install report {field} does not match")
    checks = install.get("checks")
    expected_checks = {"wheel_installed", "import_smoke", "demo", "schema_commands"}
    _require(isinstance(checks, dict) and set(checks) == expected_checks, "install report check set is incomplete")
    _require(install.get("passed") is True and all(value is True for value in checks.values()), "isolated install checks did not all pass")

    provenance = _read_json(resolved["provenance_verification"], "provenance verification receipt")
    _require(provenance.get("profile") == "ump.github-attestation-verification/v1", "unsupported provenance verification profile")
    _require(provenance.get("passed") is True, "provenance verification did not pass")
    for field in ("repository", "repository_revision"):
        _require(provenance.get(field) == document[field], f"provenance {field} does not match")
    subjects = provenance.get("subjects")
    _require(isinstance(subjects, list), "provenance subjects must be an array")
    indexed = {
        subject.get("filename"): subject.get("sha256")
        for subject in subjects
        if isinstance(subject, dict)
    }
    _require(len(indexed) == len(subjects), "provenance subjects are invalid or duplicated")
    for artifact in (wheel, source):
        _require(indexed.get(artifact.name) == _digest(artifact), f"provenance does not bind {artifact.name}")

    return {
        "valid": True,
        "validation_scope": "retained_artifact_integrity_and_release_receipt_consistency",
        "release_id": document["release_id"],
        "repository": document["repository"],
        "tag": document["tag"],
        "package_version": version,
        "repository_revision": document["repository_revision"],
        "artifacts_verified": len(resolved),
        "install_checks_verified": len(expected_checks),
        "provenance_subjects_verified": 2,
    }
