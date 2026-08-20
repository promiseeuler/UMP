from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
from threading import RLock
import time

from cryptography import x509
from cryptography.hazmat.primitives import serialization

from .network import IDENTITY_URI_PREFIX


class CredentialError(ValueError):
    pass


@dataclass(frozen=True)
class CredentialGeneration:
    generation: int
    robot_id: str
    fingerprint_sha256: str
    certificate_path: Path
    private_key_path: Path
    ca_path: Path
    not_before_ms: int
    not_after_ms: int
    status: str


@dataclass(frozen=True)
class CredentialGenerationSummary:
    generation: CredentialGeneration
    effective_status: str
    revoked: bool
    created_at_ms: int
    activated_at_ms: int | None


EFFECTIVE_CREDENTIAL_STATUSES = frozenset(
    {"active", "staged", "retired", "not_yet_valid", "expired", "revoked"}
)
CREDENTIAL_GENERATION_COLUMNS = frozenset(
    {
        "generation",
        "robot_id",
        "fingerprint_sha256",
        "certificate_path",
        "private_key_path",
        "ca_path",
        "not_before_ms",
        "not_after_ms",
        "status",
        "created_at_ms",
        "activated_at_ms",
    }
)
REVOKED_CERTIFICATE_COLUMNS = frozenset(
    {"fingerprint_sha256", "reason", "revoked_at_ms"}
)
CREDENTIAL_EVENT_COLUMNS = frozenset(
    {"sequence", "robot_id", "event_type", "occurred_at_ms", "detail_json"}
)


def _open_credentials_read_only(database: str | Path) -> sqlite3.Connection:
    path = Path(database).resolve()
    if not path.is_file():
        raise CredentialError(f"credential database does not exist: {path}")
    try:
        connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        tables = {
            "credential_generations": CREDENTIAL_GENERATION_COLUMNS,
            "revoked_certificates": REVOKED_CERTIFICATE_COLUMNS,
            "credential_events": CREDENTIAL_EVENT_COLUMNS,
        }
        for table, expected in tables.items():
            columns = {
                row[1] for row in connection.execute(f"PRAGMA table_info({table})")
            }
            if missing := sorted(expected - columns):
                raise CredentialError(
                    f"credential database {table} is missing columns: {missing}"
                )
        return connection
    except (sqlite3.Error, CredentialError) as error:
        if "connection" in locals():
            connection.close()
        if isinstance(error, CredentialError):
            raise
        raise CredentialError(
            f"credential database cannot be opened read-only: {error}"
        ) from error


def _generation_summary(row: sqlite3.Row) -> CredentialGenerationSummary:
    try:
        generation = SqliteCredentialStore._generation(row)
        return CredentialGenerationSummary(
            generation=generation,
            effective_status=row["effective_status"],
            revoked=bool(row["revoked"]),
            created_at_ms=row["created_at_ms"],
            activated_at_ms=row["activated_at_ms"],
        )
    except (KeyError, TypeError, ValueError) as error:
        raise CredentialError(f"credential generation row is invalid: {error}") from error


def _credential_inventory_query() -> str:
    return (
        "SELECT inventory.* FROM (SELECT generations.*, "
        "CASE WHEN revoked.fingerprint_sha256 IS NOT NULL THEN 1 ELSE 0 END AS revoked, "
        "CASE WHEN revoked.fingerprint_sha256 IS NOT NULL THEN 'revoked' "
        "WHEN generations.not_before_ms > ? THEN 'not_yet_valid' "
        "WHEN generations.not_after_ms <= ? THEN 'expired' "
        "ELSE generations.status END AS effective_status "
        "FROM credential_generations AS generations LEFT JOIN revoked_certificates AS revoked "
        "ON revoked.fingerprint_sha256 = generations.fingerprint_sha256) AS inventory"
    )


