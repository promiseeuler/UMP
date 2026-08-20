from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
import json
from threading import RLock
from typing import Any, Protocol
from uuid import uuid4

PROTOCOL_VERSION = "ump/0.1"
MAX_MESSAGE_BYTES = 65_536
OPERATIONAL_STREAM = "operational"
SAFETY_STREAM = "safety"
STREAMS = frozenset({OPERATIONAL_STREAM, SAFETY_STREAM})
MESSAGE_TYPES = frozenset(
    {
        "manifest",
        "state",
        "goal",
        "plan",
        "assignment",
        "assignment_ack",
        "assignment_query",
        "assignment_snapshot",
        "cancellation_request",
        "cancellation_ack",
        "outcome",
    }
)


class ProtocolDecodeError(ValueError):
    pass


@dataclass(frozen=True)
class Envelope:
    message_id: str
    message_type: str
    source_id: str
    session_id: str
    sequence: int
    timestamp_ms: int
    payload: dict[str, Any]
    correlation_id: str | None = None
    protocol: str = PROTOCOL_VERSION
    stream: str = OPERATIONAL_STREAM


def encode_envelope(envelope: Envelope) -> bytes:
    if envelope.stream not in STREAMS:
        raise ProtocolDecodeError("unknown envelope stream")
    if envelope.stream == SAFETY_STREAM and envelope.message_type != "state":
        raise ProtocolDecodeError("safety stream carries state messages only")
    document = {
        "protocol": envelope.protocol,
        "message_id": envelope.message_id,
        "message_type": envelope.message_type,
        "source_id": envelope.source_id,
        "session_id": envelope.session_id,
        "sequence": envelope.sequence,
        "timestamp_ms": envelope.timestamp_ms,
        "correlation_id": envelope.correlation_id,
        "payload": envelope.payload,
    }
    if envelope.stream != OPERATIONAL_STREAM:
        document["stream"] = envelope.stream
    try:
        encoded = json.dumps(
            document, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ProtocolDecodeError("envelope is not JSON serializable") from error
    if len(encoded) > MAX_MESSAGE_BYTES:
        raise ProtocolDecodeError("envelope exceeds the 64 KiB core profile")
    return encoded


def decode_envelope(encoded: bytes) -> Envelope:
    if not encoded or len(encoded) > MAX_MESSAGE_BYTES:
        raise ProtocolDecodeError("encoded envelope size is invalid")
    try:
        document = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProtocolDecodeError("encoded envelope is not valid UTF-8 JSON") from error
    if not isinstance(document, dict):
        raise ProtocolDecodeError("envelope must be a JSON object")
    required = {
        "protocol", "message_id", "message_type", "source_id", "session_id",
        "sequence", "timestamp_ms", "payload",
    }
    if missing := required - document.keys():
        raise ProtocolDecodeError(f"envelope is missing fields: {sorted(missing)}")
    if document["protocol"] != PROTOCOL_VERSION:
        raise ProtocolDecodeError("unsupported protocol version")
    if document["message_type"] not in MESSAGE_TYPES:
        raise ProtocolDecodeError("unknown message type")
    stream = document.get("stream", OPERATIONAL_STREAM)
    if stream not in STREAMS:
        raise ProtocolDecodeError("unknown envelope stream")
    if stream == SAFETY_STREAM and document["message_type"] != "state":
        raise ProtocolDecodeError("safety stream carries state messages only")
    for field_name in ("message_id", "source_id", "session_id"):
        value = document[field_name]
        if not isinstance(value, str) or not value or len(value.encode()) > 128:
            raise ProtocolDecodeError(f"invalid {field_name}")
    sequence = document["sequence"]
    timestamp_ms = document["timestamp_ms"]
    if type(sequence) is not int or sequence < 1:
        raise ProtocolDecodeError("sequence must be a positive integer")
    if type(timestamp_ms) is not int or timestamp_ms < 0:
        raise ProtocolDecodeError("timestamp_ms must be a non-negative integer")
    if not isinstance(document["payload"], dict):
        raise ProtocolDecodeError("payload must be an object")
    correlation_id = document.get("correlation_id")
    if correlation_id is not None and (
        not isinstance(correlation_id, str) or len(correlation_id.encode()) > 128
    ):
        raise ProtocolDecodeError("invalid correlation_id")
    return Envelope(
        message_id=document["message_id"],
        message_type=document["message_type"],
        source_id=document["source_id"],
        session_id=document["session_id"],
        sequence=sequence,
        timestamp_ms=timestamp_ms,
        payload=document["payload"],
        correlation_id=correlation_id,
        stream=stream,
    )


Handler = Callable[[Envelope], None]


class MessageBus(Protocol):
    def subscribe(self, message_type: str, handler: Handler) -> None: ...
    def publish(self, envelope: Envelope) -> None: ...


class InMemoryBus:
    """Deterministic development transport implementing publish/subscribe."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Handler]] = defaultdict(list)
        self.trace: list[Envelope] = []
        self._lock = RLock()

    def subscribe(self, message_type: str, handler: Handler) -> None:
        with self._lock:
            self._subscribers[message_type].append(handler)

    def publish(self, envelope: Envelope) -> None:
        canonical = decode_envelope(encode_envelope(envelope))
        with self._lock:
            self.trace.append(canonical)
            handlers = tuple(self._subscribers[canonical.message_type])
            wildcard_handlers = tuple(self._subscribers["*"])
        for handler in handlers:
            handler(canonical)
        for handler in wildcard_handlers:
            handler(canonical)


def make_envelope(
    message_type: str,
    source_id: str,
    session_id: str,
    sequence: int,
    timestamp_ms: int,
    body: dict[str, Any],
    correlation_id: str | None = None,
    stream: str = OPERATIONAL_STREAM,
) -> Envelope:
    return Envelope(
        message_id=str(uuid4()),
        message_type=message_type,
        source_id=source_id,
        session_id=session_id,
        sequence=sequence,
        timestamp_ms=timestamp_ms,
        payload=body,
        correlation_id=correlation_id,
        stream=stream,
    )
