import copy
import json
import tempfile
import unittest
from pathlib import Path

from verify_trace import ARM, MOBILE, TASKS, load_trace, verify


def nominal_records():
    records = []
    for task in TASKS[:2]:
        records.extend(({"task_id": task, "status": "accepted"}, {"task_id": task, "status": "succeeded", "capability": "test"}))
    records.append({"reservation_id": "s4-zone-reservation", "status": "active"})
    states = ("proposed", "prepared", "ready", "transferring", "transferring", "transferring", "committed", "committed")
    for revision, state in zip((1, 2, 3, 4, 5, 6, 7, 7), states):
        evidence = []
        if revision >= 5:
            evidence.append({"actor_machine_id": ARM, "evidence_type": "physical_release_confirmed"})
        if revision >= 6:
            evidence.append({"actor_machine_id": MOBILE, "evidence_type": "payload_attached_confirmed"})
        records.append({"handoff_id": "s4-payload-handoff", "revision": revision, "state": state, "authoritative_owner_machine_id": ARM if revision < 7 else MOBILE, "evidence": evidence, "inspection_required": False, "failure_code": ""})
    for task in TASKS[2:5]:
        records.extend(({"task_id": task, "status": "accepted"}, {"task_id": task, "status": "succeeded", "capability": "test"}))
    records.append({"reservation_id": "s4-zone-reservation", "status": "released"})
    records.extend(({"task_id": TASKS[5], "status": "accepted"}, {"task_id": TASKS[5], "status": "succeeded", "capability": "test"}))
    return records


class TraceVerifierTests(unittest.TestCase):
    def test_nominal_trace_passes(self):
        self.assertTrue(verify(nominal_records())["passed"])

    def test_early_owner_change_fails(self):
        records = nominal_records()
        next(record for record in records if record.get("revision") == 6)["authoritative_owner_machine_id"] = MOBILE
        report = verify(records)
        self.assertIn("ownership_changes_only_at_commit", report["failed_checks"])

    def test_unilateral_evidence_fails(self):
        records = nominal_records()
        for record in records:
            if record.get("state") == "committed":
                record["evidence"] = record["evidence"][:1]
        report = verify(records)
        self.assertIn("bilateral_authenticated_evidence", report["failed_checks"])

    def test_malformed_json_is_rejected_with_line(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            path.write_text(json.dumps({"ok": True}) + "\nnot-json\n")
            with self.assertRaisesRegex(ValueError, "line 2"):
                load_trace(path)


if __name__ == "__main__":
    unittest.main()
