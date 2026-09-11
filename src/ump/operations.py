"""Minimal production supervision endpoints for long-running UMP services."""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import RLock, Thread
import time
from typing import Any


@dataclass(frozen=True)
class ServiceStatus:
    healthy: bool
    ready: bool
    started_at_ms: int
    checks: int
    failures: int
    last_error: str | None


class RuntimeMonitor:
    """Thread-safe, content-free service health state."""

    def __init__(self, clock_ms=None) -> None:
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1_000))
        self._lock = RLock()
        self._started_at_ms = self._clock_ms()
        self._healthy = True
        self._ready = False
        self._checks = 0
        self._failures = 0
        self._last_error: str | None = None

    def ready(self) -> None:
        with self._lock:
            self._ready = True

    def stopping(self) -> None:
        with self._lock:
            self._ready = False

    def check_passed(self) -> None:
        with self._lock:
            self._checks += 1
            self._healthy = True
            self._last_error = None

    def check_failed(self, error: BaseException) -> None:
        with self._lock:
            self._checks += 1
            self._failures += 1
            self._healthy = False
            self._ready = False
            self._last_error = f"{type(error).__name__}: {error}"[:512]

    def snapshot(self) -> ServiceStatus:
        with self._lock:
            return ServiceStatus(
                self._healthy,
                self._ready,
                self._started_at_ms,
                self._checks,
                self._failures,
                self._last_error,
            )


class OperationsServer:
    """Serves content-free health and Prometheus metrics for a supervisor."""

    def __init__(self, monitor: RuntimeMonitor, host: str, port: int) -> None:
        self.monitor = monitor
        self._server = ThreadingHTTPServer((host, port), self._handler_type())
        self._thread: Thread | None = None

    @property
    def address(self) -> tuple[str, int]:
        host, port = self._server.server_address[:2]
        return host, port

    def start(self) -> tuple[str, int]:
        if self._thread is not None:
            raise RuntimeError("operations server is already running")
        self._thread = Thread(
            target=self._server.serve_forever, name="ump-operations", daemon=True
        )
        self._thread.start()
        return self.address

    def close(self) -> None:
        if self._thread is not None:
            self._server.shutdown()
            self._thread.join()
            self._thread = None
        self._server.server_close()

    def _handler_type(self):
        monitor = self.monitor

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                status = monitor.snapshot()
                if self.path == "/healthz":
                    self._json(
                        {"healthy": status.healthy},
                        HTTPStatus.OK if status.healthy else HTTPStatus.SERVICE_UNAVAILABLE,
                    )
                    return
                if self.path == "/readyz":
                    self._json(
                        {"ready": status.ready},
                        HTTPStatus.OK if status.ready else HTTPStatus.SERVICE_UNAVAILABLE,
                    )
                    return
                if self.path == "/metrics":
                    body = (
                        "# TYPE ump_service_healthy gauge\n"
                        f"ump_service_healthy {int(status.healthy)}\n"
                        "# TYPE ump_service_ready gauge\n"
                        f"ump_service_ready {int(status.ready)}\n"
                        "# TYPE ump_runtime_checks_total counter\n"
                        f"ump_runtime_checks_total {status.checks}\n"
                        "# TYPE ump_runtime_failures_total counter\n"
                        f"ump_runtime_failures_total {status.failures}\n"
                    ).encode("ascii")
                    self._send(HTTPStatus.OK, "text/plain; version=0.0.4", body)
                    return
                self.send_error(HTTPStatus.NOT_FOUND)

            def _json(self, value: dict[str, Any], status: HTTPStatus) -> None:
                body = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
                self._send(status, "application/json; charset=utf-8", body)

            def _send(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: Any) -> None:
                del format, args

        return Handler
