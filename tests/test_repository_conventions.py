from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1]))

from scripts.check_repo_conventions import commit_error, filename_errors


class RepositoryConventionTests(unittest.TestCase):
    def test_conventional_commit_subjects(self):
        self.assertIsNone(commit_error("feat(protocol): add standard mapping"))
        self.assertIsNone(commit_error("refactor(core)!: remove old API"))
        self.assertIsNotNone(commit_error("updated files"))

    def test_filename_rules(self):
        self.assertEqual(
            filename_errors(
                [
                    Path("src/ump/robot_state.py"),
                    Path("src/ump/__init__.py"),
                    Path("tests/test_robot_state.py"),
                    Path("docs/ROBOT_STATE.md"),
                    Path("schemas/ump-robot-state-v1.schema.json"),
                ]
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
