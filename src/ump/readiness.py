from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
from typing import Any


REQUIREMENT_PATTERN = re.compile(r"`([A-Z]{3}-[0-9]{2})`")
VALID_STATUSES = frozenset({"implemented", "partial", "missing"})


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
    return {
        "protocol": document.get("protocol"),
        "total": len(requirements),
        "counts": dict(sorted(counts.items())),
        "ready": counts["partial"] == 0 and counts["missing"] == 0,
        "requirements": requirements,
    }
