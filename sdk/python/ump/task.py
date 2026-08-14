"""Adapter-facing task handler API for the UMP Python SDK preview."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol


MAX_CAPABILITY_BYTES = 256


@dataclass(frozen=True)
class TaskContext:
    task_id: str
    issuer_machine_id: str
    capability: str
    input: bytes
    input_content_type: str
    attempt: int
    deadline_ms: int
    correlation_id: str


@dataclass(frozen=True)
class HandlerFailure(Exception):
    code: str
    detail: str
    outcome_unknown: bool = False
    retryable: bool = False
    retry_after_ms: int = 0


class TaskHandler(Protocol):
    def __call__(self, context: TaskContext) -> bytes | Awaitable[bytes]: ...


@dataclass(frozen=True)
class RegisteredHandler:
    capability: str
    interruptible: bool
    callback: TaskHandler


class HandlerRegistry:
    """Maps namespaced capabilities to adapter callbacks.

    The runtime remains responsible for authentication, leases, journaling, and
    idempotency before this registry is invoked.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, RegisteredHandler] = {}

    def register(
        self,
        capability: str,
        callback: TaskHandler,
        *,
        interruptible: bool,
    ) -> None:
        if not capability or len(capability.encode("utf-8")) > MAX_CAPABILITY_BYTES:
            raise ValueError("handler capability is invalid")
        if capability in self._handlers:
            raise ValueError(f"handler already registered for {capability}")
        self._handlers[capability] = RegisteredHandler(
            capability=capability,
            interruptible=interruptible,
            callback=callback,
        )

    def get(self, capability: str) -> RegisteredHandler:
        try:
            return self._handlers[capability]
        except KeyError as error:
            raise KeyError(f"no adapter handler registered for {capability}") from error

    async def execute(self, context: TaskContext) -> bytes:
        result = self.get(context.capability).callback(context)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, bytes):
            raise TypeError("task handler must return bytes")
        return result
