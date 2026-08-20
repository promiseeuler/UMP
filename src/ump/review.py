"""Validation for integrity-bound independent UMP review bundles."""

from __future__ import annotations

from hashlib import sha256
from importlib.resources import files
import json
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator


class ReviewValidationError(ValueError):
    pass


def review_schema() -> dict[str, Any]:
    resource = files("ump").joinpath("review_data/v1/schema.json")
    return json.loads(resource.read_text(encoding="utf-8"))


def _evidence_items(document: dict[str, Any]) -> Iterable[dict[str, str]]:
    yield document["report_evidence"]
    yield document["independence_attestation"]
    yield from document.get("supporting_evidence", ())
    for finding in document["findings"]:
        disposition = finding.get("disposition_evidence")
        if disposition is not None:
            yield disposition


def _digest(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_review_bundle(manifest_path: str | Path) -> dict[str, Any]:
    """Validate review structure, finding disposition, and artifact integrity."""
    path = Path(manifest_path)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReviewValidationError(f"review manifest cannot be read: {error}") from error
    if not isinstance(document, dict):
        raise ReviewValidationError("review manifest must contain a JSON object")
    errors = sorted(
        Draft202012Validator(review_schema()).iter_errors(document),
        key=lambda item: list(item.path),
    )
    if errors:
        error = errors[0]
        location = ".".join(str(item) for item in error.absolute_path) or "document"
        raise ReviewValidationError(f"{location}: {error.message}")

    finding_ids = [finding["finding_id"] for finding in document["findings"]]
    if len(finding_ids) != len(set(finding_ids)):
        raise ReviewValidationError("finding IDs must be unique")
    open_findings = [
        finding for finding in document["findings"] if finding["status"] == "open"
    ]
    blocking_open = [
        finding
        for finding in open_findings
        if finding["severity"] in {"critical", "high"}
    ]
    if blocking_open:
        raise ReviewValidationError(
            "critical or high findings must be resolved or explicitly accepted"
        )
    accepted_without_evidence = [
        finding["finding_id"]
        for finding in document["findings"]
        if finding["status"] == "accepted"
        and "disposition_evidence" not in finding
    ]
    if accepted_without_evidence:
        raise ReviewValidationError(
            f"accepted findings lack disposition evidence: {accepted_without_evidence}"
        )
    conclusion = document["conclusion"]
    if conclusion == "approved" and open_findings:
        raise ReviewValidationError("approved review cannot contain open findings")

    base = path.resolve().parent
    evidence = tuple(_evidence_items(document))
    artifact_names = [item["artifact"] for item in evidence]
    if len(artifact_names) != len(set(artifact_names)):
        raise ReviewValidationError("review evidence artifacts must be distinct")
    for item in evidence:
        artifact = (base / item["artifact"]).resolve()
        if not artifact.is_relative_to(base):
            raise ReviewValidationError("review evidence escapes the bundle")
        if not artifact.is_file():
            raise ReviewValidationError(
                f"review evidence does not exist: {item['artifact']}"
            )
        if _digest(artifact) != item["sha256"]:
            raise ReviewValidationError(
                f"review evidence digest does not match: {item['artifact']}"
            )

    severity_counts = {
        severity: sum(
            finding["severity"] == severity for finding in document["findings"]
        )
        for severity in ("critical", "high", "medium", "low", "informational")
    }
    passed = conclusion in {"approved", "approved_with_conditions"}
    return {
        "valid": True,
        "passed": passed,
        "validation_scope": "review_structure_disposition_and_evidence_integrity",
        "protocol": document["protocol"],
        "review_id": document["review_id"],
        "review_type": document["review_type"],
        "repository_revision": document["subject"]["repository_revision"],
        "conclusion": conclusion,
        "open_findings": len(open_findings),
        "severity_counts": severity_counts,
        "evidence_artifacts_verified": len(evidence),
    }
