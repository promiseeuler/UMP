from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
from typing import Any


REQUIREMENT_PATTERN = re.compile(r"`([A-Z]{3}-[0-9]{2})`")
VALID_STATUSES = frozenset({"implemented", "partial", "missing"})
VALID_QUALIFICATION_STATUSES = frozenset({"passed", "pending"})
QUALIFICATION_GATE_IDS = frozenset(
    {
        "QAL-ROS2-NATIVE",
        "QAL-LAN-TWO-HOST",
        "QAL-HARDWARE-PILOT",
        "QAL-ADAPTER-CONFORMANCE",
        "QAL-SECURITY-REVIEW",
        "QAL-SAFETY-REVIEW",
        "QAL-INTEROPERABILITY-REVIEW",
        "QAL-RELEASE-ARTIFACT",
    }
)


def _validate_paths(
    root: Path,
    paths: Any,
    description: str,
    *,
    require_exists: bool,
) -> list[str]:
    if not isinstance(paths, list):
        raise ValueError(f"{description} must be a list")
    if any(not isinstance(path, str) or not path for path in paths):
        raise ValueError(f"{description} contains an invalid path")
    if require_exists:
        absent = [path for path in paths if not (root / path).is_file()]
        if absent:
            raise ValueError(f"absent {description}: {absent}")
    return paths


def _load_qualification(root: Path, *, strict_evidence: bool) -> dict[str, Any]:
    path = root / "compliance" / "qualification.json"
    document = json.loads(path.read_text())
    if document.get("profile") != "ump.production-qualification/v1":
        raise ValueError("unsupported production qualification profile")
    gates = document.get("gates")
    if not isinstance(gates, list):
        raise ValueError("qualification matrix must contain a gates list")
    gate_ids = [item.get("id") for item in gates if isinstance(item, dict)]
    if len(gate_ids) != len(gates) or len(gate_ids) != len(set(gate_ids)):
        raise ValueError("qualification matrix contains invalid or duplicate gate IDs")
    if set(gate_ids) != QUALIFICATION_GATE_IDS:
        missing = sorted(QUALIFICATION_GATE_IDS - set(gate_ids))
        extra = sorted(set(gate_ids) - QUALIFICATION_GATE_IDS)
        raise ValueError(
            f"qualification matrix drift: missing={missing}, extra={extra}"
        )
    for gate in gates:
        gate_id = gate["id"]
        status = gate.get("status")
        if status not in VALID_QUALIFICATION_STATUSES:
            raise ValueError(f"invalid qualification status for {gate_id}")
        implementation = gate.get("implementation_evidence")
        if not isinstance(implementation, list) or not implementation:
            raise ValueError(f"missing implementation evidence for {gate_id}")
        results = gate.get("result_evidence")
        if not isinstance(results, list):
            raise ValueError(f"result evidence for {gate_id} must be a list")
        if status == "passed" and not results:
            raise ValueError(f"passed qualification gate lacks results: {gate_id}")
        _validate_paths(
            root,
            implementation,
            f"implementation evidence for {gate_id}",
            require_exists=strict_evidence,
        )
        _validate_paths(
            root,
            results,
            f"result evidence for {gate_id}",
            require_exists=strict_evidence,
        )
        note = gate.get("note")
        if not isinstance(note, str) or not note.strip():
            raise ValueError(f"missing qualification note for {gate_id}")
    counts = Counter(gate["status"] for gate in gates)
    return {
        "profile": document.get("profile"),
        "total": len(gates),
        "counts": dict(sorted(counts.items())),
        "ready": counts["pending"] == 0,
        "gates": gates,
    }


def load_readiness_report(
    project_root: str | Path,
    *,
    strict_evidence: bool = True,
) -> dict[str, Any]:
    root = Path(project_root)
    prd_path = root / "docs" / "PRD.md"
    matrix_path = root / "compliance" / "requirements.json"
    prd_ids = REQUIREMENT_PATTERN.findall(prd_path.read_text())
    document = json.loads(matrix_path.read_text())
    requirements = document.get("requirements")
    if not isinstance(requirements, list):
        raise ValueError("requirements matrix must contain a requirements list")
    matrix_ids = [item.get("id") for item in requirements]
    if len(matrix_ids) != len(set(matrix_ids)):
        raise ValueError("requirements matrix contains duplicate IDs")
    if set(matrix_ids) != set(prd_ids):
        missing = sorted(set(prd_ids) - set(matrix_ids))
        extra = sorted(set(matrix_ids) - set(prd_ids))
        raise ValueError(f"requirements matrix drift: missing={missing}, extra={extra}")
    for item in requirements:
        if item.get("status") not in VALID_STATUSES:
            raise ValueError(f"invalid status for {item.get('id')}")
        evidence = item.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError(f"missing evidence for {item.get('id')}")
        if strict_evidence:
            absent = [path for path in evidence if not (root / path).is_file()]
            if absent:
                raise ValueError(f"absent evidence for {item.get('id')}: {absent}")
        note = item.get("note")
        if not isinstance(note, str) or not note.strip():
            raise ValueError(f"missing assessment note for {item.get('id')}")
    counts = Counter(item["status"] for item in requirements)
    functional_ready = counts["partial"] == 0 and counts["missing"] == 0
    qualification = _load_qualification(root, strict_evidence=strict_evidence)
    return {
        "protocol": document.get("protocol"),
        "total": len(requirements),
        "counts": dict(sorted(counts.items())),
        "functional_ready": functional_ready,
        "production_ready": qualification["ready"],
        "ready": functional_ready and qualification["ready"],
        "requirements": requirements,
        "qualification": qualification,
    }
