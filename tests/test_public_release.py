from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ump.cli import public_readiness_main
from ump.public_release import audit_public_release


def git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def write_governance(root: Path) -> None:
    for name in ("CODE_OF_CONDUCT.md", "CONTRIBUTING.md", "LICENSE", "SECURITY.md"):
        (root / name).write_text(f"# {name}\n", encoding="utf-8")


def repository(*, deleted_secret: bool = False) -> tempfile.TemporaryDirectory:
    temporary = tempfile.TemporaryDirectory()
    root = Path(temporary.name)
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Public Test")
    git(root, "config", "user.email", "1+public-test@users.noreply.github.com")
    (root / "legacy").mkdir()
    (root / "legacy" / "implementation.txt").write_text(
        "old implementation\n", encoding="utf-8"
    )
    if deleted_secret:
        private_key_header = "-----BEGIN " + "PRIVATE KEY-----"
        private_key_footer = "-----END " + "PRIVATE KEY-----"
        (root / "legacy" / "secret.txt").write_text(
            f"{private_key_header}\n{'A' * 80}\n{private_key_footer}\n",
            encoding="utf-8",
        )
    git(root, "add", ".")
    git(root, "commit", "-m", "legacy")
    for path in (root / "legacy").iterdir():
        path.unlink()
    (root / "legacy").rmdir()
    write_governance(root)
    (root / "README.md").write_text("# Public project\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-m", "current")
    return temporary


class PublicReleaseTests(unittest.TestCase):
    def test_deleted_historical_tree_requires_explicit_review(self):
        with repository() as name:
            root = Path(name)
            report = audit_public_release(root)
            self.assertFalse(report["ready"])
            self.assertFalse(report["checks"]["historical_tree_reviewed"])
            self.assertEqual(report["findings"]["historical_only_roots"], ["legacy"])

            accepted = audit_public_release(root, accept_historical_tree=True)
            self.assertTrue(accepted["ready"])
            self.assertTrue(all(accepted["checks"].values()))

    def test_deleted_secret_remains_a_blocker_after_history_review(self):
        with repository(deleted_secret=True) as name:
            report = audit_public_release(name, accept_historical_tree=True)
        self.assertFalse(report["ready"])
        self.assertGreaterEqual(
            report["findings"]["secret_match_counts"]["private_key"], 1
        )

    def test_cli_emits_machine_readable_review_result(self):
        with repository() as name:
            output = StringIO()
            with redirect_stdout(output):
                status = public_readiness_main(
                    [name, "--accept-historical-tree"]
                )
        self.assertEqual(status, 0)
        self.assertEqual(
            json.loads(output.getvalue())["profile"], "ump.public-readiness/v1"
        )


if __name__ == "__main__":
    unittest.main()
