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
                event_type TEXT NOT NULL,
                occurred_at_ms INTEGER NOT NULL,
                detail_json TEXT NOT NULL
            );
            """
        )

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
                "SELECT sequence, event_type, occurred_at_ms, detail_json FROM credential_events ORDER BY sequence"
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
            "INSERT INTO credential_events(event_type, occurred_at_ms, detail_json) VALUES (?, ?, ?)",
            (event_type, occurred_at_ms, json.dumps(detail, separators=(",", ":"), sort_keys=True)),
        )

    @staticmethod
    def _fingerprint(value: str) -> str:
        value = value.lower()
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise CredentialError("fingerprint must be 64 hexadecimal characters")
        return value
