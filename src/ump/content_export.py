"""Export content-safe evidence from a read-only UMP inspector database."""

from __future__ import annotations

import argparse
import csv
from hashlib import sha256
import io
import json
from pathlib import Path
import shutil
import sys
import time
from typing import Any

from .inspector import InspectorStoreError, ReadOnlyInspectorStore


class EvidenceExportError(ValueError):
    pass


def _hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_result(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvidenceExportError(f"scenario result cannot be read: {error}") from error
    if not isinstance(document, dict) or not isinstance(document.get("passed"), bool):
        raise EvidenceExportError("scenario result must contain a boolean passed field")
    return document


def _timeline_csv(events: list[dict[str, Any]]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=(
            "timestamp_ms",
            "source_id",
            "message_type",
            "correlation_id",
            "message_id",
        ),
    )
    writer.writeheader()
    for event in sorted(events, key=lambda item: (item["timestamp_ms"], item["sequence"])):
        writer.writerow(
            {
                "timestamp_ms": event["timestamp_ms"],
                "source_id": event["source_id"],
                "message_type": event["message_type"],
                "correlation_id": event.get("correlation_id") or "",
                "message_id": event["message_id"],
            }
        )
    return output.getvalue()


def _pitch_markdown(evidence: dict[str, Any]) -> str:
    result = evidence["scenario_result"]
    robot_ids = evidence["observed_robot_ids"]
    counts = evidence["message_counts"]
    status = "passed" if result["passed"] else "failed"
    lines = [
        "# UMP Multi-Robot Test Evidence",
        "",
        f"**Recorded result:** {status}",
        "",
        "## What happened",
        "",
        f"UMP observed {len(robot_ids)} independently identified participants: "
        + ", ".join(f"`{item}`" for item in robot_ids)
        + ".",
        f"The append-only inspector database recorded {evidence['event_count']} canonical UMP messages.",
        "The collaborative scenario completed with these step outcomes:",
        "",
    ]
    for step_id, step_status in sorted(result.get("step_statuses", {}).items()):
        lines.append(f"- `{step_id}`: `{step_status}`")
    lines.extend(
        [
            "",
            "## Recorded protocol flow",
            "",
        ]
    )
    for message_type, count in sorted(counts.items()):
        lines.append(f"- `{message_type}`: {count}")
    lines.extend(
        [
            "",
            "## What this demonstrates",
            "",
            "The evidence shows that multiple robot identities published semantic state, shared one UMP network, and produced correlated collaboration records through the normal participant and coordinator interfaces.",
            "",
            "## Claim boundary",
            "",
            "This is software-simulation evidence. It demonstrates UMP message flow and observability, not physical robot compatibility, motion safety, or manufacturer certification.",
            "",
        ]
    )
    return "\n".join(lines)


def export_evidence(
    database: str | Path,
    scenario_result: str | Path,
    output_directory: str | Path,
    *,
    generated_at_ms: int | None = None,
) -> dict[str, Any]:
    database_path = Path(database).resolve()
    result_path = Path(scenario_result).resolve()
    destination = Path(output_directory).resolve()
    if destination.exists():
        raise EvidenceExportError(f"evidence output already exists: {destination}")
    result = _read_result(result_path)
    store = ReadOnlyInspectorStore(database_path)
    try:
        snapshot = store.snapshot(event_limit=1_000)
    finally:
        store.close()
    events = snapshot["events"]
    message_counts: dict[str, int] = {}
    for event in events:
        message_type = event["message_type"]
        message_counts[message_type] = message_counts.get(message_type, 0) + 1
    evidence = {
        "profile": "ump-content-evidence/v1",
        "generated_at_ms": generated_at_ms or int(time.time() * 1_000),
        "scenario_result": result,
        "observed_robot_ids": [item["robot_id"] for item in snapshot["robots"]],
        "event_count": snapshot["event_count"],
        "message_counts": message_counts,
        "lab_events": snapshot["lab_events"],
        "artifacts": {
            "inspector_database": {
                "file": database_path.name,
                "sha256": _hash(database_path),
                "size_bytes": database_path.stat().st_size,
            },
            "scenario_result": {
                "file": result_path.name,
                "sha256": _hash(result_path),
                "size_bytes": result_path.stat().st_size,
            },
        },
        "claim_boundary": "software simulation; no physical robot certification",
    }
    destination.mkdir(parents=True)
    try:
        (destination / "evidence.json").write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (destination / "timeline.csv").write_text(
            _timeline_csv(events), encoding="utf-8"
        )
        (destination / "pitch-summary.md").write_text(
            _pitch_markdown(evidence), encoding="utf-8"
        )
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return {
        "exported": True,
        "output_directory": str(destination),
        "event_count": evidence["event_count"],
        "robot_count": len(evidence["observed_robot_ids"]),
        "passed": result["passed"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ump-evidence",
        description="Export presentation-safe evidence from recorded UMP traffic.",
    )
    parser.add_argument("--database", required=True)
    parser.add_argument("--scenario-result", required=True)
    parser.add_argument("--output-directory", required=True)
    arguments = parser.parse_args(argv)
    try:
        report = export_evidence(
            arguments.database,
            arguments.scenario_result,
            arguments.output_directory,
        )
        print(json.dumps(report, sort_keys=True))
        return 0 if report["passed"] else 1
    except (EvidenceExportError, InspectorStoreError, OSError) as error:
        print(f"ump-evidence: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
