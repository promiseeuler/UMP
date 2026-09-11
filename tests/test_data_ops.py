import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ump.data_ops import DataOperationError, create_backup, restore_backup, verify_backup


class DataOperationsTests(unittest.TestCase):
    def test_backup_verify_and_restore_are_integrity_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.sqlite3"
            connection = sqlite3.connect(source)
            connection.execute("CREATE TABLE evidence (value TEXT NOT NULL)")
            connection.execute("INSERT INTO evidence VALUES ('terminal-outcome')")
            connection.commit()
            connection.close()

            result = create_backup({"assignment": source}, root / "backup")
            verified = verify_backup(result["manifest"])
            restored = restore_backup(result["manifest"], root / "restored")

            self.assertEqual(verified["roles"], ["assignment"])
            self.assertTrue(restored["restored"])
            restored_connection = sqlite3.connect(root / "restored" / "assignment.sqlite3")
            try:
                value = restored_connection.execute("SELECT value FROM evidence").fetchone()[0]
            finally:
                restored_connection.close()
            self.assertEqual(value, "terminal-outcome")

    def test_modified_backup_and_existing_restore_target_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.sqlite3"
            sqlite3.connect(source).close()
            result = create_backup({"store": source}, root / "backup")
            manifest = Path(result["manifest"])
            document = json.loads(manifest.read_text())
            backup = manifest.parent / document["databases"][0]["file"]
            backup.write_bytes(backup.read_bytes() + b"changed")
            with self.assertRaisesRegex(DataOperationError, "checksum mismatch"):
                verify_backup(manifest)
            existing = root / "existing"
            existing.mkdir()
            with self.assertRaises(DataOperationError):
                restore_backup(manifest, existing)


if __name__ == "__main__":
    unittest.main()
