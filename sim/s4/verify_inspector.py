#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


EXPECTED_IDS = {
    "coordinator": "ump:machine:s4-coordinator",
    "mobile": "ump:machine:mobile-base-1",
    "arm": "ump:machine:robot-arm-1",
    "zone": "ump:machine:transfer-zone-1",
}
REQUIRED_SECTIONS = {
    "machine",
    "runtime",
    "trust",
    "capabilities",
    "tasks",
    "leases",
    "resources",
    "reservations",
    "handoffs",
    "events",
    "faults",
}
FORBIDDEN_KEYS = {"private_key", "identity_key", "certificate_der", "root_certificate_der"}


def verify(directory: Path, profile: str = "nominal") -> dict:
    snapshots = {
        name: json.loads((directory / f"{name}.json").read_text())
        for name in EXPECTED_IDS
    }
    checks = []

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})

    sections_ok = all(REQUIRED_SECTIONS <= set(value) for value in snapshots.values())
    check("required_sections", sections_ok, "every runtime exposes the inspector contract")
    identities = {name: value["machine"]["machine_id"] for name, value in snapshots.items()}
    check("expected_identities", identities == EXPECTED_IDS, str(identities))

    zone = snapshots["zone"]
    if profile == "nominal":
        tasks = list(snapshots["mobile"]["tasks"].values()) + list(
            snapshots["arm"]["tasks"].values()
        )
        task_ids = {task["task_id"] for task in tasks}
        check(
            "six_successful_tasks",
            len(task_ids) == 6 and all(task["state"] == "succeeded" for task in tasks),
            f"task_ids={sorted(task_ids)}",
        )
        reservations = list(zone["reservations"].values())
        handoffs = list(zone["handoffs"].values())
        check(
            "released_reservation",
            len(reservations) == 1 and reservations[0]["state"] == "released",
            f"states={[item['state'] for item in reservations]}",
        )
        check(
            "committed_handoff",
            len(handoffs) == 1
            and handoffs[0]["state"] == "committed"
            and handoffs[0]["authoritative_owner_machine_id"]
            == "ump:machine:mobile-base-1",
            f"states={[item['state'] for item in handoffs]}",
        )
    elif profile == "fault":
        check(
            "expected_task_faults",
            "s4-blocked-navigation"
            in snapshots["mobile"]["faults"]["failed_or_unknown_task_ids"]
            and "s4-failed-grasp"
            in snapshots["arm"]["faults"]["failed_or_unknown_task_ids"],
            "blocked navigation and failed grasp must be explicit",
        )
        check(
            "expected_uncertain_handoffs",
            set(zone["faults"]["failed_or_unknown_handoff_ids"])
            >= {"s4-drop-handoff", "s4-physical-intrusion"},
            "drop and intrusion transfers must require inspection",
        )
        check(
            "emergency_latched",
            snapshots["mobile"]["runtime"]["safety"] == "emergency_stop"
            and snapshots["mobile"]["tasks"]["s4-post-emergency-gate"]["state"]
            == "accepted",
            "emergency is persisted and post-stop work remains unclaimed",
        )
    else:
        raise ValueError(f"unknown inspector profile: {profile}")
    check(
        "event_history_present",
        all(
            isinstance(value["events"]["tasks"], list)
            and isinstance(value["events"]["coordination"], list)
            for value in snapshots.values()
        )
        and len(zone["events"]["coordination"]) >= (8 if profile == "nominal" else 1),
        "task and coordination histories are structured arrays",
    )

    observed_keys = set()

    def collect_keys(value) -> None:
        if isinstance(value, dict):
            observed_keys.update(value)
            for nested in value.values():
                collect_keys(nested)
        elif isinstance(value, list):
            for nested in value:
                collect_keys(nested)

    collect_keys(snapshots)
    leaked = sorted(observed_keys & FORBIDDEN_KEYS)
    check("no_secret_material", not leaked, f"forbidden_keys={leaked}")

    failed = [item["name"] for item in checks if not item["passed"]]
    return {
        "profile": profile,
        "passed": not failed,
        "failed_checks": failed,
        "checks": checks,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--profile", choices=("nominal", "fault"), default="nominal")
    args = parser.parse_args()
    result = verify(args.directory, args.profile)
    if args.report:
        args.report.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