def read_credential_generations(
    database: str | Path,
    robot_id: str,
    now_ms: int,
    *,
    effective_status: str | None = None,
    stored_status: str | None = None,
    limit: int = 100,
) -> tuple[CredentialGenerationSummary, ...]:
    """List bounded credential generations without mutating inventory state."""
    if not isinstance(robot_id, str) or not robot_id.strip():
        raise CredentialError("robot_id is required")
    if type(now_ms) is not int or now_ms < 0:
        raise CredentialError("now_ms must be a non-negative integer")
    if (
        effective_status is not None
        and effective_status not in EFFECTIVE_CREDENTIAL_STATUSES
    ):
        raise CredentialError("effective credential status is invalid")
    if stored_status is not None and stored_status not in {"staged", "active", "retired"}:
        raise CredentialError("stored credential status is invalid")
    if type(limit) is not int or not 1 <= limit <= 1_000:
        raise CredentialError("credential limit must be between 1 and 1000")
    connection = _open_credentials_read_only(database)
    try:
        clauses = ["robot_id = ?"]
        parameters: list[object] = [now_ms, now_ms, robot_id]
        if effective_status is not None:
            clauses.append("effective_status = ?")
            parameters.append(effective_status)
        if stored_status is not None:
            clauses.append("status = ?")
            parameters.append(stored_status)
        rows = connection.execute(
            _credential_inventory_query()
            + " WHERE "
            + " AND ".join(clauses)
            + " ORDER BY generation DESC LIMIT ?",
            (*parameters, limit),
        ).fetchall()
        return tuple(_generation_summary(row) for row in rows)
    except sqlite3.Error as error:
        raise CredentialError(f"credential inventory cannot be read: {error}") from error
    finally:
        connection.close()


def read_credential_generation(
    database: str | Path,
    robot_id: str,
    generation: int,
    now_ms: int,
) -> CredentialGenerationSummary:
    if type(generation) is not int or generation < 1:
        raise CredentialError("generation must be a positive integer")
    if not isinstance(robot_id, str) or not robot_id.strip():
        raise CredentialError("robot_id is required")
    if type(now_ms) is not int or now_ms < 0:
        raise CredentialError("now_ms must be a non-negative integer")
    connection = _open_credentials_read_only(database)
    try:
        row = connection.execute(
            _credential_inventory_query()
            + " WHERE robot_id = ? AND generation = ?",
            (now_ms, now_ms, robot_id, generation),
        ).fetchone()
        if row is None:
            raise CredentialError("credential generation does not exist")
        return _generation_summary(row)
    except sqlite3.Error as error:
        raise CredentialError(f"credential generation cannot be read: {error}") from error
    finally:
        connection.close()


def read_credential_events(
    database: str | Path,
    robot_id: str,
    *,
    limit: int = 1_000,
) -> tuple[dict[str, object], ...]:
    if not isinstance(robot_id, str) or not robot_id.strip():
        raise CredentialError("robot_id is required")
    if type(limit) is not int or not 1 <= limit <= 1_000:
        raise CredentialError("event limit must be between 1 and 1000")
    connection = _open_credentials_read_only(database)
    try:
        rows = connection.execute(
            "SELECT sequence, event_type, occurred_at_ms, detail_json FROM ("
            "SELECT sequence, event_type, occurred_at_ms, detail_json "
            "FROM credential_events WHERE robot_id = ? "
            "ORDER BY sequence DESC LIMIT ?) ORDER BY sequence",
            (robot_id, limit),
        ).fetchall()
        try:
            return tuple(
                {
                    "sequence": row[0],
                    "event_type": row[1],
                    "occurred_at_ms": row[2],
                    "detail": json.loads(row[3]),
                }
                for row in rows
            )
        except (TypeError, json.JSONDecodeError) as error:
            raise CredentialError(f"credential event row is invalid: {error}") from error
    except sqlite3.Error as error:
        raise CredentialError(f"credential events cannot be read: {error}") from error
    finally:
        connection.close()


def read_active_credential(
    database: str | Path,
    robot_id: str,
    certificate_path: str | Path,
    private_key_path: str | Path,
    ca_path: str | Path,
    *,
    now_ms: int | None = None,
) -> CredentialGeneration:
    """Inspect an active generation through a non-mutating SQLite connection."""
    path = Path(database).resolve()
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM credential_generations "
            "WHERE robot_id = ? AND status = 'active'",
            (robot_id,),
        ).fetchone()
        if row is None:
            raise CredentialError("robot has no active credential generation")
        revoked = connection.execute(
            "SELECT 1 FROM revoked_certificates WHERE fingerprint_sha256 = ?",
            (row["fingerprint_sha256"],),
        ).fetchone()
    except sqlite3.Error as error:
        raise CredentialError(f"credential database cannot be inspected: {error}") from error
    finally:
        if connection is not None:
            connection.close()
    generation = SqliteCredentialStore._generation(row)
    _require_generation(
        generation,
        certificate_path,
        private_key_path,
        ca_path,
        revoked=revoked is not None,
        now_ms=int(time.time() * 1_000) if now_ms is None else now_ms,
    )
    return generation


