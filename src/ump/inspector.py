from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
from threading import RLock, Thread
from typing import Any
from urllib.parse import parse_qs, urlparse

from .transport import Envelope, MessageBus, encode_envelope


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

    def snapshot(self, event_limit: int = 100) -> dict[str, Any]:
        events = self.events(event_limit)
        robots: dict[str, dict[str, Any]] = {}
        with self._lock:
            latest_rows = self._connection.execute(
                """
                SELECT envelope FROM protocol_events AS candidate
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
        with self._lock:
            total = self._connection.execute(
                "SELECT COUNT(*) FROM protocol_events"
            ).fetchone()[0]
        return {
            "robots": sorted(robots.values(), key=lambda item: item["robot_id"]),
            "events": events,
            "event_count": total,
        }

    def close(self) -> None:
        with self._lock:
            self._connection.close()


class InspectorRecorder:
    def __init__(self, bus: MessageBus, store: InspectorStore) -> None:
        self.store = store
        bus.subscribe("*", self.store.record)


class InspectorServer:
    def __init__(
        self,
        store: InspectorStore,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("UMP inspector v0.1 is restricted to loopback binding")
        self.store = store
        self.static_root = Path(__file__).with_name("inspector_static")
        handler_type = self._handler_type()
        self._server = ThreadingHTTPServer((host, port), handler_type)
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

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
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
