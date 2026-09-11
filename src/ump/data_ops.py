"""Consistent backup, verification, and restore for UMP SQLite stores."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import time
from typing import Any


class DataOperationError(ValueError):
    pass


def _hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_databases(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        role, separator, raw_path = value.partition("=")
        if not separator or not role or not raw_path:
            raise DataOperationError("database must use role=/path/to/store.sqlite3")
        if role in result or not role.replace("-", "_").isidentifier():
            raise DataOperationError(f"invalid or duplicate database role: {role}")
        path = Path(raw_path).resolve()
        if not path.is_file():
            raise DataOperationError(f"database does not exist: {path}")
        result[role] = path
    if not result:
        raise DataOperationError("at least one database is required")
    return result


def create_backup(databases: dict[str, Path], output: str | Path) -> dict[str, Any]:
    destination = Path(output).resolve()
    if destination.exists():
        raise DataOperationError(f"backup output already exists: {destination}")
    destination.mkdir(parents=True)
    records = []
    try:
        for role, source_path in sorted(databases.items()):
            filename = f"{role}.sqlite3"
            backup_path = destination / filename
            source = sqlite3.connect(f"{source_path.as_uri()}?mode=ro", uri=True)
            target = sqlite3.connect(backup_path)
            try:
                source.backup(target)
                result = target.execute("PRAGMA integrity_check").fetchone()[0]
                if result != "ok":
                    raise DataOperationError(f"backup integrity failed for {role}: {result}")
            finally:
                target.close()
                source.close()
            records.append(
                {
                    "role": role,
                    "file": filename,
                    "size_bytes": backup_path.stat().st_size,
                    "sha256": _hash(backup_path),
                }
            )
        manifest = {
            "profile": "ump-data-backup/v1",
            "created_at_ms": int(time.time() * 1_000),
            "databases": records,
        }
        manifest_path = destination / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return {**manifest, "manifest": str(manifest_path)}
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def verify_backup(manifest_path: str | Path) -> dict[str, Any]:
    path = Path(manifest_path).resolve()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DataOperationError(f"backup manifest cannot be read: {error}") from error
    if document.get("profile") != "ump-data-backup/v1":
        raise DataOperationError("unsupported backup manifest profile")
    records = document.get("databases")
    if not isinstance(records, list) or not records:
        raise DataOperationError("backup manifest has no databases")
    verified = []
    for record in records:
        if not isinstance(record, dict):
            raise DataOperationError("invalid database record")
        candidate = (path.parent / str(record.get("file", ""))).resolve()
        if candidate.parent != path.parent or not candidate.is_file():
            raise DataOperationError("backup database path is invalid")
        if _hash(candidate) != record.get("sha256"):
            raise DataOperationError(f"backup checksum mismatch: {record.get('role')}")
        connection = sqlite3.connect(f"{candidate.as_uri()}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            connection.close()
        if integrity != "ok":
            raise DataOperationError(f"database integrity failed: {record.get('role')}")
        verified.append(record["role"])
    return {"valid": True, "profile": document["profile"], "roles": verified}


def restore_backup(manifest_path: str | Path, target_directory: str | Path) -> dict[str, Any]:
    verification = verify_backup(manifest_path)
    manifest = Path(manifest_path).resolve()
    destination = Path(target_directory).resolve()
    if destination.exists():
        raise DataOperationError(f"restore target already exists: {destination}")
    destination.mkdir(parents=True)
    document = json.loads(manifest.read_text(encoding="utf-8"))
    try:
        for record in document["databases"]:
            shutil.copyfile(manifest.parent / record["file"], destination / record["file"])
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return {"restored": True, "target": str(destination), "roles": verification["roles"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ump-ops", description="Operate durable UMP data.")
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup", help="Create consistent SQLite backups")
    backup.add_argument("--database", action="append", default=[], metavar="ROLE=PATH")
    backup.add_argument("--output", required=True)
    verify = commands.add_parser("verify-backup", help="Verify checksums and SQLite integrity")
    verify.add_argument("manifest")
    restore = commands.add_parser("restore", help="Restore into a new directory")
    restore.add_argument("manifest")
    restore.add_argument("--target-directory", required=True)
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "backup":
            result = create_backup(_parse_databases(arguments.database), arguments.output)
        elif arguments.command == "verify-backup":
            result = verify_backup(arguments.manifest)
        else:
            result = restore_backup(arguments.manifest, arguments.target_directory)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (DataOperationError, OSError, sqlite3.Error) as error:
        print(f"ump-ops: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
