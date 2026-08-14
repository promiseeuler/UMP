#!/usr/bin/env python3
"""Verify the nominal S4 trace and emit a portable invariant report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


TASKS = (
    "s4-arm-pick",
    "s4-mobile-approach",
    "s4-arm-place",
    "s4-arm-release",
    "s4-mobile-receive",
    "s4-mobile-deliver",
)
ARM = "ump:machine:robot-arm-1"
MOBILE = "ump:machine:mobile-base-1"


def load_trace(path: Path) -> list[dict[str, Any]]:
    records = []
    for line_number, line in enumerate(path.read_text().splitlines(), 1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"line {line_number} is not JSON: {error}") from error
        if not isinstance(value, dict):
            raise ValueError(f"line {line_number} is not a JSON object")
        records.append(value)
    return records


def verify(records: list[dict[str, Any]]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def check(name: str, condition: bool, detail: str) -> None:
        checks.append({"name": name, "passed": bool(condition), "detail": detail})

    terminal_tasks = {
        record.get("task_id"): record
        for record in records
        if record.get("status") == "succeeded" and "capability" in record
    }
    check(
        "all_tasks_succeeded_once",
        set(terminal_tasks) == set(TASKS)
        and sum(
            record.get("status") == "succeeded" and "capability" in record
            for record in records
        )
        == len(TASKS),
        "six distinct terminal task results are required",
    )

    reservation_indices = [
        index
        for index, record in enumerate(records)
        if record.get("reservation_id") == "s4-zone-reservation"
    ]
    active = [index for index in reservation_indices if records[index].get("status") == "active"]
    released = [
        index for index in reservation_indices if records[index].get("status") == "released"
    ]
    check(
        "exclusive_zone_lifecycle",
        len(active) == 1 and len(released) == 1 and active[0] < released[0],
        "one active reservation must precede one release",
    )

    handoffs = [record for record in records if record.get("handoff_id") == "s4-payload-handoff"]
    revisions = [record.get("revision") for record in handoffs]
    check(
        "handoff_revision_sequence",
        revisions == [1, 2, 3, 4, 5, 6, 7, 7],
        f"observed revisions: {revisions}",
    )
    states = [record.get("state") for record in handoffs]
    check(
        "handoff_state_sequence",
        states
        == [
            "proposed",
            "prepared",
            "ready",
            "transferring",
            "transferring",
            "transferring",
            "committed",
            "committed",
        ],
        f"observed states: {states}",
    )

    precommit = [record for record in handoffs if (record.get("revision") or 0) < 7]
    committed = [record for record in handoffs if record.get("state") == "committed"]
    check(
        "ownership_changes_only_at_commit",
        bool(precommit)
        and all(record.get("authoritative_owner_machine_id") == ARM for record in precommit)
        and len(committed) == 2
        and all(record.get("authoritative_owner_machine_id") == MOBILE for record in committed),
        "arm must remain owner through revision 6; mobile owns committed records",
    )

    final_evidence = committed[-1].get("evidence", []) if committed else []
    evidence_by_actor = {
        item.get("actor_machine_id"): item.get("evidence_type")
        for item in final_evidence
        if isinstance(item, dict)
    }
    check(
        "bilateral_authenticated_evidence",
        evidence_by_actor
        == {
            ARM: "physical_release_confirmed",
            MOBILE: "payload_attached_confirmed",
        },
        f"observed participant evidence: {evidence_by_actor}",
    )

    commit_index = next(
        (index for index, record in enumerate(records) if record.get("state") == "committed"),
        -1,
    )
    deliver_index = next(
        (index for index, record in enumerate(records) if record.get("task_id") == "s4-mobile-deliver"),
        -1,
    )
    check(
        "delivery_after_commit_and_release",
        commit_index >= 0
        and len(released) == 1
        and commit_index < released[0] < deliver_index,
        "final delivery may begin only after commit and zone release",
    )
    check(
        "no_uncertain_or_failed_outcomes",
        not any(
            record.get("inspection_required")
            or record.get("failure_code")
            or record.get("status") in {"failed", "cancelled", "rejected", "unknown"}
            for record in records
        ),
        "nominal trace must contain no failure or inspection-required state",
    )

    failures = [item["name"] for item in checks if not item["passed"]]
    return {
        "scenario": "s4_embodied_warehouse_handoff",
        "passed": not failures,
        "record_count": len(records),
        "task_count": len(terminal_tasks),
        "checks": checks,
        "failed_checks": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("trace", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        report = verify(load_trace(args.trace))
    except (OSError, ValueError) as error:
        report = {
            "scenario": "s4_embodied_warehouse_handoff",
            "passed": False,
            "failed_checks": ["trace_readable"],
            "error": str(error),
        }
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered)
    print(rendered, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
