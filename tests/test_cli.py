import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ump.cli import authority_main


class AuthorityCliTests(unittest.TestCase):
    def test_owner_can_grant_audit_and_revoke_local_lease(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = str(Path(temporary_directory) / "authority.sqlite3")
            common = ["--robot-id", "robot-1", "--database", database]
            output = io.StringIO()
            with redirect_stdout(output):
                result = authority_main(
                    common
                    + [
                        "grant",
                        "--lease-id",
                        "lease-1",
                        "--issuer-id",
                        "coordinator-1",
                        "--capability",
                        "ump.material.carry/v1",
                        "--issued-at-ms",
                        "1000",
                        "--expires-at-ms",
                        "10000",
                    ]
                )
            self.assertEqual(result, 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "active")

            output = io.StringIO()
            with redirect_stdout(output):
                result = authority_main(common + ["events", "--lease-id", "lease-1"])
            self.assertEqual(result, 0)
            self.assertEqual(json.loads(output.getvalue())[0]["event_type"], "grant")

            output = io.StringIO()
            with redirect_stdout(output):
                result = authority_main(
                    common
                    + [
                        "revoke",
                        "--lease-id",
                        "lease-1",
                        "--expected-revision",
                        "1",
                        "--reason",
                        "operator request",
                        "--occurred-at-ms",
                        "2000",
                    ]
                )
            self.assertEqual(result, 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "revoked")


if __name__ == "__main__":
    unittest.main()
