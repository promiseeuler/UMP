from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
from pathlib import Path
from threading import Thread
from typing import Any
from urllib.parse import urlparse

from .collaboration import Coordinator
from .demo import WarehousePlanner, build_demo
from .models import SharedGoal


@dataclass(frozen=True)
class VisualSimulationAddress:
    host: str
    port: int


def build_visual_scenario() -> dict[str, Any]:
    """Run the reference collaboration and project it into a visual timeline."""
    bus, registry, participants = build_demo()
    robot_ids = tuple(sorted(registry.peers))
    goal = SharedGoal(
        goal_id="goal-visual-warehouse",
        description="Move the sealed package from intake to storage shelf A",
        participant_ids=robot_ids,
        constraints={"keep_upright": True},
        deadline_ms=60_000,
    )
    coordinator = Coordinator(
        "warehouse-coordinator-1",
        bus,
        registry,
        authority_lease_ids={robot_id: "visual-simulation" for robot_id in robot_ids},
    )
    try:
        plan = coordinator.execute(goal, WarehousePlanner(), now_ms=1_100)
        snapshot = coordinator.store.snapshot(plan.plan_id)
    finally:
        coordinator.close()
        for participant in participants:
            participant.close()

    events = [
        {
            "index": index,
            "type": envelope.message_type,
            "source_id": envelope.source_id,
            "correlation_id": envelope.correlation_id,
            "summary": envelope.payload.get("description")
            or envelope.payload.get("summary")
            or envelope.payload.get("activity")
            or envelope.message_type,
        }
        for index, envelope in enumerate(bus.trace)
    ]
    robots = []
    for robot_id in robot_ids:
        peer = registry.peers[robot_id]
        if peer.manifest is None or peer.state is None:
            raise RuntimeError("visual simulation participant context is incomplete")
        capability = peer.manifest.capabilities[0]
        robots.append(
            {
                "robot_id": robot_id,
                "manufacturer": peer.manifest.manufacturer,
                "model": peer.manifest.model,
                "robot_class": peer.manifest.robot_class,
                "capability": capability.name,
                "activity": peer.state.activity,
                "intent": peer.state.intent,
                "safety": peer.state.safety.value,
            }
        )
    return {
        "profile": "ump.visual-simulation/v1",
        "protocol": "ump/0.1",
        "simulation": {
            "kind": "semantic_digital_twin",
            "physical_dynamics": False,
            "description": "Three robots coordinate a route inspection, package move, and shelf placement.",
        },
        "goal": {"goal_id": goal.goal_id, "description": goal.description},
        "plan": {
            "plan_id": plan.plan_id,
            "planner_id": plan.planner_id,
            "status": snapshot.status.value,
            "steps": [
                {
                    "step_id": step.step_id,
                    "robot_id": step.assigned_robot_id,
                    "description": step.description,
                    "capability": step.capability,
                    "depends_on": list(step.depends_on),
                }
                for step in plan.steps
            ],
        },
        "robots": robots,
        "events": events,
        "message_counts": dict(
            sorted(Counter(event["type"] for event in events).items())
        ),
        "timeline": [
            {"at_ms": 0, "phase": "awareness", "label": "Peers discovered"},
            {"at_ms": 1800, "phase": "inspect", "label": "Quadruped inspects route"},
            {"at_ms": 4800, "phase": "carry", "label": "Humanoid carries package"},
            {"at_ms": 8000, "phase": "place", "label": "Mobile arm places package"},
            {"at_ms": 10500, "phase": "complete", "label": "Goal completed"},
        ],
        "duration_ms": 12000,
    }


class VisualSimulationServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("visual simulation is restricted to loopback binding")
        self.static_root = Path(str(files("ump").joinpath("visual_simulation_static")))
        self._server = ThreadingHTTPServer((host, port), self._handler_type())
        self._thread: Thread | None = None

    @property
    def address(self) -> VisualSimulationAddress:
        host, port = self._server.server_address[:2]
        return VisualSimulationAddress(host, port)

    def start(self) -> VisualSimulationAddress:
        if self._thread is not None:
            raise RuntimeError("visual simulation server is already running")
        self._thread = Thread(
            target=self._server.serve_forever,
            name="ump-visual-simulation",
            daemon=True,
        )
        self._thread.start()
        return self.address

    def serve_forever(self) -> None:
        if self._thread is not None:
            raise RuntimeError("visual simulation server is already running")
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
        static_root = self.static_root

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                path = urlparse(self.path).path
                if path == "/api/scenario":
                    self._json(build_visual_scenario())
                    return
                static_files = {
                    "/": ("index.html", "text/html; charset=utf-8"),
                    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
                }
                item = static_files.get(path)
                if item is None:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                filename, content_type = item
                try:
                    content = (static_root / filename).read_bytes()
                except OSError:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                self.send_response(HTTPStatus.OK)
                self._headers(content_type)
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            def _json(self, document: dict[str, Any]) -> None:
                content = json.dumps(document, separators=(",", ":")).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self._headers("application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            def _headers(self, content_type: str) -> None:
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; script-src 'self'; style-src 'self'; "
                    "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
                    "base-uri 'none'; frame-ancestors 'none'",
                )

            def log_message(self, format: str, *args: object) -> None:
                del format, args

        return Handler
