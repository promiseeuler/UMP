"""Owner-facing long-running UMP participant service."""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from threading import Event
import time

from .adapter import RobotAdapter
from .authority import AssignmentAuthorizer
from .journal import AssignmentJournal
from .network import TlsNetworkBus
from .runtime import Participant


def load_adapter(
    specification: str, config_path: str | Path | None = None
) -> RobotAdapter:
    """Load a trusted adapter factory from ``module:attribute``."""
    module_name, separator, attribute_name = specification.partition(":")
    if not separator or not module_name or not attribute_name:
        raise ValueError("adapter must use module:factory syntax")
    try:
        factory = getattr(import_module(module_name), attribute_name)
    except (ImportError, AttributeError) as error:
        raise ValueError(f"adapter factory cannot be loaded: {specification}") from error
    if not callable(factory):
        raise ValueError("adapter factory must be callable")
    adapter = factory(Path(config_path) if config_path is not None else None)
    if not isinstance(adapter, RobotAdapter):
        raise TypeError("adapter factory did not return a RobotAdapter")
    return adapter


class ParticipantService:
    """Runs one adapter and participant over a configured network bus."""

    def __init__(
        self,
        adapter: RobotAdapter,
        bus: TlsNetworkBus,
        journal: AssignmentJournal,
        authorizer: AssignmentAuthorizer,
        *,
        state_hz: float = 2.0,
        execution_workers: int = 0,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        if not 1.0 <= state_hz <= 10.0:
            raise ValueError("state_hz must be between 1 and 10")
        manifest = adapter.manifest()
        if manifest.robot_id != bus.robot_id:
            raise ValueError("adapter and network robot identities differ")
        self.adapter = adapter
        self.bus = bus
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1_000))
        self._interval = 1.0 / state_hz
        self._participant = Participant(
            adapter,
            bus,
            journal=journal,
            authorizer=authorizer,
            clock_ms=self._clock_ms,
            execution_workers=execution_workers,
        )
        self._started = False
        self._closed = False

    def start(self) -> tuple[str, int]:
        if self._closed:
            raise RuntimeError("participant service is closed")
        if self._started:
            raise RuntimeError("participant service is already started")
        endpoint = self.bus.start()
        try:
            self._participant.announce(self._clock_ms())
        except BaseException:
            self.bus.stop()
            raise
        self._started = True
        return endpoint

    def run(self, stop: Event) -> None:
        if not self._started:
            raise RuntimeError("participant service must be started before run")
        while not stop.wait(self._interval):
            self._participant.publish_state(self._clock_ms())

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._participant.close()
        finally:
            if self._started:
                self.bus.stop()
                self._started = False
