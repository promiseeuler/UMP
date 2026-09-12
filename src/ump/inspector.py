from __future__ import annotations

from dataclasses import dataclass
import base64
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import sqlite3
import ssl
from threading import RLock, Thread
from typing import Any
from urllib.parse import parse_qs, urlparse

from .integrations import IntegrationProvenance
from .transport import Envelope, MessageBus, encode_envelope


INSPECTOR_COLUMNS = frozenset(
    {
        "event_order",
        "message_id",
        "message_type",
        "source_id",
        "session_id",
        "sequence",
        "timestamp_ms",
        "correlation_id",
        "envelope",
    }
)


class InspectorStoreError(ValueError):
    pass


@dataclass(frozen=True)
class InspectorAddress:
    host: str
    port: int


class InspectorStore:
    """Append-only protocol event recorder and read model for operators."""

    def __init__(self, path: str | Path) -> None:
        database_path = Path(path)
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(
            database_path, isolation_level=None, check_same_thread=False
        )
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS protocol_events (
                event_order INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id TEXT NOT NULL UNIQUE,
                message_type TEXT NOT NULL,
                source_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                timestamp_ms INTEGER NOT NULL,
                correlation_id TEXT,
                envelope BLOB NOT NULL,
                UNIQUE(source_id, session_id, sequence)
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS integration_reports (
                report_order INTEGER PRIMARY KEY AUTOINCREMENT,
                robot_id TEXT NOT NULL,
                source_standard TEXT NOT NULL,
                source_version TEXT NOT NULL,
                external_id TEXT NOT NULL,
                mapped_at_ms INTEGER NOT NULL,
                report_json TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS lab_events (
                event_order INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                robot_id TEXT,
                status TEXT NOT NULL,
                observed_at_ms INTEGER NOT NULL,
                detail_json TEXT NOT NULL
            )
            """
        )

    def record(self, envelope: Envelope) -> bool:
        encoded = encode_envelope(envelope)
        with self._lock:
            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO protocol_events
                (message_id, message_type, source_id, session_id, sequence,
                 timestamp_ms, correlation_id, envelope)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    envelope.message_id,
                    envelope.message_type,
                    envelope.source_id,
                    envelope.session_id,
                    envelope.sequence,
                    envelope.timestamp_ms,
                    envelope.correlation_id,
                    encoded,
                ),
            )
            return cursor.rowcount == 1

    def events(self, limit: int = 200) -> list[dict[str, Any]]:
        if not 1 <= limit <= 1_000:
            raise ValueError("event limit must be between 1 and 1000")
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT envelope FROM protocol_events
                ORDER BY event_order DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [json.loads(bytes(row[0])) for row in rows]

    def record_integration(
        self, robot_id: str, provenance: IntegrationProvenance
    ) -> None:
        report_json = json.dumps(
            provenance.report.as_dict(), separators=(",", ":"), sort_keys=True
        )
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO integration_reports
                (robot_id, source_standard, source_version, external_id,
                 mapped_at_ms, report_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    robot_id,
                    provenance.source_standard,
                    provenance.source_version,
                    provenance.external_id,
                    provenance.mapped_at_ms,
                    report_json,
                ),
            )

    def record_lab_event(
        self,
        event_type: str,
        status: str,
        observed_at_ms: int,
        *,
        robot_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        if not event_type.strip() or not status.strip() or observed_at_ms < 0:
            raise ValueError("lab event type, status, and timestamp are required")
        encoded = json.dumps(detail or {}, sort_keys=True, separators=(",", ":"))
        with self._lock:
            self._connection.execute(
                "INSERT INTO lab_events (event_type, robot_id, status, observed_at_ms, detail_json) VALUES (?, ?, ?, ?, ?)",
                (event_type, robot_id, status, observed_at_ms, encoded),
            )

    def snapshot(self, event_limit: int = 100) -> dict[str, Any]:
        events = self.events(event_limit)
        robots: dict[str, dict[str, Any]] = {}
        with self._lock:
            latest_rows = self._connection.execute(
                """
                SELECT envelope, timestamp_ms, session_id FROM protocol_events AS candidate
                WHERE message_type IN ('manifest', 'state')
                  AND event_order = (
                    SELECT MAX(event_order) FROM protocol_events AS latest
                    WHERE latest.source_id = candidate.source_id
                      AND latest.message_type = candidate.message_type
                  )
                ORDER BY source_id, message_type
                """
            ).fetchall()
        for row in latest_rows:
            event = json.loads(bytes(row[0]))
            robot = robots.setdefault(
                event["source_id"],
                {"robot_id": event["source_id"], "manifest": None, "state": None},
            )
            robot[event["message_type"]] = event["payload"]
            if event["message_type"] == "state":
                robot["state_observed_at_ms"] = row[1]
                robot["session_id"] = row[2]
        with self._lock:
            reconnect_rows = self._connection.execute(
                "SELECT source_id, COUNT(DISTINCT session_id) - 1 FROM protocol_events GROUP BY source_id"
            ).fetchall()
        for robot_id, reconnect_count in reconnect_rows:
            robot = robots.get(robot_id)
            if robot is not None:
                robot["reconnect_count"] = reconnect_count
        with self._lock:
            has_reports = self._connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='integration_reports'"
            ).fetchone()
            report_rows = (
                self._connection.execute(
                    """
                    SELECT robot_id, source_standard, source_version, external_id,
                           mapped_at_ms, report_json
                    FROM integration_reports AS candidate
                    WHERE report_order = (
                        SELECT MAX(report_order) FROM integration_reports AS latest
                        WHERE latest.robot_id = candidate.robot_id
                    )
                    """
                ).fetchall()
                if has_reports
                else ()
            )
        for row in report_rows:
            robot = robots.setdefault(
                row[0], {"robot_id": row[0], "manifest": None, "state": None}
            )
            robot["integration"] = {
                "source_standard": row[1],
                "source_version": row[2],
                "external_id": row[3],
                "mapped_at_ms": row[4],
                "report": json.loads(row[5]),
            }
        with self._lock:
            total = self._connection.execute(
                "SELECT COUNT(*) FROM protocol_events"
            ).fetchone()[0]
            has_lab_events = self._connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='lab_events'"
            ).fetchone()
            lab_rows = (
                self._connection.execute(
                    "SELECT event_type, robot_id, status, observed_at_ms, detail_json FROM lab_events ORDER BY event_order DESC LIMIT 200"
                ).fetchall()
                if has_lab_events
                else ()
            )
        return {
            "robots": sorted(robots.values(), key=lambda item: item["robot_id"]),
            "events": events,
            "event_count": total,
            "lab_events": [
                {"event_type": row[0], "robot_id": row[1], "status": row[2], "observed_at_ms": row[3], "detail": json.loads(row[4])}
                for row in lab_rows
            ],
        }

    def close(self) -> None:
        with self._lock:
            self._connection.close()


class ReadOnlyInspectorStore(InspectorStore):
    """Existing inspector read model opened without schema or data mutation."""

    def __init__(self, path: str | Path) -> None:
        database_path = Path(path).resolve()
        if not database_path.is_file():
            raise InspectorStoreError(
                f"inspector database does not exist: {database_path}"
            )
        self._lock = RLock()
        try:
            self._connection = sqlite3.connect(
                f"{database_path.as_uri()}?mode=ro",
                uri=True,
                isolation_level=None,
                check_same_thread=False,
            )
            self._connection.execute("PRAGMA query_only = ON")
            columns = {
                row[1]
                for row in self._connection.execute(
                    "PRAGMA table_info(protocol_events)"
                )
            }
        except sqlite3.Error as error:
            if hasattr(self, "_connection"):
                self._connection.close()
            raise InspectorStoreError(
                f"inspector database cannot be opened read-only: {error}"
            ) from error
        missing = sorted(INSPECTOR_COLUMNS - columns)
        if missing:
            self._connection.close()
            raise InspectorStoreError(
                f"inspector database schema is missing columns: {missing}"
            )

    def record(self, envelope: Envelope) -> bool:
        del envelope
        raise InspectorStoreError("read-only inspector store cannot record events")

    def record_integration(
        self, robot_id: str, provenance: IntegrationProvenance
    ) -> None:
        del robot_id, provenance
        raise InspectorStoreError("read-only inspector store cannot record integration reports")

    def record_lab_event(self, *args, **kwargs) -> None:
        del args, kwargs
        raise InspectorStoreError("read-only inspector store cannot record lab events")


class InspectorRecorder:
    def __init__(self, bus: MessageBus, store: InspectorStore) -> None:
        self.store = store
        self._lock = RLock()
        self._error: Exception | None = None
        bus.subscribe("*", self._record)

    def _record(self, envelope: Envelope) -> None:
        with self._lock:
            if self._error is not None:
                return
        try:
            self.store.record(envelope)
        except Exception as error:
            with self._lock:
                if self._error is None:
                    self._error = error

    def require_healthy(self) -> None:
        with self._lock:
            error = self._error
        if error is not None:
            raise RuntimeError(f"protocol event recording failed: {error}") from error


class InspectorServer:
    def __init__(
        self,
        store: InspectorStore,
        host: str = "127.0.0.1",
        port: int = 0,
        *,
        allow_remote: bool = False,
        auth_token: str | None = None,
        tls_certificate: str | Path | None = None,
        tls_private_key: str | Path | None = None,
    ) -> None:
        loopback = host in {"127.0.0.1", "::1", "localhost"}
        if not loopback and not allow_remote:
            raise ValueError("UMP inspector v0.1 is restricted to loopback binding")
        if not loopback and (
            not auth_token or tls_certificate is None or tls_private_key is None
        ):
            raise ValueError("remote inspector requires authentication and TLS")
        if (tls_certificate is None) != (tls_private_key is None):
            raise ValueError("inspector TLS certificate and private key must be provided together")
        if auth_token is not None and len(auth_token.encode("utf-8")) < 32:
            raise ValueError("inspector authentication token must contain at least 32 bytes")
        self.store = store
        self._auth_token = auth_token
        self.static_root = Path(__file__).with_name("inspector_static")
        handler_type = self._handler_type()
        self._server = ThreadingHTTPServer((host, port), handler_type)
        if tls_certificate is not None and tls_private_key is not None:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_3
            context.load_cert_chain(str(tls_certificate), str(tls_private_key))
            self._server.socket = context.wrap_socket(
                self._server.socket, server_side=True
            )
        self._thread: Thread | None = None

    @property
    def address(self) -> InspectorAddress:
        host, port = self._server.server_address[:2]
        return InspectorAddress(host, port)

    def start(self) -> InspectorAddress:
        if self._thread is not None:
            raise RuntimeError("inspector server is already running")
        self._thread = Thread(
            target=self._server.serve_forever, name="ump-inspector", daemon=True
        )
        self._thread.start()
        return self.address

    def serve_forever(self) -> None:
        if self._thread is not None:
            raise RuntimeError("inspector server is already running")
        self._server.serve_forever()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()
        self._thread = None

    def close(self) -> None:
        if self._thread is not None:
            self.stop()
        else:
            self._server.server_close()

    def _handler_type(self):
        store = self.store
        static_root = self.static_root
        auth_token = self._auth_token

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if auth_token is not None and not self._authorized(auth_token):
                    self.send_response(HTTPStatus.UNAUTHORIZED)
                    self.send_header("WWW-Authenticate", 'Basic realm="UMP Inspector"')
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                parsed = urlparse(self.path)
                if parsed.path == "/api/snapshot":
                    try:
                        limit = int(parse_qs(parsed.query).get("limit", ["100"])[0])
                        self._json(store.snapshot(limit))
                    except (ValueError, OverflowError) as error:
                        self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                    return
                files = {
                    "/": ("index.html", "text/html; charset=utf-8"),
                    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
                }
                item = files.get(parsed.path)
                if item is None:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                path = static_root / item[0]
                try:
                    body = path.read_bytes()
                except OSError:
                    self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR)
                    return
                self.send_response(HTTPStatus.OK)
                self._headers(item[1], len(body))
                self.end_headers()
                self.wfile.write(body)

            def _authorized(self, expected_token: str) -> bool:
                authorization = self.headers.get("Authorization", "")
                scheme, separator, encoded = authorization.partition(" ")
                if not separator or scheme.lower() != "basic":
                    return False
                try:
                    decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
                except (ValueError, UnicodeDecodeError):
                    return False
                username, separator, token = decoded.partition(":")
                return bool(separator and username == "ump") and secrets.compare_digest(
                    token, expected_token
                )

            def _json(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
                body = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
                self.send_response(status)
                self._headers("application/json; charset=utf-8", len(body))
                self.end_headers()
                self.wfile.write(body)

            def _headers(self, content_type: str, length: int) -> None:
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(length))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'",
                )

            def log_message(self, format: str, *args: Any) -> None:
                del format, args

        return Handler