def _require_generation(
    generation: CredentialGeneration,
    certificate_path: str | Path,
    private_key_path: str | Path,
    ca_path: str | Path,
    *,
    revoked: bool,
    now_ms: int,
) -> None:
    expected = tuple(
        path.resolve()
        for path in (
            generation.certificate_path,
            generation.private_key_path,
            generation.ca_path,
        )
    )
    configured = tuple(
        Path(path).resolve()
        for path in (certificate_path, private_key_path, ca_path)
    )
    if configured != expected:
        raise CredentialError(
            "network TLS files do not match the active credential generation"
        )
    if revoked:
        raise CredentialError("active credential generation is revoked")
    if generation.not_before_ms > now_ms:
        raise CredentialError("active credential generation is not yet valid")
    if generation.not_after_ms <= now_ms:
        raise CredentialError("active credential generation is expired")


class SqliteCredentialStore:
    """Robot-local inventory and audit trail for externally issued credentials."""

    def __init__(self, robot_id: str, database: str | Path, directory: str | Path) -> None:
        if not robot_id or len(robot_id.encode()) > 128:
            raise CredentialError("robot_id is required and bounded")
        self.robot_id = robot_id
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        database_path = Path(database)
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(
            database_path, isolation_level=None, check_same_thread=False
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS credential_generations (
                generation INTEGER PRIMARY KEY AUTOINCREMENT,
                robot_id TEXT NOT NULL,
                fingerprint_sha256 TEXT NOT NULL UNIQUE,
                certificate_path TEXT NOT NULL,
                private_key_path TEXT NOT NULL,
                ca_path TEXT NOT NULL,
                not_before_ms INTEGER NOT NULL,
                not_after_ms INTEGER NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('staged', 'active', 'retired')),
                created_at_ms INTEGER NOT NULL,
                activated_at_ms INTEGER
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_credential
            ON credential_generations(robot_id) WHERE status = 'active';
            CREATE TABLE IF NOT EXISTS revoked_certificates (
                fingerprint_sha256 TEXT PRIMARY KEY,
                reason TEXT NOT NULL,
                revoked_at_ms INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS credential_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                robot_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                occurred_at_ms INTEGER NOT NULL,
                detail_json TEXT NOT NULL
            );
            """
        )
        self._migrate_event_robot_ids()

    def _migrate_event_robot_ids(self) -> None:
        columns = {
            row[1] for row in self._connection.execute("PRAGMA table_info(credential_events)")
        }
        if "robot_id" in columns:
            return
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            self._connection.execute("ALTER TABLE credential_events ADD COLUMN robot_id TEXT")
            rows = self._connection.execute(
                "SELECT sequence, detail_json FROM credential_events"
            ).fetchall()
            for row in rows:
                try:
                    detail = json.loads(row["detail_json"])
                except (TypeError, json.JSONDecodeError) as error:
                    raise CredentialError(
                        f"legacy credential event {row['sequence']} is invalid"
                    ) from error
                owner = None
                if "generation" in detail:
                    owner_row = self._connection.execute(
                        "SELECT robot_id FROM credential_generations WHERE generation = ?",
                        (detail["generation"],),
                    ).fetchone()
                    owner = owner_row[0] if owner_row is not None else None
                elif "fingerprint_sha256" in detail:
                    owners = self._connection.execute(
                        "SELECT DISTINCT robot_id FROM credential_generations "
                        "WHERE fingerprint_sha256 = ?",
                        (detail["fingerprint_sha256"],),
                    ).fetchall()
                    owner = owners[0][0] if len(owners) == 1 else None
                if owner is None:
                    raise CredentialError(
                        f"legacy credential event {row['sequence']} ownership is unknown"
                    )
                self._connection.execute(
                    "UPDATE credential_events SET robot_id = ? WHERE sequence = ?",
                    (owner, row["sequence"]),
                )
            self._connection.execute("COMMIT")
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise

    def enroll(
        self,
        certificate: str | Path,
        private_key: str | Path,
        ca: str | Path,
        now_ms: int,
    ) -> CredentialGeneration:
        certificate_bytes = Path(certificate).read_bytes()
        private_key_bytes = Path(private_key).read_bytes()
        ca_bytes = Path(ca).read_bytes()
        parsed = self._validate_bundle(certificate_bytes, private_key_bytes, ca_bytes, now_ms)
        fingerprint, not_before_ms, not_after_ms = parsed
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            generation_directory: Path | None = None
            try:
                cursor = self._connection.execute(
                    """
                    INSERT INTO credential_generations
                    (robot_id, fingerprint_sha256, certificate_path, private_key_path,
                     ca_path, not_before_ms, not_after_ms, status, created_at_ms)
                    VALUES (?, ?, '', '', '', ?, ?, 'staged', ?)
                    """,
                    (self.robot_id, fingerprint, not_before_ms, not_after_ms, now_ms),
                )
                generation = int(cursor.lastrowid)
                generation_directory = self.directory / f"generation-{generation}"
                generation_directory.mkdir(mode=0o700)
                paths = (
                    generation_directory / "certificate.pem",
                    generation_directory / "private-key.pem",
                    generation_directory / "ca.pem",
                )
                for path, content, mode in zip(
                    paths, (certificate_bytes, private_key_bytes, ca_bytes), (0o644, 0o600, 0o644)
                ):
                    path.write_bytes(content)
                    os.chmod(path, mode)
                self._connection.execute(
                    """UPDATE credential_generations
                    SET certificate_path = ?, private_key_path = ?, ca_path = ?
                    WHERE generation = ?""",
                    (*map(str, paths), generation),
                )
                self._event("enrolled", now_ms, {"generation": generation, "fingerprint_sha256": fingerprint})
                self._connection.execute("COMMIT")
            except sqlite3.IntegrityError as error:
                self._connection.execute("ROLLBACK")
                if generation_directory is not None:
                    shutil.rmtree(generation_directory, ignore_errors=True)
                raise CredentialError("certificate is already enrolled") from error
            except BaseException:
                self._connection.execute("ROLLBACK")
                if generation_directory is not None:
                    shutil.rmtree(generation_directory, ignore_errors=True)
                raise
        return self.get(generation)

    def activate(self, generation: int, now_ms: int) -> CredentialGeneration:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._row(generation)
                if row["robot_id"] != self.robot_id:
                    raise CredentialError("credential belongs to another robot")
                if row["not_before_ms"] > now_ms or row["not_after_ms"] <= now_ms:
                    raise CredentialError("credential is not currently valid")
                if self.is_revoked(row["fingerprint_sha256"]):
                    raise CredentialError("revoked credential cannot be activated")
                self._connection.execute(
                    "UPDATE credential_generations SET status = 'retired' WHERE robot_id = ? AND status = 'active'",
                    (self.robot_id,),
                )
                self._connection.execute(
                    "UPDATE credential_generations SET status = 'active', activated_at_ms = ? WHERE generation = ?",
                    (now_ms, generation),
                )
                self._event("activated", now_ms, {"generation": generation})
                self._connection.execute("COMMIT")
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise
        return self.get(generation)

    def revoke(self, fingerprint: str, reason: str, now_ms: int) -> None:
        fingerprint = self._fingerprint(fingerprint)
        if not reason.strip() or len(reason.encode()) > 512:
            raise CredentialError("revocation reason is required and bounded")
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(
                    "INSERT INTO revoked_certificates VALUES (?, ?, ?)",
                    (fingerprint, reason, now_ms),
                )
                self._connection.execute(
                    "UPDATE credential_generations SET status = 'retired' WHERE fingerprint_sha256 = ?",
                    (fingerprint,),
                )
                self._event("revoked", now_ms, {"fingerprint_sha256": fingerprint, "reason": reason})
                self._connection.execute("COMMIT")
            except sqlite3.IntegrityError as error:
                self._connection.execute("ROLLBACK")
                raise CredentialError("certificate is already revoked") from error
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise

    def is_revoked(self, fingerprint: str) -> bool:
        fingerprint = self._fingerprint(fingerprint)
        with self._lock:
            return self._connection.execute(
                "SELECT 1 FROM revoked_certificates WHERE fingerprint_sha256 = ?",
                (fingerprint,),
            ).fetchone() is not None

    def active(self) -> CredentialGeneration | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM credential_generations WHERE robot_id = ? AND status = 'active'",
                (self.robot_id,),
            ).fetchone()
        return self._generation(row) if row else None

    def require_active_bundle(
        self,
        certificate_path: str | Path,
        private_key_path: str | Path,
        ca_path: str | Path,
        *,
        now_ms: int | None = None,
    ) -> CredentialGeneration:
        """Require the configured TLS files to be the active managed generation."""
        generation = self.active()
        if generation is None:
            raise CredentialError("robot has no active credential generation")
        _require_generation(
            generation,
            certificate_path,
            private_key_path,
            ca_path,
            revoked=self.is_revoked(generation.fingerprint_sha256),
            now_ms=int(time.time() * 1_000) if now_ms is None else now_ms,
        )
        return generation

    def get(self, generation: int) -> CredentialGeneration:
        with self._lock:
            return self._generation(self._row(generation))

    def events(self) -> tuple[dict[str, object], ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT sequence, event_type, occurred_at_ms, detail_json "
                "FROM credential_events WHERE robot_id = ? ORDER BY sequence",
                (self.robot_id,),
            ).fetchall()
        return tuple(
            {"sequence": row[0], "event_type": row[1], "occurred_at_ms": row[2], "detail": json.loads(row[3])}
            for row in rows
        )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _validate_bundle(self, certificate_bytes: bytes, key_bytes: bytes, ca_bytes: bytes, now_ms: int) -> tuple[str, int, int]:
        try:
            certificate = x509.load_pem_x509_certificate(certificate_bytes)
            private_key = serialization.load_pem_private_key(key_bytes, password=None)
            ca_certificate = x509.load_pem_x509_certificate(ca_bytes)
        except (ValueError, TypeError) as error:
            raise CredentialError("credential bundle contains invalid or encrypted PEM") from error
        try:
            uris = certificate.extensions.get_extension_for_class(
                x509.SubjectAlternativeName
            ).value.get_values_for_type(x509.UniformResourceIdentifier)
        except x509.ExtensionNotFound as error:
            raise CredentialError("certificate has no subject alternative name") from error
        identities = {
            uri.removeprefix(IDENTITY_URI_PREFIX)
            for uri in uris
            if uri.startswith(IDENTITY_URI_PREFIX)
        }
        if identities != {self.robot_id}:
            raise CredentialError("certificate must contain exactly the configured UMP robot identity")
        try:
            certificate.verify_directly_issued_by(ca_certificate)
        except (ValueError, TypeError) as error:
            raise CredentialError("certificate was not issued by the supplied CA") from error
        public = certificate.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        private_public = private_key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        if public != private_public:
            raise CredentialError("private key does not match certificate")
        not_before_ms = int(certificate.not_valid_before_utc.timestamp() * 1_000)
        not_after_ms = int(certificate.not_valid_after_utc.timestamp() * 1_000)
        if not_before_ms > now_ms or not_after_ms <= now_ms:
            raise CredentialError("certificate is not currently valid")
        fingerprint = hashlib.sha256(certificate.public_bytes(serialization.Encoding.DER)).hexdigest()
        return fingerprint, not_before_ms, not_after_ms

    def _row(self, generation: int) -> sqlite3.Row:
        row = self._connection.execute(
            "SELECT * FROM credential_generations WHERE generation = ?", (generation,)
        ).fetchone()
        if row is None:
            raise CredentialError("credential generation does not exist")
        return row

    @staticmethod
    def _generation(row: sqlite3.Row) -> CredentialGeneration:
        return CredentialGeneration(
            row["generation"], row["robot_id"], row["fingerprint_sha256"],
            Path(row["certificate_path"]), Path(row["private_key_path"]), Path(row["ca_path"]),
            row["not_before_ms"], row["not_after_ms"], row["status"],
        )

    def _event(self, event_type: str, occurred_at_ms: int, detail: dict[str, object]) -> None:
        self._connection.execute(
            "INSERT INTO credential_events"
            "(robot_id, event_type, occurred_at_ms, detail_json) VALUES (?, ?, ?, ?)",
            (
                self.robot_id,
                event_type,
                occurred_at_ms,
                json.dumps(detail, separators=(",", ":"), sort_keys=True),
            ),
        )

    @staticmethod
    def _fingerprint(value: str) -> str:
        value = value.lower()
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise CredentialError("fingerprint must be 64 hexadecimal characters")
        return value
