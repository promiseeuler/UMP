from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ump.cli import goal_main
from ump.goal import (
    goal_batch_schema,
    goal_schema,
    goal_validation_report,
    shared_goal_from_document,
    shared_goals_from_document,
)
from ump.models import SharedGoal


ROOT = Path(__file__).parents[1]


def goal_document(**updates):
    document = {
        "goal_id": "goal-move-1",
        "description": "Move the sealed package to storage",
        "participant_ids": ["robot-humanoid-1", "robot-arm-1"],
        "constraints": {"keep_upright": True},
        "deadline_ms": 1_770_000_000_000,
    }
    document.update(updates)
    return document


class SharedGoalTests(unittest.TestCase):
    def test_single_and_batch_documents_produce_typed_goals(self):
        goal = shared_goal_from_document(goal_document())
        self.assertIsInstance(goal, SharedGoal)
        self.assertEqual(goal.participant_ids, ("robot-humanoid-1", "robot-arm-1"))

        goals = shared_goals_from_document(
            [goal_document(), goal_document(goal_id="goal-move-2")]
        )
        self.assertEqual([item.goal_id for item in goals], ["goal-move-1", "goal-move-2"])

    def test_validation_rejects_unknown_fields_and_wrong_container_types(self):
        invalid_documents = (
            goal_document(unexpected=True),
            goal_document(participant_ids="robot-arm-1"),
            goal_document(constraints=[]),
            goal_document(deadline_ms=True),
            goal_document(participant_ids=["robot-arm-1", "robot-arm-1"]),
        )
        for document in invalid_documents:
            with self.subTest(document=document), self.assertRaises(ValueError):
                shared_goal_from_document(document)

    def test_batch_rejects_duplicate_goal_ids(self):
        with self.assertRaisesRegex(ValueError, "IDs must be unique"):
            shared_goals_from_document([goal_document(), goal_document()])

    def test_model_rejects_non_json_constraints(self):
        with self.assertRaisesRegex(ValueError, "JSON serializable"):
            SharedGoal("goal-1", "Inspect the route", ("robot-1",), {"x": float("nan")})

    def test_validation_report_is_content_light(self):
        report = goal_validation_report(goal_document())
        self.assertEqual(report["profile"], "ump.shared-goal/v1")
        self.assertEqual(report["goal_ids"], ["goal-move-1"])
        encoded = json.dumps(report)
        self.assertNotIn("sealed package", encoded)
        self.assertNotIn("keep_upright", encoded)

    def test_public_and_packaged_schemas_match(self):
        self.assertEqual(
            goal_schema(),
            json.loads((ROOT / "schemas" / "ump-shared-goal-v1.schema.json").read_text()),
        )
        self.assertEqual(
            goal_batch_schema(),
            json.loads(
                (ROOT / "schemas" / "ump-shared-goal-batch-v1.schema.json").read_text()
            ),
        )

    def test_cli_validates_files_and_stdin_with_controlled_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "goal.json"
            path.write_text(json.dumps(goal_document()))
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(goal_main(["validate", str(path)]), 0)
            self.assertTrue(json.loads(output.getvalue())["valid"])

            errors = StringIO()
            path.write_text(json.dumps(goal_document(deadline_ms=True)))
            with redirect_stderr(errors):
                self.assertEqual(goal_main(["validate", str(path)]), 2)
            self.assertIn("ump-goal:", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
